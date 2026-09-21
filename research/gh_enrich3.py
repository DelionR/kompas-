#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обогащение находок волны 3 (варианты написания КОМПАС + английские щитовые термины)."""
import json
import time
import urllib.request

UA = {"User-Agent": "research-script", "Accept": "application/vnd.github+json"}

REPOS = [
    # --- MCP и мосты к КОМПАС (конкуренты/соседи) ---
    "dwnmf/KOMPAS-3D-MCP-bin",
    "dwnmf/kompas-3d-guard-bin",
    "Alexey-Izilianov/kompas-mcp",
    "xomyachok-shaolin/kompas-3d-mcp-linux",
    "AntonSHBK/compas3d-mcp-server",
    "kuzadmin-coder/mcp-kompas-3D",
    # --- SDK, скриптовые языки, инструменты КОМПАС ---
    "Mikarisar/KOMPAS_tools",
    "Weltraum/Article-Python-in-aid-of-the-designer-Tames-API-Kompas-3D",
    "alexbereznikov/kompas-interp",
    "AntonPetunin/KompasSDKReference",
    "Dima-tyman/KOMPAS-SDK",
    "Leafan23/rapidly_part",
    "TemalProg/kompas-automation",
    "maximvaraxin/CAPP_create_model_automation",
    "Jestyrn/K-KompasPlacer",
    # --- параметрические сборки и плагины ---
    "tamoshka/Screwdriver-plugin-for-Compas3D",
    "toni3098/compas3D_krushka",
    "GregoryGhost/plugin-glass-for-compass3d",
    "robotsrulz/Signum",
    "AlexandrPerun/KOMPAS",
    # --- английские щитовые термины ---
    "managallment-svg/lv-panel-designer-pro",
    "AliMunzerShamma/Expert-System-For-Design-Electrical-Panels",
    "SenMorgan/Electrical-Panel-Label-Generator",
    "Waltberry/LV-Switchboard",
    "luisdanielsilva/MCC",
    # --- библиотеки компонентов и УГО ---
    "earthtojake/step.parts",
    "upb-lea/Inkscape_electric_Symbols",
]


def main():
    out = []
    for full in REPOS:
        req = urllib.request.Request("https://api.github.com/repos/" + full, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            out.append({
                "full_name": d["full_name"],
                "stars": d.get("stargazers_count", 0),
                "lang": d.get("language"),
                "license": (d.get("license") or {}).get("spdx_id"),
                "topics": d.get("topics", []),
                "desc": (d.get("description") or "").strip(),
                "url": d.get("html_url"),
                "created": d.get("created_at"),
                "pushed": d.get("pushed_at"),
                "archived": d.get("archived", False),
            })
            print("OK  %-58s %4d*  %s  %s" % (
                full, d.get("stargazers_count", 0),
                (d.get("pushed_at") or "")[:10],
                ((d.get("license") or {}).get("spdx_id") or "-")[:12]), flush=True)
        except Exception as e:
            out.append({"full_name": full, "error": str(e)})
            print("ERR", full, e, flush=True)
        time.sleep(0.4)
    with open("gh_enriched3.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
