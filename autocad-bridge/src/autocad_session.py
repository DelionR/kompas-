"""Подключение к AutoCAD через COM — тонкий слой исполнения.

Дисциплина та же, что в мосте КОМПАС: адаптер **не запускает** CAD, а
подключается к уже открытому экземпляру. Причины ровно те же:

* запуск второго экземпляра подменяет «тот чертёж, с которым работает человек»
  на «тот, который открыл агент»;
* у AutoCAD профиль и набор загруженных вертикалей определяются сеансом, и
  новый экземпляр может оказаться без Electrical;
* модальные диалоги при старте блокируют автоматику.

Поэтому подключение только через ``GetActiveObject``, а ``Dispatch`` здесь
запрещён: если экземпляра нет — отказ, а не тихий запуск.

Что здесь не определяется
-------------------------
**Является ли запущенный AutoCAD именно Electrical — программно не
проверяется.** Отдельного ProgID у Electrical нет: это вертикаль поверх того
же AutoCAD. Определить её по COM надёжно мы не можем, поэтому профиль
приносит оператор и он помечается ``unverified``. Выдать «это Electrical» без
проверки — значит подменить факт догадкой.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import autocad_version

# Сколько раз пробуем подцепиться и с какой паузой: AutoCAD может быть занят
# перерисовкой или диалогом, и первая попытка часто неудачна.
ATTACH_ATTEMPTS = 8
ATTACH_PAUSE_S = 0.25

LIMITS: List[str] = [
    "Адаптер не запускает AutoCAD: только GetActiveObject к уже открытому "
    "экземпляру, иначе агент подменит чертёж пользователя.",
    "Является ли AutoCAD именно Electrical, по COM надёжно не проверить: "
    "отдельного ProgID у вертикали нет. Профиль приносит оператор и он "
    "помечен unverified.",
    "Приёмка на живом AutoCAD не проводилась: AutoCAD на машине разработки "
    "отсутствует.",
]


class AttachError(RuntimeError):
    """Не удалось подключиться к запущенному AutoCAD."""


def _get_active_object(progid: str, attempts: int = ATTACH_ATTEMPTS):
    import pythoncom
    import win32com.client as wc

    try:
        pythoncom.CoInitialize()
    except Exception:
        pass

    last_error: Optional[BaseException] = None
    for _ in range(max(1, attempts)):
        try:
            return wc.GetActiveObject(progid)
        except BaseException as exc:  # COM отдаёт разные типы ошибок
            last_error = exc
            time.sleep(ATTACH_PAUSE_S)
    raise AttachError("autocad_active_instance_not_found: %s" % last_error)


def attach(release: Any = None, attempts: int = ATTACH_ATTEMPTS) -> Tuple[Any, Dict[str, Any]]:
    """Подключиться к запущенному AutoCAD нужного релиза."""
    info = autocad_version.describe(release or autocad_version.TARGET_RELEASE)
    progid = info.get("progid")
    if not progid:
        raise AttachError("autocad_progid_unresolved: %s" % info.get("refusal"))
    app = _get_active_object(progid, attempts)
    info["attached"] = True
    return app, info


def read_version(app: Any) -> Optional[str]:
    """Строка версии из свойства ``Version`` приложения AutoCAD."""
    try:
        return str(app.Version)
    except Exception:
        return None


def document_info(app: Any) -> Dict[str, Any]:
    """Сведения об активном документе без всяких мутаций."""
    info: Dict[str, Any] = {"has_document": False}
    try:
        doc = app.ActiveDocument
    except Exception:
        info["reason"] = "no_active_document"
        return info
    info["has_document"] = True
    try:
        info["name"] = str(doc.Name)
    except Exception:
        info["name"] = None
    try:
        info["path"] = str(doc.FullName)
    except Exception:
        info["path"] = None
    insunits: Optional[int] = None
    try:
        insunits = doc.GetVariable("INSUNITS")
    except Exception:
        pass
    info["insunits"] = insunits
    return info


def status(session_app: Any = None, release: Any = None,
           profile: Any = None, matrix: Any = None,
           attempts: int = ATTACH_ATTEMPTS) -> Dict[str, Any]:
    """Состояние подключения: релиз, ProgID, уровень, документ, единицы."""
    import autocad_units

    payload: Dict[str, Any] = {
        "ok": False,
        "attached": False,
        "version": None,
        "document": None,
        "units": None,
        "refusals": [],
        "limits": list(LIMITS),
    }

    app = session_app
    if app is None:
        try:
            app, info = attach(release, attempts)
        except AttachError as exc:
            payload["refusals"].append({
                "code": "autocad_active_instance_not_found",
                "message": str(exc),
                "hint": "Откройте один экземпляр AutoCAD с нужным чертежом и "
                        "закройте модальные диалоги.",
            })
            payload["version"] = autocad_version.describe(
                release or autocad_version.TARGET_RELEASE, matrix)
            return payload
    else:
        info = autocad_version.describe(release or autocad_version.TARGET_RELEASE,
                                        matrix)

    payload["attached"] = True
    payload["ok"] = True

    detected = read_version(app)
    detected_release = autocad_version.parse_release(detected)
    payload["version"] = info
    payload["version"]["reported"] = detected
    payload["version"]["detected_release"] = detected_release
    if detected_release and detected_release != info.get("release"):
        payload["refusals"].append({
            "code": "release_mismatch",
            "message": "Запрошен релиз %s, а запущен %s."
                       % (info.get("release"), detected_release),
            "requested": info.get("release"),
            "detected": detected_release,
        })
    if not info.get("allowed"):
        payload["refusals"].append({
            "code": "version_not_confirmed",
            "message": "Релиз %s имеет уровень %s: матрица подтверждённых "
                       "версий не заполнена оператором."
                       % (info.get("release"), info.get("level")),
            "level": info.get("level"),
        })

    doc = document_info(app)
    payload["document"] = doc
    units, reason = autocad_units.describe_units(doc.get("insunits"))
    payload["units"] = units
    if units is None:
        payload["refusals"].append({
            "code": reason or "no_insunits",
            "message": "Масштаб чертежа не определён: запись в миллиметрах "
                       "невозможна без единиц документа.",
        })

    requested_profile = str(profile or "").strip().lower()
    payload["profile"] = {
        "requested": requested_profile or None,
        "is_electrical": requested_profile == "electrical",
        "verified": False,
        "note": "профиль приносит оператор: по COM надёжно не проверяется",
    }
    return payload
