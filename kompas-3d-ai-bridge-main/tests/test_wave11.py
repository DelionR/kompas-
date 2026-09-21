"""Волна 11: электрорасчёт щита как чистые функции.

Проверяется не столько арифметика, сколько дисциплина: движок считает то, что
считается физикой, и отказывает там, где нужна нормативная таблица. Главный
класс тестов — отказы: подмена расчёта правдоподобной цифрой здесь страшнее
недостающей функции, потому что её не видно в ответе.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_calc as cc  # noqa: E402

AMPACITY = {"source": "ПУЭ 7-е изд., табл. 1.3.4", "rows": [
    {"section_mm2": 1.5, "current_a": 23},
    {"section_mm2": 2.5, "current_a": 30},
    {"section_mm2": 4, "current_a": 41},
    {"section_mm2": 6, "current_a": 50},
]}

BREAKERS = {"source": "каталог", "rows": [
    {"designation": "АВ 16А C", "rating_a": 16, "trip_curve": "C", "breaking_ka": 6},
    {"designation": "АВ 25А C", "rating_a": 25, "trip_curve": "C", "breaking_ka": 6},
    {"designation": "АВ 32А C", "rating_a": 32, "trip_curve": "C", "breaking_ka": 6},
    {"designation": "АВ 40А D", "rating_a": 40, "trip_curve": "D", "breaking_ka": 4.5},
]}

CURVES = {"source": "каталог, характеристики типа C", "rows": [
    {"rating_a": 16, "trip_curve": "C", "points": [
        {"multiple": 1.45, "t_min_s": 60, "t_max_s": 3600},
        {"multiple": 3, "t_min_s": 0.02, "t_max_s": 8},
        {"multiple": 10, "t_min_s": 0.004, "t_max_s": 0.02}]},
    {"rating_a": 32, "trip_curve": "C", "points": [
        {"multiple": 1.45, "t_min_s": 60, "t_max_s": 3600},
        {"multiple": 3, "t_min_s": 0.02, "t_max_s": 8},
        {"multiple": 10, "t_min_s": 0.004, "t_max_s": 0.02}]},
]}

DEMAND = {"source": "справочник", "rows": [
    {"load_type": "motor", "kc": 0.7},
    {"load_type": "socket", "kc": 0.3},
]}


class DispatchTests(unittest.TestCase):
    """Диспетчер и конверт ответа."""

    def test_unknown_kind_is_refused(self):
        result = cc.calculate({"kind": "ток"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "unknown_kind")
        self.assertIn("current", result["refusals"][0]["known_kinds"])

    def test_every_kind_is_dispatchable(self):
        for kind in cc.KINDS:
            self.assertIn(kind, cc._DISPATCH)

    def test_limits_are_reported(self):
        self.assertTrue(cc.calculate({"kind": "current", "power_w": 1000,
                                      "voltage_v": 380, "cos_phi": 0.9})["limits"])

    def test_verdict_computed_when_nothing_skipped(self):
        result = cc.calculate({"kind": "derating", "base_current_a": 41,
                               "factors": [{"name": "t", "value": 0.9}]})
        self.assertEqual(result["verdict"], "computed")

    def test_verdict_gap_when_criterion_not_checkable(self):
        result = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "copper"})
        self.assertEqual(result["verdict"], "computed_with_gaps")
        self.assertIn("drop_percent", [g["criterion"] for g in result["not_checked"]])


class CurrentTests(unittest.TestCase):
    """Физика: ток по мощности."""

    def test_three_phase_matches_hand_calculation(self):
        result = cc.calculate({"kind": "current", "power_w": 15000,
                               "voltage_v": 380, "cos_phi": 0.9})
        expected = 15000.0 / (math.sqrt(3.0) * 380 * 0.9)
        self.assertAlmostEqual(result["result"]["current_a"], expected, places=3)

    def test_single_phase_uses_no_sqrt3(self):
        result = cc.calculate({"kind": "current", "power_w": 2300,
                               "voltage_v": 230, "cos_phi": 1.0, "phases": 1})
        self.assertAlmostEqual(result["result"]["current_a"], 10.0, places=3)

    def test_power_kw_is_accepted(self):
        result = cc.calculate({"kind": "current", "power_kw": 15,
                               "voltage_v": 380, "cos_phi": 0.9})
        self.assertAlmostEqual(result["result"]["current_a"], 25.322, places=2)

    def test_efficiency_increases_current(self):
        base = cc.calculate({"kind": "current", "power_w": 15000,
                             "voltage_v": 380, "cos_phi": 0.9})
        with_eta = cc.calculate({"kind": "current", "power_w": 15000,
                                 "voltage_v": 380, "cos_phi": 0.9,
                                 "efficiency": 0.9})
        self.assertGreater(with_eta["result"]["current_a"], base["result"]["current_a"])

    def test_cos_phi_out_of_range_is_refused(self):
        result = cc.calculate({"kind": "current", "power_w": 1000,
                               "voltage_v": 380, "cos_phi": 1.4})
        self.assertFalse(result["ok"])
        self.assertIn("bad_cos_phi", [r["code"] for r in result["refusals"]])

    def test_zero_voltage_is_refused(self):
        result = cc.calculate({"kind": "current", "power_w": 1000, "voltage_v": 0})
        self.assertIn("bad_voltage", [r["code"] for r in result["refusals"]])


class VoltageDropTests(unittest.TestCase):
    """Физика: падение напряжения."""

    def test_resistance_matches_rho_l_over_s(self):
        result = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "copper", "voltage_v": 380,
                               "cos_phi": 0.9})
        self.assertAlmostEqual(result["result"]["resistance_ohm"],
                               0.01754 * 40 / 4, places=6)

    def test_three_phase_drop_formula(self):
        result = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "copper", "voltage_v": 380,
                               "cos_phi": 1.0})
        expected = math.sqrt(3.0) * 25 * (0.01754 * 40 / 4)
        self.assertAlmostEqual(result["result"]["drop_v"], expected, places=3)

    def test_single_phase_doubles_the_drop(self):
        single = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "copper", "voltage_v": 230,
                               "cos_phi": 1.0, "phases": 1})
        expected = 2.0 * 25 * (0.01754 * 40 / 4)
        self.assertAlmostEqual(single["result"]["drop_v"], expected, places=3)

    def test_aluminium_drops_more_than_copper(self):
        copper = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "copper", "voltage_v": 380})["result"]
        alu = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                            "length_m": 40, "section_mm2": 4,
                            "material": "aluminium", "voltage_v": 380})["result"]
        self.assertGreater(alu["drop_v"], copper["drop_v"])

    def test_unknown_material_is_refused(self):
        result = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "серебро"})
        self.assertIn("unknown_material", [r["code"] for r in result["refusals"]])

    def test_explicit_resistivity_beats_material(self):
        result = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 100, "section_mm2": 10,
                               "material": "copper",
                               "resistivity_ohm_mm2_per_m": 0.02,
                               "voltage_v": 380})
        self.assertAlmostEqual(result["result"]["resistance_ohm"], 0.2, places=6)

    def test_percent_needs_voltage(self):
        result = cc.calculate({"kind": "voltage_drop", "current_a": 25,
                               "length_m": 40, "section_mm2": 4,
                               "material": "copper"})
        self.assertIsNone(result["result"]["drop_percent"])


class FaultCurrentTests(unittest.TestCase):
    """Физика: ток КЗ. Отказ вместо «типичного» источника."""

    def test_three_phase_from_impedance(self):
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                               "source_impedance_ohm": 0.1})
        self.assertAlmostEqual(result["result"]["fault_current_a"],
                               380 / (math.sqrt(3.0) * 0.1), places=2)

    def test_transformer_nameplate(self):
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                               "transformer": {"power_mva": 0.63,
                                               "uk_percent": 5.5}})
        z = 0.055 * 380 * 380 / 630000.0
        self.assertAlmostEqual(result["result"]["impedance"]["source_ohm"], z, places=6)
        self.assertEqual(result["result"]["source_kind"], "transformer")

    def test_short_circuit_power(self):
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                               "source_short_circuit_mva": 10})
        self.assertEqual(result["result"]["source_kind"], "short_circuit_power")

    def test_line_resistance_is_added(self):
        bare = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                             "source_impedance_ohm": 0.1})["result"]
        with_line = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                                  "source_impedance_ohm": 0.1, "length_m": 40,
                                  "section_mm2": 4, "material": "copper"})["result"]
        self.assertLess(with_line["fault_current_a"], bare["fault_current_a"])
        self.assertAlmostEqual(with_line["impedance"]["line_r_ohm"],
                               0.01754 * 40 / 4, places=6)

    def test_missing_source_is_refused(self):
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380})
        self.assertFalse(result["ok"])
        refusal = result["refusals"][0]
        self.assertEqual(refusal["code"], "no_source_data")
        self.assertIn("transformer", refusal["required_any"])

    def test_single_phase_without_zero_sequence_is_refused(self):
        """Петля «фаза-нуль» не считается без данных о нулевой последовательности."""
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                               "phases": 1, "source_impedance_ohm": 0.1})
        self.assertFalse(result["ok"])
        self.assertIn("no_zero_sequence_data", [r["code"] for r in result["refusals"]])

    def test_single_phase_with_zero_sequence(self):
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                               "phases": 1, "source_impedance_ohm": 0.1,
                               "zero_sequence_impedance_ohm": 0.2})
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["result"]["fault_current_a"],
                               (380 / math.sqrt(3.0)) / 0.3, places=2)

    def test_half_specified_line_is_noted(self):
        """Длина без сечения — это не «линии нет», а молча неучтённое сопротивление."""
        result = cc.calculate({"kind": "fault_current", "voltage_v": 380,
                               "source_impedance_ohm": 0.1, "length_m": 40})
        self.assertTrue(result["ok"])
        self.assertTrue(any("неполностью" in note for note in result["notes"]))


class DeratingTests(unittest.TestCase):
    """Арифметика поправочных коэффициентов."""

    def test_product(self):
        result = cc.calculate({"kind": "derating", "base_current_a": 41,
                               "factors": [{"name": "temperature", "value": 0.94},
                                           {"name": "grouping", "value": 0.8}]})
        self.assertAlmostEqual(result["result"]["product"], 0.752, places=6)
        self.assertAlmostEqual(result["result"]["derated_current_a"], 30.832, places=3)

    def test_factors_as_dict(self):
        result = cc.calculate({"kind": "derating", "base_current_a": 100,
                               "factors": {"grouping": 0.7}})
        self.assertEqual(result["result"]["product"], 0.7)

    def test_no_factors_means_no_reduction(self):
        result = cc.calculate({"kind": "derating", "base_current_a": 41})
        self.assertEqual(result["result"]["product"], 1.0)
        self.assertTrue(any("не заданы" in note for note in result["notes"]))

    def test_zero_factor_is_refused(self):
        result = cc.calculate({"kind": "derating", "base_current_a": 41,
                               "factors": [{"name": "x", "value": 0}]})
        self.assertIn("bad_factor", [r["code"] for r in result["refusals"]])

    def test_factor_above_one_is_flagged(self):
        result = cc.calculate({"kind": "derating", "base_current_a": 41,
                               "factors": [{"name": "x", "value": 1.2}]})
        self.assertTrue(result["ok"])
        self.assertTrue(any("больше единицы" in note for note in result["notes"]))


class MaxDemandTests(unittest.TestCase):
    """Норма: коэффициенты спроса."""

    def test_demand_is_lower_than_installed(self):
        result = cc.calculate({"kind": "max_demand",
                               "loads": [{"load_type": "motor", "power_kw": 11,
                                          "count": 2},
                                         {"load_type": "socket", "power_kw": 2.5,
                                          "count": 3}],
                               "tables": {"demand_factors": DEMAND}})["result"]
        self.assertEqual(result["installed_power_w"], 29500.0)
        self.assertEqual(result["demand_power_w"], 17650.0)

    def test_unknown_load_type_goes_to_unverified(self):
        """Нет коэффициента — нагрузка не «считается по единице», а остается непроверенной."""
        result = cc.calculate({"kind": "max_demand",
                               "loads": [{"load_type": "heater", "power_kw": 5}],
                               "tables": {"demand_factors": DEMAND}})["result"]
        self.assertEqual(result["unverified"][0]["reason"], "no_demand_factor")
        self.assertEqual(result["demand_power_w"], 0.0)

    def test_missing_table_is_refused(self):
        result = cc.calculate({"kind": "max_demand",
                               "loads": [{"load_type": "motor", "power_kw": 11}]})
        self.assertEqual(result["refusals"][0]["required_table"], "demand_factors")

    def test_table_source_is_echoed(self):
        result = cc.calculate({"kind": "max_demand",
                               "loads": [{"load_type": "motor", "power_kw": 11}],
                               "tables": {"demand_factors": DEMAND}})
        self.assertEqual(result["provenance"][0]["source"], "справочник")

    def test_table_without_source_is_flagged(self):
        result = cc.calculate({"kind": "max_demand",
                               "loads": [{"load_type": "motor", "power_kw": 11}],
                               "tables": {"demand_factors": DEMAND["rows"]}})
        self.assertTrue(any("source" in note for note in result["notes"]))


class CableSizeTests(unittest.TestCase):
    """Норма: выбор сечения по таблице допустимых токов."""

    def test_selects_smallest_passing_section(self):
        result = cc.calculate({"kind": "cable_size", "current_a": 25,
                               "tables": {"ampacity": AMPACITY}})["result"]
        self.assertEqual(result["selected"]["section_mm2"], 2.5)

    def test_derating_raises_the_section(self):
        """Снижение допустимого тока не должно «улучшать» сечение."""
        plain = cc.calculate({"kind": "cable_size", "current_a": 25,
                              "tables": {"ampacity": AMPACITY}})["result"]
        derated = cc.calculate({"kind": "cable_size", "current_a": 25,
                                "factors": [{"name": "temperature", "value": 0.8}],
                                "tables": {"ampacity": AMPACITY}})["result"]
        self.assertGreater(derated["selected"]["section_mm2"],
                           plain["selected"]["section_mm2"])

    def test_drop_criterion_raises_the_section(self):
        """По току хватило бы 1.5 мм², по потере напряжения — 6 мм²."""
        by_current = cc.calculate({"kind": "cable_size", "current_a": 20,
                                   "tables": {"ampacity": AMPACITY}})["result"]
        self.assertEqual(by_current["selected"]["section_mm2"], 1.5)

        by_drop = cc.calculate({"kind": "cable_size", "current_a": 20,
                                "length_m": 120, "material": "copper",
                                "voltage_v": 380, "cos_phi": 0.9,
                                "drop_limit_percent": 3.5,
                                "tables": {"ampacity": AMPACITY}})["result"]
        self.assertTrue(by_drop["selected"]["passes_drop"])
        self.assertEqual(by_drop["selected"]["section_mm2"], 6.0)

    def test_no_section_passes_the_drop_limit(self):
        """Слишком строгий предел — это отсутствие решения, а не «возьмём побольше»."""
        result = cc.calculate({"kind": "cable_size", "current_a": 20,
                               "length_m": 120, "material": "copper",
                               "voltage_v": 380, "cos_phi": 0.9,
                               "drop_limit_percent": 1.0,
                               "tables": {"ampacity": AMPACITY}})["result"]
        self.assertIsNone(result["selected"])
        self.assertIn("selection_note", result)

    def test_unlimited_drop_is_not_checked_not_passed(self):
        result = cc.calculate({"kind": "cable_size", "current_a": 25,
                               "tables": {"ampacity": AMPACITY}})
        criteria = [g["criterion"] for g in result["not_checked"]]
        self.assertIn("voltage_drop", criteria)

    def test_no_section_passes_is_a_result_not_a_guess(self):
        result = cc.calculate({"kind": "cable_size", "current_a": 500,
                               "tables": {"ampacity": AMPACITY}})["result"]
        self.assertIsNone(result["selected"])
        self.assertIn("selection_note", result)

    def test_missing_table_is_refused(self):
        result = cc.calculate({"kind": "cable_size", "current_a": 25})
        self.assertEqual(result["refusals"][0]["required_table"], "ampacity")
        self.assertFalse(result["result"])

    def test_current_derived_from_power(self):
        result = cc.calculate({"kind": "cable_size", "power_kw": 15,
                               "voltage_v": 380, "cos_phi": 0.9,
                               "tables": {"ampacity": AMPACITY}})["result"]
        self.assertAlmostEqual(result["load_current_a"], 25.322, places=2)
        self.assertEqual(result["selected"]["section_mm2"], 2.5)


class BreakerSelectTests(unittest.TestCase):
    """Норма: подбор аппарата по каталогу."""

    def test_rating_must_cover_the_load(self):
        result = cc.calculate({"kind": "breaker_select", "current_a": 25.3,
                               "tables": {"breakers": BREAKERS}})["result"]
        self.assertEqual(result["selected"]["rating_a"], 32.0)

    def test_rating_must_not_exceed_cable(self):
        result = cc.calculate({"kind": "breaker_select", "current_a": 20,
                               "allowable_current_a": 27.0,
                               "tables": {"breakers": BREAKERS}})["result"]
        self.assertLessEqual(result["selected"]["rating_a"], 27.0)

    def test_breaking_capacity_is_checked(self):
        result = cc.calculate({"kind": "breaker_select", "current_a": 20,
                               "fault_current_ka": 5.0,
                               "tables": {"breakers": BREAKERS}})["result"]
        for candidate in result["candidates"]:
            if candidate["breaking_ka"] == 4.5:
                self.assertFalse(candidate["passes_breaking"])

    def test_curve_filter(self):
        result = cc.calculate({"kind": "breaker_select", "current_a": 20,
                               "trip_curve": "D",
                               "tables": {"breakers": BREAKERS}})["result"]
        self.assertEqual([c["trip_curve"] for c in result["candidates"]], ["D"])

    def test_missing_catalogue_is_refused(self):
        result = cc.calculate({"kind": "breaker_select", "current_a": 20})
        self.assertEqual(result["refusals"][0]["required_table"], "breakers")

    def test_cable_criterion_without_data_is_not_checked(self):
        result = cc.calculate({"kind": "breaker_select", "current_a": 20,
                               "tables": {"breakers": BREAKERS}})
        self.assertIn("cable_protection",
                      [g["criterion"] for g in result["not_checked"]])


class SelectivityTests(unittest.TestCase):
    """Норма: селективность по время-токовым характеристикам."""

    def test_exact_times_are_compared(self):
        result = cc.calculate({"kind": "selectivity", "fault_current_a": 100,
                               "upstream": {"rating_a": 32, "t_min_s": 0.5},
                               "downstream": {"rating_a": 16, "t_max_s": 0.05}})["result"]
        self.assertTrue(result["selective"])
        self.assertAlmostEqual(result["margin_s"], 0.45, places=6)

    def test_no_margin_means_not_selective(self):
        result = cc.calculate({"kind": "selectivity", "fault_current_a": 100,
                               "upstream": {"rating_a": 32, "t_min_s": 0.02},
                               "downstream": {"rating_a": 16, "t_max_s": 0.05}})["result"]
        self.assertFalse(result["selective"])

    def test_curve_point_is_used_when_times_absent(self):
        """Нижестоящий попадает точно в узел 3×: его время берётся с характеристики."""
        result = cc.calculate({"kind": "selectivity", "fault_current_a": 48,
                               "upstream": {"rating_a": 32, "trip_curve": "C"},
                               "downstream": {"rating_a": 16, "trip_curve": "C"},
                               "tables": {"trip_curves": CURVES}})
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["result"]["downstream"]["multiple"], 3.0, places=6)
        self.assertEqual(result["result"]["downstream"]["t_max_s"], 8.0)
        self.assertIsNotNone(result["result"]["selective"])

    def test_interpolation_is_flagged(self):
        """Интерполяция — допущение, и она не выдается за расчёт по каталогу."""
        result = cc.calculate({"kind": "selectivity", "fault_current_a": 60,
                               "upstream": {"rating_a": 32, "trip_curve": "C"},
                               "downstream": {"rating_a": 16, "trip_curve": "C"},
                               "tables": {"trip_curves": CURVES}})
        self.assertTrue(any("interpolated" in note for note in result["notes"]))

    def test_out_of_range_is_not_extrapolated(self):
        result = cc.calculate({"kind": "selectivity", "fault_current_a": 400,
                               "upstream": {"rating_a": 32, "trip_curve": "C"},
                               "downstream": {"rating_a": 16, "trip_curve": "C"},
                               "tables": {"trip_curves": CURVES}})
        self.assertIsNone(result["result"]["selective"])
        self.assertTrue(any("вне диапазона" in note for note in result["notes"]))
        self.assertTrue(result["not_checked"])

    def test_missing_curves_and_times_is_refused(self):
        result = cc.calculate({"kind": "selectivity", "fault_current_a": 100,
                               "upstream": {"rating_a": 32},
                               "downstream": {"rating_a": 16}})
        self.assertEqual(result["refusals"][0]["required_table"], "trip_curves")


class HonestyTests(unittest.TestCase):
    """То, без чего модуль нельзя было бы считать честным."""

    def test_no_normative_tables_are_built_in(self):
        """Таблица внутри модуля сделала бы отказ невозможным."""
        self.assertNotIn("ampacity", cc.__dict__)
        self.assertNotIn("PUE", cc.__dict__)
        self.assertNotIn("ПУЭ", cc.__dict__)
        self.assertTrue(cc.RESISTIVITY_OHM_MM2_PER_M)

    def test_resistivity_is_declared_as_physical_property(self):
        """Удельное сопротивление — свойство металла, а не нормативная таблица."""
        joined = " ".join(cc.LIMITS)
        self.assertIn("физическое свойство", joined)

    def test_refusal_instead_of_default_section(self):
        """Без таблицы модуль обязан молчать, а не возвращать «4 мм²»."""
        result = cc.calculate({"kind": "cable_size", "current_a": 25})
        self.assertFalse(result["ok"])
        self.assertEqual(result["result"], {})

    def test_module_does_not_import_com(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "cabinet_calc.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("win32com", source)
        self.assertNotIn("pythoncom", source)


if __name__ == "__main__":
    unittest.main()
