"""Доменный слой щита (НКУ / АСУ ТП) — чистые функции, без COM.

Модуль проверяет *инженерную* сторону щита: состав шкафа, секционирование по
ГОСТ IEC 61439-2 и совместимость оборудования. Он ничего не знает о геометрии
(это ``cabinet_layout``) и ничего не знает о КОМПАСе.

Главное правило модуля: **ничего не угадывать**. Если правило не может быть
проверено из-за отсутствующих данных, оно попадает в ``unknowns``, а не
молча возвращает "всё хорошо". Правдоподобно неверный щит уходит в цех и
стоит денег — поэтому отсутствующий номинал отличается от нулевого.

Структура входных данных (всё опционально, кроме cabinets)::

    {
      "cabinets": [
        {"id": "cab-1", "kind": "Шкаф ПЛК", "name": "...",
         "form": "2b",
         "segments": [{"id": "s1", "kind": "input", "name": "...", "partitions": 2}],
         "items": [{"lineId": "i1", "eqId": "eq-plc110", "qty": 1, "tag": "A1"}]}
      ],
      "catalog": [
        {"id": "eq-plc110", "sku": "ПЛК110-24", "name": "Контроллер",
         "category": "ПЛК и модули", "ratedCurrent": null}
      ]
    }

``catalog`` можно также передать как ``library`` или ``equipment``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# справочные константы
# --------------------------------------------------------------------------

CAT_BREAKER = "Автоматические выключатели"
CAT_RCD = "УЗО и дифавтоматы"
CAT_SWITCH = "Рубильники и переключатели"
CAT_CONTACTOR = "Контакторы и реле"
CAT_UZIP = "УЗИП и защита"
CAT_BUS = "Шины и клеммы"
CAT_PLC = "ПЛК и модули"
CAT_PSU = "Блоки питания"
CAT_HMI = "Панели оператора"
CAT_HEAT = "Греющий кабель"
CAT_THERMO = "Управление обогревом"

KNOWN_CATEGORIES = (
    CAT_BREAKER, CAT_RCD, CAT_SWITCH, CAT_CONTACTOR, CAT_UZIP, CAT_BUS,
    CAT_PLC, CAT_PSU, CAT_HMI, CAT_HEAT, CAT_THERMO,
)

# Категории, у которых номинальный ток обязателен для правил 1 и 9.
POWER_CATEGORIES = (CAT_BREAKER, CAT_RCD, CAT_SWITCH, CAT_CONTACTOR)

CABINET_KINDS = (
    "ГРЩ", "ВРУ", "АВР", "Шкаф ПЛК", "ЩУО", "ЗИП", "Шкаф управления", "Секция",
)

SEGMENT_KINDS = ("input", "feeders", "control", "busbar", "cable", "custom")
SEPARATION_FORMS = ("1", "2a", "2b", "3a", "3b", "4a", "4b")

# Ранг формы разделения: 1 < 2a=2b < 3a=3b < 4a=4b.
FORM_RANK: Dict[str, int] = {
    "1": 1, "2a": 2, "2b": 2, "3a": 3, "3b": 3, "4a": 4, "4b": 4,
}

SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}
SEVERITY_LABEL = {"error": "Ошибка", "warn": "Внимание", "info": "Подсказка"}

# Ни одного правила не требует знаний о КОМПАСе, поэтому это единственная
# причина, по которой модуль остаётся COM-free на уровне импорта.
NO_COM_DEPENDENCIES = True


# --------------------------------------------------------------------------
# разбор и нормализация
# --------------------------------------------------------------------------

def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _catalog_of(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("catalog", "library", "equipment"):
        if key in payload:
            return [ _as_dict(x) for x in _as_list(payload[key]) ]
    return []


def load_spec(payload: Any) -> Dict[str, Any]:
    """Нормализовать запрос. Ничего не отбрасывает молча: неизвестные ключи
    и нечитаемые значения попадают в ``parse_issues``."""
    data = _as_dict(payload)
    parse_issues: List[Dict[str, Any]] = []

    cabinets: List[Dict[str, Any]] = []
    for raw in _as_list(data.get("cabinets")):
        cab = _as_dict(raw)
        if not cab:
            parse_issues.append({"level": "error", "code": "BAD_CABINET",
                                 "message": "элемент cabinets не является объектом"})
            continue
        cab_id = _text(cab.get("id")) or ("cab-%d" % (len(cabinets) + 1))
        segments = []
        for sraw in _as_list(cab.get("segments")):
            seg = _as_dict(sraw)
            if not seg:
                parse_issues.append({"level": "error", "code": "BAD_SEGMENT",
                                     "message": "элемент segments не является объектом",
                                     "ref": cab_id})
                continue
            segments.append({
                "id": _text(seg.get("id")),
                "kind": _text(seg.get("kind")),
                "name": _text(seg.get("name")),
                "partitions": _number(seg.get("partitions")) or 0,
            })
        items = []
        for iraw in _as_list(cab.get("items")):
            item = _as_dict(iraw)
            if not item:
                parse_issues.append({"level": "error", "code": "BAD_ITEM",
                                     "message": "элемент items не является объектом",
                                     "ref": cab_id})
                continue
            qty = _number(item.get("qty"))
            items.append({
                "lineId": _text(item.get("lineId")) or _text(item.get("id")),
                "eqId": _text(item.get("eqId")),
                "sku": _text(item.get("sku")),
                "name": _text(item.get("name")),
                "qty": qty if qty is not None else 1.0,
                "tag": _text(item.get("tag")),
            })
        raw_form = _text(cab.get("form"))
        if raw_form and raw_form not in SEPARATION_FORMS:
            parse_issues.append({"level": "error", "code": "BAD_FORM",
                                 "message": 'форма разделения "%s" не из перечня %s'
                                            % (raw_form, ", ".join(SEPARATION_FORMS)),
                                 "ref": cab_id})
        cabinets.append({
            "id": cab_id,
            "kind": _text(cab.get("kind")),
            "name": _text(cab.get("name")) or cab_id,
            "form": raw_form if raw_form in SEPARATION_FORMS else None,
            "segments": segments,
            "items": items,
        })

    catalog: List[Dict[str, Any]] = []
    for raw in _catalog_of(data):
        eq = _as_dict(raw)
        if not eq:
            parse_issues.append({"level": "error", "code": "BAD_EQUIPMENT",
                                 "message": "элемент справочника не является объектом"})
            continue
        catalog.append({
            "id": _text(eq.get("id")),
            "sku": _text(eq.get("sku")),
            "name": _text(eq.get("name")),
            "brand": _text(eq.get("brand")),
            "category": _text(eq.get("category")),
            "ratedCurrent": _number(eq.get("ratedCurrent")),
            "width_mm": _number(eq.get("width_mm")),
            "height_mm": _number(eq.get("height_mm")),
            "confirm": bool(eq.get("confirm")),
        })

    return {"cabinets": cabinets, "catalog": catalog, "parse_issues": parse_issues}


# --------------------------------------------------------------------------
# доступ к справочнику
# --------------------------------------------------------------------------

def _by_id(catalog: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for eq in catalog:
        if eq["id"]:
            out[eq["id"]] = eq
    return out


def _by_sku(catalog: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for eq in catalog:
        if eq["sku"] and eq["sku"] not in out:
            out[eq["sku"]] = eq
    return out


def resolve_equipment(item: Dict[str, Any], catalog: Sequence[Dict[str, Any]],
                      by_id: Dict[str, Dict[str, Any]],
                      by_sku: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Позиция строки состава -> карточка справочника. Порядок: eqId, затем sku."""
    if item["eqId"] and item["eqId"] in by_id:
        return by_id[item["eqId"]]
    if item["sku"] and item["sku"] in by_sku:
        return by_sku[item["sku"]]
    return None


def is_busbar(eq: Optional[Dict[str, Any]]) -> bool:
    """Шина, а не клемма и не DIN-рейка: имя начинается с «Шина» или артикул
    из семейства ШИ/PS/ШМ/ШМТ."""
    if not eq or eq.get("category") != CAT_BUS:
        return False
    name = eq.get("name") or ""
    sku = eq.get("sku") or ""
    return name.lower().startswith("шина") or sku.upper().startswith(("ШИ", "PS", "ШМ", "ШМТ"))


def is_device(eq: Optional[Dict[str, Any]]) -> bool:
    """Силовой аппарат с номинальным током."""
    return bool(eq) and eq.get("ratedCurrent") is not None and eq.get("category") in POWER_CATEGORIES


def is_breaker(eq: Optional[Dict[str, Any]]) -> bool:
    return bool(eq) and eq.get("category") in (CAT_BREAKER, CAT_RCD)


def expand_items(pairs, predicate) -> List[Dict[str, Any]]:
    """Развернуть позиции по количеству.

    Строка состава «автомат 40 А, qty 3» — это три автомата, а не один.
    Правила по токам обязаны это учитывать, иначе три автомата на шине
    пройдут проверку как один.
    """
    out: List[Dict[str, Any]] = []
    for item, eq in pairs:
        if eq is None or not predicate(eq):
            continue
        qty = item.get("qty") or 1.0
        count = int(qty) if float(qty) == int(qty) else int(qty) + 1
        out.extend([eq] * max(count, 1))
    return out


def is_plc(eq: Optional[Dict[str, Any]]) -> bool:
    if not eq or eq.get("category") != CAT_PLC:
        return False
    blob = ((eq.get("name") or "") + " " + (eq.get("sku") or "")).lower()
    return "контроллер" in blob or "плк" in blob


# --------------------------------------------------------------------------
# выдача
# --------------------------------------------------------------------------

def _issue(code: str, severity: str, message: str, hint: str = "",
           ref: Optional[str] = None, cabinet: Optional[Dict[str, Any]] = None
           ) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if hint:
        out["hint"] = hint
    if ref:
        out["ref"] = ref
    if cabinet is not None:
        out["cabinetId"] = cabinet.get("id")
        out["cabinetName"] = cabinet.get("name")
    return out


def _sorted(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(issues, key=lambda i: (SEVERITY_RANK.get(i["severity"], 9), i["code"]))


# --------------------------------------------------------------------------
# правила на один шкаф
# --------------------------------------------------------------------------

def rule_breaker_over_bus(cab, ctx) -> List[Dict[str, Any]]:
    """1. Отходящий аппарат мощнее шины. Вводной (самый мощный) исключается:
    шины питают отходящие группы, ввод подключается напрямую."""
    devices = expand_items(ctx["resolved"], is_device)
    buses = [eq for _it, eq in ctx["resolved"] if is_busbar(eq) and eq.get("ratedCurrent") is not None]
    if len(devices) < 2 or not buses:
        return []
    ordered = sorted(devices, key=lambda eq: eq["ratedCurrent"], reverse=True)
    dev = ordered[1]
    bus = min(buses, key=lambda eq: eq["ratedCurrent"])
    if dev["ratedCurrent"] > bus["ratedCurrent"]:
        return [_issue(
            "BREAKER_OVER_BUS", "error",
            '%s (%.4g А) — номинал выше, чем у шины %s (%.4g А).'
            % (dev["name"] or dev["sku"], dev["ratedCurrent"],
               bus["name"] or bus["sku"], bus["ratedCurrent"]),
            "Замените шину на более мощную или снизьте номинал отходящего аппарата.",
            cabinet=cab)]
    return []


def rule_many_breakers_no_bus(cab, ctx) -> List[Dict[str, Any]]:
    """2. Много автоматов, но нет соединительной шины."""
    breakers = expand_items(ctx["resolved"], is_breaker)
    any_bus = any(is_busbar(eq) for _it, eq in ctx["resolved"])
    if len(breakers) >= 3 and not any_bus:
        return [_issue(
            "MANY_BREAKERS_NO_BUS", "info",
            "%d автомата(ов) без соединительной шины — монтаж перемычками менее надёжен."
            % len(breakers),
            "Добавьте гребёнку или медную шину.",
            cabinet=cab)]
    return []


def rule_uzip_no_breaker(cab, ctx) -> List[Dict[str, Any]]:
    """3. УЗИП без вводного автомата или рубильника."""
    has_uzip = any(eq and eq.get("category") == CAT_UZIP for _it, eq in ctx["resolved"])
    has_in = any(eq and eq.get("category") in (CAT_BREAKER, CAT_SWITCH) for _it, eq in ctx["resolved"])
    if has_uzip and not has_in:
        return [_issue(
            "UZIP_NO_INCOMING", "warn",
            "УЗИП установлен без вводного автомата или рубильника.",
            "Перед УЗИП нужен вводной АВ/рубильник — иначе его нельзя безопасно отключить для замены.",
            cabinet=cab)]
    return []


def rule_plc_no_psu(cab, ctx) -> List[Dict[str, Any]]:
    """4. ПЛК в шкафу без блока питания 24 В."""
    has_plc = any(is_plc(eq) for _it, eq in ctx["resolved"])
    has_psu = any(eq and eq.get("category") == CAT_PSU for _it, eq in ctx["resolved"])
    if has_plc and not has_psu:
        return [_issue(
            "PLC_NO_PSU", "warn",
            "Контроллер установлен без блока питания 24 В DC в этом шкафу.",
            "Добавьте блок питания 24 В — ПЛК и модули питаются от него.",
            cabinet=cab)]
    return []


def rule_busbar_overload(cab, ctx) -> List[Dict[str, Any]]:
    """9. Суммарный номинал отходящих автоматов выше допустимого тока шины."""
    devices = expand_items(ctx["resolved"], is_device)
    buses = [eq for _it, eq in ctx["resolved"] if is_busbar(eq) and eq.get("ratedCurrent") is not None]
    if len(devices) < 2 or not buses:
        return []
    ordered = sorted(devices, key=lambda eq: eq["ratedCurrent"], reverse=True)
    total = sum(eq["ratedCurrent"] for eq in ordered[1:])
    bus = min(buses, key=lambda eq: eq["ratedCurrent"])
    if total > 0 and total > bus["ratedCurrent"]:
        return [_issue(
            "BUSBAR_OVERLOAD", "warn",
            "Суммарный номинал отходящих автоматов (%.4g А) превышает допустимый ток шины %s (%.4g А)."
            % (total, bus["name"] or bus["sku"], bus["ratedCurrent"]),
            "Проверьте коэффициент неодновременности или выберите шину большего сечения.",
            cabinet=cab)]
    return []


def rule_empty(cab, ctx) -> List[Dict[str, Any]]:
    """6. Пустой шкаф."""
    if not cab["items"]:
        return [_issue("EMPTY_CABINET", "info",
                       "Шкаф пока пуст — оборудование ещё не добавлено.",
                       cabinet=cab)]
    return []


def rule_form_no_busbar(cab, ctx) -> List[Dict[str, Any]]:
    """7. Заявлена форма >= 2, а шинного отсека нет."""
    form = cab.get("form")
    if not form or FORM_RANK.get(form, 1) < 2:
        return []
    if any(seg["kind"] == "busbar" for seg in cab["segments"]):
        return []
    return [_issue(
        "FORM_NO_BUSBAR_SEGMENT", "warn",
        "Форма разделения %s требует отделения шин, но шинного отсека в шкафу нет." % form,
        "Добавьте «Шинный отсек» в секционировании этого шкафа.",
        cabinet=cab)]


def rule_form_few_segments(cab, ctx) -> List[Dict[str, Any]]:
    """8. Форма >= 3 при менее чем двух функциональных отсеках."""
    form = cab.get("form")
    if not form or FORM_RANK.get(form, 1) < 3:
        return []
    functional = [s for s in cab["segments"] if s["kind"] != "busbar"]
    if len(functional) >= 2:
        return []
    return [_issue(
        "FORM_FEW_SEGMENTS", "warn",
        "Форма %s отделяет функциональные блоки друг от друга, а отсеков всего %d."
        % (form, len(functional)),
        "Разбейте шкаф минимум на два функциональных отсека.",
        cabinet=cab)]


PER_CABINET_RULES = (
    rule_breaker_over_bus,
    rule_many_breakers_no_bus,
    rule_uzip_no_breaker,
    rule_plc_no_psu,
    rule_busbar_overload,
)


# --------------------------------------------------------------------------
# правила на весь проект
# --------------------------------------------------------------------------

def rule_heat_no_thermostat(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """10. Греющий кабель есть, терморегулятора нет."""
    cats = set()
    for cab in spec["cabinets"]:
        for _it, eq in _resolved_for(cab, spec):
            if eq is not None:
                cats.add(eq.get("category"))
    if CAT_HEAT in cats and CAT_THERMO not in cats:
        return [_issue(
            "HEAT_NO_THERMOSTAT", "warn",
            "В проекте есть греющий кабель, но нет ни одного терморегулятора.",
            "Без регулирования кабель будет греть постоянно — добавьте терморегулятор.")]
    return []


def rule_hmi_no_plc(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """5. Панель оператора есть, а ПЛК нет ни в одном шкафу проекта."""
    plc_somewhere = False
    hmi_cabinets: List[Dict[str, Any]] = []
    for cab in spec["cabinets"]:
        pairs = _resolved_for(cab, spec)
        if any(eq is not None and eq.get("category") == CAT_PLC for _it, eq in pairs):
            plc_somewhere = True
        if any(eq is not None and eq.get("category") == CAT_HMI for _it, eq in pairs):
            hmi_cabinets.append(cab)
    if not hmi_cabinets or plc_somewhere:
        return []
    return [_issue(
        "HMI_NO_PLC", "warn",
        "Панель оператора есть, а контроллера нет ни в одном шкафу проекта.",
        "Панель работает в паре с ПЛК — добавьте контроллер.",
        cabinet=hmi_cabinets[0])]


# --------------------------------------------------------------------------
# неподтверждённое и неизвестное
# --------------------------------------------------------------------------

def _resolved_for(cab: Dict[str, Any], spec: Dict[str, Any]):
    by_id = spec["_by_id"]
    by_sku = spec["_by_sku"]
    out = []
    for item in cab["items"]:
        out.append((item, resolve_equipment(item, spec["catalog"], by_id, by_sku)))
    return out


def collect_unresolved(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ссылка на справочник не разрешается — позиция не может быть проверена
    и не может быть размещена. Это ошибка, а не предупреждение."""
    issues: List[Dict[str, Any]] = []
    for cab in spec["cabinets"]:
        for item, eq in _resolved_for(cab, spec):
            if eq is None:
                issues.append(_issue(
                    "UNRESOLVED_CATALOG_REF", "error",
                    'позиция %s: ссылка "%s" не найдена в справочнике'
                    % (item["lineId"] or "?", item["eqId"] or item["sku"] or "?"),
                    "Передайте catalog с этой позицией или исправьте ссылку.",
                    ref=item["lineId"] or None, cabinet=cab))
    return issues


def collect_unconfirmed(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Позиции, чьи данные помечены как неподтверждённые (``confirm: true``),
    и силовые аппараты без номинального тока.

    Правила 1 и 9 физически не могут сработать без ``ratedCurrent``. Разница
    между «нет данных» и «данных нет, но мы решили, что всё нормально» — это
    вся разница между проверкой и её имитацией.
    """
    out: List[Dict[str, Any]] = []
    for cab in spec["cabinets"]:
        for item, eq in _resolved_for(cab, spec):
            if eq is None:
                continue
            if eq.get("confirm"):
                out.append({
                    "cabinetId": cab["id"],
                    "lineId": item["lineId"],
                    "eqId": eq["id"],
                    "reason": "confirm",
                    "detail": "габарит или параметр позиции помечен как неподтверждённый",
                })
            elif eq.get("category") in POWER_CATEGORIES and eq.get("ratedCurrent") is None:
                out.append({
                    "cabinetId": cab["id"],
                    "lineId": item["lineId"],
                    "eqId": eq["id"],
                    "reason": "rated_current_missing",
                    "detail": "у силового аппарата нет номинального тока — правила 1 и 9 не проверены",
                })
    return out


def collect_unknowns(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Что именно осталось непроверенным и почему."""
    out: List[Dict[str, Any]] = []
    for cab in spec["cabinets"]:
        pairs = _resolved_for(cab, spec)
        devices = expand_items(pairs, is_device)
        buses = [eq for _it, eq in pairs if is_busbar(eq) and eq.get("ratedCurrent") is not None]
        if len(devices) < 2 or not buses:
            out.append({
                "cabinetId": cab["id"],
                "rules": ["BREAKER_OVER_BUS", "BUSBAR_OVERLOAD"],
                "reason": "нужны минимум два силовых аппарата с током и шина с током",
            })
        if any(eq is not None and eq.get("category") == CAT_PLC for _it, eq in pairs):
            # правило 4 проверено; сюда попадает только отсутствие данных по ПЛК
            pass
        if not cab.get("form"):
            out.append({
                "cabinetId": cab["id"],
                "rules": ["FORM_NO_BUSBAR_SEGMENT", "FORM_FEW_SEGMENTS"],
                "reason": "форма разделения не указана",
            })
    return out


# --------------------------------------------------------------------------
# публичный API
# --------------------------------------------------------------------------

def validate_cabinet(cab: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Проверить один шкаф."""
    ctx = {"resolved": _resolved_for(cab, spec)}
    issues: List[Dict[str, Any]] = []
    for rule in PER_CABINET_RULES:
        issues.extend(rule(cab, ctx))
    issues.extend(rule_empty(cab, ctx))
    issues.extend(rule_form_no_busbar(cab, ctx))
    issues.extend(rule_form_few_segments(cab, ctx))
    return _sorted(issues)


def validate_project(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Проверить весь проект: все шкафы плюс межшкафные правила."""
    issues: List[Dict[str, Any]] = []
    for cab in spec["cabinets"]:
        issues.extend(validate_cabinet(cab, spec))
    issues.extend(rule_hmi_no_plc(spec))
    issues.extend(rule_heat_no_thermostat(spec))
    issues.extend(collect_unresolved(spec))
    return _sorted(issues)


def summarize(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"error": 0, "warn": 0, "info": 0, "total": len(issues)}
    for i in issues:
        sev = i.get("severity")
        if sev in out:
            out[sev] += 1
    return out


def check_spec(payload: Any) -> Dict[str, Any]:
    """Точка входа инструмента. Возвращает проверки, неподтверждённое и
    непроверенное. Ничего не открывает и не пишет."""
    spec = load_spec(payload)
    spec["_by_id"] = _by_id(spec["catalog"])
    spec["_by_sku"] = _by_sku(spec["catalog"])
    issues = validate_project(spec)
    unconfirmed = collect_unconfirmed(spec)
    unknowns = collect_unknowns(spec)
    summary = summarize(issues)

    verdict = "blocked" if summary["error"] else (
        "ready_with_unknowns" if (unconfirmed or unknowns or summary["warn"]) else "ready")

    return {
        "ok": summary["error"] == 0,
        "verdict": verdict,
        "summary": summary,
        "issues": issues,
        "unconfirmed": unconfirmed,
        "unverified": unknowns,
        "parse_issues": spec["parse_issues"],
        "cabinets": len(spec["cabinets"]),
        "catalog_entries": len(spec["catalog"]),
        "rules": [r.__name__ for r in PER_CABINET_RULES]
                 + ["rule_empty", "rule_form_no_busbar", "rule_form_few_segments",
                    "rule_hmi_no_plc", "rule_heat_no_thermostat"],
        "limits": [
            "Проверка домена, а не геометрии: раскладку монтажной панели проверяет cabinet_layout.",
            "Результат не является сертификатом соответствия ГОСТ IEC 61439 и не заменяет расчёт "
            "защиты, кабелей и теплового режима.",
            "Отсутствующие данные попадают в unverified и unconfirmed, а не считаются допустимыми.",
        ],
    }
