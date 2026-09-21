"""План материализации раскладки щита в КОМПАС — чистые функции, без COM.

Волна 8 считала **координаты**. Эта волна превращает их в **последовательность
вызовов моста**. Модуль ничего не выполняет: он отдаёт план, который можно
прочитать, проверить и только потом исполнить.

Два вида источника геометрии:

- ``primitive`` — габаритный блок, строится на месте через
  ``kompas_part_create`` + ``kompas_part_add_primitive``. Работает без библиотеки
  оборудования: тело получается по подтверждённому габариту из библиотеки.
- ``fragment`` — готовый ``.m3d`` по полю ``fragment_ref``. Предпочтителен, но
  требует библиотеки фрагментов; пока её нет, поле остаётся пустым.

План от вида источника не зависит: координаты, разбиение сетов, порядок вызовов
и маркировка считаются одинаково. Появится библиотека — заменится только
источник тела.

Что модуль **не** делает и не делает принципиально:

- не угадывает индексы экземпляров в дереве сборки. После вставки индекс
  известен только из ответа КОМПАСа, поэтому план ссылается на результат
  предыдущего шага символически, а не числом;
- не маркирует каждый экземпляр сета — только исходный. Маркировка двадцати
  четырёх клемм потребовала бы двадцать четыре вызова с числовыми индексами,
  которых у нас нет;
- не проверяет электрику и не трассирует проводку.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import cabinet_layout as cl

# Ограничения инструментов моста, а не ours прихоть:
# kompas_component_pattern_linear принимает count 2..20.
MIN_PATTERN_COUNT = 2
MAX_PATTERN_COUNT = 20

DEFAULT_DEPTH_MM = 60.0
DEFAULT_PLANE_Z_MM = 0.0
BODY_PRIMITIVE = "primitive"
BODY_FRAGMENT = "fragment"

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


# --------------------------------------------------------------------------
# разбор и утилиты
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


def slug(key: str) -> str:
    """Имя файла из ключа библиотеки: только латиница, цифры, подчёркивание.

    Ключи бывают кириллическими и с пробелами, а имя файла пойдёт в КОМПАС.
    """
    cleaned = _UNSAFE.sub("_", _text(key)).strip("_")
    return cleaned or "part"


def part_filename(lib_key: str) -> str:
    """Имя детали по правилам моста: basename, ``_AGENT_COPY``, ``.m3d``."""
    return "%s_AGENT_COPY.m3d" % slug(lib_key)


def is_agent_copy(name: str) -> bool:
    return "_agent_copy" in _text(name).lower()


def split_count(count: int, max_count: int = MAX_PATTERN_COUNT) -> List[int]:
    """Разбить партию на части, каждую из которых примет один паттерн.

    Паттерн моста принимает ``count`` от 2 до 20, поэтому сет из 24 клемм одним
    вызовом не собрать. Режем на равные части — так проще считать смещение
    каждой части вдоль рейки.
    """
    count = int(count)
    if count <= 0:
        return []
    if count <= max_count:
        return [count]
    parts = -(-count // max_count)          # округление вверх
    base, rest = divmod(count, parts)
    return [base + (1 if i < rest else 0) for i in range(parts)]


def set_step_mm(width_mm: float, internal_gap_mm: float) -> float:
    """Шаг между экземплярами сета: ширина детали плюс внутренний зазор."""
    return float(width_mm) + (float(internal_gap_mm) or 0.0)


def origin_cad(x_mm: float, y_mm: float, width_mm: float, height_mm: float,
               plate_height_mm: float, z_mm: float) -> List[float]:
    """Центр детали в системе CAD.

    Раскладка живёт в координатах «сверху слева, +y вниз», КОМПАС — обычной
    системой. Переворот делает ``cabinet_layout.to_cad`` и только он: ручной
    арифметики здесь нет, иначе две функции начнут расходиться.
    """
    box = cl.placed_box(x_mm, y_mm, width_mm, height_mm, 0.0)
    cad = cl.to_cad(box, plate_height_mm)
    return [round(cad["x"] + width_mm / 2.0, 6),
            round(cad["y"] + height_mm / 2.0, 6),
            round(z_mm, 6)]


# --------------------------------------------------------------------------
# источник тела
# --------------------------------------------------------------------------

def body_source(item: Dict[str, Any], mode: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Определить источник геометрии позиции.

    Возвращает ``(вид, имя файла, причина)``. Причина заполнена, если тело
    получить нельзя: такая позиция уходит в ``unresolved``, а не в план.
    """
    key = _text(item.get("lib_key"))
    width = _number(item.get("width_mm"), 0.0) or 0.0
    height = _number(item.get("height_mm"), 0.0) or 0.0

    if mode == BODY_FRAGMENT:
        ref = _text(item.get("fragment_ref"))
        if not ref:
            return None, None, "no_fragment_ref"
        if not is_agent_copy(ref):
            return None, None, "fragment_ref_not_agent_copy"
        return BODY_FRAGMENT, ref, None

    if width <= 0 or height <= 0:
        return None, None, "unconfirmed_size"
    return BODY_PRIMITIVE, part_filename(key), None


# --------------------------------------------------------------------------
# план
# --------------------------------------------------------------------------

def _step(seq: int, stage: str, tool: str, action: str, params: Dict[str, Any],
          why: str, critical: bool = True, automated: bool = True) -> Dict[str, Any]:
    """Один шаг плана.

    ``automated=False`` означает, что исполнитель этот шаг **не** выполнит:
    у инструмента другой контракт (не ``session``), поэтому он остаётся
    рекомендацией оператору. Притворяться, будто мы его вызвали, нельзя.
    """
    return {"seq": seq, "stage": stage, "tool": tool, "action": action,
            "params": params, "why": why, "critical": critical,
            "automated": automated}


def build_plan(payload: Any) -> Dict[str, Any]:
    """Собрать план материализации. Ничего не исполняет и не открывает."""
    data = _as_dict(payload)
    assembly = _text(data.get("assembly_filename"))
    mode = _text(data.get("body")) or BODY_PRIMITIVE
    if mode not in (BODY_PRIMITIVE, BODY_FRAGMENT):
        mode = BODY_PRIMITIVE

    layout = _as_dict(data.get("layout"))
    plate = _as_dict(layout.get("plate"))
    plate_height = _number(plate.get("height_mm"), 0.0) or 0.0

    mount = _as_dict(data.get("mount"))
    depth = _number(mount.get("depth_mm"), DEFAULT_DEPTH_MM) or DEFAULT_DEPTH_MM
    plane_z = _number(mount.get("plane_z_mm"), DEFAULT_PLANE_Z_MM) or 0.0
    z_centre = plane_z + depth / 2.0

    options = _as_dict(data.get("options"))
    want_checkpoint = bool(options.get("checkpoint", True))
    want_verify = bool(options.get("verify", True))
    want_marks = bool(options.get("marks", True))

    library: Dict[str, Dict[str, Any]] = {}
    for raw in _as_list(layout.get("library")):
        item = _as_dict(raw)
        key = _text(item.get("lib_key"))
        if key:
            library[key] = item

    steps: List[Dict[str, Any]] = []
    bodies: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    seen_bodies: Dict[str, Dict[str, Any]] = {}
    seq = 0

    def push(stage, tool, action, params, why, critical=True, automated=True):
        """Добавить шаг и вернуть его номер.

        Номер возвращается потому, что следующие шаги ссылаются на результат
        именно вставки. Считать «предыдущий шаг» как ``seq - 1`` нельзя: между
        вставкой и трансформом ссылка на один шаг назад даёт создание тела.
        """
        nonlocal seq
        seq += 1
        steps.append(_step(seq, stage, tool, action, params, why, critical, automated))
        return seq

    if not assembly:
        unresolved.append({"id": None, "reason": "no_assembly_filename"})

    # --- чекпоинт: точка отката до первой записи -------------------------
    # Не автоматизируется: manage_checkpoints принимает bridge_root, а не
    # сессию, и живёт в другой части моста. Шаг остаётся рекомендацией.
    if assembly and want_checkpoint:
        push("checkpoint", "kompas_checkpoints", "checkpoint.manage",
             {"target": assembly, "operation": "create"},
             "снимок сборки до изменений: материализация пишет в существующий "
             "файл; выполнить отдельным вызовом до запуска плана",
             automated=False)

    # --- тела: один файл на используемый ключ, а не на позицию ----------
    # Тела строятся только для того, что реально стоит в раскладке: библиотека
    # может содержать сотни позиций, а в щите — пять. Создавать пятьсот
    # файлов ради пяти — не план, а мусор.
    elements = _as_list(layout.get("elements"))
    groups = _as_list(layout.get("groups"))
    targets = ([("element", e) for e in elements]
               + [("group", g) for g in groups])

    used_keys = set()
    for _kind, obj in targets:
        key = _text(_as_dict(obj).get("lib_key"))
        if key:
            used_keys.add(key)

    for key in sorted(used_keys):
        item = library.get(key)
        if item is None:
            continue
        kind, filename, reason = body_source(item, mode)
        if kind is None:
            unresolved.append({"id": key, "reason": reason})
            continue
        if filename in seen_bodies:
            continue
        rec = {"lib_key": key, "filename": filename, "source": kind,
               "width_mm": _number(item.get("width_mm")),
               "height_mm": _number(item.get("height_mm"))}
        if kind == BODY_PRIMITIVE:
            push("body", "kompas_part_create", "part.create",
                 {"filename": filename},
                 "деталь %s: пустой .m3d под габаритный блок" % key)
            push("body", "kompas_part_add_primitive", "part.primitive",
                 {"filename": filename, "kind": "block",
                  "width_mm": rec["width_mm"], "height_mm": rec["height_mm"],
                  "depth_mm": depth},
                 "тело %s по подтверждённому габариту %.4g x %.4g x %.4g мм"
                 % (key, rec["width_mm"], rec["height_mm"], depth))
        seen_bodies[filename] = rec
        bodies.append(rec)

    # --- размещение: одиночные позиции и сеты ----------------------------
    placements = 0
    patterns = 0
    marks = 0

    for kind, obj in targets:
        if not assembly:
            # Вставлять некуда: шаги размещения гарантированно провалятся.
            # Тела при этом создать можно — они от сборки не зависят.
            break
        obj = _as_dict(obj)
        ident = _text(obj.get("id"))
        key = _text(obj.get("lib_key"))
        item = library.get(key)
        if item is None:
            unresolved.append({"id": ident, "reason": "unknown_lib_key"})
            continue
        rec = seen_bodies.get(part_filename(key) if mode == BODY_PRIMITIVE
                              else _text(item.get("fragment_ref")))
        if rec is None:
            kind_src, _fn, reason = body_source(item, mode)
            unresolved.append({"id": ident, "reason": reason or "no_body"})
            continue

        width = _number(item.get("width_mm"), 0.0) or 0.0
        height = _number(item.get("height_mm"), 0.0) or 0.0
        x0 = _number(obj.get("x_mm"), 0.0) or 0.0
        y0 = _number(obj.get("y_mm"), 0.0) or 0.0

        if kind == "element":
            parts = [1]
            gap = 0.0
            tag = _text(obj.get("tag"))
        else:
            count = int(_number(obj.get("count"), 0.0) or 0.0)
            if count <= 0:
                unresolved.append({"id": ident, "reason": "empty_set"})
                continue
            parts = split_count(count)
            gap = _number(obj.get("internal_gap_mm"), 0.0) or 0.0
            tag = _text(obj.get("tag_start"))

        step_mm = set_step_mm(width, gap)
        offset = 0.0
        for index, part_count in enumerate(parts):
            x_mm = x0 + offset
            allow_duplicate = index > 0
            insert_seq = push("insert", "kompas_assembly_insert_component",
                              "assembly.insert_component",
                              {"assembly_filename": assembly,
                               "component_filename": rec["filename"],
                               "allow_duplicate": allow_duplicate},
                              "%s: вставка %s%s"
                              % (ident, rec["filename"],
                                 " (повторный экземпляр)" if allow_duplicate else ""),
                              critical=index == 0)

            push("place", "kompas_component_transform", "component.transform",
                 {"assembly_filename": assembly,
                  "selector": {"from_step": insert_seq, "field": "component_index"},
                  "origin": origin_cad(x_mm, y0, width, height, plate_height, z_centre),
                  "axis_x": [1, 0, 0], "axis_y": [0, 1, 0], "axis_z": [0, 0, 1],
                  "mode": "absolute"},
                 "%s: центр на %.4g, %.4g (CAD), рейка по Y" % (ident, x_mm, y0))
            placements += 1

            if part_count >= MIN_PATTERN_COUNT:
                push("pattern", "kompas_component_pattern_linear",
                     "component.pattern_linear",
                     {"assembly_filename": assembly,
                      "source_selector": {"from_step": insert_seq,
                                          "field": "component_index"},
                      "direction": [1, 0, 0], "spacing": round(step_mm, 6),
                      "count": part_count},
                     "%s: %d экземпляров с шагом %.4g мм" % (ident, part_count, step_mm))
                patterns += 1

            if want_marks and tag and index == 0:
                push("mark", "kompas_component_properties_set",
                     "component.properties_set",
                     {"assembly_filename": assembly,
                      "selector": {"from_step": insert_seq, "field": "component_index"},
                      "designation": tag, "name": _text(item.get("name")) or key},
                     "%s: позиционное обозначение %s" % (ident, tag),
                     critical=False)
                marks += 1

            offset += part_count * step_mm

    # --- проверка после записи -------------------------------------------
    # Тоже не автоматизируется: model_bbox и model_components принимают только
    # session, а снимок — root. Все три вызываются отдельно, уже по факту.
    if assembly and want_verify:
        push("verify", "kompas_model_components", "model.components", {},
             "перечень компонентов: число позиций против плана",
             critical=False, automated=False)
        push("verify", "kompas_model_bbox", "model.bbox", {},
             "габарит сборки против панели", critical=False, automated=False)
        push("verify", "kompas_viewport_capture", "viewport.capture", {},
             "снимок видового экрана для визуальной приёмки",
             critical=False, automated=False)

    return {
        "ok": not unresolved,
        "assembly_filename": assembly or None,
        "body": mode,
        "steps": steps,
        "bodies": bodies,
        "unresolved": unresolved,
        "summary": {"steps": len(steps), "bodies": len(bodies),
                    "placements": placements, "patterns": patterns,
                    "marks": marks, "unresolved": len(unresolved)},
        "conventions": {
            "units": "mm",
            "origin": "центр детали в системе CAD; переворот y делает to_cad()",
            "z": "центр по глубине: plane_z_mm + depth_mm/2, блок центрирован",
            "pattern_limit": "count %d..%d — сеты больше %d режутся на части"
                             % (MIN_PATTERN_COUNT, MAX_PATTERN_COUNT, MAX_PATTERN_COUNT),
        },
        "limits": [
            "План ссылается на экземпляры символически ({from_step, field}): "
            "числовой индекс компонента известен только из ответа КОМПАСа "
            "после вставки, угадывать его нельзя.",
            "Маркируется только исходный экземпляр сета. Позиционные "
            "обозначения каждого экземпляра требуют подтверждённой нумерации "
            "в дереве сборки.",
            "Режим primitive строит габаритный блок по размеру из библиотеки: "
            "это не модель изделия, а занимаемый объём.",
            "План не считает электрику, тепловой режим и трассировку.",
        ],
    }
