"""Волна 16: схема щита как модель данных.

Главное, что проверяется: **схему нельзя вывести из раскладки**. Раскладка
знает координаты, но не электрические связи — поэтому попытка получить зажимы
из одной раскладки обязана отказать, а не угадать.

Второе: что в схеме цитата нормы, а что — вывод. Графы таблицы соединений
взяты из ГОСТ 2.702-2011 буквально. Системы нумерации цепей в стандарте нет —
проверено по тексту, слова «маркировка» там не встречается, — поэтому
обозначение цепи либо приносит вызывающая сторона, либо оно помечается
``derived``. Число зажимов тоже расчётное: производитель публикует число
полюсов, но не число зажимов и не распиновку.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_schematic as sch  # noqa: E402


def model(**extra):
    """Двухполюсный ввод, одна групповая линия, клемма N."""
    payload = {
        "devices": [
            {"ref": "QF1", "kind": "breaker", "poles": 1},
            {"ref": "QF2", "kind": "breaker", "poles": 1},
            {"ref": "XT1", "kind": "terminal"},
        ],
        "nets": [
            {"id": "L_in", "designation": "L (ввод)", "kind": "phase",
             "external_address": "ввод"},
            {"id": "L_QF2", "designation": "L после QF1", "kind": "phase"},
            {"id": "N", "designation": "N", "kind": "neutral"},
        ],
        "connections": [
            {"net": "L_in", "device": "QF1", "terminal": 1},
            {"net": "L_QF2", "device": "QF1", "terminal": 2},
            {"net": "L_QF2", "device": "QF2", "terminal": 1},
            {"net": "N", "device": "QF2", "terminal": 2},
            {"net": "N", "device": "XT1", "terminal": 1},
        ],
    }
    payload.update(extra)
    return payload


def codes(result):
    return [i["code"] for i in result["result"]["issues"]]


class TerminalTests(unittest.TestCase):
    """Зажимы: откуда число и почему оно расчётное."""

    def test_pole_count_gives_two_terminals_per_pole(self):
        rows, reason = sch.terminals_of({"ref": "QF1", "kind": "breaker", "poles": 2})
        self.assertIsNone(reason)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["derived"] for r in rows),
                        "число зажимов расчётное, а не паспортное")

    def test_explicit_terminal_count_is_not_derived(self):
        rows, _ = sch.terminals_of({"ref": "QF1", "kind": "breaker",
                                    "poles": 2, "terminals": 6})
        self.assertEqual(len(rows), 6)
        self.assertFalse(rows[0]["derived"],
                         "заданное вызывающей стороной число зажимов не derived")

    def test_terminal_block_and_psu_have_fixed_counts(self):
        self.assertEqual(len(sch.terminals_of({"ref": "XT1", "kind": "terminal"})[0]),
                         sch.TERMINALS_OF_TERMINAL_BLOCK)
        self.assertEqual(len(sch.terminals_of({"ref": "G1", "kind": "psu"})[0]),
                         sch.TERMINALS_OF_PSU)

    def test_no_pole_count_refuses_instead_of_guessing(self):
        rows, reason = sch.terminals_of({"ref": "QF9", "kind": "breaker"})
        self.assertIsNone(rows)
        self.assertEqual(reason, "no_terminals")

    def test_terminals_are_numbered_from_one(self):
        rows, _ = sch.terminals_of({"ref": "QF1", "kind": "breaker", "poles": 1})
        self.assertEqual([r["number"] for r in rows], [1, 2])

    def test_empty_devices_is_a_refusal(self):
        result = sch.build_terminals({})
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "no_devices")

    def test_device_without_terminals_goes_to_unverified(self):
        result = sch.build_terminals({"devices": [
            {"ref": "QF1", "kind": "breaker", "poles": 1},
            {"ref": "QF9", "kind": "breaker"},
        ]})
        self.assertEqual(result["result"]["terminal_count"], 2)
        self.assertEqual(result["result"]["unverified"][0]["ref"], "QF9")
        self.assertEqual(result["result"]["unverified"][0]["reason"], "no_terminals")


class LayoutIsNotASchematicTests(unittest.TestCase):
    """Раскладка знает координаты, но не связи: схему из неё не вывести."""

    def test_layout_elements_give_no_terminals(self):
        elements = [{"lib_key": "breaker_1p", "x_mm": 0.0, "y_mm": 120.0,
                     "w_mm": 18.0, "h_mm": 90.0}]
        result = sch.build_terminals({"devices": elements})
        self.assertEqual(result["result"]["terminal_count"], 0)
        self.assertTrue(result["result"]["unverified"])

    def test_no_connections_means_no_nets(self):
        result = sch.build_nets({"devices": model()["devices"], "nets": []})
        self.assertEqual(result["result"]["net_count"], 0)
        self.assertEqual(result["result"]["dangling"], [])


class NetTests(unittest.TestCase):

    def test_net_is_closed_at_two_terminals(self):
        result = sch.build_nets(model())
        nets = {n["id"]: n for n in result["result"]["nets"]}
        self.assertTrue(nets["L_QF2"]["closed"])
        self.assertFalse(nets["L_in"]["closed"])
        self.assertEqual(nets["L_QF2"]["terminal_count"], 2)

    def test_connection_to_unknown_net_is_dangling(self):
        payload = model()
        payload["connections"].append({"net": "X7", "device": "QF1", "terminal": 1})
        result = sch.build_nets(payload)
        self.assertEqual(result["result"]["dangling"][0]["net"], "X7")
        self.assertEqual(result["result"]["dangling"][0]["reason"], "unknown_net")

    def test_nets_are_sorted_by_id(self):
        ids = [n["id"] for n in sch.build_nets(model())["result"]["nets"]]
        self.assertEqual(ids, sorted(ids))


class ConnectionTableTests(unittest.TestCase):
    """Таблица соединений: графы — цитата из ГОСТ 2.702-2011."""

    def test_columns_are_the_standard_ones(self):
        result = sch.build_table(model())
        self.assertEqual(result["result"]["columns"],
                         ["Конт.", "Адрес", "Цепь", "Адрес внешний"])
        self.assertIn("2.702", result["result"]["columns_source"])

    def test_provenance_names_the_standard(self):
        result = sch.build_table(model())
        self.assertIn("2.702-2011", result["provenance"][0]["source"])

    def test_table_is_built_for_connectors_only(self):
        result = sch.build_table(model())
        rows = result["result"]["rows"]
        self.assertEqual(sorted({r["connector"] for r in rows}), ["XT1"],
                         "таблица соединений заполняется на соединитель")

    def test_contacts_ascend(self):
        rows = sch.build_table(model())["result"]["rows"]
        contacts = [(r["connector"], r["contact"]) for r in rows]
        self.assertEqual(contacts, sorted(contacts))
        self.assertEqual([r["contact"] for r in rows], [1, 2])

    def test_address_is_the_circuit_designation(self):
        rows = {r["contact"]: r for r in sch.build_table(model())["result"]["rows"]}
        self.assertEqual(rows[1]["address"], "N")

    def test_unconnected_contact_has_no_address(self):
        payload = model()
        payload["connections"] = [c for c in payload["connections"]
                                  if c["device"] != "XT1"]
        rows = sch.build_table(payload)["result"]["rows"]
        self.assertIsNone(rows[0]["address"])


class CheckTests(unittest.TestCase):

    def test_correct_model_has_no_errors(self):
        result = sch.check(model())
        self.assertEqual(result["result"]["error_count"], 0)

    def test_duplicate_designation(self):
        payload = model()
        payload["devices"].append({"ref": "QF1", "kind": "breaker", "poles": 1})
        self.assertIn("duplicate_designation", codes(sch.check(payload)))

    def test_missing_designation(self):
        payload = model()
        payload["devices"].append({"kind": "breaker", "poles": 1})
        self.assertIn("no_designation", codes(sch.check(payload)))

    def test_unknown_device(self):
        payload = model()
        payload["connections"].append(
            {"net": "N", "device": "QF99", "terminal": 1})
        self.assertIn("unknown_device", codes(sch.check(payload)))

    def test_open_net(self):
        payload = model()
        payload["nets"].append({"id": "Lonely", "designation": "Lonely",
                                "kind": "phase"})
        payload["connections"].append({"net": "Lonely", "device": "QF1",
                                       "terminal": 1})
        self.assertIn("open_net", codes(sch.check(payload)))

    def test_external_address_closes_the_circuit(self):
        """Цепь с внешним адресом замкнута на внешний мир — один зажим не ошибка."""
        payload = model()
        payload["nets"].append({"id": "PE", "designation": "PE",
                                "kind": "pe", "external_address": "шина PE"})
        payload["connections"].append({"net": "PE", "device": "XT1", "terminal": 2})
        self.assertNotIn("open_net", codes(sch.check(payload)))

    def test_unconnected_device(self):
        payload = model()
        payload["devices"].append({"ref": "KM1", "kind": "contactor", "poles": 2})
        codes_ = codes(sch.check(payload))
        self.assertIn("unconnected_device", codes_)
        issue = next(i for i in sch.check(payload)["result"]["issues"]
                     if i["code"] == "unconnected_device")
        self.assertEqual(issue["severity"], "warn")

    def test_phase_circuit_without_protection(self):
        payload = model()
        payload["devices"] = [d for d in payload["devices"]
                              if d["ref"] not in ("QF1", "QF2")]
        payload["connections"] = [c for c in payload["connections"]
                                  if c["device"] not in ("QF1", "QF2")]
        self.assertIn("unprotected_net", codes(sch.check(payload)))

    def test_dangling_connection_is_reported(self):
        payload = model()
        payload["connections"].append({"net": "X7", "device": "QF1", "terminal": 1})
        self.assertIn("unknown_net", codes(sch.check(payload)))

    def test_errors_sort_before_warnings(self):
        payload = model()
        payload["devices"].append({"ref": "KM1", "kind": "contactor", "poles": 2})
        payload["devices"].append({"kind": "breaker", "poles": 1})
        issues = sch.check(payload)["result"]["issues"]
        first_error = next(i for i in issues if i["severity"] == "error")
        self.assertEqual(issues.index(first_error), 0)


class TypicalCircuitTests(unittest.TestCase):
    """Типовая цепь: шаблон составлен, обозначения помечены derived."""

    def test_template_passes_its_own_check(self):
        result = sch.build({"kind": "typical", "incoming": "QF1", "phases": "L",
                            "group": [{"ref": "QF2", "kind": "breaker"},
                                      {"ref": "QF3", "kind": "rcd", "poles": 2}]})
        self.assertEqual(result["verdict"], "computed")
        self.assertEqual(result["result"]["check"]["error_count"], 0)

    def test_designations_are_derived(self):
        result = sch.build({"kind": "typical", "group": [{"ref": "QF2"}]})
        self.assertTrue(all(n.get("derived") for n in result["result"]["nets"]),
                        "системы нумерации цепей в ГОСТ 2.702-2011 нет")

    def test_chain_is_incoming_then_group(self):
        result = sch.build({"kind": "typical", "incoming": "QF1", "phases": "L",
                            "group": [{"ref": "QF2"}]})
        nets = result["result"]["nets"]
        self.assertEqual(nets[0]["id"], "L_in")
        self.assertEqual(nets[0]["external_address"], "ввод")
        self.assertEqual(nets[1]["id"], "L_after_QF1")
        self.assertEqual(nets[2]["id"], "L_QF2")

    def test_group_member_reference_defaults(self):
        result = sch.build({"kind": "typical", "group": [{}]})
        refs = [d["ref"] for d in result["result"]["devices"]]
        self.assertEqual(refs, ["QF1", "QF2"])

    def test_template_output_feeds_the_model(self):
        """Шаблон — это модель: её можно подать в kind=build."""
        template = sch.build({"kind": "typical", "group": [{"ref": "QF2"}]})
        payload = dict(template["result"])
        payload["kind"] = "build"
        assembled = sch.build(payload)
        self.assertEqual(assembled["verdict"], "computed")
        self.assertGreater(assembled["result"]["terminals"]["terminal_count"], 0)
        self.assertEqual(assembled["result"]["check"]["error_count"], 0)


class DispatchTests(unittest.TestCase):

    def test_build_assembles_everything(self):
        result = sch.build({"kind": "build", **model()})
        self.assertEqual(result["verdict"], "computed")
        for key in ("terminals", "nets", "table", "check"):
            self.assertIn(key, result["result"])

    def test_build_is_the_default(self):
        self.assertEqual(sch.build(model())["kind"], "build")

    def test_unknown_kind_is_refused(self):
        result = sch.build({"kind": "draw"})
        self.assertEqual(result["verdict"], "refused")
        self.assertEqual(result["refusals"][0]["code"], "unknown_kind")
        self.assertIn("build", result["refusals"][0]["known_kinds"])

    def test_limits_are_declared(self):
        self.assertTrue(sch.LIMITS)
        joined = " ".join(sch.LIMITS)
        self.assertIn("2.702", joined)
        self.assertIn("derived", joined)

    def test_module_does_not_import_com(self):
        source = open(sch.__file__, encoding="utf-8").read()
        self.assertNotIn("win32com", source)
        self.assertNotIn("pythoncom", source)


class SurfaceTests(unittest.TestCase):
    """Поверхность выросла на один инструмент."""

    def test_tool_registered(self):
        import tools_catalog
        self.assertIn("kompas_cabinet_schematic", tools_catalog.TOOL_INDEX)
        self.assertEqual(
            tools_catalog.ACTION_MAP["kompas_cabinet_schematic"][0],
            "cabinet.schematic")

    def test_tool_count(self):
        import tools_catalog
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_schema_declares_the_kinds(self):
        import tools_catalog
        tool = tools_catalog.TOOL_INDEX["kompas_cabinet_schematic"]
        kinds = tool["inputSchema"]["properties"]["kind"]["enum"]
        self.assertEqual(set(kinds), set(sch.KINDS))

    def test_adapter_returns_the_model(self):
        """Адаптер реестра отдаёт ту же модель, что и чистый модуль."""
        import cabinet_tools
        import tools_catalog
        action = tools_catalog.ACTION_MAP["kompas_cabinet_schematic"][0]
        self.assertEqual(action, "cabinet.schematic")
        answer = cabinet_tools.cabinet_schematic_build(None, {"kind": "check",
                                                              **model()})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"]["result"]["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
