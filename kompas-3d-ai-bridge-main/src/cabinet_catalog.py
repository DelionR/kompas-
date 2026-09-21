"""Каталог щитового оборудования: подбор по параметрам со ссылкой на источник.

Щитовой домен волн 7–11 умел проверять состав, считать раскладку, материализовать
её и рассчитывать режимы — но не мог ответить на первый вопрос проектировщика:
«а что именно поставить?» Раскладка требовала габарит, расчёт требовал каталог,
а каталога не было ни у моста, ни у конкурентов в открытом виде: `sans-calc-mcp`
заявляет `breaker_select`, но не выпущен; `SwitchScope` ищет по документам, а не
по изделиям.

Этот модуль закрывает разрыв и связывает три слоя:

    каталог -> габарит -> раскладка (cabinet_layout_build)
    каталог -> параметры -> расчёт (cabinet_calc)

 Поэтому каждая найденная позиция возвращается сразу с полем ``layout`` — готовой
 записью библиотеки раскладки, если габарит известен.

**Данных внутри модуля нет.** Каталог приносит вызывающая сторона: в поле
``catalog`` или файлом в ``catalog_path``. В репозитории лежит только
``data/cabinet_catalog.example.json`` — заглушка на типовых модульных габаритах
DIN 43880 без производителей, серий и артикулов, помеченная ``example: true``.
Ответ, построенный на ней, тоже помечается, чтобы типовой габарит нельзя было
выдать за паспорт изделия.

Дисциплина та же, что в расчёте: позиция без обязательного габарита уходит в
``unverified``, а не подменяется «средним по рынку» значением.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import cabinet_enclosure

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CATALOG_PATH = os.path.join(_PROJECT_ROOT, "data",
                                    "cabinet_catalog.example.json")

KIND_BREAKER = "breaker"
KIND_RCD = "rcd"
KIND_CONTACTOR = "contactor"
KIND_TERMINAL = "terminal"
KIND_BUSBAR = "busbar"
KIND_PSU = "psu"
KIND_ENCLOSURE = "enclosure"
KINDS = (KIND_BREAKER, KIND_RCD, KIND_CONTACTOR, KIND_TERMINAL, KIND_BUSBAR,
         KIND_PSU, KIND_ENCLOSURE)

GEOMETRY_FIELDS = ("width_mm", "height_mm")
# Корпусу нужен и третий габарит: глубина определяет, встанет ли оборудование.
ENCLOSURE_GEOMETRY_FIELDS = ("width_mm", "height_mm", "depth_mm")

LIMITS = [
    "Данных внутри модуля нет: каталог приносит вызывающая сторона в поле "
    "catalog или файлом в catalog_path.",
    "Файл data/cabinet_catalog.example.json — заглушка на типовых модульных "
    "габаритах DIN 43880 без производителей, серий и артикулов. Ответы по ней "
    "помечены example=true и не пригодны для проекта.",
    "Чтение файлов допускается только внутри корня проекта и текущего "
    "каталога: путь наружу отклоняется.",
    "Позиция без обязательного габарита уходит в unverified, а не заполняется "
    "типовым значением.",
    "Поиск по каталогу не заменяет сверку с паспортом изделия: перед заказом "
    "нужна проверка по каталогу изготовителя.",
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


def _as_values(value: Any) -> List[str]:
    """Значение фильтра в список строк в нижнем регистре."""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else [value]
    return [_text(item).lower() for item in raw if _text(item)]


# --------------------------------------------------------------------------
# загрузка каталога
# --------------------------------------------------------------------------

def _allowed_roots() -> List[str]:
    roots = [_PROJECT_ROOT]
    try:
        roots.append(os.path.realpath(os.getcwd()))
    except OSError:
        pass
    return [os.path.realpath(root) for root in roots]


def resolve_catalog_path(path: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Путь к каталогу, если он внутри разрешённого корня."""
    candidate = os.path.realpath(os.path.abspath(path))
    for root in _allowed_roots():
        if candidate == root or candidate.startswith(root + os.sep):
            return candidate, None
    return None, _refusal("path_outside_root",
                          "Файл каталога вне корня проекта и вне текущего каталога.",
                          allowed_roots=_allowed_roots())


def load_catalog(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any],
                                        List[Dict[str, Any]], List[str]]:
    """Позиции каталога, его описание, отказы и примечания.

    Источник в приоритете: явное поле ``catalog``, затем ``catalog_path``, затем
    пример из репозитория — но пример всегда помечается, чтобы типовой габарит
    нельзя было принять за паспорт изделия.
    """
    data = _as_dict(payload)
    notes: List[str] = []
    refusals: List[Dict[str, Any]] = []

    inline = data.get("catalog")
    if isinstance(inline, (list, tuple)):
        items = [i for i in inline if isinstance(i, dict)]
        return items, {"source": None, "example": False}, refusals, notes
    if isinstance(inline, dict):
        items = [i for i in _as_list(inline.get("items")) if isinstance(i, dict)]
        return items, {"source": _text(inline.get("source")) or None,
                       "example": bool(inline.get("example"))}, refusals, notes

    path = _text(data.get("catalog_path"))
    if path:
        resolved, refusal = resolve_catalog_path(path)
        if refusal:
            return [], {}, [refusal], notes
        if not os.path.isfile(resolved):
            return [], {}, [_refusal("no_catalog_file",
                                     "Файл каталога не найден: %s" % path,
                                     path=path)], notes
        try:
            with open(resolved, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as error:
            return [], {}, [_refusal("bad_catalog_file",
                                     "Каталог не прочитан: %s" % error,
                                     path=path)], notes
        if isinstance(document, (list, tuple)):
            items = [i for i in document if isinstance(i, dict)]
            return items, {"source": None, "example": False}, refusals, notes
        document = _as_dict(document)
        items = [i for i in _as_list(document.get("items")) if isinstance(i, dict)]
        return items, {"source": _text(document.get("source")) or None,
                       "example": bool(document.get("example")),
                       "path": path}, refusals, notes

    if not os.path.isfile(EXAMPLE_CATALOG_PATH):
        return [], {}, [_refusal("no_catalog",
                                 "Каталог не передан: нужно поле catalog или "
                                 "catalog_path, а файл-пример отсутствует.",
                                 required_any=["catalog", "catalog_path"])], notes
    try:
        with open(EXAMPLE_CATALOG_PATH, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as error:
        return [], {}, [_refusal("bad_catalog_file",
                                 "Пример каталога не прочитан: %s" % error)], notes
    notes.append("использован пример из репозитория: типовые модульные габариты "
                 "DIN 43880 без производителей и артикулов. Для проекта замените "
                 "каталог своим")
    items = [i for i in _as_list(_as_dict(document).get("items")) if isinstance(i, dict)]
    return items, {"source": _text(_as_dict(document).get("source")) or None,
                   "example": True, "path": EXAMPLE_CATALOG_PATH}, refusals, notes


# --------------------------------------------------------------------------
# подбор
# --------------------------------------------------------------------------

def matches(item: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Подходит ли позиция под запрос. Неизвестное поле считается неподходящим."""
    kinds = _as_values(query.get("kind"))
    if kinds and _text(item.get("kind")).lower() not in kinds:
        return False

    poles = query.get("poles")
    if poles is not None:
        allowed = {_num(value) for value in (poles if isinstance(poles, (list, tuple)) else [poles])}
        allowed.discard(None)
        if allowed and _num(item.get("poles")) not in allowed:
            return False

    rating = _num(item.get("rating_a"))
    low = _num(query.get("rating_min_a"))
    high = _num(query.get("rating_max_a"))
    if low is not None and (rating is None or rating < low):
        return False
    if high is not None and (rating is None or rating > high):
        return False

    curves = _as_values(query.get("trip_curve"))
    if curves:
        curve = _text(item.get("trip_curve")).lower()
        if not curve or curve not in curves:
            return False

    breaking = _num(query.get("breaking_ka_min"))
    if breaking is not None:
        value = _num(item.get("breaking_ka"))
        if value is None or value < breaking:
            return False

    leakage = _num(query.get("leakage_ma"))
    if leakage is not None and _num(item.get("leakage_ma")) != leakage:
        return False

    rcd_types = _as_values(query.get("rcd_type"))
    if rcd_types:
        value = _text(item.get("rcd_type")).lower()
        if not value or value not in rcd_types:
            return False

    mounts = _as_values(query.get("mount"))
    if mounts and _text(item.get("mount")).lower() not in mounts:
        return False

    for key, field in (("width_max_mm", "width_mm"), ("height_max_mm", "height_mm"),
                       ("depth_max_mm", "depth_mm")):
        limit = _num(query.get(key))
        if limit is not None:
            value = _num(item.get(field))
            if value is None or value > limit:
                return False

    for key in ("manufacturer", "series"):
        wanted = _as_values(query.get(key))
        if wanted and _text(item.get(key)).lower() not in wanted:
            return False

    modules_min = _num(query.get("modules_min"))
    if modules_min is not None:
        value = _num(item.get("modules"))
        if value is None or value < modules_min:
            return False

    wanted_rows = _num(query.get("rows"))
    if wanted_rows is not None and _num(item.get("rows")) != wanted_rows:
        return False

    wanted_ip = _text(query.get("ip")).upper()
    if wanted_ip and _text(item.get("ip")).upper() != wanted_ip:
        return False

    for key in ("mounting_plate", "din_rail"):
        wanted = query.get(key)
        if isinstance(wanted, bool) and item.get(key) is not wanted:
            return False

    text = _text(query.get("text")).lower()
    if text:
        haystack = " ".join(_text(item.get(field)).lower()
                            for field in ("id", "kind", "label", "designation",
                                          "manufacturer", "series"))
        if text not in haystack:
            return False
    return True


def item_to_layout(item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Запись библиотеки раскладки из позиции каталога.

    Возвращает ``(None, причина)``, если габарита нет: подставлять типовой
    размер здесь нельзя — раскладка станет правдоподобной и неверной.
    """
    fields = (ENCLOSURE_GEOMETRY_FIELDS
              if _text(item.get("kind")).lower() == KIND_ENCLOSURE
              else GEOMETRY_FIELDS)
    missing = [field for field in fields if _num(item.get(field)) is None]
    if missing:
        return None, "no_geometry:" + ",".join(missing)
    if _text(item.get("kind")).lower() == KIND_ENCLOSURE:
        # Корпус не ставится на плату — он сам задаёт плату. Как позиция
        # раскладки он не имеет смысла, поэтому layout для него не строится.
        return None, "enclosure_is_not_a_position"
    entry = {
        "lib_key": _text(item.get("id")) or _text(item.get("label")),
        "name": _text(item.get("label")) or _text(item.get("id")),
        "width_mm": _num(item.get("width_mm")),
        "height_mm": _num(item.get("height_mm")),
    }
    offset = _num(item.get("rail_offset_mm"))
    if offset is not None:
        entry["rail_offset_mm"] = offset
    depth = _num(item.get("depth_mm"))
    if depth is not None:
        entry["depth_mm"] = depth
    return entry, None


def search(payload: Any) -> Dict[str, Any]:
    """Подбор оборудования по параметрам."""
    data = _as_dict(payload)
    query = _as_dict(data.get("query")) or data
    items, meta, refusals, notes = load_catalog(data)
    if refusals:
        return {
            "ok": False, "verdict": "refused", "query": query, "count": 0,
            "items": [], "unverified": [], "refusals": refusals,
            "notes": notes, "provenance": [], "example": False,
            "limits": list(LIMITS),
        }
    if not items:
        return {
            "ok": False, "verdict": "refused", "query": query, "count": 0,
            "items": [], "unverified": [],
            "refusals": [_refusal("empty_catalog", "В каталоге нет ни одной позиции.")],
            "notes": notes, "provenance": [], "example": bool(meta.get("example")),
            "limits": list(LIMITS),
        }

    need_geometry = bool(data.get("need_geometry"))
    found: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    for item in items:
        if not matches(item, query):
            continue
        layout, reason = item_to_layout(item)
        is_enclosure = _text(item.get("kind")).lower() == KIND_ENCLOSURE
        plate = cabinet_enclosure.plate_of(item) if is_enclosure else None
        row = {
            "id": _text(item.get("id")) or _text(item.get("article")) or None,
            "article": _text(item.get("article")) or None,
            "kind": _text(item.get("kind")) or None,
            "label": _text(item.get("label")) or None,
            "manufacturer": _text(item.get("manufacturer")) or None,
            "series": _text(item.get("series")) or None,
            "poles": _num(item.get("poles")),
            "rating_a": _num(item.get("rating_a")),
            "trip_curve": _text(item.get("trip_curve")) or None,
            "breaking_ka": _num(item.get("breaking_ka")),
            "leakage_ma": _num(item.get("leakage_ma")),
            "rcd_type": _text(item.get("rcd_type")) or None,
            "mount": _text(item.get("mount")) or None,
            "width_mm": _num(item.get("width_mm")),
            "height_mm": _num(item.get("height_mm")),
            "depth_mm": _num(item.get("depth_mm")),
            "rail_offset_mm": _num(item.get("rail_offset_mm")),
            "source": _text(item.get("source")) or meta.get("source"),
            "layout": layout,
            "ip": _text(item.get("ip")) or None,
            "modules": _num(item.get("modules")),
            "rows": _num(item.get("rows")),
            "material": _text(item.get("material")) or None,
            "mounting_plate": item.get("mounting_plate"),
            "din_rail": item.get("din_rail"),
            "plate": plate,
        }
        if is_enclosure:
            # У корпуса вместо позиции раскладки — монтажная плата. Её ширина
            # расчётная, поэтому отсутствие числа модулей честнее отказ, чем
            # подстановка «типовой» ширины.
            if plate is not None and plate["width_mm"] is None:
                row["unverified_reason"] = "no_plate:" + (plate["reason"] or "unknown")
            if plate is not None and plate["conflict"]:
                row["unverified_reason"] = "conflict:" + plate["conflict"]
            if need_geometry and (plate is None or plate["width_mm"] is None
                                  or plate["conflict"]):
                unverified.append({"id": row["id"], "reason": row.get("unverified_reason")})
            else:
                found.append(row)
            continue
        if layout is None:
            row["unverified_reason"] = reason
        if need_geometry and layout is None:
            unverified.append({"id": row["id"], "reason": reason})
        else:
            found.append(row)

    found.sort(key=lambda row: (row["rating_a"] if row["rating_a"] is not None else 1e9,
                                row["modules"] if row["modules"] is not None else 1e9,
                                row["width_mm"] if row["width_mm"] is not None else 1e9,
                                row["id"] or ""))
    limit = int(_num(data.get("limit"), 20.0) or 20.0)
    total = len(found)
    truncated = total > max(limit, 0)
    if limit > 0:
        found = found[:limit]

    provenance: List[Dict[str, Any]] = []
    if meta.get("source"):
        provenance.append({"catalog": meta.get("path") or "catalog",
                           "source": meta.get("source")})
    elif not notes:
        notes.append("каталог передан без source: источник данных неизвестен")

    return {
        "ok": True,
        "verdict": "computed_with_gaps" if unverified else "computed",
        "query": query,
        "count": len(found),
        "total_matched": total,
        "truncated": truncated,
        "items": found,
        "unverified": unverified,
        "refusals": [],
        "notes": notes,
        "provenance": provenance,
        "example": bool(meta.get("example")),
        "kinds": sorted({_text(item.get("kind")).lower()
                         for item in items if _text(item.get("kind"))}),
        "limits": list(LIMITS),
    }
