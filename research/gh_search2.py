#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вторая волна поиска: прицельные запросы по щитам, АСУ ТП, CAD-автоматизации."""
import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "research-script", "Accept": "application/vnd.github+json"}
BASE = "https://api.github.com/search/repositories"

QUERIES = [
    "КОМПАС-Электрик",
    "Компас Электрик",
    "компас api",
    "КОМПАС API автоматизация",
    "EPLAN",
    "eplan electric p8",
    "eplan pro panel",
    "IEC 61439",
    "61439",
    "busbar",
    "электрический шкаф",
    "монтажная панель",
    "щит автоматики",
    "ШКАФ ПЛК",
    "PLC cabinet",
    "electrical panel layout",
    "din rail layout",
    "panel layout generator dxf",
    "solidworks api automation python",
    "cad mcp server",
    "mcp server cad automation",
    " parametric cabinet generator",
    "opencad электрика",
    "ГОСТ ЕСКД библиотека",
]

def search(q, per_page=25):
    url = BASE + "?" + urllib.parse.urlencode({"q": q, "per_page": per_page, "sort": "stars", "order": "desc"})
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return None, str(e)
    items = [{
        "full_name": it["full_name"],
        "stars": it.get("stargazers_count", 0),
        "lang": it.get("language"),
        "desc": (it.get("description") or "").strip(),
        "url": it.get("html_url"),
        "pushed": it.get("pushed_at"),
    } for it in d.get("items", [])]
    return d.get("total_count"), items

def main():
    out = {}
    for i, q in enumerate(QUERIES):
        total, res = search(q)
        if total is None:
            out[q] = {"error": res}
            print("ERR", q, res, flush=True)
        else:
            out[q] = {"total": total, "items": res}
            print("OK  %-40s total=%s" % (q, total), flush=True)
        if i < len(QUERIES) - 1:
            time.sleep(7)
    with open("gh_results2.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
