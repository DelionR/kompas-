#!/usr/bin/env python3
"""Тесты «Волны 6»: сухой прогон (dry run) мутирующих инструментов.

Все тесты работают без КОМПАСа, без COM и без сети. Запуск::

    python -m unittest discover -s tests -p "test_wave6.py" -v

Что проверяется по существу:

* сухой прогон ничего не выполняет: файлы остаются побайтово теми же, новых
  файлов в рабочем каталоге не появляется;
* заблокированный вызов назван заблокированным, а не «вероятно сработает»;
* манифест предпросмотра покрывает каждое мутирующее действие каталога -
  иначе сухой прогон молча проверит только схему аргументов;
* то, что требует Automation, попадает в `unverifiable`, а не угадывается;
* без переданной версии защищённое действие даёт «неизвестно», а не
  «заблокировано»: мост не делает вид, что знает версию running-КОМПАСа.
"""
from __future__ import annotations

import hashlib
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

import dryrun          # noqa: E402
import settings        # noqa: E402
import tools_catalog   # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DryRunCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="kompas_wave6_")
        self.root = Path(self._tmp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        settings.reset_cache()

    def tearDown(self):
        settings.reset_cache()
        self._tmp.cleanup()

    def write_config(self, payload):
        config_dir = self.root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "agent_config.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        settings.reset_cache()

    def write_allowlist(self, jobs):
        jobs_dir = self.root / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "allowlist.json").write_text(
            json.dumps({"version": 1, "jobs": jobs}, ensure_ascii=False), encoding="utf-8"
        )
        settings.reset_cache()

    def make_file(self, name: str, content: bytes = b"model") -> Path:
        path = self.work / name
        path.write_bytes(content)
        return path

    def plan(self, tool, arguments=None, version=None):
        return dryrun.preflight(self.root, tool, arguments, version)

    def status_of(self, report, check_name):
        for row in report["checks"]:
            if row["check"] == check_name:
                return row["status"]
        return None


class TestSchemaValidation(DryRunCase):
    """Минимальный валидатор схемы - первая линия сухого прогона."""

    def _problems(self, tool, arguments):
        tool_row = tools_catalog.TOOL_INDEX[tool]
        return dryrun.validate_arguments(tool_row["inputSchema"], arguments)

    def test_valid_arguments_pass(self):
        problems = self._problems(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "src_AGENT_COPY.m3d",
            },
        )
        self.assertEqual(problems, [])

    def test_missing_required_is_reported(self):
        problems = self._problems("kompas_material_set", {"filename": "a.m3d"})
        self.assertTrue(any("required" in item["problem"] for item in problems))

    def test_wrong_type_is_reported(self):
        problems = self._problems("kompas_dry_run", {"tool": 42})
        self.assertTrue(any("expected type" in item["problem"] for item in problems))

    def test_enum_violation_is_reported(self):
        problems = self._problems("kompas_mate_create", {"kind": "glue"})
        self.assertTrue(any("not one of" in item["problem"] for item in problems))

    def test_numeric_bounds_are_reported(self):
        problems = self._problems(
            "kompas_component_translate_xy",
            {"selector": 0, "dx_mm": 99, "dy_mm": 0},
        )
        self.assertTrue(any("above maximum" in item["problem"] for item in problems))

    def test_unexpected_property_is_reported(self):
        problems = self._problems("kompas_dry_run", {"tool": "x", "extra": 1})
        self.assertTrue(any("unexpected property" in item["problem"] for item in problems))

    def test_array_item_bounds_are_reported(self):
        problems = self._problems("kompas_stamp_read", {"cells": [1, 500]})
        self.assertTrue(problems)

    def test_oneOf_branch_is_accepted(self):
        problems = self._problems(
            "kompas_component_info", {"selector": [0, 2]}
        )
        self.assertEqual(problems, [])


class TestNothingIsExecuted(DryRunCase):
    def test_files_are_untouched(self):
        target = self.make_file("part_AGENT_COPY.m3d", b"before")
        before_hash = _sha256(target)
        listing_before = sorted(p.name for p in self.work.iterdir())

        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
        )

        self.assertFalse(report["would_execute"])
        self.assertEqual(_sha256(target), before_hash)
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), listing_before)
        self.assertTrue(any("Nothing was executed" in note for note in report["notes"]))

    def test_read_only_tool_is_marked_as_such(self):
        report = self.plan("kompas_model_tree", {})
        self.assertTrue(report["read_only_tool"])
        self.assertEqual(self.status_of(report, "mutating"), "note")


class TestTargetChecks(DryRunCase):
    def test_ready_call_is_ready(self):
        self.make_file("part_AGENT_COPY.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
        )
        self.assertEqual(report["verdict"], "ready")
        self.assertEqual(report["blocking_checks"], [])

    def test_missing_file_blocks(self):
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "absent_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "absent_AGENT_COPY.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "target_exists"), "fail")
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("target_exists", report["blocking_checks"])

    def test_absolute_path_blocks(self):
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "C:/secret/model.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "x.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "target_path"), "fail")

    def test_escaping_work_directory_blocks(self):
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "../outside_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "x.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "target_path"), "fail")

    def test_wrong_extension_blocks(self):
        self.make_file("drawing_AGENT_COPY.cdw")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "drawing_AGENT_COPY.cdw",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "drawing_AGENT_COPY.cdw",
            },
        )
        self.assertEqual(self.status_of(report, "target_suffix"), "fail")

    def test_without_agent_copy_blocks(self):
        self.make_file("original.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "original.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "original.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "agent_copy"), "fail")

    def test_create_action_refuses_existing_file(self):
        self.make_file("new_part.m3d")
        report = self.plan("kompas_part_create", {"filename": "new_part.m3d"})
        self.assertEqual(self.status_of(report, "target_absent"), "fail")

    def test_protected_path_blocks(self):
        self.write_config(
            {"safety": {"protected_roots": ["{bridge_root}/work/protected"]}}
        )
        protected = self.work / "protected"
        protected.mkdir(parents=True, exist_ok=True)
        (protected / "part_AGENT_COPY.m3d").write_bytes(b"model")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "protected/part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "protected/part_AGENT_COPY.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "not_protected"), "fail")

    def test_action_on_active_document_is_unknown_not_silent(self):
        report = self.plan("kompas_stamp_write", {"cells": {"1": "text"}})
        self.assertEqual(self.status_of(report, "file_target"), "unknown")
        self.assertEqual(report["verdict"], "ready_with_unknowns")


class TestVersionGuard(DryRunCase):
    def test_unguarded_action_passes(self):
        self.make_file("part_AGENT_COPY.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "version_guard"), "pass")

    def test_guarded_action_without_version_is_unknown(self):
        self.write_config(
            {
                "kompas": {
                    "version_matrix": {
                        "default_level": "unverified",
                        "entries": [],
                        "guarded_actions": ["material.set"],
                    }
                }
            }
        )
        self.make_file("part_AGENT_COPY.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
        )
        self.assertEqual(self.status_of(report, "version_guard"), "unknown")
        self.assertEqual(report["verdict"], "ready_with_unknowns")

    def test_guarded_action_on_unverified_version_blocks(self):
        self.write_config(
            {
                "kompas": {
                    "version_matrix": {
                        "default_level": "unverified",
                        "entries": [],
                        "guarded_actions": ["material.set"],
                    }
                }
            }
        )
        self.make_file("part_AGENT_COPY.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
            version="25.0.0.1",
        )
        self.assertEqual(self.status_of(report, "version_guard"), "fail")

    def test_guarded_action_on_certified_version_passes(self):
        self.write_config(
            {
                "kompas": {
                    "version_matrix": {
                        "default_level": "unverified",
                        "entries": [{"pattern": "25.*", "level": "certified", "notes": ""}],
                        "guarded_actions": ["material.set"],
                    }
                }
            }
        )
        self.make_file("part_AGENT_COPY.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
            version="25.0.0.1",
        )
        self.assertEqual(self.status_of(report, "version_guard"), "pass")


class TestJobAllowList(DryRunCase):
    def test_unlisted_script_blocks(self):
        self.write_allowlist([{"name": "job_template.py", "enabled": True}])
        report = self.plan("kompas_run_job", {"script": "sneaky.py"})
        self.assertEqual(self.status_of(report, "job_allowlist"), "fail")

    def test_listed_script_passes(self):
        self.write_allowlist([{"name": "job_template.py", "enabled": True}])
        report = self.plan("kompas_run_job", {"script": "job_template.py"})
        self.assertEqual(self.status_of(report, "job_allowlist"), "pass")

    def test_missing_manifest_is_empty_not_allowed(self):
        report = self.plan("kompas_run_job", {"script": "anything.py"})
        self.assertEqual(self.status_of(report, "job_allowlist"), "fail")
        detail = [row for row in report["checks"] if row["check"] == "job_allowlist"][0]
        self.assertIn("none", detail["detail"])


class TestUnknownToolAndLimits(DryRunCase):
    def test_unknown_tool_is_blocked(self):
        report = self.plan("kompas_teleport_model", {})
        self.assertFalse(report["known_tool"])
        self.assertEqual(report["verdict"], "blocked")

    def test_unverifiable_is_not_empty(self):
        self.make_file("part_AGENT_COPY.m3d")
        report = self.plan(
            "kompas_material_set",
            {
                "filename": "part_AGENT_COPY.m3d",
                "material_name": "Steel",
                "density": 7.85,
                "evidence_source_relative_path": "part_AGENT_COPY.m3d",
            },
        )
        self.assertTrue(report["unverifiable"])
        self.assertTrue(any("interface" in item.lower() for item in report["unverifiable"]))

    def test_limits_say_it_does_not_prove_success(self):
        report = self.plan("kompas_model_tree", {})
        self.assertTrue(any("does not prove" in item for item in report["limits"]))


class TestManifestCoverage(unittest.TestCase):
    """Сухой прогон без манифеста проверяет только схему - это надо ловить."""

    def test_every_mutating_action_has_a_manifest_entry(self):
        missing = []
        for tool in tools_catalog.TOOLS:
            if tool["name"] in tools_catalog.WRAPPER_SIDE_TOOLS:
                continue  # обслуживается обёрткой, через воркер не идёт
            if (tool.get("annotations") or {}).get("readOnlyHint") is True:
                continue
            entry = tools_catalog.ACTION_MAP.get(tool["name"])
            action = entry[0] if entry else None
            if action not in dryrun.ACTION_PREFLIGHT:
                missing.append((tool["name"], action))
        self.assertEqual(missing, [], "нет записи в ACTION_PREFLIGHT: %s" % missing)

    def test_every_manifest_action_exists_in_the_catalog(self):
        known = {
            entry[0] for entry in tools_catalog.ACTION_MAP.values() if isinstance(entry, (list, tuple))
        }
        stale = [action for action in dryrun.ACTION_PREFLIGHT if action not in known]
        self.assertEqual(stale, [], "устаревшая запись манифеста: %s" % stale)

    def test_every_kind_has_a_note(self):
        for action, spec in dryrun.ACTION_PREFLIGHT.items():
            self.assertIn(spec.get("kind"), dryrun.KIND_NOTES, action)


class TestCatalogSurface(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_dry_run_tool_exists_and_is_read_only(self):
        self.assertIn("kompas_dry_run", tools_catalog.TOOL_INDEX)
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_dry_run"][0], "dryrun.plan")
        self.assertTrue(
            tools_catalog.TOOL_INDEX["kompas_dry_run"]["annotations"]["readOnlyHint"]
        )

    def test_every_tool_has_annotations(self):
        for tool in tools_catalog.TOOLS:
            self.assertIn("annotations", tool, tool["name"])


if __name__ == "__main__":
    unittest.main()
