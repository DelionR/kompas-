#!/usr/bin/env python3
"""Dependency-free MCP stdio adapter for the interactive KOMPAS worker.

The MCP process never talks to KOMPAS COM. It only routes structured requests to
runtime/commands and reads runtime/responses. This keeps COM in the interactive
Windows bootstrap/worker session even when Codex itself is sandboxed.
"""
from __future__ import annotations

# KOMPAS_MCP_UTF8_STDIO_FIX_1_1_10B
import sys as _kompas_stdio_sys
for _kompas_stdio_name in ('stdin', 'stdout', 'stderr'):
    _kompas_stdio_stream = getattr(_kompas_stdio_sys, _kompas_stdio_name, None)
    if _kompas_stdio_stream is not None and hasattr(_kompas_stdio_stream, 'reconfigure'):
        _kompas_stdio_errors = 'backslashreplace' if _kompas_stdio_name == 'stderr' else 'strict'
        _kompas_stdio_stream.reconfigure(encoding='utf-8', errors=_kompas_stdio_errors)

import argparse, base64, json, os, sys, time, uuid
from pathlib import Path

# Каталог инструментов и таблица маршрутизации вынесены в единый декларативный
# модуль tools_catalog.py. Раньше 33 описания жили прямо здесь, а ещё 2 дописывала
# обёртка kompas_mcp_server.py поиском подстроки в ответе на tools/list - то есть
# поверхность собиралась из двух источников и могла разъехаться молча.
from tools_catalog import (  # noqa: E402
    ACTION_MAP,
    MCP_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOLS,
    WRAPPER_SIDE_TOOLS,
    negotiate_protocol,
)


class BridgeClient:
    def __init__(self, root: Path, timeout=90):
        self.root = Path(root)
        self.timeout = timeout
        self.cmd = self.root / "runtime" / "commands"
        self.rsp = self.root / "runtime" / "responses"
        self.cmd.mkdir(parents=True, exist_ok=True); self.rsp.mkdir(parents=True, exist_ok=True)

    def call(self, action, payload):
        req_id = uuid.uuid4().hex
        request = {"id": req_id, "action": action, "payload": payload, "sent_at": time.time()}
        tmp = self.cmd / f"{req_id}.json.tmp"; dst = self.cmd / f"{req_id}.json"
        tmp.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, dst)
        response_path = self.rsp / f"{req_id}.json"
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if response_path.is_file():
                return json.loads(response_path.read_text(encoding="utf-8"))
            time.sleep(.08)
        raise TimeoutError(f"bridge_timeout action={action} timeout={self.timeout}s")


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _load_src_layer(bridge_root):
    """Ленивая загрузка слоёв из src/: mcp/ и src/ - разные каталоги.

    Если каталог src/ недоступен, сервер продолжает работать без ужимания
    ответов и без чтения настроек: это деградация, а не отказ.
    """
    src = Path(bridge_root) / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from compact import compact_payload
        from settings import settings_for
    except Exception:
        return None
    return settings_for, compact_payload


def tool_result(name, response, *, bridge_root=None, request_id=""):
    is_error = not bool(response.get("ok"))
    payload = response
    if not is_error and bridge_root is not None and "result" in response:
        loaded = _load_src_layer(bridge_root)
        if loaded is not None:
            settings_for, compact_payload = loaded
            policy = settings_for(bridge_root)
            compacted, meta = compact_payload(
                response["result"],
                bridge_root=bridge_root,
                request_id=request_id,
                threshold=policy.compact_threshold_bytes,
                max_items=policy.compact_max_items,
            )
            if meta is not None:
                payload = dict(response, result=compacted)
    content = [{"type":"text", "text":json.dumps(payload, ensure_ascii=False, indent=2)}]
    if name == "kompas_viewport_capture" and not is_error:
        try:
            path = Path(response["result"]["path"])
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type":"image", "data":data, "mimeType":"image/png"})
        except Exception as exc:
            content.append({"type":"text", "text":f"Image attachment failed: {exc}"})
    out = {"content": content, "isError": is_error}
    if "result" in payload:
        out["structuredContent"] = payload["result"]
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default=r"C:\KOMPAS_AI_BRIDGE"); ap.add_argument("--timeout", type=int, default=90); args = ap.parse_args()
    client = BridgeClient(Path(args.root), args.timeout)
    instructions = (
        "Use KOMPAS structured tools for exact CAD data and edits; use fresh viewport captures for visual QA. "
        "Before saving, restore temporary visibility. Never infer material, mass, geometry, or references from screenshots. "
        "Large responses are compacted: if a result contains _compact, the lists are incomplete - read the artifact file it names "
        "or continue with next_cursor instead of treating the received lists as complete. "
        "kompas_run_job executes only jobs listed in jobs/allowlist.json; if the operation you need is not there, say so explicitly "
        "rather than looking for another way to run code."
    )
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            method = msg.get("method"); req_id = msg.get("id")
            if method == "initialize":
                requested = (msg.get("params") or {}).get("protocolVersion")
                emit({"jsonrpc":"2.0","id":req_id,"result":{"protocolVersion":negotiate_protocol(requested),"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":SERVER_NAME,"version":SERVER_VERSION},"instructions":instructions}})
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                emit({"jsonrpc":"2.0","id":req_id,"result":{}})
            elif method == "tools/list":
                emit({"jsonrpc":"2.0","id":req_id,"result":{"tools":TOOLS}})
            elif method == "tools/call":
                params = msg.get("params") or {}; name = params.get("name"); arguments = params.get("arguments") or {}
                if name in WRAPPER_SIDE_TOOLS:
                    emit({"jsonrpc":"2.0","id":req_id,"error":{"code":-32602,"message":f"Tool {name} is served by the wrapper layer and must not be routed through the core"}}); continue
                if name not in ACTION_MAP:
                    emit({"jsonrpc":"2.0","id":req_id,"error":{"code":-32602,"message":f"Unknown tool: {name}"}}); continue
                action, transform = ACTION_MAP[name]
                try:
                    response = client.call(action, transform(arguments))
                    emit({"jsonrpc":"2.0","id":req_id,"result":tool_result(name, response, bridge_root=args.root, request_id=str(req_id or ""))})
                except Exception as exc:
                    emit({"jsonrpc":"2.0","id":req_id,"result":{"content":[{"type":"text","text":f"{type(exc).__name__}: {exc}"}],"isError":True}})
            elif req_id is not None:
                emit({"jsonrpc":"2.0","id":req_id,"error":{"code":-32601,"message":f"Method not found: {method}"}})
        except Exception as exc:
            # stdio transport must never log to stdout except JSON-RPC
            sys.stderr.write(f"MCP server error: {type(exc).__name__}: {exc}\n"); sys.stderr.flush()

if __name__ == "__main__":
    main()
