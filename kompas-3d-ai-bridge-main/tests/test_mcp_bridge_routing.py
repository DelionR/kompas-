from __future__ import annotations
import json, subprocess, sys, tempfile, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "kompas_mcp_server.py"
# Valid 1x1 transparent PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


def read_json_line(proc):
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError(f"server closed; stderr={proc.stderr.read()}")
    return json.loads(line)


def worker_once(root: Path, expected_action: str, result: dict):
    cmd = root / "runtime" / "commands"
    rsp = root / "runtime" / "responses"
    deadline = time.time() + 5
    while time.time() < deadline:
        files = list(cmd.glob("*.json"))
        if files:
            p = files[0]
            req = json.loads(p.read_text(encoding="utf-8"))
            assert req["action"] == expected_action, req
            out = {
                "protocol_version": "1.1",
                "id": req["id"],
                "action": req["action"],
                "ok": True,
                "result": result,
            }
            (rsp / f"{req['id']}.json").write_text(json.dumps(out), encoding="utf-8")
            return
        time.sleep(.02)
    raise TimeoutError("fake worker did not receive command")


def call(proc, req_id, name, arguments=None):
    proc.stdin.write(json.dumps({
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }) + "\n")
    proc.stdin.flush()
    return read_json_line(proc)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "runtime" / "commands").mkdir(parents=True)
        (root / "runtime" / "responses").mkdir(parents=True)
        image = root / "capture.png"
        image.write_bytes(PNG)
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), "--root", str(root), "--timeout", "5"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        try:
            proc.stdin.write(json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}) + "\n")
            proc.stdin.flush(); assert read_json_line(proc)["result"]["serverInfo"]["name"] == "kompas-engineering"
            proc.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n"); proc.stdin.flush()

            t = threading.Thread(target=worker_once, args=(root, "kompas.status", {"com_alive":True,"version":"25"}), daemon=True)
            t.start(); r = call(proc, 2, "kompas_status"); t.join(2)
            assert not r["result"]["isError"]
            assert r["result"]["structuredContent"]["com_alive"] is True

            # Move processed fake command aside to avoid the fake worker seeing it again.
            for p in (root / "runtime" / "commands").glob("*.json"): p.unlink()
            t = threading.Thread(target=worker_once, args=(root, "viewport.capture", {"path":str(image),"method":"PrintWindow"}), daemon=True)
            t.start(); r = call(proc, 3, "kompas_viewport_capture", {"viewport_only":True}); t.join(2)
            content = r["result"]["content"]
            assert any(x.get("type") == "image" and x.get("mimeType") == "image/png" for x in content)
            print("PASS test_mcp_bridge_routing")
        finally:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    main()
