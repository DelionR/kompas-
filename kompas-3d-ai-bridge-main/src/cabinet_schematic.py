"""Схема щита как модель данных — чистые функции, без COM.

До этой волны схема не существовала вовсе: мост знал состав, раскладку и
перечень, но не знал **электрических связей**. Раскладка знает координаты, но
не связи — поэтому схему нельзя вывести из раскладки, её нужно задать.

Модель минимальна и состоит из четырёх сущностей:

- **устройство** (``devices``) — изделие с позиционным обозначением;
- **зажим** (вывод устройства) — точка, к которой цепляется цепь;
- **цепь** (``nets``) — электрическое соединение двух и более зажимов;
- **соединение** (``connections``) — связь зажима с цепью.

Что берётся из нормы, а что нет
-------------------------------

**Графы таблицы соединений — цитата из ГОСТ 2.702-2011**: «Конт.» (номер
контакта), «Адрес» (обозначение цепи и/или позиционное обозначение), «Цепь»
(характеристика цепи), «Адрес внешний». Номера контактов записываются в порядке
возрастания — это тоже из стандарта.

**Системы нумерации цепей в ГОСТ 2.702-2011 нет.** Проверено по тексту
стандарта: слово «маркировка» не встречается вовсе, а «обозначение цепи»
требуется, но не нормируется. Поэтому обозначение цепи приносит вызывающая
сторона; если мы присваиваем его сами по шаблону, значение помечается
``derived``, а не выдаётся за норму.

**Распиновку производитель не публикует.** Открытый каталог IEK даёт число
полюсов (ETIM ``EF001391``) и тип подключения, но не число зажимов и не то,
какой зажим вход, а какой выход. Число зажимов считается как ``2 × poles`` и
помечается ``derived``. Для изделия без числа полюсов зажимов не будет вовсе —
отказ вместо правдоподобной догадки.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

KIND_BUILD = "build"
KIND_TERMINALS = "terminals"
KIND_NETS = "nets"
KIND_TABLE = "table"
KIND_CHECK = "check"
KIND_TYPICAL = "typical"
KINDS = (KIND_BUILD, KIND_TERMINALS, KIND_NETS, KIND_TABLE, KIND_CHECK, KIND_TYPICAL)

# Сколько зажимов на один полюс. Для модульных аппаратов вход и выход на каждый
# полюс — это вывод из практики, а не паспортное значение.
TERMINALS_PER_POLE = 2
# Клемма: два зажима — вход и выход перемычки.
TERMINALS_OF_TERMINAL_BLOCK = 2
# Блок питания: вход сети и выход.
TERMINALS_OF_PSU = 4

CONNECTOR_KINDS = ("terminal",)
PROTECTIVE_KINDS = ("breaker", "rcd", "fuse")

LIMITS = [
    "Схему нельзя вывести из раскладки: раскладка знает координаты, но не "
    "электрические связи. Соединения приносит вызывающая сторона.",
    "Графы таблицы соединений (Конт., Адрес, Цепь, Адрес внешний) — по "
    "ГОСТ 2.702-2011. Системы нумерации цепей в стандарте нет: обозначение "
    "цепи задаёт вызывающая сторона.",
    "Число зажимов считается как 2 × poles и помечается derived: "
    "производитель не публикует ни числа зажимов, ни распиновки.",
    "Изделие без числа полюсов не получает зажимов: отказ, а не догадка.",
    "Отрисовка схемы и запись её в документ КОМПАС здесь не производятся: "
    "модуль отдаёт данные.",
]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    parsed = _num(value)
    return int(parsed) if parsed is not None else default


def _result(kind: str, data: Dict[str, Any],
            refusals: Optional[List[Dict[str, Any]]] = None,
            notes: Optional[List[str]] = None,
            provenance: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "kind": kind,
        "ok": not refusals,
        "verdict": "refused" if refusals else "computed",
        "result": data,
        "refusals": list(refusals or []),
        "notes": list(notes or []),
        "provenance": list(provenance or []),
        "limits": list(LIMITS),
    }


# --------------------------------------------------------------------------
# зажимы
# --------------------------------------------------------------------------

def terminals_of(device: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Зажимы устройства.

    Возвращает ``(None, причина)``, если число зажимов вывести не из чего.
    """
    data = _as_dict(device)
    kind = _text(data.get("kind")).lower()
    poles = _int(data.get("poles"))
    explicit = _int(data.get("terminals"))

    if explicit is not None and explicit > 0:
        count, derived = explicit, False
    elif kind in CONNECTOR_KINDS:
        count, derived = TERMINALS_OF_TERMINAL_BLOCK, True
    elif kind == "psu":
        count, derived = TERMINALS_OF_PSU, True
    elif poles is not None and poles > 0:
        count, derived = poles * TERMINALS_PER_POLE, True
    else:
        return None, "no_terminals"

    ref = _text(data.get("ref")) or _text(data.get("designation"))
    rows = [{"ref": ref, "number": index + 1,
             "derived": derived,
             "kind": kind or None}
            for index in range(count)]
    return rows, None


def build_terminals(payload: Any) -> Dict[str, Any]:
    """Зажимы всех устройств схемы."""
    data = _as_dict(payload)
    notes: List[str] = []
    rows: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    for device in _as_list(data.get("devices")):
        record = _as_dict(device)
        found, reason = terminals_of(record)
        if found is None:
            unverified.append({"ref": _text(record.get("ref")) or None,
                               "kind": _text(record.get("kind")) or None,
                               "reason": reason})
            continue
        rows.extend(found)
    if not rows and not unverified:
        return _result(KIND_TERMINALS, {}, [
            {"code": "no_devices",
             "message": "Устройства не заданы: поле devices пусто."}])
    notes.append("зажимов: %d, без данных о выводах: %d"
                 % (len(rows), len(unverified)))
    return _result(KIND_TERMINALS, {
        "terminals": rows,
        "terminal_count": len(rows),
        "unverified": unverified,
    }, notes=notes)


# --------------------------------------------------------------------------
# цепи и соединения
# --------------------------------------------------------------------------

def build_nets(payload: Any) -> Dict[str, Any]:
    """Цепи с подключёнными зажимами."""
    data = _as_dict(payload)
    nets: Dict[str, Dict[str, Any]] = {}
    for net in _as_list(data.get("nets")):
        record = _as_dict(net)
        key = _text(record.get("id")) or _text(record.get("designation"))
        if not key:
            continue
        nets[key] = {"id": key,
                     "designation": _text(record.get("designation")) or key,
                     "kind": _text(record.get("kind")) or None,
                     "characteristic": _text(record.get("characteristic")) or None,
                     "external_address": _text(record.get("external_address")) or None,
                     "terminals": []}

    dangling: List[Dict[str, Any]] = []
    for connection in _as_list(data.get("connections")):
        record = _as_dict(connection)
        key = _text(record.get("net")) or _text(record.get("net_id"))
        if key not in nets:
            dangling.append({"net": key or None,
                             "device": _text(record.get("device")) or None,
                             "reason": "unknown_net"})
            continue
        nets[key]["terminals"].append({
            "device": _text(record.get("device")) or None,
            "terminal": _int(record.get("terminal")),
        })

    rows = sorted(nets.values(), key=lambda item: item["id"])
    for row in rows:
        row["terminal_count"] = len(row["terminals"])
        row["closed"] = len(row["terminals"]) >= 2
    return _result(KIND_NETS, {
        "nets": rows,
        "net_count": len(rows),
        "dangling": dangling,
    })


# --------------------------------------------------------------------------
# таблица соединений по ГОСТ 2.702-2011
# --------------------------------------------------------------------------

def build_table(payload: Any) -> Dict[str, Any]:
    """Таблица соединений соединителя: графы по ГОСТ 2.702-2011.

    Графы стандарта: «Конт.» — номер контакта, «Адрес» — обозначение цепи
    и/или позиционное обозначение, «Цепь» — характеристика цепи, «Адрес
    внешний». Номера контактов идут по возрастанию.
    """
    data = _as_dict(payload)
    nets = build_nets(data)["result"]
    net_by_terminal: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for net in nets.get("nets", []):
        for point in net["terminals"]:
            key = (point["device"], point["terminal"])
            net_by_terminal[key] = net

    rows: List[Dict[str, Any]] = []
    for device in _as_list(data.get("devices")):
        record = _as_dict(device)
        if _text(record.get("kind")).lower() not in CONNECTOR_KINDS:
            continue
        ref = _text(record.get("ref")) or _text(record.get("designation"))
        found, reason = terminals_of(record)
        if found is None:
            continue
        for terminal in found:
            net = net_by_terminal.get((ref, terminal["number"]))
            rows.append({
                "connector": ref,
                "contact": terminal["number"],
                "address": (net or {}).get("designation")
                           or _address_of_terminal(net_by_terminal, (ref, terminal["number"])),
                "circuit": (net or {}).get("characteristic") or None,
                "external_address": (net or {}).get("external_address") or None,
            })

    rows.sort(key=lambda row: (row["connector"], row["contact"]))
    columns = ["Конт.", "Адрес", "Цепь", "Адрес внешний"]
    return _result(KIND_TABLE, {
        "columns": columns,
        "columns_source": "ГОСТ 2.702-2011, графы таблицы соединений соединителя",
        "rows": rows,
        "row_count": len(rows),
    }, provenance=[{"table": "connection_table",
                    "source": "ГОСТ 2.702-2011 ЕСКД. Правила выполнения "
                              "электрических схем"}])


def _address_of_terminal(net_by_terminal, key) -> Optional[str]:
    """Адрес как позиционные обозначения, если обозначения цепи нет."""
    net = net_by_terminal.get(key)
    if not net:
        return None
    peers = [p for p in net["terminals"] if (p["device"], p["terminal"]) != key]
    if not peers:
        return None
    return ", ".join(p["device"] or "?" for p in peers)


# --------------------------------------------------------------------------
# проверки
# --------------------------------------------------------------------------

def check(payload: Any) -> Dict[str, Any]:
    """Проверки связности и полноты схемы."""
    data = _as_dict(payload)
    devices = [d for d in _as_list(data.get("devices")) if isinstance(d, dict)]
    nets_result = build_nets(data)["result"]
    nets = nets_result.get("nets", [])
    issues: List[Dict[str, Any]] = []

    def issue(code: str, severity: str, message: str, **extra):
        item = {"code": code, "severity": severity, "message": message}
        item.update(extra)
        issues.append(item)

    # 1. повтор позиционных обозначений
    seen: Dict[str, int] = {}
    for device in devices:
        ref = _text(device.get("ref")) or _text(device.get("designation"))
        if not ref:
            issue("no_designation", "error",
                  "Устройство без позиционного обозначения.",
                  kind=_text(device.get("kind")) or None)
            continue
        seen[ref] = seen.get(ref, 0) + 1
    for ref, count in sorted(seen.items()):
        if count > 1:
            issue("duplicate_designation", "error",
                  "Позиционное обозначение %s повторяется %d раза."
                  % (ref, count), ref=ref)

    # 2. ссылка на несуществующее устройство
    known = set(seen)
    for net in nets:
        for point in net["terminals"]:
            if point["device"] not in known:
                issue("unknown_device", "error",
                      "Цепь %s ссылается на неизвестное устройство %s."
                      % (net["id"], point["device"] or "—"),
                      net=net["id"])

    # 3. цепь не замкнута. Цепь с внешним адресом замкнута на внешний мир,
    # поэтому один зажим в ней не ошибка.
    for net in nets:
        if net["terminal_count"] < 2 and not net.get("external_address"):
            issue("open_net", "error",
                  "Цепь %s подключена к %d зажиму: цепь должна соединять "
                  "минимум два." % (net["id"], net["terminal_count"]),
                  net=net["id"])

    # 3а. соединение ссылается на цепь, которой нет в описании
    for lost in nets_result.get("dangling", []):
        issue("unknown_net", "error",
              "Соединение ссылается на цепь %s, которой нет в описании."
              % (lost.get("net") or "—"),
              net=lost.get("net"))

    # 4. устройство ни к чему не подключено
    connected = {p["device"] for net in nets for p in net["terminals"]}
    for device in devices:
        ref = _text(device.get("ref")) or _text(device.get("designation"))
        if ref and ref not in connected:
            issue("unconnected_device", "warn",
                  "Устройство %s не подключено ни к одной цепи." % ref, ref=ref)

    # 5. силовая цепь без защиты
    protected: Dict[str, List[str]] = {}
    for net in nets:
        for point in net["terminals"]:
            device = next((d for d in devices
                           if (_text(d.get("ref")) or _text(d.get("designation")))
                           == point["device"]), None)
            if device and _text(device.get("kind")).lower() in PROTECTIVE_KINDS:
                protected.setdefault(net["id"], []).append(point["device"])
    for net in nets:
        if _text(net.get("kind")).lower() in ("phase", "power", "") and \
                net["id"] not in protected:
            issue("unprotected_net", "warn",
                  "Цепь %s не содержит защитного аппарата." % net["id"],
                  net=net["id"])

    severity_rank = {"error": 0, "warn": 1}
    issues.sort(key=lambda item: (severity_rank.get(item["severity"], 9),
                                  item["code"]))
    errors = [i for i in issues if i["severity"] == "error"]
    return _result(KIND_CHECK, {
        "issues": issues,
        "issue_count": len(issues),
        "error_count": len(errors),
        "devices": len(devices),
        "nets": len(nets),
    })


# --------------------------------------------------------------------------
# типовая цепь
# --------------------------------------------------------------------------

def build_typical(payload: Any) -> Dict[str, Any]:
    """Типовая цепь: ввод → защитный аппарат → группа.

    Шаблон составлен, а не взят из стандарта: в ГОСТ 2.702-2011 системы
    нумерации цепей нет, поэтому обозначения цепей здесь присваиваются по
    шаблону и помечаются ``derived``.
    """
    data = _as_dict(payload)
    incoming = _text(data.get("incoming")) or "QF1"
    group = _as_list(data.get("group"))
    phases = _text(data.get("phases")) or "L"
    notes: List[str] = []
    notes.append("обозначения цепей присвоены по шаблону и помечены derived: "
                 "системы нумерации в ГОСТ 2.702-2011 нет")

    devices: List[Dict[str, Any]] = []
    nets: List[Dict[str, Any]] = []
    connections: List[Dict[str, Any]] = []

    devices.append({"ref": incoming, "kind": "breaker", "poles": 1})
    # вводная цепь замкнута на внешний источник: один зажим в ней не ошибка
    nets.append({"id": "%s_in" % phases, "designation": "%s (ввод)" % phases,
                 "kind": "phase", "derived": True,
                 "external_address": _text(data.get("supply")) or "ввод"})
    nets.append({"id": "%s_after_%s" % (phases, incoming),
                 "designation": "%s после %s" % (phases, incoming),
                 "kind": "phase", "derived": True})
    connections.append({"net": "%s_in" % phases, "device": incoming, "terminal": 1})
    connections.append({"net": "%s_after_%s" % (phases, incoming),
                        "device": incoming, "terminal": 2})

    for index, member in enumerate(group):
        record = _as_dict(member)
        ref = _text(record.get("ref")) or ("QF%d" % (index + 2))
        devices.append({"ref": ref, "kind": _text(record.get("kind")) or "breaker",
                        "poles": _int(record.get("poles"), 1) or 1})
        connections.append({"net": "%s_after_%s" % (phases, incoming),
                            "device": ref, "terminal": 1})
        outgoing = _text(record.get("outgoing")) or ("%s_%s" % (phases, ref))
        nets.append({"id": outgoing, "designation": outgoing, "kind": "phase",
                     "derived": True,
                     "external_address": _text(record.get("load")) or "нагрузка"})
        connections.append({"net": outgoing, "device": ref, "terminal": 2})

    # шаблон проверяем тем же порядком, что и схему инженера: шаблон
    # составляет обозначения цепей сам, и на нём ошибки виднее всего
    model = {"devices": devices, "nets": nets, "connections": connections}
    verdict = check(model)["result"]
    return _result(KIND_TYPICAL, {
        "devices": devices,
        "nets": nets,
        "connections": connections,
        "check": verdict,
    }, notes=notes)


def build_schematic(payload: Any) -> Dict[str, Any]:
    """Собрать схему целиком: зажимы, цепи, таблица, проверки."""
    data = _as_dict(payload)
    terminals = build_terminals(data)
    nets = build_nets(data)
    table = build_table(data)
    checks = check(data)
    return _result(KIND_BUILD, {
        "terminals": terminals["result"],
        "nets": nets["result"],
        "table": table["result"],
        "check": checks["result"],
    }, notes=(terminals["notes"] + nets["notes"] + table["notes"] + checks["notes"]),
        provenance=table["provenance"])


_DISPATCH = {
    KIND_BUILD: build_schematic,
    KIND_TERMINALS: build_terminals,
    KIND_NETS: build_nets,
    KIND_TABLE: build_table,
    KIND_CHECK: check,
    KIND_TYPICAL: build_typical,
}


def build(payload: Any) -> Dict[str, Any]:
    """Единая точка входа: ``{"kind": "build|terminals|nets|table|check|typical"}``."""
    data = _as_dict(payload)
    kind = _text(data.get("kind")).lower() or KIND_BUILD
    if kind not in _DISPATCH:
        return {
            "kind": kind or None,
            "ok": False,
            "verdict": "refused",
            "result": {},
            "refusals": [{"code": "unknown_kind",
                          "message": "Неизвестный вид: %s." % (kind or "не задан"),
                          "known_kinds": list(KINDS)}],
            "notes": [],
            "provenance": [],
            "limits": list(LIMITS),
        }
    return _DISPATCH[kind](data)
