import json, subprocess, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
server = root / "mcp" / "kompas_mcp_server.py"
p = subprocess.Popen([sys.executable, str(server), "--root", str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    init = {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"selftest","version":"1"}}}
    p.stdin.write(json.dumps(init)+"\n"); p.stdin.flush()
    r1 = json.loads(p.stdout.readline())
    assert r1["result"]["serverInfo"]["name"] == "kompas-engineering"
    p.stdin.write(json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})+"\n"); p.stdin.flush()
    r2 = json.loads(p.stdout.readline())
    names = {x["name"] for x in r2["result"]["tools"]}
    required = {"kompas_status","kompas_active_document","kompas_open_agent_copy","kompas_model_tree","kompas_component_info","kompas_model_bbox","kompas_set_view","kompas_rotate_view","kompas_viewport_capture","kompas_references_qa","kompas_run_job","kompas_assembly_insert_component","kompas_component_transform","kompas_material_read","kompas_material_set","kompas_pmi_read","kompas_pmi_dimension_create","kompas_component_properties_read","kompas_component_properties_set","kompas_bom_read","kompas_component_pattern_linear"}
    assert required <= names, sorted(required - names)
    print("PASS test_mcp_stdio", len(names))
finally:
    p.terminate(); p.wait(timeout=5)
