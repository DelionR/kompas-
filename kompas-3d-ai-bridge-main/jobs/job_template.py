"""Safe task-specific COM job template for KOMPAS Engineering Agent V1.

Copy/rename this file inside KOMPAS_BRIDGE/jobs and implement only the narrow
operation that is missing from the stable MCP surface. Jobs execute in the
interactive Windows bridge host and MUST emit a compact machine-readable result.

Security contract:
- keep the file under KOMPAS_BRIDGE/jobs;
- never address protected project source roots directly;
- write only under KOMPAS_BRIDGE/work or the approved output root;
- do not start unrelated processes, network servers, shells, or download code;
- verify every geometry-changing job afterward with structured API data AND a
  fresh viewport capture.
"""
from __future__ import annotations
import json, os
from pathlib import Path

ROOT = Path(os.environ["KOMPAS_BRIDGE_ROOT"])
WORK = Path(os.environ["KOMPAS_BRIDGE_WORK"])


def main() -> None:
    # Replace this body with ONE narrow KOMPAS COM operation.
    # Import win32com.client only when the job really needs COM.
    result = {
        "ok": True,
        "template": True,
        "root": str(ROOT),
        "work": str(WORK),
        "message": "Template ran without modifying KOMPAS.",
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
