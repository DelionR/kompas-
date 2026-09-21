"""Волна 8: раскладка как генератор координат.

Отличие от волны 7: там раскладка только проверялась, здесь она строится.
Поэтому главные требования к тестам другие:

- неизвестное и неподтверждённое **не должно попадать в геометрию**;
- генератор обязан проверять собственный вывод той же проверкой;
- порядок позиций берётся из входа и не переставляется.

COM не импортируется: модуль чистый, как и вся доменная линейка щита.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_build as cb            # noqa: E402
import cabinet_layout as cl           # noqa: E402
import tools_catalog                  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# --------------------------------------------------------------------------
# фикстуры
# --------------------------------------------------------------------------

def lib(key, width, height, rail_offset=None, name=None, **extra):
    """Позиция библиотеки. ``rail_offset_mm`` — от верхней кромки до оси рейки."""
    row = {"lib_key": key, "name": name or key,
           "width_mm": width, "height_mm": height}
    if rail_offset is not None:
        row["rail_offset_mm"] = rail_offset
    row.update(extra)
    return row


def line(ident, key, qty=1, **extra):
    row = {"id": ident, "lib_key": key, "qty": qty}
    row.update(extra)
    return row


PLATE = {"width_mm": 800.0, "height_mm": 600.0}

LIBRARY = [
    lib("brk", 36.0, 90.0, rail_offset=45.0, name="Автомат 2P"),
    lib("term", 6.0, 40.0, rail_offset=25.0, name="Клемма"),
    lib("psu", 50.0, 100.0, rail_offset=60.0, name="БП 24В"),
]


def build(lines, library=None, plate=None, rail=None, defaults=None, ducts=None):
    payload = {"plate": dict(plate or PLATE),
               "library": list(library or LIBRARY),
               "lines": list(lines)}
    if rail is not None:
        payload["rail"] = rail
    if defaults is not None:
        payload["defaults"] = defaults
    if ducts is not None:
        payload["ducts"] = ducts
    return cb.build_layout(payload)


# --------------------------------------------------------------------------
# поверхность инструментов
# --------------------------------------------------------------------------

class ToolSurface(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_build_tool_declared(self):
        names = [t["name"] for t in tools_catalog.TOOLS]
        self.assertIn("kompas_cabinet_layout_build", names)

    def test_build_tool_is_read_only(self):
        tool = tools_catalog.TOOL_INDEX["kompas_cabinet_layout_build"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_build_tool_routed(self):
        action = tools_catalog.ACTION_MAP["kompas_cabinet_layout_build"][0]
        self.assertEqual(action, "cabinet.layout_build")

    def test_build_tool_input_schema_closed(self):
        tool = tools_catalog.TOOL_INDEX["kompas_cabinet_layout_build"]
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertIn("lines", tool["inputSchema"]["properties"])
        self.assertIn("library", tool["inputSchema"]["properties"])

    def test_registry_wires_action(self):
        """engineering_capabilities не импортируется статически (win32com),
        поэтому маршрут проверяем по исходнику."""
        path = os.path.join(ROOT, "src", "engineering_capabilities.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("cabinet_layout_build", body)
        self.assertIn('"cabinet.layout_build": cabinet_layout_build', body)


# --------------------------------------------------------------------------
# каналы
# --------------------------------------------------------------------------

class SideDucts(unittest.TestCase):
    def test_generated_when_absent(self):
        result = build([line("l1", "brk")])
        self.assertTrue(result["ducts_generated"])
        ids = [d["id"] for d in result["layout"]["ducts"]]
        self.assertIn("duct-left", ids)
        self.assertIn("duct-right", ids)

    def test_supplied_ducts_win(self):
        ducts = [{"id": "mine", "x_mm": 0.0, "y_mm": 0.0, "length_mm": 600.0,
                  "width_mm": 30.0, "rot_deg": 90.0}]
        result = build([line("l1", "brk")], ducts=ducts)
        self.assertFalse(result["ducts_generated"])
        self.assertEqual([d["id"] for d in result["layout"]["ducts"]], ["mine"])

    def test_generated_mark_does_not_leak_into_layout(self):
        """Служебный флаг generated — для человека, а не для модели раскладки."""
        result = build([line("l1", "brk")])
        for duct in result["layout"]["ducts"]:
            self.assertNotIn("generated", duct)

    def test_no_ducts_when_side_duct_disabled(self):
        result = build([line("l1", "brk")], defaults={"side_duct_mm": 0})
        self.assertEqual(result["layout"]["ducts"], [])

    def test_usable_span_excludes_side_ducts(self):
        model = cb.load_build({"plate": PLATE, "library": LIBRARY, "lines": []})
        ducts = cb.make_side_ducts(model)
        span = cb.usable_span(model, ducts)
        # левый канал 40 мм + зазор 20 мм; справа симметрично
        self.assertAlmostEqual(span["x0"], 60.0)
        self.assertAlmostEqual(span["x1"], 740.0)

    def test_usable_span_does_not_collapse_on_middle_duct(self):
        """Канал в середине поля — препятствие, а не граница.

        Регрессия: первая версия брала минимум x по всем каналам, из-за чего
        левый канал ограничивал правую границу и доступная ширина была нулевой.
        """
        model = cb.load_build({"plate": PLATE, "library": LIBRARY, "lines": []})
        ducts = [{"id": "mid", "x_mm": 400.0, "y_mm": 0.0, "length_mm": 600.0,
                  "width_mm": 40.0, "rot_deg": 90.0}]
        span = cb.usable_span(model, ducts)
        self.assertGreater(span["x1"] - span["x0"], 0.0)
        self.assertAlmostEqual(span["x0"], 20.0)


# --------------------------------------------------------------------------
# DIN-рейка: ради чего вся волна
# --------------------------------------------------------------------------

class RailAlignment(unittest.TestCase):
    def test_parts_line_up_on_rail_axis(self):
        """Детали разной высоты стоят на одной оси рейки, а не центрами."""
        result = build([line("l1", "brk"), line("l2", "psu")],
                       rail={"first_rail_y_mm": 120.0})
        ys = {e["id"]: e["y_mm"] for e in result["layout"]["elements"]}
        self.assertAlmostEqual(ys["l1"], 120.0 - 45.0)   # автомат
        self.assertAlmostEqual(ys["l2"], 120.0 - 60.0)   # блок питания выше
        self.assertNotAlmostEqual(ys["l1"], ys["l2"])

    def test_centre_fallback_when_offset_absent(self):
        library = [lib("plain", 20.0, 80.0)]
        result = build([line("l1", "plain")], library=library,
                       rail={"first_rail_y_mm": 200.0})
        element = result["layout"]["elements"][0]
        self.assertAlmostEqual(element["y_mm"], 200.0 - 40.0)

    def test_rail_y_comes_from_rail_block(self):
        result = build([line("l1", "brk")], rail={"first_rail_y_mm": 300.0})
        self.assertAlmostEqual(result["layout"]["elements"][0]["y_mm"], 255.0)


# --------------------------------------------------------------------------
# строки состава: элемент против сета
# --------------------------------------------------------------------------

class Lines(unittest.TestCase):
    def test_single_becomes_element(self):
        result = build([line("l1", "brk", tag="QF1")])
        self.assertEqual(len(result["layout"]["elements"]), 1)
        self.assertEqual(result["layout"]["groups"], [])
        self.assertEqual(result["layout"]["elements"][0]["tag"], "QF1")

    def test_batch_becomes_set(self):
        """Партия — это сет для последующего линейного массива, а не N элементов."""
        result = build([line("l1", "term", qty=12, tag_start="B101", tag_step=1)])
        self.assertEqual(result["layout"]["elements"], [])
        self.assertEqual(len(result["layout"]["groups"]), 1)
        group = result["layout"]["groups"][0]
        self.assertEqual(group["count"], 12)
        self.assertEqual(group["tag_start"], "B101")
        self.assertEqual(group["tag_step"], 1.0)

    def test_set_width_includes_internal_gap(self):
        result = build([line("l1", "term", qty=12, internal_gap_mm=1.0)])
        group = result["layout"]["groups"][0]
        self.assertAlmostEqual(group["internal_gap_mm"], 1.0)
        width = cl.group_width(group, {i["lib_key"]: i for i in LIBRARY})
        self.assertAlmostEqual(width, 12 * 6.0 + 11 * 1.0)

    def test_input_order_is_preserved(self):
        """Генератор не перегруппировывает оборудование: порядок из входа."""
        result = build([line("l3", "psu"), line("l1", "brk"), line("l2", "term")])
        xs = [e["x_mm"] for e in result["layout"]["elements"]]
        self.assertEqual(xs, sorted(xs))
        order = [e["id"] for e in
                 sorted(result["layout"]["elements"], key=lambda e: e["x_mm"])]
        self.assertEqual(order, ["l3", "l1", "l2"])

    def test_gap_between_lines(self):
        result = build([line("l1", "brk"), line("l2", "brk")])
        xs = sorted(e["x_mm"] for e in result["layout"]["elements"])
        self.assertAlmostEqual(xs[1] - xs[0], 36.0 + 5.0)


# --------------------------------------------------------------------------
# перенос по рядам
# --------------------------------------------------------------------------

class RowWrapping(unittest.TestCase):
    # Шесть автоматов по 36 мм с зазором 5 мм требуют 241 мм; панель 240 мм
    # без каналов и без зазоров даёт ровно 240 мм — шестой уходит во второй ряд.
    NARROW = {"width_mm": 240.0, "height_mm": 600.0}
    NO_DUCTS = {"side_duct_mm": 0.0, "clearance_equipment_to_duct_mm": 0.0}

    def test_wrap_uses_pitch(self):
        library = [lib("brk", 36.0, 90.0, rail_offset=45.0)]
        lines = [line("l%d" % i, "brk") for i in range(6)]
        result = build(lines, library=library, plate=self.NARROW,
                       rail={"first_rail_y_mm": 100.0, "pitch_mm": 150.0},
                       defaults=self.NO_DUCTS)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["members"], 5)
        self.assertAlmostEqual(result["rows"][0]["rail_y_mm"], 100.0)
        self.assertAlmostEqual(result["rows"][1]["rail_y_mm"], 250.0)

    def test_wrap_without_pitch_uses_row_height(self):
        library = [lib("brk", 36.0, 90.0, rail_offset=45.0)]
        lines = [line("l%d" % i, "brk") for i in range(6)]
        defaults = dict(self.NO_DUCTS, row_gap_mm=30.0)
        result = build(lines, library=library, plate=self.NARROW,
                       rail={"first_rail_y_mm": 100.0}, defaults=defaults)
        self.assertEqual(len(result["rows"]), 2)
        # высота ряда 90 мм + зазор 30 мм
        self.assertAlmostEqual(result["rows"][1]["rail_y_mm"], 220.0)

    def test_row_starts_at_span_begin(self):
        result = build([line("l%d" % i, "term", qty=20) for i in range(4)],
                       plate={"width_mm": 200.0, "height_mm": 600.0},
                       rail={"first_rail_y_mm": 100.0, "pitch_mm": 150.0},
                       defaults={"side_duct_mm": 0.0})
        self.assertGreater(len(result["rows"]), 1)
        for group in result["layout"]["groups"]:
            self.assertGreaterEqual(group["x_mm"], -1e-9)


# --------------------------------------------------------------------------
# непомещённое: главная граница ответственности
# --------------------------------------------------------------------------

class Unplaced(unittest.TestCase):
    def test_unknown_lib_key(self):
        result = build([line("l1", "no-such-key")])
        self.assertEqual(result["layout"]["elements"], [])
        self.assertEqual(result["unplaced"][0]["reason"], "unknown_lib_key")
        self.assertFalse(result["ok"])

    def test_unconfirmed_size_not_placed(self):
        """Нулевой габарит — не «примерно 90 мм», а отсутствие в геометрии."""
        library = [lib("hazy", 0.0, 0.0)]
        result = build([line("l1", "hazy")], library=library)
        self.assertEqual(result["layout"]["elements"], [])
        self.assertEqual(result["layout"]["groups"], [])
        self.assertEqual(result["unplaced"][0]["reason"], "unconfirmed_size")

    def test_confirm_flag_blocks_placement(self):
        library = [lib("hazy", 40.0, 90.0, confirm=True)]
        result = build([line("l1", "hazy")], library=library)
        self.assertEqual(result["layout"]["elements"], [])
        self.assertEqual(result["unplaced"][0]["reason"], "unconfirmed_size")

    def test_wider_than_plate(self):
        library = [lib("huge", 900.0, 90.0)]
        result = build([line("l1", "huge")], library=library)
        self.assertEqual(result["layout"]["elements"], [])
        self.assertEqual(result["unplaced"][0]["reason"], "wider_than_plate")

    def test_no_vertical_space(self):
        """Ниже некуда: позиция не размещается «за краем панели».

        Узкая панель заставляет шестой автомат перейти на второй ряд, а
        высота 260 мм не оставляет этому ряду места.
        """
        library = [lib("tall", 36.0, 90.0, rail_offset=45.0)]
        lines = [line("l%d" % i, "tall") for i in range(6)]
        result = build(lines, library=library,
                       plate={"width_mm": 240.0, "height_mm": 260.0},
                       rail={"first_rail_y_mm": 100.0, "pitch_mm": 150.0},
                       defaults=RowWrapping.NO_DUCTS)
        reasons = [u["reason"] for u in result["unplaced"]]
        self.assertIn("no_vertical_space", reasons)
        placed = (len(result["layout"]["elements"])
                  + len(result["layout"]["groups"]))
        self.assertEqual(placed + len(result["unplaced"]), 6)

    def test_unplaced_never_reaches_geometry(self):
        """Ни одна запись из unplaced не должна фигурировать в раскладке."""
        library = LIBRARY + [lib("hazy", 0.0, 0.0)]
        lines = [line("l1", "brk"), line("l2", "hazy"), line("l3", "ghost")]
        result = build(lines, library=library)
        placed_ids = {e["id"] for e in result["layout"]["elements"]}
        placed_ids |= {g["id"] for g in result["layout"]["groups"]}
        self.assertEqual(placed_ids, {"l1"})
        self.assertEqual({u["id"] for u in result["unplaced"]}, {"l2", "l3"})


# --------------------------------------------------------------------------
# самопроверка генератора
# --------------------------------------------------------------------------

class SelfCheck(unittest.TestCase):
    def test_clean_build_has_no_errors(self):
        result = build([line("l1", "brk"), line("l2", "term", qty=10),
                        line("l3", "psu")], rail={"first_rail_y_mm": 120.0})
        self.assertEqual(result["self_check"]["summary"]["error"], 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["unplaced"], [])

    def test_result_accepted_by_separate_checker(self):
        """Модель из генератора принимается инструментом проверки как есть."""
        result = build([line("l1", "brk"), line("l2", "term", qty=10)])
        check = cl.check_layout(result["layout"])
        self.assertEqual(check["summary"]["error"], 0)

    def test_self_check_reports_own_fault(self):
        """Генератор не объявляет успех, если сам же нарушил границы."""
        library = [lib("brk", 36.0, 90.0, rail_offset=45.0)]
        result = build([line("l1", "brk")], library=library,
                       plate={"width_mm": 100.0, "height_mm": 50.0},
                       rail={"first_rail_y_mm": 30.0},
                       defaults={"side_duct_mm": 0.0})
        self.assertFalse(result["ok"])

    def test_verdict_present(self):
        result = build([line("l1", "brk")])
        self.assertIn(result["self_check"]["verdict"],
                      ("ready", "ready_with_unknowns", "blocked"))


# --------------------------------------------------------------------------
# устойчивость и детерминизм
# --------------------------------------------------------------------------

class Robustness(unittest.TestCase):
    def test_deterministic(self):
        payload = {"plate": PLATE, "library": LIBRARY,
                   "lines": [line("l1", "brk"), line("l2", "term", qty=8)]}
        first = cb.build_layout(payload)
        second = cb.build_layout(payload)
        self.assertEqual(first["layout"], second["layout"])

    def test_empty_lines(self):
        result = build([])
        self.assertEqual(result["unplaced"], [])
        self.assertEqual(result["rows"], [])

    def test_bad_plate_reported_not_raised(self):
        result = cb.build_layout({"plate": {"width_mm": 0.0, "height_mm": 0.0},
                                  "lines": [line("l1", "brk")],
                                  "library": LIBRARY})
        codes = [i["code"] for i in result["parse_issues"]]
        self.assertIn("BAD_PLATE_SIZE", codes)

    def test_garbage_input_does_not_raise(self):
        for payload in (None, {}, [], "text", 42,
                        {"lines": "nope", "library": "nope"}):
            result = cb.build_layout(payload)
            self.assertIn("layout", result)

    def test_limits_declared(self):
        result = build([line("l1", "brk")])
        self.assertTrue(any("DIN" in s for s in result["limits"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
