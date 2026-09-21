#!/usr/bin/env python3
"""File-routed, versioned KOMPAS Automation bridge."""
from __future__ import annotations
import argparse, json, os, sys, threading, time
from pathlib import Path
from datetime import datetime, timezone
from protocol import ok, fail, atomic_json
from errors import error_body, log_traceback
from settings import settings_for
from kompas_session import KompasSession
from capabilities import registry as legacy_registry
from engineering_capabilities import registry as engineering_registry
from version_matrix import guard_for_session

VERSION = "1.1.14_CAP2"
PROTOCOL_VERSION = "1.1.11"
REGISTRY = dict(legacy_registry)
REGISTRY.update(engineering_registry)


def now(): return datetime.now(timezone.utc).astimezone().isoformat()


def _heartbeat_loop(path: Path):
    """Keep watchdog liveness current while the main thread is inside COM.

    KOMPAS operations such as Save/Close/Reopen can legitimately exceed the
    watchdog's four-second stale threshold.  Only the main thread touches COM;
    this daemon writes the isolated heartbeat file once per second.
    """
    while True:
        try:
            atomic_json(path, {
                "pid": os.getpid(),
                "time": now(),
                "kind": "kompas-engineering-bridge",
                "version": VERSION,
            })
        except Exception:
            pass
        time.sleep(1.0)

# KOMPAS_SAFE_REOPEN_TARGET_FIX_1_1_12B
# Safety:
# document.close remembers the exact path it closed.
# document.reopen({}) may reopen only that exact path.
# No source/original fallback is allowed.
import json as _sr_json
import os as _sr_os
from pathlib import Path as _SRPath
import win32com.client as _sr_win32

_sr_close_original = REGISTRY.get("document.close")
_sr_reopen_original = REGISTRY.get("document.reopen")

if _sr_close_original is None:
    raise RuntimeError("SAFE_REOPEN_INIT: document.close missing")
if _sr_reopen_original is None:
    raise RuntimeError("SAFE_REOPEN_INIT: document.reopen missing")


def _sr_norm(value):
    return _sr_os.path.normcase(
        _sr_os.path.abspath(str(value))
    )


def _sr_active_path():
    app = _sr_win32.GetActiveObject("KOMPAS.Application.7")
    doc = app.ActiveDocument
    if doc is None:
        return ""

    for attr in ("PathName", "FileName"):
        try:
            value = getattr(doc, attr)
            if callable(value):
                value = value()
            value = str(value or "").strip()
            if value:
                return value
        except Exception:
            pass

    return ""


def _sr_state_file(session):
    root = _SRPath(getattr(session, "root"))
    path = root / "runtime" / "last_closed_document.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sr_write_state(session, closed_path):
    path = _sr_state_file(session)
    tmp = path.with_suffix(path.suffix + ".tmp")

    payload = {
        "closed_path": str(closed_path),
        "closed_path_normalized": _sr_norm(closed_path),
    }

    tmp.write_text(
        _sr_json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _sr_os.replace(tmp, path)


def _sr_read_state(session):
    path = _sr_state_file(session)

    if not path.is_file():
        return ""

    data = _sr_json.loads(
        path.read_text(encoding="utf-8-sig")
    )

    return str(data.get("closed_path") or "").strip()


def _sr_close(session, payload):
    closed_path = _sr_active_path()

    if not closed_path:
        raise RuntimeError(
            "close_target_unknown: active document has no path"
        )

    result = _sr_close_original(session, payload)

    # Persist only after successful close.
    _sr_write_state(session, closed_path)

    return result


def _sr_open_exact(target):
    target = str(target or "").strip()

    if not target:
        raise RuntimeError("reopen_target_missing")

    path = _SRPath(target)

    if not path.is_file():
        raise FileNotFoundError(
            f"reopen_target_not_found:{target}"
        )

    app = _sr_win32.GetActiveObject("KOMPAS.Application.7")

    # API7: Open(PathName, Visible, ReadOnly)
    doc = app.Documents.Open(str(path), True, False)

    if doc is None:
        raise RuntimeError(
            f"reopen_open_returned_null:{target}"
        )

    active = _sr_active_path()

    if not active:
        raise RuntimeError(
            f"reopen_no_active_document_after_open:{target}"
        )

    if _sr_norm(active) != _sr_norm(target):
        raise RuntimeError(
            "reopen_postcondition_failed:"
            f"target={target!r}, active={active!r}"
        )

    return {
        "reopened": str(path),
        "active_path": active,
        "target_verified": True,
        "target_source": "last_closed_document",
        "visible": True,
        "read_only": False,
    }


def _sr_reopen(session, payload):
    payload = payload or {}

    explicit = ""

    if isinstance(payload, dict):
        for key in ("path", "path_name", "file_name"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                explicit = candidate
                break

    target = explicit or _sr_read_state(session)

    if not target:
        raise RuntimeError(
            "reopen_target_missing:"
            "no explicit path and no last_closed_document"
        )

    result = _sr_open_exact(target)

    if explicit:
        result["target_source"] = "explicit_payload"

    return {
        "ok": True,
        "action": "document.reopen",
        "result": result,
    }


REGISTRY["document.close"] = _sr_close
REGISTRY["document.reopen"] = _sr_reopen


from geometry_translate import register_geometry_translate
register_geometry_translate(REGISTRY, ok)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True); args = ap.parse_args()
    root = Path(args.root).resolve(); cmd = root/"runtime/commands"; rsp = root/"runtime/responses"; done = root/"runtime/processed"
    for d in (cmd, rsp, done, root/"runtime/logs", root/"work"): d.mkdir(parents=True, exist_ok=True)
    session = KompasSession(root)
    atomic_json(root/"runtime/worker_state.json", {"ok": True, "state": "running", "kind": "kompas-engineering-bridge", "version": VERSION, "pid": os.getpid(), "started": now()})
    threading.Thread(
        target=_heartbeat_loop,
        args=(root/"runtime/worker_heartbeat.json",),
        name="worker-heartbeat",
        daemon=True,
    ).start()
    while True:
        files = sorted(cmd.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not files: time.sleep(.12); continue
        for path in files:
            req_id = path.stem; req = {}
            try:
                req = json.loads(path.read_text(encoding="utf-8")); req_id = str(req.get("id") or req_id)
                action = str(req.get("action", "")).strip(); payload = req.get("payload") or {}
                if action == "ping": body = ok(action, {"worker": "kompas-engineering-bridge", "bridge_version": VERSION, "pid": os.getpid(), "python": sys.version, "settings_warnings": list(settings_for(root).warnings)})
                elif action == "capabilities": body = ok(action, {"bridge_version": VERSION, "capabilities": sorted(REGISTRY)})
                elif action == "worker_info": body = ok(action, {"bridge_version": VERSION, "root": str(root), "pid": os.getpid(), "settings": settings_for(root).describe()})
                elif action not in REGISTRY: body = fail(action, "unknown_action", "No registered capability", {"available": sorted(REGISTRY)})
                else:
                    # Матрица версий: для действий из guarded_actions сверяем
                    # версию КОМПАСа. Список по умолчанию пуст, поэтому
                    # накладных расходов на обычных вызовах нет.
                    guard_for_session(root, session, action)
                    body = REGISTRY[action](session, payload)
                response = {"protocol_version": PROTOCOL_VERSION, "id": req_id, "action": action, "time": now(), "responded_at": now(), **body}
            except Exception as exc:
                # Наружу уходит только устойчивый код, сообщение и подсказка.
                # Полная трассировка остаётся в runtime/logs/errors.log: раньше она
                # уезжала прямо в контекст модели и раскрывала структуру диска.
                failed_action = str(req.get("action", ""))
                log_traceback(root, failed_action, exc)
                response = {"protocol_version": PROTOCOL_VERSION, "id": req_id, "action": failed_action, "ok": False, "time": now(), "responded_at": now(), "error": error_body(failed_action, exc, root)}
            atomic_json(rsp/f"{req_id}.json", response)
            try: os.replace(path, done/path.name)
            except Exception: path.unlink(missing_ok=True)

if __name__ == "__main__": main()
