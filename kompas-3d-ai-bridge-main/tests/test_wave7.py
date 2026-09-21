"""Волна 7: доменный слой щита — состав, секционирование, раскладка.

Всё здесь работает без КОМПАСа: модули чистые, COM не импортируется даже
на уровне модуля (иначе их нельзя было бы проверить в статических тестах).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_domain as cd          # noqa: E402
import cabinet_layout as cl          # noqa: E402
import tools_catalog                 # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# --------------------------------------------------------------------------
# фикстуры
# --------------------------------------------------------------------------

def eq(ident, name, category, rated=None, sku=None, **extra):
    row = {"id": ident, "sku": sku or ident, "name": name,
           "category": category, "ratedCurrent": rated}
    row.update(extra)
    return row


# Шкаф ПЛК, собранный правильно: вводной самый мощный, отходящий слабее шины,
# у контроллера есть блок питания, форма 2b с шинным отсеком.
GOOD_CABINET = {
    "id": "cab-1",
    "kind": "Шкаф ПЛК",
    "name": "Шкаф управления",
    "form": "2b",
    "segments": [
        {"id": "s1", "kind": "input", "name": "Вводной отсек", "partitions": 2},
        {"id": "s2", "kind": "control", "name": "Управление", "partitions": 2},
        {"id": "s3", "kind": "busbar", "name": "Шинный отсек", "partitions": 1},
    ],
    "items": [
        {"lineId": "i1", "eqId": "eq-in", "qty": 1},
        {"lineId": "i2", "eqId": "eq-brk", "qty": 3},
        {"lineId": "i3", "eqId": "eq-bus", "qty": 1},
        {"lineId": "i4", "eqId": "eq-plc", "qty": 1},
        {"lineId": "i5", "eqId": "eq-psu", "qty": 1},
    ],
}

GOOD_CATALOG = [
    eq("eq-in", "Рубильник вводной", cd.CAT_SWITCH, 100),
    eq("eq-brk", "Автомат 40 А", cd.CAT_BREAKER, 40),
    eq("eq-bus", "Шина ШМТ 25x3", cd.CAT_BUS, 160, sku="ШМТ-25x3"),
    eq("eq-plc", "Контроллер ПЛК110-24", cd.CAT_PLC),
    eq("eq-psu", "БП DRP-120-24", cd.CAT_PSU),
]


def spec_of(cabinets, catalog=None):
    return {"cabinets": cabinets, "catalog": catalog if catalog is not None else GOOD_CATALOG}


def codes(report_or_issues):
    items = report_or_issues if isinstance(report_or_issues, list) \
        else report_or_issues["issues"]
    return [i["code"] for i in items]


# --------------------------------------------------------------------------
# базовые свойства модулей
# --------------------------------------------------------------------------

class TestPureModules(unittest.TestCase):
    """Модули должны оставаться COM-free — иначе их не проверить статически."""

    def test_no_com_import(self):
        for name in ("cabinet_domain.py", "cabinet_layout.py", "cabinet_tools.py"):
            path = os.path.join(ROOT, "src", name)
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            self.assertNotIn("win32com", body, name)
            self.assertNotIn("import pythoncom", body, name)

    def test_modules_import_without_com(self):
        self.assertNotIn("win32com", sys.modules)
        self.assertNotIn("pythoncom", sys.modules)


# --------------------------------------------------------------------------
# домен: правила срабатывают там, где должны
# --------------------------------------------------------------------------

class TestDomainRules(unittest.TestCase):

    def test_good_cabinet_is_clean(self):
        report = cd.check_spec(spec_of([GOOD_CABINET]))
        self.assertEqual(report["summary"]["error"], 0)
        self.assertEqual(report["verdict"], "ready", report["issues"])

    def test_breaker_over_bus(self):
        """Отходящий аппарат мощнее шины. Вводной — самый мощный, он исключён."""
        cabinets = [{
            "id": "c1", "items": [
                {"lineId": "a", "eqId": "eq-in", "qty": 1},
                {"lineId": "b", "eqId": "eq-brk80", "qty": 1},
                {"lineId": "c", "eqId": "eq-bus63", "qty": 1},
            ]}]
        catalog = [
            eq("eq-in", "Рубильник", cd.CAT_SWITCH, 100),
            eq("eq-brk80", "Автомат 80 А", cd.CAT_BREAKER, 80),
            eq("eq-bus63", "Шина ШМТ", cd.CAT_BUS, 63, sku="ШМТ-63"),
        ]
        report = cd.check_spec(spec_of(cabinets, catalog))
        self.assertIn("BREAKER_OVER_BUS", codes(report))
        self.assertEqual(report["summary"]["error"], 1)

    def test_busbar_overload(self):
        """Сумма отходящих выше допустимого тока шины."""
        cabinets = [{
            "id": "c1", "items": [
                {"lineId": "a", "eqId": "eq-in", "qty": 1},
                {"lineId": "b", "eqId": "eq-b40", "qty": 2},
                {"lineId": "c", "eqId": "eq-bus63", "qty": 1},
            ]}]
        catalog = [
            eq("eq-in", "Рубильник", cd.CAT_SWITCH, 100),
            eq("eq-b40", "Автомат 40 А", cd.CAT_BREAKER, 40),
            eq("eq-bus63", "Шина ШМТ", cd.CAT_BUS, 63, sku="ШМТ-63"),
        ]
        report = cd.check_spec(spec_of(cabinets, catalog))
        # 40 + 40 = 80 > 63
        self.assertIn("BUSBAR_OVERLOAD", codes(report))

    def test_uzip_without_incoming(self):
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-uzip", "qty": 1}]}]
        catalog = [eq("eq-uzip", "УЗИП", cd.CAT_UZIP)]
        self.assertIn("UZIP_NO_INCOMING", codes(cd.check_spec(spec_of(cabinets, catalog))))

    def test_plc_without_psu(self):
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-plc", "qty": 1}]}]
        catalog = [eq("eq-plc", "Контроллер ПЛК110", cd.CAT_PLC)]
        self.assertIn("PLC_NO_PSU", codes(cd.check_spec(spec_of(cabinets, catalog))))

    def test_hmi_without_plc_in_whole_project(self):
        """Межшкафное правило: ПЛК ищется во всех шкафах, а не только в этом."""
        cabinets = [
            {"id": "c1", "items": [{"lineId": "a", "eqId": "eq-hmi", "qty": 1}]},
            {"id": "c2", "items": [{"lineId": "b", "eqId": "eq-plc", "qty": 1}]},
        ]
        catalog = [eq("eq-hmi", "Панель оператора", cd.CAT_HMI),
                   eq("eq-plc", "Контроллер ПЛК110", cd.CAT_PLC)]
        self.assertNotIn("HMI_NO_PLC", codes(cd.check_spec(spec_of(cabinets, catalog))))

        alone = [cabinets[0]]
        self.assertIn("HMI_NO_PLC", codes(cd.check_spec(spec_of(alone, catalog))))

    def test_heat_without_thermostat(self):
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-heat", "qty": 1}]}]
        catalog = [eq("eq-heat", "Греющий кабель", cd.CAT_HEAT)]
        self.assertIn("HEAT_NO_THERMOSTAT", codes(cd.check_spec(spec_of(cabinets, catalog))))

    def test_empty_cabinet(self):
        self.assertIn("EMPTY_CABINET", codes(cd.check_spec(spec_of([{"id": "c1", "items": []}]))))

    def test_form_requires_busbar_segment(self):
        cabinets = [{"id": "c1", "form": "2b",
                     "segments": [{"id": "s", "kind": "input", "name": "Ввод"}],
                     "items": []}]
        self.assertIn("FORM_NO_BUSBAR_SEGMENT", codes(cd.check_spec(spec_of(cabinets))))

    def test_form3_requires_two_functional_segments(self):
        cabinets = [{"id": "c1", "form": "3a",
                     "segments": [{"id": "s1", "kind": "input", "name": "Ввод"},
                                  {"id": "s3", "kind": "busbar", "name": "Шины"}],
                     "items": []}]
        self.assertIn("FORM_FEW_SEGMENTS", codes(cd.check_spec(spec_of(cabinets))))

    def test_many_breakers_without_bus(self):
        cabinets = [{"id": "c1", "items": [
            {"lineId": "a", "eqId": "eq-brk", "qty": 1},
            {"lineId": "b", "eqId": "eq-brk", "qty": 1},
            {"lineId": "c", "eqId": "eq-brk", "qty": 1},
        ]}]
        self.assertIn("MANY_BREAKERS_NO_BUS", codes(cd.check_spec(spec_of(cabinets))))


# --------------------------------------------------------------------------
# домен: честность — отсутствующие данные не считаются пройденной проверкой
# --------------------------------------------------------------------------

class TestDomainHonesty(unittest.TestCase):

    def test_unresolved_catalog_reference_is_an_error(self):
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-missing", "qty": 1}]}]
        report = cd.check_spec(spec_of(cabinets))
        self.assertIn("UNRESOLVED_CATALOG_REF", codes(report))
        self.assertEqual(report["summary"]["error"], 1)
        self.assertFalse(report["ok"])

    def test_missing_rated_current_goes_to_unconfirmed_not_ok(self):
        """Силовой аппарат без номинала: правила 1 и 9 физически не могут
        сработать. Это обязано попасть в unconfirmed, а не в «проверено»."""
        cabinets = [{"id": "c1", "items": [
            {"lineId": "a", "eqId": "eq-norating", "qty": 1},
            {"lineId": "b", "eqId": "eq-brk", "qty": 1},
            {"lineId": "c", "eqId": "eq-bus", "qty": 1},
        ]}]
        catalog = [
            eq("eq-norating", "Автомат без номинала", cd.CAT_BREAKER, None),
            eq("eq-brk", "Автомат 40 А", cd.CAT_BREAKER, 40),
            eq("eq-bus", "Шина ШМТ", cd.CAT_BUS, 100, sku="ШМТ-1"),
        ]
        report = cd.check_spec(spec_of(cabinets, catalog))
        self.assertTrue(any(u["reason"] == "rated_current_missing" for u in report["unconfirmed"]))
        self.assertEqual(report["verdict"], "ready_with_unknowns")

    def test_confirm_flag_goes_to_unconfirmed(self):
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-guess", "qty": 1}]}]
        catalog = [eq("eq-guess", "Модуль", cd.CAT_PLC, confirm=True)]
        report = cd.check_spec(spec_of(cabinets, catalog))
        self.assertTrue(any(u["reason"] == "confirm" for u in report["unconfirmed"]))

    def test_unverifiable_rules_are_listed(self):
        """Один силовой аппарат и нет шины — правила 1 и 9 не выполнены,
        и это надо показать, а не промолчать."""
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-brk", "qty": 1}]}]
        report = cd.check_spec(spec_of(cabinets))
        names = set()
        for item in report["unverified"]:
            names.update(item["rules"])
        self.assertIn("BREAKER_OVER_BUS", names)
        self.assertIn("BUSBAR_OVERLOAD", names)

    def test_bad_form_is_a_parse_error(self):
        cabinets = [{"id": "c1", "form": "9z", "items": []}]
        report = cd.check_spec(spec_of(cabinets))
        self.assertTrue(any(p["code"] == "BAD_FORM" for p in report["parse_issues"]))

    def test_verdict_blocked_on_error(self):
        cabinets = [{"id": "c1", "items": [{"lineId": "a", "eqId": "eq-missing", "qty": 1}]}]
        self.assertEqual(cd.check_spec(spec_of(cabinets))["verdict"], "blocked")


# --------------------------------------------------------------------------
# раскладка: геометрия
# --------------------------------------------------------------------------

class TestLayoutGeometry(unittest.TestCase):

    def test_rotated_size_swaps_dimensions(self):
        w, h = cl.rotated_size(18.0, 90.0, 90.0)
        self.assertAlmostEqual(w, 90.0, places=6)
        self.assertAlmostEqual(h, 18.0, places=6)

    def test_rotated_size_is_symmetric(self):
        w0, h0 = cl.rotated_size(20.0, 60.0, 0.0)
        self.assertAlmostEqual(w0, 20.0, places=6)
        self.assertAlmostEqual(h0, 60.0, places=6)

    def test_to_cad_flips_y_only(self):
        box = {"x": 10.0, "y": 0.0, "w": 20.0, "h": 100.0}
        cad = cl.to_cad(box, plate_height_mm=600.0)
        self.assertAlmostEqual(cad["x"], 10.0, places=9)
        self.assertAlmostEqual(cad["y"], 500.0, places=9)   # 600 - (0 + 100)
        self.assertAlmostEqual(cad["h"], 100.0, places=9)

    def test_box_within_plate(self):
        plate = {"width_mm": 800.0, "height_mm": 600.0, "origin": "top_left"}
        self.assertTrue(cl.box_within_plate({"x": 0, "y": 0, "w": 10, "h": 10}, plate))
        self.assertFalse(cl.box_within_plate({"x": -1, "y": 0, "w": 10, "h": 10}, plate))
        self.assertFalse(cl.box_within_plate({"x": 795, "y": 0, "w": 10, "h": 10}, plate))

    def test_box_distance_is_zero_when_touching(self):
        a = {"x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0}
        b = {"x": 10.0, "y": 0.0, "w": 10.0, "h": 10.0}
        self.assertAlmostEqual(cl.box_distance(a, b), 0.0, places=9)
        self.assertFalse(cl.boxes_overlap(a, b))

    def test_group_width_counts_caps(self):
        library = {
            "k": {"lib_key": "k", "name": "k", "width_mm": 10.0, "height_mm": 40.0},
            "cap": {"lib_key": "cap", "name": "cap", "width_mm": 2.0, "height_mm": 40.0},
        }
        grp = {"lib_key": "k", "count": 3, "internal_gap_mm": 1.0, "cap_start_key": "cap"}
        # 3*10 + 2*1 + (2 + 1) = 35
        self.assertAlmostEqual(cl.group_width(grp, library), 35.0, places=9)


# --------------------------------------------------------------------------
# раскладка: проверки
# --------------------------------------------------------------------------

BASE_PLATE = {"width_mm": 800.0, "height_mm": 600.0, "origin": "top_left"}
BASE_LIB = [{"lib_key": "brk", "name": "Автомат", "width_mm": 18.0, "height_mm": 90.0}]


def layout_of(elements=None, ducts=None, groups=None, plate=None, library=None):
    return {
        "plate": plate or dict(BASE_PLATE),
        "defaults": {"gap_between_equipment_mm": 5.0, "clearance_equipment_to_duct_mm": 20.0},
        "ducts": ducts or [],
        "elements": elements or [],
        "groups": groups or [],
        "library": library or [dict(x) for x in BASE_LIB],
    }


class TestLayoutValidation(unittest.TestCase):

    def test_clean_layout_is_ready(self):
        report = cl.check_layout(layout_of(elements=[
            {"id": "e1", "lib_key": "brk", "x_mm": 100.0, "y_mm": 100.0}]))
        self.assertEqual(report["summary"]["error"], 0)
        self.assertEqual(report["verdict"], "ready", report["issues"])

    def test_bad_origin(self):
        report = cl.check_layout(layout_of(plate={"width_mm": 800, "height_mm": 600,
                                                  "origin": "center"}))
        self.assertIn("BAD_ORIGIN", codes(report))

    def test_bad_plate_size(self):
        report = cl.check_layout(layout_of(plate={"width_mm": 0, "height_mm": 600,
                                                  "origin": "top_left"}))
        self.assertIn("BAD_PLATE_SIZE", codes(report))

    def test_duplicate_id(self):
        report = cl.check_layout(layout_of(elements=[
            {"id": "e1", "lib_key": "brk", "x_mm": 0, "y_mm": 0},
            {"id": "e1", "lib_key": "brk", "x_mm": 100, "y_mm": 0}]))
        self.assertIn("DUPLICATE_ID", codes(report))

    def test_unresolved_lib_key(self):
        report = cl.check_layout(layout_of(elements=[
            {"id": "e1", "lib_key": "нет-такого", "x_mm": 0, "y_mm": 0}]))
        self.assertIn("UNRESOLVED_LIB_KEY", codes(report))
        self.assertFalse(report["ok"])

    def test_overlap_is_a_warning(self):
        report = cl.check_layout(layout_of(elements=[
            {"id": "e1", "lib_key": "brk", "x_mm": 100.0, "y_mm": 100.0},
            {"id": "e2", "lib_key": "brk", "x_mm": 105.0, "y_mm": 100.0}]))
        overlap = [i for i in report["issues"] if i["code"] == "OVERLAP"]
        self.assertTrue(overlap)
        self.assertEqual(overlap[0]["level"], "warning")

    def test_off_plate_is_a_warning(self):
        report = cl.check_layout(layout_of(elements=[
            {"id": "e1", "lib_key": "brk", "x_mm": 790.0, "y_mm": 0.0}]))
        off = [i for i in report["issues"] if i["code"] == "OFF_PLATE"]
        self.assertTrue(off)
        self.assertEqual(off[0]["level"], "warning")

    def test_clearance_tight(self):
        # канал вдоль левой кромки шириной 40, элемент вплотную к нему
        report = cl.check_layout(layout_of(
            elements=[{"id": "e1", "lib_key": "brk", "x_mm": 45.0, "y_mm": 100.0}],
            ducts=[{"id": "d1", "x_mm": 0.0, "y_mm": 0.0, "length_mm": 600.0,
                    "width_mm": 40.0, "rot_deg": 90.0}]))
        self.assertIn("CLEARANCE_TIGHT", codes(report))

    def test_clearance_ok_when_far(self):
        report = cl.check_layout(layout_of(
            elements=[{"id": "e1", "lib_key": "brk", "x_mm": 300.0, "y_mm": 100.0}],
            ducts=[{"id": "d1", "x_mm": 0.0, "y_mm": 0.0, "length_mm": 600.0,
                    "width_mm": 40.0, "rot_deg": 90.0}]))
        self.assertNotIn("CLEARANCE_TIGHT", codes(report))

    def test_row_overflow(self):
        """Два боковых канала близко друг к другу — ряд в них не влезает."""
        library = [{"lib_key": "wide", "name": "Широкий", "width_mm": 100.0, "height_mm": 90.0}]
        report = cl.check_layout(layout_of(
            elements=[{"id": "e1", "lib_key": "wide", "x_mm": 10.0, "y_mm": 100.0},
                      {"id": "e2", "lib_key": "wide", "x_mm": 130.0, "y_mm": 100.0}],
            ducts=[{"id": "dl", "x_mm": 0.0, "y_mm": 0.0, "length_mm": 600.0,
                    "width_mm": 10.0, "rot_deg": 90.0},
                   {"id": "dr", "x_mm": 200.0, "y_mm": 0.0, "length_mm": 600.0,
                    "width_mm": 10.0, "rot_deg": 90.0}],
            library=library))
        overflow = [i for i in report["issues"] if i["code"] == "ROW_OVERFLOW"]
        self.assertTrue(overflow, report["issues"])
        # 100 + 5 + 100 = 205, доступно 200 - 10 = 190 -> перебор 15 мм
        self.assertIn("15", overflow[0]["message"])

    def test_unconfirmed_size_is_reported_not_substituted(self):
        library = [{"lib_key": "guess", "name": "?", "width_mm": 90.0,
                    "height_mm": 90.0, "confirm": True}]
        report = cl.check_layout(layout_of(
            elements=[{"id": "e1", "lib_key": "guess", "x_mm": 0, "y_mm": 0}],
            library=library))
        self.assertTrue(report["unconfirmed"])
        self.assertEqual(report["verdict"], "ready_with_unknowns")

    def test_set_explodes_into_count(self):
        report = cl.check_layout(layout_of(groups=[
            {"id": "g1", "lib_key": "brk", "count": 12, "internal_gap_mm": 0.0,
             "x_mm": 10.0, "y_mm": 300.0, "rot_deg": 0.0}]))
        self.assertEqual(report["summary"]["error"], 0)

    def test_bad_count(self):
        report = cl.check_layout(layout_of(groups=[
            {"id": "g1", "lib_key": "brk", "count": 0, "x_mm": 10.0, "y_mm": 300.0}]))
        self.assertIn("BAD_COUNT", codes(report))


# --------------------------------------------------------------------------
# каталог инструментов
# --------------------------------------------------------------------------

class TestCatalogSurface(unittest.TestCase):

    def test_tools_present(self):
        for name in ("kompas_cabinet_spec_check", "kompas_cabinet_layout_check"):
            self.assertIn(name, tools_catalog.TOOL_INDEX)

    def test_tools_are_read_only(self):
        for name in ("kompas_cabinet_spec_check", "kompas_cabinet_layout_check"):
            self.assertTrue(tools_catalog.TOOL_INDEX[name]["annotations"]["readOnlyHint"])

    def test_tools_have_a_route(self):
        for name in ("kompas_cabinet_spec_check", "kompas_cabinet_layout_check"):
            entry = tools_catalog.ACTION_MAP.get(name)
            self.assertIsNotNone(entry, name)
            self.assertEqual(entry[0], {"kompas_cabinet_spec_check": "cabinet.spec_check",
                                        "kompas_cabinet_layout_check": "cabinet.layout_check"}[name])

    def test_surface_grew_to_forty_nine(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_registry_wired(self):
        """engineering_capabilities не импортируется статически (win32com),
        поэтому маршрут проверяем по исходнику."""
        path = os.path.join(ROOT, "src", "engineering_capabilities.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("from cabinet_tools import", body)
        self.assertIn('"cabinet.spec_check": cabinet_spec_check', body)
        self.assertIn('"cabinet.layout_check": cabinet_layout_check', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
