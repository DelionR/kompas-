#!/usr/bin/env python3
"""Small generic JSON-RPC client for the local KOMPAS MCP stdio server."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments-file", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    server = root / "mcp" / "kompas_mcp_server.py"
    arguments = json.loads(
        Path(args.arguments_file).read_text(encoding="utf-8-sig")
    )
    process = subprocess.Popen(
        [sys.executable, str(server), "--root", str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "cap2-verifier", "version": "1"},
            },
        }
        process.stdin.write(json.dumps(initialize, ensure_ascii=False) + "\n")
        process.stdin.flush()
        init_result = json.loads(process.stdout.readline())
        if "error" in init_result:
            raise RuntimeError("MCP initialize failed: " + repr(init_result["error"]))

        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": args.tool, "arguments": arguments},
        }
        process.stdin.write(json.dumps(call, ensure_ascii=False) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        result = response.get("result") or {}
        is_error = bool(result.get("isError")) or "error" in response
        structured = result.get("structuredContent") or {}
        summary = {
            "tool": args.tool,
            "is_error": is_error,
            "action": structured.get("requested_action") or structured.get("action"),
            "target": structured.get("target"),
            "changed": structured.get("changed"),
            "verification": structured.get("verification"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if is_error:
            if not args.output:
                print(json.dumps(response, ensure_ascii=False, indent=2))
            return 2
        return 0
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
