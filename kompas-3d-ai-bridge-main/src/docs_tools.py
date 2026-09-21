"""Обработчик поиска по документам проекта.

Сам поиск живёт в ``docs_search`` — чистый модуль без зависимости от протокола.
Здесь только обёртка: маршрут и ответ.
"""

from __future__ import annotations

from typing import Any, Dict

from protocol import ok
from docs_search import search


def docs_search_handler(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Найти фрагменты в документах проекта: файл, строка, цитата."""
    return ok("docs.search", search(params))
