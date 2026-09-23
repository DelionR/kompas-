"""Версия AutoCAD и матрица подтверждённых сборок.

Почему версия проверяется вообще
--------------------------------
AutoCAD наружу отдаёт COM-объект с именем ``AutoCAD.Application.<R>``, где
``<R>`` — номер релиза, а не года выпуска. Ошибиться легко: 2019 год — это
релиз R23, а 2020 — R23.1. Если подключиться не к тому ProgID, получим либо
отказ COM, либо — что хуже — **чужой запущенный экземпляр другой версии**,
в котором поведение API отличается.

Откуда число релиза
-------------------
Схема взята из опубликованной таблицы ProgID (AutoCAD 2004–2024) и
подтверждена для нужной нам версии: **AutoCAD 2019 → R23 →
``AutoCAD.Application.23``**. AutoCAD 2020 → R23.1, 2021 → R24, 2024 → R24.3.
Ничего сверх этой таблицы здесь не выдумано: релиз, которого нет в
``RELEASE_TO_PROGID``, не угадывается, а приводит к отказу.

Матрица подтверждённых версий
-----------------------------
Устроена ровно так же, как в мосте КОМПАС: **по умолчанию пуста**, уровень
``unverified``, что означает «не проверено», а не «разрешено». Заполняет её
оператор на машине, где AutoCAD действительно стоит. Пока матрица пуста,
мост не делает вид, что сборка подтверждена.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Номер релиза → ProgID. Подтверждено таблицей ProgID Autodesk (2019 → R23).
RELEASE_TO_PROGID: Dict[str, str] = {
    "16": "AutoCAD.Application.16",
    "16.1": "AutoCAD.Application.16.1",
    "16.2": "AutoCAD.Application.16.2",
    "17": "AutoCAD.Application.17",
    "17.1": "AutoCAD.Application.17.1",
    "17.2": "AutoCAD.Application.17.2",
    "18": "AutoCAD.Application.18",
    "18.1": "AutoCAD.Application.18.1",
    "19.1": "AutoCAD.Application.19.1",
    "20": "AutoCAD.Application.20",
    "20.1": "AutoCAD.Application.20.1",
    "21": "AutoCAD.Application.21",
    "22": "AutoCAD.Application.22",
    "23": "AutoCAD.Application.23",
    "23.1": "AutoCAD.Application.23.1",
    "24": "AutoCAD.Application.24",
    "24.1": "AutoCAD.Application.24.1",
    "24.2": "AutoCAD.Application.24.2",
    "24.3": "AutoCAD.Application.24.3",
}

# Год выпуска → номер релиза. Нужно потому, что окно «О программе» отдаёт год
# («P.162.0.0 AutoCAD 2019.1.2»), а свойство Version — релиз («23.0s (LMS
# Tech)»). Та же таблица ProgID, только по другой оси.
YEAR_TO_RELEASE: Dict[str, str] = {
    "2014": "19.1",
    "2015": "20",
    "2016": "20.1",
    "2017": "21",
    "2018": "22",
    "2019": "23",
    "2020": "23.1",
    "2021": "24",
    "2022": "24.1",
    "2023": "24.2",
    "2024": "24.3",
}

# Целевая сборка, под которую писался адаптер.
TARGET_RELEASE = "23"
TARGET_LABEL = "AutoCAD 2019 (R23)"

# Уровни поддержки. Порядок — от наиболее подтверждённого к наименее.
CERTIFIED = "certified"
TARGETED = "targeted"
EXPERIMENTAL = "experimental"
UNSUPPORTED = "unsupported"
UNVERIFIED = "unverified"

SUPPORT_LEVELS: Tuple[str, ...] = (CERTIFIED, TARGETED, EXPERIMENTAL,
                                   UNSUPPORTED, UNVERIFIED)
ALLOWED_LEVELS: Tuple[str, ...] = (CERTIFIED, TARGETED)
DEFAULT_LEVEL = UNVERIFIED

DEFAULT_MATRIX: Dict[str, Any] = {
    "entries": [],
    "default_level": DEFAULT_LEVEL,
    "guarded_actions": [],
    "notes": ("Формат записи: {\"pattern\": \"23\", \"level\": "
              "\"certified|targeted|experimental|unsupported\", \"notes\": \"...\"}. "
              "Пустая матрица означает «не проверено», а не «поддерживается»."),
}

LIMITS: List[str] = [
    "AutoCAD на машине разработки отсутствует: COM-слой не исполнялся, "
    "приёмка адаптера остаётся за оператором.",
    "Номер релиза берётся из опубликованной таблицы ProgID; релиза вне "
    "таблицы адаптер не угадывает, а отказывает.",
    "Матрица подтверждённых версий пуста по умолчанию: уровень unverified "
    "означает «не проверено», а не «разрешено».",
    "Отдельного ProgID для AutoCAD Electrical не существует: это вертикаль "
    "поверх того же AutoCAD, подключение то же.",
]


def parse_release(text: Any) -> Optional[str]:
    """Вытащить номер релиза из строки версии AutoCAD.

    Свойство ``Version`` отдаёт строки вроде ``23.0s (LMS Tech)``, а окно
    «О программе» — ``P.162.0.0 AutoCAD 2019.1.2``. Поэтому ищем сначала год
    выпуска (он однозначнее), а потом — номер релиза.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    year = re.search(r"\b(20[0-2]\d)\b", raw)
    if year and year.group(1) in YEAR_TO_RELEASE:
        return YEAR_TO_RELEASE[year.group(1)]
    match = re.search(r"\b(1[6-9]|2[0-9])(?:\.(\d+))?\b", raw)
    if not match:
        return None
    release = match.group(1)
    if match.group(2) is not None:
        candidate = "%s.%s" % (release, match.group(2))
        if candidate in RELEASE_TO_PROGID:
            return candidate
    return release if release in RELEASE_TO_PROGID else None


def progid_for_release(release: Any) -> Tuple[Optional[str], Optional[str]]:
    """ProgID по номеру релиза. Возвращает ``(None, причина)``, если релиза нет."""
    key = str(release or "").strip()
    if not key:
        return None, "no_release"
    if key not in RELEASE_TO_PROGID:
        return None, "unknown_release"
    return RELEASE_TO_PROGID[key], None


def level_for(release: Any, matrix: Any = None) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Уровень поддержки релиза по матрице оператора."""
    data = matrix if isinstance(matrix, dict) else DEFAULT_MATRIX
    entries = data.get("entries") or []
    key = str(release or "").strip()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "").strip()
        if not pattern:
            continue
        if pattern == key or re.fullmatch(pattern.replace("*", ".*"), key):
            level = str(entry.get("level") or DEFAULT_LEVEL).strip()
            if level not in SUPPORT_LEVELS:
                level = DEFAULT_LEVEL
            return level, entry
    default = str(data.get("default_level") or DEFAULT_LEVEL).strip()
    return (default if default in SUPPORT_LEVELS else DEFAULT_LEVEL), None


def is_allowed(release: Any, matrix: Any = None) -> bool:
    return level_for(release, matrix)[0] in ALLOWED_LEVELS


def describe(release: Any, matrix: Any = None) -> Dict[str, Any]:
    """Полное описание версии: релиз, ProgID, уровень, можно ли работать."""
    key = str(release or "").strip() or None
    progid, reason = progid_for_release(key)
    level, entry = level_for(key, matrix)
    return {
        "release": key,
        "progid": progid,
        "level": level,
        "allowed": level in ALLOWED_LEVELS,
        "target_release": TARGET_RELEASE,
        "target_label": TARGET_LABEL,
        "is_target": key == TARGET_RELEASE,
        "refusal": reason,
        "matrix_entry": entry,
    }
