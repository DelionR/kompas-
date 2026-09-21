"""Исполнение плана материализации — тонкий слой над инструментами моста.

План строит ``cabinet_materialize`` (чистые функции) и не знает ни одного
числового индекса: после вставки компонента его номер в дереве сборки известен
только из ответа КОМПАСа. Этот модуль как раз и занимается тем, чтобы взять
номер из ответа предыдущего шага и подставить в следующий.

Модуль **не импортирует COM на уровне модуля** — otherwise его нельзя проверить
без КОМПАСа. Функции моста подтягиваются лениво, внутри
``_default_executor()``, а тесты подставляют свой исполнитель.

Что здесь принципиально не делается:

- не угадывается индекс экземпляра. Если ответ вставки не даёт числа, шаг
  уходит в ``skipped``, а не выполняется «наудачу»;
- не исполняются шаги с ``automated=False``: у них другой контракт (не
  ``session``), они остаются рекомендацией оператору и попадают в ``deferred``;
- не подавляются ошибки: падение критичного шага прерывает выполнение, потому что
  продолжать по непостроенной геометрии бессмысленно.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from cabinet_materialize import build_plan


# --------------------------------------------------------------------------
# резолв селекторов
# --------------------------------------------------------------------------

def component_index(result: Any) -> Optional[int]:
    """Номер последнего вставленного компонента из ответа моста.

    ``assembly_insert_component`` отдаёт ``result.readback.component_count``.
    Нумерация с нуля — последний вставленный это ``count - 1``. Если ответа нет
    или он не такой, возвращаем ``None``: угадывать номер нельзя.

    Нумерация подтверждена только чтением кода, не живым КОМПАСом — это
    отражено в ``limits``.
    """
    if not isinstance(result, dict):
        return None
    readback = (result.get("result") or {}).get("readback") or {}
    count = readback.get("component_count")
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return count - 1 if count > 0 else None


def _substitute(value: Any, results: Dict[int, Any]) -> Any:
    """Подставить в параметры значения из ответов предыдущих шагов."""
    if isinstance(value, dict):
        if "from_step" in value and "field" in value:
            step = value.get("from_step")
            field = str(value.get("field") or "")
            source = results.get(step if isinstance(step, int) else -1)
            if source is None:
                return {"unresolved": True, "from_step": step, "field": field}
            if field == "component_index":
                index = component_index(source)
                # None здесь — не «нулевой индекс», а «индекс неизвестен».
                # Вызвать шаг с selector=None значило бы обратиться не к тому
                # экземпляру, поэтому шаг помечается неподставленным.
                if index is None:
                    return {"unresolved": True, "from_step": step, "field": field}
                return index
            return {"unresolved": True, "from_step": step, "field": field}
        return {k: _substitute(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, results) for v in value]
    return value


def _unresolved_refs(params: Dict[str, Any]) -> List[str]:
    """Найти параметры, которые не удалось подставить."""
    found: List[str] = []

    def walk(node: Any, path: str):
        if isinstance(node, dict):
            if node.get("unresolved"):
                found.append(path or "<root>")
                return
            for key, value in node.items():
                walk(value, "%s.%s" % (path, key) if path else key)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, "%s[%d]" % (path, i))

    walk(params, "")
    return found


# --------------------------------------------------------------------------
# исполнитель
# --------------------------------------------------------------------------

def _default_executor() -> Dict[str, Any]:
    """Ленивые ссылки на функции моста. Импорт здесь, а не в шапке файла."""
    from part_create_tools import part_create
    from primitive_tools import part_add_primitive
    from assembly_insert_tools import assembly_insert_component
    from component_transform_tools import component_transform
    from component_pattern_tools import component_pattern_linear
    from component_properties_tools import component_properties_set

    def call_part_create(session, **params):
        return part_create(session, params["filename"])

    def call_primitive(session, **params):
        return part_add_primitive(
            session, params["filename"], params["kind"],
            params["width_mm"], params["height_mm"], params["depth_mm"])

    def call_insert(session, **params):
        return assembly_insert_component(
            session, params["assembly_filename"], params["component_filename"],
            params.get("allow_duplicate", False))

    def call_transform(session, **params):
        return component_transform(
            session, params["assembly_filename"], params["selector"],
            params["origin"], params["axis_x"], params["axis_y"],
            params["axis_z"], params.get("mode", "absolute"))

    def call_pattern(session, **params):
        return component_pattern_linear(
            session, params["assembly_filename"], params["source_selector"],
            params["direction"], params["spacing"], params["count"])

    def call_properties(session, **params):
        return component_properties_set(
            session, params["assembly_filename"], params["selector"],
            params.get("designation"), params.get("name"))

    return {
        "part.create": call_part_create,
        "part.primitive": call_primitive,
        "assembly.insert_component": call_insert,
        "component.transform": call_transform,
        "component.pattern_linear": call_pattern,
        "component.properties_set": call_properties,
    }


def execute_plan(session: Any, params: Dict[str, Any],
                 executor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Исполнить план. Возвращает отчёт, не бросает исключения наружу."""
    given = params.get("plan")
    plan = given if isinstance(given, dict) else build_plan(params)

    steps = plan.get("steps") or []
    executed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    results: Dict[int, Any] = {}

    if executor is None:
        executor = _default_executor()

    for step in steps:
        seq = step.get("seq")
        action = str(step.get("action") or "")
        if not step.get("automated", True):
            deferred.append({"seq": seq, "tool": step.get("tool"),
                             "action": action, "why": step.get("why")})
            continue

        handler = executor.get(action)
        if handler is None:
            skipped.append({"seq": seq, "action": action,
                            "reason": "no_executor_for_action"})
            continue

        resolved = _substitute(step.get("params") or {}, results)
        gaps = _unresolved_refs(resolved)
        if gaps:
            skipped.append({"seq": seq, "action": action,
                            "reason": "selector_unresolved",
                            "detail": "%s: индекс экземпляра неизвестен" % ", ".join(gaps)})
            if step.get("critical", True):
                break
            continue

        try:
            outcome = handler(session, **resolved)
        except Exception as exc:                       # ошибку не прячем
            row = {"seq": seq, "action": action,
                   "error": "%s: %s" % (type(exc).__name__, exc)}
            failed.append(row)
            if step.get("critical", True):
                break
            continue

        results[seq] = outcome
        executed.append({"seq": seq, "action": action, "stage": step.get("stage")})

    return {
        "ok": not failed and not skipped,
        "applied": bool(executed),
        "executed": executed,
        "skipped": skipped,
        "failed": failed,
        "deferred": deferred,
        "counts": {"executed": len(executed), "skipped": len(skipped),
                   "failed": len(failed), "deferred": len(deferred),
                   "planned": len(steps)},
        "plan_summary": plan.get("summary"),
        "unresolved": plan.get("unresolved") or [],
        "limits": plan.get("limits") or [],
    }
