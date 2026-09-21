"""Спецификация и маркировка щита из раскладки — чистые функции, без COM.

Волны 7–10 научили мост проверять состав, считать раскладку, материализовать её
и сверять перечень с уже существующим. Одно звено отсутствовало: **перечень сам
из раскладки не рождался**. Спецификация в мосте только читалась, а поле
`labels` в модели раскладки оставалось пустым — то есть щит можно было
разложить и даже построить, но нельзя было получить из этого документа.

Эта волна закрывает звено тремя видами:

- ``bom`` — перечень: что стоит, сколько, какого габарита и где;
- ``marking`` — позиционные обозначения по ГОСТ 2.710-81 (QF1, KM1, XT1…);
- ``labels`` — шильды: обозначение с координатами для нанесения.

Дисциплина та же, что в расчёте: **отказ вместо выдумывания**.

- позиция без наименования в библиотеке не получает «типовое» имя, а уходит в
  ``unverified``;
- изделие, у которого нет буквенного кода (УЗО: в ГОСТ 2.710-81 отдельного кода
  для устройства защитного отключения нет), не получает код автоматического
  выключателя, а уходит в ``unverified`` с указанием причины;
- нумерация идёт по порядку расположения на плате, а не по алфавиту
  наименований: обозначение должно читаться слева направо и сверху вниз.

Соответствие «вид изделия → код» лежит в реестре нормативных таблиц
(``designation_map``) и помечено как составленное, а не процитированное: в
стандарте коды заданы для видов элементов схемы, а не для изделий щита. Это
различие важно — выдавать составленное соответствие за цитату из стандарта
нельзя.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import cabinet_layout
import normative_tables

KIND_BOM = "bom"
KIND_MARKING = "marking"
KIND_LABELS = "labels"
KINDS = (KIND_BOM, KIND_MARKING, KIND_LABELS)

LIMITS = [
    "Перечень и маркировка строятся из раскладки: что не попало в library, "
    "того в перечне нет с достоверным наименованием и габаритом.",
    "Буквенные коды — по ГОСТ 2.710-81. Соответствие вида изделия коду "
    "составлено, а не процитировано: в стандарте коды заданы для видов "
    "элементов схемы.",
    "Для УЗО кода в ГОСТ 2.710-81 нет: позиция уходит в unverified, а не "
    "получает код автоматического выключателя.",
    "Нумерация идёт по порядку расположения на плате (сверху вниз, слева "
    "направо), а не по наименованию.",
    "Запись перечня и маркировки в документ КОМПАС здесь не производится: "
    "модуль отдаёт данные.",
]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _refusal(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "message": message}
    item.update(extra)
    return item


def _result(kind: str, data: Dict[str, Any], refusals: Optional[List[Dict[str, Any]]] = None,
            notes: Optional[List[str]] = None,
            provenance: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "kind": kind,
        "ok": not refusals,
        "verdict": "refused" if refusals else "computed",
        "result": data,
        "refusals": list(refusals or []),
        "notes": list(notes or []),
        "provenance": list(provenance or []),
        "limits": list(LIMITS),
    }


# --------------------------------------------------------------------------
# разбор раскладки в позиции
# --------------------------------------------------------------------------

def collect_positions(model: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Позиции из раскладки и то, что не удалось разобрать.

    Одна запись ``elements`` — одна позиция. Запись ``groups`` — несколько
    одинаковых изделий подряд, то есть ``count`` позиций с coordinates ряда.
    """
    library = model.get("library") or {}
    positions: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []

    for element in _as_list(model.get("elements")):
        record = _as_dict(element)
        key = _text(record.get("lib_key"))
        item = library.get(key) if key else None
        position = {
            "id": _text(record.get("id")) or None,
            "lib_key": key or None,
            "name": (item or {}).get("name") if item else None,
            "width_mm": (item or {}).get("width_mm") if item else None,
            "height_mm": (item or {}).get("height_mm") if item else None,
            "rail_offset_mm": (item or {}).get("rail_offset_mm") if item else None,
            "x_mm": _num(record.get("x_mm")),
            "y_mm": _num(record.get("y_mm")),
            "rot_deg": _num(record.get("rot_deg")),
            "count": 1,
            "origin": "element",
        }
        if item is None:
            position["unverified_reason"] = "lib_key отсутствует в library"
            unverified.append(position)
        positions.append(position)

    for group in _as_list(model.get("groups")):
        record = _as_dict(group)
        key = _text(record.get("lib_key"))
        item = library.get(key) if key else None
        count = int(_num(record.get("count"), 1.0) or 1.0)
        x_mm = _num(record.get("x_mm"))
        y_mm = _num(record.get("y_mm"))
        internal_gap = _num(record.get("internal_gap_mm"), 0.0) or 0.0
        step = (item or {}).get("width_mm") or 0.0
        step = float(step) + internal_gap
        for index in range(max(count, 1)):
            position = {
                "id": ("%s[%d]" % (_text(record.get("id")) or key, index + 1)) if key else None,
                "lib_key": key or None,
                "name": (item or {}).get("name") if item else None,
                "width_mm": (item or {}).get("width_mm") if item else None,
                "height_mm": (item or {}).get("height_mm") if item else None,
                "rail_offset_mm": (item or {}).get("rail_offset_mm") if item else None,
                "x_mm": None if x_mm is None else x_mm + step * index,
                "y_mm": y_mm,
                "rot_deg": _num(record.get("rot_deg")),
                "count": 1,
                "origin": "group",
            }
            if item is None:
                position["unverified_reason"] = "lib_key отсутствует в library"
                unverified.append(position)
            positions.append(position)
    return positions, unverified


def unwrap(payload: Any) -> Dict[str, Any]:
    """Развернуть обёртку ``kompas_cabinet_layout_build``.

    Раскладчик волны 8 отдаёт ``{"ok": ..., "layout": {...}}``. Пользователь
    должен иметь возможность передать этот ответ следующим вызовом целиком, не
    разбирая его руками: иначе звено «раскладка → спецификация» существует
    только на словах.
    """
    data = _as_dict(payload)
    inner = data.get("layout")
    if isinstance(inner, dict) and not data.get("elements") and not data.get("groups"):
        merged = dict(inner)
        for key, value in data.items():
            if key not in ("layout", "ok", "result", "limits", "notes",
                           "self_check", "parse_issues", "unplaced",
                           "rows", "ducts_generated"):
                merged.setdefault(key, value)
        return merged
    return data


def _order_key(position: Dict[str, Any]) -> Tuple[float, float, str]:
    """Порядок нумерации: сверху вниз, затем слева направо."""
    y = position.get("y_mm")
    x = position.get("x_mm")
    return (y if y is not None else 1e9,
            x if x is not None else 1e9,
            position.get("id") or "")


# --------------------------------------------------------------------------
# коды обозначений
# --------------------------------------------------------------------------

def resolve_codes(payload: Dict[str, Any], notes: List[str],
                  provenance: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Буквенный код для каждого lib_key.

    Код берётся одним из трёх способов, в порядке приоритета:

    1. явно переданное поле ``codes`` — ``{lib_key: "QF"}``;
    2. вид изделия (``kinds``: ``{lib_key: "breaker"}``) плюс таблица
       соответствия из реестра или переданная своя;
    3. ничего — тогда позиция уходит в ``unverified``.
    """
    data = _as_dict(payload)
    explicit: Dict[str, str] = {}
    for key, value in _as_dict(data.get("codes")).items():
        code = _text(value)
        if code:
            explicit[_text(key)] = code.upper()

    kinds: Dict[str, str] = {}
    for key, value in _as_dict(data.get("kinds")).items():
        kind = _text(value)
        if kind:
            kinds[_text(key)] = kind.lower()

    mapping: Dict[str, Optional[str]] = {}
    entry = _as_dict(data.get("tables")).get("designation_map")
    if entry is None and kinds:
        entry = "normative:designation_map"
        notes.append("таблица designation_map взята из реестра data/normative")
    if entry is not None:
        if isinstance(entry, (list, tuple)):
            rows = [r for r in entry if isinstance(r, dict)]
            source = None
        elif normative_tables.is_reference(entry):
            rows, source, refusals, extra_notes = normative_tables.resolve_reference(entry)
            notes.extend(extra_notes)
            if refusals:
                return {}, refusals
        else:
            raw = _as_dict(entry)
            rows = [r for r in _as_list(raw.get("rows")) if isinstance(r, dict)]
            source = _text(raw.get("source")) or None
        if source:
            provenance.append({"table": "designation_map", "source": source})
        for row in rows:
            kind = _text(row.get("kind")).lower()
            code = _text(row.get("code")) or None
            if kind:
                mapping[kind] = code.upper() if code else None

    codes: Dict[str, str] = dict(explicit)
    refused: List[Dict[str, Any]] = []
    for key, kind in kinds.items():
        if key in codes:
            continue
        code = mapping.get(kind)
        if code:
            codes[key] = code
        elif kind in mapping:
            refused.append({"lib_key": key, "kind": kind,
                            "reason": "no_code_for_kind",
                            "message": "Для вида изделия %s нет кода в ГОСТ 2.710-81."
                                       % kind})
        else:
            refused.append({"lib_key": key, "kind": kind,
                            "reason": "kind_not_in_table",
                            "message": "Вида изделия %s нет в таблице соответствия "
                                       "кодам." % kind})
    if not codes and not kinds and not explicit:
        notes.append("коды обозначений не заданы: позиции останутся без "
                     "позиционных обозначений")
    return codes, refused


# --------------------------------------------------------------------------
# виды
# --------------------------------------------------------------------------

def build_bom(payload: Any) -> Dict[str, Any]:
    """Перечень: что стоит, сколько, какого габарита и где."""
    data = unwrap(payload)
    model = cabinet_layout.load_layout(data)
    notes: List[str] = []
    for issue in _as_list(model.get("parse_issues")):
        notes.append("разбор раскладки: %s" % _as_dict(issue).get("message"))
    positions, unverified = collect_positions(model)
    if not positions:
        return _result(KIND_BOM, {}, [_refusal("empty_layout",
                                               "В раскладке нет ни одной позиции.",
                                               required_any=["elements", "groups"])],
                       notes=notes)

    grouped: Dict[str, Dict[str, Any]] = {}
    for position in positions:
        key = position.get("lib_key") or "?"
        entry = grouped.setdefault(key, {
            "lib_key": position.get("lib_key"),
            "name": position.get("name"),
            "width_mm": position.get("width_mm"),
            "height_mm": position.get("height_mm"),
            "rail_offset_mm": position.get("rail_offset_mm"),
            "count": 0,
            "positions": [],
        })
        entry["count"] += 1
        entry["positions"].append({"id": position.get("id"),
                                   "x_mm": position.get("x_mm"),
                                   "y_mm": position.get("y_mm")})

    items = sorted(grouped.values(), key=lambda item: _text(item["lib_key"]))
    total = sum(int(item["count"]) for item in items)
    return _result(KIND_BOM, {
        "items": items,
        "item_count": len(items),
        "total_count": total,
        "unverified": unverified,
        "ducts": [{"id": _text(_as_dict(d).get("id")) or None,
                   "width_mm": _num(_as_dict(d).get("width_mm")),
                   "length_mm": _num(_as_dict(d).get("length_mm"))}
                  for d in _as_list(model.get("ducts"))],
        "plate": model.get("plate"),
    }, notes=notes)


def _assign(payload: Any):
    """Общая часть marking и labels.

    Всегда возвращает пять значений: ``(позиции, неразрешённое, отказы по
    кодам, notes, provenance)``. Для пустой раскладки позиции — ``None``, а
    отказ лежит там же, где неразрешённое, чтобы вызывающий не разбирал два
    разных формата.
    """
    data = unwrap(payload)
    model = cabinet_layout.load_layout(data)
    notes: List[str] = []
    provenance: List[Dict[str, Any]] = []
    for issue in _as_list(model.get("parse_issues")):
        notes.append("разбор раскладки: %s" % _as_dict(issue).get("message"))
    positions, unverified = collect_positions(model)
    if not positions:
        return (None,
                [_refusal("empty_layout",
                          "В раскладке нет ни одной позиции.",
                          required_any=["elements", "groups"])],
                [], notes, provenance)

    codes, code_refusals = resolve_codes(data, notes, provenance)
    ordered = sorted(positions, key=_order_key)
    counters: Dict[str, int] = {}
    for position in ordered:
        key = position.get("lib_key") or ""
        code = codes.get(key)
        if not code:
            position["designation"] = None
            position["unverified_reason"] = ("нет буквенного кода: задайте codes "
                                             "или kinds с таблицей designation_map")
            continue
        counters[code] = counters.get(code, 0) + 1
        position["designation"] = "%s%d" % (code, counters[code])

    unresolved = list(unverified)
    for position in ordered:
        if not position.get("designation"):
            unresolved.append(position)
    return ordered, unresolved, code_refusals, notes, provenance


def build_marking(payload: Any) -> Dict[str, Any]:
    """Позиционные обозначения по ГОСТ 2.710-81."""
    ordered, unresolved, code_refusals, notes, provenance = _assign(payload)
    if ordered is None:
        return _result(KIND_MARKING, {}, unresolved, notes=notes,
                       provenance=provenance)
    assigned = sorted([p for p in ordered if p.get("designation")],
                      key=lambda p: (len(p["designation"]), p["designation"]))
    return _result(KIND_MARKING, {
        "positions": assigned,
        "assigned_count": len(assigned),
        "unresolved": unresolved,
        "code_refusals": code_refusals,
    }, notes=notes, provenance=provenance)


def build_labels(payload: Any) -> Dict[str, Any]:
    """Шильды: обозначение с координатами для нанесения."""
    data = unwrap(payload)
    ordered, unresolved, code_refusals, notes, provenance = _assign(payload)
    if ordered is None:
        return _result(KIND_LABELS, {}, unresolved, notes=notes,
                       provenance=provenance)
    with_name = bool(data.get("with_name"))
    labels: List[Dict[str, Any]] = []
    for position in ordered:
        designation = position.get("designation")
        if not designation:
            continue
        text = designation
        if with_name and position.get("name"):
            text = "%s %s" % (designation, position["name"])
        labels.append({
            "designation": designation,
            "text": text,
            "lib_key": position.get("lib_key"),
            "x_mm": position.get("x_mm"),
            "y_mm": position.get("y_mm"),
            "width_mm": position.get("width_mm"),
            "height_mm": position.get("height_mm"),
        })
    if not labels:
        return _result(KIND_LABELS, {}, [_refusal("no_designations",
                                                  "Ни одной позиции не присвоено "
                                                  "обозначение: шильды нечего "
                                                  "наносить.",
                                                  unresolved=unresolved)],
                       notes=notes, provenance=provenance)
    return _result(KIND_LABELS, {
        "labels": labels,
        "label_count": len(labels),
        "unresolved": unresolved,
    }, notes=notes, provenance=provenance)


_DISPATCH = {
    KIND_BOM: build_bom,
    KIND_MARKING: build_marking,
    KIND_LABELS: build_labels,
}


def build(payload: Any) -> Dict[str, Any]:
    """Единая точка входа: ``{"kind": "bom|marking|labels"}``."""
    data = _as_dict(payload)
    kind = _text(data.get("kind")).lower() or KIND_BOM
    if kind not in _DISPATCH:
        return {
            "kind": kind or None,
            "ok": False,
            "verdict": "refused",
            "result": {},
            "refusals": [_refusal("unknown_kind",
                                  "Неизвестный вид: %s." % (kind or "не задан"),
                                  known_kinds=list(KINDS))],
            "notes": [],
            "provenance": [],
            "limits": list(LIMITS),
        }
    return _DISPATCH[kind](data)
