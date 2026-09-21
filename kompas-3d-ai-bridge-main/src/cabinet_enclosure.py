"""Корпуса щита: посадка оборудования и монтажная плата — чистые функции.

Корпус отличается от аппарата принципиально: аппарат **ставится на плату**, а
корпус **есть** плата. Поэтому корпус не попадает в раскладку как позиция — он
задаёт габарит, внутри которого раскладка считается.

Откуда что берётся
------------------

Габарит корпуса, число модулей, число рядов, степень защиты и материал — данные
производителя, приходят в каталоге (ETIM-характеристики открытого каталога IEK).

**Полезная ширина ряда — величина расчётная, а не паспортная.** Производитель
публикует число модульных расстояний и число рядов, но не публикует ширину ряда в
миллиметрах. Она считается как ``modules / rows × 18``, где 18 мм — модуль IEK,
подтверждённый в проекте дважды: габаритом изделия и полем числа модулей. Такое
значение помечается ``derived`` и возвращается с указанием формулы, чтобы его
нельзя было принять за паспортное.

**Габарит монтажной панели производитель не публикует вовсе.** Для корпусов с
монтажной панелью поле остаётся ``None``: подставить «панель на 20 мм меньше
корпуса» — значит выдать оценку за данные.

Противоречие в данных производителя
-----------------------------------

Если расчётная ширина ряда больше габарита корпуса, значит данные
противоположны сами себе (такое встретилось ровно один раз на 331 позиции:
ряд 324 мм при корпусе 310 мм). Корпус не молча «поправляется» — он уходит в
``unverified`` с указанием противоречия: взять его в проект нельзя, пока
производитель не объяснит, чему верить.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MODULE_WIDTH_MM = 18.0

LIMITS = [
    "Габарит корпуса, число модулей и рядов, степень защиты — данные "
    "производителя. Полезная ширина ряда считается как modules / rows × 18 мм "
    "и помечается как расчётная, а не паспортная.",
    "Габарит монтажной панели производитель не публикует: поле остаётся None, "
    "а не заполняется оценкой.",
    "Если расчётная ширина ряда больше габарита корпуса, данные производителя "
    "противоречивы: корпус уходит в unverified.",
    "Выбор корпуса по числу модулей не заменяет проверку нагрева НКУ и не "
    "проверяет глубину корпуса под конкретное оборудование.",
]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    parsed = _num(value)
    return int(parsed) if parsed is not None else default


def row_width_mm(item: Any) -> Tuple[Optional[float], Optional[str]]:
    """Расчётная ширина одного ряда в миллиметрах.

    Возвращает ``(None, причина)``, если числа модулей или рядов нет — считать
    «по типу щита» нельзя.
    """
    data = _as_dict(item)
    modules = _int(data.get("modules"))
    rows = _int(data.get("rows")) or 1
    if modules is None:
        return None, "no_modules"
    if rows < 1:
        return None, "bad_rows"
    return modules / rows * MODULE_WIDTH_MM, None


def plate_of(item: Any) -> Dict[str, Any]:
    """Монтажная плата (зона установки) из корпуса.

    Ширина — расчётная ширина ряда, помечена ``derived``. Высота остаётся
    ``None``: производитель не публикует габарит монтажной панели, а высота
    ряда зависит от оборудования, а не от корпуса.
    """
    data = _as_dict(item)
    width, reason = row_width_mm(data)
    body = _num(data.get("width_mm"))
    conflict = None
    if width is not None and body is not None and width > body:
        conflict = ("ширина ряда %.1f мм больше габарита корпуса %.1f мм: "
                    "данные производителя противоречивы" % (width, body))
    return {
        "width_mm": None if width is None else round(width, 3),
        "height_mm": None,
        "derived": True,
        "formula": "modules / rows × %.1f мм" % MODULE_WIDTH_MM,
        "modules": _int(data.get("modules")),
        "rows": _int(data.get("rows")) or 1,
        "enclosure_width_mm": body,
        "enclosure_height_mm": _num(data.get("height_mm")),
        "enclosure_depth_mm": _num(data.get("depth_mm")),
        "conflict": conflict,
        "reason": reason,
    }


def fits(item: Any, modules_needed: Any) -> Dict[str, Any]:
    """Влезает ли ``modules_needed`` модулей в корпус.

    Сравнение идёт по числу модулей, а не по миллиметрам: корпус продаётся
    модулями, и именно в них считается состав щита. Если у корпуса несколько
    рядов, модули распределяются по рядам, поэтому сравнивается общее число.
    """
    data = _as_dict(item)
    need = _int(modules_needed)
    have = _int(data.get("modules"))
    if need is None:
        return {"ok": False, "verdict": "refused", "reason": "no_modules_needed"}
    if have is None:
        return {"ok": False, "verdict": "refused", "reason": "no_modules"}
    plate = plate_of(data)
    return {
        "ok": have >= need and plate["conflict"] is None,
        "verdict": "computed" if plate["conflict"] is None else "unverified",
        "modules_needed": need,
        "modules_available": have,
        "free_modules": have - need,
        "rows": plate["rows"],
        "row_width_mm": plate["width_mm"],
        "conflict": plate["conflict"],
    }


def select(payload: Any) -> Dict[str, Any]:
    """Подобрать корпуса под нужное число модулей.

    Отбор по числу модулей, степень защиты и число рядов — данные
    производителя; посадка проверяется функцией :func:`fits`.
    """
    data = _as_dict(payload)
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    need = _int(data.get("modules"))
    ip = _text(data.get("ip")).upper()
    rows = _int(data.get("rows"))
    limit = _int(data.get("limit"), 20) or 20

    notes: List[str] = []
    refusals: List[Dict[str, Any]] = []
    if need is None:
        refusals.append({"code": "no_modules",
                         "message": "Не задано число модулей (modules)."})
        return {"ok": False, "verdict": "refused", "result": {},
                "refusals": refusals, "notes": notes, "limits": list(LIMITS)}
    if not items:
        refusals.append({"code": "no_catalog",
                         "message": "Каталог корпусов пуст."})
        return {"ok": False, "verdict": "refused", "result": {},
                "refusals": refusals, "notes": notes, "limits": list(LIMITS)}

    picked: List[Dict[str, Any]] = []
    for item in items:
        if ip and _text(item.get("ip")).upper() != ip:
            continue
        if rows is not None and (_int(item.get("rows")) or 1) != rows:
            continue
        fit = fits(item, need)
        if not fit["ok"]:
            continue
        picked.append({
            "article": _text(item.get("article")) or None,
            "name": _text(item.get("name")) or None,
            "manufacturer": _text(item.get("manufacturer")) or None,
            "series": _text(item.get("series")) or None,
            "ip": _text(item.get("ip")) or None,
            "modules": fit["modules_available"],
            "rows": fit["rows"],
            "free_modules": fit["free_modules"],
            "width_mm": _num(item.get("width_mm")),
            "height_mm": _num(item.get("height_mm")),
            "depth_mm": _num(item.get("depth_mm")),
            "plate": plate_of(item),
        })

    picked.sort(key=lambda entry: (entry["free_modules"], entry["width_mm"] or 0.0))
    notes.append("отбор по числу модулей: нужно %d, подходит %d корпусов"
                 % (need, len(picked)))
    if ip:
        notes.append("степень защиты отфильтрована: %s" % ip)
    return {
        "ok": True,
        "verdict": "computed",
        "result": {
            "modules": need,
            "count": len(picked),
            "items": picked[:limit],
            "truncated": len(picked) > limit,
        },
        "refusals": refusals,
        "notes": notes,
        "limits": list(LIMITS),
    }
