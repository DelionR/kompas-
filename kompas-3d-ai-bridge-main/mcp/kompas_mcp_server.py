# KOMPAS_MCP_WRAPPER_1_1_11
from __future__ import annotations
import json,os,subprocess,sys,time,uuid
from pathlib import Path
for n in ("stdin","stdout","stderr"):
    s=getattr(sys,n,None)
    if s is not None and hasattr(s,"reconfigure"): s.reconfigure(encoding="utf-8",errors="backslashreplace" if n=="stderr" else "strict")
def argv(name,default=None):
    try:i=sys.argv.index(name)
    except ValueError:return default
    return sys.argv[i+1] if i+1<len(sys.argv) else default
ROOT=Path(argv("--root",r"C:\KOMPAS_AI_BRIDGE")).resolve()
try: TIMEOUT=max(5.0,float(argv("--timeout","15")))
except Exception: TIMEOUT=15.0
CORE=Path(__file__).with_name("kompas_mcp_server_core.py")
if not CORE.is_file(): raise SystemExit(f"missing core:{CORE}")
CMD=ROOT/"runtime"/"commands"; RSP=ROOT/"runtime"/"responses"; CMD.mkdir(parents=True,exist_ok=True); RSP.mkdir(parents=True,exist_ok=True)
# Описания kompas_open_copy и kompas_component_translate_xy переехали в единый
# каталог mcp/tools_catalog.py. Раньше они жили здесь, и обёртка дописывала их
# в ответ на tools/list поиском подстроки - из-за этого поверхность собиралась
# из двух источников, а VERSION.json приходилось править руками.
def atomic(p,o):
    t=p.with_suffix(p.suffix+".tmp"); t.write_text(json.dumps(o,ensure_ascii=False,separators=(",",":")),encoding="utf-8"); os.replace(t,p)
def call(action,payload,timeout=None):
    ident=uuid.uuid4().hex; c=CMD/f"{ident}.json"; r=RSP/f"{ident}.json"
    atomic(c,{"id":ident,"action":action,"payload":payload or {},"sent_at":time.time()})
    end=time.monotonic()+float(timeout or TIMEOUT)
    while time.monotonic()<end:
        if r.is_file():
            o=json.loads(r.read_text(encoding="utf-8-sig"))
            if not o.get("ok"): raise RuntimeError(json.dumps(o,ensure_ascii=False))
            return o.get("result") or {}
        time.sleep(.05)
    try:c.unlink()
    except FileNotFoundError:pass
    raise TimeoutError(f"{action} timed out")
def norm(p): return os.path.normcase(os.path.abspath(str(p)))
def copytool(a):
    a=a or {}; source=str(a.get("source") or "").strip()
    if not source:
        x=call("document.active",{},10)
        if not x.get("present"): raise RuntimeError("no active KOMPAS document")
        source=str(x.get("path_name") or x.get("file_name") or "").strip()
    if not source or not Path(source).is_file(): raise FileNotFoundError(f"source not found:{source}")
    name=str(a.get("name") or "").strip()
    if not name: name=f"{Path(source).stem}_AGENT_COPY{Path(source).suffix}"
    if Path(name).name!=name or "/" in name or "\\" in name or ":" in name: raise ValueError("name must be filename only")
    if Path(source).suffix and Path(name).suffix.lower()!=Path(source).suffix.lower(): raise ValueError(f"copy extension must remain {Path(source).suffix}")
    y=call("document.open_copy",{"source":source,"name":name,"visible":True},max(TIMEOUT,30))
    cp=str(y.get("copy") or y.get("active_path") or y.get("file_name") or "").strip()
    x=call("document.active",{},10); ap=str(x.get("path_name") or x.get("file_name") or "").strip()
    if not cp or not x.get("present") or norm(cp)!=norm(ap): raise RuntimeError(f"copy_not_active copy={cp!r} active={ap!r}")
    if norm(cp)==norm(source): raise RuntimeError("safety violation: copy equals source")
    return {"source":source,"copy":cp,"active_path":ap,"active_verified":True,"visible":True,"writable":True,"rebuild_prompt_policy":y.get("rebuild_prompt_policy","auto_yes_on_copy_only")}

def geotool(a):
    return call("component.translate_xy",a or {},max(TIMEOUT,30))
def result(d): return {"content":[{"type":"text","text":json.dumps(d,ensure_ascii=False,indent=2)}],"isError":False,"structuredContent":d}
def error(e): return {"content":[{"type":"text","text":f"{type(e).__name__}: {e}"}],"isError":True}
core=subprocess.Popen([sys.executable,str(CORE),*sys.argv[1:]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=None,text=True,encoding="utf-8",errors="strict",bufsize=1)
def rt(q):
    core.stdin.write(json.dumps(q,ensure_ascii=False,separators=(",",":"))+"\n"); core.stdin.flush()
    if "id" not in q:return None
    line=core.stdout.readline()
    if line=="": raise RuntimeError(f"core exited:{core.poll()}")
    return json.loads(line)
def send(o):
    sys.stdout.write(json.dumps(o,ensure_ascii=False,separators=(",",":"))+"\n"); sys.stdout.flush()
try:
    for line in sys.stdin:
        if not line.strip():continue
        try:q=json.loads(line)
        except Exception as e:
            send({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":f"parse error:{e}"}}); continue
        m=q.get("method")
        if m=="tools/call" and (q.get("params") or {}).get("name")=="kompas_component_translate_xy":
            try:r=result(geotool((q.get("params") or {}).get("arguments") or {}))
            except Exception as e:r=error(e)
            if "id" in q:send({"jsonrpc":"2.0","id":q.get("id"),"result":r})
            continue
        if m=="tools/call" and (q.get("params") or {}).get("name")=="kompas_open_copy":
            try:r=result(copytool((q.get("params") or {}).get("arguments") or {}))
            except Exception as e:r=error(e)
            if "id" in q:send({"jsonrpc":"2.0","id":q.get("id"),"result":r})
            continue
        try:o=rt(q)
        except Exception as e:
            if "id" in q:send({"jsonrpc":"2.0","id":q.get("id"),"error":{"code":-32603,"message":f"core proxy failure:{type(e).__name__}:{e}"}})
            continue
        if o is None:continue
        if m=="initialize":
            # Версия сервера и каталог инструментов теперь приходят из ядра,
            # единый источник - mcp/tools_catalog.py. Патчить ответ поиском
            # подстроки больше не нужно; к инструкции добавляем только правило
            # про обязательное создание копии перед записью.
            z=o.get("result") or {}
            ins=str(z.get("instructions") or "")
            if "kompas_open_copy" not in ins:
                z["instructions"]=ins+" Use kompas_open_copy before any write/edit operation."
        send(o)
finally:
    try:core.terminate()
    except Exception:pass
