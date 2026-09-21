"""Сборка каталога корпусов щитов из открытого каталога IEK.

Данные берутся из публичного каталога iek.ru, из блока ``apiListing`` на
страницах разделов. У каждой позиции там есть блок ``etim`` — характеристики по
классификатору ETIM, и именно оттуда берутся габарит, число модулей, число
рядов и степень защиты. Ничего не вычисляется и не подставляется «по типу
щита»: чего в ETIM нет, то остаётся ``None``.

Почему именно ETIM, а не текст карточки: характеристики нормализованы, у них
есть идентификатор признака (EF000008 — ширина, EF002950 — число модульных
расстояний), поэтому значение не надо выдирать из описания регуляркой.

Запуск::

    python tools/build_enclosure_catalog.py --out data/cabinet_enclosures.example.json

Скрипт не входит в состав моста: он только добывает данные, чтобы их можно
было проверить и пересобрать.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

BASE = "https://www.iek.ru"
# Пути в каталоге производителя живут под этим префиксом; сами ссылки в
# разметке отдаются без него.
CATALOG_PREFIX = "/products/catalog"

# Разделы щитового оборудования IEK, относящиеся к силовым щитам:
# модульные корпуса, корпуса и шкафы с монтажной панелью, напольные шкафы.
# Слаботочные и корпуса под счётчик сюда не берём — это не силовой щит.
SECTIONS = (
    ("Модульные корпуса пластиковые KREPTA",
     "/shchitovoe_oborudovanie/korpusa_plastikovye/korpusa_plastikovye_dlya_modulnogo_oborudovaniya_krepta/"),
    ("Модульные корпуса пластиковые TEKFOR",
     "/shchitovoe_oborudovanie/korpusa_plastikovye/korpusa_plastikovye_dlya_modulnogo_oborudovaniya_tekfor/"),
    ("Модульные корпуса пластиковые PRIME",
     "/shchitovoe_oborudovanie/korpusa_plastikovye/korpusa_plastikovye_dlya_modulnogo_oborudovaniya_prime/"),
    ("Модульные корпуса пластиковые UNION",
     "/shchitovoe_oborudovanie/korpusa_plastikovye/korpusa_plastikovye_dlya_modulnogo_oborudovaniya_union/"),
    ("Корпуса пластиковые с монтажной панелью TETRA",
     "/shchitovoe_oborudovanie/korpusa_plastikovye/korpusa_plastikovye_s_montazhnoy_panelyu_tetra/"),
    ("Корпуса металлические модульные TITAN",
     "/shchitovoe_oborudovanie/korpusa_metallicheskie_modulnye/korpusa_metallicheskie_raspredelitelnye_titan/"),
    ("Корпуса металлические с монтажной панелью TITAN",
     "/shchitovoe_oborudovanie/korpusa_metallicheskie_s_montazhnoy_panelyu/korpusa_metallicheskie_s_montazhnoy_panelyu_titan/"),
    ("Шкафы металлические напольные сварные TITAN",
     "/shchitovoe_oborudovanie/shkafy_metallicheskie_napolnye/shkafy_metallicheskie_napolnye_svarnye_titan/"),
    ("Шкафы металлические напольные разборные SMART",
     "/shchitovoe_oborudovanie/shkafy_metallicheskie_napolnye/shkafy_metallicheskie_napolnye_razbornye_smart/"),
    ("Шкафы металлические напольные FORMAT",
     "/shchitovoe_oborudovanie/shkafy_metallicheskie_napolnye/shkafy_metallicheskie_napolnye_format/"),
    ("Шкафы металлические напольные с монтажной панелью",
     "/shchitovoe_oborudovanie/shkafy_metallicheskie_napolnye/shkafy_metallicheskie_napolnye_s_montazhnoy_panelyu/"),
)

# Признаки ETIM, которые нам нужны. Идентификатор, а не название: название в
# публикации может поменяться, идентификатор признака — нет.
FEATURES = {
    "EF000008": "width_mm",        # Ширина
    "EF000040": "height_mm",       # Высота
    "EF000049": "depth_mm",        # Глубина
    "EF002950": "modules",         # Ширина по количеству модульных расстояний
    "EF000266": "rows",            # Кол-во рядов
    "EF005474": "ip",              # Степень защиты - IP
    "EF001596": "material",        # Материал корпуса
    "EF001134": "din_rail",        # DIN-рейка
    "EF000118": "mounting_plate",  # С монтажной платой
    "EF000003": "mounting",        # Тип монтажа
    "EF001094": "transparent_door",  # С прозрачной крышкой
}

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url: str) -> str:
    request = urllib.request.Request(BASE + CATALOG_PREFIX + url,
                               headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def _json_array(text: str, start_marker: str, opener: str, closer: str):
    """Вырезать массив или объект по балансу скобок от маркера."""
    index = text.find(start_marker)
    if index < 0:
        return []
    start = text.find(opener, index)
    if start < 0:
        return []
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:position + 1])
    return []


def products_of(html: str) -> list:
    return _json_array(html, '"apiListing":{"products":', "[", "]")


def next_data(html: str):
    """Блок __NEXT_DATA__ со страницы: в нём дерево категорий."""
    match = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def find_node(node, url: str):
    """Найти узел дерева категорий по его url.

    Дерево лежит внутри массивов (категории идут списком), поэтому обходятся
    и списки: без этого узел не находится, и подкатегории остаются за кадром.
    """
    if isinstance(node, list):
        for item in node:
            found = find_node(item, url)
            if found is not None:
                return found
        return None
    if not isinstance(node, dict):
        return None
    if node.get("url") == url:
        return node
    for child in node.get("children") or []:
        found = find_node(child, url)
        if found is not None:
            return found
    for value in node.values():
        found = find_node(value, url)
        if found is not None:
            return found
    return None


def leaf_urls(html: str, url: str) -> list:
    """Листья подкаталога.

    Листинг раздела отдает не больше ~80 позиций, но разделы делятся на
    подкатегории: без обхода листьев часть корпусов осталась бы за кадром.
    """
    data = next_data(html)
    if data is None:
        return []
    node = find_node(data, url)
    if node is None:
        return []
    result = []

    def walk(current):
        children = current.get("children") or []
        if not children:
            result.append((current.get("nameOrig") or current.get("name"),
                           current.get("url")))
            return
        for child in children:
            walk(child)

    walk(node)
    return [pair for pair in result if pair[1]]


def number(value) -> float | None:
    """Число из значения ETIM; прочерк и пустота — не число."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text or text == "-":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def integer(value) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def yes_no(value) -> bool | None:
    """«Да»/«Нет» из значения ETIM."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "да":
        return True
    if text == "нет":
        return False
    return None


def parse_product(product: dict, section: str) -> dict:
    etim = product.get("etim") or {}
    raw = {f.get("id"): f.get("value") for f in (etim.get("features") or [])}
    item = {
        "article": product.get("article"),
        "name": product.get("name"),
        "manufacturer": "IEK",
        "series": section,
        "kind": "enclosure",
        "url": BASE + CATALOG_PREFIX + product["url"] if product.get("url") else None,
        "etim_class": (etim.get("class") or {}).get("id"),
        "width_mm": number(raw.get("EF000008")),
        "height_mm": number(raw.get("EF000040")),
        "depth_mm": number(raw.get("EF000049")),
        "modules": integer(raw.get("EF002950")),
        "rows": integer(raw.get("EF000266")),
        "ip": raw.get("EF005474") if raw.get("EF005474") not in ("-", "") else None,
        "material": raw.get("EF001596"),
        "mounting": raw.get("EF000003"),
        "din_rail": yes_no(raw.get("EF001134")),
        "mounting_plate": yes_no(raw.get("EF000118")),
        "transparent_door": yes_no(raw.get("EF001094")),
        "archived": bool(product.get("isArchived") or product.get("isOutOfProduction")),
    }
    return item


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/cabinet_enclosures.example.json",
                        help="куда записать каталог")
    parser.add_argument("--keep-archived", action="store_true",
                        help="оставить снятые с производства позиции")
    args = parser.parse_args(argv)

    collected: list = []
    for label, path in SECTIONS:
        try:
            html = fetch(path)
        except Exception as error:  # сеть — не наша зона ответственности
            print("  пропуск %s: %s" % (label, error), file=sys.stderr)
            continue
        leaves = leaf_urls(html, path) or [(label, path)]
        total = 0
        for leaf_label, leaf_path in leaves:
            try:
                leaf_html = html if leaf_path == path else fetch(leaf_path)
            except Exception as error:
                print("  пропуск %s: %s" % (leaf_label, error), file=sys.stderr)
                continue
            found = products_of(leaf_html)
            total += len(found)
            for product in found:
                collected.append(parse_product(product, label))
        print("  %-52s %3d позиций (%d подкатегорий)"
              % (label, total, len(leaves)))

    seen = set()
    items = []
    for item in collected:
        key = item["article"]
        if not key or key in seen:
            continue
        seen.add(key)
        if item["archived"] and not args.keep_archived:
            continue
        items.append(item)

    # Позиция без габарита бесполезна для раскладки: она не молча попадает в
    # каталог, а уходит в отдельный список.
    sized = [i for i in items
             if i["width_mm"] and i["height_mm"] and i["depth_mm"]]
    unsized = [i for i in items if i not in sized]

    payload = {
        "table": "cabinet_enclosures",
        "example": True,
        "items": sized,
        "source": "Открытый каталог IEK, раздел «Щитовое оборудование» "
                  "(www.iek.ru/products/catalog/shchitovoe_oborudovanie/)",
        "source_detail": {
            "document": "Открытый каталог IEK, раздел «Щитовое оборудование»",
            "publisher": "IEK GROUP",
            "url": BASE + "/products/catalog/shchitovoe_oborudovanie/",
            "retrieved": __import__("datetime").date.today().isoformat(),
            "row_count": len(sized),
            "fields": "ETIM (EF000008, EF000040, EF000049, EF002950, EF000266, EF005474, EF001596, EF000003, EF001134, EF000118, EF001094)",
            "legal": "Значения взяты из публичного каталога производителя.",
            "note": ("Выборка: силовые щиты IEK — модульные корпуса, корпуса и "
                     "шкафы с монтажной панелью и напольные шкафы. Слаботочные "
                     "корпуса и корпуса под счётчик не включены. Это не полный "
                     "каталог производителя, поэтому файл помечен example."),
            "limits": ("Габарит монтажной панели производитель в ETIM не "
                       "публикует: поле plate_framed_mm остаётся None. "
                       "Полезная ширина по DIN-рейке не является паспортным "
                       "значением и считается отдельно."),
        },
        "unsized": [{"article": i["article"], "name": i["name"]} for i in unsized],
    }

    directory = os.path.dirname(args.out)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print("записано %s: %d позиций, без габарита %d"
          % (args.out, len(sized), len(unsized)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
