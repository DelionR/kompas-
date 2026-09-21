#!/usr/bin/env python3
"""Runtime dependency probe for KOMPAS CODEX ENGINEERING AGENT.

Kept as a real .py file instead of ``python -c`` because Windows PowerShell
5.1/native-command quoting can strip quotes from inline Python source.
"""
from __future__ import annotations

import importlib
import json
import sys

REQUIRED = {
    "win32com.client": "pywin32",
    "PIL.Image": "Pillow",
    "pymupdf": "PyMuPDF",
}


def main() -> int:
    failures: list[dict[str, str]] = []
    for module_name, package_name in REQUIRED.items():
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # report the actual Windows import problem
            failures.append(
                {
                    "module": module_name,
                    "package": package_name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if failures:
        print(json.dumps({"ok": False, "failures": failures}, ensure_ascii=False))
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "status": "deps-ok",
                "python": sys.version.split()[0],
                "executable": sys.executable,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
