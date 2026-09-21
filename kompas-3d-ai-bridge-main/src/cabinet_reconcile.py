"""Сверка ожидаемого состава щита с перечнем из КОМПАС — чистые функции, без COM.

Волна 9 материализовала раскладку. Эта волна отвечает на вопрос «а что
получилось на самом деле»: сравнивает, что мы собирались поставить, с тем, что
КОМПАС показывает в перечне.

Модуль **не читает КОМПАС**. Он принимает уже готовый результат
``kompas_bom_read`` (или ``kompas_specification_read``) и сверяет его с моделью
раскладки. Поэтому всё здесь проверяется без установленного КОМПАСа.

Ключ сверки — **имя файла детали** (``source_file``), а не позиционное
обозначение. Причина простая: в волне 9 маркируется только исходный экземпляр
сета, поэтому в перечне у двадцати четырёх клемм стоит одно обозначение ``B101``.
Сверять по обозначениям значит получить двадцать три ложных расхождения.

Что модуль **не** делает:

- не считает расхождение ошибкой сборки по умолчанию: часть расхождений
  нормальна и объяснима (та же маркировка), поэтому они помечаются причиной;
- не подменяет отсутствующие данные: если в перечне нет поля, по которому
  сверяем, позиция уходит в ``unverified``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from cabinet_materialize import part_filename

MARKING_FIRST_ONLY = "first_only"
MARKING_ALL = "all"

_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


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


def next_designation(tag: str, step: float) -> Optional[str]:
    """Следующее обозначение: ``B101`` с шагом 1 даёт ``B102``.

    Если числовой части нет, возвращаем ``None`` — наращивать нечего, и молча
    вернуть исходное обозначение значило бы выдать желаемое за действительное.
    """
    match = _TRAILING_NUMBER.match(_text(tag))
    if not match:
        return None
    prefix, digits = match.group(1), match.group(2)
    step_int = int(step) if float(step) == int(step) else None
    if step_int is None:
        return None
    return "%s%0*d" % (prefix, len(digits), int(digits) + step_int)


def designation_series(tag: str, count: int, step: float) -> Tuple[List[str], bool]:
    """Все обозначения сета. Второй элемент — удалось ли нарастить."""
    series: List[str] = []
    current = _text(tag)
    total = max(int(count), 0)
    for _ in range(total):
        if not current:
            break
        series.append(current)
        nxt = next_designation(current, step)
        if nxt is None:
            # Нарастить не удалось: серия заведомо неполная, и это надо
            # показать, а не вернуть «всё в порядке».
            return series, False
        current = nxt
    return series, len(series) == total


def expected_designations(obj: Dict[str, Any], policy: str) -> Tuple[List[str], bool]:
    """Ожидаемые позиционные обозначения одной позиции раскладки.

    Для сета при политике ``first_only`` это ровно одно обозначение: остальные
    экземпляры наследуют его через паттерн и отдельно в перечень не попадают.
    """
    kind = "group" if obj.get("count") else "element"
    if kind == "element":
        tag = _text(obj.get("tag"))
        return ([tag] if tag else []), True

    count = int(_number(obj.get("count"), 0.0) or 0.0)
    start = _text(obj.get("tag_start"))
    if not start:
        return [], True
    if policy == MARKING_ALL:
        return designation_series(start, count, _number(obj.get("tag_step"), 1.0) or 1.0)
    return [start], True        # маркируется только исходный экземпляр


# --------------------------------------------------------------------------
# стороны сверки
# --------------------------------------------------------------------------

def expected_items(payload: Any) -> List[Dict[str, Any]]:
    """Ожидаемый состав из модели раскладки."""
    data = _as_dict(payload)
    layout = _as_dict(data.get("layout"))
    library: Dict[str, Dict[str, Any]] = {}
    for raw in _as_list(layout.get("library")):
        item = _as_dict(raw)
        key = _text(item.get("lib_key"))
        if key:
            library[key] = item

    policy = _text(data.get("marking_policy")) or MARKING_FIRST_ONLY
    rows: List[Dict[str, Any]] = []

    for kind, obj in ([("element", e) for e in _as_list(layout.get("elements"))]
                      + [("group", g) for g in _as_list(layout.get("groups"))]):
        obj = _as_dict(obj)
        key = _text(obj.get("lib_key"))
        item = library.get(key, {})
        count = (1 if kind == "element"
                 else int(_number(obj.get("count"), 0.0) or 0.0))
        tags, complete = expected_designations(obj, policy)
        rows.append({
            "id": _text(obj.get("id")) or None,
            "lib_key": key or None,
            "name": _text(item.get("name")) or key or None,
            # Имя файла задаётся правилом моста, а не выдумывается здесь:
            # в библиотеке раскладки его может не быть вовсе.
            "filename": (_text(item.get("filename"))
                         or (part_filename(key) if key else None)),
            "fragment_ref": _text(item.get("fragment_ref")) or None,
            "expected_count": count,
            "expected_designations": tags,
            "designations_complete": complete,
        })
    return rows


def bom_index(bom: Any) -> Dict[str, Dict[str, Any]]:
    """Индекс перечня по имени файла детали.

    Регистр не важен: КОМПАС может вернуть ``Term_AGENT_COPY.M3D``, а мы
    ожидаем ``term_AGENT_COPY.m3d``.
    """
    rows = _as_dict(bom).get("bom_rows")
    if rows is None:
        rows = _as_list(bom)
    index: Dict[str, Dict[str, Any]] = {}
    for raw in _as_list(rows):
        row = _as_dict(raw)
        key = (_text(row.get("source_file"))
               or _text(row.get("component_filename"))
               or _text(row.get("filename")))
        if not key:
            continue
        slot = index.setdefault(key.casefold(), {
            "source_file": key,
            "occurrence_count": 0,
            "designations": [],
            "names": [],
        })
        slot["occurrence_count"] += int(_number(row.get("occurrence_count"), 1.0) or 1.0)
        designation = _text(row.get("designation"))
        if designation and designation not in slot["designations"]:
            slot["designations"].append(designation)
        name = _text(row.get("name"))
        if name and name not in slot["names"]:
            slot["names"].append(name)
    return index


def _key_of(row: Dict[str, Any]) -> Optional[str]:
    """Ключ ожидаемой позиции: файл детали, иначе ссылка на фрагмент."""
    return _text(row.get("filename")) or _text(row.get("fragment_ref")) or None


# --------------------------------------------------------------------------
# сверка
# --------------------------------------------------------------------------

def reconcile(payload: Any) -> Dict[str, Any]:
    """Сверить ожидаемый состав с перечнем КОМПАС."""
    data = _as_dict(payload)
    expected = expected_items(data)
    index = bom_index(data.get("bom"))
    policy = _text(data.get("marking_policy")) or MARKING_FIRST_ONLY

    matched: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    quantity: List[Dict[str, Any]] = []
    designation_gaps: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    seen: Dict[str, bool] = {}

    for row in expected:
        key = _key_of(row)
        if not key:
            unverified.append({"id": row["id"], "lib_key": row["lib_key"],
                               "reason": "no_filename"})
            continue
        seen[key.casefold()] = True
        actual = index.get(key.casefold())
        if actual is None:
            missing.append({"id": row["id"], "lib_key": row["lib_key"],
                            "filename": key,
                            "expected_count": row["expected_count"],
                            "reason": "absent_in_bom"})
            continue

        expected_count = row["expected_count"]
        actual_count = actual["occurrence_count"]
        if expected_count != actual_count:
            quantity.append({"id": row["id"], "lib_key": row["lib_key"],
                             "filename": key,
                             "expected_count": expected_count,
                             "actual_count": actual_count,
                             "delta": actual_count - expected_count})
        else:
            matched.append({"id": row["id"], "lib_key": row["lib_key"],
                            "filename": key, "count": actual_count})

        present = set(actual["designations"])
        wanted = row["expected_designations"]
        absent = [tag for tag in wanted if tag not in present]
        if absent:
            gap = {"id": row["id"], "lib_key": row["lib_key"],
                   "missing_designations": absent}
            if policy == MARKING_FIRST_ONLY and len(wanted) < max(expected_count, 1):
                gap["reason"] = ("маркируется только исходный экземпляр; "
                                 "политика first_only")
            designation_gaps.append(gap)
        if not row["designations_complete"]:
            unverified.append({"id": row["id"], "lib_key": row["lib_key"],
                               "reason": "designation_series_incomplete"})

    unexpected = [{"source_file": slot["source_file"],
                   "occurrence_count": slot["occurrence_count"]}
                  for key, slot in index.items() if not seen.get(key)]

    ok = not missing and not quantity and not unexpected and not unverified
    return {
        "ok": ok,
        "verdict": ("match" if ok else
                    ("mismatch" if (missing or quantity or unexpected) else
                     "match_with_unknowns")),
        "marking_policy": policy,
        "summary": {
            "expected_items": len(expected),
            "bom_items": len(index),
            "matched": len(matched),
            "missing": len(missing),
            "quantity_mismatch": len(quantity),
            "unexpected": len(unexpected),
            "designation_gaps": len(designation_gaps),
            "unverified": len(unverified),
        },
        "matched": matched,
        "missing_in_bom": missing,
        "quantity_mismatch": quantity,
        "unexpected_in_bom": unexpected,
        "designation_gaps": designation_gaps,
        "unverified": unverified,
        "limits": [
            "Сверка идёт по имени файла детали: позиционные обозначения "
            "вторичны, потому что при политике first_only в перечне стоит "
            "только обозначение исходного экземпляр сета.",
            "Расхождение по количеству — не всегда ошибка сборки: экземпляры, "
            "созданные линейным массивом, могут учитываться КОМПАСом иначе.",
            "Имена файлов сравниваются без учёта регистра.",
            "Модуль не читает КОМПАС: он сверяет переданный результат "
            "kompas_bom_read с моделью раскладки.",
        ],
    }
