"""Экспорт из КОМПАСа с обязательной проверкой результата.

Зачем этот модуль существует
---------------------------
Экспорт - единственная операция моста, результат которой уходит наружу,
и при этом самая лживая: COM-метод возвращает управление без исключения
даже тогда, когда файл не создан, пуст или обрезан. Волна 2 дала проверку
артефактов (`kompas_artifact_check`), но проверять приходилось вручную
и отдельным вызовом - то есть «экспорт выполнен» и «файл годен» оставались
двумя разными событиями.

Здесь они объединены: экспорт считается успешным только если файл прошёл
проверку по содержимому.

Границы честности
-----------------
Интерфейс экспорта в КОМПАСе не подтверждён на стенде, поэтому:

* методы перебираются из списка известных вариантов, и фактически
  сработавший путь возвращается в ответе (`interface.method`);
* `SaveAs` в перечень **не** включён намеренно: он сохраняет сам документ,
  а не выгружает его копию, и ошибка в аргументах может перезаписать модель;
* если файл не прошёл проверку, операция считается неудачной
  (`ok: false`), но файл не удаляется - его нужно посмотреть;
* `apply=false` - сухой прогон: ни одного обращения к COM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from artifact_check import artifact_check, detect_kind
from settings import settings_for

SUPPORTED_FORMATS: Dict[str, Dict[str, Any]] = {
    "step": {"suffixes": (".step", ".stp"), "needs_3d": True, "label": "STEP AP214/AP242"},
    "dxf": {"suffixes": (".dxf",), "needs_3d": False, "label": "DXF"},
    "pdf": {"suffixes": (".pdf",), "needs_3d": False, "label": "PDF"},
}

# Кандидаты на метод экспорта. Общие (ExportToFile, Export) идут последними:
# специфичные методы точнее по смыслу аргументов. SaveAs исключён намеренно.
EXPORT_METHODS: Dict[str, Tuple[str, ...]] = {
    "step": ("ExportToStep", "ExportSTEP", "ExportToFile", "Export"),
    "dxf": ("ExportToDxf", "ExportDXF", "ExportToFile", "Export"),
    "pdf": ("ExportToPdf", "ExportPDF", "ExportToFile", "Export"),
}

MAX_NAME_LENGTH = 120


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Прочитать свойство: для COM-свойств вызов без аргументов - норма."""
    try:
        value = getattr(obj, name)
        return value() if callable(value) else value
    except Exception:
        return default


def _method(obj: Any, name: str) -> Any:
    """Получить вызываемый метод, НЕ вызывая его.

    Отдельная функция именно потому, что ``_attr`` вызывает найденное: для
    свойств это верно, а для методов вроде ``ExportToFile(path)`` приводит
    к TypeError, который молча гасится и выглядит как «метода нет».
    """
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    return value if callable(value) else None


def normalize_format(value: Any) -> str:
    fmt = str(value or "").strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            "unsupported_export_format: " + (fmt or "<empty>")
            + "; supported=" + ",".join(sorted(SUPPORTED_FORMATS))
        )
    return fmt


def validate_relative_path(value: Any, fmt: str) -> str:
    """Путь обязан быть именем файла внутри рабочего каталога моста."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("relative_path_required")
    if len(raw) > MAX_NAME_LENGTH:
        raise ValueError("relative_path_too_long")
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.name != raw:
        raise ValueError("relative_path_must_be_basename")
    suffix = candidate.suffix.lower()
    if suffix not in SUPPORTED_FORMATS[fmt]["suffixes"]:
        raise ValueError(
            f"filename_suffix_must_match_format_{fmt}: "
            + ",".join(SUPPORTED_FORMATS[fmt]["suffixes"])
        )
    return raw


def _resolve(bridge_root: Any, relative_path: str) -> Tuple[Path, Path]:
    settings = settings_for(bridge_root)
    work = settings.work_dir.resolve()
    target = (work / relative_path).resolve()
    if target.parent != work:
        raise RuntimeError("path_outside_approved_roots:" + str(target))
    if settings.is_protected(target):
        raise RuntimeError("path_protected:" + str(target))
    return work, target


def _export_call(document: Any, app: Any, target: Path, fmt: str) -> Dict[str, Any]:
    """Вызвать первый сработавший метод экспорта."""
    errors: List[str] = []
    for owner_name, owner in (("document", document), ("application", app)):
        if owner is None:
            continue
        for method_name in EXPORT_METHODS.get(fmt, ()):
            method = _method(owner, method_name)
            if method is None:
                continue
            for args in ((str(target),), (str(target), fmt)):
                try:
                    result = method(*args)
                    return {
                        "method": f"{owner_name}.{method_name}",
                        "argument_count": len(args),
                        "return": None if result is None else str(result),
                    }
                except Exception as exc:
                    errors.append(f"{owner_name}.{method_name}/{len(args)}: {exc}")
    raise RuntimeError(
        "export_interface_not_available: "
        + ("; ".join(errors[:6]) or "ни один известный метод экспорта не найден")
        + ". Запустите проверенный скрипт через kompas_run_job "
        "и зафиксируйте имя метода в EXPORT_METHODS."
    )


def export_file(
    session: Any,
    relative_path: str,
    fmt: str,
    apply: bool = False,
) -> Dict[str, Any]:
    """Экспортировать активный документ и проверить получившийся файл."""
    normalized = normalize_format(fmt)
    name = validate_relative_path(relative_path, normalized)
    root = session.root
    work, target = _resolve(root, name)

    plan: Dict[str, Any] = {
        "requested": {"relative_path": name, "format": normalized, "apply": bool(apply)},
        "target": str(target),
        "format_label": SUPPORTED_FORMATS[normalized]["label"],
        "methods_to_try": list(EXPORT_METHODS[normalized]),
        "verification": "artifact_check по содержимому файла",
        "save_as_excluded": True,
    }

    if target.exists():
        raise RuntimeError("target_already_exists:" + str(target))

    if not apply:
        plan.update({
            "dry_run": True,
            "executed": False,
            "ok": False,
            "warnings": [
                "Сухой прогон: ни одного обращения к КОМПАСу не выполнено.",
                "Имя метода экспорта будет подобрано перебором; фактически "
                "сработавший путь вернётся в поле interface.method.",
            ],
            "limits": [
                "Файл проверяется по содержимому, а не по факту вызова: "
                "пустой или обрезанный файл считается неудачей.",
                "Совпадение геометрии с моделью экспортной проверкой "
                "не подтверждается - нужен обратный импорт.",
            ],
        })
        return plan

    active_path = str(session.active_path() or "")
    if not active_path:
        raise RuntimeError("no_active_document_path")

    document = session.active()
    app = session.connect()

    if SUPPORTED_FORMATS[normalized]["needs_3d"]:
        try:
            from model_tools import _doc3d
            _doc3d(session)
        except Exception as exc:
            raise RuntimeError(
                f"active_document_is_not_3d_for_step_export: {exc}"
            ) from None

    interface = _export_call(document, app, target, normalized)

    if not target.is_file():
        raise RuntimeError(
            "export_produced_no_file: "
            f"method={interface['method']}; target={target}"
        )

    verdict = artifact_check(root, name)
    ok = bool(verdict.get("ok"))

    result: Dict[str, Any] = {
        **plan,
        "dry_run": False,
        "executed": True,
        "active_document": active_path,
        "interface": interface,
        "artifact": verdict,
        "ok": ok,
        "saved": False,
        "warnings": list(verdict.get("warnings") or []),
        "limits": [
            "Файл проверен по содержимому, но не на совпадение геометрии: "
            "для STEP это делает только обратный импорт и сверка габаритов.",
            "При ok=false файл намеренно не удаляется - его нужно посмотреть.",
        ],
    }
    if not ok:
        result["warnings"].append(
            "Экспорт завершился без исключения, но файл не прошёл проверку. "
            "Не считайте выгрузку выполненной: findings="
            + json.dumps(verdict.get("findings") or [], ensure_ascii=False)
        )
    return result


def manage_checkpoints(bridge_root: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Операции над контрольными точками: list, prune, restore."""
    from checkpoint import create as ck_create
    from checkpoint import list_checkpoints, prune, restore

    operation = str(payload.get("operation") or "").strip().lower()
    if operation == "list":
        rows = list_checkpoints(bridge_root, payload.get("target"))
        return {
            "operation": "list",
            "count": len(rows),
            "checkpoints": rows[:100],
            "read_only": True,
        }
    if operation == "prune":
        keep = payload.get("keep")
        outcome = prune(bridge_root, DEFAULT_KEEP if keep is None else int(keep))
        return {"operation": "prune", **outcome, "read_only": False}
    if operation == "restore":
        identifier = str(payload.get("id") or "").strip()
        if not identifier:
            raise ValueError("checkpoint_id_required_for_restore")
        expected = payload.get("target")
        outcome = restore(bridge_root, identifier, expected)
        return {
            "operation": "restore",
            **outcome,
            "read_only": False,
            "warnings": [
                "Файл перезаписан снимком. Если КОМПАС держит его открытым, "
                "закройте и переоткройте документ перед продолжением."
            ],
        }
    if operation == "create":
        target = str(payload.get("target") or "").strip()
        if not target:
            raise ValueError("target_required_for_create")
        resolved = _resolve(bridge_root, target)[1]
        row = ck_create(bridge_root, resolved, str(payload.get("action") or "manual"))
        return {"operation": "create", **row, "read_only": False}
    raise ValueError(
        "unsupported_checkpoint_operation: " + (operation or "<empty>")
        + "; supported=list,prune,restore,create"
    )
