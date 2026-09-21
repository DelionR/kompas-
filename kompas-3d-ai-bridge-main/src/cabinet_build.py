"""Генератор раскладки монтажной панели — чистые функции, без COM.

Волна 7 умела только проверять раскладку. Эта волна её **строит**: по составу
изделий, панели и каналам рассчитывает координаты каждой позиции.

Соглашения унаследованы из ``cabinet_layout``: миллиметры, начало координат сверху
слева, ``+y`` вниз. Результат — готовая модель раскладки, которую принимает
``cabinet_layout.check_layout``.

Что генератор **не** делает:

- не выбирает оборудование и не меняет порядок позиций (порядок входа — это
  инженерное решение, генератор его уважает);
- не подбирает габарит, если его нет: позиция без подтверждённого размера
  уходит в ``unplaced``, а не в геометрию «примерно 90 мм»;
- не считает электрику и тепловой режим.

Ключевая механика — **DIN-рейка**. Детали в ряду выравниваются не по центру
габарита, а по оси рейки: расстояние от верхней кромки детали до оси задаётся
полем ``rail_offset_mm`` в библиотеке. Именно из-за этого клеммы разной высоты
встают в одну линию, а не «пляшут».
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import cabinet_layout as cl

DEFAULT_SIDE_DUCT_MM = 40.0
DEFAULT_ROW_GAP_MM = 30.0
DEFAULT_FIRST_RAIL_Y_MM = 120.0


# --------------------------------------------------------------------------
# разбор
# --------------------------------------------------------------------------

def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def load_build(payload: Any) -> Dict[str, Any]:
    data = _as_dict(payload)
    parse_issues: List[Dict[str, Any]] = []

    plate_raw = _as_dict(data.get("plate"))
    plate = {
        "width_mm": _number(plate_raw.get("width_mm")) or 0.0,
        "height_mm": _number(plate_raw.get("height_mm")) or 0.0,
        "origin": _text(plate_raw.get("origin")) or "top_left",
    }
    if plate["width_mm"] <= 0 or plate["height_mm"] <= 0:
        parse_issues.append({"level": "error", "code": "BAD_PLATE_SIZE",
                             "message": "нужны положительные ширина и высота панели"})

    defaults_raw = _as_dict(data.get("defaults"))
    defaults = {
        "gap_between_equipment_mm": _number(defaults_raw.get("gap_between_equipment_mm"), cl.DEFAULT_GAP_MM),
        "clearance_equipment_to_duct_mm": _number(
            defaults_raw.get("clearance_equipment_to_duct_mm"), cl.DEFAULT_CLEARANCE_MM),
        "row_gap_mm": _number(defaults_raw.get("row_gap_mm"), DEFAULT_ROW_GAP_MM),
        "side_duct_mm": _number(defaults_raw.get("side_duct_mm"), DEFAULT_SIDE_DUCT_MM),
    }

    rail_raw = _as_dict(data.get("rail"))
    rail = {
        "first_rail_y_mm": _number(rail_raw.get("first_rail_y_mm"), DEFAULT_FIRST_RAIL_Y_MM),
        "pitch_mm": _number(rail_raw.get("pitch_mm")),
    }

    library: Dict[str, Dict[str, Any]] = {}
    for raw in _as_list(data.get("library")):
        item = _as_dict(raw)
        key = _text(item.get("lib_key"))
        if not key:
            parse_issues.append({"level": "error", "code": "BAD_LIBRARY_ITEM",
                                 "message": "позиция библиотеки без lib_key"})
            continue
        library[key] = {
            "lib_key": key,
            "name": _text(item.get("name")) or key,
            "width_mm": _number(item.get("width_mm")) or 0.0,
            "height_mm": _number(item.get("height_mm")) or 0.0,
            "rail_offset_mm": _number(item.get("rail_offset_mm")),
            "confirm": bool(item.get("confirm")),
        }

    lines: List[Dict[str, Any]] = []
    for raw in _as_list(data.get("lines")):
        line = _as_dict(raw)
        if not line:
            parse_issues.append({"level": "error", "code": "BAD_LINE",
                                 "message": "элемент lines не является объектом"})
            continue
        qty = _number(line.get("qty"), 1.0) or 1.0
        lines.append({
            "id": _text(line.get("id")),
            "lib_key": _text(line.get("lib_key")),
            "qty": int(qty) if float(qty) == int(qty) else int(qty) + 1,
            "tag": _text(line.get("tag")),
            "tag_start": _text(line.get("tag_start")),
            "tag_step": _number(line.get("tag_step"), 1.0) or 1.0,
            "internal_gap_mm": _number(line.get("internal_gap_mm"), 0.0) or 0.0,
            "cap_start_key": _text(line.get("cap_start_key")) or None,
            "cap_end_key": _text(line.get("cap_end_key")) or None,
        })

    ducts: List[Dict[str, Any]] = []
    for raw in _as_list(data.get("ducts")):
        duct = _as_dict(raw)
        if not duct:
            continue
        rec = dict(duct)
        rec["id"] = _text(duct.get("id"))
        for key in ("x_mm", "y_mm", "length_mm", "width_mm", "rot_deg"):
            if key in duct:
                rec[key] = _number(duct.get(key))
        ducts.append(rec)

    return {"plate": plate, "defaults": defaults, "rail": rail, "library": library,
            "lines": lines, "ducts": ducts, "parse_issues": parse_issues}


# --------------------------------------------------------------------------
# каналы и доступная область
# --------------------------------------------------------------------------

def make_side_ducts(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Два вертикальных канала по краям панели, если свои не переданы.

    Такие каналы помечаются ``generated: true`` — инженер должен видеть, что их
    не он задал.
    """
    plate = model["plate"]
    width = model["defaults"]["side_duct_mm"] or 0.0
    if width <= 0:
        return []
    return [
        {"id": "duct-left", "x_mm": 0.0, "y_mm": 0.0, "length_mm": plate["height_mm"],
         "width_mm": width, "rot_deg": 90.0, "generated": True},
        {"id": "duct-right", "x_mm": plate["width_mm"] - width, "y_mm": 0.0,
         "length_mm": plate["height_mm"], "width_mm": width, "rot_deg": 90.0,
         "generated": True},
    ]


def usable_span(model: Dict[str, Any], ducts: List[Dict[str, Any]]) -> Dict[str, float]:
    """Доступный по X интервал между боковыми каналами с учётом зазора.

    Границу задают только каналы, примыкающие к кромке панели: левый сдвигает
    начало, правый — конец. Канал в середине поля — не граница, а препятствие;
    наложение на него поймает ``check_layout``, а не эта функция. Смешивать роли
    нельзя: если считать середину границей, доступная область схлопывается.
    """
    plate = model["plate"]
    clearance = model["defaults"]["clearance_equipment_to_duct_mm"] or 0.0
    width = plate["width_mm"]
    edge = 1.0  # допуск на примыкание к кромке, мм
    x0 = clearance
    x1 = width - clearance
    for duct in ducts:
        rot = _number(duct.get("rot_deg"), 0.0) or 0.0
        if abs((rot % 180.0) - 90.0) >= 1e-6:
            continue
        box = cl.duct_box(duct)
        if box["w"] <= 0:
            continue
        if box["x"] <= edge:
            x0 = max(x0, box["x"] + box["w"] + clearance)
        if box["x"] + box["w"] >= width - edge:
            x1 = min(x1, box["x"] - clearance)
    return {"x0": x0, "x1": max(x1, x0)}


# --------------------------------------------------------------------------
# расстановка
# --------------------------------------------------------------------------

def line_width(line: Dict[str, Any], library: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """Ширина, которую займёт строка состава: одиночная деталь или сет."""
    item = library.get(line["lib_key"])
    if item is None:
        return None
    gap = line["internal_gap_mm"] or 0.0
    if line["qty"] <= 1:
        return item["width_mm"]
    grp = {"lib_key": line["lib_key"], "count": line["qty"],
           "internal_gap_mm": gap,
           "cap_start_key": line.get("cap_start_key"),
           "cap_end_key": line.get("cap_end_key")}
    return cl.group_width(grp, library)


def place_line(line: Dict[str, Any], library: Dict[str, Dict[str, Any]],
               x_mm: float, rail_y_mm: float) -> Dict[str, Any]:
    """Разместить одну строку состава и вернуть объект модели раскладки.

    Одиночная позиция становится ``element``, партия — ``group`` (сет): именно
    сет потом материализуется линейным массивом в CAD.
    """
    item = library[line["lib_key"]]
    offset = item.get("rail_offset_mm")
    if offset is None:
        offset = item["height_mm"] / 2.0
        source = "centre"
    else:
        source = "catalog"
    y_mm = rail_y_mm - offset

    if line["qty"] <= 1:
        return {"kind": "element", "source": source,
                "obj": {"id": line["id"], "lib_key": line["lib_key"],
                        "tag": line["tag"], "x_mm": round(x_mm, 6),
                        "y_mm": round(y_mm, 6), "rot_deg": 0.0,
                        "gap_before_mm": 0.0, "clearance_to_duct_mm": None,
                        "group_id": None, "locked": False}}
    return {"kind": "group", "source": source,
            "obj": {"id": line["id"], "kind": "set", "lib_key": line["lib_key"],
                    "count": line["qty"], "internal_gap_mm": line["internal_gap_mm"],
                    "cap_start_key": line.get("cap_start_key"),
                    "cap_end_key": line.get("cap_end_key"),
                    "tag_start": line["tag_start"], "tag_step": line["tag_step"],
                    "x_mm": round(x_mm, 6), "y_mm": round(y_mm, 6), "rot_deg": 0.0,
                    "exploded": False, "label_id": None}}


def build_layout(payload: Any) -> Dict[str, Any]:
    """Собрать раскладку. Возвращает модель, непомещённое и самопроверку."""
    model = load_build(payload)
    plate = model["plate"]
    defaults = model["defaults"]
    library = model["library"]
    gap = defaults["gap_between_equipment_mm"] or 0.0
    row_gap = defaults["row_gap_mm"] or 0.0

    ducts = model["ducts"] or make_side_ducts(model)
    span = usable_span(model, ducts)
    available = span["x1"] - span["x0"]

    elements: List[Dict[str, Any]] = []
    groups: List[Dict[str, Any]] = []
    unplaced: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []

    rail_y = model["rail"]["first_rail_y_mm"] or 0.0
    pitch = model["rail"]["pitch_mm"]
    x = span["x0"]
    row_index = 0
    row_height = 0.0
    row_members = 0

    for line in model["lines"]:
        item = library.get(line["lib_key"])
        if item is None:
            unplaced.append({"id": line["id"], "lib_key": line["lib_key"],
                             "reason": "unknown_lib_key"})
            continue
        if item.get("confirm") or item["width_mm"] <= 0 or item["height_mm"] <= 0:
            unplaced.append({"id": line["id"], "lib_key": line["lib_key"],
                             "reason": "unconfirmed_size"})
            continue

        width = line_width(line, library)
        if width is None or width <= 0:
            unplaced.append({"id": line["id"], "lib_key": line["lib_key"],
                             "reason": "zero_width"})
            continue
        if width > available + 1e-9:
            unplaced.append({"id": line["id"], "lib_key": line["lib_key"],
                             "reason": "wider_than_plate",
                             "detail": "ширина %.4g мм больше доступных %.4g мм" % (width, available)})
            continue

        # перенос строки: не влезает в остаток ряда
        if row_members and x + width > span["x1"] + 1e-9:
            rows.append({"index": row_index, "rail_y_mm": round(rail_y, 6),
                         "members": row_members, "height_mm": round(row_height, 6)})
            row_index += 1
            rail_y = (rail_y + pitch) if pitch else (rail_y + row_height + row_gap)
            x = span["x0"]
            row_height = 0.0
            row_members = 0

        # по низу панели: дальше ставить некуда
        bottom = rail_y + (item["height_mm"] / 2.0 if item.get("rail_offset_mm") is None
                           else max(item["height_mm"] - item["rail_offset_mm"], item["rail_offset_mm"]))
        if bottom > plate["height_mm"] + 1e-9:
            unplaced.append({"id": line["id"], "lib_key": line["lib_key"],
                             "reason": "no_vertical_space",
                             "detail": "ряд %d выходит за низ панели" % (row_index + 1)})
            continue

        placed = place_line(line, library, x, rail_y)
        if placed["kind"] == "element":
            elements.append(placed["obj"])
        else:
            groups.append(placed["obj"])
        x += width + gap
        row_height = max(row_height, item["height_mm"])
        row_members += 1

    if row_members:
        rows.append({"index": row_index, "rail_y_mm": round(rail_y, 6),
                     "members": row_members, "height_mm": round(row_height, 6)})

    layout_model = {
        "plate": plate,
        "defaults": {"gap_between_equipment_mm": defaults["gap_between_equipment_mm"],
                     "clearance_equipment_to_duct_mm": defaults["clearance_equipment_to_duct_mm"]},
        "ducts": [{k: v for k, v in d.items() if k != "generated"} for d in ducts],
        "elements": elements,
        "groups": groups,
        "labels": [],
        "library": [dict(v) for v in library.values()],
    }

    # генератор обязан проверить собственный вывод той же проверкой, которой
    # пользуется отдельный инструмент, — иначе «сгенерировано» и «корректно»
    # расходятся.
    self_check = cl.check_layout(layout_model)

    return {
        "ok": not unplaced and self_check["summary"]["error"] == 0,
        "layout": layout_model,
        "unplaced": unplaced,
        "rows": rows,
        "ducts_generated": not model["ducts"],
        "self_check": {"summary": self_check["summary"],
                       "issues": self_check["issues"],
                       "verdict": self_check["verdict"]},
        "parse_issues": model["parse_issues"],
        "limits": [
            "Порядок позиций берётся из входа: генератор не перегруппировывает "
            "оборудование по функциональным признакам.",
            "rail_offset_mm задаёт расстояние от верхней кромки детали до оси "
            "DIN-рейки; если поле отсутствует, используется центр по высоте "
            "(соглашение cabinet-layout-generator-v2).",
            "Позиция без подтверждённого габарита уходит в unplaced, а не "
            "размещается с оценочным размером.",
            "Раскладка не проверяет электрику, тепловой режим и трассировку.",
        ],
    }
