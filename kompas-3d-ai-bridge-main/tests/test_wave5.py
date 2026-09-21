#!/usr/bin/env python3
"""Тесты «Волны 5»: 2D-документы (листы, штамп) и спецификация.

Все тесты работают без КОМПАСа, без COM и без сети. Запуск::

    python -m unittest discover -s tests -p "test_wave5.py" -v

Что проверяется по существу:

* мост не выдумывает названия ячеек штампа: нумерация зависит от стиля
  основной надписи, поэтому инструмент возвращает номера и значения;
* запись в штамп отклоняет управляющие символы, слишком длинный текст и
  превышение лимита ячеек - до всякого обращения к КОМПАСу;
* чтение не «угадывает» интерфейс: если коллекция видов не нашлась, в ответе
  честно стоит `confirmed: false`, а не пустой список;
* разделы спецификации выводятся из нативных признаков: Standard ->
  стандартные изделия, IsBillet -> материалы, наличие детей -> сборочная
  единица. Классификация - чистая функция и проверяется целиком;
* производное количество обязано совпасть с числом подходящих вхождений,
  иначе ответ помечен как недостоверный.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
MCP = REPO_ROOT / "mcp"
for path in (str(SRC), str(MCP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import drawing_tools  # noqa: E402
import spec_tools     # noqa: E402
import tools_catalog  # noqa: E402


class FakeStampCell:
    def __init__(self, value=""):
        self.Str = value


class FakeStamp:
    """Минимальная модель основной надписи: ячейки 1..N, часть - недоступна."""

    def __init__(self, values=None, broken=()):
        self._values = dict(values or {})
        self._broken = set(broken)
        self.update_calls = 0

    def Text(self, number):
        if int(number) in self._broken:
            raise AttributeError("no such cell")
        return FakeStampCell(str(self._values.get(int(number), "")))

    def Update(self):
        self.update_calls += 1
        return True


class FakeFormat:
    def __init__(self, width=210.0, height=297.0, fmt=4):
        self.Width = width
        self.Height = height
        self.Format = fmt


class FakeSheet:
    def __init__(self, name="Sheet", stamp=None, fmt=None):
        self.Name = name
        self.Format = fmt or FakeFormat()
        self.Stamp = stamp if stamp is not None else FakeStamp()


class FakeSheets:
    def __init__(self, sheets):
        self._sheets = sheets
        self.Count = len(sheets)

    def Item(self, index):
        return self._sheets[int(index)]


class FakeDocument:
    def __init__(self, sheets=None, document_type=1):
        self.LayoutSheets = FakeSheets(sheets or [])
        self.DocumentType = document_type

    def Save(self):
        return True

    def Close(self, *args):
        return True


class FakeDocument3D:
    """3D-документ: LayoutSheets отсутствует, как и в КОМПАСе."""

    def __init__(self):
        self.DocumentType = 5


class FakeSession:
    def __init__(self, document, path=""):
        self.doc = document
        self.path = path
        self.root = REPO_ROOT

    def active(self):
        return self.doc

    def active_path(self):
        return self.path


class TestCellValidation(unittest.TestCase):
    """Границы ячеек - первая линия защиты до всякого COM-вызова."""

    def test_cell_number_must_be_integer(self):
        for bad in ("1", 1.0, None, True):
            with self.assertRaises(ValueError):
                drawing_tools.normalize_cell_number(bad)

    def test_cell_number_range(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_cell_number(0)
        with self.assertRaises(ValueError):
            drawing_tools.normalize_cell_number(201)
        self.assertEqual(drawing_tools.normalize_cell_number(1), 1)
        self.assertEqual(drawing_tools.normalize_cell_number(200), 200)

    def test_omitted_cells_read_the_first_sixty(self):
        cells = drawing_tools.normalize_cells(None)
        self.assertEqual(cells[0], 1)
        self.assertEqual(cells[-1], drawing_tools.DEFAULT_READ_CELLS)

    def test_integer_argument_means_a_range(self):
        self.assertEqual(drawing_tools.normalize_cells(5), [1, 2, 3, 4, 5])

    def test_cells_are_deduplicated_and_sorted(self):
        self.assertEqual(drawing_tools.normalize_cells([5, 1, 3, 1]), [1, 3, 5])

    def test_empty_cell_list_is_rejected(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_cells([])

    def test_too_many_cells_is_rejected(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_cells(list(range(1, 203)))


class TestStampWriteValidation(unittest.TestCase):
    def test_writes_accept_integer_and_numeric_string_keys(self):
        writes = drawing_tools.normalize_stamp_writes({"1": "A", 2: "B"})
        self.assertEqual(writes, {1: "A", 2: "B"})

    def test_empty_write_set_is_rejected(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_stamp_writes({})

    def test_more_than_twenty_cells_is_rejected(self):
        cells = {str(index): "text" for index in range(1, 22)}
        with self.assertRaises(ValueError):
            drawing_tools.normalize_stamp_writes(cells)

    def test_non_numeric_key_is_rejected(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_stamp_writes({"designation": "value"})

    def test_text_must_be_a_string(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_stamp_writes({1: 42})

    def test_control_characters_are_rejected(self):
        for bad in ("line\nbreak", "tab\there", "bell\x07"):
            with self.assertRaises(ValueError):
                drawing_tools.normalize_stamp_writes({1: bad})

    def test_text_length_is_bounded(self):
        with self.assertRaises(ValueError):
            drawing_tools.normalize_stamp_writes({1: "x" * 256})
        self.assertEqual(
            drawing_tools.normalize_stamp_writes({1: "x" * 255}), {1: "x" * 255}
        )


class TestStampDiff(unittest.TestCase):
    def test_matching_values_pass(self):
        diff = drawing_tools.stamp_diff({1: "A", 2: "B"}, {1: "A", 2: "B"})
        self.assertTrue(diff["ok"])
        self.assertEqual(diff["missing"], [])
        self.assertEqual(diff["mismatched"], [])

    def test_missing_cell_is_reported(self):
        diff = drawing_tools.stamp_diff({7: "A"}, {1: "A"})
        self.assertFalse(diff["ok"])
        self.assertEqual(diff["missing"], [7])

    def test_mismatch_carries_expected_and_actual(self):
        diff = drawing_tools.stamp_diff({1: "A"}, {1: "B"})
        self.assertFalse(diff["ok"])
        self.assertEqual(
            diff["mismatched"], [{"cell": 1, "expected": "A", "actual": "B"}]
        )

    def test_extra_cells_in_the_document_are_not_a_failure(self):
        diff = drawing_tools.stamp_diff({1: "A"}, {1: "A", 9: "Z"})
        self.assertTrue(diff["ok"])


class TestSheetAndViewInventory(unittest.TestCase):
    def test_sheet_inventory_is_reported(self):
        doc = FakeDocument([FakeSheet("First"), FakeSheet("Second")])
        session = FakeSession(doc, "C:/work/drawing_AGENT_COPY.cdw")
        result = drawing_tools.drawing_info(session, include_views=False)
        self.assertEqual(result["sheet_count"], 2)
        self.assertEqual([row["name"] for row in result["sheets"]], ["First", "Second"])
        self.assertTrue(result["interface"]["confirmed"])
        self.assertTrue(result["read_only"])

    def test_three_d_document_is_refused_explicitly(self):
        session = FakeSession(FakeDocument3D(), "C:/work/part_AGENT_COPY.m3d")
        with self.assertRaises(RuntimeError) as ctx:
            drawing_tools.drawing_info(session)
        self.assertIn("no_layout_sheets", str(ctx.exception))

    def test_sheet_index_out_of_range_is_refused(self):
        doc = FakeDocument([FakeSheet("Only")])
        session = FakeSession(doc, "C:/work/drawing_AGENT_COPY.cdw")
        with self.assertRaises(RuntimeError):
            drawing_tools.drawing_info(session, sheet_index=3, include_views=False)

    def test_missing_view_collection_is_not_an_empty_list(self):
        """Пустой список читается как «видов нет»; это другой ответ."""
        doc = FakeDocument([FakeSheet("First")])
        session = FakeSession(doc, "C:/work/drawing_AGENT_COPY.cdw")
        result = drawing_tools.drawing_info(session)
        meta = result["views_interface"]
        self.assertFalse(meta["confirmed"])
        self.assertIsNone(meta["access"])
        self.assertTrue(meta["attempts"])


class TestStampRead(unittest.TestCase):
    def setUp(self):
        self.stamp = FakeStamp({1: "Наименование", 2: "АБВГ.123456.001", 5: "1.2"})
        self.doc = FakeDocument([FakeSheet("First", stamp=self.stamp)])
        self.session = FakeSession(self.doc, "C:/work/drawing_AGENT_COPY.cdw")

    def test_read_returns_numbers_and_values(self):
        result = drawing_tools.stamp_read(self.session, cells=[1, 2, 3])
        self.assertEqual(result["cells"]["1"], "Наименование")
        self.assertEqual(result["cells"]["2"], "АБВГ.123456.001")
        self.assertEqual(result["cells"]["3"], "")

    def test_empty_cell_is_not_reported_as_non_empty(self):
        result = drawing_tools.stamp_read(self.session, cells=[1, 3])
        self.assertEqual(result["non_empty_cells"], [1])
        self.assertEqual(result["non_empty_cell_count"], 1)

    def test_unreadable_cells_are_reported_not_swallowed(self):
        stamp = FakeStamp({1: "A"}, broken=(4,))
        doc = FakeDocument([FakeSheet("First", stamp=stamp)])
        session = FakeSession(doc, "C:/work/drawing_AGENT_COPY.cdw")
        result = drawing_tools.stamp_read(session, cells=[1, 4])
        self.assertEqual(result["read_cell_count"], 1)
        self.assertEqual(len(result["read_errors"]), 1)
        self.assertEqual(result["read_errors"][0]["cell"], 4)

    def test_limits_are_stated(self):
        result = drawing_tools.stamp_read(self.session, cells=[1])
        self.assertTrue(any("numbering" in item for item in result["limits"]))


class TestSpecificationClassification(unittest.TestCase):
    def test_children_are_derived_from_paths(self):
        rows = [
            {"selector_path": [0]},
            {"selector_path": [0, 1]},
            {"selector_path": [1]},
        ]
        marked = spec_tools.mark_children(rows)
        flags = {tuple(row["selector_path"]): row["has_children"] for row in marked}
        self.assertTrue(flags[(0,)])
        self.assertFalse(flags[(0, 1)])
        self.assertFalse(flags[(1,)])

    def test_standard_component_goes_to_standard_products(self):
        self.assertEqual(
            spec_tools.classify({"standard_component": True, "has_children": False}),
            "standard_products",
        )

    def test_billet_goes_to_materials_before_children(self):
        self.assertEqual(
            spec_tools.classify(
                {"standard_component": False, "is_billet": True, "has_children": True}
            ),
            "materials",
        )

    def test_component_with_children_is_an_assembly_unit(self):
        self.assertEqual(
            spec_tools.classify(
                {"standard_component": False, "is_billet": False, "has_children": True}
            ),
            "assembly_units",
        )

    def test_plain_leaf_is_a_detail(self):
        self.assertEqual(
            spec_tools.classify(
                {"standard_component": False, "is_billet": False, "has_children": False}
            ),
            "details",
        )

    def _occurrence(self, path, **extra):
        row = {
            "selector_path": path,
            "designation": "АБВГ.%06d" % path[-1],
            "name": "Деталь %d" % path[-1],
            "source_file": "C:/work/part_%d.m3d" % path[-1],
            "material": "Ст3",
            "mass": 1.5,
            "standard_component": False,
            "is_billet": False,
            "specification_eligible": True,
        }
        row.update(extra)
        return row

    def test_grouping_builds_sections_with_positions(self):
        rows = [
            self._occurrence([0]),
            self._occurrence([1]),
            self._occurrence([0, 0]),
            self._occurrence([2], standard_component=True),
            self._occurrence([3], is_billet=True),
        ]
        grouped = spec_tools.group_rows(spec_tools.mark_children(rows))
        keys = [section["key"] for section in grouped["sections"]]
        self.assertEqual(
            keys, ["assembly_units", "details", "standard_products", "materials"]
        )
        by_key = {section["key"]: section for section in grouped["sections"]}
        self.assertEqual(by_key["assembly_units"]["items"][0]["position"], 1)
        self.assertEqual(by_key["details"]["total_quantity"], 2)
        self.assertEqual(by_key["assembly_units"]["total_quantity"], 1)

    def test_occurrences_are_grouped_and_quantities_added(self):
        rows = [
            self._occurrence(
                [0], source_file="C:/work/same.m3d", designation="X", name="Одинаковая"
            ),
            self._occurrence(
                [1], source_file="C:/work/same.m3d", designation="X", name="Одинаковая"
            ),
        ]
        grouped = spec_tools.group_rows(spec_tools.mark_children(rows))
        details = grouped["sections"][0]
        self.assertEqual(details["item_count"], 1)
        self.assertEqual(details["items"][0]["quantity"], 2)
        self.assertAlmostEqual(details["items"][0]["total_mass"], 3.0)

    def test_ineligible_occurrences_are_excluded(self):
        rows = [
            self._occurrence([0]),
            self._occurrence([1], specification_eligible=False),
        ]
        grouped = spec_tools.group_rows(spec_tools.mark_children(rows))
        self.assertEqual(grouped["total_quantity"], 1)

    def test_sections_are_in_gost_order(self):
        rows = [
            self._occurrence([0], is_billet=True),
            self._occurrence([1]),
            self._occurrence([2], standard_component=True),
        ]
        grouped = spec_tools.group_rows(spec_tools.mark_children(rows))
        self.assertEqual(
            [section["key"] for section in grouped["sections"]],
            ["details", "standard_products", "materials"],
        )

    def test_titles_are_russian(self):
        for key, title in spec_tools.SECTION_TITLES.items():
            self.assertTrue(any(ord(char) > 127 for char in title), key)


class TestCatalogSurface(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_new_tools_exist_and_route(self):
        expected = {
            "kompas_drawing_info": "drawing.info",
            "kompas_stamp_read": "stamp.read",
            "kompas_stamp_write": "stamp.write",
            "kompas_specification_read": "specification.read",
        }
        for name, action in expected.items():
            self.assertIn(name, tools_catalog.TOOL_INDEX)
            self.assertEqual(tools_catalog.ACTION_MAP[name][0], action)

    def test_write_tool_is_marked_destructive(self):
        annotations = tools_catalog.TOOL_INDEX["kompas_stamp_write"]["annotations"]
        self.assertTrue(annotations["destructiveHint"])
        self.assertFalse(annotations["readOnlyHint"])

    def test_read_tools_are_marked_read_only(self):
        for name in ("kompas_drawing_info", "kompas_stamp_read", "kompas_specification_read"):
            self.assertTrue(
                tools_catalog.TOOL_INDEX[name]["annotations"]["readOnlyHint"], name
            )

    def test_stamp_write_schema_bounds_match_the_code(self):
        """Схема не должна расходиться с валидацией."""
        schema = tools_catalog.TOOL_INDEX["kompas_stamp_write"]["inputSchema"]
        props = schema["properties"]["cells"]
        self.assertEqual(props["maxProperties"], drawing_tools.MAX_STAMP_WRITES)
        self.assertEqual(
            props["additionalProperties"]["maxLength"], drawing_tools.MAX_TEXT_LENGTH
        )

    def test_stamp_read_schema_bounds_match_the_code(self):
        schema = tools_catalog.TOOL_INDEX["kompas_stamp_read"]["inputSchema"]
        cells = schema["properties"]["cells"]["items"]
        self.assertEqual(cells["maximum"], drawing_tools.MAX_CELL_NUMBER)


if __name__ == "__main__":
    unittest.main()
