"""Перевод раскладки щита в координаты чертежа AutoCAD.

Главное отличие от КОМПАС
-------------------------
Щитовая раскладка считается в миллиметрах, начало координат **сверху слева**,
``+y`` идёт вниз — так устроен домен, и это зафиксировано в проекте.

В AutoCAD начало координат **снизу слева** и ``+y`` идёт вверх. Значит
координату Y нужно не просто умножить на масштаб, а **развернуть** относительно
высоты платы:

.. code-block:: text

    y_acad = origin_y + (H - y_layout - h) * k

где ``H`` — высота платы, ``h`` — высота изделия, ``k`` — единиц документа в
одном миллиметре.

Отсюда важное следствие: **без высоты платы перевод невозможен**. У части
корпусов производитель не публикует габарит монтажной панели, и в мосте
КОМПАС высота платы там честно равна ``None``. Здесь это превращается в отказ
``no_plate_height``: развернуть Y относительно неизвестной высоты нельзя, а
подставить «на 20 мм меньше корпуса» — значит выдать оценку за расчёт.

Соглашение о точке вставки
--------------------------
Блок вставляется в точку **левого нижнего угла**. Это соглашение адаптера, а не
свойство AutoCAD: базовая точка блока задаётся при его создании и может быть
любой. Поэтому соглашение возвращается в ответе и помечено ``convention``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

INSERT_POINT_CONVENTION = "left_bottom"

LIMITS: List[str] = [
    "Начало координат раскладки сверху слева, +y вниз; в AutoCAD — снизу "
    "слева, +y вверх. Координата Y разворачивается относительно высоты платы.",
    "Без высоты платы перевод невозможен: отказ no_plate_height, а не оценка "
    "«панель на 20 мм меньше корпуса».",
    "Точка вставки блока — левый нижний угол. Это соглашение адаптера, а не "
    "свойство AutoCAD: базовая точка блока задаётся при его создании.",
    "Масштаб берётся только из INSUNITS: документ без единиц — отказ.",
    "Запись в чертёж здесь не производится: модуль отдаёт координаты.",
]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _result(placements: List[Dict[str, Any]],
            refusals: List[Dict[str, Any]],
            notes: List[str],
            extra: Dict[str, Any]) -> Dict[str, Any]:
    verdict = "refused" if refusals else (
        "computed_with_gaps" if extra.get("outside") else "computed")
    payload = {
        "verdict": verdict,
        "insert_point": INSERT_POINT_CONVENTION,
        "insert_point_note": ("соглашение адаптера, помечено convention: "
                              "базовая точка блока задаётся при его создании"),
        "placements": placements,
        "placement_count": len(placements),
        "outside": extra.get("outside", []),
        "bbox_units": extra.get("bbox_units"),
        "refusals": refusals,
        "notes": notes,
        "limits": list(LIMITS),
    }
    payload.update({k: v for k, v in extra.items() if k not in ("outside", "bbox_units")})
    return payload


def place(payload: Any) -> Dict[str, Any]:
    """Разместить элементы раскладки в системе координат AutoCAD.

    Возвращает точки вставки в единицах документа. Мутаций чертежа нет.
    """
    data = _as_dict(payload)
    refusals: List[Dict[str, Any]] = []
    notes: List[str] = []

    units = _as_dict(data.get("units"))
    scale = _num(units.get("units_per_mm"))
    if scale is None:
        refusals.append({"code": "no_units",
                         "message": "Единицы документа не определены: нужен "
                                    "INSUNITS, отличный от 0.",
                         "required": ["units"]})

    plate = _as_dict(data.get("plate"))
    plate_w = _num(plate.get("width_mm"))
    plate_h = _num(plate.get("height_mm"))
    if plate_h is None:
        refusals.append({"code": "no_plate_height",
                         "message": "Высота платы неизвестна: развернуть "
                                    "координату Y не относительно чего.",
                         "required": ["plate.height_mm"]})

    origin = _as_dict(data.get("origin"))
    origin_x = _num(origin.get("x")) or 0.0
    origin_y = _num(origin.get("y")) or 0.0

    if refusals:
        return _result([], refusals, notes, {})

    placements: List[Dict[str, Any]] = []
    outside: List[Dict[str, Any]] = []
    for element in _as_list(data.get("elements")):
        item = _as_dict(element)
        name = _text(item.get("name")) or _text(item.get("lib_key")) or None
        x_mm = _num(item.get("x_mm"))
        y_mm = _num(item.get("y_mm"))
        w_mm = _num(item.get("width_mm")) or _num(item.get("w_mm"))
        h_mm = _num(item.get("height_mm")) or _num(item.get("h_mm"))
        if x_mm is None or y_mm is None or w_mm is None or h_mm is None:
            refusals.append({"code": "no_geometry",
                             "message": "У элемента нет габарита и координат: "
                                        "подставлять типовой размер нельзя.",
                             "name": name})
            continue

        # Левый нижний угол в системе раскладки переводим в систему AutoCAD:
        # Y раскладки идёт вниз от верха платы, Y чертежа — вверх от низа.
        x_units = origin_x + x_mm * scale
        y_units = origin_y + (plate_h - y_mm - h_mm) * scale
        placements.append({
            "name": name,
            "block": _text(item.get("block")) or name,
            "insert_x": round(x_units, 6),
            "insert_y": round(y_units, 6),
            "width_units": round(w_mm * scale, 6),
            "height_units": round(h_mm * scale, 6),
            "rotation_deg": _num(item.get("rotation_deg")) or 0.0,
            "mm": {"x_mm": x_mm, "y_mm": y_mm,
                   "width_mm": w_mm, "height_mm": h_mm},
            "convention": INSERT_POINT_CONVENTION,
        })

        if plate_w is not None and (x_mm + w_mm) > plate_w + 1e-9:
            outside.append({"name": name, "reason": "wider_than_plate",
                            "x_mm": x_mm, "width_mm": w_mm,
                            "plate_width_mm": plate_w})
        if (y_mm + h_mm) > plate_h + 1e-9:
            outside.append({"name": name, "reason": "lower_than_plate",
                            "y_mm": y_mm, "height_mm": h_mm,
                            "plate_height_mm": plate_h})

    if not placements and not refusals:
        refusals.append({"code": "no_elements",
                         "message": "Элементы не переданы: поле elements пусто."})

    extra: Dict[str, Any] = {"outside": outside}
    if placements:
        xs = [p["insert_x"] for p in placements]
        ys = [p["insert_y"] for p in placements]
        extra["bbox_units"] = {
            "min_x": min(xs), "min_y": min(ys),
            "max_x": max(p["insert_x"] + p["width_units"] for p in placements),
            "max_y": max(p["insert_y"] + p["height_units"] for p in placements),
        }
    if outside:
        notes.append("за пределами платы: %d" % len(outside))
    return _result(placements, refusals, notes, extra)
