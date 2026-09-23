"""Действия адаптера AutoCAD — адаптеры доменного слоя к реестру.

Аргумент ``session`` принимается ради единообразия и может быть уже
подключённым COM-объектом: это позволяет тестам подсунуть мок, а в бою —
переиспользовать подключение.

Мутирующих действий здесь нет: адаптер на этом этапе отдаёт данные и план,
но ничего не пишет в чертёж. Запись появится отдельно, после приёмки.
"""

from __future__ import annotations

from typing import Any, Dict

from protocol import ok
import autocad_version
import autocad_units
import autocad_layout
import autocad_session


def autocad_status(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Состояние подключения, версии и единиц чертежа."""
    return ok("autocad.status", autocad_session.status(
        session_app=session,
        release=params.get("release"),
        profile=params.get("profile"),
        matrix=params.get("version_matrix")))


def autocad_version_check(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Версия AutoCAD: релиз, ProgID, уровень по матрице оператора."""
    release = params.get("release") or autocad_version.TARGET_RELEASE
    return ok("autocad.version_check", {
        "version": autocad_version.describe(release, params.get("version_matrix")),
        "known_releases": sorted(autocad_version.RELEASE_TO_PROGID),
        "target": {"release": autocad_version.TARGET_RELEASE,
                   "label": autocad_version.TARGET_LABEL},
        "limits": list(autocad_version.LIMITS),
    })


def autocad_document_active(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Сведения об активном документе, без мутаций."""
    app = session
    if app is None:
        try:
            app, _info = autocad_session.attach(params.get("release"))
        except autocad_session.AttachError as exc:
            return {"ok": False, "action": "autocad.document_active",
                    "error": {"code": "autocad_active_instance_not_found",
                              "message": str(exc), "details": {}}}
    return ok("autocad.document_active", autocad_session.document_info(app))


def autocad_units_read(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Единицы чертежа по INSUNITS и коэффициент перевода миллиметров."""
    code = params.get("insunits")
    if code is None:
        app = session
        if app is None:
            try:
                app, _info = autocad_session.attach(params.get("release"))
            except autocad_session.AttachError as exc:
                return {"ok": False, "action": "autocad.units_read",
                        "error": {"code": "autocad_active_instance_not_found",
                                  "message": str(exc), "details": {}}}
        code = autocad_session.document_info(app).get("insunits")
    units, reason = autocad_units.describe_units(code)
    return ok("autocad.units_read", {
        "insunits": code,
        "units": units,
        "refusal": reason,
        "limits": list(autocad_units.LIMITS),
    })


def autocad_layout_place(session: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Перевести раскладку щита в точки вставки блоков AutoCAD.

    Чистая функция: COM не нужен, чертёж не открывается.
    """
    data = dict(params or {})
    units = data.get("units")
    if isinstance(units, dict) and units.get("units_per_mm") is None:
        described, reason = autocad_units.describe_units(units.get("insunits"))
        data["units"] = described
        if described is None:
            data["units_refusal"] = reason
    return ok("autocad.layout_place", autocad_layout.place(data))
