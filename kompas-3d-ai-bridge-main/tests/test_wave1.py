#!/usr/bin/env python3
"""Тесты «Волны 1»: каталог инструментов, конфигурация, ошибки, сжатие, задания.

Все тесты работают без КОМПАСа, без COM и без сети - их можно запускать в CI
на любой машине. Запуск::

    python -m unittest discover -s tests -p "test_wave1.py" -v

Что проверяется по существу (а не «код не упал»):

* каталог инструментов - единственный источник поверхности, и VERSION.json
  не может с ним разъехаться;
* политика записи управляется конфигом, а не захардкоженными путями;
* наружу при ошибке не уходит ни трассировка, ни абсолютные пути;
* большой ответ инструмента не уезжает в контекст целиком, но и не теряется
  молча - есть счётчики, курсор и артефакт;
* задание, которого нет в манифесте, не исполняется ни при каких условиях.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
MCP = REPO_ROOT / "mcp"
for path in (str(SRC), str(MCP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import compact            # noqa: E402
import errors             # noqa: E402
import jobs               # noqa: E402
import safety             # noqa: E402
import settings           # noqa: E402
import tools_catalog      # noqa: E402


class TempRootCase(unittest.TestCase):
    """Общая обвязка: временный корень моста с конфигом."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="kompas_wave1_")
        self.root = Path(self._tmp.name).resolve()
        settings.reset_cache()

    def tearDown(self) -> None:
        settings.reset_cache()
        self._tmp.cleanup()

    def write_config(self, payload) -> Path:
        config_dir = self.root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / "agent_config.json"
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        settings.reset_cache()
        return path


# ---------------------------------------------------------------------------
# 1. Единый каталог инструментов
# ---------------------------------------------------------------------------
class TestToolsCatalog(unittest.TestCase):
    def test_surface_is_complete_and_unique(self):
        names = tools_catalog.tool_names()
        self.assertEqual(len(names), 59, "поверхность изменилась - обновите ожидание осознанно")
        self.assertEqual(len(set(names)), len(names), "имена инструментов должны быть уникальны")

    def test_every_tool_is_described_for_the_model(self):
        for tool in tools_catalog.TOOLS:
            with self.subTest(tool=tool.get("name")):
                self.assertTrue(str(tool.get("name", "")).startswith("kompas_"))
                self.assertGreaterEqual(len(str(tool.get("description", ""))), 40,
                                        "описание должно объяснять назначение, а не называть инструмент")
                schema = tool.get("inputSchema")
                self.assertIsInstance(schema, dict)
                self.assertEqual(schema.get("type"), "object")
                self.assertIn("properties", schema)

    def test_routing_covers_every_tool_exactly_once(self):
        routing = tools_catalog.routing_table()
        self.assertEqual(set(routing), set(tools_catalog.tool_names()))

    def test_wrapper_tools_are_not_routed_by_worker(self):
        for name in tools_catalog.WRAPPER_SIDE_TOOLS:
            self.assertIn(name, tools_catalog.TOOL_INDEX)
            self.assertNotIn(name, tools_catalog.ACTION_MAP,
                             "инструменты обёртки не должны маршрутизироваться воркером")
            self.assertIn(name, tools_catalog.WRAPPER_SIDE_ACTIONS)

    def test_version_json_matches_catalog(self):
        declared = json.loads((REPO_ROOT / "VERSION.json").read_text(encoding="utf-8"))
        snapshot = tools_catalog.catalog_snapshot()
        self.assertEqual(declared["mcp_tool_count"], snapshot["mcp_tool_count"])
        self.assertEqual(declared["mcp_tools"], snapshot["mcp_tools"],
                         "VERSION.json и каталог разъехались")
        self.assertEqual(declared["mcp_protocol_version"], tools_catalog.MCP_PROTOCOL_VERSION)
        self.assertEqual(declared["mcp_supported_protocol_versions"],
                         list(tools_catalog.SUPPORTED_PROTOCOL_VERSIONS))

    def test_protocol_negotiation(self):
        self.assertEqual(tools_catalog.negotiate_protocol("2025-06-18"), "2025-06-18",
                         "поддерживаемую версию клиента нужно уважать")
        self.assertEqual(tools_catalog.negotiate_protocol("2025-11-25"), "2025-11-25")
        self.assertEqual(tools_catalog.negotiate_protocol("1999-01-01"),
                         tools_catalog.MCP_PROTOCOL_VERSION,
                         "на неизвестную версию отвечаем своей максимальной")
        self.assertEqual(tools_catalog.negotiate_protocol(None), tools_catalog.MCP_PROTOCOL_VERSION)

    def test_annotations_are_present_where_they_matter(self):
        read_only = [t for t in tools_catalog.TOOLS
                     if (t.get("annotations") or {}).get("readOnlyHint") is True]
        self.assertGreater(len(read_only), 10, "чтение должно быть явно помечено readOnlyHint")
        writers = [t for t in tools_catalog.TOOLS
                   if (t.get("annotations") or {}).get("readOnlyHint") is False]
        self.assertGreater(len(writers), 5, "изменяющие инструменты должны быть помечены явно")


# ---------------------------------------------------------------------------
# 2. Конфигурация
# ---------------------------------------------------------------------------
class TestSettings(TempRootCase):
    def test_defaults_when_config_missing(self):
        policy = settings.read_settings(self.root)
        self.assertFalse(policy.config_loaded)
        self.assertTrue(any(w.startswith("config_missing") for w in policy.warnings))
        self.assertEqual(len(policy.protected_roots), 3)
        self.assertEqual(policy.mcp_tool_timeout_sec, 90)

    def test_malformed_config_falls_back_instead_of_crashing(self):
        self.write_config("{ это не json")
        policy = settings.read_settings(self.root)
        self.assertFalse(policy.config_loaded)
        self.assertTrue(any(w.startswith("config_unreadable") for w in policy.warnings))
        self.assertEqual(len(policy.protected_roots), 3)

    def test_bridge_root_placeholder_is_expanded(self):
        self.write_config({
            "safety": {
                "protected_roots": [str(self.root / "src")],
                "write_roots": ["{bridge_root}\\out"],
            }
        })
        policy = settings.read_settings(self.root)
        self.assertIn((self.root / "src").resolve(), policy.protected_roots)
        self.assertIn((self.root / "out").resolve(), policy.write_roots)
        self.assertIn((self.root / "work").resolve(), policy.write_roots,
                      "рабочий каталог work должен быть доступен всегда")

    def test_write_root_inside_protected_is_dropped(self):
        self.write_config({
            "safety": {
                "protected_roots": [str(self.root / "src")],
                "write_roots": [str(self.root / "src" / "out")],
            }
        })
        policy = settings.read_settings(self.root)
        self.assertTrue(any(w.startswith("write_root_inside_protected_root") for w in policy.warnings))
        self.assertFalse(policy.is_writable(self.root / "src" / "out"))
        self.assertTrue(policy.is_protected(self.root / "src" / "out"))

    def test_out_of_range_number_is_reported_and_defaulted(self):
        self.write_config({"mcp_tool_timeout_sec": 99999})
        policy = settings.read_settings(self.root)
        self.assertEqual(policy.mcp_tool_timeout_sec, 90)
        self.assertTrue(any("out_of_range" in w for w in policy.warnings))

    def test_compaction_settings_are_configurable(self):
        self.write_config({"compact_threshold_bytes": 5000, "compact_max_items": 7})
        policy = settings.read_settings(self.root)
        self.assertEqual(policy.compact_threshold_bytes, 5000)
        self.assertEqual(policy.compact_max_items, 7)

    def test_settings_are_cached_per_root(self):
        first = settings.settings_for(self.root)
        self.assertIs(first, settings.settings_for(self.root))
        settings.reset_cache()
        self.assertIsNot(first, settings.settings_for(self.root))


# ---------------------------------------------------------------------------
# 3. Политика записи
# ---------------------------------------------------------------------------
class TestSafetyPolicy(TempRootCase):
    def test_protected_root_is_never_writable(self):
        source = self.root / "src"
        self.write_config({"safety": {"protected_roots": [str(source)]}})
        with self.assertRaises(PermissionError) as ctx:
            safety.safe_write(source / "part.m3d", self.root)
        self.assertIn("protected_path", str(ctx.exception))

    def test_bridge_code_directory_is_not_writable_by_default(self):
        # Каталоги кода самого моста (src, mcp, jobs) не должны быть доступны
        # агенту на запись: иначе он сможет подменить собственную логику
        # или дописать себя в манифест заданий.
        for name in ("src", "mcp", "jobs"):
            with self.subTest(directory=name):
                with self.assertRaises(PermissionError) as ctx:
                    safety.safe_write(self.root / name / "injected.py", self.root)
                self.assertIn("write_outside_allowed_roots", str(ctx.exception))

    def test_work_directory_is_writable(self):
        target = self.root / "work" / "detail_AGENT_COPY.m3d"
        self.assertEqual(safety.safe_write(target, self.root), target.resolve())

    def test_outside_root_is_rejected(self):
        with self.assertRaises(PermissionError) as ctx:
            safety.safe_write(self.root / "somewhere" / "x.m3d", self.root)
        self.assertIn("write_outside_allowed_roots", str(ctx.exception))

    def test_config_controls_the_policy(self):
        extra = self.root / "extra_out"
        self.write_config({"safety": {"write_roots": [str(extra)]}})
        self.assertEqual(safety.safe_write(extra / "a.step", self.root), (extra / "a.step").resolve())

    def test_copy_destination_lands_in_work(self):
        dest = safety.copy_destination(self.root / "src" / "frame.a3d", self.root)
        self.assertEqual(dest.parent, (self.root / "work").resolve())
        self.assertEqual(dest.name, "frame.a3d")

    def test_protected_module_constant_still_importable(self):
        # jobs.py исторически импортирует PROTECTED - контракт сохранён.
        self.assertTrue(len(safety.PROTECTED) >= 1)


# ---------------------------------------------------------------------------
# 4. Ошибки
# ---------------------------------------------------------------------------
class TestErrors(unittest.TestCase):
    def test_known_failures_map_to_stable_codes(self):
        cases = [
            (RuntimeError("kompas_active_instance_not_found: ..."), "kompas_not_attached"),
            (PermissionError("protected_path: C:\\X\\Y"), "path_protected"),
            (PermissionError("write_outside_allowed_roots: C:\\X"), "path_not_allowed"),
            (PermissionError("job_not_in_allowlist: a.py"), "job_not_allowed"),
            (RuntimeError("reopen_postcondition_failed: target=..."), "active_document_mismatch"),
            (TimeoutError("bridge_timeout action=x"), "timeout"),
            (FileNotFoundError("file not found: a.m3d"), "file_not_found"),
            (ValueError("width_mm must be positive"), "invalid_argument"),
            (RuntimeError("что-то совсем неожиданное"), "internal_error"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__, expected=expected):
                code, _, hint = errors.classify(exc)
                self.assertEqual(code, expected)
                self.assertTrue(hint, "у каждого кода должна быть подсказка")

    def test_error_body_never_leaks_traceback(self):
        root = Path(r"C:\KOMPAS_AI_BRIDGE")
        try:
            raise RuntimeError(f"boom in {root}\\src\\worker.py")
        except RuntimeError as exc:
            body = errors.error_body("part.create", exc, root)
        self.assertNotIn("traceback", json.dumps(body).lower())
        self.assertNotIn("Traceback (most recent call last)", json.dumps(body))
        self.assertIn(body["code"], errors.CODE_HINTS)

    def test_redaction_hides_absolute_paths_but_keeps_file_names(self):
        root = Path(r"C:\KOMPAS_AI_BRIDGE")
        text = r"failed at C:\KOMPAS_AI_BRIDGE\src\worker.py and C:\Other\place\model.m3d"
        cleaned = errors.redact(text, root)
        self.assertIn("<bridge_root>", cleaned)
        self.assertNotIn(r"C:\Other\place", cleaned)
        self.assertIn("model.m3d", cleaned, "имя файла нужно сохранить - без него сообщение бесполезно")

    def test_redaction_clips_long_messages(self):
        cleaned = errors.redact("x" * 5000, None)
        self.assertLessEqual(len(cleaned), errors.MAX_MESSAGE_CHARS)

    def test_traceback_goes_to_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                raise RuntimeError("detailed failure")
            except RuntimeError as exc:
                path = errors.log_traceback(Path(tmp), "part.create", exc)
            self.assertIsNotNone(path)
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("detailed failure", content)
            self.assertIn("action=part.create", content)


# ---------------------------------------------------------------------------
# 5. Компактные ответы
# ---------------------------------------------------------------------------
class TestCompact(TempRootCase):
    def test_cursor_roundtrip(self):
        for offset in (0, 1, 42, 9999):
            self.assertEqual(compact.decode_cursor(compact.encode_cursor(offset)), offset)

    def test_invalid_cursor_restarts_from_beginning(self):
        for bad in ("", None, "мусор", "c:!!!", "x:5"):
            self.assertEqual(compact.decode_cursor(bad), 0)

    def test_pagination_walks_the_whole_list(self):
        items = list(range(250))
        seen, cursor, pages = [], None, 0
        while True:
            page = compact.paginate(items, limit=100, cursor=cursor)
            seen.extend(page["items"])
            pages += 1
            if not page["next_cursor"]:
                break
            cursor = page["next_cursor"]
        self.assertEqual(seen, items, "постраничный обход обязан вернуть все элементы без потерь")
        self.assertEqual(pages, 3)
        self.assertEqual(page["total"], 250)
        self.assertFalse(page["truncated"])

    def test_pagination_beyond_end_is_empty_not_error(self):
        page = compact.paginate([1, 2, 3], limit=10, cursor=compact.encode_cursor(99))
        self.assertEqual(page["items"], [])
        self.assertFalse(page["truncated"])

    def test_small_payload_is_not_touched(self):
        payload = {"bodies": [{"index": i} for i in range(3)]}
        result, meta = compact.compact_payload(
            payload, bridge_root=self.root, request_id="r1", threshold=60_000, max_items=200)
        self.assertIsNone(meta)
        self.assertIs(result, payload, "маленький ответ должен уходить как есть")
        self.assertFalse((self.root / "runtime" / "artifacts").exists())

    def test_large_payload_is_truncated_and_preserved_in_artifact(self):
        payload = {"faces": [{"index": i, "area": i * 1.5} for i in range(4000)]}
        result, meta = compact.compact_payload(
            payload, bridge_root=self.root, request_id="req42", threshold=1000, max_items=50)

        self.assertIsNotNone(meta)
        self.assertEqual(len(result["faces"]), 50, "в контекст уходит только страница")
        self.assertIn("_compact", result, "факт усечения нельзя скрывать")
        self.assertEqual(result["_compact"]["truncated_lists"]["faces"]["total"], 4000)
        self.assertGreater(result["_compact"]["original_bytes"], 1000)

        artifact = Path(result["_compact"]["artifact"])
        self.assertTrue(artifact.is_file())
        saved = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["faces"]), 4000, "полный дамп должен быть доступен агенту")
        self.assertLess(compact.serialized_size(result), compact.serialized_size(payload))

    def test_scalar_fields_survive_compaction(self):
        payload = {"count": 4000, "faces": [{"i": i} for i in range(3000)], "bbox": [1, 2, 3]}
        result, _ = compact.compact_payload(
            payload, bridge_root=self.root, request_id="r2", threshold=500, max_items=10)
        self.assertEqual(result["count"], 4000)
        self.assertEqual(result["bbox"], [1, 2, 3])


# ---------------------------------------------------------------------------
# 6. Задания
# ---------------------------------------------------------------------------
class FakeSession:
    def __init__(self, root: Path) -> None:
        self.root = root


class TestJobsAllowlist(TempRootCase):
    def _jobs_dir(self) -> Path:
        path = self.root / "jobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _script(self, name: str, body: str) -> Path:
        path = self._jobs_dir() / name
        path.write_text(body, encoding="utf-8")
        return path

    def _allowlist(self, entries) -> None:
        (self._jobs_dir() / "allowlist.json").write_text(
            json.dumps({"version": 1, "jobs": entries}, ensure_ascii=False), encoding="utf-8")

    def test_missing_manifest_denies_everything(self):
        self._script("hello.py", "print('hi')")
        with self.assertRaises(PermissionError) as ctx:
            jobs.run_job(FakeSession(self.root), {"script": "hello.py"})
        self.assertIn("job_not_in_allowlist", str(ctx.exception))

    def test_unlisted_script_is_refused_even_if_it_exists(self):
        self._script("sneaky.py", "import os\nprint(os.environ['USERPROFILE'])")
        self._allowlist(["other.py"])
        with self.assertRaises(PermissionError) as ctx:
            jobs.run_job(FakeSession(self.root), {"script": "sneaky.py"})
        self.assertIn("job_not_in_allowlist", str(ctx.exception))

    def test_disabled_entry_is_refused(self):
        self._script("off.py", "print('hi')")
        self._allowlist([{"name": "off.py", "enabled": False}])
        with self.assertRaises(PermissionError) as ctx:
            jobs.run_job(FakeSession(self.root), {"script": "off.py"})
        self.assertIn("disabled", str(ctx.exception))

    def test_path_outside_jobs_directory_is_refused(self):
        outside = self.root / "elsewhere.py"
        outside.write_text("print('hi')", encoding="utf-8")
        self._allowlist(["elsewhere.py"])
        with self.assertRaises(PermissionError) as ctx:
            jobs.run_job(FakeSession(self.root), {"script": str(outside)})
        self.assertIn("job_must_be_existing_python_file", str(ctx.exception))

    def test_non_python_file_is_refused(self):
        (self._jobs_dir() / "run.cmd").write_text("echo hi", encoding="utf-8")
        self._allowlist(["run.cmd"])
        with self.assertRaises(PermissionError):
            jobs.run_job(FakeSession(self.root), {"script": "run.cmd"})

    def test_allowlisted_job_runs_and_reports_environment(self):
        self._script("hello.py", (
            "import json, os\n"
            "print(json.dumps({'root': os.environ.get('KOMPAS_BRIDGE_ROOT'),\n"
            "                  'work': os.environ.get('KOMPAS_BRIDGE_WORK')}))\n"
        ))
        self._allowlist([{"name": "hello.py", "description": "smoke", "timeout_sec": 30}])

        response = jobs.run_job(FakeSession(self.root), {"script": "hello.py"})
        self.assertTrue(response["ok"])
        body = response["result"]
        self.assertEqual(body["exit_code"], 0)
        self.assertEqual(body["script"], "hello.py")
        payload = json.loads(body["stdout"])
        self.assertEqual(Path(payload["root"]), self.root)
        self.assertEqual(Path(payload["work"]), (self.root / "work").resolve())

    def test_failing_job_reports_structured_error(self):
        self._script("boom.py", "import sys\nsys.exit(3)\n")
        self._allowlist(["boom.py"])
        response = jobs.run_job(FakeSession(self.root), {"script": "boom.py"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "job_failed")
        self.assertEqual(response["error"]["details"]["exit_code"], 3)

    def test_timeout_is_clamped_by_configuration(self):
        self.write_config({"job_timeout_max_sec": 2})
        self._script("slow.py", "import time\ntime.sleep(30)\n")
        self._allowlist(["slow.py"])

        started = time.monotonic()
        response = jobs.run_job(FakeSession(self.root), {"script": "slow.py", "timeout_sec": 9999})
        elapsed = time.monotonic() - started

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "job_timeout")
        self.assertLess(elapsed, 15, "запрос на 9999 секунд должен быть обрезан настройкой")

    def test_allowed_jobs_listing_reflects_reality(self):
        self._script("present.py", "print('hi')")
        self._allowlist(["present.py", "absent.py"])
        listed = {entry["name"]: entry for entry in jobs.allowed_jobs(self.root)}
        self.assertTrue(listed["present.py"]["exists"])
        self.assertFalse(listed["absent.py"]["exists"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
