"""Волна 15: корпуса щита как данные.

Проверяется главное различие корпуса и аппарата: аппарат **ставится на плату**,
а корпус **есть** плата. Поэтому корпус не получает позицию раскладки, а отдаёт
габарит, внутри которого раскладка считается.

И второе: расчётная ширина ряда помечена как расчётная. Производитель
публикует число модулей и число рядов, но не ширину ряда в миллиметрах —
подставить её как паспортное значение нельзя.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_enclosure  # noqa: E402
import cabinet_catalog  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
CATALOG_PATH = os.path.join(ROOT, "data", "cabinet_enclosures.example.json")


def enclosure(**extra):
    item = {"kind": "enclosure", "article": "SHCHRN-24", "manufacturer": "IEK",
            "name": "Корпус ЩРН-П-24", "width_mm": 270.0, "height_mm": 327.0,
            "depth_mm": 99.0, "modules": 24, "rows": 2, "ip": "IP41",
            "material": "Пластик", "mounting_plate": False, "din_rail": True}
    item.update(extra)
    return item


class RowWidthTests(unittest.TestCase):

    def test_row_width_is_modules_over_rows_times_module(self):
        width, reason = cabinet_enclosure.row_width_mm(enclosure())
        self.assertIsNone(reason)
        self.assertEqual(width, 24 / 2 * 18.0)

    def test_single_row_is_all_modules(self):
        width, _ = cabinet_enclosure.row_width_mm(enclosure(modules=12, rows=1))
        self.assertEqual(width, 216.0)

    def test_without_modules_is_refused(self):
        width, reason = cabinet_enclosure.row_width_mm(enclosure(modules=None))
        self.assertIsNone(width)
        self.assertEqual(reason, "no_modules")

    def test_module_width_is_the_confirmed_ieK_module(self):
        self.assertEqual(cabinet_enclosure.MODULE_WIDTH_MM, 18.0)


class PlateTests(unittest.TestCase):

    def test_plate_width_is_derived_not_nameplate(self):
        plate = cabinet_enclosure.plate_of(enclosure())
        self.assertTrue(plate["derived"])
        self.assertEqual(plate["width_mm"], 216.0)
        self.assertIn("modules / rows", plate["formula"])

    def test_plate_height_is_none_because_manufacturer_does_not_publish_it(self):
        plate = cabinet_enclosure.plate_of(enclosure())
        self.assertIsNone(plate["height_mm"])

    def test_plate_carries_enclosure_body(self):
        plate = cabinet_enclosure.plate_of(enclosure())
        self.assertEqual(plate["enclosure_width_mm"], 270.0)
        self.assertEqual(plate["enclosure_depth_mm"], 99.0)

    def test_conflict_is_reported_when_row_is_wider_than_body(self):
        plate = cabinet_enclosure.plate_of(
            enclosure(modules=18, rows=1, width_mm=310.0))
        self.assertIsNotNone(plate["conflict"])
        self.assertIn("противоречивы", plate["conflict"])

    def test_no_conflict_when_row_fits_body(self):
        self.assertIsNone(cabinet_enclosure.plate_of(enclosure())["conflict"])

    def test_without_modules_plate_has_no_width(self):
        plate = cabinet_enclosure.plate_of(enclosure(modules=None))
        self.assertIsNone(plate["width_mm"])
        self.assertEqual(plate["reason"], "no_modules")


class FitTests(unittest.TestCase):

    def test_fits_when_modules_are_enough(self):
        fit = cabinet_enclosure.fits(enclosure(), 24)
        self.assertTrue(fit["ok"])
        self.assertEqual(fit["free_modules"], 0)

    def test_does_not_fit_when_modules_are_short(self):
        fit = cabinet_enclosure.fits(enclosure(), 36)
        self.assertFalse(fit["ok"])
        self.assertEqual(fit["free_modules"], -12)

    def test_conflicting_data_is_never_a_fit(self):
        item = enclosure(modules=18, rows=1, width_mm=310.0)
        self.assertFalse(cabinet_enclosure.fits(item, 18)["ok"])
        self.assertEqual(cabinet_enclosure.fits(item, 18)["verdict"], "unverified")

    def test_fit_without_needed_modules_is_refused(self):
        self.assertFalse(cabinet_enclosure.fits(enclosure(), None)["ok"])


class SelectTests(unittest.TestCase):

    ITEMS = [
        enclosure(article="A", modules=24),
        enclosure(article="B", modules=36, rows=3),
        enclosure(article="C", modules=12, rows=1),
        enclosure(article="D", modules=24, ip="IP65", material="Сталь"),
    ]

    def test_picks_smallest_sufficient(self):
        result = cabinet_enclosure.select({"items": self.ITEMS, "modules": 12})
        self.assertEqual(result["verdict"], "computed")
        articles = [i["article"] for i in result["result"]["items"]]
        self.assertEqual(articles[0], "C")

    def test_filters_by_ip(self):
        result = cabinet_enclosure.select({"items": self.ITEMS, "modules": 24,
                                           "ip": "ip65"})
        self.assertEqual([i["article"] for i in result["result"]["items"]], ["D"])

    def test_filters_by_rows(self):
        result = cabinet_enclosure.select({"items": self.ITEMS, "modules": 24,
                                           "rows": 3})
        self.assertEqual([i["article"] for i in result["result"]["items"]], ["B"])

    def test_without_modules_is_refused(self):
        result = cabinet_enclosure.select({"items": self.ITEMS})
        self.assertEqual(result["verdict"], "refused")

    def test_empty_catalog_is_refused(self):
        result = cabinet_enclosure.select({"items": [], "modules": 12})
        self.assertEqual(result["verdict"], "refused")


class CatalogTests(unittest.TestCase):
    """Корпус в каталоге: свои поля, свои фильтры, и не позиция на плате."""

    def test_enclosure_kind_is_declared(self):
        self.assertIn("enclosure", cabinet_catalog.KINDS)

    def test_enclosure_gets_no_layout_position(self):
        row = {"kind": "enclosure", "width_mm": 270.0, "height_mm": 327.0,
               "depth_mm": 99.0, "modules": 24, "rows": 2}
        layout, reason = cabinet_catalog.item_to_layout(row)
        self.assertIsNone(layout)
        self.assertEqual(reason, "enclosure_is_not_a_position")

    def test_enclosure_without_depth_is_unfit(self):
        row = {"kind": "enclosure", "width_mm": 270.0, "height_mm": 327.0,
               "modules": 24, "rows": 2}
        layout, reason = cabinet_catalog.item_to_layout(row)
        self.assertIsNone(layout)
        self.assertIn("depth_mm", reason)

    def test_device_still_gets_a_layout_position(self):
        layout, reason = cabinet_catalog.item_to_layout(
            {"kind": "breaker", "width_mm": 18.0, "height_mm": 80.0})
        self.assertIsNotNone(layout)
        self.assertIsNone(reason)

    def test_filters_by_modules(self):
        items = [enclosure(article="A", modules=12), enclosure(article="B", modules=36)]
        result = cabinet_catalog.search(
            {"catalog": items, "query": {"modules_min": 24}})
        self.assertEqual([i["article"] for i in result["items"]], ["B"])

    def test_filters_by_ip(self):
        items = [enclosure(article="A", ip="IP41"), enclosure(article="B", ip="IP65")]
        result = cabinet_catalog.search({"catalog": items, "query": {"ip": "ip65"}})
        self.assertEqual([i["article"] for i in result["items"]], ["B"])

    def test_filters_by_rows_and_mounting_plate(self):
        items = [enclosure(article="A", rows=1, mounting_plate=False),
                 enclosure(article="B", rows=2, mounting_plate=True)]
        result = cabinet_catalog.search(
            {"catalog": items, "query": {"rows": 2, "mounting_plate": True}})
        self.assertEqual([i["article"] for i in result["items"]], ["B"])

    def test_enclosure_row_carries_plate_and_no_layout(self):
        result = cabinet_catalog.search({"catalog": [enclosure()], "query": {}})
        row = result["items"][0]
        self.assertIsNone(row["layout"])
        self.assertEqual(row["plate"]["width_mm"], 216.0)
        self.assertEqual(row["ip"], "IP41")
        self.assertEqual(row["modules"], 24.0)

    def test_article_becomes_identifier(self):
        result = cabinet_catalog.search({"catalog": [enclosure()], "query": {}})
        self.assertEqual(result["items"][0]["id"], "SHCHRN-24")
        self.assertEqual(result["items"][0]["article"], "SHCHRN-24")


class RealDataTests(unittest.TestCase):
    """Реальные корпуса IEK из открытого каталога."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(CATALOG_PATH):
            raise unittest.SkipTest("нет собранного каталога корпусов")
        with open(CATALOG_PATH, encoding="utf-8") as handle:
            cls.document = json.load(handle)

    def test_catalog_is_real_and_marked(self):
        self.assertGreater(len(self.document["items"]), 500)
        self.assertTrue(self.document["example"])
        self.assertIn("IEK", self.document["source"])

    def test_every_item_has_body_dimensions(self):
        for item in self.document["items"]:
            self.assertIsNotNone(item["width_mm"], item["article"])
            self.assertIsNotNone(item["height_mm"], item["article"])
            self.assertIsNotNone(item["depth_mm"], item["article"])

    def test_row_width_never_exceeds_body_except_recorded_conflicts(self):
        """Правило modules / rows × 18 мм проверено на всей выборке."""
        sized = [i for i in self.document["items"] if i["modules"] and i["rows"]]
        self.assertGreater(len(sized), 200)
        conflicts = [i["article"] for i in sized
                     if cabinet_enclosure.plate_of(i)["conflict"]]
        self.assertLessEqual(len(conflicts), 2,
                             "противоречий больше, чем зафиксировано: %s" % conflicts)

    def test_known_enclosure_matches_the_nameplate(self):
        """ЩРН-П-24: 24 модуля в два ряда, полезная ширина ряда 216 мм."""
        found = [i for i in self.document["items"]
                 if i["article"] == "MKP12-N-24-40-10"]
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item["modules"], 24)
        self.assertEqual(item["rows"], 2)
        self.assertEqual(item["ip"], "IP41")
        self.assertEqual(cabinet_enclosure.row_width_mm(item)[0], 216.0)
        self.assertGreater(item["width_mm"], 216.0)

    def test_search_over_real_catalog(self):
        result = cabinet_catalog.search(
            {"catalog_path": "data/cabinet_enclosures.example.json",
             "query": {"kind": "enclosure", "modules_min": 36, "ip": "IP41"},
             "limit": 5})
        self.assertEqual(result["verdict"], "computed")
        self.assertTrue(result["items"])
        self.assertEqual(result["items"][0]["ip"], "IP41")
        self.assertGreaterEqual(result["items"][0]["modules"], 36)

    def test_provenance_names_the_source(self):
        result = cabinet_catalog.search(
            {"catalog_path": "data/cabinet_enclosures.example.json",
             "query": {"kind": "enclosure"}, "limit": 1})
        self.assertTrue(result["provenance"])
        self.assertIn("IEK", result["provenance"][0]["source"])


class SurfaceTests(unittest.TestCase):
    """Поверхность не выросла: корпуса вошли в существующий инструмент."""

    def test_tool_count_unchanged(self):
        import tools_catalog
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_existing_tool_gained_enclosure_filters(self):
        import tools_catalog
        tool = next(t for t in tools_catalog.TOOLS
                    if t["name"] == "kompas_cabinet_catalog_search")
        properties = tool["inputSchema"]["properties"]
        for field in ("modules_min", "rows", "ip", "mounting_plate", "din_rail"):
            self.assertIn(field, properties)
        self.assertIn("enclosure", tool["description"])


if __name__ == "__main__":
    unittest.main()
