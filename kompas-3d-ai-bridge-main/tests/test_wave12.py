"""Волна 12: каталог оборудования и поиск по документам.

Два инструмента, которых у моста не было и которые есть у конкурентов в том или
ином виде: подбор изделий по параметрам (`sans-calc-mcp`) и поиск по документам
со ссылкой на источник (`SwitchScope`).

Главное, что проверяется — не фильтры, а честность: каталог не выдумывает
изделия, пример из репозитория помечен, позиция без габарита не подставляет
типовой размер, а найденная цитата всегда указывает файл и строку.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_catalog as ccat  # noqa: E402
import docs_search as ds  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INLINE = {"source": "каталог КЭАЗ, выгрузка 2026-08", "items": [
    {"id": "b1", "kind": "breaker", "label": "Автомат 1P C16", "poles": 1,
     "rating_a": 16.0, "trip_curve": "C", "breaking_ka": 6.0, "mount": "din",
     "width_mm": 17.5, "height_mm": 85.0, "rail_offset_mm": 45.0},
    {"id": "b2", "kind": "breaker", "label": "Автомат 3P C32", "poles": 3,
     "rating_a": 32.0, "trip_curve": "C", "breaking_ka": 6.0, "mount": "din",
     "width_mm": 52.5, "height_mm": 85.0, "rail_offset_mm": 45.0},
    {"id": "b3", "kind": "breaker", "label": "Автомат 3P D32", "poles": 3,
     "rating_a": 32.0, "trip_curve": "D", "breaking_ka": 4.5, "mount": "din",
     "width_mm": 52.5, "height_mm": 85.0},
    {"id": "r1", "kind": "rcd", "label": "УЗО 2P 30 мА", "poles": 2,
     "rating_a": 40.0, "leakage_ma": 30.0, "rcd_type": "A", "mount": "din",
     "width_mm": 35.0, "height_mm": 85.0},
    {"id": "x1", "kind": "busbar", "label": "Шина PIN", "rating_a": 63.0,
     "mount": "panel"},
]}


class CatalogLoadingTests(unittest.TestCase):
    """Откуда берутся данные и как это помечается."""

    def test_example_catalog_is_flagged(self):
        """Типовой габарит не должен выглядеть как паспорт изделия."""
        result = ccat.search({"kind": "breaker", "limit": 1})
        self.assertTrue(result["example"])
        self.assertTrue(any("пример" in note for note in result["notes"]))

    def test_example_carries_manufacturer_and_source(self):
        """Реальные позиции обязаны нести производителя, серию и источник."""
        items, _meta, refusals, _notes = ccat.load_catalog({})
        self.assertFalse(refusals)
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item.get("manufacturer"), "IEK")
            self.assertTrue(item.get("series"))
            self.assertTrue(item.get("source") or item.get("url"))

    def test_rail_offset_is_left_empty_because_it_is_not_published(self):
        """Производитель не публикует смещение оси рейки — заполнять его нечем."""
        items, _meta, _refusals, _notes = ccat.load_catalog({})
        for item in items:
            self.assertIsNone(item.get("rail_offset_mm"))

    def test_real_width_differs_from_typical_module(self):
        """Главная находка: реальный модуль IEK 18 мм, а не 17,5 из DIN 43880."""
        result = ccat.search({"kind": "breaker", "poles": 1, "limit": 1})
        self.assertEqual(result["items"][0]["width_mm"], 18.0)
        self.assertNotEqual(result["items"][0]["width_mm"], 17.5)

    def test_width_grows_with_pole_count(self):
        widths = {}
        for poles in (1, 2, 3, 4):
            result = ccat.search({"kind": "breaker", "poles": poles, "limit": 1})
            widths[poles] = result["items"][0]["width_mm"]
        self.assertEqual([widths[p] for p in (1, 2, 3, 4)], [18.0, 36.0, 54.0, 72.0])

    def test_rcd_is_wider_than_breaker_of_same_poles(self):
        """УЗО шире автомата: проверяется на реальных числах, а не на ощущении."""
        rcd = ccat.search({"kind": "rcd", "poles": 2, "limit": 1})["items"][0]
        brk = ccat.search({"kind": "breaker", "poles": 2, "limit": 1})["items"][0]
        self.assertGreater(rcd["width_mm"], brk["width_mm"])

    def test_contactor_without_dimensions_is_not_placed(self):
        """Расхождение в данных производителя не разруливается домыслом."""
        result = ccat.search({"kind": "contactor", "need_geometry": True})
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["unverified"])

    def test_inline_catalog_source_is_echoed(self):
        result = ccat.search({"catalog": INLINE, "kind": "breaker"})
        self.assertFalse(result["example"])
        self.assertEqual(result["provenance"][0]["source"],
                         "каталог КЭАЗ, выгрузка 2026-08")

    def test_catalog_without_source_is_noted(self):
        result = ccat.search({"catalog": INLINE["items"], "kind": "breaker"})
        self.assertTrue(any("source" in note for note in result["notes"]))

    def test_empty_query_returns_all_of_a_kind(self):
        result = ccat.search({"catalog": INLINE})
        self.assertEqual(result["count"], 5)

    def test_missing_catalog_file_is_refused(self):
        result = ccat.search({"catalog_path": "data/нет_такого.json"})
        self.assertEqual(result["refusals"][0]["code"], "no_catalog_file")

    def test_path_outside_root_is_refused(self):
        result = ccat.search({"catalog_path": os.path.join(tempfile.gettempdir(),
                                                           "evil.json")})
        self.assertEqual(result["refusals"][0]["code"], "path_outside_root")


class CatalogFilterTests(unittest.TestCase):
    """Подбор по параметрам."""

    def test_kind_filter(self):
        result = ccat.search({"catalog": INLINE, "kind": "rcd"})
        self.assertEqual([i["id"] for i in result["items"]], ["r1"])

    def test_rating_range(self):
        result = ccat.search({"catalog": INLINE, "rating_min_a": 32,
                              "rating_max_a": 32})
        self.assertEqual({i["id"] for i in result["items"]}, {"b2", "b3"})

    def test_trip_curve_filter(self):
        result = ccat.search({"catalog": INLINE, "trip_curve": "D"})
        self.assertEqual([i["id"] for i in result["items"]], ["b3"])

    def test_breaking_capacity_filter(self):
        result = ccat.search({"catalog": INLINE, "breaking_ka_min": 6.0})
        self.assertEqual({i["id"] for i in result["items"]}, {"b1", "b2"})

    def test_poles_filter(self):
        result = ccat.search({"catalog": INLINE, "poles": 3})
        self.assertEqual({i["id"] for i in result["items"]}, {"b2", "b3"})

    def test_leakage_and_rcd_type(self):
        result = ccat.search({"catalog": INLINE, "kind": "rcd",
                              "leakage_ma": 30.0, "rcd_type": "A"})
        self.assertEqual([i["id"] for i in result["items"]], ["r1"])

    def test_mount_filter(self):
        result = ccat.search({"catalog": INLINE, "mount": "panel"})
        self.assertEqual([i["id"] for i in result["items"]], ["x1"])

    def test_width_limit(self):
        result = ccat.search({"catalog": INLINE, "width_max_mm": 20.0})
        self.assertEqual([i["id"] for i in result["items"]], ["b1"])

    def test_text_filter(self):
        result = ccat.search({"catalog": INLINE, "text": "УЗО"})
        self.assertEqual([i["id"] for i in result["items"]], ["r1"])

    def test_sorted_by_rating(self):
        result = ccat.search({"catalog": INLINE, "kind": "breaker"})
        ratings = [i["rating_a"] for i in result["items"]]
        self.assertEqual(ratings, sorted(ratings))

    def test_limit_truncates_and_reports(self):
        result = ccat.search({"catalog": INLINE, "kind": "breaker", "limit": 2})
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total_matched"], 3)

    def test_nested_query_object(self):
        result = ccat.search({"catalog": INLINE, "query": {"kind": "rcd"}})
        self.assertEqual([i["id"] for i in result["items"]], ["r1"])


class CatalogGeometryTests(unittest.TestCase):
    """Связь каталога с раскладкой и расчётом."""

    def test_layout_entry_is_produced(self):
        result = ccat.search({"catalog": INLINE, "kind": "breaker", "limit": 1})
        layout = result["items"][0]["layout"]
        self.assertEqual(layout["width_mm"], 17.5)
        self.assertEqual(layout["height_mm"], 85.0)
        self.assertEqual(layout["rail_offset_mm"], 45.0)
        self.assertTrue(layout["lib_key"])

    def test_item_without_geometry_has_no_layout(self):
        """Нет габарита — нет записи, а не типовой размер."""
        result = ccat.search({"catalog": INLINE, "kind": "busbar"})
        self.assertIsNone(result["items"][0]["layout"])
        self.assertEqual(result["items"][0]["unverified_reason"],
                         "no_geometry:width_mm,height_mm")

    def test_need_geometry_moves_item_to_unverified(self):
        result = ccat.search({"catalog": INLINE, "kind": "busbar",
                              "need_geometry": True})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["unverified"][0]["id"], "x1")
        self.assertEqual(result["verdict"], "computed_with_gaps")

    def test_selected_item_feeds_layout_build(self):
        """Ответ каталога пригоден как библиотека раскладки без правок."""
        from cabinet_build import build_layout
        result = ccat.search({"catalog": INLINE, "kind": "breaker", "limit": 1})
        library = [result["items"][0]["layout"]]
        layout = build_layout({
            "plate": {"width_mm": 600, "height_mm": 800},
            "rail": {"first_rail_y_mm": 130, "pitch_mm": 170},
            "library": library,
            "lines": [{"id": "qf", "lib_key": library[0]["lib_key"], "qty": 1,
                       "tag": "QF1"}],
        })
        self.assertTrue(layout.get("ok", True))
        self.assertFalse(layout["unplaced"])
        elements = layout["layout"]["elements"]
        self.assertEqual(len(elements), 1)
        used = next(item for item in layout["layout"]["library"]
                    if item["lib_key"] == elements[0]["lib_key"])
        self.assertEqual(used["width_mm"], 17.5)
        # габарит из каталога дошёл до координат: y + rail_offset = ось рейки
        self.assertEqual(elements[0]["y_mm"] + used["rail_offset_mm"], 130.0)


class RealDataTests(unittest.TestCase):
    """Проверка на реальном каталоге производителя.

    Эти тесты существуют ради одного: показать, что механизм подбора ловит то,
    что «на глаз» не видно. Подбор аппарата по току КЗ и вместимость ряда —
    места, где правдоподобная цифра дороже пустого ответа.
    """

    @staticmethod
    def _breaker_rows(poles: int = 3) -> list:
        result = ccat.search({"kind": "breaker", "poles": poles, "limit": 0})
        return [{"id": item["id"], "designation": item["id"],
                 "rating_a": item["rating_a"], "trip_curve": item["trip_curve"],
                 "breaking_ka": item["breaking_ka"]}
                for item in result["items"]]

    def test_breaker_is_refused_when_fault_current_exceeds_capacity(self):
        """17,4 кА на вводе против 4,5 кА у серии ВА47-29: выбора нет."""
        import cabinet_calc
        fault = cabinet_calc.calculate({
            "kind": "fault_current", "voltage_v": 380,
            "transformer": {"power_mva": 0.63, "uk_percent": 5.5}})
        self.assertGreater(fault["result"]["fault_current_ka"], 10.0)
        selected = cabinet_calc.calculate({
            "kind": "breaker_select", "current_a": 40.0,
            "fault_current_ka": fault["result"]["fault_current_ka"],
            "tables": {"breakers": {"source": "IEK ВА47-29",
                                    "rows": self._breaker_rows()}}})
        self.assertIsNone(selected["result"]["selected"])

    def test_line_impedance_brings_fault_current_down(self):
        """Та же точка, но за 40 м кабеля 4 мм²: аппарат находится."""
        import cabinet_calc
        fault = cabinet_calc.calculate({
            "kind": "fault_current", "voltage_v": 380,
            "transformer": {"power_mva": 0.63, "uk_percent": 5.5},
            "length_m": 40, "section_mm2": 4, "material": "copper"})
        self.assertLess(fault["result"]["fault_current_ka"], 2.0)
        selected = cabinet_calc.calculate({
            "kind": "breaker_select", "current_a": 40.0,
            "fault_current_ka": fault["result"]["fault_current_ka"],
            "tables": {"breakers": {"source": "IEK ВА47-29",
                                    "rows": self._breaker_rows()}}})
        self.assertIsNotNone(selected["result"]["selected"])
        self.assertEqual(selected["result"]["selected"]["rating_a"], 40.0)

    def test_row_capacity_with_real_width(self):
        """Реальный модуль 18 мм: в ряд входит 26 автоматов, а не 27.

        Цифры сняты с настоящих изделий. Типовой габарит 17,5 мм дал бы лишний
        слот — и этот лишний слот не влез бы в щит.
        """
        from cabinet_build import build_layout
        item = ccat.search({"kind": "breaker", "poles": 1, "limit": 1})["items"][0]
        width = item["width_mm"]
        self.assertEqual(width, 18.0)

        def fits(qty):
            result = build_layout({
                "plate": {"width_mm": 600, "height_mm": 800},
                "rail": {"first_rail_y_mm": 130, "pitch_mm": 170},
                "library": [dict(item["layout"], rail_offset_mm=40.0)],
                "lines": [{"id": "a", "lib_key": item["layout"]["lib_key"],
                           "qty": qty, "tag": "QF1", "internal_gap_mm": 0}]})
            return not result["unplaced"]

        capacity = 0
        for qty in range(1, 40):
            if not fits(qty):
                break
            capacity = qty
        self.assertEqual(capacity, 26)


class DocsSearchTests(unittest.TestCase):
    """Поиск по документам: цитата обязана указывать источник."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=PROJECT_ROOT, prefix="_wave12_docs_")
        with open(os.path.join(cls.tmp, "note.md"), "w", encoding="utf-8") as fh:
            fh.write("# Нормы\n\nСечение кабеля выбирается по допустимому току.\n"
                     "ПУЭ таблица 1.3.4 действует в этой редакции.\n")
        with open(os.path.join(cls.tmp, "other.txt"), "w", encoding="utf-8") as fh:
            fh.write("Ничего общего с кабелем.\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _root(self):
        return os.path.relpath(self.tmp, PROJECT_ROOT)

    def test_citation_has_file_line_and_text(self):
        result = ds.search({"query": "допустимому току", "root": self._root()})
        self.assertTrue(result["ok"])
        match = result["matches"][0]
        self.assertTrue(match["file"].endswith("note.md"))
        self.assertEqual(match["line"], 3)
        self.assertIn("допустимому току", match["text"])

    def test_all_words_must_match(self):
        found = ds.search({"query": "ПУЭ таблица", "root": self._root()})
        self.assertEqual(len(found["matches"]), 1)
        missed = ds.search({"query": "ПУЭ несуществующее", "root": self._root()})
        self.assertEqual(missed["matches"], [])

    def test_context_lines(self):
        """Совпадение в середине файла: строка выше и ниже попадают в цитату."""
        result = ds.search({"query": "Сечение", "root": self._root(),
                            "context_lines": 1})
        self.assertEqual(len(result["matches"][0]["context"]), 3)

    def test_max_results(self):
        result = ds.search({"query": "е", "root": self._root(), "max_results": 2})
        self.assertLessEqual(len(result["matches"]), 2)
        self.assertTrue(any("максимум" in note for note in result["notes"]))

    def test_extensions_filter(self):
        result = ds.search({"query": "кабелем", "root": self._root(),
                            "extensions": [".md"]})
        self.assertEqual(result["matches"], [])

    def test_case_sensitive(self):
        result = ds.search({"query": "пуэ", "root": self._root(),
                            "case_sensitive": True})
        self.assertEqual(result["matches"], [])

    def test_empty_query_is_refused(self):
        result = ds.search({"query": "  ", "root": self._root()})
        self.assertEqual(result["refusals"][0]["code"], "empty_query")

    def test_outside_root_is_refused(self):
        result = ds.search({"query": "что угодно",
                            "root": os.path.join(tempfile.gettempdir(), "x")})
        self.assertEqual(result["refusals"][0]["code"], "path_outside_root")

    def test_missing_root_is_refused(self):
        result = ds.search({"query": "нормы", "root": "нет_такого_каталога"})
        self.assertEqual(result["refusals"][0]["code"], "no_root")

    def test_max_files_truncation_is_reported(self):
        result = ds.search({"query": "кабель", "root": self._root(),
                            "max_files": 1})
        self.assertTrue(result["truncated"])
        self.assertTrue(any("остановлено" in note for note in result["notes"]))

    def test_project_docs_are_searchable(self):
        """Поиск работает и по настоящей документации проекта."""
        result = ds.search({"query": "отказ вместо экстраполяции"})
        self.assertTrue(result["ok"], result.get("refusals"))
        self.assertTrue(result["matches"])
        self.assertTrue(result["matches"][0]["file"].startswith("docs"))


class Honesty12Tests(unittest.TestCase):
    """То, без чего волну нельзя считать честной."""

    def test_no_catalogue_data_is_built_in(self):
        self.assertNotIn("КЭАЗ", ccat.__dict__)
        self.assertNotIn("ABB", ccat.__dict__)
        with open(os.path.join(PROJECT_ROOT, "src", "cabinet_catalog.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("win32com", source)
        self.assertNotIn("requests", source)

    def test_example_file_is_declared_as_example(self):
        path = os.path.join(PROJECT_ROOT, "data", "cabinet_catalog.example.json")
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertTrue(document["example"])
        self.assertIn("iek.ru", document["source"])
        self.assertTrue(document["note"])
        self.assertTrue(document["legal"])

    def test_docs_search_has_no_network(self):
        with open(os.path.join(PROJECT_ROOT, "src", "docs_search.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("urllib", "requests", "socket", "http"):
            self.assertNotIn(forbidden, source)

    def test_both_tools_are_registered(self):
        import tools_catalog
        names = [tool["name"] for tool in tools_catalog.TOOLS]
        self.assertIn("kompas_cabinet_catalog_search", names)
        self.assertIn("kompas_docs_search", names)


if __name__ == "__main__":
    unittest.main()
