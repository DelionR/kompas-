"""Волна 5 поиска: MCP-серверы по щитовому оборудованию и электрике.

Вопрос Романа: есть ли бесплатные MCP-серверы для подбора автоматов, УЗО,
контакторов, реле, шин и для расчёта щитов. Ищем **MCP**, а не библиотеки.

Критерий годности: реальный MCP-сервер (tools/resources), отношение к
электротехнике или щитам, лицензия, живость.
"""

import json
import time
import urllib.parse
import urllib.request

QUERIES = [
    # щитовое оборудование как MCP
    "circuit breaker mcp",
    "electrical panel mcp",
    "switchgear mcp",
    "electrical mcp server",
    "mcp server electrical engineering",
    "IEC 61439 mcp",
    "electrical schematic mcp",
    "panel design mcp server",
    "component selection mcp",
    "electrical component catalog mcp",
    # CAD/ECAD как ориентир ландшафта
    "mcp server cad",
    "eplan mcp",
    "mcp server bom",
    "kicad mcp server",
    # агрегаторы и каталоги самих MCP
    "awesome mcp servers",
    "mcp servers catalog",
]

HEADERS = {"User-Agent": "kompas-mcp-research", "Accept": "application/vnd.github+json"}


def search(query, per_page=12):
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(query)
           + "&sort=stars&order=desc&per_page=%d" % per_page)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:
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
            "desc": (it.get("description") or "")[:220],
            "license": (it.get("license") or {}).get("spdx_id"),
            "updated": (it.get("pushed_at") or "")[:10],
            "topics": it.get("topics", [])[:8],
            "archived": it.get("archived", False),
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
        time.sleep(7)
    with open("gh_results5.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print("сохранено gh_results5.json")


if __name__ == "__main__":
    main()
