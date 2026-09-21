"""Устаревшая обёртка над :mod:`settings`.

Исторически здесь жила ``load_config()``, которая читала
``config/agent_config.json`` и возвращала сырой словарь. Функция не вызывалась
нигде в проекте, а политика безопасности была захардкожена в ``safety.py`` -
то есть конфиг существовал, но ни на что не влиял.

Настройки теперь читаются, валидируются и кэшируются в :mod:`settings`.
Модуль оставлен как совместимый фасад для внешнего кода и тестов, которые
могли импортировать ``load_config``: он возвращает тот же сырой словарь,
что и раньше, но с раскрытым плейсхолдером ``{bridge_root}``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from settings import read_settings


def load_config(root: Path) -> Dict[str, Any]:
    """Сырое содержимое конфига. Для новой логики используйте settings.settings_for()."""
    import json

    path = Path(root) / "config" / "agent_config.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolved_settings(root: Path):
    """Провалидированные настройки - то, что реально применяет мост."""
    return read_settings(root)
