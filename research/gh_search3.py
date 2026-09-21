#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Волна 3: все варианты написания КОМПАС (kompas/compas/компас, слитно и раздельно)
+ английские щитовые термины + топики. Урок: проверять транслитерации, а не один язык."""
import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "research-script", "Accept": "application/vnd.github+json"}
BASE = "https://api.github.com/search/repositories"

QUERIES = [
    # --- варианты написания КОМПАС (латиница, без привязки к теме) ---
    "kompas",
    "kompas 3d",
    "kompas-3d",
    "compas",
    "compas 3d",
    "compas3d",
    "kompas3d library",
    "kompas3d macro",
    "kompas sdk",
    "kompas automation",
    "ascon",
    "ascon kompas",
    "ascon cad",
    # --- КОМПАС + щиты (английские термины) ---
    "kompas cabinet",
    "kompas 3d cabinet",
    "kompas3d cabinet",
    "compas cabinet",
    "compas electrical",
    "kompas electrical cabinet",
    "kompas switchgear",
    "kompas panel",
    "kompas enclosure",
    "kompas electric",
    "kompas3d electrical",
    "kompas distribution board",
    # --- топики ---
    "topic:kompas",
    "topic:kompas3d",
    "topic:compas",
    "topic:ascon",
    "topic:control-cabinet",
    "topic:panel-building",
    "topic:electrical-engineering",
    # --- щитовые термины без CAD-привязки ---
    "switchgear cabinet",
    "control cabinet",
    "distribution board",
    "motor control center",
    "LV switchboard",
    "electrical panel design",
    "enclosure design",
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
            print("OK  %-32s total=%s" % (q, total), flush=True)
        if i < len(QUERIES) - 1:
            time.sleep(7)
    with open("gh_results3.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
