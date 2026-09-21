"""Единая конфигурация моста KOMPAS-3D.

Зачем этот модуль существует
---------------------------
В проекте был ``config/agent_config.example.json`` с ключами ``protected_roots``,
``write_roots``, ``viewport.crop`` и ``view.projection_indices`` - и функция
``load_config()`` в ``src/config.py``. Но ``load_config()`` не вызывалась нигде,
а ``src/safety.py`` жёстко прошивал те же пути абсолютными строками. В результате
конфиг был декорацией: перенос на другую машину или смена структуры каталогов
требовали правки исходников.

Здесь настройки читаются один раз, валидируются и кэшируются по корню моста.
Любая некорректная секция не роняет мост, а откатывается к безопасному значению
по умолчанию и попадает в список ``warnings`` - его видно через ``kompas_status``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from version_matrix import load_matrix

CONFIG_RELPATH = Path("config") / "agent_config.json"

# Значения по умолчанию повторяют то, что раньше было захардкожено в safety.py,
# поэтому поведение на существующих установках не меняется.
DEFAULT_PROTECTED_ROOTS: Tuple[str, ...] = (
    r"C:\KOMPAS_AI_WORKSPACE\00_SOURCE",
    r"C:\KOMPAS_AI_WORKSPACE\01_CURRENT_CDW",
    r"C:\KOMPAS_AI_WORKSPACE\02_3D_V9_STABLE",
)
DEFAULT_OUTPUT_ROOTS: Tuple[str, ...] = (r"C:\KOMPAS_AI_WORKSPACE\90_OUTPUT",)
DEFAULT_WRITE_SUBDIR = "work"
DEFAULT_JOBS_SUBDIR = "jobs"

DEFAULT_CROP: Mapping[str, int] = {"left": 345, "top": 160, "right": 8, "bottom": 10}
DEFAULT_PROJECTIONS: Mapping[str, int] = {
    "front": 1, "rear": 2, "top": 3, "bottom": 4,
    "left": 5, "right": 6, "iso": 7, "dimetric": 8,
}

KOMPAS_PROGID = "KOMPAS.Application.7"
API5_PROGID = "KOMPAS.Application.5"


def _norm(path: Any) -> Path:
    """Нормализовать путь для сравнения (без требования существования)."""
    return Path(str(path)).expanduser().resolve()


def _expand(value: Any, bridge_root: Path) -> str:
    """Подставить {bridge_root} в строку из конфига."""
    text = str(value)
    return text.replace("{bridge_root}", str(bridge_root))


def _as_path_tuple(values: Any, bridge_root: Path) -> Tuple[Path, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return ()
    out: List[Path] = []
    for item in values:
        if not isinstance(item, (str, bytes)) or not str(item).strip():
            continue
        out.append(_norm(_expand(item, bridge_root)))
    return tuple(out)


def _as_int(mapping: Any, keys: Sequence[str], defaults: Mapping[str, int]) -> Dict[str, int]:
    out = dict(defaults)
    if not isinstance(mapping, Mapping):
        return out
    for key in keys:
        try:
            out[key] = int(mapping[key])
        except (KeyError, TypeError, ValueError):
            continue
    return out


@dataclass(frozen=True)
class Settings:
    """Разрешённые настройки моста, готовые к использованию."""

    bridge_root: Path
    config_path: Path
    config_loaded: bool
    protected_roots: Tuple[Path, ...]
    write_roots: Tuple[Path, ...]
    jobs_dir: Path
    kompas_progid: str = KOMPAS_PROGID
    api5_progid: str = API5_PROGID
    worker_poll_ms: int = 120
    mcp_tool_timeout_sec: int = 90
    job_timeout_max_sec: int = 300
    max_rotation_steps_per_call: int = 12
    allow_keyboard_rotation_fallback: bool = True
    viewport_crop: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_CROP))
    projection_indices: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_PROJECTIONS))
    # Порог, после которого ответ инструмента ужимается, а полный дамп
    # уходит в артефакт. Раньше порога не было вообще и в контекст модели
    # мог уехать ответ на несколько сотен килобайт.
    compact_threshold_bytes: int = 60_000
    compact_max_items: int = 200
    # Матрица подтверждённых версий КОМПАСа: какие сборки проверены и какие
    # действия запрещено выполнять на непроверенной версии. Пустая матрица
    # означает «не проверено», а не «работает», и ничего не блокирует.
    version_matrix: Mapping[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()

    # -- удобные производные ------------------------------------------------
    @property
    def work_dir(self) -> Path:
        return self.bridge_root / DEFAULT_WRITE_SUBDIR

    def is_protected(self, path: Any) -> bool:
        target = _norm(path)
        return any(target == root or target.is_relative_to(root) for root in self.protected_roots)

    def is_writable(self, path: Any) -> bool:
        target = _norm(path)
        allowed = (self.work_dir,) + self.write_roots
        return any(target == root or target.is_relative_to(root) for root in allowed)

    def describe(self) -> Dict[str, Any]:
        """Компактное описание для kompas_status: без утечки лишних путей."""
        return {
            "config_loaded": self.config_loaded,
            "protected_root_count": len(self.protected_roots),
            "write_root_count": 1 + len(self.write_roots),
            "jobs_dir": str(self.jobs_dir),
            "mcp_tool_timeout_sec": self.mcp_tool_timeout_sec,
            "job_timeout_max_sec": self.job_timeout_max_sec,
            "compact_threshold_bytes": self.compact_threshold_bytes,
            "version_matrix_entries": len(self.version_matrix.get("entries") or []),
            "guarded_action_count": len(self.version_matrix.get("guarded_actions") or []),
            "warnings": list(self.warnings),
        }


def read_settings(bridge_root: Any) -> Settings:
    """Прочитать и провалидировать настройки. Никогда не бросает исключение."""
    root = _norm(bridge_root)
    config_path = root / CONFIG_RELPATH
    warnings: List[str] = []
    raw: Dict[str, Any] = {}

    if config_path.is_file():
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, Mapping):
                raw = dict(parsed)
            else:
                warnings.append("config_not_an_object: используется конфигурация по умолчанию")
        except (OSError, ValueError) as exc:
            warnings.append(f"config_unreadable: {type(exc).__name__}; используется конфигурация по умолчанию")
    else:
        warnings.append("config_missing: используется конфигурация по умолчанию")

    safety = raw.get("safety") if isinstance(raw.get("safety"), Mapping) else {}
    viewport = raw.get("viewport") if isinstance(raw.get("viewport"), Mapping) else {}
    view = raw.get("view") if isinstance(raw.get("view"), Mapping) else {}
    kompas = raw.get("kompas") if isinstance(raw.get("kompas"), Mapping) else {}
    version_matrix = load_matrix(kompas.get("version_matrix"))

    protected = _as_path_tuple(safety.get("protected_roots"), root) or tuple(
        _norm(_expand(p, root)) for p in DEFAULT_PROTECTED_ROOTS
    )

    declared_write = _as_path_tuple(safety.get("write_roots"), root)
    # Корень work добавляется всегда: без него мост не сможет работать вообще,
    # а объявлять его в конфиге руками - лишний способ выстрелить себе в ногу.
    work = root / DEFAULT_WRITE_SUBDIR
    write_roots: List[Path] = []
    for candidate in (work,) + declared_write:
        if candidate not in write_roots:
            write_roots.append(candidate)
    if not declared_write:
        write_roots.extend(_norm(_expand(p, root)) for p in DEFAULT_OUTPUT_ROOTS)

    # Пересечение защищённых и записываемых корней - это дыра в политике,
    # а не настройка. Защищённый корень всегда побеждает.
    overlap = [str(p) for p in write_roots if any(p == q or p.is_relative_to(q) for q in protected)]
    if overlap:
        warnings.append("write_root_inside_protected_root: " + "; ".join(overlap))
        write_roots = [p for p in write_roots if not any(p == q or p.is_relative_to(q) for q in protected)]

    jobs_subdir = str(safety.get("jobs_subdir") or DEFAULT_JOBS_SUBDIR).strip() or DEFAULT_JOBS_SUBDIR

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int(raw[key])
        except (KeyError, TypeError, ValueError):
            return default
        if not lo <= value <= hi:
            warnings.append(f"{key}_out_of_range: {value} вне [{lo}, {hi}], взято {default}")
            return default
        return value

    crop = _as_int(viewport.get("crop"), ("left", "top", "right", "bottom"), DEFAULT_CROP)
    projections = _as_int(view.get("projection_indices"), tuple(DEFAULT_PROJECTIONS), DEFAULT_PROJECTIONS)

    try:
        rotation_steps = int(view.get("max_rotation_steps_per_call", 12))
    except (TypeError, ValueError):
        rotation_steps = 12
    if not 1 <= rotation_steps <= 60:
        warnings.append(f"max_rotation_steps_per_call_out_of_range: {rotation_steps}, взято 12")
        rotation_steps = 12

    return Settings(
        bridge_root=root,
        config_path=config_path,
        config_loaded=bool(raw),
        protected_roots=protected,
        write_roots=tuple(write_roots),
        jobs_dir=root / jobs_subdir,
        worker_poll_ms=_int("worker_poll_ms", 120, 10, 5_000),
        mcp_tool_timeout_sec=_int("mcp_tool_timeout_sec", 90, 5, 3_600),
        job_timeout_max_sec=_int("job_timeout_max_sec", 300, 1, 3_600),
        max_rotation_steps_per_call=rotation_steps,
        allow_keyboard_rotation_fallback=bool(view.get("allow_keyboard_rotation_fallback", True)),
        viewport_crop=crop,
        projection_indices=projections,
        compact_threshold_bytes=_int("compact_threshold_bytes", 60_000, 1_000, 10_000_000),
        compact_max_items=_int("compact_max_items", 200, 1, 100_000),
        version_matrix=version_matrix,
        warnings=tuple(warnings),
    )


_CACHE: Dict[str, Settings] = {}


def settings_for(bridge_root: Any) -> Settings:
    """Кэшированный доступ к настройкам по корню моста."""
    key = str(_norm(bridge_root))
    cached = _CACHE.get(key)
    if cached is None:
        cached = read_settings(bridge_root)
        _CACHE[key] = cached
    return cached


def reset_cache() -> None:
    """Сбросить кэш (нужно тестам и перезагрузке конфига на ходу)."""
    _CACHE.clear()
