"""Единицы чертежа AutoCAD и перевод миллиметров в единицы документа.

Зачем этот модуль
-----------------
Щитовой домен считает в миллиметрах — это зафиксировано в проекте и не
обсуждается. А AutoCAD **не обязан** считать в миллиметрах: документ может
быть в дюймах, в метрах или вовсе без единиц. Если просто записать в чертёж
число 600, не зная единицы, получим либо 600 дюймов, либо 600 «ничего».

Поэтому масштаб выясняется до всякой записи, и если его узнать нельзя —
отказ, а не правдоподобная подстановка.

Коды ``INSUNITS``
-----------------
Значение системной переменной ``INSUNITS`` — единственный надёжный источник:
это масштаб, который AutoCAD сам применяет при вставке и при обмене блоками.
В модуль включены только те коды, в которых мы уверены; неизвестный код не
трактуется как миллиметры, а приводит к отказу ``unknown_units``.

``INSUNITS = 0`` — «без единиц». Это не миллиметры, а отсутствие договора о
масштабе: такой документ тоже отказ, потому что перевести в него миллиметры
нечем.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# INSUNITS → (название, сколько миллиметров в одной единице).
UNITS_TO_MM: Dict[int, Tuple[str, float]] = {
    1: ("дюймы", 25.4),
    2: ("футы", 304.8),
    4: ("миллиметры", 1.0),
    5: ("сантиметры", 10.0),
    6: ("метры", 1000.0),
}

UNITLESS = 0

LIMITS = [
    "Масштаб берётся только из INSUNITS: других надёжных источников у "
    "документа AutoCAD нет.",
    "INSUNITS = 0 означает «без единиц», а не миллиметры: такой документ "
    "отказ, потому что масштаб неизвестен.",
    "Неизвестный код единиц не трактуется как миллиметры.",
]


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def describe_units(insunits: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Описать единицы документа по коду ``INSUNITS``.

    Возвращает ``(None, причина)``, если масштаб вывести не из чего.
    """
    code = _as_int(insunits)
    if code is None:
        return None, "no_insunits"
    if code == UNITLESS:
        return None, "unitless_document"
    if code not in UNITS_TO_MM:
        return None, "unknown_units"
    name, mm_per_unit = UNITS_TO_MM[code]
    return {
        "insunits": code,
        "units_name": name,
        "mm_per_unit": mm_per_unit,
        "units_per_mm": 1.0 / mm_per_unit,
        "is_metric": code in (4, 5, 6),
    }, None


def mm_to_units(value_mm: Any, units: Dict[str, Any]) -> Optional[float]:
    """Перевести миллиметры в единицы документа."""
    if not isinstance(units, dict):
        return None
    scale = units.get("units_per_mm")
    if not isinstance(scale, (int, float)):
        return None
    if isinstance(value_mm, bool) or value_mm is None:
        return None
    if not isinstance(value_mm, (int, float)):
        return None
    return float(value_mm) * float(scale)


def units_to_mm(value: Any, units: Dict[str, Any]) -> Optional[float]:
    """Перевести единицы документа в миллиметры (для чтения из чертежа)."""
    if not isinstance(units, dict):
        return None
    factor = units.get("mm_per_unit")
    if not isinstance(factor, (int, float)):
        return None
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    return float(value) * float(factor)
