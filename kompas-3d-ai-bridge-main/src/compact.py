"""Компактные ответы и постраничная выдача больших структур.

Зачем этот модуль существует
---------------------------
``kompas_component_topology`` по умолчанию может вернуть до 5000 граней,
10 000 рёбер и 10 000 вершин; ``kompas_model_tree`` - дерево до глубины 12;
``kompas_pmi_read`` - до 2000 размеров. Всё это сериализовалось с ``indent=2``
и целиком уходило в контекст модели. Один вызов «посмотреть топологию сборки»
был способен съесть контекстное окно, после чего агент начинал терять задачу.

Теперь работает так:

* ответ меньше порога - уходит как есть, поведение не меняется;
* ответ больше порога - длинные списки усекаются, полный дамп пишется
  в ``runtime/artifacts/<id>.json``, а в ответ добавляется блок ``_compact``
  с числами и путём к артефакту, чтобы агент мог прочитать детали по требованию.

Усечение никогда не скрывает факт усечения: счётчики и путь к артефакту всегда
на месте. Это важно - агент не должен принимать частичные данные за полные.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

CURSOR_PREFIX = "c:"
ARTIFACT_SUBDIR = ("runtime", "artifacts")


def encode_cursor(offset: int) -> str:
    """Непрозрачный курсор продолжения выдачи."""
    raw = f"{max(0, int(offset))}".encode("ascii")
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: Optional[str]) -> int:
    """Разобрать курсор. Невалидный курсор трактуется как начало выдачи."""
    if not cursor:
        return 0
    text = str(cursor)
    if not text.startswith(CURSOR_PREFIX):
        return 0
    payload = text[len(CURSOR_PREFIX):]
    padding = "=" * (-len(payload) % 4)
    try:
        value = int(base64.urlsafe_b64decode(payload + padding).decode("ascii"))
    except Exception:
        return 0
    return max(0, value)


def paginate(items: Sequence[Any], limit: int = 200, cursor: Optional[str] = None) -> Dict[str, Any]:
    """Отдать одну страницу списка вместе со счётчиками и курсором продолжения."""
    safe_limit = max(1, int(limit))
    total = len(items)
    start = decode_cursor(cursor)
    if start >= total:
        return {
            "items": [],
            "returned": 0,
            "total": total,
            "offset": start,
            "next_cursor": None,
            "truncated": False,
        }
    page = list(items[start:start + safe_limit])
    next_offset = start + len(page)
    has_more = next_offset < total
    return {
        "items": page,
        "returned": len(page),
        "total": total,
        "offset": start,
        "next_cursor": encode_cursor(next_offset) if has_more else None,
        "truncated": has_more,
    }


def serialized_size(payload: Any) -> int:
    """Размер ответа в байтах UTF-8. Ошибка сериализации трактуется как «огромный»."""
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 10 ** 9


def artifact_dir(bridge_root: Any) -> Path:
    path = Path(str(bridge_root)).joinpath(*ARTIFACT_SUBDIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _truncate_list(value: List[Any], max_items: int) -> Tuple[List[Any], bool]:
    if len(value) <= max_items:
        return value, False
    return value[:max_items], True


def compact_payload(
    result: Any,
    *,
    bridge_root: Any,
    request_id: str,
    threshold: int = 60_000,
    max_items: int = 200,
) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """Ужать ответ инструмента, если он превышает порог.

    Возвращает ``(ответ, метаданные)``. Метаданные ``None``, если ужимать не нужно.
    """
    size = serialized_size(result)
    if size <= threshold or not isinstance(result, Mapping):
        return result, None

    compacted: Dict[str, Any] = {}
    truncated: Dict[str, Dict[str, Any]] = {}

    for key, value in result.items():
        if isinstance(value, list) and value and isinstance(value[0], (dict, list, str, int, float)):
            page, was_truncated = _truncate_list(value, max_items)
            compacted[key] = page
            if was_truncated:
                truncated[key] = {
                    "returned": len(page),
                    "total": len(value),
                    "next_cursor": encode_cursor(len(page)),
                }
        else:
            compacted[key] = value

    target = artifact_dir(bridge_root) / f"{request_id}.json"
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        artifact_path: Optional[str] = str(target)
    except OSError:
        artifact_path = None

    meta: Dict[str, Any] = {
        "reason": "response_exceeds_threshold",
        "original_bytes": size,
        "threshold_bytes": threshold,
        "truncated_lists": truncated,
        "artifact": artifact_path,
        "hint": (
            "Полный ответ записан в артефакт. Прочитайте файл целиком, если нужны "
            "все элементы; для постраничного чтения передайте next_cursor из "
            "truncated_lists. Не считайте полученные списки полными."
            if artifact_path else
            "Полный ответ превысил порог и не поместился в артефакт. Запросите данные "
            "меньшими порциями. Не считайте полученные списки полными."
        ),
    }
    if truncated:
        compacted["_compact"] = meta
    return compacted, meta
