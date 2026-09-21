"""Сторож модальных диалогов КОМПАСа.

Зачем этот модуль существует
---------------------------
Застрявший модальный диалог останавливает весь мост: COM-вызовы к KOMPAS
начинают висеть, воркер уходит в таймаут, а агент не понимает, почему
последняя операция перестала отвечать. Причина почти всегда одна - окно,
которое ждёт нажатия кнопки человеком.

Здесь живёт только решение «что это за окно и можно ли его трогать».
Сам Win32-слой (перечисление и закрытие) остаётся в ``winui.py``, поэтому
классификатор проверяется тестами без КОМПАСа и без окон вообще.

Границы честности
-----------------
Автоматическое закрытие диалогов опасно: у окна «Сохранить изменения?»
крестик означает «не сохранять», и это потеря данных. Поэтому:

* политика ``close`` выдаётся только информационным окнам с одной кнопкой;
* всё, что связано с выбором, сохранением или файлами, получает ``report`` -
  его закрывает человек, а не мост;
* по умолчанию инструмент ничего не закрывает: ``apply`` нужно запросить явно;
* за один вызов закрывается не больше ``MAX_CLOSE_PER_CALL`` окон.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Стандартный класс Win32-диалога. Окна этого класса - кандидаты на сторож.
DIALOG_CLASS = "#32770"

CLOSE = "close"
REPORT = "report"
IGNORE = "ignore"

MAX_CLOSE_PER_CALL = 3

# Идентификатор, шаблон по нормализованному заголовку, политика, причина.
# Порядок важен: первое совпадение выигрывает, поэтому опасные шаблоны
# (сохранение, вопрос) стоят выше безобидных.
RULES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "save_changes",
        r"(сохранить изменения|сохранить файл|save changes|save file|unsaved)",
        REPORT,
        "закрытие крестиком означает отказ от сохранения - решает человек",
    ),
    (
        "question",
        r"(вопрос|подтвердите|подтверждение|question|confirm|are you sure)",
        REPORT,
        "диалог выбора: у кнопок разный смысл, автозакрытие недопустимо",
    ),
    (
        "file_dialog",
        r"(сохранить как|открыть файл|выбор файла|save as|open file|select file)",
        REPORT,
        "диалог работы с файлами требует решения человека",
    ),
    (
        "error_ack",
        r"^(ошибка|error|критическая ошибка|fatal)",
        REPORT,
        "ошибку должен прочитать человек, а не гасить мост",
    ),
    (
        "warning_ack",
        r"^(предупреждение|внимание|warning|caution)",
        REPORT,
        "предупреждение может требовать прочтения перед продолжением",
    ),
    (
        "info_message",
        r"^(сообщение|информация|сведения|information|message|note)$",
        CLOSE,
        "информационное окно с единственной кнопкой подтверждения",
    ),
    (
        "progress",
        r"(ожидание|выполняется|пожалуйста подождите|please wait|processing)",
        IGNORE,
        "окно прогресса закроется само; вмешательство только навредит",
    ),
    (
        "options",
        r"(параметры|настройки|конфигурация|options|preferences|settings)",
        REPORT,
        "окно настроек открыто человеком намеренно",
    ),
)

_STRIP_PREFIXES = (
    "компас-3d",
    "компас 3d",
    "kompas-3d",
    "kompas 3d",
    "kompas",
    "компас",
)

_WHITESPACE = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s.:!…]+$")


def normalize_title(title: Any) -> str:
    """Привести заголовок окна к виду, по которому работают правила."""
    text = str(title or "").strip()
    text = _WHITESPACE.sub(" ", text)
    lowered = text.lower()
    for prefix in _STRIP_PREFIXES:
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix):].strip(" -—–:")
            break
    lowered = _TRAILING.sub("", lowered)
    return lowered.strip()


def classify(
    title: Any,
    class_name: str = "",
    visible: bool = True,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Определить, что это за окно и что с ним можно делать."""
    normalized = normalize_title(title)
    matched: Optional[Tuple[str, str, str]] = None
    for dialog_id, pattern, policy, reason in RULES:
        if re.search(pattern, normalized):
            matched = (dialog_id, policy, reason)
            break

    if matched is None:
        dialog_id, policy, reason = (
            "unknown",
            REPORT,
            "правило не найдено: неизвестное окно не закрывается автоматически",
        )
    else:
        dialog_id, policy, reason = matched

    is_dialog = str(class_name or "") == DIALOG_CLASS
    if not is_dialog:
        policy = IGNORE
        reason = f"класс окна {class_name or '<unknown>'} не является диалогом Win32"

    if not visible:
        policy = IGNORE
        reason = "окно не отображается"

    return {
        "title": str(title or ""),
        "normalized_title": normalized,
        "class_name": str(class_name or ""),
        "dialog_id": dialog_id,
        "policy": policy,
        "reason": reason,
        "visible": bool(visible),
        "enabled": enabled,
        "is_dialog_class": is_dialog,
        "closable": policy == CLOSE,
        "blocking": policy == REPORT and bool(visible) and enabled is not False,
    }


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка по просканированным окнам."""
    counts = {CLOSE: 0, REPORT: 0, IGNORE: 0}
    blocking: List[Dict[str, Any]] = []
    closable: List[Dict[str, Any]] = []
    for row in rows:
        policy = str(row.get("policy") or IGNORE)
        counts[policy] = counts.get(policy, 0) + 1
        if row.get("blocking"):
            blocking.append(row)
        if row.get("closable"):
            closable.append(row)
    return {
        "window_count": len(rows),
        "counts": counts,
        "blocking_count": len(blocking),
        "closable_count": len(closable),
        "blocking": blocking,
        "closable": closable,
    }


def dismiss_plan(rows: Sequence[Dict[str, Any]], apply: bool = False) -> Dict[str, Any]:
    """Решить, какие окна закрывать, и объяснить отказ по каждому остальному."""
    closable = [row for row in rows if row.get("closable")]
    skipped = [
        {"hwnd": row.get("hwnd"), "title": row.get("title"), "policy": row.get("policy"), "reason": row.get("reason")}
        for row in rows
        if not row.get("closable")
    ]
    selected = closable[:MAX_CLOSE_PER_CALL]
    deferred = closable[MAX_CLOSE_PER_CALL:]

    return {
        "apply": bool(apply),
        "selected": selected,
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "deferred": [
            {"hwnd": row.get("hwnd"), "title": row.get("title")} for row in deferred
        ],
        "skipped_count": len(skipped),
        "skipped": skipped[:20],
        "limit": MAX_CLOSE_PER_CALL,
    }


def describe_policy() -> Dict[str, Any]:
    """Описание политики - для ответа инструмента и документации."""
    return {
        "dialog_class": DIALOG_CLASS,
        "max_close_per_call": MAX_CLOSE_PER_CALL,
        "default_action": "scan_only",
        "rules": [
            {"dialog_id": dialog_id, "pattern": pattern, "policy": policy, "reason": reason}
            for dialog_id, pattern, policy, reason in RULES
        ],
        "limits": [
            "Закрываются только окна политики close: информационные сообщения "
            "с единственной кнопкой подтверждения.",
            "Диалоги выбора, сохранения и работы с файлами не закрываются никогда - "
            "их решение остаётся за человеком.",
            "Список шаблонов неполон по определению: незнакомое окно получает "
            "политику report, а не закрывается наугад.",
        ],
    }
