"""Волна 14: спецификация и маркировка щита из раскладки.

Проверяется не только то, что перечень строится, но и то, что он **не
выдумывает**: позиция без наименования в библиотеке не получает «типового»
имени, а изделие без буквенного кода не получает чужого кода.

Тесты на реальных данных сверяют маркировку с ГОСТ 2.710-81: код берётся из
таблицы, добытой из открытой публикации стандарта, а не из памяти.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_spec  # noqa: E402
import cabinet_build  # noqa: E402
import normative_tables  # noqa: E402

LAYOUT = {
    "plate": {"width_mm": 600, "height_mm": 800},
    "library": [
        {"lib_key": "brk", "name": "Автомат 1P C16", "width_mm": 18, "height_mm": 80},
        {"lib_key": "cont", "name": "Контактор 25А", "width_mm": 45, "height_mm": 80},
        {"lib_key": "term", "name": "Клемма", "width_mm": 6, "height_mm": 45},
        {"lib_key": "rcd", "name": "УЗО 2P 40А", "width_mm": 42, "height_mm": 80},
    ],
    "elements": [
        {"id": "e1", "lib_key": "brk", "x_mm": 10, "y_mm": 100},
        {"id": "e2", "lib_key": "brk", "x_mm": 40, "y_mm": 100},
        {"id": "e3", "lib_key": "cont", "x_mm": 100, "y_mm": 100},
        {"id": "e4", "lib_key": "rcd", "x_mm": 200, "y_mm": 100},
    ],
    "groups": [
        {"id": "g1", "lib_key": "term", "x_mm": 10, "y_mm": 300, "count": 5,
         "internal_gap_mm": 0},
    ],
}

KINDS = {"brk": "breaker", "cont": "contactor", "term": "terminal", "rcd": "rcd"}


def layout(kind, **extra):
    payload = dict(LAYOUT)
    payload["kind"] = kind
    payload.update(extra)
    return payload


class BomTests(unittest.TestCase):

    def test_group_is_expanded_into_positions(self):
        result = cabinet_spec.build(layout("bom"))
        self.assertEqual(result["verdict"], "computed")
        self.assertEqual(result["result"]["total_count"], 9)

    def test_items_are_grouped_by_lib_key(self):
        result = cabinet_spec.build(layout("bom"))["result"]
        counts = {item["lib_key"]: item["count"] for item in result["items"]}
        self.assertEqual(counts, {"brk": 2, "cont": 1, "rcd": 1, "term": 5})

    def test_group_coordinates_step_by_width(self):
        result = cabinet_spec.build(layout("bom"))["result"]
        term = next(i for i in result["items"] if i["lib_key"] == "term")
        self.assertEqual([p["x_mm"] for p in term["positions"]],
                         [10.0, 16.0, 22.0, 28.0, 34.0])

    def test_empty_layout_is_refused(self):
        result = cabinet_spec.build({"kind": "bom", "elements": []})
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "empty_layout")

    def test_position_outside_library_goes_to_unverified(self):
        payload = layout("bom")
        payload["elements"] = payload["elements"] + [
            {"id": "x", "lib_key": "unknown", "x_mm": 0, "y_mm": 0}]
        result = cabinet_spec.build(payload)["result"]
        self.assertEqual(len(result["unverified"]), 1)
        self.assertIn("library", result["unverified"][0]["unverified_reason"])
        item = next(i for i in result["items"] if i["lib_key"] == "unknown")
        self.assertIsNone(item["name"])

    def test_unknown_kind_is_refused(self):
        result = cabinet_spec.build({"kind": "inventar"})
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "unknown_kind")

    def test_default_kind_is_bom(self):
        payload = dict(LAYOUT)
        self.assertEqual(cabinet_spec.build(payload)["kind"], "bom")


class MarkingTests(unittest.TestCase):

    def test_codes_come_from_registry_by_kind(self):
        result = cabinet_spec.build(layout("marking", kinds=KINDS))
        self.assertEqual(result["verdict"], "computed")
        assigned = {p["id"]: p["designation"] for p in result["result"]["positions"]}
        self.assertEqual(assigned["e1"], "QF1")
        self.assertEqual(assigned["e2"], "QF2")
        self.assertEqual(assigned["e3"], "KM1")
        self.assertEqual(assigned["g1[1]"], "XT1")
        self.assertEqual(assigned["g1[5]"], "XT5")

    def test_numbering_follows_plate_order_not_names(self):
        result = cabinet_spec.build(layout("marking", kinds=KINDS))
        positions = result["result"]["positions"]
        # контактор стоит левее второго автомата, но нумерация внутри кода своя
        order = [p["designation"] for p in positions]
        self.assertEqual(order, sorted(order, key=lambda s: (len(s), s)))
        qf = [p for p in positions if p["designation"].startswith("QF")]
        self.assertEqual([p["x_mm"] for p in qf], [10.0, 40.0])

    def test_explicit_code_wins_over_kind(self):
        result = cabinet_spec.build(layout("marking", kinds=KINDS,
                                           codes={"rcd": "QF"}))
        assigned = {p["id"]: p["designation"] for p in result["result"]["positions"]}
        self.assertEqual(assigned["e4"], "QF3")
        self.assertNotIn("e4", [p["id"] for p in result["result"]["unresolved"]])

    def test_rcd_has_no_code_and_is_unresolved(self):
        result = cabinet_spec.build(layout("marking", kinds=KINDS))
        unresolved = {p["lib_key"] for p in result["result"]["unresolved"]}
        self.assertIn("rcd", unresolved)
        refusals = {r["kind"] for r in result["result"]["code_refusals"]}
        self.assertIn("rcd", refusals)

    def test_without_codes_nothing_is_assigned(self):
        result = cabinet_spec.build(layout("marking"))
        self.assertEqual(result["result"]["assigned_count"], 0)
        self.assertTrue(result["result"]["unresolved"])

    def test_kind_missing_from_table_is_refused(self):
        result = cabinet_spec.build(layout("marking", kinds={"brk": "widget"}))
        reasons = {r["reason"] for r in result["result"]["code_refusals"]}
        self.assertIn("kind_not_in_table", reasons)

    def test_provenance_names_the_standard(self):
        result = cabinet_spec.build(layout("marking", kinds=KINDS))
        text = str(result["provenance"]).lower()
        self.assertIn("2.710", text)

    def test_registry_note_is_compiled_not_quoted(self):
        rows, source, _, _ = normative_tables.load_table("designation_map")
        self.assertTrue(any(r.get("kind") == "rcd" and r.get("code") is None
                            for r in rows))
        self.assertIn("составлено", str(source).lower())


class LabelsTests(unittest.TestCase):

    def test_label_carries_coordinates(self):
        result = cabinet_spec.build(layout("labels", kinds=KINDS))
        labels = {x["designation"]: x for x in result["result"]["labels"]}
        self.assertEqual(labels["QF1"]["x_mm"], 10.0)
        self.assertEqual(labels["QF1"]["y_mm"], 100.0)
        self.assertEqual(labels["QF1"]["width_mm"], 18.0)

    def test_with_name_appends_item_name(self):
        result = cabinet_spec.build(layout("labels", kinds=KINDS, with_name=True))
        first = result["result"]["labels"][0]
        self.assertEqual(first["text"], "%s Автомат 1P C16" % first["designation"])

    def test_without_name_text_is_designation(self):
        result = cabinet_spec.build(layout("labels", kinds=KINDS))
        first = result["result"]["labels"][0]
        self.assertEqual(first["text"], first["designation"])

    def test_unresolved_positions_get_no_label(self):
        result = cabinet_spec.build(layout("labels", kinds=KINDS))
        designations = {x["designation"] for x in result["result"]["labels"]}
        self.assertFalse(any(d is None for d in designations))
        self.assertEqual(result["result"]["label_count"], 8)

    def test_no_designations_is_refused(self):
        result = cabinet_spec.build(layout("labels"))
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "no_designations")


class RealDataTests(unittest.TestCase):
    """Маркировка по таблице, добытой из публикации ГОСТ 2.710-81."""

    def test_designations_table_is_real(self):
        rows, source, _, _ = normative_tables.load_table("designations")
        self.assertGreater(len(rows), 50)
        self.assertIn("2.710", source["document"])

    def test_breaker_code_is_qf(self):
        rows, _, _, _ = normative_tables.load_table("designations")
        codes = {r["code"]: r["element"].lower() for r in rows}
        self.assertIn("QF", codes)
        self.assertTrue("выключатель" in codes["QF"] or "автомат" in codes["QF"])

    def test_terminal_code_is_xt(self):
        rows, _, _, _ = normative_tables.load_table("designation_map")
        entry = next(r for r in rows if r["kind"] == "terminal")
        self.assertEqual(entry["code"], "XT")

    def test_layout_from_builder_feeds_marking(self):
        """Ответ раскладчика волны 8 передаётся дальше целиком."""
        built = cabinet_build.build_layout({
            "plate": {"width_mm": 800.0, "height_mm": 600.0},
            "library": LAYOUT["library"],
            "lines": [{"id": "l1", "lib_key": "brk", "qty": 2},
                      {"id": "l2", "lib_key": "cont", "qty": 1},
                      {"id": "l3", "lib_key": "term", "qty": 3}],
        })
        payload = dict(built)
        payload["kind"] = "marking"
        payload["kinds"] = KINDS
        result = cabinet_spec.build(payload)
        self.assertEqual(result["verdict"], "computed")
        self.assertEqual(result["result"]["assigned_count"], 6)
        codes = {p["designation"][:2] for p in result["result"]["positions"]}
        self.assertEqual(codes, {"QF", "KM", "XT"})

    def test_layout_wrapper_is_unwrapped(self):
        """Поле layout разворачивается, если elements не переданы явно."""
        built = cabinet_build.build_layout({
            "plate": {"width_mm": 800.0, "height_mm": 600.0},
            "library": LAYOUT["library"],
            "lines": [{"id": "l1", "lib_key": "brk", "qty": 1}],
        })
        self.assertTrue(cabinet_spec.unwrap(dict(built)).get("groups")
                        or cabinet_spec.unwrap(dict(built)).get("elements"))

    def test_explicit_elements_win_over_wrapper(self):
        payload = {"layout": {"library": [], "elements": [
                       {"id": "inner", "lib_key": "z", "x_mm": 0, "y_mm": 0}]},
                   "elements": [{"id": "outer", "lib_key": "y", "x_mm": 0, "y_mm": 0}]}
        unwrapped = cabinet_spec.unwrap(payload)
        self.assertEqual(unwrapped["elements"][0]["id"], "outer")


class SurfaceTests(unittest.TestCase):
    """Инструмент зарегистрирован: поверхность выросла осознанно."""

    def test_tool_is_registered(self):
        import tools_catalog
        names = [t["name"] for t in tools_catalog.TOOLS]
        self.assertIn("kompas_cabinet_spec", names)
        self.assertEqual(len(names), 59,
                         "поверхность изменилась - обновите ожидание осознанно")

    def test_tool_has_route(self):
        import tools_catalog
        self.assertIn("kompas_cabinet_spec", tools_catalog.ACTION_MAP)

    def test_schema_lists_three_kinds(self):
        import tools_catalog
        tool = next(t for t in tools_catalog.TOOLS
                    if t["name"] == "kompas_cabinet_spec")
        self.assertEqual(
            tool["inputSchema"]["properties"]["kind"]["enum"],
            ["bom", "marking", "labels"])


if __name__ == "__main__":
    unittest.main()
