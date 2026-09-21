"""Политика записи: что можно писать, что нельзя трогать вообще.

Что изменилось
--------------
Раньше этот модуль жёстко прошивал абсолютные пути
(``C:\\KOMPAS_AI_WORKSPACE\\00_SOURCE`` и т. д.) и полностью игнорировал
``config/agent_config.json``, хотя те же самые ключи там объявлены. Теперь
источник политики - :mod:`settings`, а захардкоженные значения остались только
как безопасные значения по умолчанию для случая, когда конфига нет.

Публичный интерфейс сохранён: ``safe_write(path, root)`` и
``copy_destination(source, root, name)`` вызываются из ``capabilities.py``
и ``engineering_capabilities.py`` без изменений.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from settings import Settings, settings_for

# Совместимость: ``jobs.py`` исторически импортирует ``PROTECTED``.
# Значение сохраняется, но больше не является источником истины.
PROTECTED = tuple(settings_for(Path(__file__).resolve().parents[1]).protected_roots)


def _norm(path: Any) -> Path:
    return Path(str(path)).expanduser().resolve()


def _settings(root: Any, settings: Optional[Settings] = None) -> Settings:
    return settings if settings is not None else settings_for(root)


def assert_not_protected(path: Any, root: Any = None, settings: Optional[Settings] = None) -> Path:
    """Запретить любую запись в защищённые корни исходников."""
    target = _norm(path)
    policy = _settings(root, settings)
    if policy.is_protected(target):
        raise PermissionError(f"protected_path: {target}")
    return target


def assert_writable(path: Any, root: Any, settings: Optional[Settings] = None) -> Path:
    """Разрешить запись только внутрь work или разрешённых корней вывода."""
    target = _norm(path)
    policy = _settings(root, settings)
    if policy.is_protected(target):
        raise PermissionError(f"protected_path: {target}")
    if not policy.is_writable(target):
        raise PermissionError(f"write_outside_allowed_roots: {target}")
    return target


# Историческое имя: оставлено, потому что используется в существующем коде.
def safe_write(path: Any, root: Any, settings: Optional[Settings] = None) -> Path:
    return assert_writable(path, root, settings)


def work_dir(root: Any, settings: Optional[Settings] = None) -> Path:
    policy = _settings(root, settings)
    path = policy.work_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_destination(source: Any, root: Any, name: Optional[str] = None,
                     settings: Optional[Settings] = None) -> Path:
    """Куда положить рабочую копию исходного документа."""
    target = work_dir(root, settings) / (name or Path(str(source)).name)
    return assert_writable(target, root, settings)


def policy_summary(root: Any, settings: Optional[Settings] = None) -> dict:
    """Описание политики для kompas_status - без перечисления всех путей."""
    return _settings(root, settings).describe()
