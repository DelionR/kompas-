#!/usr/bin/env python3
"""Тесты «Волны 4»: экспорт с проверкой результата и контрольные точки.

Все тесты работают без КОМПАСа, без COM и без сети. Запуск::

    python -m unittest discover -s tests -p "test_wave4.py" -v

Что проверяется по существу:

* сухой прогон экспорта не обращается к КОМПАСу вообще - это проверяется
  счётчиком вызовов, а не обещанием в документации;
* экспорт, который вернул управление, но произвёл пустой файл, считается
  неудачей: именно это и есть главный обман COM-экспорта;
* `SaveAs` не входит в список методов - экспорт не должен перезаписывать
  сам документ;
* снимок возвращается на место исходного файла, а восстановление вне
  разрешённых корней невозможно.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
MCP = REPO_ROOT / "mcp"
for path in (str(SRC), str(MCP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import checkpoint         # noqa: E402
import export_tools       # noqa: E402
import settings           # noqa: E402
import tools_catalog      # noqa: E402

DXF_MINIMAL = (
    "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1015\n"
    "0\nENDSEC\n"
    "0\nSECTION\n2\nENTITIES\n"
    "0\nLINE\n0\nCIRCLE\n"
    "0\nENDSEC\n0\nEOF\n"
).encode("latin-1")

STEP_MINIMAL = (
    "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
    "#1=MANIFEST_PLACEHOLDER('x');\nENDSEC;\nEND-ISO-10303-21;\n"
).encode("latin-1")


class FakeCom:
    """Простая имитация COM-объекта: методы задаются как обычные атрибуты."""

    def __init__(self, **methods):
        for name, value in methods.items():
            setattr(self, name, value)


class FakeExportSession:
    def __init__(self, root, document, app):
        self.root = root
        self._document = document
        self._app = app
        self.connect_calls = 0
        self.active_calls = 0

    def active(self):
        self.active_calls += 1
        return self._document

    def connect(self):
        self.connect_calls += 1
        if self._app is None:
            raise RuntimeError("компас недоступен")
        return self._app

    def active_path(self):
        return str(Path(self.root) / "work" / "part_AGENT_COPY.m3d")


def writer_for(data: bytes):
    def write(path, *_args):
        Path(path).write_bytes(data)
        return True
    return write


class TempRootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="kompas_wave4_")
        self.root = Path(self._tmp.name)
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        settings.reset_cache()
        self.addCleanup(settings.reset_cache)
        self.addCleanup(self._tmp.cleanup)

    def work_file(self, name: str, data: bytes = b"original\n") -> Path:
        target = self.root / "work" / name
        target.write_bytes(data)
        return target


class TestExportValidation(unittest.TestCase):
    def test_supported_formats(self):
        for fmt in ("step", "dxf", "pdf"):
            self.assertEqual(export_tools.normalize_format(fmt.upper()), fmt)
        with self.assertRaises(ValueError) as ctx:
            export_tools.normalize_format("stl")
        self.assertIn("unsupported_export_format", str(ctx.exception))

    def test_filename_suffix_must_match_format(self):
        self.assertEqual(export_tools.validate_relative_path("out.step", "step"), "out.step")
        self.assertEqual(export_tools.validate_relative_path("out.stp", "step"), "out.stp")
        with self.assertRaises(ValueError):
            export_tools.validate_relative_path("out.dxf", "step")
        with self.assertRaises(ValueError):
            export_tools.validate_relative_path("out.step", "dxf")

    def test_path_must_stay_inside_work(self):
        for bad in ("", "   ", "C:/tmp/out.step", "nested/out.step"):
            with self.assertRaises(ValueError):
                export_tools.validate_relative_path(bad, "step")

    def test_save_as_is_never_used_for_export(self):
        for methods in export_tools.EXPORT_METHODS.values():
            for name in methods:
                self.assertNotIn("saveas", name.lower(), name)


class TestExportDryRun(TempRootCase):
    def test_dry_run_does_not_touch_com(self):
        session = FakeExportSession(self.root, FakeCom(), None)
        plan = export_tools.export_file(session, "out.step", "step", apply=False)
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["executed"])
        self.assertEqual(session.connect_calls, 0)
        self.assertEqual(session.active_calls, 0)
        self.assertFalse(any(Path(self.root, "work", "out.step").is_file() for _ in [0]))

    def test_dry_run_lists_methods_and_limits(self):
        plan = export_tools.export_file(
            FakeExportSession(self.root, FakeCom(), None), "out.dxf", "dxf", apply=False
        )
        self.assertEqual(plan["methods_to_try"], list(export_tools.EXPORT_METHODS["dxf"]))
        self.assertTrue(plan["limits"])
        self.assertTrue(plan["save_as_excluded"])

    def test_existing_target_is_refused_before_any_call(self):
        self.work_file("out.step", b"old file")
        session = FakeExportSession(self.root, FakeCom(), None)
        with self.assertRaises(RuntimeError) as ctx:
            export_tools.export_file(session, "out.step", "step", apply=False)
        self.assertIn("target_already_exists", str(ctx.exception))
        self.assertEqual(session.connect_calls, 0)


class TestExportWithFakeCom(TempRootCase):
    def test_successful_export_is_verified_by_content(self):
        document = FakeCom(ExportToFile=writer_for(DXF_MINIMAL))
        session = FakeExportSession(self.root, document, FakeCom())
        result = export_tools.export_file(session, "out.dxf", "dxf", apply=True)

        self.assertTrue(result["executed"])
        self.assertTrue(result["ok"], result.get("artifact", {}).get("findings"))
        self.assertEqual(result["interface"]["method"], "document.ExportToFile")
        self.assertEqual(result["interface"]["argument_count"], 1)
        self.assertEqual(result["artifact"]["kind"], "dxf")
        self.assertEqual(result["artifact"]["counts"]["entities"], 2)

    def test_empty_file_counts_as_failed_export(self):
        document = FakeCom(ExportToFile=writer_for(b""))
        session = FakeExportSession(self.root, document, FakeCom())
        result = export_tools.export_file(session, "out.dxf", "dxf", apply=True)

        self.assertTrue(result["executed"])
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("Экспорт завершился" in w for w in result["warnings"]),
            result["warnings"],
        )
        # Файл не удаляется: его нужно посмотреть.
        self.assertTrue((self.root / "work" / "out.dxf").is_file())

    def test_method_that_writes_nothing_is_a_failure(self):
        def silent(path, *_args):
            return True

        document = FakeCom(ExportToFile=silent)
        session = FakeExportSession(self.root, document, FakeCom())
        with self.assertRaises(RuntimeError) as ctx:
            export_tools.export_file(session, "out.dxf", "dxf", apply=True)
        self.assertIn("export_produced_no_file", str(ctx.exception))

    def test_unknown_interface_fails_loudly(self):
        session = FakeExportSession(self.root, FakeCom(), FakeCom())
        with self.assertRaises(RuntimeError) as ctx:
            export_tools.export_file(session, "out.dxf", "dxf", apply=True)
        self.assertIn("export_interface_not_available", str(ctx.exception))


class TestCheckpoints(TempRootCase):
    def test_create_records_the_snapshot(self):
        target = self.work_file("part.m3d", b"version one\n")
        row = checkpoint.create(self.root, target, "part.create")
        self.assertEqual(row["action"], "part.create")
        self.assertEqual(row["target_name"], "part.m3d")
        self.assertTrue(Path(row["snapshot"]).is_file())
        self.assertEqual(row["sha256"], row["snapshot_sha256"])

    def test_missing_file_cannot_be_snapshot(self):
        with self.assertRaises(FileNotFoundError):
            checkpoint.create(self.root, self.root / "work" / "absent.m3d", "x")

    def test_restore_returns_original_content(self):
        target = self.work_file("part.m3d", b"version one\n")
        row = checkpoint.create(self.root, target, "edit")
        target.write_bytes(b"broken version\n")

        outcome = checkpoint.restore(self.root, row["id"])
        self.assertTrue(outcome["matches_snapshot"])
        self.assertEqual(target.read_bytes(), b"version one\n")

    def test_restore_requires_the_exact_id(self):
        target = self.work_file("part.m3d", b"data\n")
        checkpoint.create(self.root, target, "edit")
        with self.assertRaises(LookupError) as ctx:
            checkpoint.restore(self.root, "нет такого")
        self.assertIn("checkpoint_not_found", str(ctx.exception))

    def test_restore_refuses_a_mismatched_target(self):
        target = self.work_file("part.m3d", b"data\n")
        row = checkpoint.create(self.root, target, "edit")
        with self.assertRaises(ValueError) as ctx:
            checkpoint.restore(self.root, row["id"], "other.m3d")
        self.assertIn("checkpoint_target_mismatch", str(ctx.exception))

    def test_restore_is_impossible_outside_approved_roots(self):
        outside = self.root / "outside.m3d"
        outside.write_bytes(b"outside bridge roots\n")
        row = checkpoint.create(self.root, outside, "manual")
        with self.assertRaises(RuntimeError) as ctx:
            checkpoint.restore(self.root, row["id"])
        self.assertIn("path_not_allowed", str(ctx.exception))

    def test_list_filters_by_target(self):
        first = self.work_file("a.m3d", b"aaa\n")
        second = self.work_file("b.m3d", b"bbb\n")
        checkpoint.create(self.root, first, "edit")
        checkpoint.create(self.root, second, "edit")
        checkpoint.create(self.root, first, "edit")

        self.assertEqual(len(checkpoint.list_checkpoints(self.root)), 3)
        self.assertEqual(len(checkpoint.list_checkpoints(self.root, first)), 2)
        self.assertEqual(len(checkpoint.list_checkpoints(self.root, second)), 1)

    def test_prune_keeps_the_newest(self):
        target = self.work_file("part.m3d", b"data\n")
        for index in range(5):
            target.write_bytes(f"version {index}\n".encode())
            checkpoint.create(self.root, target, "edit")

        outcome = checkpoint.prune(self.root, keep=2)
        self.assertEqual(outcome["removed_count"], 3)
        self.assertEqual(outcome["remaining"], 2)
        self.assertGreater(outcome["freed_bytes"], 0)
        self.assertEqual(
            len([p for p in (self.root / "_BACKUPS").glob("*__part.m3d")]), 2
        )

    def test_prune_with_zero_removes_everything(self):
        target = self.work_file("part.m3d", b"data\n")
        checkpoint.create(self.root, target, "edit")
        outcome = checkpoint.prune(self.root, keep=0)
        self.assertEqual(outcome["removed_count"], 1)
        self.assertEqual(outcome["remaining"], 0)


class TestCheckpointManagement(TempRootCase):
    def test_list_operation_is_read_only(self):
        target = self.work_file("part.m3d", b"data\n")
        checkpoint.create(self.root, target, "edit")
        result = export_tools.manage_checkpoints(self.root, {"operation": "list"})
        self.assertTrue(result["read_only"])
        self.assertEqual(result["count"], 1)

    def test_prune_operation_reports_numbers(self):
        target = self.work_file("part.m3d", b"data\n")
        for index in range(3):
            target.write_bytes(f"v{index}\n".encode())
            checkpoint.create(self.root, target, "edit")
        result = export_tools.manage_checkpoints(self.root, {"operation": "prune", "keep": 1})
        self.assertEqual(result["removed_count"], 2)
        self.assertEqual(result["remaining"], 1)

    def test_restore_operation_requires_id_and_warns(self):
        with self.assertRaises(ValueError) as ctx:
            export_tools.manage_checkpoints(self.root, {"operation": "restore"})
        self.assertIn("checkpoint_id_required_for_restore", str(ctx.exception))

        target = self.work_file("part.m3d", b"original\n")
        row = checkpoint.create(self.root, target, "edit")
        target.write_bytes(b"broken\n")
        outcome = export_tools.manage_checkpoints(
            self.root, {"operation": "restore", "id": row["id"], "target": "part.m3d"}
        )
        self.assertFalse(outcome["read_only"])
        self.assertEqual(target.read_bytes(), b"original\n")
        self.assertTrue(outcome["warnings"])

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            export_tools.manage_checkpoints(self.root, {"operation": "destroy"})
        self.assertIn("unsupported_checkpoint_operation", str(ctx.exception))


class TestCatalogWave4(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_new_tools_are_routed(self):
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_export"][0], "export.file")
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_checkpoints"][0], "checkpoint.manage")

    def test_export_schema_matches_code_whitelist(self):
        schema = tools_catalog.TOOL_INDEX["kompas_export"]["inputSchema"]
        self.assertEqual(sorted(schema["required"]), ["format", "relative_path"])
        self.assertEqual(
            sorted(schema["properties"]["format"]["enum"]),
            sorted(export_tools.SUPPORTED_FORMATS),
        )
        self.assertFalse(schema["properties"]["apply"]["default"])

    def test_checkpoints_schema_matches_code(self):
        schema = tools_catalog.TOOL_INDEX["kompas_checkpoints"]["inputSchema"]
        self.assertEqual(
            sorted(schema["properties"]["operation"]["enum"]),
            ["create", "list", "prune", "restore"],
        )
        annotations = tools_catalog.TOOL_INDEX["kompas_checkpoints"]["annotations"]
        self.assertTrue(annotations["destructiveHint"])

    def test_export_is_not_marked_destructive(self):
        annotations = tools_catalog.TOOL_INDEX["kompas_export"]["annotations"]
        self.assertFalse(annotations["readOnlyHint"])
        self.assertFalse(annotations["destructiveHint"])

    def test_version_json_matches_catalog(self):
        declared = json.loads((REPO_ROOT / "VERSION.json").read_text(encoding="utf-8-sig"))
        snapshot = tools_catalog.catalog_snapshot()
        self.assertEqual(declared["mcp_tool_count"], snapshot["mcp_tool_count"])
        self.assertEqual(declared["mcp_tools"], snapshot["mcp_tools"])

    def test_new_modules_import_without_com(self):
        self.assertFalse(hasattr(export_tools, "win32com"))
        self.assertFalse(hasattr(checkpoint, "win32com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
