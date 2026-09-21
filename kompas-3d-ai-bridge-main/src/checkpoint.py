"""Контрольные точки: снимки файлов перед изменяющими операциями.

Зачем этот модуль существует
---------------------------
Снимки перед записью в проекте уже делались - но в каждом инструменте своим
кодом: ``shutil.copy2`` в каталог ``_BACKUPS``, уникальное имя на ``uuid4``
и больше ничего. В результате снимки копились бесконечно, их было нельзя
перечислить, а откат существовал только внутри того вызова, который снимок
сделал: если операция уже вернула управление, достать снимок было нечем.

Здесь снимок становится объектом с идентификатором и журналом:

* ``create`` - снимок с записью в индекс (атомарно);
* ``list`` - перечисление снимков, в том числе по конкретному файлу;
* ``restore`` - возврат снимка на место исходного файла с проверкой,
  что файл лежит в разрешённом корне;
* ``prune`` - уборка старых снимков сверх лимита.

Границы
-------
Модуль работает с файлами, а не с КОМПАСом, поэтому проверяется тестами без
COM. Восстановление возможно только внутри корней, куда мосту разрешена
запись: вернуть снимок в защищённый исходник модуль откажется.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings_for

CHECKPOINT_DIRNAME = "_BACKUPS"
INDEX_NAME = "checkpoints.json"
DEFAULT_KEEP = 20


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def checkpoint_dir(bridge_root: Any) -> Path:
    """Каталог снимков. Создаётся при первом обращении."""
    target = Path(str(bridge_root)).resolve() / CHECKPOINT_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _index_path(bridge_root: Any) -> Path:
    return checkpoint_dir(bridge_root) / INDEX_NAME


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_index(bridge_root: Any) -> List[Dict[str, Any]]:
    path = _index_path(bridge_root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _save_index(bridge_root: Any, rows: List[Dict[str, Any]]) -> None:
    path = _index_path(bridge_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def create(bridge_root: Any, target: Any, action: str = "") -> Dict[str, Any]:
    """Снять копию файла и записать её в журнал."""
    source = Path(str(target)).resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))

    directory = checkpoint_dir(bridge_root)
    identifier = time.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    snapshot = directory / f"{identifier}__{source.name}"
    shutil.copy2(source, snapshot)

    row = {
        "id": identifier,
        "created": _now(),
        "action": str(action or ""),
        "target": str(source),
        "target_name": source.name,
        "snapshot": str(snapshot),
        "size_bytes": snapshot.stat().st_size,
        "sha256": sha256(source),
        "snapshot_sha256": sha256(snapshot),
    }

    rows = _load_index(bridge_root)
    rows.append(row)
    _save_index(bridge_root, rows)
    return row


def list_checkpoints(bridge_root: Any, target: Any = None) -> List[Dict[str, Any]]:
    """Список снимков, при необходимости - только по одному файлу."""
    rows = _load_index(bridge_root)
    if target is not None:
        wanted = str(Path(str(target)).resolve()).lower()
        rows = [row for row in rows if str(row.get("target", "")).lower() == wanted]
    alive = []
    for row in rows:
        snapshot = Path(str(row.get("snapshot") or ""))
        row = dict(row)
        row["exists"] = snapshot.is_file()
        alive.append(row)
    return sorted(alive, key=lambda row: str(row.get("created") or ""), reverse=True)


def find(bridge_root: Any, checkpoint_id: str) -> Optional[Dict[str, Any]]:
    for row in _load_index(bridge_root):
        if str(row.get("id") or "") == str(checkpoint_id or "").strip():
            return row
    return None


def restore(bridge_root: Any, checkpoint_id: str, expected_target: Any = None) -> Dict[str, Any]:
    """Вернуть снимок на место исходного файла."""
    row = find(bridge_root, checkpoint_id)
    if row is None:
        raise LookupError(f"checkpoint_not_found: {checkpoint_id}")

    snapshot = Path(str(row.get("snapshot") or ""))
    if not snapshot.is_file():
        raise FileNotFoundError(str(snapshot))

    target = Path(str(row.get("target") or ""))
    if expected_target is not None:
        raw = Path(str(expected_target))
        # Относительное имя - это файл в рабочем каталоге моста, а не путь
        # относительно текущего каталога процесса.
        if raw.is_absolute():
            wanted = str(raw.resolve())
        else:
            wanted = str((settings_for(bridge_root).work_dir / raw).resolve())
        if wanted.lower() != str(target).lower():
            raise ValueError(
                "checkpoint_target_mismatch: "
                f"checkpoint={target.name}; expected={Path(wanted).name}"
            )

    if not settings_for(bridge_root).is_writable(target):
        raise RuntimeError("path_not_allowed:" + str(target))
    if settings_for(bridge_root).is_protected(target):
        raise RuntimeError("path_protected:" + str(target))

    before = sha256(target) if target.is_file() else None
    shutil.copy2(snapshot, target)
    after = sha256(target)

    return {
        "id": row["id"],
        "restored_to": str(target),
        "snapshot": str(snapshot),
        "sha256_before_restore": before,
        "sha256_after_restore": after,
        "matches_snapshot": after == row.get("snapshot_sha256"),
        "action": row.get("action") or "",
        "created": row.get("created") or "",
    }


def prune(bridge_root: Any, keep: int = DEFAULT_KEEP) -> Dict[str, Any]:
    """Удалить снимки сверх лимита. Возвращает, что удалено и сколько места."""
    limit = max(0, int(keep))
    rows = sorted(
        _load_index(bridge_root),
        key=lambda row: str(row.get("created") or ""),
        reverse=True,
    )
    survivors = rows[:limit]
    doomed = rows[limit:]

    removed: List[str] = []
    freed = 0
    for row in doomed:
        snapshot = Path(str(row.get("snapshot") or ""))
        if snapshot.is_file():
            try:
                freed += snapshot.stat().st_size
                snapshot.unlink()
                removed.append(row.get("id") or "")
            except OSError:
                continue

    # Выжившие остаются в индексе даже если файл снимка удалён вручную:
    # в списке у каждой записи есть флаг exists, и его видно оператору.
    _save_index(bridge_root, survivors)

    return {
        "keep": limit,
        "removed_count": len(removed),
        "removed": removed,
        "freed_bytes": freed,
        "remaining": len(survivors),
    }
