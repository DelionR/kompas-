"""Ограниченные задания: единственный исполняемый allow-list моста.

Что изменилось и почему
-----------------------
Раньше допуск задания проверялся так: прочитать текст скрипта в нижнем регистре
и убедиться, что в нём нет подстроки с защищённым путём::

    source = script.read_text(...).lower()
    if any(str(x).lower() in source for x in PROTECTED):
        raise PermissionError("job_references_protected_path")

Это не граница безопасности. Путь собирается из частей, из ``chr()``, из
переменной окружения - и проверка проходит. При этом сам ``kompas_run_job``
был штатным путём: ``SKILL.md`` прямо отправлял туда агента, когда стабильной
поверхности не хватало.

Теперь работает явный манифест: ``jobs/allowlist.json``. Что не перечислено -
не исполняется, независимо от содержимого. Проверка на защищённые пути осталась,
но как проверка пути, а не содержимого файла.

Формат ``jobs/allowlist.json``::

    {
      "version": 1,
      "jobs": [
        "job_template.py",
        {"name": "bom_export.py", "description": "Выгрузка спецификации",
         "timeout_sec": 120, "enabled": true}
      ]
    }
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from protocol import ok
from settings import settings_for

ALLOWLIST_NAME = "allowlist.json"


def allowlist_path(root: Any) -> Path:
    return settings_for(root).jobs_dir / ALLOWLIST_NAME


def load_allowlist(root: Any) -> Dict[str, Dict[str, Any]]:
    """Прочитать манифест допуска. Возвращает имя файла -> параметры.

    Отсутствующий или битый манифест - это пустой allow-list, а не «разрешить всё».
    """
    path = allowlist_path(root)
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}

    entries = parsed.get("jobs") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list):
        return {}

    allowed: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            allowed[Path(entry.strip()).name] = {"enabled": True}
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if name:
                allowed[Path(name).name] = {
                    "enabled": bool(entry.get("enabled", True)),
                    "description": str(entry.get("description") or ""),
                    "timeout_sec": entry.get("timeout_sec"),
                }
    return allowed


def allowed_jobs(root: Any) -> List[Dict[str, Any]]:
    """Список допущенных заданий - для чтения агентом и для документации."""
    jobs_dir = settings_for(root).jobs_dir
    out: List[Dict[str, Any]] = []
    for name, meta in sorted(load_allowlist(root).items()):
        out.append({
            "name": name,
            "exists": (jobs_dir / name).is_file(),
            "enabled": bool(meta.get("enabled", True)),
            "description": meta.get("description", ""),
            "timeout_sec": meta.get("timeout_sec"),
        })
    return out


def _resolve_script(root: Path, payload: Dict[str, Any]) -> Tuple[Path, Dict[str, Any]]:
    jobs_dir = settings_for(root).jobs_dir.resolve()
    requested = Path(str(payload.get("script") or "")).name
    if not requested:
        raise PermissionError("job_must_be_existing_python_file_under_jobs: пустое имя")

    script = (jobs_dir / requested).resolve()
    if not script.is_relative_to(jobs_dir) or script.suffix.lower() != ".py" or not script.is_file():
        raise PermissionError(f"job_must_be_existing_python_file_under_jobs: {requested}")

    entry = load_allowlist(root).get(script.name)
    if entry is None:
        raise PermissionError(f"job_not_in_allowlist: {script.name}")
    if not entry.get("enabled", True):
        raise PermissionError(f"job_not_in_allowlist: {script.name} (disabled)")

    # Проверка пути, а не текста: само расположение задания не должно оказаться
    # внутри защищённого корня исходников.
    if settings_for(root).is_protected(script):
        raise PermissionError(f"job_references_protected_path: {script.name}")

    return script, entry


def run_job(session, payload: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(session.root).resolve()
    policy = settings_for(root)
    policy.jobs_dir.mkdir(parents=True, exist_ok=True)

    script, entry = _resolve_script(root, payload or {})

    requested_timeout = (payload or {}).get("timeout_sec", entry.get("timeout_sec") or 60)
    try:
        timeout = int(requested_timeout)
    except (TypeError, ValueError):
        timeout = 60
    timeout = min(max(timeout, 1), int(policy.job_timeout_max_sec))

    env = dict(
        os.environ,
        KOMPAS_BRIDGE_ROOT=str(root),
        KOMPAS_BRIDGE_WORK=str(policy.work_dir),
    )
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": {
                "code": "job_timeout",
                "message": f"job exceeded {timeout}s",
                "details": {
                    "stdout": (exc.stdout or "")[-4000:],
                    "stderr": (exc.stderr or "")[-4000:],
                },
            },
        }

    body = {
        "script": script.name,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": {
                "code": "job_failed",
                "message": f"job exit code {completed.returncode}",
                "details": body,
            },
        }
    return ok("run_job", body)
