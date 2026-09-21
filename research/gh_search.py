#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск репозиториев GitHub по теме щитового оборудования / АСУ ТП / КОМПАС / SolidWorks."""
import json
import time
import urllib.parse
import urllib.request
import sys

UA = {"User-Agent": "research-script", "Accept": "application/vnd.github+json"}
BASE = "https://api.github.com/search/repositories"

QUERIES = [
    # --- RU: КОМПАС + щиты ---
    "компас щит",
    "компас шкаф",
    "компас библиотека щит",
    "компас НКУ",
    "компас АСУ",
    "компас электромонтаж",
    "компас электрощит",
    "компас машиностроение библиотека",
    "КОМПАС-3D библиотека",
    "компас макрос чертеж",
    # --- RU: щитовое оборудование ---
    "НКУ",
    "низковольтное комплектное устройство",
    "щит управления",
    "шкаф управления",
    "щитовая продукция",
    "сборка щитов",
    "проектирование щитов",
    "электрощит",
    # --- RU: АСУ ТП ---
    "АСУ ТП",
    "АСУТП",
    "КИПиА",
    "щит АСУ ТП",
    "мнемосхема",
    # --- EN: SolidWorks / CAD ---
    "solidworks cabinet",
    "solidworks enclosure",
    "solidworks electrical",
    "switchgear",
    "electrical cabinet",
    "control panel design",
    "panel builder software",
    "electrical enclosure 3d",
    "cabinet configurator",
    "industrial cabinet model",
    # --- topics ---
    "topic:kompas-3d",
    "topic:solidworks",
    "topic:switchgear",
    "topic:cabinet",
]


def search(q, per_page=25):
    url = BASE + "?" + urllib.parse.urlencode({"q": q, "per_page": per_page, "sort": "stars", "order": "desc"})
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa
        return None, str(e)
    items = []
    for it in d.get("items", []):
        items.append({
            "full_name": it["full_name"],
            "stars": it.get("stargazers_count", 0),
            "lang": it.get("language"),
            "desc": (it.get("description") or "").strip(),
            "url": it.get("html_url"),
            "pushed": it.get("pushed_at"),
            "archived": it.get("archived", False),
        })
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
            print("OK  %-45s total=%s" % (q, total), flush=True)
        if i < len(QUERIES) - 1:
            time.sleep(7)

    with open("gh_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
