"""Волна 10: сверка ожидаемого состава щита с перечнем из КОМПАС.

Модуль чистый: он не читает КОМПАС, а сверяет **переданный** результат
``kompas_bom_read`` с моделью раскладки. Поэтому всё проверяется без
установленного КОМПАСа.

Главное требование к тестам — сверка идёт по **имени файла детали**, а не по
позиционному обозначению. Волна 9 маркирует только исходный экземпляр сета,
поэтому в перечне у двадцати четырёх клемм стоит одно обозначение. Сверять по
обозначениям значило бы получить двадцать три ложных расхождения — отдельный
тест это запрещает.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_build as cb            # noqa: E402
import cabinet_reconcile as cr        # noqa: E402
import cabinet_tools                  # noqa: E402
import tools_catalog                  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# --------------------------------------------------------------------------
# фикстуры
# --------------------------------------------------------------------------

LIBRARY = [
    {"lib_key": "brk", "name": "Автомат 40А", "width_mm": 36.0,
     "height_mm": 90.0, "rail_offset_mm": 45.0},
    {"lib_key": "term", "name": "Клемма", "width_mm": 6.0,
     "height_mm": 40.0, "rail_offset_mm": 25.0},
]


def layout_of(lines, library=None):
    return cb.build_layout({
        "plate": {"width_mm": 600.0, "height_mm": 800.0},
        "rail": {"first_rail_y_mm": 130.0, "pitch_mm": 170.0},
        "library": list(library or LIBRARY),
        "lines": lines,
    })["layout"]


def bom(*rows):
    return {"bom_rows": list(rows)}


def row(source_file, count, designation="", name=""):
    return {"source_file": source_file, "occurrence_count": count,
            "designation": designation, "name": name}


SIMPLE = [{"id": "a", "lib_key": "brk", "qty": 1, "tag": "QF1"}]
TERMINALS = [{"id": "c", "lib_key": "term", "qty": 24, "tag_start": "B101",
              "tag_step": 1, "internal_gap_mm": 1.0}]


# --------------------------------------------------------------------------
# поверхность инструментов
# --------------------------------------------------------------------------

class ToolSurface(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_tool_declared(self):
        self.assertIn("kompas_cabinet_bom_reconcile",
                      [t["name"] for t in tools_catalog.TOOLS])

    def test_tool_is_read_only(self):
        """Сверка ничего не пишет: она сравнивает уже прочитанное."""
        tool = tools_catalog.TOOL_INDEX["kompas_cabinet_bom_reconcile"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_tool_routed(self):
        self.assertEqual(
            tools_catalog.ACTION_MAP["kompas_cabinet_bom_reconcile"][0],
            "cabinet.bom_reconcile")

    def test_registry_wires_action(self):
        path = os.path.join(ROOT, "src", "engineering_capabilities.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('"cabinet.bom_reconcile": cabinet_bom_reconcile', body)

    def test_adapter_is_pure(self):
        out = cabinet_tools.cabinet_bom_reconcile(
            None, {"layout": layout_of(SIMPLE), "bom": bom()})
        self.assertIn("summary", out["result"])


# --------------------------------------------------------------------------
# позиционные обозначения
# --------------------------------------------------------------------------

class Designations(unittest.TestCase):
    def test_next_designation(self):
        self.assertEqual(cr.next_designation("B101", 1), "B102")
        self.assertEqual(cr.next_designation("QF1", 1), "QF2")
        self.assertEqual(cr.next_designation("B101", 2), "B103")

    def test_keeps_zero_padding(self):
        self.assertEqual(cr.next_designation("B009", 1), "B010")

    def test_no_number_means_no_increment(self):
        """Молча вернуть исходное значило бы выдать желаемое за расчёт."""
        self.assertIsNone(cr.next_designation("QF", 1))
        self.assertIsNone(cr.next_designation("", 1))

    def test_series(self):
        series, complete = cr.designation_series("B101", 4, 1)
        self.assertEqual(series, ["B101", "B102", "B103", "B104"])
        self.assertTrue(complete)

    def test_series_stops_when_no_number(self):
        series, complete = cr.designation_series("QF", 3, 1)
        self.assertEqual(series, ["QF"])
        self.assertFalse(complete)

    def test_element_designation(self):
        tags, _ = cr.expected_designations({"tag": "QF1"}, cr.MARKING_FIRST_ONLY)
        self.assertEqual(tags, ["QF1"])

    def test_set_first_only_gives_one_designation(self):
        """Маркируется только исходный экземпляр — остальные наследуют."""
        tags, _ = cr.expected_designations(
            {"count": 24, "tag_start": "B101", "tag_step": 1},
            cr.MARKING_FIRST_ONLY)
        self.assertEqual(tags, ["B101"])

    def test_set_all_gives_full_series(self):
        tags, complete = cr.expected_designations(
            {"count": 3, "tag_start": "B101", "tag_step": 1}, cr.MARKING_ALL)
        self.assertEqual(tags, ["B101", "B102", "B103"])
        self.assertTrue(complete)


# --------------------------------------------------------------------------
# индекс перечня
# --------------------------------------------------------------------------

class BomIndex(unittest.TestCase):
    def test_indexed_by_source_file(self):
        index = cr.bom_index(bom(row("brk_AGENT_COPY.m3d", 3)))
        self.assertIn("brk_agent_copy.m3d", index)
        self.assertEqual(index["brk_agent_copy.m3d"]["occurrence_count"], 3)

    def test_case_insensitive(self):
        """КОМПАС может вернуть имя в другом регистре."""
        index = cr.bom_index(bom(row("BRK_AGENT_COPY.M3D", 1)))
        self.assertIn("brk_agent_copy.m3d", index)

    def test_counts_sum(self):
        index = cr.bom_index(bom(row("term_AGENT_COPY.m3d", 12),
                                 row("term_AGENT_COPY.m3d", 12)))
        self.assertEqual(index["term_agent_copy.m3d"]["occurrence_count"], 24)

    def test_designations_collected(self):
        index = cr.bom_index(bom(row("term_AGENT_COPY.m3d", 1, "B101"),
                                 row("term_AGENT_COPY.m3d", 1, "B102")))
        self.assertEqual(index["term_agent_copy.m3d"]["designations"],
                         ["B101", "B102"])

    def test_rows_without_file_are_skipped(self):
        index = cr.bom_index(bom({"occurrence_count": 1}))
        self.assertEqual(index, {})

    def test_plain_list_accepted(self):
        index = cr.bom_index([row("brk_AGENT_COPY.m3d", 1)])
        self.assertEqual(len(index), 1)


# --------------------------------------------------------------------------
# сверка
# --------------------------------------------------------------------------

class Reconciliation(unittest.TestCase):
    def test_full_match(self):
        result = cr.reconcile({
            "layout": layout_of(SIMPLE + TERMINALS),
            "bom": bom(row("brk_AGENT_COPY.m3d", 1, "QF1"),
                       row("term_AGENT_COPY.m3d", 24, "B101"))})
        self.assertEqual(result["verdict"], "match")
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["matched"], 2)

    def test_terminals_do_not_produce_false_gaps(self):
        """Ключевой тест: 24 клеммы, одно обозначение — это не расхождение."""
        result = cr.reconcile({
            "layout": layout_of(TERMINALS),
            "bom": bom(row("term_AGENT_COPY.m3d", 24, "B101"))})
        self.assertEqual(result["designation_gaps"], [])
        self.assertEqual(result["verdict"], "match")

    def test_policy_all_exposes_the_gap(self):
        result = cr.reconcile({
            "layout": layout_of(TERMINALS),
            "bom": bom(row("term_AGENT_COPY.m3d", 24, "B101")),
            "marking_policy": "all"})
        self.assertEqual(len(result["designation_gaps"]), 1)
        self.assertIn("B102", result["designation_gaps"][0]["missing_designations"])

    def test_gap_is_annotated_with_cause(self):
        """Расхождение объясняется, а не выдаётся за ошибку сборки.

        Политика first_only: ждём одно обозначение, в перечне его нет — причина
        в политике маркировки, а не в том, что клеммы не поставили.
        """
        result = cr.reconcile({
            "layout": layout_of(TERMINALS),
            "bom": bom(row("term_AGENT_COPY.m3d", 24))})
        self.assertEqual(len(result["designation_gaps"]), 1)
        self.assertIn("first_only",
                      result["designation_gaps"][0].get("reason", ""))

    def test_missing_item(self):
        result = cr.reconcile({
            "layout": layout_of(SIMPLE + TERMINALS),
            "bom": bom(row("brk_AGENT_COPY.m3d", 1, "QF1"))})
        self.assertEqual(result["verdict"], "mismatch")
        self.assertEqual(result["summary"]["missing"], 1)
        self.assertEqual(result["missing_in_bom"][0]["filename"],
                         "term_AGENT_COPY.m3d")

    def test_quantity_mismatch(self):
        result = cr.reconcile({
            "layout": layout_of(TERMINALS),
            "bom": bom(row("term_AGENT_COPY.m3d", 20, "B101"))})
        row0 = result["quantity_mismatch"][0]
        self.assertEqual(row0["expected_count"], 24)
        self.assertEqual(row0["actual_count"], 20)
        self.assertEqual(row0["delta"], -4)

    def test_unexpected_item(self):
        result = cr.reconcile({
            "layout": layout_of(SIMPLE),
            "bom": bom(row("brk_AGENT_COPY.m3d", 1, "QF1"),
                       row("psu_AGENT_COPY.m3d", 1, "G1"))})
        self.assertEqual(len(result["unexpected_in_bom"]), 1)
        self.assertEqual(result["unexpected_in_bom"][0]["source_file"],
                         "psu_AGENT_COPY.m3d")

    def test_filename_derived_from_lib_key(self):
        """В библиотеке раскладки имени файла нет — оно выводится правилом."""
        items = cr.expected_items({"layout": layout_of(SIMPLE)})
        self.assertEqual(items[0]["filename"], "brk_AGENT_COPY.m3d")

    def test_explicit_filename_wins(self):
        """Явное имя файла в библиотеке важнее выведенного из ключа.

        Проверяем на «сыром» layout: генератор раскладки библиотеку
        пересобирает и поле filename не сохраняет.
        """
        layout = {"plate": {"width_mm": 600.0, "height_mm": 800.0},
                  "library": [dict(LIBRARY[0], filename="custom_AGENT_COPY.m3d")],
                  "elements": [{"id": "a", "lib_key": "brk", "tag": "QF1",
                                "x_mm": 60.0, "y_mm": 85.0}],
                  "groups": []}
        items = cr.expected_items({"layout": layout})
        self.assertEqual(items[0]["filename"], "custom_AGENT_COPY.m3d")

    def test_expected_counts(self):
        items = cr.expected_items({"layout": layout_of(SIMPLE + TERMINALS)})
        counts = {i["lib_key"]: i["expected_count"] for i in items}
        self.assertEqual(counts, {"brk": 1, "term": 24})

    def test_empty_bom_means_all_missing(self):
        result = cr.reconcile({"layout": layout_of(SIMPLE), "bom": bom()})
        self.assertEqual(result["summary"]["missing"], 1)

    def test_item_without_lib_key_is_unverified(self):
        """Позицию без ключа библиотеки сверить нечем — это не «сошлось»."""
        layout = layout_of(SIMPLE)
        layout["elements"].append({"id": "z", "lib_key": "", "x_mm": 0.0,
                                   "y_mm": 0.0})
        result = cr.reconcile({"layout": layout,
                               "bom": bom(row("brk_AGENT_COPY.m3d", 1, "QF1"))})
        reasons = [row["reason"] for row in result["unverified"]]
        self.assertIn("no_filename", reasons)
        self.assertEqual(result["verdict"], "match_with_unknowns")

    def test_garbage_does_not_raise(self):
        for payload in (None, {}, [], "text", 42,
                        {"layout": "nope", "bom": "nope"}):
            result = cr.reconcile(payload)
            self.assertIn("verdict", result)

    def test_limits_explain_the_key_choice(self):
        result = cr.reconcile({"layout": layout_of(SIMPLE), "bom": bom()})
        self.assertTrue(any("имени файла" in s for s in result["limits"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
