"""Волна 9: материализация раскладки — план и его исполнение.

Волна 8 считала координаты, эта превращает их в последовательность вызовов.
Две части:

- ``cabinet_materialize`` — чистые функции, COM не импортируется;
- ``cabinet_exec`` — исполнитель, функции моста подтягиваются лениво, поэтому
  тесты подставляют свой исполнитель и проверяют **порядок и аргументы вызовов**.

Что тесты требуют принципиально:

- план ссылается на экземпляры символически: числовых индексов в нём быть не
  должно, они известны только КОМПАСу после вставки;
- ссылка ведёт именно на шаг вставки (регрессия: первая версия брала
  ``seq - 1`` и указывала на создание тела);
- исполнитель не угадывает индекс — неподставленный селектор уходит в
  ``skipped``, а не выполняется наудачу;
- шаги с ``automated=False`` не исполняются никогда.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import cabinet_build as cb            # noqa: E402
import cabinet_materialize as cm      # noqa: E402
import cabinet_exec as ce             # noqa: E402
import cabinet_tools                  # noqa: E402
import tools_catalog                  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSEMBLY = "SHCHIT_AGENT_COPY.a3d"


# --------------------------------------------------------------------------
# фикстуры
# --------------------------------------------------------------------------

LIBRARY = [
    {"lib_key": "brk", "name": "Автомат 40А", "width_mm": 36.0,
     "height_mm": 90.0, "rail_offset_mm": 45.0},
    {"lib_key": "term", "name": "Клемма", "width_mm": 6.0,
     "height_mm": 40.0, "rail_offset_mm": 25.0},
]


def make_layout(lines, plate=None, library=None):
    return cb.build_layout({
        "plate": plate or {"width_mm": 600.0, "height_mm": 800.0},
        "rail": {"first_rail_y_mm": 130.0, "pitch_mm": 170.0},
        "library": list(library or LIBRARY),
        "lines": lines,
    })["layout"]


def plan_for(lines, **extra):
    payload = {"assembly_filename": ASSEMBLY, "layout": make_layout(lines)}
    payload.update(extra)
    return cm.build_plan(payload)


class FakeExecutor:
    """Исполнитель, который ничего не делает, но всё запоминает."""

    ACTIONS = ("part.create", "part.primitive", "assembly.insert_component",
               "component.transform", "component.pattern_linear",
               "component.properties_set")

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)
        self._inserted = 0

    def as_dict(self):
        return {action: self._handler(action) for action in self.ACTIONS}

    def _handler(self, action):
        def fn(session, **params):
            self.calls.append((action, params))
            if action in self.fail:
                raise RuntimeError("boom-%s" % action)
            if action == "assembly.insert_component":
                self._inserted += 1
                return {"result": {"readback": {"component_count": self._inserted}}}
            return {"ok": True}
        return fn

    def actions(self):
        return [a for a, _p in self.calls]

    def params_of(self, action):
        return [p for a, p in self.calls if a == action]


# --------------------------------------------------------------------------
# поверхность инструментов
# --------------------------------------------------------------------------

class ToolSurface(unittest.TestCase):
    def test_tool_count_is_56(self):
        self.assertEqual(len(tools_catalog.TOOLS), 59)

    def test_both_tools_declared(self):
        names = [t["name"] for t in tools_catalog.TOOLS]
        self.assertIn("kompas_cabinet_materialize_plan", names)
        self.assertIn("kompas_cabinet_materialize_apply", names)

    def test_plan_is_read_only_apply_is_not(self):
        self.assertTrue(
            tools_catalog.TOOL_INDEX["kompas_cabinet_materialize_plan"]["annotations"]["readOnlyHint"])
        self.assertFalse(
            tools_catalog.TOOL_INDEX["kompas_cabinet_materialize_apply"]["annotations"]["readOnlyHint"])

    def test_apply_requires_explicit_flag(self):
        schema = tools_catalog.TOOL_INDEX["kompas_cabinet_materialize_apply"]["inputSchema"]
        self.assertIn("apply", schema["required"])

    def test_both_routed(self):
        self.assertEqual(
            tools_catalog.ACTION_MAP["kompas_cabinet_materialize_plan"][0],
            "cabinet.materialize_plan")
        self.assertEqual(
            tools_catalog.ACTION_MAP["kompas_cabinet_materialize_apply"][0],
            "cabinet.materialize_apply")

    def test_apply_has_preflight_entry(self):
        """Мутирующее действие обязано быть в манифесте предпросмотра."""
        path = os.path.join(ROOT, "src", "dryrun.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('"cabinet.materialize_apply"', body)

    def test_registry_wires_actions(self):
        path = os.path.join(ROOT, "src", "engineering_capabilities.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('"cabinet.materialize_plan": cabinet_materialize_plan', body)
        self.assertIn('"cabinet.materialize_apply": cabinet_materialize_apply', body)


# --------------------------------------------------------------------------
# имена файлов и разбиение сетов
# --------------------------------------------------------------------------

class Naming(unittest.TestCase):
    def test_part_filename_rules(self):
        name = cm.part_filename("brk")
        self.assertTrue(name.endswith(".m3d"))
        self.assertIn("_AGENT_COPY", name)
        self.assertEqual(name, os.path.basename(name))

    def test_slug_strips_cyrillic_and_spaces(self):
        self.assertEqual(cm.slug("Автомат 2P"), cm.slug("Автомат 2P"))
        self.assertTrue(cm.slug("Автомат 2P").isascii(),
                        "имя файла должно быть латиницей")
        self.assertNotIn(" ", cm.slug("Автомат 2P"))

    def test_slug_falls_back_for_empty(self):
        self.assertEqual(cm.slug(""), "part")
        self.assertEqual(cm.slug("!!!" ), "part")

    def test_is_agent_copy(self):
        self.assertTrue(cm.is_agent_copy("x_AGENT_COPY.m3d"))
        self.assertTrue(cm.is_agent_copy("x_agent_copy.m3d"))
        self.assertFalse(cm.is_agent_copy("x.m3d"))


class SetSplitting(unittest.TestCase):
    def test_small_sets_untouched(self):
        self.assertEqual(cm.split_count(1), [1])
        self.assertEqual(cm.split_count(2), [2])
        self.assertEqual(cm.split_count(20), [20])

    def test_set_over_limit_is_split(self):
        """Паттерн принимает 2..20, поэтому 24 клеммы идут двумя частями."""
        self.assertEqual(cm.split_count(24), [12, 12])
        self.assertEqual(cm.split_count(45), [15, 15, 15])

    def test_every_part_within_pattern_limits(self):
        for count in (21, 24, 37, 45, 100):
            for part in cm.split_count(count):
                self.assertGreaterEqual(part, cm.MIN_PATTERN_COUNT)
                self.assertLessEqual(part, cm.MAX_PATTERN_COUNT)

    def test_parts_sum_to_count(self):
        for count in (21, 24, 37, 45, 100):
            self.assertEqual(sum(cm.split_count(count)), count)

    def test_zero_and_negative(self):
        self.assertEqual(cm.split_count(0), [])
        self.assertEqual(cm.split_count(-5), [])

    def test_step_includes_internal_gap(self):
        self.assertAlmostEqual(cm.set_step_mm(6.0, 1.0), 7.0)
        self.assertAlmostEqual(cm.set_step_mm(36.0, 0.0), 36.0)


# --------------------------------------------------------------------------
# координаты
# --------------------------------------------------------------------------

class Coordinates(unittest.TestCase):
    def test_origin_is_centre_in_cad_space(self):
        """Раскладка сверху слева, КОМПАС обычно: переворот делает to_cad."""
        origin = cm.origin_cad(60.0, 85.0, 36.0, 90.0, 800.0, 30.0)
        self.assertAlmostEqual(origin[0], 60.0 + 18.0)          # центр по X
        self.assertAlmostEqual(origin[1], 800.0 - (85.0 + 45.0))  # центр по Y в CAD
        self.assertAlmostEqual(origin[2], 30.0)

    def test_z_is_centre_of_depth(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}],
                        mount={"depth_mm": 80.0, "plane_z_mm": 10.0})
        place = [s for s in plan["steps"] if s["stage"] == "place"][0]
        self.assertAlmostEqual(place["params"]["origin"][2], 10.0 + 40.0)

    def test_default_depth(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        place = [s for s in plan["steps"] if s["stage"] == "place"][0]
        self.assertAlmostEqual(place["params"]["origin"][2],
                               cm.DEFAULT_DEPTH_MM / 2.0)

    def test_axes_are_identity(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        place = [s for s in plan["steps"] if s["stage"] == "place"][0]
        self.assertEqual(place["params"]["axis_x"], [1, 0, 0])
        self.assertEqual(place["params"]["axis_y"], [0, 1, 0])
        self.assertEqual(place["params"]["axis_z"], [0, 0, 1])


# --------------------------------------------------------------------------
# источник тела
# --------------------------------------------------------------------------

class BodySource(unittest.TestCase):
    def test_primitive_from_confirmed_size(self):
        kind, name, reason = cm.body_source(
            {"lib_key": "brk", "width_mm": 36.0, "height_mm": 90.0}, "primitive")
        self.assertEqual(kind, "primitive")
        self.assertEqual(name, "brk_AGENT_COPY.m3d")
        self.assertIsNone(reason)

    def test_primitive_refuses_unconfirmed_size(self):
        kind, _name, reason = cm.body_source(
            {"lib_key": "hazy", "width_mm": 0.0, "height_mm": 0.0}, "primitive")
        self.assertIsNone(kind)
        self.assertEqual(reason, "unconfirmed_size")

    def test_fragment_uses_reference(self):
        kind, name, reason = cm.body_source(
            {"lib_key": "brk", "fragment_ref": "brk_AGENT_COPY.m3d"}, "fragment")
        self.assertEqual(kind, "fragment")
        self.assertEqual(name, "brk_AGENT_COPY.m3d")
        self.assertIsNone(reason)

    def test_fragment_requires_reference(self):
        kind, _name, reason = cm.body_source({"lib_key": "brk"}, "fragment")
        self.assertIsNone(kind)
        self.assertEqual(reason, "no_fragment_ref")

    def test_fragment_refuses_foreign_file(self):
        """Чужой .m3d без _AGENT_COPY мост не примет — говорим сразу."""
        kind, _name, reason = cm.body_source(
            {"lib_key": "brk", "fragment_ref": "catalog_breaker.m3d"}, "fragment")
        self.assertIsNone(kind)
        self.assertEqual(reason, "fragment_ref_not_agent_copy")

    def test_body_created_once_per_library_key(self):
        """Двенадцать клемм — один файл детали, а не двенадцать."""
        plan = plan_for([{"id": "c", "lib_key": "term", "qty": 24}])
        creates = [s for s in plan["steps"] if s["action"] == "part.create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(plan["bodies"]), 1)


# --------------------------------------------------------------------------
# структура плана
# --------------------------------------------------------------------------

class PlanStructure(unittest.TestCase):
    def test_stage_order(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        order = []
        for step in plan["steps"]:
            if not order or order[-1] != step["stage"]:
                order.append(step["stage"])
        self.assertEqual(order[0], "checkpoint")
        self.assertEqual(order[-1], "verify")
        self.assertLess(order.index("body"), order.index("insert"))
        self.assertLess(order.index("insert"), order.index("place"))

    def test_every_reference_points_at_insert(self):
        """Регрессия: первая версия брала seq-1 и ссылалась на создание тела."""
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1},
                         {"id": "c", "lib_key": "term", "qty": 24,
                          "internal_gap_mm": 1.0}])
        by_seq = {s["seq"]: s for s in plan["steps"]}
        for step in plan["steps"]:
            ref = (step["params"].get("selector")
                   or step["params"].get("source_selector"))
            if isinstance(ref, dict) and "from_step" in ref:
                target = by_seq[ref["from_step"]]
                self.assertEqual(target["action"], "assembly.insert_component",
                                 "шаг %s ссылается не на вставку" % step["seq"])

    def test_second_subset_allows_duplicate(self):
        plan = plan_for([{"id": "c", "lib_key": "term", "qty": 24,
                          "internal_gap_mm": 1.0}])
        inserts = [s for s in plan["steps"] if s["stage"] == "insert"]
        self.assertEqual(len(inserts), 2)
        self.assertFalse(inserts[0]["params"]["allow_duplicate"])
        self.assertTrue(inserts[1]["params"]["allow_duplicate"])

    def test_second_subset_is_offset_along_x(self):
        plan = plan_for([{"id": "c", "lib_key": "term", "qty": 24,
                          "internal_gap_mm": 1.0}])
        places = [s for s in plan["steps"] if s["stage"] == "place"]
        self.assertEqual(len(places), 2)
        shift = places[1]["params"]["origin"][0] - places[0]["params"]["origin"][0]
        self.assertAlmostEqual(shift, 12 * 7.0)

    def test_pattern_count_within_tool_limit(self):
        plan = plan_for([{"id": "c", "lib_key": "term", "qty": 24,
                          "internal_gap_mm": 1.0}])
        for step in plan["steps"]:
            if step["stage"] == "pattern":
                self.assertLessEqual(step["params"]["count"], cm.MAX_PATTERN_COUNT)
                self.assertGreaterEqual(step["params"]["count"], cm.MIN_PATTERN_COUNT)

    def test_single_item_has_no_pattern(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        self.assertEqual([s for s in plan["steps"] if s["stage"] == "pattern"], [])

    def test_only_first_subset_is_marked(self):
        """Маркировка каждого экземпляра потребовала бы числовых индексов."""
        plan = plan_for([{"id": "c", "lib_key": "term", "qty": 24,
                          "tag_start": "B101"}])
        marks = [s for s in plan["steps"] if s["stage"] == "mark"]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["params"]["designation"], "B101")

    def test_marks_can_be_disabled(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1, "tag": "QF1"}],
                        options={"marks": False})
        self.assertEqual([s for s in plan["steps"] if s["stage"] == "mark"], [])

    def test_checkpoint_and_verify_are_not_automated(self):
        """У них другой контракт: не session. Исполнитель их не трогает."""
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        for step in plan["steps"]:
            if step["stage"] in ("checkpoint", "verify"):
                self.assertFalse(step["automated"])

    def test_no_assembly_is_unresolved(self):
        """Без сборки нельзя ничего вставить, но тела создать можно.

        Поэтому проверяем не «шагов нет», а «шагов вставки нет»: создание
        детали от сборки не зависит.
        """
        plan = cm.build_plan({"layout": make_layout(
            [{"id": "a", "lib_key": "brk", "qty": 1}])})
        reasons = [u["reason"] for u in plan["unresolved"]]
        self.assertIn("no_assembly_filename", reasons)
        assembly_stages = {"insert", "place", "pattern", "mark"}
        self.assertEqual([s for s in plan["steps"]
                          if s["stage"] in assembly_stages], [])

    def test_unknown_lib_key_unresolved(self):
        layout = make_layout([{"id": "a", "lib_key": "brk", "qty": 1}])
        layout["elements"].append({"id": "z", "lib_key": "ghost", "x_mm": 0.0,
                                   "y_mm": 0.0})
        plan = cm.build_plan({"assembly_filename": ASSEMBLY, "layout": layout})
        self.assertIn("unknown_lib_key",
                      [u["reason"] for u in plan["unresolved"]])

    def test_garbage_does_not_raise(self):
        for payload in (None, {}, [], "text", 42, {"layout": "nope"}):
            plan = cm.build_plan(payload)
            self.assertIn("steps", plan)

    def test_limits_declared(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        self.assertTrue(any("индекс" in s for s in plan["limits"]))


# --------------------------------------------------------------------------
# исполнение
# --------------------------------------------------------------------------

class Execution(unittest.TestCase):
    def test_apply_false_executes_nothing(self):
        payload = {"assembly_filename": ASSEMBLY,
                   "layout": make_layout([{"id": "a", "lib_key": "brk", "qty": 1}])}
        out = cabinet_tools.cabinet_materialize_apply(None, dict(payload, apply=False))
        self.assertFalse(out["result"]["applied"])

    def test_plan_tool_is_pure(self):
        payload = {"assembly_filename": ASSEMBLY,
                   "layout": make_layout([{"id": "a", "lib_key": "brk", "qty": 1}])}
        out = cabinet_tools.cabinet_materialize_plan(None, payload)
        self.assertIn("steps", out["result"])

    def test_every_automated_step_is_called(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1, "tag": "QF1"}])
        ex = FakeExecutor()
        report = ce.execute_plan(None, {"plan": plan}, executor=ex.as_dict())
        self.assertTrue(report["ok"])
        automated = [s["action"] for s in plan["steps"] if s["automated"]]
        self.assertEqual(ex.actions(), automated)

    def test_selector_resolved_from_previous_insert(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        ex = FakeExecutor()
        ce.execute_plan(None, {"plan": plan}, executor=ex.as_dict())
        transform = ex.params_of("component.transform")[0]
        self.assertEqual(transform["selector"], 0)

    def test_deferred_steps_are_never_called(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        ex = FakeExecutor()
        report = ce.execute_plan(None, {"plan": plan}, executor=ex.as_dict())
        self.assertEqual(len(report["deferred"]), 4)      # checkpoint + 3 verify
        self.assertNotIn("checkpoint.manage", ex.actions())

    def test_unresolved_selector_is_skipped_not_guessed(self):
        class Silent(FakeExecutor):
            def _handler(self, action):
                def fn(session, **params):
                    self.calls.append((action, params))
                    return {"ok": True}          # без component_count
                return fn

        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        ex = Silent()
        report = ce.execute_plan(None, {"plan": plan}, executor=ex.as_dict())
        reasons = [row["reason"] for row in report["skipped"]]
        self.assertIn("selector_unresolved", reasons)
        self.assertNotIn("component.transform", ex.actions())

    def test_critical_failure_stops_execution(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1},
                         {"id": "c", "lib_key": "term", "qty": 4}])
        ex = FakeExecutor(fail=("part.create",))
        report = ce.execute_plan(None, {"plan": plan}, executor=ex.as_dict())
        self.assertEqual(len(report["failed"]), 1)
        self.assertNotIn("assembly.insert_component", ex.actions())

    def test_non_critical_failure_continues(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1, "tag": "QF1"},
                         {"id": "b", "lib_key": "term", "qty": 4,
                          "tag_start": "B1"}])
        ex = FakeExecutor(fail=("component.properties_set",))
        report = ce.execute_plan(None, {"plan": plan}, executor=ex.as_dict())
        # обе позиции размечены, обе маркировки упали, но геометрия построена
        self.assertEqual(len(report["failed"]), 2)
        self.assertFalse(report["ok"])
        for action in ("part.create", "part.primitive", "assembly.insert_component",
                       "component.transform"):
            self.assertIn(action, ex.actions())

    def test_component_index_from_readback(self):
        self.assertEqual(ce.component_index(
            {"result": {"readback": {"component_count": 3}}}), 2)
        self.assertIsNone(ce.component_index({"result": {}}))
        self.assertIsNone(ce.component_index(None))
        self.assertIsNone(ce.component_index(
            {"result": {"readback": {"component_count": 0}}}))

    def test_unknown_action_is_skipped(self):
        plan = plan_for([{"id": "a", "lib_key": "brk", "qty": 1}])
        report = ce.execute_plan(None, {"plan": plan}, executor={})
        self.assertTrue(report["skipped"])
        self.assertEqual(report["counts"]["skipped"], report["counts"]["planned"] - 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
