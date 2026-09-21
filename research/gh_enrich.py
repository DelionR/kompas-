#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дособрать метаданные по отобранным репозиториям через core API GitHub."""
import json
import time
import urllib.request

UA = {"User-Agent": "research-script", "Accept": "application/vnd.github+json"}

REPOS = [
    # щиты / НКУ / АСУ ТП
    "Taam4142/cabinet-layout-generator-v2",
    "Taamrock04/cabinet-layout-generator",
    "postall74/TaCP",
    "Samidou-Automation/industrial-control-cabinet-eplan",
    "nomnom033-sys/nku-docs-checker",
    "martynov-alex/dkc-cabinet-configurator-flutter",
    "jsalliotte/CabinetConfigurator",
    "pakhomovnikolay/Project_Configurator",
    "foasyaw/perechni-signalov",
    "SachkovDI/PCS-CAD",
    "ctanto333/AntoCAD",
    "KoperekPL/Electrical_Cabinet_Layout",
    "Bossixd/dirac-assembly-simulation",
    "lweinstock/KiCad_Electrical",
    "CameronBrooks11/parametric-electrical-enclosure",
    "orionkht-cmd/switchgear-estimate-app",
    "noovikov/virtual-asutp-stand",
    "Skeeter-spec/industrial-index",
    "Skeeter-spec/power-service-toolbox",
    "Shchelkonogov/MnemonicScheme",
    "covagashi/eplan-rag-mcp",
    "savushkin-r-d/EasyEPLANner",
    "Suplanus/EPLAN-Scripting",
    "Suplanus/Suplanus.Sepla",
    "Hopelezz/Eplan",
    "annavannagu/eplan_addin_export_parts_to_xls",
    "TechFlipsi/FlipsiTherm",
    "chaimarochdi-etu-collab/outil-predimensionnement-bt",
    "jhonat23/IEC-60895-busbar-shortcircuit-calculation",
    "sergekomarov/object-detection-s2ds",
    "ch5721032-arch/gstarcad-electrical-tools",
    "Slacker-LLC/solidworks-mcp",
    "infei12306/solidworks-bridge",
    "danielproxd2/solidworks-mcp",
    "ANYXLB/solidworks-mcp-pro",
    "just1step/solidworks-mcp",
    "ANYLXB/solidworks-mcp-pro",
    "Cai-aa/CAD-Agent-Hub",
    "U-C4N/Autocad-MCP",
    "AnCode666/multiCAD-mcp",
    "Bogruina/DFCAD",
    "frei480/-22-SDK",
    "Sapchanskiy/CADCUP",
    "wikijerry/CAD-Automation-pipeline",
    "Erfouni/solidworks-drawing-api-2026",
    # КОМПАС
    "kostia-egik/geomwright",
    "EMX302/AI-to-CAD-Bridge-SW-",
    "hymaxo/kompas3d-mcp",
    "ortman/Kompas3DPrint",
    "nikitamamay/romashki-macros",
    "nikitamamay/KompasAPI-01",
    "nikitamamay/KompasAPI-stubs-generator",
    "afedorov3/Kompas3D-libs",
    "U-Board/CKE_converter-from-Kompas3D-to-excel",
    "baalp/TheScrewdriverGenerator",
    "Paashik/LibAccess",
    "IKEENOO/3DTreadsLib",
    "dimak222/KSaver",
    # SolidWorks
    "alisamsam/Solidworks-MCP",
    "wzyn20051216/solidworks-automation-skill",
    "Linmoqian/solidworks-autodesign",
    "xarial/xcad",
    "xarial/codestack",
    "CAD-Booster/SolidDNA",
    "codestackdev/swex-addin",
    "weianweigan/SolidWorksLookup",
    "mklanac/pySolidWorks",
    "Glutenberg/swtoolkit",
    "piresig/Excel-Automation",
    "OmniaGit/odooplm",
    "david-dorf/ExportURDF",
]


def main():
    out = []
    for full in REPOS:
        url = "https://api.github.com/repos/" + full
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            out.append({
                "full_name": d["full_name"],
                "stars": d.get("stargazers_count", 0),
                "forks": d.get("forks_count", 0),
                "issues": d.get("open_issues_count", 0),
                "lang": d.get("language"),
                "license": (d.get("license") or {}).get("spdx_id"),
                "topics": d.get("topics", []),
                "desc": (d.get("description") or "").strip(),
                "url": d.get("html_url"),
                "created": d.get("created_at"),
                "pushed": d.get("pushed_at"),
                "archived": d.get("archived", False),
            })
            print("OK  %-52s %4d*  %s" % (full, d.get("stargazers_count", 0), (d.get("pushed_at") or "")[:10]), flush=True)
        except Exception as e:
            out.append({"full_name": full, "error": str(e)})
            print("ERR", full, e, flush=True)
        time.sleep(0.4)
    with open("gh_enriched.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
