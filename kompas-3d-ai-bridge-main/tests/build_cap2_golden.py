#!/usr/bin/env python3
"""Build and offline-verify the self-contained CAP2 Golden package."""
from __future__ import annotations

import argparse
import ast
import compileall
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path


VERSION = "1.1.14_CAP2"
PROTOCOL = "1.1.11"
PACKAGE_DIRNAME = "KOMPAS_CODEX_ENGINEERING_AGENT_1.1.14_CAP2"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, target: Path, allowed_suffixes: set[str] | None = None) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in {"__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo", ".log", ".tmp"}:
            continue
        if allowed_suffixes is not None and path.suffix.lower() not in allowed_suffixes:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise LookupError(f"assignment_not_found:{path}:{name}")


def tool_names(root: Path) -> list[str]:
    core = literal_assignment(root / "mcp" / "kompas_mcp_server_core.py", "TOOLS")
    wrapper_tool = literal_assignment(root / "mcp" / "kompas_mcp_server.py", "TOOL")
    wrapper_geo = literal_assignment(root / "mcp" / "kompas_mcp_server.py", "GEO_TOOL")
    names = [item["name"] for item in core] + [wrapper_tool["name"], wrapper_geo["name"]]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate_MCP_tool_name")
    return names


def setup_text(names: list[str]) -> str:
    return f"""# KOMPAS CODEX ENGINEERING AGENT {VERSION}

This Golden is a self-contained bridge/package snapshot for KOMPAS-3D v25 x64.

## Requirements

- Windows, normal non-administrator desktop session.
- A running KOMPAS-3D v25 x64 instance in the same user session.
- Python 3.11. The package includes `requirements.txt` and dependency diagnostics.

## Setup

1. Extract the whole ZIP to a stable directory. The recommended runtime target is
   `C:\\KOMPAS_AI_BRIDGE`.
2. From a normal PowerShell session, install the included Python requirements:

   `python -m pip install -r .\\requirements.txt`

3. Verify dependencies and static contracts:

   `python .\\common\\dependency_check.py`

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\\tests\\RUN_STATIC_TESTS.ps1 -Root (Resolve-Path .)`

4. Start the watchdog/worker, passing the exact extracted root:

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\\bootstrap\\START_BOOTSTRAP_SAFE.ps1 -Root (Resolve-Path .)`

   Keep that watchdog running. In another normal PowerShell window:

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\\bootstrap\\BootstrapClient.ps1 -Action status -Root (Resolve-Path .)`

5. Start the MCP server from Codex/another stdio client:

   `python .\\mcp\\kompas_mcp_server.py --root (Resolve-Path .)`

## Verification

- Open an `*_AGENT_COPY.m3d` or `*_AGENT_COPY.a3d` in KOMPAS before live tests.
- Run `tests\\KOMPAS_AGENT_SELF_TEST.ps1` for the legacy bridge smoke.
- CAP2 evidence is under `runtime\\evidence`.
- Deterministic CAD fixtures and native material evidence are under `work`.
- Never run bootstrap elevated; strict live attach intentionally refuses a hidden second KOMPAS instance.

## Version and surface

- Worker: `{VERSION}`
- Protocol: `{PROTOCOL}`
- MCP tools: {len(names)}

The authoritative names are also recorded in `VERSION.json` and `GOLDEN_MANIFEST.json`.
"""


def build(root: Path, timestamp: str) -> tuple[Path, Path, dict]:
    build_root = root / "runtime" / "CAP2_GOLDEN_BUILD" / timestamp
    package_root = build_root / PACKAGE_DIRNAME
    zip_path = build_root / f"KOMPAS_CODEX_ENGINEERING_AGENT_GOLDEN_1.1.14_CAP2_{timestamp}.zip"
    verify_root = root / "runtime" / "CAP2_GOLDEN_VERIFY" / timestamp
    for path in (build_root, verify_root, zip_path):
        if path.exists():
            raise FileExistsError(f"refuse_existing_build_target:{path}")
    package_root.mkdir(parents=True)

    for directory in ("src", "mcp", "common", "docs", "jobs", "skills"):
        copy_tree(root / directory, package_root / directory)
    copy_tree(root / "tests", package_root / "tests", {".py", ".ps1"})
    copy_tree(root / "bootstrap", package_root / "bootstrap", {".ps1", ".cmd"})

    for filename in (
        "requirements.txt",
        "ROLLBACK_OR_UNINSTALL.ps1",
        "FINAL_BUILD_NOTES.md",
        "CODEX_ACCEPTANCE_PROMPT.txt",
    ):
        shutil.copy2(root / filename, package_root / filename)

    config = json.loads((root / "config" / "agent_config.json").read_text(encoding="utf-8-sig"))
    config["bridge_version"] = VERSION
    config["protocol_version"] = PROTOCOL
    (package_root / "config").mkdir(parents=True)
    config_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    (package_root / "config" / "agent_config.json").write_text(config_text, encoding="utf-8")
    (package_root / "config" / "agent_config.example.json").write_text(config_text, encoding="utf-8")

    names = tool_names(root)
    version = {
        "product": "KOMPAS CODEX ENGINEERING AGENT",
        "worker_version": VERSION,
        "protocol_version": PROTOCOL,
        "mcp_tool_count": len(names),
        "mcp_tools": names,
        "kompas": "KOMPAS-3D v25 x64",
        "python": "3.11",
    }
    (package_root / "VERSION.json").write_text(
        json.dumps(version, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (package_root / "CAP2_SETUP_AND_RUN.md").write_text(setup_text(names), encoding="utf-8")

    report_names = (
        "CAP2_PROGRESS_LEDGER.json",
        "CAP2_LITERAL_GAP_AUDIT.json",
        "CAP2_A_SELF_TEST_REPORT.json",
        "CAP2_P1_EXISTING_COMPONENT_AUDIT.json",
        "CAP2_B_AUDIT_REPORT.json",
        "CAP2_B_CONSTRAINTS_AUDIT.json",
        "CAP2_FINAL_MCP_ACCEPTANCE_REPORT.json",
        "CAP2_SOURCE_INTEGRITY_REPORT.json",
        "CAP2_WRITE_ROLLBACK_STATIC_SAFETY_AUDIT.json",
    )
    evidence = package_root / "runtime" / "evidence"
    evidence.mkdir(parents=True)
    for filename in report_names:
        source = root / "runtime" / filename
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, evidence / filename)
    for directory in ("commands", "responses", "processed", "logs"):
        placeholder = package_root / "runtime" / directory / ".keep"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        placeholder.write_text("", encoding="utf-8")
    placeholder = package_root / "bootstrap" / "logs" / ".keep"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    placeholder.write_text("", encoding="utf-8")

    fixtures = (
        "CAP2_TEST_JOINT_BASE_PLATE_AGENT_COPY.m3d",
        "CAP2_TEST_JOINT_BRACKET_AGENT_COPY.m3d",
        "CAP2_TEST_JOINT_ASSEMBLY_AGENT_COPY.a3d",
        "CAP2_ACCEPTANCE_BASE_AGENT_COPY.m3d",
        "CAP2_ACCEPTANCE_BRACKET_AGENT_COPY.m3d",
        "CAP2_ACCEPTANCE_ASSEMBLY_AGENT_COPY.a3d",
        "CAP2_PMI_REFERENCE_AGENT_COPY.m3d",
        "CAP2_PMI_WRITE_AGENT_COPY.m3d",
        "CAP2_COMPONENT_PROPERTIES_AGENT_COPY.a3d",
        "CAP2_PATTERN_AGENT_COPY.a3d",
    )
    work = package_root / "work"
    work.mkdir(parents=True)
    for filename in fixtures:
        shutil.copy2(root / "work" / filename, work / filename)
    material_source = root / "work" / "SAMPLE_3D_PATCH" / "03_3D_NATIVE" / "3D_РљРћРњРџРђРЎ" / "РџР°РЅРµР»СЊ HPL С‚РёРї 01 в„–01 - 200x1080x1840 РјРј - РџР°РЅРµР»СЊ HPL С‚РёРї 01 в„–01 - 200x1080x1840 РјРј.m3d"
    shutil.copy2(material_source, work / "CAP2_MATERIAL_EVIDENCE_HPL_AGENT_COPY.m3d")

    screenshot_names = (
        "kompas_13464_20260813_185956_426_CAP2_ACCEPTANCE_A.png",
        "kompas_13464_20260813_185958_424_CAP2_ACCEPTANCE_B.png",
        "kompas_13464_20260813_190138_146_CAP2_ACCEPTANCE_PATTERN.png",
    )
    screenshot_target = evidence / "screenshots"
    screenshot_target.mkdir()
    for filename in screenshot_names:
        shutil.copy2(root / "work" / "screenshots" / filename, screenshot_target / filename)

    mandatory = [
        "VERSION.json",
        "CAP2_SETUP_AND_RUN.md",
        "requirements.txt",
        "src/worker.py",
        "src/engineering_capabilities.py",
        "src/assembly_insert_tools.py",
        "src/component_transform_tools.py",
        "src/material_tools.py",
        "src/pmi_tools.py",
        "src/component_properties_tools.py",
        "src/bom_tools.py",
        "src/component_pattern_tools.py",
        "mcp/kompas_mcp_server.py",
        "mcp/kompas_mcp_server_core.py",
        "bootstrap/BridgeBootstrap.ps1",
        "bootstrap/BootstrapClient.ps1",
        "bootstrap/START_BOOTSTRAP_SAFE.ps1",
        "common/PythonResolver.ps1",
        "common/dependency_check.py",
        "config/agent_config.json",
        "config/agent_config.example.json",
        "tests/RUN_STATIC_TESTS.ps1",
        "tests/KOMPAS_AGENT_SELF_TEST.ps1",
        "tests/mcp_call.py",
        "runtime/evidence/CAP2_A_SELF_TEST_REPORT.json",
        "runtime/evidence/CAP2_B_AUDIT_REPORT.json",
        "runtime/evidence/CAP2_FINAL_MCP_ACCEPTANCE_REPORT.json",
        "runtime/evidence/CAP2_SOURCE_INTEGRITY_REPORT.json",
        "work/CAP2_ACCEPTANCE_ASSEMBLY_AGENT_COPY.a3d",
        "work/CAP2_MATERIAL_EVIDENCE_HPL_AGENT_COPY.m3d",
    ]
    for relative in mandatory:
        if not (package_root / relative).is_file():
            raise FileNotFoundError(f"mandatory_missing_before_zip:{relative}")

    entries = {}
    for path in sorted(package_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(package_root).as_posix()
            entries[relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    manifest = {
        "schema_version": 1,
        "package": PACKAGE_DIRNAME,
        "created_at_local": datetime.now().astimezone().isoformat(),
        "worker_version": VERSION,
        "protocol_version": PROTOCOL,
        "mcp_tool_count": len(names),
        "mcp_tools": names,
        "entry_points": {
            "bootstrap": "bootstrap/START_BOOTSTRAP_SAFE.ps1",
            "bootstrap_client": "bootstrap/BootstrapClient.ps1",
            "worker": "src/worker.py",
            "mcp": "mcp/kompas_mcp_server.py",
            "static_tests": "tests/RUN_STATIC_TESTS.ps1",
            "live_self_test": "tests/KOMPAS_AGENT_SELF_TEST.ps1",
        },
        "mandatory_files": mandatory,
        "file_count_excluding_manifest": len(entries),
        "files_excluding_manifest": entries,
        "excluded_live_tree_content": [
            "_BACKUPS",
            "bootstrap/logs",
            "runtime command/response/heartbeat garbage",
            "runtime acceptance payloads and raw response fan-out",
            "unrelated work models and random screenshots",
            "Python caches",
        ],
    }
    manifest_path = package_root / "GOLDEN_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(PACKAGE_DIRNAME) / path.relative_to(package_root)).as_posix())

    with zipfile.ZipFile(zip_path, "r") as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"zip_crc_failure:{bad_member}")
        names_in_zip = set(archive.namelist())
        for relative in mandatory + ["GOLDEN_MANIFEST.json"]:
            expected = f"{PACKAGE_DIRNAME}/{relative}"
            if expected not in names_in_zip:
                raise RuntimeError(f"mandatory_missing_in_zip:{relative}")
        archive.extractall(verify_root)

    extracted = verify_root / PACKAGE_DIRNAME
    extracted_manifest = json.loads((extracted / "GOLDEN_MANIFEST.json").read_text(encoding="utf-8"))
    for relative, expected in extracted_manifest["files_excluding_manifest"].items():
        path = extracted / relative
        if not path.is_file() or path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"extracted_manifest_mismatch:{relative}")

    pycache = verify_root / "pycache"
    pycache.mkdir()
    old_prefix = sys.pycache_prefix
    sys.pycache_prefix = str(pycache)
    try:
        for directory in ("src", "mcp", "tests", "jobs", "common"):
            if not compileall.compile_dir(extracted / directory, quiet=1, force=True):
                raise RuntimeError(f"compileall_failed:{directory}")
    finally:
        sys.pycache_prefix = old_prefix

    env = dict(__import__("os").environ)
    env["PYTHONPYCACHEPREFIX"] = str(pycache)
    for test in ("test_mcp_stdio.py", "test_mcp_bridge_routing.py"):
        result = subprocess.run(
            [sys.executable, str(extracted / "tests" / test)],
            cwd=extracted,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"offline_test_failed:{test}:{result.stdout}:{result.stderr}")

    result = {
        "zip_path": str(zip_path),
        "zip_sha256": sha256(zip_path),
        "zip_size": zip_path.stat().st_size,
        "package_root": str(package_root),
        "verify_root": str(verify_root),
        "manifest_file_count_excluding_manifest": len(entries),
        "zip_member_count": len(names_in_zip),
        "mandatory_file_count": len(mandatory),
        "mcp_tool_count": len(names),
        "mcp_tools": names,
        "compileall_pass": True,
        "manifest_hashes_pass": True,
        "zip_crc_pass": True,
        "entry_point_tests_pass": True,
    }
    return zip_path, verify_root, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    _, _, result = build(root, args.timestamp)
    result_path = Path(args.result).resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

