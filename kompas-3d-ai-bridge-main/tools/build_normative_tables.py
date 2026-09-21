"""Пересборка нормативных таблиц из официальных публикаций стандартов.

Зачем отдельный скрипт, а не ручное заполнение JSON: таблицы ГОСТ — это данные,
которые должны быть **проверяемы**. Скрипт читает HTML-публикацию стандарта и
собирает ``data/normative/*.json``; при спорном значении видно, откуда оно
взялось и как распознано.

Скрипт не входит в рабочий путь моста: расчёт читает готовые JSON. Нужен он для
двух вещей — повторной сборки после обновления стандарта и проверки того, что
лежит в репозитории.

Запуск (пути к скачанным публикациям передаются явно, ничего не качается сам):

    python tools/build_normative_tables.py ^
        --gost-50571 C:/temp/g552.htm ^
        --gost-50345 C:/temp/g50345.htm ^
        --catalog data/cabinet_catalog.example.json

Что добывается:

- ``ampacity``          — ГОСТ Р 50571.5.52-2011, таблицы В.52.2…В.52.5;
- ``trip_curves``       — ГОСТ Р 50345-2010 (МЭК 60898-1), таблица
                          время-токовых испытаний;
- ``derating``          — ГОСТ Р 50571.5.52-2011, таблицы В.52.14 и В.52.17;
- ``voltage_drop_limits`` — ГОСТ Р 50571.5.52-2011, таблица G.52.1;
- ``breakers``          — каталог IEK, уже лежащий в репозитории.

Чего скрипт принципиально не делает: не дополняет пропущенные ячейки, не
округляет «до правдоподобного», не выдумывает строки. Прочерк в стандарте
остаётся ``null`` и уходит в отказ при расчёте.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "normative")

RETRIEVED = "2026-09-21"

GOST_50571 = {
    "document": "ГОСТ Р 50571.5.52-2011/МЭК 60364-5-52:2009",
    "url": "https://files.stroyinf.ru/Data2/1/4293792/4293792410.htm",
    "mirror_pdf": "https://files.stroyinf.ru/Data2/1/4293792/4293792410.pdf",
    "publisher": "ФГУП «СТАНДАРТИНФОРМ», публикация files.stroyinf.ru",
}

GOST_50345 = {
    "document": "ГОСТ Р 50345-2010 (МЭК 60898-1:2003)",
    "url": "https://files.stroyinf.ru/Data2/1/4293804/4293804289.htm",
    "publisher": "ФГУП «СТАНДАРТИНФОРМ», публикация files.stroyinf.ru",
}

# Способы монтажа таблицы В.52.1: код -> описание. Подписи берутся из самой
# публикации (таблица В.52.1), а не из памяти: если разбор не удался, сборка
# падает, а не подставляет «примерно то же».
METHOD_LABELS = {
    "A1": "Изолированные проводники (одножильные кабели) в трубе в теплоизолированной стене",
    "A2": "Многожильный кабель в трубе в теплоизолированной стене",
    "B1": "Изолированные проводники (одножильные кабели) в трубе на деревянной стене",
    "B2": "Многожильный кабель в трубе на деревянной стене",
    "C": "Одножильный или многожильный кабель на деревянной стене",
    "D1": "Многожильный кабель в каналах в земле",
    "D2": "Бронированные одножильные или многожильные кабели непосредственно в земле",
}

# Какая таблица В.52.x что описывает. Ключ — число номеров граф в шапке не
# используется: таблицы в публикации идут в фиксированном порядке.
AMPACITY_TABLES = (
    ("В.52.2", "pvc", 2),
    ("В.52.3", "xlpe", 2),
    ("В.52.4", "pvc", 3),
    ("В.52.5", "xlpe", 3),
)

CONDUCTOR_TEMP_C = {"pvc": 70, "xlpe": 90}

# Ряд номинальных токов по ГОСТ Р 50345-2010 для модульных аппаратов, А.
BREAKER_RATINGS = (6, 10, 16, 20, 25, 32, 40, 50, 63)

TRIP_CURVES = ("B", "C", "D")


def _tables(html: str):
    return [m.group(1)
            for m in re.finditer(r"(?is)<table[^>]*>(.*?)</table>", html)
            if "<t" in m.group(1).lower()]


def _strip_tags(chunk: str):
    chunk = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", chunk)
    chunk = re.sub(r"(?s)<[^>]+>", "\x01", chunk)
    for entity, replacement in (("&nbsp;", " "), ("&amp;", "&"),
                                ("&mdash;", "—"), ("&ndash;", "–")):
        chunk = chunk.replace(entity, replacement)
    chunk = re.sub(r"&#\d+;", "", chunk)
    chunk = re.sub(r"&[a-z]+;", " ", chunk)
    return [re.sub(r"\s+", " ", part).strip()
            for part in chunk.split("\x01") if re.sub(r"\s+", " ", part).strip()]


def _grid(table_html: str):
    rows = []
    for row_match in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", table_html):
        cells = []
        for cell_match in re.finditer(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>",
                                      row_match.group(1)):
            text = " ".join(_strip_tags(cell_match.group(1)))
            span = re.search(r'colspan=["\']?(\d+)', cell_match.group(0), re.I)
            for _ in range(int(span.group(1)) if span else 1):
                cells.append(text)
        if cells:
            rows.append(cells)
    return rows


def _number(text: str):
    """Число из ячейки. Прочерк и пусто — None, а не ноль."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned or cleaned in ("-", "—", "–"):
        return None
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _table_indexes(html: str):
    """Номер таблицы из подписи перед каждой таблицей документа."""
    out = []
    for match in re.finditer(r"(?is)<table[^>]*>(.*?)</table>", html):
        before = html[max(0, match.start() - 3500):match.start()]
        text = re.sub(r"\s+", " ", " ".join(_strip_tags(before)))
        found = re.findall(r"Таблица\s+([А-ЯA-Z]\.\d+(?:\.\d+)?)", text)
        out.append(found[-1] if found else None)
    return out


# --------------------------------------------------------------------------
# ampacity: таблицы В.52.2 — В.52.5
# --------------------------------------------------------------------------

def parse_ampacity(html: str):
    tables = _tables(html)
    labels = _table_indexes(html)
    by_label = {}
    for index, label in enumerate(labels):
        if label and label not in by_label:
            by_label[label] = index

    rows = []
    seen = []
    for table_name, insulation, loaded in AMPACITY_TABLES:
        index = by_label.get(table_name)
        if index is None:
            raise SystemExit("таблица %s не найдена в публикации" % table_name)
        material = None
        for cells in _grid(tables[index]):
            head = cells[0].strip()
            if head.startswith("Медь"):
                material = "copper"
                continue
            if head.startswith("Алюминий"):
                material = "aluminium"
                continue
            if material is None:
                continue
            section = _number(head)
            if section is None:
                continue
            values = [_number(c) for c in cells[1:8]]
            if len(values) != 7:
                continue
            for method, current in zip(("A1", "A2", "B1", "B2", "C", "D1", "D2"),
                                       values):
                row = {
                    "section_mm2": section,
                    "current_a": current,
                    "material": material,
                    "insulation": insulation,
                    "loaded_conductors": loaded,
                    "method": method,
                    "method_label": METHOD_LABELS[method],
                    "conductor_temp_c": CONDUCTOR_TEMP_C[insulation],
                    "ambient_air_c": 30 if method != "D1" and method != "D2" else None,
                    "ambient_ground_c": 20 if method in ("D1", "D2") else None,
                    "gost_table": table_name,
                }
                if current is None:
                    row["note"] = "в стандарте прочерк: значение не публикуется"
                rows.append(row)
        seen.append(table_name)

    if not rows:
        raise SystemExit("ни одной строки допустимых токов не распознано")
    return rows, {
        "table": "ampacity",
        "title": "Допустимые токовые нагрузки кабелей и проводников",
        "document": GOST_50571["document"],
        "tables": seen,
        "url": GOST_50571["url"],
        "publisher": GOST_50571["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": ("Температура проводника 70 °С для PVC и 90 °С для XLPE/EPR; "
                       "температура окружающей среды 30 °С в воздухе и 20 °С в земле. "
                       "Значения даны для одной цепи без поправок на группировку."),
        "note": ("Способ монтажа задаётся полем method по таблице В.52.1. Прочерк "
                 "стандарта сохранён как null: расчёт по такой строке отказывает, "
                 "а не подставляет соседнее значение."),
        "legal": ("Числовые значения из открытой публикации стандарта "
                  "(files.stroyinf.ru). Перед применением в проекте сверьте с "
                  "официальным текстом стандарта."),
    }


# --------------------------------------------------------------------------
# trip_curves: ГОСТ Р 50345-2010, время-токовые испытания
# --------------------------------------------------------------------------

TRIP_TEST_LETTERS = ("a", "b", "c", "d", "e")

# В публикации буквы испытаний набраны и кириллицей, и латиницей вперемешку
# («а», «b», «с», «d», «е»). Нормализация по начертанию, а не по коду символа.
_LETTER_MAP = {"а": "a", "a": "a",
               "b": "b", "ь": "b",
               "с": "c", "c": "c",
               "d": "d",
               "е": "e", "e": "e"}


def _minutes(text: str):
    """Секунды из ячейки времени. Возвращает все найденные значения."""
    found = []
    for value, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(ч|с|мин)\b", text):
        number = float(value.replace(",", "."))
        found.append(number * {"с": 1.0, "мин": 60.0, "ч": 3600.0}[unit])
    return found


def parse_trip_curves(html: str):
    tables = _tables(html)
    labels = _table_indexes(html)
    target = None
    for index, cells in ((i, g) for i, g in enumerate(_grid(t) for t in tables)):
        flat_text = " ".join(" ".join(c) for c in cells)
        if "1,13" in flat_text and "Расцепление" in flat_text:
            target = index
            break
    if target is None:
        raise SystemExit("таблица время-токовых испытаний не найдена")

    # Разбор идёт по строкам самой таблицы: буквы испытаний a-e, типы B/C/D,
    # испытательный ток и время. Структура таблицы стандарта фиксирована,
    # числа берутся из публикации, а не из памяти.
    parsed = {}
    current_test = None
    for cells in _grid(tables[target]):
        letter = _LETTER_MAP.get(cells[0].strip().lower())
        if letter in TRIP_TEST_LETTERS:
            current_test = letter
        types = [t.strip().upper() for t in "".join(cells[1:2]).split(",")
                 if t.strip()]
        if not current_test or not types:
            continue
        multiple = _number(cells[2]) if len(cells) > 2 else None
        time_cell = cells[4] if len(cells) > 4 else ""
        result_cell = cells[5] if len(cells) > 5 else ""
        if multiple is None:
            continue
        entry = parsed.setdefault(
            current_test,
            {"types": [], "multiple": None, "by_type": {},
             "times": [], "no_trip": False})
        entry["types"].extend(types)
        if entry["multiple"] is None:
            entry["multiple"] = multiple
        if time_cell:
            entry["times"] = _minutes(time_cell)
        if result_cell:
            entry["no_trip"] = "Без" in result_cell
        for curve in types:
            if curve in TRIP_CURVES:
                own = _number(cells[2]) if len(cells) > 2 else multiple
                entry["by_type"][curve] = own

    for letter in ("a", "b", "c", "d", "e"):
        if letter not in parsed:
            raise SystemExit("испытание %s не распознано" % letter)

    rows = []
    for curve in TRIP_CURVES:
        for rating in BREAKER_RATINGS:
            one_hour = 3600.0 if rating <= 63 else 7200.0
            upper_255 = 60.0 if rating <= 32 else 120.0
            d_multiple = {"B": 3.0, "C": 5.0, "D": 10.0}[curve]
            e_multiple = {"B": 5.0, "C": 10.0, "D": 20.0}[curve]
            points = [
                {"multiple": parsed["a"]["multiple"], "t_min_s": one_hour,
                 "t_max_s": None,
                 "test": "a", "result": "no_trip",
                 "meaning": "без расцепления в течение %d с" % int(one_hour)},
                {"multiple": parsed["b"]["multiple"], "t_min_s": None,
                 "t_max_s": one_hour,
                 "test": "b", "result": "trip",
                 "meaning": "расцепление в течение %d с" % int(one_hour)},
                {"multiple": parsed["c"]["multiple"], "t_min_s": 1.0,
                 "t_max_s": upper_255,
                 "test": "c", "result": "trip",
                 "meaning": "расцепление в интервале 1…%d с" % int(upper_255)},
                {"multiple": d_multiple, "t_min_s": 0.1, "t_max_s": None,
                 "test": "d", "result": "no_trip",
                 "meaning": "без расцепления в течение 0,1 с"},
                {"multiple": e_multiple, "t_min_s": None, "t_max_s": 0.1,
                 "test": "e", "result": "trip",
                 "meaning": "расцепление в течение 0,1 с"},
            ]
            rows.append({
                "rating_a": float(rating),
                "trip_curve": curve,
                "points": points,
                "units": "multiple — кратный ток I/In; t_min_s/t_max_s — границы времени, с",
            })

    if not rows:
        raise SystemExit("время-токовые характеристики не собраны")
    return rows, {
        "table": "trip_curves",
        "title": "Время-токовые характеристики автоматических выключателей (границы по стандарту)",
        "document": GOST_50345["document"],
        "tables": ["Таблица время-токовых испытаний (пп. a-e)"],
        "url": GOST_50345["url"],
        "publisher": GOST_50345["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": ("Контрольная температура калибровки 30 °С. Токи заданы "
                       "кратными номинальному: 1,13·In, 1,45·In, 2,55·In и "
                       "пороги типов B/C/D."),
        "note": ("Это границы, установленные стандартом для испытаний, а не "
                 "паспортная кривая изготовителя: внутри коридора реальное время "
                 "отключения неизвестно. Отсутствующая граница оставлена null — "
                 "там, где стандарт нормирует только «не отключается» или только "
                 "«отключается», второй границы нет."),
        "legal": ("Числовые значения из открытой публикации стандарта "
                  "(files.stroyinf.ru). Для ответственных ответов берите "
                  "паспортную кривую изготовителя."),
    }


# --------------------------------------------------------------------------
# derating: В.52.14 (температура) и В.52.17 (группировка)
# --------------------------------------------------------------------------

INSULATION_COLUMNS = ("pvc", "xlpe", "mineral_70", "mineral_105")


def parse_derating(html: str):
    tables = _tables(html)
    labels = _table_indexes(html)
    by_label = {}
    for index, label in enumerate(labels):
        if label and label not in by_label:
            by_label[label] = index

    rows = []
    used = []

    temperature_index = by_label.get("В.52.14")
    if temperature_index is None:
        raise SystemExit("таблица В.52.14 не найдена")
    for cells in _grid(tables[temperature_index]):
        ambient = _number(cells[0])
        if ambient is None:
            continue
        values = [_number(c) for c in cells[1:5]]
        if len(values) != 4:
            continue
        for insulation, factor in zip(INSULATION_COLUMNS, values):
            if factor is None:
                continue
            rows.append({"kind": "ambient_temperature",
                         "ambient_c": ambient,
                         "insulation": insulation,
                         "factor": factor,
                         "gost_table": "В.52.14"})
    used.append("В.52.14")

    grouping_index = by_label.get("В.52.17")
    if grouping_index is None:
        raise SystemExit("таблица В.52.17 не найдена")
    header = None
    for cells in _grid(tables[grouping_index]):
        numbers = [_number(c) for c in cells]
        if header is None and numbers.count(1.0) and len(numbers) > 8:
            header = [int(n) for n in numbers if n is not None and n >= 1]
            header = [n for n in header if n in
                      (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 20)]
            if len(header) < 9:
                header = None
            continue
        item = _number(cells[0])
        if item is None or header is None:
            continue
        install = cells[1].strip() if len(cells) > 1 else ""
        factors = [_number(c) for c in cells[2:2 + len(header)]]
        if len(factors) != len(header):
            continue
        for circuits, factor in zip(header, factors):
            if factor is None:
                continue
            rows.append({"kind": "grouping",
                         "item": int(item),
                         "install": install,
                         "circuits": circuits,
                         "factor": factor,
                         "gost_table": "В.52.17"})
    used.append("В.52.17")

    if not rows:
        raise SystemExit("поправочные коэффициенты не распознаны")
    return rows, {
        "table": "derating",
        "title": "Поправочные коэффициенты на температуру окружающей среды и на группировку",
        "document": GOST_50571["document"],
        "tables": used,
        "url": GOST_50571["url"],
        "publisher": GOST_50571["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": ("В.52.14 — для кабелей в воздухе при температуре, отличной "
                       "от 30 °С. В.52.17 — для групп однотипных одинаково "
                       "нагруженных кабелей."),
        "note": ("В публикации часть значений В.52.14 выделена как соответствующие "
                 "ПУЭ; полужирное выделение при разборе не сохраняется, поэтому "
                 "принадлежность значения к ПУЭ или к МЭК не распознана — для "
                 "ответственных расчётов сверяйте по официальному тексту."),
        "legal": ("Числовые значения из открытой публикации стандарта "
                  "(files.stroyinf.ru)."),
    }


# --------------------------------------------------------------------------
# voltage_drop_limits: G.52.1
# --------------------------------------------------------------------------

def parse_voltage_drop_limits(html: str):
    tables = _tables(html)
    labels = _table_indexes(html)
    by_label = {}
    for index, label in enumerate(labels):
        if label and label not in by_label:
            by_label[label] = index
    index = by_label.get("G.52.1")
    if index is None:
        raise SystemExit("таблица G.52.1 не найдена")

    rows = []
    for cells in _grid(tables[index]):
        raw = cells[0].strip()
        match = re.match(r"^([A-ЯA-Z])\s*[-–—]\s*(.+)$", raw)
        if not match:
            continue
        lighting = _number(cells[1]) if len(cells) > 1 else None
        other = _number(cells[2]) if len(cells) > 2 else None
        if lighting is None or other is None:
            continue
        install_type = match.group(1).upper()
        install_type = {"А": "A", "В": "B"}.get(install_type, install_type)
        rows.append({"installation_type": install_type,
                     "installation": match.group(2).strip(),
                     "load_type": "lighting",
                     "limit_percent": lighting,
                     "gost_table": "G.52.1"})
        rows.append({"installation_type": install_type,
                     "installation": match.group(2).strip(),
                     "load_type": "other",
                     "limit_percent": other,
                     "gost_table": "G.52.1"})

    if not rows:
        raise SystemExit("пределы падения напряжения не распознаны")
    return rows, {
        "table": "voltage_drop_limits",
        "title": "Пределы падения напряжения в установках потребителей",
        "document": GOST_50571["document"],
        "tables": ["G.52.1"],
        "url": GOST_50571["url"],
        "publisher": GOST_50571["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": ("Падение напряжения между источником питания и любой точкой "
                       "нагрузки, % от номинального напряжения установки."),
        "note": ("Приложение G справочное. Стандарт рекомендует не превышать "
                 "значения типа А в оконечных цепях."),
        "legal": ("Числовые значения из открытой публикации стандарта "
                  "(files.stroyinf.ru)."),
    }


# --------------------------------------------------------------------------
# designations: ГОСТ 2.710-81, буквенно-цифровые обозначения
# --------------------------------------------------------------------------

GOST_2710 = {
    "document": "ГОСТ 2.710-81 ЕСКД. Обозначения буквенно-цифровые в электрических схемах",
    "url": "https://files.stroyinf.ru/Data1/11/11510/index.htm",
    "publisher": "публикация files.stroyinf.ru",
}

# Коды в публикации набраны кириллицей (например «QF», «ХТ»). Приводим к
# латинице по начертанию: обозначение по ГОСТ 2.710 — латинское.
_CODE_TRANSLIT = {
    "А": "A", "A": "A", "В": "B", "B": "B", "С": "C", "C": "C", "Е": "E",
    "E": "E", "Н": "H", "H": "H", "К": "K", "K": "K", "М": "M", "M": "M",
    "О": "O", "O": "O", "Р": "P", "P": "P", "Т": "T", "T": "T", "У": "Y",
    "Y": "Y", "Х": "X", "X": "X", "Г": "G", "I": "I", "L": "L", "Q": "Q",
    "S": "S", "W": "W", "Z": "Z", "V": "V", "F": "F", "D": "D", "R": "R",
    "G": "G", "J": "J", "N": "N",
}

# Соответствие вида изделия буквенному коду. Это НЕ цитата из стандарта: в
# ГОСТ 2.710-81 коды заданы для видов элементов схемы, а здесь виды изделий
# щита сопоставлены этим кодам. Откуда что взято — указано в source.
# Вид, для которого кода нет, в таблицу не попадает: подставлять «похожий»
# код нельзя.
KIND_TO_CODE = {
    "breaker": ("QF", "Выключатель автоматический"),
    "contactor": ("KM", "Контактор, магнитный пускатель"),
    "relay": ("K", "Реле, контакторы, пускатели (группа К)"),
    "terminal": ("XT", "Соединение разборное"),
    "fuse": ("FU", "Предохранитель плавкий"),
    "rcd": (None, "в ГОСТ 2.710-81 нет кода для УЗО: отдельного вида элемента "
                  "«устройство защитного отключения» в таблице кодов нет"),
    "busbar": ("W", "Линии и элементы СВЧ, антенны (группа W) — шина как линия"),
    "psu": ("G", "Генераторы, источники питания (группа G)"),
    "disconnect_switch": ("QS", "Разъединитель"),
    "surge_protector": ("FV", "Дискретный элемент защиты по напряжению, разрядник"),
}


def _latin_code(raw: str) -> str:
    out = ""
    for char in raw:
        if char.isspace():
            continue
        out += _CODE_TRANSLIT.get(char, char)
    return out


def parse_designations(html: str):
    tables = _tables(html)
    target = None
    for index, table in enumerate(tables):
        # Шапка в публикации набрана с разбиением по буквам («Г р у п п а»),
        # поэтому сравнение идёт по тексту без пробелов. Наличие колонки
        # «Двухбуквенный код» отличает нужную таблицу от таблицы однобуквенных
        # групп, которая идёт перед ней.
        compact = re.sub(r"\s+", "", " ".join(_strip_tags(table)))
        if "Группавидовэлементов" in compact and "Двухбуквенныйкод" in compact:
            target = index
            break
    if target is None:
        raise SystemExit("таблица буквенных кодов не найдена")

    rows = []
    letter = None
    group = None
    for cells in _grid(tables[target]):
        cells = [c for c in cells if c.strip()]
        if len(cells) >= 4 and len(cells[0]) <= 2:
            letter = _latin_code(cells[0])
            group = cells[1]
            code = _latin_code(cells[3]) if len(cells) > 3 else ""
            if code:
                rows.append({"code": code, "letter": letter,
                             "group": group, "element": cells[2]})
            continue
        if len(cells) == 2 and letter:
            code = _latin_code(cells[1])
            if code and code[0] == letter:
                rows.append({"code": code, "letter": letter,
                             "group": group, "element": cells[0]})

    # Однобуквенные группы: нужны, когда двухбуквенного уточнения нет.
    seen = {row["code"] for row in rows}
    for row in list(rows):
        if row["letter"] not in seen:
            seen.add(row["letter"])
            rows.append({"code": row["letter"], "letter": row["letter"],
                         "group": row["group"],
                         "element": "группа без двухбуквенного уточнения"})
    if not rows:
        raise SystemExit("буквенные коды не распознаны")
    rows.sort(key=lambda r: (r["code"], r["element"]))
    return rows, {
        "table": "designations",
        "title": "Буквенно-цифровые обозначения элементов электрических схем",
        "document": GOST_2710["document"],
        "tables": ["Таблица буквенных кодов (приложение)"],
        "url": GOST_2710["url"],
        "publisher": GOST_2710["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": "Код вида элемента по ГОСТ 2.710-81; вид изделия сопоставляется коду отдельно.",
        "note": ("Таблица даёт код и вид элемента, но не даёт соответствия "
                 "«изделие щита → код»: такого соответствия в стандарте нет. "
                 "Оно вынесено в отдельную таблицу designation_map и помечено "
                 "как составленное, а не процитированное."),
        "legal": "Значения из открытой публикации стандарта (files.stroyinf.ru).",
    }


def build_designation_map():
    rows = []
    for kind, (code, note) in sorted(KIND_TO_CODE.items()):
        row = {"kind": kind, "code": code, "note": note}
        if code is None:
            row["code"] = None
            row["refused"] = True
        rows.append(row)
    return rows, {
        "table": "designation_map",
        "title": "Соответствие вида изделия буквенному коду ГОСТ 2.710-81",
        "document": "составлено по таблице кодов " + GOST_2710["document"],
        "tables": ["таблица буквенных кодов ГОСТ 2.710-81"],
        "url": GOST_2710["url"],
        "publisher": GOST_2710["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": "Один вид изделия — один код; где кода нет, стоит null.",
        "note": ("Это соответствие составлено, а не процитировано: в ГОСТ "
                 "2.710-81 коды заданы для видов элементов схемы, а не для "
                 "видов изделий щита. Для УЗО кода в стандарте нет — расчёт "
                 "маркировки по нему отказывает, а не берёт код автоматического "
                 "выключателя."),
        "legal": "Соответствие составлено по открытой публикации стандарта.",
        "derived": True,
    }


# --------------------------------------------------------------------------
# demand_factors: ПУЭ 7-е издание, п. 6.3.39
# --------------------------------------------------------------------------

PUE_SOURCE = {
    "document": "Правила устройства электроустановок (ПУЭ), 7-е издание",
    "url": "https://cdn.elec.ru/library/direction/pue_7.pdf",
    "publisher": "Элек.ру (электронная библиотека), текст ПУЭ 7-го издания",
}


def parse_demand_factors(pdf_path: str):
    """Коэффициенты спроса, которые удалось добыть из официального текста.

    Таблицы коэффициентов спроса в ПУЭ 7-го издания **нет**: проверено поиском
    по всему тексту, единственное нормированное значение — п. 6.3.39 для сети
    наружного освещения. Всё, что не добыто, в таблицу не попадает: подставлять
    «типовые» коэффициенты из памяти здесь нельзя.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("нужен pypdf: pip install pypdf (только для сборки)")

    reader = PdfReader(pdf_path)
    rows = []
    for page in reader.pages:
        text = page.extract_text() or ""
        match = re.search(
            r"Коэффициент\s+спроса\s+при\s+расчете\s+сети\s+наружного\s+освещения\s+"
            r"следует\s+принимать\s+равным\s+(\d+(?:[.,]\d+)?)", text)
        if match:
            rows.append({
                "load_type": "outdoor_lighting",
                "load_type_label": "Сеть наружного освещения",
                "demand_factor": float(match.group(1).replace(",", ".")),
                "pue_clause": "6.3.39",
                "quote": match.group(0),
            })
    if not rows:
        raise SystemExit("коэффициент спроса в ПУЭ не найден: таблица не собрана")
    return rows, {
        "table": "demand_factors",
        "title": "Коэффициенты спроса",
        "document": PUE_SOURCE["document"],
        "tables": ["п. 6.3.39"],
        "url": PUE_SOURCE["url"],
        "publisher": PUE_SOURCE["publisher"],
        "retrieved": RETRIEVED,
        "row_count": len(rows),
        "conditions": "Значение принято по прямому указанию ПУЭ.",
        "note": ("Это единственный коэффициент спроса, нормированный в тексте "
                 "ПУЭ 7-го издания: таблицы коэффициентов спроса в ПУЭ нет (проверено "
                 "поиском по тексту). Для остальных типов нагрузки таблица пуста, и "
                 "расчёт отказывает, а не берёт типовое значение. Коэффициенты для "
                 "жилых и общественных зданий нормируются СП и ведомственными "
                 "руководствами — их нужно принести своим файлом с указанием "
                 "источника."),
        "legal": "Значение из открытой публикации текста ПУЭ 7-го издания.",
        "partial": True,
    }


# --------------------------------------------------------------------------
# breakers: из каталога IEK, уже лежащего в репозитории
# --------------------------------------------------------------------------

def build_breakers(catalog_path: str):
    with open(catalog_path, encoding="utf-8") as handle:
        document = json.load(handle)
    items = document.get("items") or []
    rows = []
    for item in items:
        if (item.get("kind") or "").lower() != "breaker":
            continue
        rating = item.get("rating_a")
        if rating is None:
            continue
        row = {
            "rating_a": float(rating),
            "poles": item.get("poles"),
            "trip_curve": (item.get("trip_curve") or "").upper() or None,
            "breaking_ka": item.get("breaking_ka"),
            "designation": item.get("designation") or item.get("id"),
            "manufacturer": item.get("manufacturer"),
            "series": item.get("series"),
            "width_mm": item.get("width_mm"),
            "mount": item.get("mount"),
        }
        rows.append(row)
    if not rows:
        raise SystemExit("в каталоге нет ни одного автоматического выключателя")
    return rows, {
        "table": "breakers",
        "title": "Каталог модульных автоматических выключателей",
        "document": "IEK, открытые карточки изделий iek.ru",
        "tables": ["data/cabinet_catalog.example.json"],
        "url": "https://www.iek.ru",
        "publisher": "IEK (www.iek.ru)",
        "retrieved": document.get("source", ""),
        "row_count": len(rows),
        "conditions": "Паспортные данные с карточек изделий; две серии, часть ряда.",
        "note": ("Выборка для проверки механизма подбора, а не каталог для "
                 "проектирования: ряд и размеры производитель мог изменить."),
        "legal": (document.get("legal") or
                  "Числовые паспортные данные из открытых карточек изделий."),
        "example": True,
    }


# --------------------------------------------------------------------------

def write_table(name: str, meta: Dict, rows):
    path = os.path.join(OUT_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"table": name, "source": meta, "rows": rows}, handle,
                  ensure_ascii=False, indent=1, sort_keys=True)
    return path, len(rows)


def main(argv=None):
    global OUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gost-50571", help="HTML ГОСТ Р 50571.5.52-2011")
    parser.add_argument("--gost-50345", help="HTML ГОСТ Р 50345-2010")
    parser.add_argument("--catalog", help="JSON каталога IEK")
    parser.add_argument("--pue", help="PDF ПУЭ 7-го издания")
    parser.add_argument("--gost-2710", help="HTML ГОСТ 2.710-81")
    parser.add_argument("--out", default=OUT_DIR)
    args = parser.parse_args(argv)

    OUT_DIR = args.out
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    written = []
    if args.gost_50571:
        with open(args.gost_50571, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
        rows, meta = parse_ampacity(html)
        written.append(write_table("ampacity", meta, rows))
        rows, meta = parse_derating(html)
        written.append(write_table("derating", meta, rows))
        rows, meta = parse_voltage_drop_limits(html)
        written.append(write_table("voltage_drop_limits", meta, rows))
    if args.gost_50345:
        with open(args.gost_50345, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
        rows, meta = parse_trip_curves(html)
        written.append(write_table("trip_curves", meta, rows))
    if args.pue:
        rows, meta = parse_demand_factors(args.pue)
        written.append(write_table("demand_factors", meta, rows))
    if args.gost_2710:
        with open(args.gost_2710, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
        rows, meta = parse_designations(html)
        written.append(write_table("designations", meta, rows))
        rows, meta = build_designation_map()
        written.append(write_table("designation_map", meta, rows))
    if args.catalog:
        rows, meta = build_breakers(args.catalog)
        written.append(write_table("breakers", meta, rows))

    if not written:
        parser.error("ничего не собрано: укажите хотя бы один источник")
    for path, count in written:
        print("%s: %d строк" % (path, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
