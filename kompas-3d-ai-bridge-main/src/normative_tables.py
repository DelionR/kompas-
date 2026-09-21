"""Нормативные таблицы как данные: реестр, схемы, отбор строк, отказ.

Волна 11 дала двигатель расчёта, в котором нормативных таблиц не было вовсе:
движок умел считать, но каждая таблица приходила от вызывающей стороны, и в
репозитории не было ни одной. Это был главный остаток против альтернатив: движок
без данных считает только там, где хватает физики.

Эта волна закрывает остаток — но не так, как закрывается «взять таблицу из
памяти и записать в код»:

1. **Данные лежат отдельно от кода** — в ``data/normative/*.json``, каждая
   таблица с ``source``: документ, таблица стандарта, URL, издатель, дата.
   Источник возвращается в каждом ответе, иначе результат нечем проверить.
2. **Данные собраны из публикаций стандартов**, а не набраны по памяти.
   Пересборка — ``tools/build_normative_tables.py``.
3. **Чего добыть не удалось, того нет.** Таблицы коэффициентов спроса в ПУЭ
   нет; в репозитории лежит ровно одно значение, найденное в тексте (п. 6.3.39),
   и таблица помечена ``partial``. Расчёт по непокрытому типу нагрузки отказывает.

Модуль ничего не считает: он отдаёт строки, проверяет их по схеме и умеет
отбирать строки по значениям полей. Считает по-прежнему ``cabinet_calc``.

Отбор строк — отдельная и важная часть. Таблица допустимых токов одна, а условий
применения много: материал, изоляция, число нагруженных проводников, способ
монтажа. Передать в расчёт все 868 строк сразу значит выбрать минимальное
сечение по лучшему из способов монтажа — то есть получить правдоподобный и
неверный ответ. Поэтому отбор обязателен: ``filter`` задаёт значения полей, и
строка либо подходит, либо нет. Если после отбора строк не осталось — отказ со
списком доступных значений, а не подбор «похожего».
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORMATIVE_DIR = os.path.join(_PROJECT_ROOT, "data", "normative")
REFERENCE_PREFIX = "normative:"

TABLE_AMPACITY = "ampacity"
TABLE_BREAKERS = "breakers"
TABLE_TRIP_CURVES = "trip_curves"
TABLE_DERATING = "derating"
TABLE_VOLTAGE_DROP_LIMITS = "voltage_drop_limits"
TABLE_DEMAND_FACTORS = "demand_factors"
TABLE_DESIGNATIONS = "designations"
TABLE_DESIGNATION_MAP = "designation_map"

TABLES = (TABLE_AMPACITY, TABLE_BREAKERS, TABLE_TRIP_CURVES, TABLE_DERATING,
          TABLE_VOLTAGE_DROP_LIMITS, TABLE_DEMAND_FACTORS,
          TABLE_DESIGNATIONS, TABLE_DESIGNATION_MAP)

# Обязательные поля строки: без них строка не пригодна для расчёта. Тип вторым
# элементом — чтобы «16» и «16 А» не выдавались за одно и то же.
SCHEMAS: Dict[str, Dict[str, str]] = {
    TABLE_AMPACITY: {"section_mm2": "number", "current_a": "number"},
    TABLE_BREAKERS: {"rating_a": "number"},
    TABLE_TRIP_CURVES: {"rating_a": "number", "points": "list"},
    TABLE_DERATING: {"factor": "number"},
    TABLE_VOLTAGE_DROP_LIMITS: {"limit_percent": "number"},
    TABLE_DEMAND_FACTORS: {"load_type": "text", "demand_factor": "number"},
    TABLE_DESIGNATIONS: {"code": "text", "element": "text"},
    TABLE_DESIGNATION_MAP: {"kind": "text", "code": "text"},
}

# Поля, по которым таблица обычно отбирается. Нужны для подсказки в ответе:
# вызывающая сторона должна видеть, чем можно фильтровать.
FILTER_HINTS: Dict[str, Tuple[str, ...]] = {
    TABLE_AMPACITY: ("material", "insulation", "loaded_conductors", "method"),
    TABLE_BREAKERS: ("poles", "trip_curve", "manufacturer", "series"),
    TABLE_TRIP_CURVES: ("trip_curve",),
    TABLE_DERATING: ("kind", "insulation", "ambient_c", "circuits", "item"),
    TABLE_VOLTAGE_DROP_LIMITS: ("installation_type", "load_type"),
    TABLE_DEMAND_FACTORS: ("load_type",),
    TABLE_DESIGNATIONS: ("letter", "code", "group"),
    TABLE_DESIGNATION_MAP: ("kind",),
}

# Таблицы, которых в репозитории нет, с причиной. Держать этот список в коде
# важно: пустой файл и отсутствующий файл должны объясняться по-разному, а
# «таблицы нет» — это ответ, который модель не должна додумывать.
MISSING_TABLES: Dict[str, str] = {}

LIMITS = [
    "Модуль отдаёт данные и проверяет их по схеме. Считает cabinet_calc.",
    "Каждая таблица сопровождается source: документ, таблица стандарта, URL, "
    "издатель и дата получения. Результат без источника проверить нечем.",
    "Таблицы собраны из открытых публикаций стандартов и открытых карточек "
    "изделий. Перед применением в проекте сверяйте с официальным текстом.",
    "Чего добыть не удалось, того в репозитории нет: расчёт отказывает, а не "
    "подставляет правдоподобное значение.",
    "Отбор строк по filter обязателен там, где таблица зависит от условий "
    "применения. Без отбора по способу монтажа выбор сечения дал бы минимальное "
    "сечение по лучшему из способов — правдоподобный и неверный ответ.",
]


# --------------------------------------------------------------------------
# разбор
# --------------------------------------------------------------------------

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
# загрузка
# --------------------------------------------------------------------------

def table_path(name: str) -> str:
    return os.path.join(NORMATIVE_DIR, name + ".json")


def _load_file(name: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    path = table_path(name)
    if not os.path.isfile(path):
        return None, _refusal("no_table_file",
                              "Файл таблицы %s не найден: %s" % (name, path),
                              table=name, expected_path=path,
                              missing_reason=MISSING_TABLES.get(name))
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as error:
        return None, _refusal("bad_table_file",
                              "Таблица %s не прочитана: %s" % (name, error),
                              table=name, path=path)
    if not isinstance(document, dict):
        return None, _refusal("bad_table_file",
                              "Таблица %s: ожидался объект с rows." % name,
                              table=name, path=path)
    return document, None


def _provenance(name: str, source: Dict[str, Any],
                notes: List[str]) -> List[Dict[str, Any]]:
    if not source:
        notes.append("таблица %s без source: источник нормы неизвестен, "
                     "результат нельзя проверить по первоисточнику" % name)
    entry = {"table": name}
    for key in ("document", "tables", "url", "publisher", "retrieved", "row_count"):
        if source.get(key) is not None:
            entry[key] = source[key]
    if source.get("partial"):
        notes.append("таблица %s неполная: покрывает не все случаи, для "
                     "непокрытых расчёт отказывает" % name)
    return [entry]


def load_table(name: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any],
                                   List[Dict[str, Any]], List[str]]:
    """Строки таблицы, её source, отказы и примечания."""
    notes: List[str] = []
    document, refusal = _load_file(name)
    if refusal:
        return [], {}, [refusal], notes
    rows = [r for r in _as_list(document.get("rows")) if isinstance(r, dict)]
    source = _as_dict(document.get("source"))
    if not rows:
        notes.append("таблица %s пустая: расчёт по ней откажет" % name)
    return rows, source, [], notes


def list_tables() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Инвентарь: что есть и чего нет. Второй список — отсутствующее с причиной."""
    present: List[Dict[str, Any]] = []
    absent: List[Dict[str, Any]] = []
    for name in TABLES:
        rows, source, refusals, _notes = load_table(name)
        if refusals:
            absent.append({"table": name,
                           "reason": MISSING_TABLES.get(name)
                           or "файл data/normative/%s.json отсутствует" % name,
                           "refusal": refusals[0]})
            continue
        entry = {"table": name, "row_count": len(rows),
                 "filter_fields": list(FILTER_HINTS.get(name, ()))}
        for key in ("title", "document", "tables", "url", "publisher",
                    "retrieved", "conditions", "partial"):
            if source.get(key) is not None:
                entry[key] = source[key]
        present.append(entry)
    return present, absent


# --------------------------------------------------------------------------
# отбор строк
# --------------------------------------------------------------------------

def _matches(row: Dict[str, Any], field: str, wanted: Any) -> bool:
    value = row.get(field)
    variants = wanted if isinstance(wanted, (list, tuple)) else [wanted]
    for variant in variants:
        if isinstance(variant, str) and isinstance(value, str):
            if value.strip().lower() == variant.strip().lower():
                return True
            continue
        left, right = _num(value), _num(variant)
        if left is not None and right is not None and abs(left - right) < 1e-9:
            return True
    return False


def apply_filter(rows: List[Dict[str, Any]], spec: Any,
                 table: str = "") -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Отбор строк по значениям полей.

    Строка подходит, если по каждому полю фильтра значение совпало. Поля в
    строке нет — строка не подходит: неизвестное не считается подходящим.
    Если после отбора пусто, возвращается отказ со списком доступных значений —
    вызывающая сторона видит, что именно есть в таблице.
    """
    spec_dict = _as_dict(spec)
    if not spec_dict:
        return rows, None
    kept = []
    for row in rows:
        if all(_matches(row, field, wanted) for field, wanted in spec_dict.items()):
            kept.append(row)
    if kept:
        return kept, None
    available: Dict[str, Any] = {}
    for field in spec_dict:
        seen: List[Any] = []
        for row in rows:
            value = row.get(field)
            if value is not None and value not in seen:
                seen.append(value)
        if not seen:
            available[field] = None
            continue
        numbers = [v for v in seen if _num(v) is not None]
        available[field] = (sorted(set(numbers)) if len(numbers) == len(seen)
                            else sorted(set(str(v) for v in seen)))
    return [], _refusal(
        "no_rows_after_filter",
        "После отбора в таблице %s не осталось строк." % (table or "?"),
        table=table or None,
        filter=spec_dict,
        available_values=available,
        filter_fields=list(FILTER_HINTS.get(table, ())))


# --------------------------------------------------------------------------
# ссылки из расчёта
# --------------------------------------------------------------------------

def is_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower().startswith(REFERENCE_PREFIX)
    if isinstance(value, dict):
        return bool(_text(value.get("ref")) or _text(value.get("table_ref")))
    return False


def reference_name(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value.strip()[len(REFERENCE_PREFIX):].strip() or None
    if isinstance(value, dict):
        return _text(value.get("ref")) or _text(value.get("table_ref")) or None
    return None


def resolve_reference(value: Any) -> Tuple[List[Dict[str, Any]], Optional[str],
                                           List[Dict[str, Any]], List[str]]:
    """Таблица по ссылке ``normative:<name>``.

    Возвращает строки, source, отказы и примечания — в том же виде, что
    ``load_table``, чтобы расчёт не различал «таблицу передали» и «таблицу
    взяли из реестра».
    """
    name = reference_name(value)
    if not name:
        return [], None, [_refusal("bad_reference",
                                   "Ссылка на таблицу не распознана: %r" % (value,),
                                   expected=REFERENCE_PREFIX + "<имя>",
                                   known_tables=list(TABLES))], []
    if name not in TABLES:
        return [], None, [_refusal("unknown_table",
                                   "Неизвестная таблица: %s." % name,
                                   known_tables=list(TABLES))], []
    rows, source, refusals, notes = load_table(name)
    if refusals:
        return [], None, refusals, notes
    spec = None
    if isinstance(value, dict):
        spec = value.get("filter")
    if spec:
        rows, refusal = apply_filter(rows, spec, name)
        if refusal:
            return [], None, [refusal], notes
    return rows, _text(source.get("document")) or None, [], notes


# --------------------------------------------------------------------------
# проверка по схеме
# --------------------------------------------------------------------------

def validate_rows(name: str, rows: List[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Ошибки строк по схеме таблицы. Пустой список — строки пригодны."""
    errors: List[Dict[str, Any]] = []
    notes: List[str] = []
    missing_values = 0
    schema = SCHEMAS.get(name)
    if schema is None:
        return [_refusal("unknown_table", "Неизвестная таблица: %s." % name,
                         known_tables=list(TABLES))], notes
    if not rows:
        errors.append(_refusal("empty_rows", "Нет ни одной строки для проверки.",
                               table=name))
        return errors, notes
    if name not in SCHEMAS:
        # Проверка структурной ошибки раньше поколоночной: неизвестную таблицу
        # нечего сверять со схемой.
        return [_refusal("unknown_table", "Неизвестная таблица: %s." % name,
                         known_tables=list(TABLES))], notes
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append({"index": index, "code": "bad_row",
                           "message": "Строка не объект."})
            continue
        for field, kind in schema.items():
            if field not in row:
                errors.append({"index": index, "code": "missing_field",
                               "field": field,
                               "message": "Нет обязательного поля %s." % field})
                continue
            value = row[field]
            if value is None:
                # Поле есть, но значение не опубликовано (прочерк стандарта).
                # Это не ошибка схемы, а пропуск: строка пригодна, но расчёт по
                # ней откажет.
                missing_values += 1
                continue
            if kind == "number" and _num(value) is None:
                errors.append({"index": index, "code": "bad_type", "field": field,
                               "message": "Поле %s должно быть числом." % field})
            elif kind == "text" and not _text(value):
                errors.append({"index": index, "code": "bad_type", "field": field,
                               "message": "Поле %s должно быть непустой строкой." % field})
            elif kind == "list" and not isinstance(value, (list, tuple)):
                errors.append({"index": index, "code": "bad_type", "field": field,
                               "message": "Поле %s должно быть списком." % field})
    if name == TABLE_TRIP_CURVES:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for point in _as_list(row.get("points")):
                if not isinstance(point, dict) or _num(point.get("multiple")) is None:
                    errors.append({"index": index, "code": "bad_point",
                                   "message": "Точка характеристики должна иметь "
                                              "числовое поле multiple."})
                    break
    if missing_values:
        notes.append("в %d строках значение не опубликовано (прочерк источника): "
                     "строка пригодна, но расчёт по ней откажет" % missing_values)
    if not errors:
        notes.append("строки %d: проверка по схеме %s пройдена" % (len(rows), name))
    return errors, notes


# --------------------------------------------------------------------------
# диспетчер инструмента
# --------------------------------------------------------------------------

KIND_LIST = "list"
KIND_GET = "get"
KIND_VALIDATE = "validate"
KINDS = (KIND_LIST, KIND_GET, KIND_VALIDATE)


def _kind_list(_payload: Dict[str, Any]) -> Dict[str, Any]:
    present, absent = list_tables()
    notes: List[str] = []
    for entry in present:
        if entry.get("partial"):
            notes.append("таблица %s неполная" % entry["table"])
    for entry in absent:
        notes.append("таблица %s отсутствует: %s" % (entry["table"], entry["reason"]))
    return _result(KIND_LIST, {
        "tables": present,
        "missing": absent,
        "table_count": len(present),
        "missing_count": len(absent),
        "directory": NORMATIVE_DIR,
    }, notes=notes)


def _kind_get(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = _text(payload.get("table") or payload.get("name"))
    if not name:
        return _result(KIND_GET, {}, [_refusal("no_table_name",
                                               "Не задано имя таблицы.",
                                               known_tables=list(TABLES))])
    if name not in TABLES:
        return _result(KIND_GET, {}, [_refusal("unknown_table",
                                               "Неизвестная таблица: %s." % name,
                                               known_tables=list(TABLES))])
    rows, source, refusals, notes = load_table(name)
    if refusals:
        return _result(KIND_GET, {}, refusals, notes=notes)
    provenance = _provenance(name, source, notes)
    spec = payload.get("filter")
    if spec:
        rows, refusal = apply_filter(rows, spec, name)
        if refusal:
            return _result(KIND_GET, {}, [refusal], notes=notes,
                           provenance=provenance)
    limit = int(_num(payload.get("limit"), 0.0) or 0.0)
    total = len(rows)
    truncated = limit > 0 and total > limit
    if truncated:
        rows = rows[:limit]
    return _result(KIND_GET, {
        "table": name,
        "row_count": len(rows),
        "total_rows": total,
        "truncated": truncated,
        "filter": _as_dict(spec) or None,
        "filter_fields": list(FILTER_HINTS.get(name, ())),
        "rows": rows,
        "source": source,
    }, notes=notes, provenance=provenance)


def _kind_validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = _text(payload.get("table") or payload.get("name"))
    if not name:
        return _result(KIND_VALIDATE, {}, [_refusal("no_table_name",
                                                    "Не задано имя таблицы.",
                                                    known_tables=list(TABLES))])
    rows = payload.get("rows")
    if rows is None:
        _rows, _source, refusals, notes = load_table(name)
        if refusals:
            return _result(KIND_VALIDATE, {}, refusals, notes=notes)
        rows = _rows
    rows = [r for r in _as_list(rows) if isinstance(r, dict)]
    errors, notes = validate_rows(name, rows)
    structural = [e for e in errors if str(e.get("code")) == "unknown_table"]
    if structural:
        return _result(KIND_VALIDATE, {}, refusals=structural, notes=notes)
    if errors:
        # Ошибки схемы — это тоже отказ: таблица, не прошедшая проверку, не
        # должна уходить в расчёт.
        return _result(KIND_VALIDATE, {
            "table": name,
            "checked_rows": len(rows),
            "valid": False,
            "errors": errors,
            "error_count": len(errors),
        }, refusals=[_refusal("schema_errors",
                              "Таблица %s не прошла проверку по схеме: "
                              "%d ошибок в %d строках." % (name, len(errors), len(rows)),
                              table=name, error_count=len(errors))],
            notes=notes)
    return _result(KIND_VALIDATE, {
        "table": name,
        "checked_rows": len(rows),
        "valid": True,
        "errors": [],
        "error_count": 0,
    }, notes=notes)


_DISPATCH = {
    KIND_LIST: _kind_list,
    KIND_GET: _kind_get,
    KIND_VALIDATE: _kind_validate,
}


def handle(payload: Any) -> Dict[str, Any]:
    """Единая точка входа инструмента: ``{"kind": "list|get|validate"}``."""
    data = _as_dict(payload)
    kind = _text(data.get("kind")).lower() or KIND_LIST
    if kind not in _DISPATCH:
        return _result(kind, {}, [_refusal("unknown_kind",
                                           "Неизвестный вид запроса: %s." % kind,
                                           known_kinds=list(KINDS))])
    return _DISPATCH[kind](data)
