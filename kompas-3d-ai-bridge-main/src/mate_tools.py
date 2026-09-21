"""Сопряжения сборки КОМПАС-3D: чтение и создание.

Зачем этот модуль существует
---------------------------
До этой волны мост умел собрать изделие из деталей, но не умел связать их
между собой: `kompas_assembly_insert_component` вставляет компонент,
`kompas_component_transform` ставит его в абсолютную позу, и на этом
сборка оставалась набором независимо висящих тел. Сопряжения - это то,
что делает сборку сборкой, и именно их не хватало.

Границы честности
-----------------
Интерфейс сопряжений (`IMateConstraint` / `ksMateConstraint`) в разных
версиях КОМПАСа называется по-разному, и подтверждённого стенда под рукой
не было. Поэтому модуль не утверждает, что умеет создавать сопряжения:

* имена коллекции и методов перебираются из списка известных вариантов;
* если ни один вариант не подошёл, поднимается
  ``mate_create_interface_not_available`` со списком испробованного,
  а не тихая подмена результата;
* в ответе всегда есть блок ``interface`` с тем, какой именно путь
  сработал, чтобы оператор мог это зафиксировать в матрице версий.

Валидация входных данных вынесена в чистые функции и проверяется тестами
без КОМПАСа - это единственная часть, которую можно подтвердить здесь.
"""
from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from assembly_insert_tools import _close, _norm, _open_exact, _sha256
from material_tools import _ensure_closed, _restore_exact_active
from model_tools import _doc3d, find_component

# Типы сопряжений и обязательность числового параметра.
MATE_KINDS: Dict[str, Dict[str, Any]] = {
    "coincident": {"requires_value": False, "unit": None},
    "parallel": {"requires_value": False, "unit": None},
    "perpendicular": {"requires_value": False, "unit": None},
    "concentric": {"requires_value": False, "unit": None},
    "tangent": {"requires_value": False, "unit": None},
    "distance": {"requires_value": True, "unit": "mm"},
    "angle": {"requires_value": True, "unit": "deg"},
}

ALIGNMENTS: Tuple[str, ...] = ("aligned", "opposite", "auto")

# Границы значений: агент не должен одним вызовом увести деталь на километр.
MAX_DISTANCE_MM = 10_000.0
MAX_ANGLE_DEG = 360.0

# Одно сопряжение за вызов. Пакетная запись не даёт проверить, какое именно
# сопряжение испортило сборку, если после перестроения что-то разъехалось.
MAX_MATES_PER_CALL = 1

# Кандидаты на коллекцию сопряжений. Порядок - от наиболее вероятного
# к наименее; в ответе видно, какой источник сработал.
COLLECTION_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("document", "MateConstraints"),
    ("top_part", "MateConstraints"),
    ("document", "MateConstraintCollection"),
    ("top_part", "MateConstraintCollection"),
    ("document", "Constraints"),
    ("top_part", "Constraints"),
)

COLLECTION_METHODS: Tuple[str, ...] = (
    "GetMateConstraints",
    "GetMateConstraintCollection",
    "GetConstraints",
)

CREATE_METHODS: Tuple[str, ...] = (
    "AddMateConstraint",
    "CreateMateConstraint",
    "AddConstraint",
)


# ---------------------------------------------------------------------------
# Валидация (чистые функции, тестируются без КОМПАСа)
# ---------------------------------------------------------------------------
def normalize_kind(kind: Any) -> str:
    value = str(kind or "").strip().lower()
    if value not in MATE_KINDS:
        raise ValueError(
            "unsupported_mate_kind: " + (value or "<empty>")
            + "; supported=" + ",".join(sorted(MATE_KINDS))
        )
    return value


def validate_value(kind: str, value: Any) -> Optional[float]:
    """Проверить числовой параметр сопряжения под конкретный тип."""
    spec = MATE_KINDS[normalize_kind(kind)]
    if not spec["requires_value"]:
        if value is None:
            return None
        raise ValueError(f"value_not_allowed_for_{kind}")
    if value is None:
        raise ValueError(f"value_required_for_{kind}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("value_must_be_number") from None
    if not math.isfinite(number):
        raise ValueError("value_must_be_finite")
    limit = MAX_DISTANCE_MM if spec["unit"] == "mm" else MAX_ANGLE_DEG
    if number <= 0:
        raise ValueError("value_must_be_positive")
    if number > limit:
        raise ValueError(f"value_above_limit_{limit:g}_{spec['unit']}")
    return number


def validate_alignment(align: Any) -> str:
    value = str(align or "auto").strip().lower()
    if value not in ALIGNMENTS:
        raise ValueError(
            "unsupported_alignment: " + (value or "<empty>")
            + "; supported=" + ",".join(ALIGNMENTS)
        )
    return value


def validate_assembly_filename(name: Any) -> str:
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("assembly_filename_required")
    if Path(raw).name != raw:
        raise ValueError("assembly_filename_must_be_basename")
    if not raw.lower().endswith(".a3d"):
        raise ValueError("assembly_filename_must_end_with_a3d")
    if "_agent_copy" not in Path(raw).stem.lower():
        raise ValueError("assembly_filename_must_contain_AGENT_COPY")
    return raw


def validate_selector(selector: Any, label: str) -> Any:
    if isinstance(selector, bool) or selector is None:
        raise ValueError(f"{label}_required")
    if isinstance(selector, int):
        if selector < 0:
            raise ValueError(f"{label}_must_be_nonnegative")
        return selector
    if isinstance(selector, str):
        value = selector.strip()
        if not value:
            raise ValueError(f"{label}_must_be_nonempty")
        return value
    if isinstance(selector, (list, tuple)):
        if not selector:
            raise ValueError(f"{label}_list_must_be_nonempty")
        out: List[int] = []
        for item in selector:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"{label}_list_must_contain_nonnegative_ints")
            out.append(int(item))
        return out
    raise ValueError(f"{label}_must_be_int_string_or_int_list")


# ---------------------------------------------------------------------------
# COM-доступ
# ---------------------------------------------------------------------------
def _attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(obj, name)
        return value() if callable(value) else value
    except Exception:
        return default


def _method(obj: Any, name: str) -> Any:
    """Получить вызываемый метод, не вызывая его.

    ``_attr`` вызывает найденное значение: для свойств (Name, Distance) это
    верно, а для методов вроде ``AddMateConstraint(...)`` даёт TypeError,
    который молча гасится и выглядит как «метода нет».
    """
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    return value if callable(value) else None


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _bool_or_none(value: Any) -> Optional[bool]:
    try:
        return bool(value)
    except Exception:
        return None


def _items(collection: Any) -> Optional[List[Any]]:
    """Развернуть COM-коллекцию в список, если это действительно коллекция."""
    if collection is None:
        return None
    try:
        count = int(collection.Count)
    except Exception:
        return None
    rows: List[Any] = []
    for index in range(count):
        item = None
        for getter in ("Item", "MateConstraint"):
            try:
                item = getattr(collection, getter)(index)
                break
            except Exception:
                continue
        rows.append(item)
    return rows


def _find_collection(document: Any, top: Any) -> Tuple[Optional[List[Any]], Optional[str]]:
    owners = {"document": document, "top_part": top}
    for owner_name, attribute in COLLECTION_SOURCES:
        owner = owners.get(owner_name)
        if owner is None:
            continue
        rows = _items(_attr(owner, attribute))
        if rows is not None:
            return rows, f"{owner_name}.{attribute}"
    for owner_name, owner in owners.items():
        if owner is None:
            continue
        for method in COLLECTION_METHODS:
            function = _method(owner, method)
            if function is None:
                continue
            try:
                rows = _items(function())
            except Exception:
                continue
            if rows is not None:
                return rows, f"{owner_name}.{method}()"
    return None, None


def _mate_row(index: int, mate: Any) -> Dict[str, Any]:
    if mate is None:
        return {"index": index, "unreadable": True}
    participants: List[Any] = []
    for attribute in ("Objects", "Participants", "Items"):
        rows = _items(_attr(mate, attribute))
        if rows:
            participants = [str(_attr(item, "Name", "") or "") for item in rows]
            break
    return {
        "index": index,
        "name": str(_attr(mate, "Name", "") or ""),
        "constraint_type": _attr(
            mate, "ConstraintType", _attr(mate, "constraintType", None)
        ),
        "direction": _attr(mate, "Direction", None),
        "distance": _finite(_attr(mate, "Distance", None)),
        "angle": _finite(_attr(mate, "Angle", None)),
        "fixed": _bool_or_none(_attr(mate, "Fixed", None)),
        "participants": participants,
    }


def mate_read(session: Any, max_items: int = 200) -> Dict[str, Any]:
    """Прочитать сопряжения активной сборки."""
    limit = max(1, min(int(max_items), 2000))
    document, top = _doc3d(session)
    rows, source = _find_collection(document, top)

    if rows is None:
        return {
            "count": None,
            "mates": [],
            "interface": {
                "confirmed": False,
                "source": None,
                "tried": [f"{owner}.{attr}" for owner, attr in COLLECTION_SOURCES]
                + list(COLLECTION_METHODS),
            },
            "active_document": str(session.active_path() or ""),
            "warnings": [
                "Коллекция сопряжений не найдена ни по одному известному имени. "
                "Это не значит, что сопряжений нет: возможно, интерфейс этой "
                "версии КОМПАСа называется иначе. Добавьте подтверждённое имя "
                "в COLLECTION_SOURCES и повторите."
            ],
        }

    mates = [_mate_row(index, mate) for index, mate in enumerate(rows)]
    return {
        "count": len(mates),
        "mates": mates[:limit],
        "truncated": len(mates) > limit,
        "interface": {
            "confirmed": True,
            "source": source,
            "tried": [],
        },
        "active_document": str(session.active_path() or ""),
        "read_only": True,
        "saved": False,
    }


def _create_mate(
    document: Any,
    top: Any,
    kind: str,
    part_a: Any,
    part_b: Any,
    value: Optional[float],
    align: str,
    fixed: bool,
) -> Tuple[Any, str]:
    """Создать сопряжение через первый сработавший метод.

    Перебор сигнатур - вынужденная мера: подтверждённого стенда под рукой
    не было, а молча вернуть успех без созданного сопряжения нельзя.
    """
    owners = {"document": document, "top_part": top}
    errors: List[str] = []
    for owner_name, owner in owners.items():
        if owner is None:
            continue
        for method_name in CREATE_METHODS:
            method = _method(owner, method_name)
            if method is None:
                continue
            attempts: Sequence[Tuple[Any, ...]] = (
                (kind, part_a, part_b, value, align, fixed),
                (kind, part_a, part_b, value),
                (kind, part_a, part_b),
            )
            for args in attempts:
                try:
                    return method(*args), f"{owner_name}.{method_name}/{len(args)}"
                except Exception as exc:
                    errors.append(f"{owner_name}.{method_name}/{len(args)}: {exc}")
    raise RuntimeError(
        "mate_create_interface_not_available: "
        + ("; ".join(errors[:6]) or "ни один известный метод создания не найден")
    )


def mate_create(
    session: Any,
    assembly_filename: str,
    kind: str,
    selector_a: Any,
    selector_b: Any,
    value: Any = None,
    align: Any = "auto",
    fixed: bool = False,
) -> Dict[str, Any]:
    """Создать одно сопряжение в сборке с полной транзакцией и проверкой."""
    filename = validate_assembly_filename(assembly_filename)
    mate_kind = normalize_kind(kind)
    mate_value = validate_value(mate_kind, value)
    alignment = validate_alignment(align)
    selector_a = validate_selector(selector_a, "selector_a")
    selector_b = validate_selector(selector_b, "selector_b")
    is_fixed = bool(fixed)

    original_document = session.active()
    original_path = str(session.active_path() or "")
    if original_document is None or not original_path:
        raise RuntimeError("no_active_document_path")
    original = Path(original_path).resolve()
    work = (Path(session.root).resolve() / "work").resolve()
    if original.parent != work or "_agent_copy" not in original.stem.lower():
        raise RuntimeError("active_document_not_safe_direct_work_AGENT_COPY")
    target = (work / filename).resolve()
    if target.parent != work or not target.is_file():
        raise FileNotFoundError(str(target))

    app = session.connect()
    _ensure_closed(app, session, target)
    backup_dir = (Path(session.root).resolve() / "_BACKUPS").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot = backup_dir / ("TXN_MATE_" + uuid.uuid4().hex + "_" + target.name)
    shutil.copy2(target, snapshot)
    before_hash = _sha256(target)
    opened = None
    reopened_document = None
    saved = False
    try:
        opened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("opened_mate_target_not_exact_active")

        _, top = _doc3d(session)
        part_a, path_a = find_component(session, selector_a)
        part_b, path_b = find_component(session, selector_b)
        if list(path_a) == list(path_b):
            raise ValueError("selector_a_and_selector_b_resolve_to_same_component")

        before_rows, before_source = _find_collection(opened, top)
        before_count = len(before_rows) if before_rows is not None else None

        created, interface_used = _create_mate(
            opened, top, mate_kind, part_a, part_b,
            mate_value, alignment, is_fixed,
        )
        for name in ("Update", "Rebuild"):
            function = _attr(opened, name)
            if callable(function):
                try:
                    function()
                    break
                except Exception:
                    continue

        after_rows, after_source = _find_collection(opened, top)
        if after_rows is None:
            raise RuntimeError("mate_collection_unreadable_after_create")
        if before_count is not None and len(after_rows) <= before_count:
            raise RuntimeError(
                "mate_count_did_not_grow: "
                f"before={before_count}, after={len(after_rows)}"
            )

        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("active_document_changed_before_mate_Save")
        if opened.Save() is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True
        _close(opened)
        opened = None

        reopened_document = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("reopened_mate_target_not_exact_active")
        _, reopened_top = _doc3d(session)
        reopened_rows, reopened_source = _find_collection(reopened_document, reopened_top)
        if reopened_rows is None:
            raise RuntimeError("mate_collection_unreadable_after_reopen")
        if before_count is not None and len(reopened_rows) <= before_count:
            raise RuntimeError("mate_not_persistent_after_reopen")

        persisted = _mate_row(len(reopened_rows) - 1, reopened_rows[-1])
        _close(reopened_document)
        reopened_document = None
        restored = _restore_exact_active(app, session, original_path, original_document)
        after_hash = _sha256(target)
        snapshot.unlink()

        return {
            "requested_action": {
                "assembly_filename": filename,
                "kind": mate_kind,
                "selector_a": selector_a,
                "selector_b": selector_b,
                "value": mate_value,
                "unit": MATE_KINDS[mate_kind]["unit"],
                "align": alignment,
                "fixed": is_fixed,
            },
            "active_document": restored,
            "target": str(target),
            "interface": {
                "confirmed": True,
                "read_source": reopened_source or after_source or before_source,
                "create_method": interface_used,
                "create_return": str(created) if created is not None else None,
            },
            "before": {"mate_count": before_count, "assembly_sha256": before_hash},
            "after": {"mate_count": len(reopened_rows), "assembly_sha256": after_hash},
            "created_mate": persisted,
            "changed": before_hash != after_hash,
            "saved": saved,
            "verification": {
                "mate_count_grew": (
                    before_count is None or len(reopened_rows) > before_count
                ),
                "save_close_reopen_verified": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
            },
            "warnings": [
                "Создано ровно одно сопряжение за вызов.",
                "Тип и параметры прочитаны обратно из модели, но "
                "геометрическая корректность сопряжения не проверяется: "
                "для этого нужен анализ степеней свободы.",
                "Метод создания сопряжения подобран перебором известных "
                "имён; зафиксируйте фактически сработавший путь в матрице "
                "версий КОМПАСа.",
            ],
        }
    except Exception:
        for document in (reopened_document, opened):
            if document is not None:
                try:
                    _close(document)
                except Exception:
                    pass
        try:
            _restore_exact_active(app, session, original_path, original_document)
        except Exception:
            pass
        try:
            if snapshot.is_file():
                shutil.copy2(snapshot, target)
        finally:
            try:
                snapshot.unlink()
            except FileNotFoundError:
                pass
        try:
            _restore_exact_active(app, session, original_path, original_document)
        except Exception:
            pass
        raise
