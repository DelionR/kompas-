"""Обработчик реестра нормативных таблиц.

Реестр живёт в ``normative_tables`` — чистый модуль без зависимости от
протокола. Здесь только обёртка: маршрут и ответ.
"""

from __future__ import annotations

from typing import Any, Dict

from protocol import ok
from normative_tables import handle


def normative_tables_handler(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Нормативные таблицы как данные: инвентарь, строки, проверка по схеме."""
    return ok("normative.tables", handle(params))
