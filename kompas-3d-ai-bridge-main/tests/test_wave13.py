"""Волна 13: нормативные таблицы как данные.

Волна 11 дала двигатель расчёта без единой таблицы: движок умел считать, но
данные приносила вызывающая сторона. Эта волна кладёт данные в репозиторий —
и проверяет не столько их наличие, сколько происхождение и честность.

Что здесь принципиально:

- у каждой таблицы есть источник: документ, номер таблицы, URL, издатель, дата;
- отбор строк по условиям применения обязателен: без него выбор сечения дал бы
  минимальное сечение по лучшему из способов монтажа;
- отбор, не оставивший строк, — отказ со списком доступных значений, а не
  «возьмём похожее»;
- то, что добыть не удалось, отсутствует: таблица коэффициентов спроса покрывает
  один тип нагрузки, остальные уходят в unverified.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_calc as cc  # noqa: E402
import normative_tables as nt  # noqa: E402
import tools_catalog  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORMATIVE_DIR = os.path.join(PROJECT_ROOT, "data", "normative")


def codes(refusals):
    return [item["code"] for item in refusals]


def load(name):
    with open(os.path.join(NORMATIVE_DIR, name + ".json"), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# 1. Инвентарь реестра
# ---------------------------------------------------------------------------
class TestRegistryInventory(unittest.TestCase):
    def test_all_tables_are_present(self):
        answer = nt.handle({"kind": "list"})
        self.assertTrue(answer["ok"])
        names = [item["table"] for item in answer["result"]["tables"]]
        for name in nt.TABLES:
            self.assertIn(name, names)
        self.assertEqual(answer["result"]["missing_count"], 0)

    def test_every_table_carries_a_source(self):
        for name in nt.TABLES:
            with self.subTest(table=name):
                document = load(name)
                source = document.get("source") or {}
                self.assertTrue(source.get("document"), "нет документа-источника")
                self.assertTrue(source.get("url"), "нет URL источника")
                self.assertTrue(source.get("retrieved"), "нет даты получения")

    def test_unknown_kind_is_refused(self):
        answer = nt.handle({"kind": "whatever"})
        self.assertFalse(answer["ok"])
        self.assertIn("unknown_kind", codes(answer["refusals"]))

    def test_unknown_table_is_refused(self):
        answer = nt.handle({"kind": "get", "table": "puetables"})
        self.assertFalse(answer["ok"])
        self.assertIn("unknown_table", codes(answer["refusals"]))

    def test_missing_table_name_is_refused(self):
        answer = nt.handle({"kind": "get"})
        self.assertFalse(answer["ok"])
        self.assertIn("no_table_name", codes(answer["refusals"]))


# ---------------------------------------------------------------------------
# 2. Отбор строк
# ---------------------------------------------------------------------------
class TestRowSelection(unittest.TestCase):
    def test_filter_selects_one_installation_method(self):
        answer = nt.handle({"kind": "get", "table": "ampacity",
                            "filter": {"material": "copper",
                                       "insulation": "pvc",
                                       "loaded_conductors": 3,
                                       "method": "C"}})
        self.assertTrue(answer["ok"])
        rows = answer["result"]["rows"]
        self.assertEqual(len(rows), 16)
        self.assertEqual({row["method"] for row in rows}, {"C"})

    def test_filter_reports_available_values_when_nothing_matches(self):
        answer = nt.handle({"kind": "get", "table": "ampacity",
                            "filter": {"method": "Z9"}})
        self.assertFalse(answer["ok"])
        refusal = answer["refusals"][0]
        self.assertEqual(refusal["code"], "no_rows_after_filter")
        self.assertIn("A1", refusal["available_values"]["method"])

    def test_filter_without_rows_keeps_everything(self):
        answer = nt.handle({"kind": "get", "table": "ampacity"})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"]["total_rows"], 868)

    def test_limit_truncates_and_reports_it(self):
        answer = nt.handle({"kind": "get", "table": "ampacity", "limit": 5})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"]["row_count"], 5)
        self.assertTrue(answer["result"]["truncated"])

    def test_provenance_names_the_document(self):
        answer = nt.handle({"kind": "get", "table": "ampacity", "limit": 1})
        self.assertTrue(answer["provenance"])
        self.assertIn("50571.5.52", answer["provenance"][0]["document"])


# ---------------------------------------------------------------------------
# 3. Проверка по схеме
# ---------------------------------------------------------------------------
class TestSchemaValidation(unittest.TestCase):
    def test_good_rows_pass(self):
        answer = nt.handle({"kind": "validate", "table": "ampacity",
                            "rows": [{"section_mm2": 2.5, "current_a": 24}]})
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["result"]["valid"])

    def test_missing_required_field_is_an_error(self):
        answer = nt.handle({"kind": "validate", "table": "ampacity",
                            "rows": [{"section_mm2": 2.5}]})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["result"]["errors"][0]["code"], "missing_field")

    def test_text_instead_of_number_is_an_error(self):
        answer = nt.handle({"kind": "validate", "table": "ampacity",
                            "rows": [{"section_mm2": 2.5, "current_a": "24 А"}]})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["result"]["errors"][0]["code"], "bad_type")

    def test_trip_curve_point_must_have_a_multiple(self):
        answer = nt.handle({"kind": "validate", "table": "trip_curves",
                            "rows": [{"rating_a": 16, "points": [{"t_min_s": 1}]}]})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["result"]["errors"][0]["code"], "bad_point")

    def test_bundled_tables_pass_their_own_schema(self):
        for name in nt.TABLES:
            with self.subTest(table=name):
                answer = nt.handle({"kind": "validate", "table": name})
                self.assertTrue(answer["ok"], name)

    def test_unknown_table_cannot_be_validated(self):
        answer = nt.handle({"kind": "validate", "table": "nope", "rows": []})
        self.assertFalse(answer["ok"])
        self.assertIn("unknown_table", codes(answer["refusals"]))


# ---------------------------------------------------------------------------
# 4. Реальные значения из публикаций стандартов
# ---------------------------------------------------------------------------
class TestRealValues(unittest.TestCase):
    def test_ampacity_matches_the_standard(self):
        rows, _source, refusals, _notes = nt.load_table("ampacity")
        self.assertFalse(refusals)
        found = [row for row in rows
                 if row["material"] == "copper" and row["insulation"] == "pvc"
                 and row["loaded_conductors"] == 3 and row["method"] == "C"
                 and row["section_mm2"] == 2.5]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["current_a"], 24.0)

    def test_trip_band_depends_on_rating_threshold(self):
        rows, _source, _refusals, _notes = nt.load_table("trip_curves")
        point_255 = {}
        for row in rows:
            if row["trip_curve"] != "C":
                continue
            for point in row["points"]:
                if abs(point["multiple"] - 2.55) < 1e-9:
                    point_255[row["rating_a"]] = point["t_max_s"]
        self.assertEqual(point_255[16.0], 60.0)
        self.assertEqual(point_255[40.0], 120.0)

    def test_dash_in_the_standard_stays_null(self):
        rows, _source, _refusals, _notes = nt.load_table("ampacity")
        dashed = [row for row in rows
                  if row["material"] == "aluminium" and row["method"] == "D2"
                  and row["current_a"] is None]
        self.assertTrue(dashed, "прочерк стандарта должен остаться null")

    def test_demand_factors_table_is_partial(self):
        document = load("demand_factors")
        self.assertTrue(document["source"].get("partial"))
        self.assertEqual(len(document["rows"]), 1)

    def test_ambient_correction_factor_matches_the_standard(self):
        answer = nt.handle({"kind": "get", "table": "derating",
                            "filter": {"kind": "ambient_temperature",
                                       "insulation": "pvc",
                                       "ambient_c": 40}})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"]["rows"][0]["factor"], 0.87)


# ---------------------------------------------------------------------------
# 5. Расчёт по таблицам из реестра
# ---------------------------------------------------------------------------
COPPER_PVC_3_C = {"material": "copper", "insulation": "pvc",
                  "loaded_conductors": 3, "method": "C"}


class TestCalculationWithRegistry(unittest.TestCase):
    def test_cable_size_by_reference(self):
        answer = cc.calculate({
            "kind": "cable_size", "current_a": 20.0, "length_m": 25.0,
            "voltage_v": 400.0, "cos_phi": 0.9, "material": "copper",
            "tables": {"ampacity": "normative:ampacity"},
            "table_filters": {"ampacity": COPPER_PVC_3_C},
        })
        self.assertTrue(answer["ok"])
        selected = answer["result"]["selected"]
        self.assertIsNotNone(selected)
        self.assertEqual(selected["section_mm2"], 2.5)
        self.assertEqual(selected["table_current_a"], 24.0)

    def test_cable_size_takes_table_from_registry_when_only_filter_given(self):
        answer = cc.calculate({
            "kind": "cable_size", "current_a": 20.0,
            "table_filters": {"ampacity": COPPER_PVC_3_C},
        })
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"]["selected"]["section_mm2"], 2.5)
        self.assertTrue(answer["provenance"])

    def test_provenance_names_the_document(self):
        answer = cc.calculate({
            "kind": "cable_size", "current_a": 20.0,
            "tables": {"ampacity": "normative:ampacity"},
            "table_filters": {"ampacity": COPPER_PVC_3_C},
        })
        self.assertTrue(any("50571.5.52" in item.get("source", "")
                            for item in answer["provenance"]))

    def test_bad_filter_refuses_instead_of_selecting(self):
        answer = cc.calculate({
            "kind": "cable_size", "current_a": 20.0,
            "tables": {"ampacity": "normative:ampacity"},
            "table_filters": {"ampacity": {"method": "Z9"}},
        })
        self.assertFalse(answer["ok"])
        self.assertIn("no_rows_after_filter", codes(answer["refusals"]))

    def test_unknown_reference_is_refused(self):
        answer = cc.calculate({
            "kind": "cable_size", "current_a": 20.0,
            "tables": {"ampacity": "normative:whatever"},
        })
        self.assertFalse(answer["ok"])
        self.assertIn("unknown_table", codes(answer["refusals"]))

    def test_breaker_select_from_registry(self):
        answer = cc.calculate({
            "kind": "breaker_select", "current_a": 16.0,
            "allowable_current_a": 24.0, "fault_current_a": 1200.0,
            "tables": {"breakers": "normative:breakers"},
            "table_filters": {"breakers": {"poles": 1, "trip_curve": "C"}},
        })
        self.assertTrue(answer["ok"])
        selected = answer["result"]["selected"]
        self.assertIsNotNone(selected)
        self.assertEqual(selected["rating_a"], 16.0)
        self.assertTrue(selected["passes_all_checked"])

    def test_derating_factor_comes_from_the_table(self):
        answer = cc.calculate({
            "kind": "derating", "base_current_a": 25.0,
            "table_select": {"derating": {"kind": "ambient_temperature",
                                          "insulation": "pvc",
                                          "ambient_c": 40}},
        })
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"]["product"], 0.87)
        self.assertEqual(answer["result"]["derated_current_a"], 21.75)
        self.assertTrue(answer["provenance"])

    def test_derating_unknown_row_is_refused(self):
        answer = cc.calculate({
            "kind": "derating", "base_current_a": 25.0,
            "table_select": {"derating": {"kind": "ambient_temperature",
                                          "insulation": "pvc",
                                          "ambient_c": 41}},
        })
        self.assertFalse(answer["ok"])
        self.assertIn("no_rows_after_filter", codes(answer["refusals"]))

    def test_voltage_drop_limit_comes_from_the_table(self):
        answer = cc.calculate({
            "kind": "cable_size", "current_a": 20.0, "length_m": 120.0,
            "voltage_v": 400.0, "cos_phi": 0.9, "material": "copper",
            "table_filters": {"ampacity": COPPER_PVC_3_C},
            "table_select": {"voltage_drop_limits": {"installation_type": "A",
                                                     "load_type": "other"}},
        })
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["not_checked"], [])
        selected = answer["result"]["selected"]
        self.assertIsNotNone(selected)
        self.assertLessEqual(selected["drop_percent"], 5.0)

    def test_selectivity_out_of_curve_range_is_not_checked(self):
        answer = cc.calculate({
            "kind": "selectivity", "fault_current_a": 3000.0,
            "upstream": {"rating_a": 40, "trip_curve": "C"},
            "downstream": {"rating_a": 16, "trip_curve": "C"},
            "tables": {"trip_curves": "normative:trip_curves"},
        })
        self.assertTrue(answer["ok"])
        self.assertIsNone(answer["result"]["selective"])
        self.assertTrue(answer["not_checked"])

    def test_max_demand_refuses_uncovered_load_type(self):
        answer = cc.calculate({
            "kind": "max_demand",
            "loads": [{"load_type": "outdoor_lighting", "power_kw": 5,
                       "count": 4},
                      {"load_type": "socket_group", "power_kw": 3, "count": 2}],
            "tables": {"demand_factors": "normative:demand_factors"},
        })
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["verdict"], "computed_with_gaps")
        self.assertEqual(answer["result"]["demand_power_w"], 20000.0)
        unverified = answer["result"]["unverified"]
        self.assertEqual(len(unverified), 1)
        self.assertEqual(unverified[0]["reason"], "no_demand_factor")

    def test_partial_table_is_noted(self):
        answer = nt.handle({"kind": "get", "table": "demand_factors"})
        self.assertTrue(answer["ok"])
        self.assertTrue(any("неполная" in note for note in answer["notes"]))


# ---------------------------------------------------------------------------
# 6. Поверхность инструмента
# ---------------------------------------------------------------------------
class TestToolSurface(unittest.TestCase):
    def test_tool_is_registered(self):
        self.assertIn("kompas_normative_tables", tools_catalog.tool_names())

    def test_tool_is_read_only(self):
        tool = [t for t in tools_catalog.TOOLS
                if t["name"] == "kompas_normative_tables"][0]
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_tool_accepts_filters(self):
        tool = [t for t in tools_catalog.TOOLS
                if t["name"] == "kompas_normative_tables"][0]
        properties = tool["inputSchema"]["properties"]
        self.assertIn("filter", properties)
        self.assertIn("rows", properties)

    def test_calc_tool_describes_filters(self):
        tool = [t for t in tools_catalog.TOOLS
                if t["name"] == "kompas_cabinet_calc"][0]
        properties = tool["inputSchema"]["properties"]
        self.assertIn("table_filters", properties)
        self.assertIn("table_select", properties)


if __name__ == "__main__":
    unittest.main()
