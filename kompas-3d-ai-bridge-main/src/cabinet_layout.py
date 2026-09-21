"""Раскладка монтажной панели щита — чистые функции, без COM.

Модель повторяет соглашения, проверенные в ``cabinet-layout-generator-v2``:
миллиметры, начало координат — **верхний левый** угол панели, ``+y`` вниз.
Переворот в систему CAD (начало снизу слева, ``+y`` вверх) делается в одной
функции :func:`to_cad` и нигде больше.

Модуль ничего не выбирает и не подбирает: он проверяет то, что передали.
Неподтверждённый габарит (``confirm: true``) — не ошибка и не повод подставить
«примерно 90»: такая позиция уходит в ``unconfirmed``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Погрешность сравнения геометрии, мм. Ноль здесь не работает: повёрнутые
# габариты дают погрешность тригонометрии.
EPS_MM = 1e-6

# Порог, начиная с которого зазор считается «не проложить провод».
DEFAULT_CLEARANCE_MM = 20.0
DEFAULT_GAP_MM = 5.0

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


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


def load_layout(payload: Any) -> Dict[str, Any]:
    """Нормализовать модель раскладки. Нечитаемые элементы не отбрасываются
    молча — они попадают в ``parse_issues``."""
    data = _as_dict(payload)
    parse_issues: List[Dict[str, Any]] = []

    plate_raw = _as_dict(data.get("plate"))
    plate = {
        "width_mm": _number(plate_raw.get("width_mm")) or 0.0,
        "height_mm": _number(plate_raw.get("height_mm")) or 0.0,
        "origin": _text(plate_raw.get("origin")) or "top_left",
    }

    defaults_raw = _as_dict(data.get("defaults"))
    defaults = {
        "gap_between_equipment_mm": _number(defaults_raw.get("gap_between_equipment_mm"), DEFAULT_GAP_MM),
        "clearance_equipment_to_duct_mm": _number(
            defaults_raw.get("clearance_equipment_to_duct_mm"), DEFAULT_CLEARANCE_MM),
    }

    def parse_point(kind: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for raw in _as_list(data.get(kind)):
            obj = _as_dict(raw)
            if not obj:
                parse_issues.append({"level": "error", "code": "BAD_%s" % kind.upper(),
                                     "message": "элемент %s не является объектом" % kind})
                continue
            rec = dict(obj)
            rec["id"] = _text(obj.get("id"))
            for key in ("x_mm", "y_mm", "length_mm", "width_mm", "height_mm",
                        "rot_deg", "gap_before_mm", "clearance_to_duct_mm",
                        "internal_gap_mm", "count"):
                if key in obj:
                    rec[key] = _number(obj.get(key))
            out.append(rec)
        return out

    ducts = parse_point("ducts")
    elements = parse_point("elements")
    groups = parse_point("groups")

    library: Dict[str, Dict[str, Any]] = {}
    for raw in _as_list(data.get("library")):
        item = _as_dict(raw)
        if not item:
            parse_issues.append({"level": "error", "code": "BAD_LIBRARY_ITEM",
                                 "message": "элемент library не является объектом"})
            continue
        key = _text(item.get("lib_key"))
        library[key] = {
            "lib_key": key,
            "name": _text(item.get("name")) or key,
            "width_mm": _number(item.get("width_mm")) or 0.0,
            "height_mm": _number(item.get("height_mm")) or 0.0,
            "rail_offset_mm": _number(item.get("rail_offset_mm")),
            "confirm": bool(item.get("confirm")),
        }

    return {
        "plate": plate,
        "defaults": defaults,
        "ducts": ducts,
        "elements": elements,
        "groups": groups,
        "library": library,
        "parse_issues": parse_issues,
    }


# --------------------------------------------------------------------------
# геометрия
# --------------------------------------------------------------------------

def rotated_size(width_mm: float, height_mm: float, rot_deg: float) -> Tuple[float, float]:
    """Габарит повёрнутой детали (AABB)."""
    a = math.radians((rot_deg or 0.0) % 360.0)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return (width_mm * c + height_mm * s, width_mm * s + height_mm * c)


def placed_box(x_mm: float, y_mm: float, width_mm: float, height_mm: float,
               rot_deg: float = 0.0) -> Dict[str, float]:
    """Прямоугольник детали в системе панели.

    ``x_mm``/``y_mm`` — левый верхний угол **неповёрнутой** детали. При
    повороте рамка расширяется вокруг центра, поэтому левый верхний угол
    сдвигается. Это именно та тонкость, на которой ошибаются при переносе
    координат в CAD.
    """
    w, h = rotated_size(width_mm, height_mm, rot_deg)
    cx = x_mm + width_mm / 2.0
    cy = y_mm + height_mm / 2.0
    return {"x": cx - w / 2.0, "y": cy - h / 2.0, "w": w, "h": h}


def to_cad(box: Dict[str, float], plate_height_mm: float) -> Dict[str, float]:
    """Единственное место перехода в систему CAD: ``+y`` вниз -> ``+y`` вверх.

    Возвращает рамку в координатах с началом снизу слева.
    """
    return {
        "x": box["x"],
        "y": plate_height_mm - (box["y"] + box["h"]),
        "w": box["w"],
        "h": box["h"],
    }


def box_within_plate(box: Dict[str, float], plate: Dict[str, Any]) -> bool:
    return (box["x"] >= -EPS_MM and box["y"] >= -EPS_MM
            and box["x"] + box["w"] <= plate["width_mm"] + EPS_MM
            and box["y"] + box["h"] <= plate["height_mm"] + EPS_MM)


def boxes_overlap(a: Dict[str, float], b: Dict[str, float]) -> bool:
    return (a["x"] < b["x"] + b["w"] - EPS_MM and b["x"] < a["x"] + a["w"] - EPS_MM
            and a["y"] < b["y"] + b["h"] - EPS_MM and b["y"] < a["y"] + a["h"] - EPS_MM)


def box_distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Расстояние между двумя рамками; 0 при касании или пересечении."""
    dx = max(b["x"] - (a["x"] + a["w"]), a["x"] - (b["x"] + b["w"]), 0.0)
    dy = max(b["y"] - (a["y"] + a["h"]), a["y"] - (b["y"] + b["h"]), 0.0)
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------
# примитивы модели -> рамки
# --------------------------------------------------------------------------

def element_box(el: Dict[str, Any], library: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, float]]:
    item = library.get(_text(el.get("lib_key")))
    if item is None:
        return None
    return placed_box(_number(el.get("x_mm"), 0.0) or 0.0,
                      _number(el.get("y_mm"), 0.0) or 0.0,
                      item["width_mm"], item["height_mm"],
                      _number(el.get("rot_deg"), 0.0) or 0.0)


def group_width(grp: Dict[str, Any], library: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """Ширина «сета»: N одинаковых деталей, внутренний зазор между ними и
    необязательные торцевые крышки."""
    item = library.get(_text(grp.get("lib_key")))
    if item is None:
        return None
    count = int(_number(grp.get("count"), 0.0) or 0.0)
    if count <= 0:
        return None
    gap = _number(grp.get("internal_gap_mm"), 0.0) or 0.0
    total = count * item["width_mm"] + max(count - 1, 0) * gap
    for cap_key in (grp.get("cap_start_key"), grp.get("cap_end_key")):
        cap = library.get(_text(cap_key))
        if cap is not None:
            total += cap["width_mm"] + gap
    return total


def group_box(grp: Dict[str, Any], library: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, float]]:
    item = library.get(_text(grp.get("lib_key")))
    if item is None:
        return None
    width = group_width(grp, library)
    if width is None:
        return None
    return placed_box(_number(grp.get("x_mm"), 0.0) or 0.0,
                      _number(grp.get("y_mm"), 0.0) or 0.0,
                      width, item["height_mm"],
                      _number(grp.get("rot_deg"), 0.0) or 0.0)


def duct_box(duct: Dict[str, Any]) -> Dict[str, float]:
    """Канал: ``length_mm`` вдоль направления, ``width_mm`` поперёк.

    ``label_h_mm`` здесь отсутствует намеренно: вторая цифра в «40x60» — это
    только подпись, на геометрию она не влияет.
    """
    length = _number(duct.get("length_mm"), 0.0) or 0.0
    width = _number(duct.get("width_mm"), 0.0) or 0.0
    rot = _number(duct.get("rot_deg"), 0.0) or 0.0
    vertical = abs((rot % 180.0) - 90.0) < 1e-6
    w, h = (width, length) if vertical else (length, width)
    return {"x": _number(duct.get("x_mm"), 0.0) or 0.0,
            "y": _number(duct.get("y_mm"), 0.0) or 0.0,
            "w": w, "h": h}


# --------------------------------------------------------------------------
# ряды
# --------------------------------------------------------------------------

def detect_rows(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Сгруппировать оборудование в ряды по положению центра.

    Признак ряда — близость центров по ``y``: два объекта попадают в один ряд,
    если их центры различаются меньше, чем на половину высоты более крупного.
    Это эвристика, а не чтение настоящей структуры rows из CAD-модели.
    """
    boxes: List[Tuple[str, str, Dict[str, float]]] = []
    for el in model["elements"]:
        box = element_box(el, model["library"])
        if box is not None:
            boxes.append((_text(el.get("id")), _text(el.get("tag")) or _text(el.get("id")), box))
    for grp in model["groups"]:
        box = group_box(grp, model["library"])
        if box is not None:
            boxes.append((_text(grp.get("id")), _text(grp.get("tag_start")) or _text(grp.get("id")), box))
    if not boxes:
        return []

    boxes.sort(key=lambda p: (p[2]["y"] + p[2]["h"] / 2.0, p[2]["x"]))
    rows: List[Dict[str, Any]] = []
    for ident, label, box in boxes:
        centre = box["y"] + box["h"] / 2.0
        if rows and abs(centre - rows[-1]["centre_mm"]) <= max(box["h"], rows[-1]["max_h"]) / 2.0:
            rows[-1]["members"].append({"id": ident, "label": label, "box": box})
            rows[-1]["max_h"] = max(rows[-1]["max_h"], box["h"])
        else:
            rows.append({"index": len(rows), "centre_mm": centre, "max_h": box["h"],
                         "members": [{"id": ident, "label": label, "box": box}]})
    for row in rows:
        row["members"].sort(key=lambda m: m["box"]["x"])
    return rows


def available_row_width(model: Dict[str, Any], row: Dict[str, Any]) -> float:
    """Доступная ширина ряда: между боковыми (вертикальными) каналами.

    Если вертикальных каналов нет или он один — считается вся ширина панели,
    и это честно фиксируется в ответе, а не маскируется под точный расчёт.
    """
    verticals = []
    for duct in model["ducts"]:
        rot = _number(duct.get("rot_deg"), 0.0) or 0.0
        if abs((rot % 180.0) - 90.0) < 1e-6:
            verticals.append(duct_box(duct))
    if len(verticals) < 2:
        return model["plate"]["width_mm"]
    xs = sorted(v["x"] + v["w"] for v in verticals)
    lefts = sorted(v["x"] for v in verticals)
    return max(min(lefts[1:]) - max(xs[:-1]), 0.0) if len(verticals) >= 2 else model["plate"]["width_mm"]


def row_overflow_mm(model: Dict[str, Any], row: Dict[str, Any]) -> float:
    """Насколько ряд не влезает, в мм. 0 — влезает."""
    gap = model["defaults"]["gap_between_equipment_mm"] or 0.0
    total = sum(m["box"]["w"] for m in row["members"])
    total += gap * max(len(row["members"]) - 1, 0)
    return max(total - available_row_width(model, row), 0.0)


# --------------------------------------------------------------------------
# проверки
# --------------------------------------------------------------------------

def _issue(level: str, code: str, message: str, ref: Optional[str] = None) -> Dict[str, Any]:
    out = {"level": level, "code": code, "message": message}
    if ref:
        out["ref"] = ref
    return out


def collect_unconfirmed(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Позиции с неподтверждённым габаритом: их нельзя честно разместить."""
    out: List[Dict[str, Any]] = []
    for el in model["elements"]:
        item = model["library"].get(_text(el.get("lib_key")))
        if item is not None and (item.get("confirm") or item["width_mm"] <= 0 or item["height_mm"] <= 0):
            out.append({"id": _text(el.get("id")), "lib_key": item["lib_key"],
                        "reason": "unconfirmed_size" if item.get("confirm") else "zero_size"})
    for grp in model["groups"]:
        item = model["library"].get(_text(grp.get("lib_key")))
        if item is not None and (item.get("confirm") or item["width_mm"] <= 0):
            out.append({"id": _text(grp.get("id")), "lib_key": item["lib_key"],
                        "reason": "unconfirmed_size" if item.get("confirm") else "zero_size"})
    return out


def validate_layout(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Проверить раскладку. ``error`` — структурно невозможно,
    ``warning`` — разрешено, но показать. Ничего не исправляется."""
    issues: List[Dict[str, Any]] = []
    plate = model["plate"]
    library = model["library"]

    if plate["origin"] != "top_left":
        issues.append(_issue("error", "BAD_ORIGIN",
                             'plate.origin должен быть "top_left", получен "%s"' % plate["origin"],
                             "plate"))
    if plate["width_mm"] <= 0 or plate["height_mm"] <= 0:
        issues.append(_issue("error", "BAD_PLATE_SIZE",
                             "ширина и высота панели должны быть больше нуля", "plate"))

    seen: set = set()
    for kind in ("ducts", "elements", "groups"):
        for obj in model[kind]:
            ident = _text(obj.get("id"))
            if not ident:
                issues.append(_issue("error", "MISSING_ID", "у объекта %s нет id" % kind))
                continue
            if ident in seen:
                issues.append(_issue("error", "DUPLICATE_ID", 'повторяющийся id "%s"' % ident, ident))
            seen.add(ident)

    for duct in model["ducts"]:
        if (_number(duct.get("length_mm"), 0.0) or 0.0) <= 0 or (_number(duct.get("width_mm"), 0.0) or 0.0) <= 0:
            issues.append(_issue("error", "BAD_DUCT_SIZE",
                                 "длина и ширина канала должны быть больше нуля",
                                 _text(duct.get("id"))))

    for el in model["elements"]:
        key = _text(el.get("lib_key"))
        item = library.get(key)
        ident = _text(el.get("id"))
        if item is None:
            issues.append(_issue("error", "UNRESOLVED_LIB_KEY",
                                 'lib_key "%s" не найден в library' % (key or "?"), ident))
            continue
        box = element_box(el, library)
        if box is None or box["w"] <= 0 or box["h"] <= 0:
            issues.append(_issue("error", "BAD_SIZE", "неположительный габарит позиции", ident))
            continue
        if not box_within_plate(box, plate):
            issues.append(_issue("warning", "OFF_PLATE",
                                 "позиция выходит за пределы панели (разрешено, но показать)", ident))

    for grp in model["groups"]:
        key = _text(grp.get("lib_key"))
        ident = _text(grp.get("id"))
        if library.get(key) is None:
            issues.append(_issue("error", "UNRESOLVED_LIB_KEY",
                                 'lib_key "%s" не найден в library' % (key or "?"), ident))
            continue
        count = int(_number(grp.get("count"), 0.0) or 0.0)
        if count <= 0:
            issues.append(_issue("error", "BAD_COUNT", "количество в сете должно быть больше нуля", ident))
            continue
        box = group_box(grp, library)
        if box is None:
            continue
        if not box_within_plate(box, plate):
            issues.append(_issue("warning", "OFF_PLATE",
                                 "сет выходит за пределы панели (разрешено, но показать)", ident))

    # пересечения
    placed: List[Tuple[str, str, Dict[str, float]]] = []
    for el in model["elements"]:
        box = element_box(el, library)
        if box is not None:
            placed.append((_text(el.get("id")), _text(el.get("tag")) or _text(el.get("id")), box))
    for grp in model["groups"]:
        box = group_box(grp, library)
        if box is not None:
            placed.append((_text(grp.get("id")), _text(grp.get("tag_start")) or _text(grp.get("id")), box))
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            if boxes_overlap(placed[i][2], placed[j][2]):
                issues.append(_issue("warning", "OVERLAP",
                                     '"%s" пересекается с "%s"' % (placed[i][1], placed[j][1]),
                                     placed[i][0]))

    # зазор до каналов
    duct_boxes = [(_text(d.get("id")), duct_box(d)) for d in model["ducts"]]
    for ident, label, box in placed:
        need = None
        for el in model["elements"]:
            if _text(el.get("id")) == ident:
                need = _number(el.get("clearance_to_duct_mm"))
                break
        clearance = need if need is not None else model["defaults"]["clearance_equipment_to_duct_mm"]
        for duct_id, dbox in duct_boxes:
            if box_distance(box, dbox) < (clearance or 0.0) - EPS_MM:
                issues.append(_issue("warning", "CLEARANCE_TIGHT",
                                     '"%s" ближе %.4g мм до канала %s' % (label, clearance or 0.0, duct_id),
                                     ident))
                break

    # переполнение рядов
    for row in detect_rows(model):
        overflow = row_overflow_mm(model, row)
        if overflow > EPS_MM:
            issues.append(_issue("warning", "ROW_OVERFLOW",
                                 "ряд %d не влезает на %.4g мм — оборудование шире, "
                                 "чем расстояние между боковыми каналами"
                                 % (row["index"] + 1, overflow)))

    return sorted(issues, key=lambda i: (SEVERITY_RANK.get(i["level"], 9), i["code"]))


def summarize(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"error": 0, "warning": 0, "info": 0, "total": len(issues)}
    for i in issues:
        level = i.get("level")
        if level in out:
            out[level] += 1
    return out


def check_layout(payload: Any) -> Dict[str, Any]:
    """Точка входа инструмента. Ничего не открывает и не пишет."""
    model = load_layout(payload)
    issues = validate_layout(model)
    unconfirmed = collect_unconfirmed(model)
    summary = summarize(issues)
    rows = detect_rows(model)

    verdict = "blocked" if summary["error"] else (
        "ready_with_unknowns" if (unconfirmed or summary["warning"]) else "ready")

    return {
        "ok": summary["error"] == 0,
        "verdict": verdict,
        "summary": summary,
        "issues": issues,
        "unconfirmed": unconfirmed,
        "parse_issues": model["parse_issues"],
        "rows": [{"index": r["index"], "members": len(r["members"]),
                  "overflow_mm": round(row_overflow_mm(model, r), 6)}
                 for r in rows],
        "plate": model["plate"],
        "limits": [
            "Начало координат — верхний левый угол, +y вниз; переход в систему CAD "
            "делает только to_cad().",
            "Признак ряда — близость центров по y, это эвристика, а не чтение структуры из CAD.",
            "Неподтверждённый габарит уходит в unconfirmed, а не заменяется оценкой.",
        ],
    }
