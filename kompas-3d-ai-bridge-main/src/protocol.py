from __future__ import annotations
import json, os
from pathlib import Path


def atomic_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def ok(action, result):
    return {"ok": True, "result": result}


def fail(action, code, message, details=None):
    return {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}
