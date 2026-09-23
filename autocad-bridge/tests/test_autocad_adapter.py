"""Адаптер AutoCAD: чистые функции и тонкий COM-слой с моком.

AutoCAD на машине разработки отсутствует — это проверено (в реестре нет ни
``AutoCAD.Application.*``, ни раздела ``Autodesk``). Поэтому COM здесь
подменяется моком, а проверяется главное: **отказ вместо правдоподобной
подстановки**.

Что именно проверяется:

* релиз → ProgID берётся из таблицы, а релиз вне таблицы **не угадывается**;
* матрица подтверждённых версий пуста по умолчанию, значит AutoCAD 2019 —
  ``unverified``, а не «поддерживается»;
* документ без единиц (``INSUNITS = 0``) — отказ, а не «миллиметры»;
* раскладка без высоты платы — отказ, потому что развернуть Y не относительно
  чего;
* координата Y действительно разворачивается: элемент у верхней кромки
  платы получает наибольшую Y в чертеже.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import autocad_version  # noqa: E402
import autocad_units  # noqa: E402
import autocad_layout  # noqa: E402
import autocad_session  # noqa: E402
import autocad_tools  # noqa: E402
import tools_catalog  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def mm_units():
    return {"insunits": 4, "units_name": "миллиметры",
            "mm_per_unit": 1.0, "units_per_mm": 1.0, "is_metric": True}


def plate(**extra):
    data = {"width_mm": 600.0, "height_mm": 800.0}
    data.update(extra)
    return data


def elements():
    return [
        {"name": "QF1", "x_mm": 0.0, "y_mm": 0.0, "width_mm": 18.0, "height_mm": 90.0},
        {"name": "QF2", "x_mm": 18.0, "y_mm": 0.0, "width_mm": 18.0, "height_mm": 90.0},
    ]


class ReleaseTests(unittest.TestCase):
    """Релиз → ProgID: таблица, а не догадка."""

    def test_2019_is_release_23(self):
        progid, reason = autocad_version.progid_for_release("23")
        self.assertEqual(progid, "AutoCAD.Application.23")
        self.assertIsNone(reason)

    def test_target_matches_the_requested_build(self):
        self.assertEqual(autocad_version.TARGET_RELEASE, "23")
        self.assertIn("2019", autocad_version.TARGET_LABEL)

    def test_release_outside_table_is_refused(self):
        progid, reason = autocad_version.progid_for_release("99")
        self.assertIsNone(progid)
        self.assertEqual(reason, "unknown_release")

    def test_release_parsed_from_version_property(self):
        self.assertEqual(autocad_version.parse_release("23.0s (LMS Tech)"), "23")
        self.assertEqual(autocad_version.parse_release("P.162.0.0 AutoCAD 2019.1.2"), "23")

    def test_unparseable_version_gives_nothing(self):
        self.assertIsNone(autocad_version.parse_release(""))
        self.assertIsNone(autocad_version.parse_release("нет версии"))

    def test_known_neighbours_are_mapped(self):
        self.assertEqual(autocad_version.progid_for_release("23.1")[0],
                         "AutoCAD.Application.23.1")
        self.assertEqual(autocad_version.progid_for_release("24")[0],
                         "AutoCAD.Application.24")
        self.assertEqual(autocad_version.progid_for_release("22")[0],
                         "AutoCAD.Application.22")


class VersionMatrixTests(unittest.TestCase):
    """Матрица пуста по умолчанию: unverified — это «не проверено»."""

    def test_default_matrix_has_no_entries(self):
        self.assertEqual(autocad_version.DEFAULT_MATRIX["entries"], [])

    def test_target_release_is_unverified_until_operator_confirms(self):
        info = autocad_version.describe("23")
        self.assertEqual(info["level"], autocad_version.UNVERIFIED)
        self.assertFalse(info["allowed"])

    def test_operator_entry_turns_it_certified(self):
        matrix = {"entries": [{"pattern": "23", "level": "certified"}],
                  "default_level": "unverified", "guarded_actions": []}
        info = autocad_version.describe("23", matrix)
        self.assertEqual(info["level"], autocad_version.CERTIFIED)
        self.assertTrue(info["allowed"])

    def test_pattern_with_wildcard_matches(self):
        matrix = {"entries": [{"pattern": "23*", "level": "targeted"}],
                  "default_level": "unverified", "guarded_actions": []}
        self.assertTrue(autocad_version.describe("23.1", matrix)["allowed"])

    def test_unknown_level_falls_back_to_unverified(self):
        matrix = {"entries": [{"pattern": "23", "level": "какое-то"}],
                  "default_level": "unverified", "guarded_actions": []}
        self.assertEqual(autocad_version.describe("23", matrix)["level"],
                         autocad_version.UNVERIFIED)


class UnitsTests(unittest.TestCase):
    """INSUNITS: единицы документа, а не предположение."""

    def test_millimetres(self):
        units, reason = autocad_units.describe_units(4)
        self.assertIsNone(reason)
        self.assertEqual(units["mm_per_unit"], 1.0)

    def test_inches_are_converted(self):
        units, _ = autocad_units.describe_units(1)
        self.assertEqual(units["mm_per_unit"], 25.4)
        self.assertAlmostEqual(autocad_units.mm_to_units(600.0, units),
                               600.0 / 25.4, places=6)

    def test_unitless_is_a_refusal(self):
        units, reason = autocad_units.describe_units(0)
        self.assertIsNone(units)
        self.assertEqual(reason, "unitless_document")

    def test_unknown_code_is_not_treated_as_millimetres(self):
        units, reason = autocad_units.describe_units(13)
        self.assertIsNone(units)
        self.assertEqual(reason, "unknown_units")

    def test_missing_value_is_a_refusal(self):
        self.assertEqual(autocad_units.describe_units(None)[1], "no_insunits")

    def test_round_trip(self):
        units, _ = autocad_units.describe_units(6)
        self.assertAlmostEqual(autocad_units.units_to_mm(
            autocad_units.mm_to_units(1500.0, units), units), 1500.0, places=6)


class LayoutTests(unittest.TestCase):
    """Раскладка → чертёж: разворот Y и отказ без высоты платы."""

    def test_y_is_flipped_about_plate_height(self):
        result = autocad_layout.place({
            "plate": plate(), "units": mm_units(), "elements": elements()})
        self.assertEqual(result["verdict"], "computed")
        first = result["placements"][0]
        # элемент у верхней кромки (y_mm = 0, высота 90) при плате 800 мм
        # получает Y = 800 - 0 - 90 = 710: он в самом верху чертежа
        self.assertAlmostEqual(first["insert_y"], 710.0, places=6)
        self.assertAlmostEqual(first["insert_x"], 0.0, places=6)

    def test_two_elements_in_a_row_keep_their_order(self):
        result = autocad_layout.place({
            "plate": plate(), "units": mm_units(), "elements": elements()})
        xs = [p["insert_x"] for p in result["placements"]]
        self.assertEqual(xs, [0.0, 18.0])

    def test_inch_document_scales_the_coordinates(self):
        units, _ = autocad_units.describe_units(1)
        result = autocad_layout.place({
            "plate": plate(), "units": units, "elements": elements()})
        first = result["placements"][0]
        self.assertAlmostEqual(first["insert_y"], 710.0 / 25.4, places=6)
        self.assertAlmostEqual(first["width_units"], 18.0 / 25.4, places=6)

    def test_no_plate_height_is_a_refusal(self):
        result = autocad_layout.place({
            "plate": {"width_mm": 600.0, "height_mm": None},
            "units": mm_units(), "elements": elements()})
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "no_plate_height")

    def test_no_units_is_a_refusal(self):
        result = autocad_layout.place({"plate": plate(), "elements": elements()})
        self.assertEqual(result["refusals"][0]["code"], "no_units")

    def test_element_without_geometry_is_refused_not_guessed(self):
        result = autocad_layout.place({
            "plate": plate(), "units": mm_units(),
            "elements": [{"name": "QF9", "x_mm": 0.0, "y_mm": 0.0}]})
        self.assertEqual(result["refusals"][0]["code"], "no_geometry")

    def test_element_outside_plate_is_reported(self):
        result = autocad_layout.place({
            "plate": plate(), "units": mm_units(),
            "elements": [{"name": "Wide", "x_mm": 590.0, "y_mm": 0.0,
                          "width_mm": 100.0, "height_mm": 90.0}]})
        self.assertEqual(result["verdict"], "computed_with_gaps")
        self.assertTrue(result["outside"])

    def test_origin_is_respected(self):
        result = autocad_layout.place({
            "plate": plate(), "units": mm_units(), "elements": elements(),
            "origin": {"x": 100.0, "y": 50.0}})
        self.assertAlmostEqual(result["placements"][0]["insert_x"], 100.0, places=6)
        self.assertAlmostEqual(result["placements"][0]["insert_y"], 760.0, places=6)

    def test_insert_point_convention_is_declared(self):
        result = autocad_layout.place({
            "plate": plate(), "units": mm_units(), "elements": elements()})
        self.assertEqual(result["insert_point"], "left_bottom")
        self.assertIn("convention", result["insert_point_note"])

    def test_empty_elements_is_a_refusal(self):
        result = autocad_layout.place({"plate": plate(), "units": mm_units()})
        self.assertEqual(result["refusals"][0]["code"], "no_elements")


class FakeDoc:
    def __init__(self, insunits=4, name="panel.dwg", path=r"C:\work\panel.dwg"):
        self.Name = name
        self.FullName = path
        self._insunits = insunits

    def GetVariable(self, name):
        if name == "INSUNITS":
            return self._insunits
        raise KeyError(name)


class FakeApp:
    def __init__(self, version="23.0s (LMS Tech)", doc=None):
        self.Version = version
        self._doc = doc

    @property
    def ActiveDocument(self):
        if self._doc is None:
            raise AttributeError("no document")
        return self._doc


class SessionTests(unittest.TestCase):
    """COM-слой: подключение к уже открытому экземпляру."""

    def test_status_reads_document_and_units(self):
        app = FakeApp(doc=FakeDoc(insunits=4))
        status = autocad_session.status(session_app=app, release="23")
        self.assertTrue(status["attached"])
        self.assertEqual(status["document"]["name"], "panel.dwg")
        self.assertEqual(status["units"]["insunits"], 4)
        self.assertEqual(status["version"]["detected_release"], "23")

    def test_unitless_document_is_reported_as_refusal(self):
        app = FakeApp(doc=FakeDoc(insunits=0))
        status = autocad_session.status(session_app=app, release="23")
        codes = [r["code"] for r in status["refusals"]]
        self.assertIn("unitless_document", codes)

    def test_release_mismatch_is_reported(self):
        app = FakeApp(version="24.0s (LMS Tech)", doc=FakeDoc())
        status = autocad_session.status(session_app=app, release="23")
        codes = [r["code"] for r in status["refusals"]]
        self.assertIn("release_mismatch", codes)

    def test_unconfirmed_release_is_reported(self):
        app = FakeApp(doc=FakeDoc())
        status = autocad_session.status(session_app=app, release="23")
        codes = [r["code"] for r in status["refusals"]]
        self.assertIn("version_not_confirmed", codes)

    def test_electrical_profile_is_never_verified(self):
        app = FakeApp(doc=FakeDoc())
        status = autocad_session.status(session_app=app, release="23",
                                        profile="electrical")
        self.assertTrue(status["profile"]["is_electrical"])
        self.assertFalse(status["profile"]["verified"])

    def test_missing_instance_is_a_refusal(self):
        def boom(progid, attempts=1):
            raise autocad_session.AttachError("нет запущенного AutoCAD")
        original = autocad_session._get_active_object
        autocad_session._get_active_object = boom
        try:
            status = autocad_session.status(release="23")
        finally:
            autocad_session._get_active_object = original
        self.assertFalse(status["attached"])
        self.assertEqual(status["refusals"][0]["code"],
                         "autocad_active_instance_not_found")

    def test_document_info_survives_no_document(self):
        info = autocad_session.document_info(FakeApp(doc=None))
        self.assertFalse(info["has_document"])
        self.assertEqual(info["reason"], "no_active_document")


class ToolTests(unittest.TestCase):
    """Действия адаптера и поверхность."""

    def test_layout_place_action(self):
        answer = autocad_tools.autocad_layout_place(None, {
            "plate": plate(), "units": mm_units(), "elements": elements()})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["action"], "autocad.layout_place")
        self.assertEqual(answer["result"]["placement_count"], 2)

    def test_units_read_accepts_explicit_code(self):
        answer = autocad_tools.autocad_units_read(None, {"insunits": 1})
        self.assertEqual(answer["result"]["units"]["mm_per_unit"], 25.4)

    def test_units_read_refuses_unitless(self):
        answer = autocad_tools.autocad_units_read(None, {"insunits": 0})
        self.assertEqual(answer["result"]["refusal"], "unitless_document")

    def test_layout_place_resolves_insunits_itself(self):
        answer = autocad_tools.autocad_layout_place(None, {
            "plate": plate(), "units": {"insunits": 4}, "elements": elements()})
        self.assertEqual(answer["result"]["verdict"], "computed")

    def test_version_check_action(self):
        answer = autocad_tools.autocad_version_check(None, {"release": "23"})
        self.assertEqual(answer["result"]["version"]["progid"],
                         "AutoCAD.Application.23")

    def test_surface_is_consistent(self):
        names = tools_catalog.tool_names()
        self.assertEqual(len(names), 5)
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(set(tools_catalog.routing_table()), set(names))

    def test_version_json_matches_catalog(self):
        import json
        with open(os.path.join(REPO_ROOT, "VERSION.json"), encoding="utf-8") as fh:
            declared = json.loads(fh.read())
        snapshot = tools_catalog.catalog_snapshot()
        self.assertEqual(declared["mcp_tool_count"], snapshot["mcp_tool_count"])
        self.assertEqual(declared["mcp_tools"], snapshot["mcp_tools"])

    def test_target_progid_matches_the_table(self):
        import json
        with open(os.path.join(REPO_ROOT, "VERSION.json"), encoding="utf-8") as fh:
            declared = json.loads(fh.read())
        self.assertEqual(declared["autocad"]["target_progid"],
                         autocad_version.RELEASE_TO_PROGID["23"])

    def test_com_modules_are_imported_inside_functions(self):
        """Импорт win32com только в слое исполнения: чистые модули тестируемы."""
        for name in ("autocad_version", "autocad_units", "autocad_layout"):
            with open(os.path.join(REPO_ROOT, "src", name + ".py"),
                      encoding="utf-8") as fh:
                source = fh.read()
            self.assertNotIn("win32com", source, name)


if __name__ == "__main__":
    unittest.main()
