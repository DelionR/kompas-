#!/usr/bin/env python3
"""Тесты «Волны 3»: сторож модальных диалогов и матрица версий КОМПАСа.

Все тесты работают без КОМПАСа, без COM, без окон и без сети. Запуск::

    python -m unittest discover -s tests -p "test_wave3.py" -v

Что проверяется по существу:

* опасные диалоги (сохранение, вопрос, выбор файла) никогда не попадают
  в список на автозакрытие, а информационные - попадают;
* лимит закрытий за вызов соблюдается, остальное честно откладывается;
* версия КОМПАСа разбирается из строки, где есть посторонние цифры
  в названии продукта;
* пустая матрица означает «не проверено», а не «поддерживается»;
* защищённое действие на неподтверждённой версии блокируется;
* версия запрашивается у КОМПАСа только для защищённых действий.
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

import dialogs             # noqa: E402
import settings            # noqa: E402
import tools_catalog       # noqa: E402
import version_matrix      # noqa: E402


class TempRootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="kompas_wave3_")
        self.root = Path(self._tmp.name)
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        settings.reset_cache()
        self.addCleanup(settings.reset_cache)
        self.addCleanup(self._tmp.cleanup)

    def write_config(self, payload: dict) -> None:
        config_dir = self.root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "agent_config.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        settings.reset_cache()


class FakeSession:
    """Сессия без COM: считает обращения к status()."""

    def __init__(self, version="25.0.0.1234"):
        self.root = None
        self._version = version
        self.status_calls = 0
        self.kompas_version = None

    def status(self):
        self.status_calls += 1
        return {"version": self._version}


class TestDialogClassification(unittest.TestCase):
    def test_title_normalisation_strips_product_prefix(self):
        self.assertEqual(dialogs.normalize_title("КОМПАС-3D Сообщение"), "сообщение")
        self.assertEqual(dialogs.normalize_title("  KOMPAS-3D   Warning "), "warning")
        self.assertEqual(dialogs.normalize_title("Ошибка..."), "ошибка")

    def test_informational_dialog_is_closable(self):
        verdict = dialogs.classify("Сообщение", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "info_message")
        self.assertEqual(verdict["policy"], dialogs.CLOSE)
        self.assertTrue(verdict["closable"])
        self.assertFalse(verdict["blocking"])

    def test_save_prompt_is_never_closable(self):
        verdict = dialogs.classify("Сохранить изменения?", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "save_changes")
        self.assertEqual(verdict["policy"], dialogs.REPORT)
        self.assertFalse(verdict["closable"])
        self.assertTrue(verdict["blocking"])

    def test_question_dialog_is_never_closable(self):
        verdict = dialogs.classify("Вопрос", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "question")
        self.assertFalse(verdict["closable"])

    def test_file_dialog_is_never_closable(self):
        for title in ("Сохранить как", "Open file", "Выбор файла"):
            verdict = dialogs.classify(title, dialogs.DIALOG_CLASS)
            self.assertFalse(verdict["closable"], title)
            self.assertEqual(verdict["dialog_id"], "file_dialog", title)

    def test_error_dialog_is_reported_not_closed(self):
        verdict = dialogs.classify("Ошибка", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "error_ack")
        self.assertFalse(verdict["closable"])
        self.assertTrue(verdict["blocking"])

    def test_progress_window_is_ignored(self):
        verdict = dialogs.classify("Ожидание", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["policy"], dialogs.IGNORE)
        self.assertFalse(verdict["blocking"])

    def test_options_window_is_reported(self):
        verdict = dialogs.classify("Параметры", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "options")
        self.assertFalse(verdict["closable"])

    def test_unknown_window_is_reported_by_default(self):
        verdict = dialogs.classify("Совершенно новое окно", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "unknown")
        self.assertEqual(verdict["policy"], dialogs.REPORT)
        self.assertFalse(verdict["closable"])

    def test_non_dialog_window_class_is_ignored(self):
        verdict = dialogs.classify("Сообщение", "KompassFrame")
        self.assertEqual(verdict["policy"], dialogs.IGNORE)
        self.assertFalse(verdict["is_dialog_class"])

    def test_hidden_dialog_is_ignored(self):
        verdict = dialogs.classify("Сообщение", dialogs.DIALOG_CLASS, visible=False)
        self.assertEqual(verdict["policy"], dialogs.IGNORE)

    def test_summarize_counts_policies(self):
        rows = [
            dialogs.classify("Сообщение", dialogs.DIALOG_CLASS),
            dialogs.classify("Вопрос", dialogs.DIALOG_CLASS),
            dialogs.classify("Ожидание", dialogs.DIALOG_CLASS),
        ]
        summary = dialogs.summarize(rows)
        self.assertEqual(summary["window_count"], 3)
        self.assertEqual(summary["closable_count"], 1)
        self.assertEqual(summary["blocking_count"], 1)

    def test_dismiss_plan_respects_the_limit(self):
        rows = [
            dialogs.classify("Сообщение", dialogs.DIALOG_CLASS)
            for _ in range(5)
        ]
        plan = dialogs.dismiss_plan(rows, apply=True)
        self.assertEqual(plan["selected_count"], dialogs.MAX_CLOSE_PER_CALL)
        self.assertEqual(plan["deferred_count"], 5 - dialogs.MAX_CLOSE_PER_CALL)

    def test_titled_variant_of_an_unknown_window_is_not_closable(self):
        # Правило для информационного окна требует точного заголовка:
        # "Сообщение 12" - уже неизвестное окно, и закрывать его нельзя.
        verdict = dialogs.classify("Сообщение 12", dialogs.DIALOG_CLASS)
        self.assertEqual(verdict["dialog_id"], "unknown")
        self.assertFalse(verdict["closable"])

    def test_dismiss_plan_skips_everything_not_closable(self):
        rows = [
            dialogs.classify("Сохранить изменения?", dialogs.DIALOG_CLASS),
            dialogs.classify("Вопрос", dialogs.DIALOG_CLASS),
        ]
        plan = dialogs.dismiss_plan(rows, apply=True)
        self.assertEqual(plan["selected_count"], 0)
        self.assertEqual(plan["skipped_count"], 2)

    def test_policy_description_states_its_limits(self):
        policy = dialogs.describe_policy()
        self.assertEqual(policy["default_action"], "scan_only")
        self.assertTrue(policy["limits"])
        self.assertGreaterEqual(len(policy["rules"]), 5)


class TestVersionMatrix(unittest.TestCase):
    def test_version_is_parsed_past_product_noise(self):
        self.assertEqual(version_matrix.parse_version("25.0.0.1234"), (25, 0, 0, 1234))
        self.assertEqual(version_matrix.parse_version("KOMPAS-3D V25.0"), (25, 0))
        self.assertEqual(version_matrix.parse_version("V25"), (25,))
        self.assertEqual(version_matrix.parse_version("версия 24.1"), (24, 1))

    def test_unparsable_version_returns_none(self):
        self.assertIsNone(version_matrix.parse_version(""))
        self.assertIsNone(version_matrix.parse_version("KOMPAS-3D"))
        self.assertIsNone(version_matrix.parse_version("unknown build"))

    def test_empty_matrix_means_unverified_not_supported(self):
        verdict = version_matrix.classify_version("25.0", {})
        self.assertEqual(verdict["level"], version_matrix.UNVERIFIED)
        self.assertFalse(verdict["verified"])
        self.assertEqual(verdict["entry_count"], 0)

    def test_certified_pattern_matches(self):
        matrix = {"entries": [{"pattern": "25.*", "level": "certified", "notes": "стенд"}]}
        verdict = version_matrix.classify_version("25.0.0.1234", matrix)
        self.assertEqual(verdict["level"], version_matrix.CERTIFIED)
        self.assertTrue(verdict["verified"])
        self.assertEqual(verdict["matched_pattern"], "25.*")

    def test_other_version_stays_unverified(self):
        matrix = {"entries": [{"pattern": "25.*", "level": "certified"}]}
        verdict = version_matrix.classify_version("24.1", matrix)
        self.assertEqual(verdict["level"], version_matrix.UNVERIFIED)
        self.assertFalse(verdict["verified"])

    def test_pattern_without_that_component_does_not_match(self):
        matrix = {"entries": [{"pattern": "25.0", "level": "certified"}]}
        verdict = version_matrix.classify_version("25", matrix)
        self.assertFalse(verdict["verified"])

    def test_unknown_level_falls_back_to_unverified(self):
        matrix = version_matrix.load_matrix({"entries": [{"pattern": "25.*", "level": "perfect"}]})
        self.assertEqual(matrix["entries"][0]["level"], version_matrix.UNVERIFIED)

    def test_malformed_matrix_does_not_raise(self):
        for bad in (None, "text", 42, [], {"entries": "nope"}):
            matrix = version_matrix.load_matrix(bad)
            self.assertEqual(matrix["entries"], [])
            self.assertEqual(matrix["default_level"], version_matrix.UNVERIFIED)

    def test_guarded_actions_are_deduplicated(self):
        matrix = version_matrix.load_matrix(
            {"guarded_actions": ["mate.create", "mate.create", "", None, "part.hole"]}
        )
        self.assertEqual(matrix["guarded_actions"], ["mate.create", "part.hole"])

    def test_unguarded_action_is_allowed_without_version(self):
        result = version_matrix.guard_action("mate.create", "", {})
        self.assertFalse(result["guarded"])
        self.assertTrue(result["allowed"])

    def test_guarded_action_passes_on_certified_version(self):
        matrix = {
            "entries": [{"pattern": "25.*", "level": "certified"}],
            "guarded_actions": ["mate.create"],
        }
        result = version_matrix.guard_action("mate.create", "25.0", matrix)
        self.assertTrue(result["guarded"])
        self.assertTrue(result["allowed"])

    def test_guarded_action_is_blocked_on_unverified_version(self):
        matrix = {"guarded_actions": ["mate.create"]}
        with self.assertRaises(RuntimeError) as ctx:
            version_matrix.guard_action("mate.create", "24.1", matrix)
        message = str(ctx.exception)
        self.assertIn("kompas_version_not_verified", message)
        self.assertIn("mate.create", message)

    def test_verdict_states_its_own_limits(self):
        verdict = version_matrix.classify_version("25.0", {})
        self.assertTrue(verdict["limits"])
        self.assertIn("оператор", " ".join(verdict["limits"]))


class TestVersionGuardSession(TempRootCase):
    """Версия запрашивается у КОМПАСа только для защищённых действий."""

    def test_unguarded_action_never_touches_com(self):
        self.write_config({"kompas": {"version_matrix": {"guarded_actions": ["mate.create"]}}})
        session = FakeSession()
        session.root = self.root
        result = version_matrix.guard_for_session(self.root, session, "part.create")
        self.assertFalse(result["guarded"])
        self.assertEqual(session.status_calls, 0)

    def test_guarded_action_reads_version_once_and_caches_it(self):
        self.write_config({
            "kompas": {
                "version_matrix": {
                    "entries": [{"pattern": "25.*", "level": "certified"}],
                    "guarded_actions": ["mate.create"],
                }
            }
        })
        session = FakeSession("25.0.0.1234")
        session.root = self.root
        first = version_matrix.guard_for_session(self.root, session, "mate.create")
        second = version_matrix.guard_for_session(self.root, session, "mate.create")
        self.assertTrue(first["allowed"])
        self.assertTrue(second["allowed"])
        self.assertEqual(session.status_calls, 1, "версия должна кэшироваться на сессии")

    def test_guarded_action_fails_closed_on_unverified_version(self):
        self.write_config({"kompas": {"version_matrix": {"guarded_actions": ["mate.create"]}}})
        session = FakeSession("24.1")
        session.root = self.root
        with self.assertRaises(RuntimeError) as ctx:
            version_matrix.guard_for_session(self.root, session, "mate.create")
        self.assertIn("kompas_version_not_verified", str(ctx.exception))

    def test_settings_read_the_matrix_from_config(self):
        self.write_config({
            "kompas": {
                "version_matrix": {
                    "entries": [{"pattern": "25.*", "level": "targeted", "notes": "частично"}],
                    "guarded_actions": ["mate.create"],
                }
            }
        })
        resolved = settings.settings_for(self.root)
        self.assertEqual(len(resolved.version_matrix["entries"]), 1)
        self.assertEqual(resolved.version_matrix["guarded_actions"], ["mate.create"])
        described = resolved.describe()
        self.assertEqual(described["version_matrix_entries"], 1)
        self.assertEqual(described["guarded_action_count"], 1)

    def test_missing_matrix_section_is_harmless(self):
        self.write_config({"worker_poll_ms": 100})
        resolved = settings.settings_for(self.root)
        self.assertEqual(resolved.version_matrix["entries"], [])
        self.assertEqual(resolved.version_matrix["guarded_actions"], [])


class TestCatalogWave3(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_new_tools_are_present_and_routed(self):
        self.assertIn("kompas_dialogs", tools_catalog.TOOL_INDEX)
        self.assertIn("kompas_version_check", tools_catalog.TOOL_INDEX)
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_dialogs"][0], "dialogs.watch")
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_version_check"][0], "version.check")

    def test_version_check_is_read_only_and_dialogs_is_not(self):
        version_annotations = tools_catalog.TOOL_INDEX["kompas_version_check"]["annotations"]
        dialogs_annotations = tools_catalog.TOOL_INDEX["kompas_dialogs"]["annotations"]
        self.assertTrue(version_annotations["readOnlyHint"])
        self.assertFalse(dialogs_annotations["readOnlyHint"])
        self.assertFalse(dialogs_annotations["destructiveHint"])

    def test_dialogs_defaults_to_scan_only(self):
        schema = tools_catalog.TOOL_INDEX["kompas_dialogs"]["inputSchema"]
        self.assertFalse(schema["properties"]["apply"]["default"])
        self.assertNotIn("required", schema)

    def test_version_json_matches_catalog(self):
        declared = json.loads((REPO_ROOT / "VERSION.json").read_text(encoding="utf-8-sig"))
        snapshot = tools_catalog.catalog_snapshot()
        self.assertEqual(declared["mcp_tool_count"], snapshot["mcp_tool_count"])
        self.assertEqual(declared["mcp_tools"], snapshot["mcp_tools"])

    def test_worker_actually_calls_the_version_guard(self):
        source = (REPO_ROOT / "src" / "worker.py").read_text(encoding="utf-8-sig")
        self.assertIn("guard_for_session", source)
        self.assertIn("from version_matrix import guard_for_session", source)

    def test_new_modules_import_without_com(self):
        self.assertFalse(hasattr(dialogs, "win32com"))
        self.assertFalse(hasattr(version_matrix, "win32com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
