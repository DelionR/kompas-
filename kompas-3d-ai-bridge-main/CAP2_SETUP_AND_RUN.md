# KOMPAS CODEX ENGINEERING AGENT 1.1.14_CAP2

This Golden is a self-contained bridge/package snapshot for KOMPAS-3D v25 x64.

## Requirements

- Windows, normal non-administrator desktop session.
- A running KOMPAS-3D v25 x64 instance in the same user session.
- Python 3.11. The package includes `requirements.txt` and dependency diagnostics.

## Setup

1. Extract the whole ZIP to a stable directory. The recommended runtime target is
   `C:\KOMPAS_AI_BRIDGE`.
2. From a normal PowerShell session, install the included Python requirements:

   `python -m pip install -r .\requirements.txt`

3. Verify dependencies and static contracts:

   `python .\common\dependency_check.py`

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\RUN_STATIC_TESTS.ps1 -Root (Resolve-Path .)`

4. Start the watchdog/worker, passing the exact extracted root:

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap\START_BOOTSTRAP_SAFE.ps1 -Root (Resolve-Path .)`

   Keep that watchdog running. In another normal PowerShell window:

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap\BootstrapClient.ps1 -Action status -Root (Resolve-Path .)`

5. Start the MCP server from Codex/another stdio client:

   `python .\mcp\kompas_mcp_server.py --root (Resolve-Path .)`

## Verification

- Open an `*_AGENT_COPY.m3d` or `*_AGENT_COPY.a3d` in KOMPAS before live tests.
- Run `tests\KOMPAS_AGENT_SELF_TEST.ps1` for the legacy bridge smoke.
- CAP2 evidence is under `runtime\evidence`.
- Deterministic CAD fixtures and native material evidence are under `work`.
- Never run bootstrap elevated; strict live attach intentionally refuses a hidden second KOMPAS instance.

## Version and surface

- Worker: `1.1.14_CAP2`
- Protocol: `1.1.11`
- MCP tools: 35

The authoritative names are also recorded in `VERSION.json` and `GOLDEN_MANIFEST.json`.
