"""Адаптеры доменного слоя щита к реестру действий моста.

Оба действия — чистые проверки: они не открывают документ, не требуют
активного КОМПАСа и ничего не пишут. Аргумент ``session`` принимается ради
единообразия с остальными действиями реестра и не используется.
"""

from __future__ import annotations

from typing import Any, Dict

from protocol import ok
from cabinet_domain import check_spec
from cabinet_layout import check_layout
from cabinet_build import build_layout
from cabinet_materialize import build_plan
from cabinet_exec import execute_plan
from cabinet_reconcile import reconcile
from cabinet_calc import calculate
from cabinet_catalog import search as catalog_search
from cabinet_spec import build as build_spec
from cabinet_schematic import build as build_schematic


def cabinet_spec_check(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Проверить состав и секционирование щита (НКУ/АСУ ТП)."""
    return ok("cabinet.spec_check", check_spec(params))


def cabinet_layout_check(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Проверить раскладку монтажной панели в миллиметрах."""
    return ok("cabinet.layout_check", check_layout(params))


def cabinet_layout_build(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Рассчитать раскладку монтажной панели: координаты, ряды, DIN-рейка."""
    return ok("cabinet.layout_build", build_layout(params))


def cabinet_materialize_plan(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """План материализации раскладки: последовательность вызовов моста."""
    return ok("cabinet.materialize_plan", build_plan(params))


def cabinet_materialize_apply(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Исполнить план материализации. Без явного apply=true только показывает его."""
    if not params.get("apply"):
        plan = build_plan(params)
        return ok("cabinet.materialize_apply", {
            "applied": False,
            "plan": plan,
            "note": "Ничего не выполнено: нужен явный apply=true.",
        })
    return ok("cabinet.materialize_apply", execute_plan(session, params))


def cabinet_calc(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Электрорасчёт щита: считает движок, нормативные таблицы передаёт caller."""
    return ok("cabinet.calc", calculate(params))


def cabinet_catalog_search(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Подбор оборудования щита по каталогу: габарит сразу для раскладки."""
    return ok("cabinet.catalog_search", catalog_search(params))


def cabinet_spec_build(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Спецификация, маркировка и шильды щита из раскладки."""
    return ok("cabinet.spec", build_spec(params))


def cabinet_schematic_build(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Схема щита как модель данных: зажимы, цепи, таблица соединений, проверки."""
    return ok("cabinet.schematic", build_schematic(params))


def cabinet_bom_reconcile(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Сверить ожидаемый состав щита с перечнем, прочитанным из КОМПАС."""
    return ok("cabinet.bom_reconcile", reconcile(params))
