"""Волна 4 поиска: opensource 3D-геометрия щитового оборудования.

Вопрос Романа: можно ли взять готовую библиотеку компонентов с GitHub вместо
габаритных блоков. Ищем не код, а **модели**: STEP / STL / BREP / параметрические
детали автоматов, клемм, DIN-реек, корпусов.

Критерий годности потом: формат, который КОМПАС умеет импортировать, лицензия,
разрешение (настоящая геометрия, а не картинка), размер библиотеки.
"""

import json
import time
import urllib.parse
import urllib.request

QUERIES = [
    # щитовое оборудование как геометрия
    "circuit breaker step",
    "circuit breaker 3d model",
    "din rail 3d model",
    "din rail step file",
    "terminal block 3d model",
    "contactor 3d model",
    "electrical cabinet 3d",
    "switchgear 3d model",
    "electrical enclosure step",
    "distribution board 3d",
    # библиотеки целиком
    "electrical cad library 3d",
    "electrical components 3d library",
    "panel components 3d library",
    "cabinet hardware 3d library",
    "enclosure 3d library",
    # поставщики
    "schneider electric 3d model",
    "abb circuit breaker 3d",
    "eaton 3d model",
    "phoenix contact 3d",
    "siemens 3d model step",
    # форматы и экосистема
    "topic:step-files",
    "topic:3d-models",
    "topic:cad-library",
    "topic:stl",
    "electrical parts library freecad",
    "freecad electrical library",
]

HEADERS = {
    "User-Agent": "kompas-mcp-research",
    "Accept": "application/vnd.github+json",
}


def search(query, per_page=15):
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(query)
           + "&sort=stars&order=desc&per_page=%d" % per_page)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:                      # лимит, сеть, что угодно
        return {"error": str(exc), "query": query}
    if "items" not in data:
        return {"error": data.get("message", "no items"), "query": query}
    return {
        "query": query,
        "total": data.get("total_count", 0),
        "items": [{
            "full_name": it["full_name"],
            "stars": it["stargazers_count"],
            "lang": it.get("language"),
            "desc": (it.get("description") or "")[:200],
            "license": (it.get("license") or {}).get("spdx_id"),
            "updated": (it.get("pushed_at") or "")[:10],
            "topics": it.get("topics", [])[:8],
        } for it in data["items"]],
    }


def main():
    results = {}
    for i, query in enumerate(QUERIES, 1):
        res = search(query)
        results[query] = res
        if "error" in res:
            print("[%2d/%d] ОШИБКА %s -> %s" % (i, len(QUERIES), query, res["error"][:60]))
        else:
            print("[%2d/%d] %-38s total=%-6d top=%s"
                  % (i, len(QUERIES), query, res["total"],
                     res["items"][0]["full_name"] if res["items"] else "-"))
        time.sleep(7)          # лимит без токена: 10 запросов в минуту
    with open("gh_results4.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print("сохранено gh_results4.json")


if __name__ == "__main__":
    main()
