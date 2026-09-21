"""Матрица подтверждённых версий КОМПАСа.

Зачем этот модуль существует
---------------------------
Мост вызывает десятки COM-интерфейсов API5/API7, и часть из них появилась
не в первой версии КОМПАСа. При этом версия системы нигде не проверялась:
на неподтверждённой сборке КОМПАСа инструмент мог либо тихо сделать не то,
либо уронить документ, а агент не имел способа отличить «работает» от
«повезло».

Здесь версия приложения сравнивается с матрицей, которую заполняет оператор,
и для отмеченных действий включается запрет на неподтверждённой версии.

Что важно в устройстве
----------------------
* По умолчанию матрица **пуста**, а уровень по умолчанию - ``unverified``.
  Это значит «не проверено», а не «запрещено»: поведение существующей
  установки не меняется, пока оператор не заполнит матрицу.
* Список ``guarded_actions`` по умолчанию пуст, поэтому накладных расходов
  на обычных вызовах нет: версия запрашивается у КОМПАСа только тогда,
  когда действие действительно под защитой.
* Мы не выдумываем номера версий. Матрица - это утверждение оператора
  «на этой версии проверено», а не догадка автора кода.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Уровни поддержки. Порядок - от наиболее подтверждённого к наименее.
CERTIFIED = "certified"
TARGETED = "targeted"
EXPERIMENTAL = "experimental"
UNSUPPORTED = "unsupported"
UNVERIFIED = "unverified"

SUPPORT_LEVELS: Tuple[str, ...] = (CERTIFIED, TARGETED, EXPERIMENTAL, UNSUPPORTED, UNVERIFIED)

# Уровни, на которых действие под защитой разрешено.
ALLOWED_LEVELS: Tuple[str, ...] = (CERTIFIED, TARGETED)

DEFAULT_LEVEL = UNVERIFIED

DEFAULT_MATRIX: Dict[str, Any] = {
    "entries": [],
    "default_level": DEFAULT_LEVEL,
    "guarded_actions": [],
    "notes": "",
}


def parse_version(text: Any) -> Optional[Tuple[int, ...]]:
    """Вытащить версию из строки, которую вернул КОМПАС.

    Строка приходит в разных видах: ``25.0.0.1234``, ``KOMPAS-3D V25.0``,
    ``V25``. Первое правдоподобное число в диапазоне 10..99 считается
    мажорной версией: в названии продукта есть посторонние цифры (``3D``),
    поэтому брать просто первое число нельзя.
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    explicit = re.search(r"(?:[Vv]|версия|version)\s*(\d{1,2}(?:\.\d{1,5})*)", raw)
    if explicit:
        return tuple(int(part) for part in explicit.group(1).split("."))

    # Версия без префикса: берём первую группу вида 25.0.0.1234, но только
    # если её мажорный компонент правдоподобен - иначе это часть названия
    # продукта вроде "3D".
    dotted = re.search(r"(\d{1,2}(?:\.\d{1,5})+)", raw)
    if dotted:
        parts = dotted.group(1).split(".")
        if 10 <= int(parts[0]) <= 99:
            return tuple(int(part) for part in parts)

    numbers = [int(token) for token in re.findall(r"\d+", raw)]
    plausible = [number for number in numbers if 10 <= number <= 99]
    if plausible:
        return (plausible[0],)
    return None


def _match_pattern(version: Sequence[int], pattern: str) -> bool:
    """Сравнить версию с маской вида ``25.*`` или ``24.1``."""
    parts = [part.strip() for part in str(pattern or "").split(".") if part.strip()]
    if not parts:
        return False
    for index, part in enumerate(parts):
        if part == "*":
            continue
        if index >= len(version):
            # В версии нет этого компонента - совпадение не подтверждено.
            return False
        if not part.isdigit():
            return False
        if int(part) != int(version[index]):
            return False
    return True


def load_matrix(raw: Any) -> Dict[str, Any]:
    """Провалидировать секцию матрицы из конфигурации."""
    matrix = dict(DEFAULT_MATRIX)
    if not isinstance(raw, Mapping):
        return matrix

    entries: List[Dict[str, Any]] = []
    for item in raw.get("entries") or []:
        if not isinstance(item, Mapping):
            continue
        pattern = str(item.get("pattern") or "").strip()
        if not pattern:
            continue
        level = str(item.get("level") or DEFAULT_LEVEL).strip().lower()
        if level not in SUPPORT_LEVELS:
            level = UNVERIFIED
        entries.append({
            "pattern": pattern,
            "level": level,
            "notes": str(item.get("notes") or ""),
        })
    matrix["entries"] = entries

    default_level = str(raw.get("default_level") or DEFAULT_LEVEL).strip().lower()
    matrix["default_level"] = default_level if default_level in SUPPORT_LEVELS else DEFAULT_LEVEL

    guarded: List[str] = []
    for item in raw.get("guarded_actions") or []:
        action = str(item or "").strip()
        if action and action not in guarded:
            guarded.append(action)
    matrix["guarded_actions"] = guarded

    matrix["notes"] = str(raw.get("notes") or "")
    return matrix


def classify_version(version_text: Any, matrix: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Сопоставить версию КОМПАСа с матрицей."""
    resolved = load_matrix(matrix or {})
    parsed = parse_version(version_text)
    warnings: List[str] = []

    level = str(resolved.get("default_level") or DEFAULT_LEVEL)
    matched: Optional[Dict[str, Any]] = None

    if parsed is None:
        warnings.append(
            "version_not_parsed: версия КОМПАСа не распознана в строке "
            f"{str(version_text or '<empty>')!r}; принят уровень по умолчанию"
        )
    else:
        for entry in resolved["entries"]:
            if _match_pattern(parsed, entry["pattern"]):
                matched = entry
                level = entry["level"]
                break

    return {
        "version_raw": str(version_text or ""),
        "version_parsed": list(parsed) if parsed else None,
        "level": level,
        "matched_pattern": matched["pattern"] if matched else None,
        "matched_notes": matched["notes"] if matched else "",
        "verified": level in ALLOWED_LEVELS,
        "guarded_actions": list(resolved["guarded_actions"]),
        "entry_count": len(resolved["entries"]),
        "warnings": warnings,
        "limits": [
            "Матрица отражает только то, что в неё внёс оператор. "
            "Пустая матрица означает «не проверено», а не «работает».",
            "Уровень certified не делает операцию безопасной: он лишь "
            "говорит, что её проверяли на этой версии.",
        ],
    }


def guard_action(
    action: str,
    version_text: Any,
    matrix: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Проверить, разрешено ли действие на этой версии КОМПАСа."""
    resolved = load_matrix(matrix or {})
    if action not in resolved["guarded_actions"]:
        return {"guarded": False, "allowed": True, "action": action}

    verdict = classify_version(version_text, resolved)
    if verdict["verified"]:
        return {"guarded": True, "allowed": True, "action": action, "verdict": verdict}

    raise RuntimeError(
        "kompas_version_not_verified: "
        f"action={action}; version={verdict['version_raw'] or '<unknown>'}; "
        f"level={verdict['level']}; "
        "действие перечислено в kompas.version_matrix.guarded_actions, "
        "а версия не входит в подтверждённые уровни certified/targeted. "
        "Проверьте работу на этой версии и внесите её в матрицу, "
        "либо уберите действие из guarded_actions."
    )


def guard_for_session(root: Any, session: Any, action: str) -> Dict[str, Any]:
    """Проверка перед вызовом действия. Версия читается только при защите.

    Запрос версии - это COM-обращение, поэтому он происходит лишь тогда,
    когда действие действительно перечислено в ``guarded_actions``.
    """
    from settings import settings_for

    matrix = settings_for(root).version_matrix
    if action not in load_matrix(matrix)["guarded_actions"]:
        return {"guarded": False, "allowed": True, "action": action}

    version = getattr(session, "kompas_version", None)
    if version is None:
        try:
            version = str(session.status().get("version") or "")
        except Exception as exc:
            raise RuntimeError(
                f"kompas_version_unavailable_for_guarded_action: {action}: {exc}"
            ) from None
        session.kompas_version = version

    return guard_action(action, version, matrix)
