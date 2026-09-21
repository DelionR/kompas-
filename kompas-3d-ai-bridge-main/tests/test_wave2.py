#!/usr/bin/env python3
"""Тесты «Волны 2»: сопряжения сборки и проверка артефактов экспорта.

Все тесты работают без КОМПАСа, без COM и без сети. Запуск::

    python -m unittest discover -s tests -p "test_wave2.py" -v

Что проверяется по существу:

* валидация сопряжений отклоняет бессмысленные комбинации (значение там,
  где его быть не должно; отсутствие значения там, где оно обязательно);
* схема инструмента ``kompas_mate_create`` в каталоге не может разъехаться
  с реальным списком типов сопряжений в коде;
* проверка артефактов различает целый файл и обрезанный, а не полагается
  на расширение;
* артефакт вне разрешённых корней не читается вообще.
"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
MCP = REPO_ROOT / "mcp"
for path in (str(SRC), str(MCP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import artifact_check     # noqa: E402
import mate_tools         # noqa: E402
import settings           # noqa: E402
import tools_catalog      # noqa: E402


STEP_MINIMAL = (
    "ISO-10303-21;\n"
    "HEADER;\n"
    "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
    "ENDSEC;\n"
    "DATA;\n"
    "#1=MANIFOLD_SOLID_BREP('solid',#2);\n"
    "#2=ADVANCED_FACE('face',(#3),#4,.T.);\n"
    "ENDSEC;\n"
    "END-ISO-10303-21;\n"
).encode("latin-1")

DXF_MINIMAL = (
    "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1015\n"
    "9\n$INSUNITS\n70\n4\n0\nENDSEC\n"
    "0\nSECTION\n2\nENTITIES\n"
    "0\nLINE\n0\nCIRCLE\n"
    "0\nENDSEC\n0\nEOF\n"
).encode("latin-1")

PDF_MINIMAL = (
    "%PDF-1.4\n"
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    "3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n"
    "trailer\n<< /Root 1 0 R >>\n%%EOF\n"
).encode("latin-1")


def png_minimal(width: int = 4, height: int = 3) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = (
        struct.pack(">I", len(ihdr_payload))
        + b"IHDR"
        + ihdr_payload
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_payload))
    )
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    return signature + ihdr + iend


class TempRootCase(unittest.TestCase):
    """Временный корень моста с каталогом work."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="kompas_wave2_")
        self.root = Path(self._tmp.name)
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        settings.reset_cache()
        self.addCleanup(settings.reset_cache)
        self.addCleanup(self._tmp.cleanup)

    def write_artifact(self, name: str, data: bytes) -> Path:
        target = self.root / "work" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target


class TestMateValidation(unittest.TestCase):
    """Валидация входных данных сопряжений - чистые функции."""

    def test_all_documented_kinds_are_accepted(self):
        for kind in mate_tools.MATE_KINDS:
            self.assertEqual(mate_tools.normalize_kind(kind.upper()), kind)

    def test_unknown_kind_is_rejected_with_supported_list(self):
        with self.assertRaises(ValueError) as ctx:
            mate_tools.normalize_kind("weld")
        self.assertIn("unsupported_mate_kind", str(ctx.exception))
        self.assertIn("coincident", str(ctx.exception))

    def test_distance_requires_value(self):
        with self.assertRaises(ValueError) as ctx:
            mate_tools.validate_value("distance", None)
        self.assertIn("value_required_for_distance", str(ctx.exception))

    def test_valueless_kind_rejects_value(self):
        with self.assertRaises(ValueError) as ctx:
            mate_tools.validate_value("coincident", 5)
        self.assertIn("value_not_allowed_for_coincident", str(ctx.exception))

    def test_value_must_be_positive_and_bounded(self):
        for bad in (0, -1, mate_tools.MAX_DISTANCE_MM + 1):
            with self.assertRaises(ValueError):
                mate_tools.validate_value("distance", bad)
        self.assertEqual(mate_tools.validate_value("distance", 12.5), 12.5)

    def test_angle_uses_its_own_limit(self):
        self.assertEqual(mate_tools.validate_value("angle", 360.0), 360.0)
        with self.assertRaises(ValueError) as ctx:
            mate_tools.validate_value("angle", 361.0)
        self.assertIn("deg", str(ctx.exception))

    def test_non_finite_value_is_rejected(self):
        with self.assertRaises(ValueError):
            mate_tools.validate_value("distance", float("nan"))
        with self.assertRaises(ValueError):
            mate_tools.validate_value("distance", "abc")

    def test_alignment_whitelist(self):
        self.assertEqual(mate_tools.validate_alignment(None), "auto")
        self.assertEqual(mate_tools.validate_alignment("ALIGNED"), "aligned")
        with self.assertRaises(ValueError):
            mate_tools.validate_alignment("flipped")

    def test_assembly_filename_must_be_controlled_copy(self):
        with self.assertRaises(ValueError):
            mate_tools.validate_assembly_filename("C:/elsewhere/part_AGENT_COPY.a3d")
        with self.assertRaises(ValueError):
            mate_tools.validate_assembly_filename("part.a3d")
        with self.assertRaises(ValueError):
            mate_tools.validate_assembly_filename("part_AGENT_COPY.m3d")
        self.assertEqual(
            mate_tools.validate_assembly_filename("unit_AGENT_COPY.a3d"),
            "unit_AGENT_COPY.a3d",
        )

    def test_selector_shapes(self):
        self.assertEqual(mate_tools.validate_selector(0, "selector_a"), 0)
        self.assertEqual(mate_tools.validate_selector("0/1", "selector_a"), "0/1")
        self.assertEqual(mate_tools.validate_selector([0, 2], "selector_a"), [0, 2])
        for bad in (True, None, -1, "", [], [0, -1], [0, "1"], 1.5):
            with self.assertRaises(ValueError):
                mate_tools.validate_selector(bad, "selector_a")

    def test_one_mate_per_call_is_a_deliberate_limit(self):
        self.assertEqual(mate_tools.MAX_MATES_PER_CALL, 1)


class TestMateCatalogContract(unittest.TestCase):
    """Схема инструмента и код не могут разъехаться."""

    def test_mate_create_schema_matches_code_whitelist(self):
        tool = tools_catalog.TOOL_INDEX["kompas_mate_create"]
        schema = tool["inputSchema"]
        enum = schema["properties"]["kind"]["enum"]
        self.assertEqual(sorted(enum), sorted(mate_tools.MATE_KINDS))

    def test_value_is_optional_but_selector_pair_is_required(self):
        schema = tools_catalog.TOOL_INDEX["kompas_mate_create"]["inputSchema"]
        self.assertEqual(
            sorted(schema["required"]),
            ["assembly_filename", "kind", "selector_a", "selector_b"],
        )
        self.assertIn("value", schema["properties"])

    def test_mate_tools_are_routed_to_the_worker(self):
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_mate_read"][0], "mate.read")
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_mate_create"][0], "mate.create")
        self.assertEqual(tools_catalog.ACTION_MAP["kompas_artifact_check"][0], "artifact.check")

    def test_mate_read_and_artifact_check_are_marked_read_only(self):
        for name in ("kompas_mate_read", "kompas_artifact_check"):
            annotations = tools_catalog.TOOL_INDEX[name].get("annotations") or {}
            self.assertTrue(annotations.get("readOnlyHint"), name)

    def test_mate_create_does_not_claim_to_be_destructive(self):
        annotations = tools_catalog.TOOL_INDEX["kompas_mate_create"].get("annotations") or {}
        self.assertFalse(annotations.get("readOnlyHint"))
        self.assertFalse(annotations.get("destructiveHint"))

    def test_module_imports_without_com(self):
        self.assertFalse(hasattr(mate_tools, "win32com"))
        self.assertFalse(hasattr(artifact_check, "win32com"))


class TestArtifactCheck(TempRootCase):
    """Проверка артефактов по содержимому."""

    def test_valid_step_is_accepted(self):
        self.write_artifact("out.step", STEP_MINIMAL)
        result = artifact_check.artifact_check(self.root, "out.step")
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["kind"], "step")
        self.assertEqual(result["counts"]["entities"], 2)
        self.assertEqual(result["counts"]["solid_breps"], 1)
        self.assertEqual(result["counts"]["file_schema"], "AUTOMOTIVE_DESIGN")

    def test_truncated_step_is_rejected(self):
        self.write_artifact("cut.step", STEP_MINIMAL.replace(b"END-ISO-10303-21;\n", b""))
        result = artifact_check.artifact_check(self.root, "cut.step")
        self.assertFalse(result["ok"])
        self.assertIn("missing_end_iso_10303_21_marker", result["findings"])

    def test_step_without_geometry_is_rejected(self):
        body = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=PRODUCT('p');\nENDSEC;\nEND-ISO-10303-21;\n"
        self.write_artifact("empty.step", body)
        result = artifact_check.artifact_check(self.root, "empty.step")
        self.assertFalse(result["ok"])
        self.assertIn("no_solid_or_face_geometry", result["findings"])

    def test_empty_file_is_not_a_valid_step(self):
        self.write_artifact("zero.step", b"")
        result = artifact_check.artifact_check(self.root, "zero.step")
        self.assertFalse(result["ok"])
        self.assertIn("missing_iso_10303_21_header", result["findings"])

    def test_valid_dxf_is_accepted(self):
        self.write_artifact("out.dxf", DXF_MINIMAL)
        result = artifact_check.artifact_check(self.root, "out.dxf")
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["counts"]["entities"], 2)
        self.assertEqual(result["counts"]["acad_version"], "AC1015")
        self.assertEqual(result["counts"]["units_code"], "4")

    def test_dxf_without_eof_is_rejected(self):
        self.write_artifact("cut.dxf", DXF_MINIMAL.replace(b"0\nEOF\n", b""))
        result = artifact_check.artifact_check(self.root, "cut.dxf")
        self.assertFalse(result["ok"])
        self.assertIn("missing_eof_marker", result["findings"])

    def test_valid_pdf_reports_pages(self):
        self.write_artifact("out.pdf", PDF_MINIMAL)
        result = artifact_check.artifact_check(self.root, "out.pdf")
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["counts"]["pages"], 1)
        self.assertEqual(result["counts"]["version"], "1.4")

    def test_pdf_pages_object_is_not_counted_as_page(self):
        self.write_artifact("pages.pdf", PDF_MINIMAL)
        result = artifact_check.artifact_check(self.root, "pages.pdf")
        self.assertEqual(result["counts"]["pages"], 1)

    def test_png_dimensions_are_read_from_ihdr(self):
        self.write_artifact("shot.png", png_minimal(1280, 720))
        result = artifact_check.artifact_check(self.root, "shot.png")
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["counts"]["width"], 1280)
        self.assertEqual(result["counts"]["height"], 720)

    def test_broken_png_is_rejected(self):
        self.write_artifact("broken.png", png_minimal()[:20])
        result = artifact_check.artifact_check(self.root, "broken.png")
        self.assertFalse(result["ok"])

    def test_unknown_kind_is_not_treated_as_valid(self):
        self.write_artifact("model.m3d", b"not a checked format")
        result = artifact_check.artifact_check(self.root, "model.m3d")
        self.assertFalse(result["ok"])
        self.assertIn("unsupported_artifact_kind", result["findings"])
        self.assertIsNone(result["kind"])

    def test_kind_is_detected_by_content_when_suffix_lies(self):
        self.write_artifact("actually_png.step", png_minimal(10, 10))
        result = artifact_check.artifact_check(self.root, "actually_png.step")
        self.assertEqual(result["kind"], "step")
        self.assertFalse(result["ok"])

    def test_large_file_is_scanned_partially_and_says_so(self):
        big = STEP_MINIMAL + b"#3=CARTESIAN_POINT('p',(0.,0.,0.));\n" * 500
        self.write_artifact("big.step", big)
        result = artifact_check.artifact_check(self.root, "big.step", max_scan_bytes=64)
        self.assertTrue(result["scan_truncated"])
        self.assertTrue(any("scan_truncated" in w for w in result["warnings"]))

    def test_path_outside_approved_roots_is_refused(self):
        outside = self.root / "outside.step"
        outside.write_bytes(STEP_MINIMAL)
        with self.assertRaises(RuntimeError) as ctx:
            artifact_check.artifact_check(self.root, "../outside.step")
        self.assertIn("path_outside_approved_roots", str(ctx.exception))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            artifact_check.artifact_check(self.root, "absent.step")

    def test_empty_relative_path_is_refused(self):
        with self.assertRaises(ValueError):
            artifact_check.artifact_check(self.root, "   ")

    def test_every_verdict_states_its_own_limits(self):
        self.write_artifact("out.step", STEP_MINIMAL)
        result = artifact_check.artifact_check(self.root, "out.step")
        self.assertTrue(result["limits"])
        self.assertIn("геометрии", " ".join(result["limits"]))


class TestCatalogWave2(unittest.TestCase):
    """Каталог остаётся единственным источником поверхности."""

    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_new_tools_are_present(self):
        for name in ("kompas_mate_read", "kompas_mate_create", "kompas_artifact_check"):
            self.assertIn(name, tools_catalog.TOOL_INDEX)

    def test_version_json_matches_catalog(self):
        version_file = REPO_ROOT / "VERSION.json"
        declared = json.loads(version_file.read_text(encoding="utf-8-sig"))
        snapshot = tools_catalog.catalog_snapshot()
        self.assertEqual(declared["mcp_tool_count"], snapshot["mcp_tool_count"])
        self.assertEqual(declared["mcp_tools"], snapshot["mcp_tools"])
        self.assertEqual(declared["worker_version"], snapshot["server_version"])

    def test_every_core_tool_is_routed(self):
        for tool in tools_catalog.TOOLS:
            name = tool["name"]
            if name in tools_catalog.WRAPPER_SIDE_TOOLS:
                continue
            self.assertIn(name, tools_catalog.ACTION_MAP, name)

    def test_descriptions_stay_useful_for_a_model(self):
        for tool in tools_catalog.TOOLS:
            self.assertGreater(len(tool["description"]), 40, tool["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
