"""Единый формат ответа адаптера AutoCAD.

Свой файл, а не импорт из моста КОМПАС: адаптер живёт отдельно и не должен
зависеть от чужого дерева каталогов.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def ok(action: str, result: Any) -> Dict[str, Any]:
    return {"ok": True, "action": action, "result": result}


def fail(action: str, code: str, message: str,
         details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": False, "action": action,
            "error": {"code": code, "message": message,
                      "details": details or {}}}
