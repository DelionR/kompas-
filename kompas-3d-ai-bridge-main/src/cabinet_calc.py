"""Электрорасчёт щита — чистые функции, без COM и без нормативных таблиц внутри.

Волны 7–10 дали состав, раскладку, материализацию и сверку. Расчёта в них не
было вовсе: ни выбора сечения, ни падения напряжения, ни тока КЗ. Эта волна его
добавляет — но не в виде «модель вспоминает таблицу ПУЭ», а как движок.

Два правила, заданные архитектурой:

1. **Считает движок, а не языковая модель.** Здесь только арифметика и физика:
   ток по мощности, падение напряжения по сопротивлению петли, ток КЗ по
   сопротивлению источника и линии. Ни одной «типичной цифры из памяти».

2. **Нормативные таблицы приходят извне, а не из памяти.** Допустимые токи
   кабелей, каталог аппаратов, коэффициенты спроса и время-токовые
   характеристики — это нормы и каталоги производителей. Таблиц внутри модуля
   нет: они передаются в поле ``tables`` с указанием источника (``source``) либо
   ссылкой ``normative:<имя>`` на реестр ``data/normative``. Источник
   возвращается в ответе, чтобы результат можно было проверить по норме, а не на
   веру.

Из второго правила следует главное поведение: **отказ вместо экстраполяции**.
Если для критерия нет данных, критерий уходит в ``not_checked``, а не
считается выполненным. Если нет таблицы — возвращается ``refusals``, а не
подобранное «на глаз» сечение. Пустой ответ здесь полезнее правдоподобного.

Что считается без таблиц (физика и арифметика):

- ``current`` — ток по мощности, напряжению и cos φ;
- ``voltage_drop`` — падение напряжения по длине, сечению и удельному
  сопротивлению;
- ``fault_current`` — ток КЗ по сопротивлению источника и линии;
- ``derating`` — произведение поправочных коэффициентов.

Что требует таблиц (иначе отказ):

- ``max_demand`` — коэффициенты спроса;
- ``cable_size`` — допустимые токи по сечениям;
- ``breaker_select`` — каталог аппаратов;
- ``selectivity`` — время-токовые характеристики.

Реестр таблиц (волна 13) лежит в ``data/normative``: ссылка
``{"ampacity": "normative:ampacity"}`` берёт таблицу из репозитория вместе с
источником. Таблица, зависящая от условий применения, отбирается полем
``table_filters``: способ монтажа, материал, изоляция, число нагруженных
проводников. Без отбора выбор сечения дал бы минимальное сечение по лучшему из
способов монтажа — правдоподобный и неверный ответ, поэтому отбор, который не
оставил ни одной строки, это отказ, а не «возьмём похожее».
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from normative_tables import (TABLES as NORMATIVE_TABLES,
                              apply_filter as normative_filter,
                              is_reference as normative_reference,
                              resolve_reference as normative_resolve)

SQRT3 = math.sqrt(3.0)

KIND_CURRENT = "current"
KIND_MAX_DEMAND = "max_demand"
KIND_VOLTAGE_DROP = "voltage_drop"
KIND_FAULT_CURRENT = "fault_current"
KIND_DERATING = "derating"
KIND_CABLE_SIZE = "cable_size"
KIND_BREAKER_SELECT = "breaker_select"
KIND_SELECTIVITY = "selectivity"

KINDS = (KIND_CURRENT, KIND_MAX_DEMAND, KIND_VOLTAGE_DROP, KIND_FAULT_CURRENT,
         KIND_DERATING, KIND_CABLE_SIZE, KIND_BREAKER_SELECT, KIND_SELECTIVITY)

# Удельное сопротивление при 20 °C, Ом·мм²/м. Это физическое свойство металла,
# а не нормативная таблица: поправка на рабочую температуру, способ прокладки и
# число кабелей вносится вызывающей стороной через factors.
RESISTIVITY_OHM_MM2_PER_M = {"copper": 0.01754, "aluminium": 0.02899}

LIMITS = [
    "Модуль считает только арифметику. Нормативные таблицы (допустимые токи, "
    "каталог аппаратов, коэффициенты спроса, время-токовые характеристики) "
    "приносит вызывающая сторона в поле tables либо ссылкой normative:<имя> на "
    "реестр data/normative; источник возвращается в provenance.",
    "Таблица, зависящая от условий применения, отбирается полем table_filters "
    "(материал, изоляция, число нагруженных проводников, способ монтажа). "
    "Отбор, не оставивший строк, — отказ: выбор по всем строкам сразу дал бы "
    "минимальное сечение по лучшему из способов монтажа.",
    "Отказ вместо экстраполяции: если данных для критерия нет, критерий уходит "
    "в not_checked, а не считается выполненным.",
    "Удельное сопротивление — физическое свойство металла при 20 °C "
    "(медь 0.01754, алюминий 0.02899 Ом·мм²/м). Поправки на температуру, "
    "способ прокладки и группировку вносит вызывающая сторона.",
    "Однофазный расчёт и трёхфазный считаются по разным формулам; для однофазной "
    "петли «фаза-нуль» ток КЗ без сопротивления нулевой последовательности не "
    "считается — это не консервативная оценка, а отказ.",
    "Расчёт не сверялся с расчётом проектировщика на реальном щите: формулы "
    "стандартные, но приёмка по проекту за инженером.",
]


# --------------------------------------------------------------------------
# разбор
# --------------------------------------------------------------------------

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


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _refusal(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "message": message}
    item.update(extra)
    return item


def _result(kind: str, data: Dict[str, Any], refusals: Optional[List[Dict[str, Any]]] = None,
            notes: Optional[List[str]] = None, provenance: Optional[List[Dict[str, Any]]] = None,
            not_checked: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    gaps = list(not_checked or [])
    verdict = ("refused" if refusals else
               ("computed_with_gaps" if gaps else "computed"))
    return {
        "kind": kind,
        "ok": not refusals,
        "verdict": verdict,
        "result": data,
        "refusals": list(refusals or []),
        "notes": list(notes or []),
        "provenance": list(provenance or []),
        "not_checked": list(not_checked or []),
        "limits": list(LIMITS),
    }


def _table(payload: Any, name: str) -> Tuple[List[Dict[str, Any]], Optional[str], bool,
                                              List[Dict[str, Any]], List[str]]:
    """Строки таблицы, её источник, признак «передана», отказы и примечания.

    Таблица может быть передана тремя способами:

    - списком строк;
    - словарём ``{"rows": [...], "source": "..."}`` — источник попадает в
      ``provenance``, потому что результат без ссылки на норму проверять нечем;
    - ссылкой ``normative:<имя>`` на реестр ``data/normative`` — тогда строки и
      источник берутся из репозитория.

    После загрузки к строкам применяется ``table_filters[name]``, если он задан.
    """
    data = _as_dict(payload)
    entry = _as_dict(data.get("tables")).get(name)
    notes: List[str] = []
    if entry is None:
        # Таблица не передана, но для неё задан отбор или выбор строки: это
        # явный запрос «посчитать по реестру», берём таблицу из репозитория.
        wants = (_as_dict(_as_dict(data.get("table_filters")).get(name))
                 or _as_dict(_as_dict(data.get("table_select")).get(name)))
        if wants and name in NORMATIVE_TABLES:
            entry = "normative:" + name
            notes.append("таблица %s взята из реестра data/normative по умолчанию: "
                         "явно она передана не была" % name)
        else:
            return [], None, False, [], notes
    refusals: List[Dict[str, Any]] = []
    source: Optional[str] = None
    if normative_reference(entry):
        rows, source, refusals, notes = normative_resolve(entry)
        present = True
    elif isinstance(entry, (list, tuple)):
        rows = [r for r in entry if isinstance(r, dict)]
        present = True
    elif isinstance(entry, dict):
        raw = entry.get("rows") if "rows" in entry else entry.get("data")
        rows = [r for r in _as_list(raw) if isinstance(r, dict)]
        source = _text(entry.get("source")) or None
        present = True
    else:
        return [], None, True, [], notes
    if refusals:
        return [], source, present, refusals, notes

    spec = _as_dict(_as_dict(data.get("table_filters")).get(name))
    if spec:
        rows, refusal = normative_filter(rows, spec, name)
        if refusal:
            return [], source, present, [refusal], notes
    return rows, source, present, [], notes


def _select_row(payload: Any, name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str],
                                                  List[Dict[str, Any]], List[str]]:
    """Одна строка таблицы по ``table_select[name]``.

    Нужна там, где таблица даёт не список кандидатов для перебора, а одно
    значение: предел падения напряжения, поправочный коэффициент. Если отбор дал
    несколько строк,
    берётся первая и это помечается — молча выбрать «какую-нибудь» нельзя.
    """
    spec = _as_dict(_as_dict(_as_dict(payload).get("table_select")).get(name))
    if not spec:
        return None, None, [], []
    rows, source, present, refusals, notes = _table(payload, name)
    if refusals:
        return None, source, refusals, notes
    if not present:
        return None, None, [_refusal("no_table",
                                     "Для выбора строки нужна таблица %s." % name,
                                     required_table=name)], notes
    rows, refusal = normative_filter(rows, spec, name)
    if refusal:
        return None, source, [refusal], notes
    if len(rows) > 1:
        notes.append("отбор в таблице %s дал %d строк: взята первая, уточните "
                     "table_select" % (name, len(rows)))
    return rows[0], source, [], notes


def _provenance(name: str, source: Optional[str],
                notes: List[str]) -> List[Dict[str, Any]]:
    if source:
        return [{"table": name, "source": source}]
    notes.append("таблица %s передана без source: источник нормы неизвестен, "
                 "результат нельзя проверить по первоисточнику" % name)
    return [{"table": name, "source": None}]


def _row_number(row: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


# --------------------------------------------------------------------------
# общие входы
# --------------------------------------------------------------------------

def load_current(payload: Dict[str, Any]) -> Tuple[Optional[float], List[Dict[str, Any]]]:
    """Ток нагрузки: напрямую или из мощности. Возвращает (ток, отказы)."""
    direct = _num(payload.get("current_a"))
    if direct is not None:
        return direct, []
    power = _num(payload.get("power_w"))
    if power is None:
        kw = _num(payload.get("power_kw"))
        power = kw * 1000.0 if kw is not None else None
    if power is None:
        return None, [_refusal("no_load_current",
                               "Не задан ток нагрузки: нужно current_a либо "
                               "power_w/power_kw вместе с напряжением и cos φ.",
                               required_any=["current_a", "power_w", "power_kw"])]
    computed = calc_current(dict(payload, power_w=power))
    value = (computed.get("result") or {}).get("current_a")
    if value is None:
        return None, computed.get("refusals") or [
            _refusal("no_load_current", "Мощность задана, но ток из неё не вычислить.")]
    return value, []


def conductor_resistivity(payload_or_material: Any) -> Tuple[Optional[float], Optional[str]]:
    """Удельное сопротивление: явно или по материалу."""
    if isinstance(payload_or_material, dict):
        explicit = _num(payload_or_material.get("resistivity_ohm_mm2_per_m"))
        if explicit is not None:
            return explicit, None
        material = _text(payload_or_material.get("material")).lower()
    else:
        material = _text(payload_or_material).lower()
    if material in RESISTIVITY_OHM_MM2_PER_M:
        return RESISTIVITY_OHM_MM2_PER_M[material], material
    return None, material or None


def _sin_cos(cos_phi: float) -> Tuple[float, float]:
    cos_phi = min(max(cos_phi, 0.0), 1.0)
    return cos_phi, math.sqrt(max(0.0, 1.0 - cos_phi * cos_phi))


def _drop_volts(current: float, resistance: float, reactance: float,
                cos_phi: float, phases: int) -> float:
    cos_v, sin_v = _sin_cos(cos_phi)
    per_conductor = current * (resistance * cos_v + reactance * sin_v)
    return SQRT3 * per_conductor if phases == 3 else 2.0 * per_conductor


# --------------------------------------------------------------------------
# физика: считается без таблиц
# --------------------------------------------------------------------------

def calc_current(payload: Any) -> Dict[str, Any]:
    """Ток по мощности: ``I = P / (√3 · U · cos φ · η)`` для трёх фаз."""
    data = _as_dict(payload)
    power = _num(data.get("power_w"))
    if power is None:
        kw = _num(data.get("power_kw"))
        power = kw * 1000.0 if kw is not None else None
    voltage = _num(data.get("voltage_v"))
    cos_phi = _num(data.get("cos_phi"), 1.0)
    phases = int(_num(data.get("phases"), 3.0) or 3)
    efficiency = _num(data.get("efficiency"), 1.0)

    refusals: List[Dict[str, Any]] = []
    if power is None:
        refusals.append(_refusal("no_power", "Не задана мощность: power_w или power_kw."))
    if voltage is None or voltage <= 0:
        refusals.append(_refusal("bad_voltage", "Напряжение должно быть положительным.",
                                 required="voltage_v"))
    if cos_phi is None or not (0.0 < cos_phi <= 1.0):
        refusals.append(_refusal("bad_cos_phi", "cos φ должен быть в интервале (0, 1].",
                                 required="cos_phi"))
    if phases not in (1, 3):
        refusals.append(_refusal("bad_phases", "Поддерживаются 1 и 3 фазы.", required="phases"))
    if efficiency is None or not (0.0 < efficiency <= 1.0):
        refusals.append(_refusal("bad_efficiency", "КПД должен быть в интервале (0, 1].",
                                 required="efficiency"))
    if refusals:
        return _result(KIND_CURRENT, {}, refusals)

    if phases == 3:
        current = power / (SQRT3 * voltage * cos_phi * efficiency)
        formula = "I = P / (√3 · U · cos φ · η)"
    else:
        current = power / (voltage * cos_phi * efficiency)
        formula = "I = P / (U · cos φ · η)"
    notes = ["Для однофазного расчёта U — фазное напряжение, а не линейное."] if phases == 1 else []
    return _result(KIND_CURRENT, {
        "current_a": _round(current, 3),
        "power_w": power,
        "voltage_v": voltage,
        "cos_phi": cos_phi,
        "phases": phases,
        "efficiency": efficiency,
        "formula": formula,
    }, notes=notes)


def calc_voltage_drop(payload: Any) -> Dict[str, Any]:
    """Падение напряжения по длине линии, сечению и удельному сопротивлению."""
    data = _as_dict(payload)
    refusals: List[Dict[str, Any]] = []

    current = _num(data.get("current_a"))
    if current is None:
        current, derived = load_current(data)
        refusals.extend(derived)
    if current is None:
        if not refusals:
            refusals.append(_refusal("no_current", "Не задан ток линии."))
        return _result(KIND_VOLTAGE_DROP, {}, refusals)

    length = _num(data.get("length_m"))
    section = _num(data.get("section_mm2"))
    rho, material = conductor_resistivity(data)
    voltage = _num(data.get("voltage_v"))
    cos_phi = _num(data.get("cos_phi"), 1.0)
    reactance = _num(data.get("reactance_ohm"), 0.0)
    phases = int(_num(data.get("phases"), 3.0) or 3)

    if length is None or length <= 0:
        refusals.append(_refusal("bad_length", "Длина линии должна быть положительной.",
                                 required="length_m"))
    if section is None or section <= 0:
        refusals.append(_refusal("bad_section", "Сечение должно быть положительным.",
                                 required="section_mm2"))
    if rho is None:
        refusals.append(_refusal("unknown_material",
                                 "Материал не распознан, а удельное сопротивление не задано.",
                                 known_materials=sorted(RESISTIVITY_OHM_MM2_PER_M),
                                 required_any=["material", "resistivity_ohm_mm2_per_m"]))
    if cos_phi is None or not (0.0 < cos_phi <= 1.0):
        refusals.append(_refusal("bad_cos_phi", "cos φ должен быть в интервале (0, 1].",
                                 required="cos_phi"))
    if phases not in (1, 3):
        refusals.append(_refusal("bad_phases", "Поддерживаются 1 и 3 фазы.", required="phases"))
    if refusals:
        return _result(KIND_VOLTAGE_DROP, {}, refusals)

    resistance = rho * length / section
    drop_v = _drop_volts(current, resistance, reactance, cos_phi, phases)

    not_checked: List[Dict[str, Any]] = []
    if voltage is None or voltage <= 0:
        drop_percent = None
        not_checked.append({"criterion": "drop_percent",
                            "reason": "не задано напряжение voltage_v"})
    else:
        drop_percent = drop_v / voltage * 100.0

    return _result(KIND_VOLTAGE_DROP, {
        "current_a": _round(current, 3),
        "length_m": length,
        "section_mm2": section,
        "material": material or "custom",
        "resistivity_ohm_mm2_per_m": rho,
        "resistance_ohm": _round(resistance, 6),
        "reactance_ohm": reactance,
        "drop_v": _round(drop_v, 3),
        "drop_percent": _round(drop_percent, 3),
        "phases": phases,
        "formula": ("ΔU = √3 · I · (R · cos φ + X · sin φ)" if phases == 3
                    else "ΔU = 2 · I · (R · cos φ + X · sin φ)"),
    }, not_checked=not_checked)


def calc_fault_current(payload: Any) -> Dict[str, Any]:
    """Ток КЗ по сопротивлению источника и линии.

    Источник задаётся одним из трёх способов: сопротивлением, мощностью КЗ или
    паспортом трансформатора. Если не задан ни один — отказ: «взять типичное»
    здесь значит подменить расчёт правдоподобной цифрой.
    """
    data = _as_dict(payload)
    voltage = _num(data.get("voltage_v"))
    phases = int(_num(data.get("phases"), 3.0) or 3)
    refusals: List[Dict[str, Any]] = []
    if voltage is None or voltage <= 0:
        refusals.append(_refusal("bad_voltage", "Напряжение должно быть положительным.",
                                 required="voltage_v"))
    if phases not in (1, 3):
        refusals.append(_refusal("bad_phases", "Поддерживаются 1 и 3 фазы.", required="phases"))

    z_source = _num(data.get("source_impedance_ohm"))
    source_kind = "impedance"
    if z_source is None:
        short_circuit_mva = _num(data.get("source_short_circuit_mva"))
        if short_circuit_mva is not None and short_circuit_mva > 0:
            z_source = (voltage * voltage) / (short_circuit_mva * 1.0e6)
            source_kind = "short_circuit_power"
    if z_source is None:
        transformer = _as_dict(data.get("transformer"))
        mva = _num(transformer.get("power_mva"))
        uk = _num(transformer.get("uk_percent"))
        if mva is not None and mva > 0 and uk is not None and uk > 0:
            z_source = (uk / 100.0) * (voltage * voltage) / (mva * 1.0e6)
            source_kind = "transformer"
    if z_source is None:
        refusals.append(_refusal(
            "no_source_data",
            "Нет данных об источнике: задайте source_impedance_ohm, "
            "source_short_circuit_mva или transformer{power_mva, uk_percent}.",
            required_any=["source_impedance_ohm", "source_short_circuit_mva", "transformer"]))
    if refusals:
        return _result(KIND_FAULT_CURRENT, {}, refusals)

    length = _num(data.get("length_m"))
    section = _num(data.get("section_mm2"))
    reactance = _num(data.get("reactance_ohm"), 0.0)
    r_line = None
    line_notes: List[str] = []
    if (length is None) != (section is None):
        line_notes.append("линия задана неполностью (нужны и length_m, и "
                          "section_mm2): сопротивление линии не учтено, ток КЗ "
                          "посчитан только по источнику")
    if length is not None and section is not None:
        if section <= 0:
            refusals.append(_refusal("bad_section", "Сечение должно быть положительным.",
                                     required="section_mm2"))
        else:
            rho, _material = conductor_resistivity(data)
            if rho is None:
                refusals.append(_refusal(
                    "unknown_material",
                    "Задана линия, но материал не распознан и удельное сопротивление не задано.",
                    known_materials=sorted(RESISTIVITY_OHM_MM2_PER_M)))
            else:
                r_line = rho * length / section
    if refusals:
        return _result(KIND_FAULT_CURRENT, {}, refusals)

    r_total = (r_line or 0.0)
    x_total = reactance
    if phases == 3:
        z_total = math.sqrt((z_source + r_total) ** 2 + x_total ** 2)
        current = voltage / (SQRT3 * z_total)
        formula = "Iкз = U / (√3 · |Zист + Zлин|)"
    else:
        loop = _num(data.get("zero_sequence_impedance_ohm"))
        if loop is None:
            return _result(KIND_FAULT_CURRENT, {}, [_refusal(
                "no_zero_sequence_data",
                "Однофазная петля «фаза-нуль» без сопротивления нулевой "
                "последовательности не считается: это не консервативная оценка, "
                "а неизвестная величина.",
                required="zero_sequence_impedance_ohm")])
        z_total = math.sqrt((z_source + r_total + loop) ** 2 + x_total ** 2)
        current = (voltage / SQRT3) / z_total
        formula = "Iкз = Uф / |Zпетли|"

    return _result(KIND_FAULT_CURRENT, {
        "fault_current_a": _round(current, 2),
        "fault_current_ka": _round(current / 1000.0, 3),
        "source_kind": source_kind,
        "impedance": {
            "source_ohm": _round(z_source, 6),
            "line_r_ohm": _round(r_total, 6),
            "line_x_ohm": _round(x_total, 6),
            "total_ohm": _round(z_total, 6),
        },
        "voltage_v": voltage,
        "phases": phases,
        "formula": formula,
    }, notes=line_notes + ["Сопротивлением дуги и переходными сопротивлениями "
                           "контактов расчёт пренебрегает: результат — верхняя "
                           "оценка."])


def calc_derating(payload: Any) -> Dict[str, Any]:
    """Произведение поправочных коэффициентов на допустимый ток."""
    data = _as_dict(payload)
    base = _num(data.get("base_current_a"))
    refusals: List[Dict[str, Any]] = []
    if base is None or base <= 0:
        refusals.append(_refusal("bad_base_current",
                                 "Нужен положительный базовый ток base_current_a.",
                                 required="base_current_a"))
        return _result(KIND_DERATING, {}, refusals)

    raw = data.get("factors")
    if isinstance(raw, dict):
        factors = [{"name": key, "value": _num(value)} for key, value in raw.items()]
    else:
        factors = [{"name": _text(f.get("name")) or "factor_%d" % i,
                    "value": _num(f.get("value"))}
                   for i, f in enumerate(_as_list(raw)) if isinstance(f, dict)]

    notes: List[str] = []
    provenance: List[Dict[str, Any]] = []

    # Коэффициент из нормативной таблицы: поправка на температуру окружающей
    # среды и на группировку нормируется, поэтому её можно взять из реестра, а
    # не из памяти. Свои факторы при этом не отменяются.
    selected, source, select_refusals, select_notes = _select_row(data, "derating")
    notes.extend(select_notes)
    refusals.extend(select_refusals)
    if selected is not None:
        factor = _num(selected.get("factor"))
        if factor is None:
            refusals.append(_refusal("bad_derating_row",
                                     "В строке таблицы derating нет числового factor."))
        else:
            name = "derating"
            for key in ("kind", "insulation", "ambient_c", "circuits", "item"):
                if selected.get(key) is not None:
                    name += "." + str(selected[key]).replace(" ", "_")
            factors.append({"name": name, "value": factor})
            provenance.append({"table": "derating", "source": source,
                               "factor": factor, "row": name})
    if refusals:
        return _result(KIND_DERATING, {}, refusals, notes=notes,
                       provenance=provenance)
    product = 1.0
    applied: List[Dict[str, Any]] = []
    for factor in factors:
        value = factor["value"]
        if value is None:
            refusals.append(_refusal("bad_factor",
                                     "Коэффициент %s не числовой." % factor["name"],
                                     factor=factor["name"]))
            continue
        if value <= 0:
            refusals.append(_refusal("bad_factor",
                                     "Коэффициент %s должен быть положительным." % factor["name"],
                                     factor=factor["name"]))
            continue
        if value > 1.0:
            notes.append("коэффициент %s больше единицы: это повышающая поправка, "
                         "проверьте источник" % factor["name"])
        product *= value
        applied.append({"name": factor["name"], "value": _round(value, 4)})
    if not factors:
        notes.append("поправочные коэффициенты не заданы: произведение равно 1, "
                     "ток не снижен")

    if refusals:
        return _result(KIND_DERATING, {}, refusals, notes=notes,
                       provenance=provenance)
    return _result(KIND_DERATING, {
        "base_current_a": _round(base, 3),
        "factors": applied,
        "product": _round(product, 6),
        "derated_current_a": _round(base * product, 3),
    }, notes=notes, provenance=provenance)


# --------------------------------------------------------------------------
# нормы: считается только с переданной таблицей
# --------------------------------------------------------------------------

def calc_max_demand(payload: Any) -> Dict[str, Any]:
    """Расчётная нагрузка по установленной мощности и коэффициентам спроса."""
    data = _as_dict(payload)
    loads = [l for l in _as_list(data.get("loads")) if isinstance(l, dict)]
    refusals: List[Dict[str, Any]] = []
    if not loads:
        refusals.append(_refusal("no_loads", "Не заданы нагрузки: список loads пуст.",
                                 required="loads"))
    rows, source, present, table_refusals, table_notes = _table(data, "demand_factors")
    notes: List[str] = list(table_notes)
    provenance: List[Dict[str, Any]] = []
    refusals.extend(table_refusals)
    if not present:
        refusals.append(_refusal("no_table",
                                 "Нужна таблица коэффициентов спроса demand_factors.",
                                 required_table="demand_factors"))
    else:
        provenance = _provenance("demand_factors", source, notes)
    if refusals:
        return _result(KIND_MAX_DEMAND, {}, refusals, notes=notes, provenance=provenance)

    by_type: Dict[str, float] = {}
    for row in rows:
        key = _text(row.get("load_type")) or _text(row.get("type"))
        value = _row_number(row, "demand_factor", "kc", "factor", "value")
        if key and value is not None:
            by_type[key.lower()] = value

    per_load: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    installed = 0.0
    demand = 0.0
    for index, load in enumerate(loads):
        power = _num(load.get("power_w"))
        if power is None:
            kw = _num(load.get("power_kw"))
            power = kw * 1000.0 if kw is not None else None
        count = _num(load.get("count"), 1.0) or 1.0
        if power is None:
            unverified.append({"index": index, "reason": "no_power"})
            continue
        group = power * count
        installed += group
        key = _text(load.get("load_type")).lower()
        factor = by_type.get(key)
        if factor is None:
            unverified.append({"index": index, "load_type": key,
                               "reason": "no_demand_factor"})
            continue
        demand += group * factor
        per_load.append({"index": index, "load_type": key,
                         "installed_w": _round(group, 1),
                         "demand_factor": _round(factor, 4),
                         "demand_w": _round(group * factor, 1)})

    out: Dict[str, Any] = {
        "installed_power_w": _round(installed, 1),
        "demand_power_w": _round(demand, 1),
        "per_load": per_load,
        "unverified": unverified,
    }
    not_checked: List[Dict[str, Any]] = []
    voltage = _num(data.get("voltage_v"))
    cos_phi = _num(data.get("cos_phi"))
    if voltage and cos_phi:
        phases = int(_num(data.get("phases"), 3.0) or 3)
        divisor = SQRT3 * voltage * cos_phi if phases == 3 else voltage * cos_phi
        out["demand_current_a"] = _round(demand / divisor, 3)
    else:
        not_checked.append({"criterion": "demand_current_a",
                            "reason": "не заданы voltage_v и cos_phi"})
    return _result(KIND_MAX_DEMAND, out, notes=notes, provenance=provenance,
                   not_checked=not_checked)


def calc_cable_size(payload: Any) -> Dict[str, Any]:
    """Выбор сечения: перебор кандидатов из таблицы допустимых токов.

    Сечение считается выбранным, только если прошли **все** критерии, которые
    удалось проверить. Критерий без данных не считается выполненным — он уходит
    в ``not_checked``.
    """
    data = _as_dict(payload)
    current, refusals = load_current(data)
    rows, source, present, table_refusals, table_notes = _table(data, "ampacity")
    notes: List[str] = list(table_notes)
    provenance: List[Dict[str, Any]] = []
    refusals.extend(table_refusals)
    if not present:
        refusals.append(_refusal("no_table",
                                 "Нужна таблица допустимых токов ampacity "
                                 "(строки: section_mm2, current_a).",
                                 required_table="ampacity"))
    else:
        provenance = _provenance("ampacity", source, notes)
    if refusals:
        return _result(KIND_CABLE_SIZE, {}, refusals, notes=notes, provenance=provenance)

    derating = _num(data.get("derating_product"))
    if derating is None:
        factors = calc_derating({"base_current_a": 1.0,
                                 "factors": data.get("factors")})
        derating = (factors.get("result") or {}).get("product", 1.0)
    if not derating or derating <= 0:
        derating = 1.0

    length = _num(data.get("length_m"))
    voltage = _num(data.get("voltage_v"))
    cos_phi = _num(data.get("cos_phi"), 1.0)
    phases = int(_num(data.get("phases"), 3.0) or 3)
    drop_limit = _num(data.get("drop_limit_percent"))
    if drop_limit is None:
        limit_row, limit_source, limit_refusals, limit_notes = _select_row(
            data, "voltage_drop_limits")
        notes.extend(limit_notes)
        refusals.extend(limit_refusals)
        if limit_row is not None:
            drop_limit = _num(limit_row.get("limit_percent"))
            if drop_limit is not None:
                provenance.append({
                    "table": "voltage_drop_limits",
                    "source": limit_source,
                    "installation_type": limit_row.get("installation_type"),
                    "load_type": limit_row.get("load_type"),
                })
    reactance = _num(data.get("reactance_ohm"), 0.0)
    rho, material = conductor_resistivity(data)
    if refusals:
        return _result(KIND_CABLE_SIZE, {}, refusals, notes=notes,
                       provenance=provenance)

    not_checked: List[Dict[str, Any]] = []
    if drop_limit is None:
        not_checked.append({"criterion": "voltage_drop",
                            "reason": "не задан drop_limit_percent и не выбрана "
                                      "строка таблицы voltage_drop_limits"})
    if rho is None:
        not_checked.append({"criterion": "voltage_drop",
                            "reason": "материал не распознан и удельное сопротивление не задано"})

    candidates: List[Dict[str, Any]] = []
    for row in rows:
        section = _row_number(row, "section_mm2", "section")
        table_current = _row_number(row, "current_a", "ampacity_a", "ampacity")
        if section is None or table_current is None:
            continue
        allowable = table_current * derating
        candidate: Dict[str, Any] = {
            "section_mm2": section,
            "table_current_a": _round(table_current, 3),
            "derating_product": _round(derating, 6),
            "allowable_current_a": _round(allowable, 3),
            "passes_current": allowable >= current,
        }
        drop_percent = None
        if length and section > 0 and voltage and voltage > 0 and rho is not None:
            resistance = rho * length / section
            drop_v = _drop_volts(current, resistance, reactance, cos_phi, phases)
            drop_percent = drop_v / voltage * 100.0
        candidate["drop_percent"] = _round(drop_percent, 3)
        if drop_percent is None:
            candidate["passes_drop"] = None
        elif drop_limit is None:
            candidate["passes_drop"] = None
        else:
            candidate["passes_drop"] = drop_percent <= drop_limit
        checks = [value for value in (candidate["passes_current"], candidate["passes_drop"])
                  if value is not None]
        candidate["passes_all_checked"] = bool(checks) and all(checks)
        candidates.append(candidate)

    candidates.sort(key=lambda item: item["section_mm2"])
    selected = next((c for c in candidates if c["passes_all_checked"]), None)

    result: Dict[str, Any] = {
        "load_current_a": _round(current, 3),
        "derating_product": _round(derating, 6),
        "material": material or "custom",
        "candidates": candidates,
        "selected": selected,
    }
    if selected is None and candidates:
        result["selection_note"] = ("ни одно сечение из таблицы не прошло проверенные "
                                    "критерии; увеличите сечение, снизьте нагрузку или "
                                    "проверьте таблицу")
    return _result(KIND_CABLE_SIZE, result, notes=notes, provenance=provenance,
                   not_checked=not_checked)


def calc_breaker_select(payload: Any) -> Dict[str, Any]:
    """Подбор аппарата по каталогу: ток нагрузки, сечение кабеля, ток КЗ."""
    data = _as_dict(payload)
    current, refusals = load_current(data)
    rows, source, present, table_refusals, table_notes = _table(data, "breakers")
    notes: List[str] = list(table_notes)
    provenance: List[Dict[str, Any]] = []
    refusals.extend(table_refusals)
    if not present:
        refusals.append(_refusal("no_table",
                                 "Нужен каталог аппаратов breakers "
                                 "(строки: rating_a, breaking_ka).",
                                 required_table="breakers"))
    else:
        provenance = _provenance("breakers", source, notes)
    if refusals:
        return _result(KIND_BREAKER_SELECT, {}, refusals, notes=notes, provenance=provenance)

    allowable = _num(data.get("allowable_current_a"))
    fault_ka = _num(data.get("fault_current_ka"))
    if fault_ka is None:
        fault_a = _num(data.get("fault_current_a"))
        fault_ka = fault_a / 1000.0 if fault_a is not None else None
    poles = _num(data.get("poles"))
    curve = _text(data.get("trip_curve"))

    not_checked: List[Dict[str, Any]] = []
    if allowable is None:
        not_checked.append({"criterion": "cable_protection",
                            "reason": "не задан allowable_current_a кабеля"})
    if fault_ka is None:
        not_checked.append({"criterion": "breaking_capacity",
                            "reason": "не задан ток КЗ"})

    candidates: List[Dict[str, Any]] = []
    for row in rows:
        rating = _row_number(row, "rating_a", "rating", "nominal_current_a")
        if rating is None:
            continue
        if poles is not None and _row_number(row, "poles") not in (None, poles):
            continue
        if curve and _text(row.get("trip_curve")) and _text(row.get("trip_curve")).upper() != curve.upper():
            continue
        breaking = _row_number(row, "breaking_ka", "breaking_capacity_ka", "icu_ka")
        passes: Dict[str, Any] = {"passes_load": rating >= current}
        if allowable is not None:
            passes["passes_cable"] = rating <= allowable
        if fault_ka is not None and breaking is not None:
            passes["passes_breaking"] = breaking >= fault_ka
        elif fault_ka is not None:
            passes["passes_breaking"] = None
            not_checked.append({"criterion": "breaking_capacity",
                                "reason": "в каталоге нет отключающей способности"})
        checks = [v for v in passes.values() if v is not None]
        candidate = {"rating_a": rating,
                     "trip_curve": _text(row.get("trip_curve")) or None,
                     "breaking_ka": breaking,
                     "designation": _text(row.get("designation")) or None,
                     "passes_all_checked": bool(checks) and all(checks)}
        candidate.update(passes)
        candidates.append(candidate)

    candidates.sort(key=lambda item: item["rating_a"])
    selected = next((c for c in candidates if c["passes_all_checked"]), None)
    return _result(KIND_BREAKER_SELECT, {
        "load_current_a": _round(current, 3),
        "allowable_current_a": allowable,
        "fault_current_ka": fault_ka,
        "candidates": candidates,
        "selected": selected,
    }, notes=notes, provenance=provenance, not_checked=not_checked)


def calc_selectivity(payload: Any) -> Dict[str, Any]:
    """Селективность по время-токовым характеристикам.

    Сравниваются верхняя граница времени отключения нижестоящего аппарата и
    нижняя граница вышестоящего: ``t_верх_min > t_низ_max``. Если точка на
    характеристике не задана, а попадает между точками, результат помечается
    ``interpolated`` — молча интерполировать и выдавать за расчёт по каталогу
    здесь нельзя.
    """
    data = _as_dict(payload)
    fault_a = _num(data.get("fault_current_a"))
    refusals: List[Dict[str, Any]] = []
    if fault_a is None or fault_a <= 0:
        refusals.append(_refusal("no_fault_current",
                                 "Нужен положительный ток КЗ fault_current_a.",
                                 required="fault_current_a"))

    upstream = _as_dict(data.get("upstream"))
    downstream = _as_dict(data.get("downstream"))
    up_rating = _num(upstream.get("rating_a"))
    down_rating = _num(downstream.get("rating_a"))
    if up_rating is None or up_rating <= 0:
        refusals.append(_refusal("no_upstream", "Не задан номинал вышестоящего аппарата.",
                                 required="upstream.rating_a"))
    if down_rating is None or down_rating <= 0:
        refusals.append(_refusal("no_downstream", "Не задан номинал нижестоящего аппарата.",
                                 required="downstream.rating_a"))

    up_times = (_num(upstream.get("t_min_s")), _num(upstream.get("t_max_s")))
    down_times = (_num(downstream.get("t_min_s")), _num(downstream.get("t_max_s")))

    rows, source, present, table_refusals, table_notes = _table(data, "trip_curves")
    notes: List[str] = list(table_notes)
    provenance: List[Dict[str, Any]] = []
    refusals.extend(table_refusals)
    need_table = up_times[0] is None or down_times[1] is None
    if need_table and not present:
        refusals.append(_refusal(
            "no_table",
            "Время отключения не задано напрямую — нужна таблица trip_curves "
            "(строки: rating_a, trip_curve, points[{multiple, t_min, t_max}]).",
            required_table="trip_curves"))
    elif present:
        provenance = _provenance("trip_curves", source, notes)
    if refusals:
        return _result(KIND_SELECTIVITY, {}, refusals, notes=notes, provenance=provenance)

    def from_curve(rating: float, curve_name: str) -> Tuple[Optional[float], Optional[float], List[str]]:
        for row in rows:
            if _row_number(row, "rating_a", "rating") != rating:
                continue
            if curve_name and _text(row.get("trip_curve")).upper() != curve_name.upper():
                continue
            points = _as_list(row.get("points"))
            multiple = fault_a / rating if rating else None
            if multiple is None:
                return None, None, []
            ordered = sorted((p for p in points if isinstance(p, dict)
                              and _num(p.get("multiple")) is not None),
                             key=lambda p: _num(p.get("multiple")))
            for point in ordered:
                if abs(_num(point.get("multiple")) - multiple) < 1e-9:
                    return _num(point.get("t_min_s")), _num(point.get("t_max_s")), []
            lower = upper = None
            for point in ordered:
                if _num(point.get("multiple")) < multiple:
                    lower = point
                elif _num(point.get("multiple")) > multiple and upper is None:
                    upper = point
            if lower is None or upper is None:
                return None, None, ["ток вне диапазона характеристики"]
            lo_m, hi_m = _num(lower.get("multiple")), _num(upper.get("multiple"))
            span = hi_m - lo_m
            flags: List[str] = [
                "interpolated: ток между узлами характеристики, время получено "
                "линейной интерполяцией. Характеристики логарифмические, поэтому "
                "линейная интерполяция груба — для ответственных ответов берите "
                "узел из каталога"]
            out_times = []
            for key in ("t_min_s", "t_max_s"):
                lo_v, hi_v = _num(lower.get(key)), _num(upper.get(key))
                if lo_v is None or hi_v is None:
                    out_times.append(None)
                    continue
                out_times.append(lo_v + (hi_v - lo_v) * (multiple - lo_m) / span)
            return out_times[0], out_times[1], flags
        return None, None, ["характеристика не найдена"]

    flags: List[str] = []
    if up_times[0] is None:
        t_min_up, _t, extra = from_curve(up_rating, _text(upstream.get("trip_curve")))
        flags.extend(extra)
        up_times = (t_min_up, up_times[1])
    if down_times[1] is None:
        _t, t_max_down, extra = from_curve(down_rating, _text(downstream.get("trip_curve")))
        flags.extend(extra)
        down_times = (down_times[0], t_max_down)

    not_checked: List[Dict[str, Any]] = []
    if up_times[0] is None:
        not_checked.append({"criterion": "upstream_t_min",
                            "reason": "нет нижней границы времени вышестоящего аппарата"})
    if down_times[1] is None:
        not_checked.append({"criterion": "downstream_t_max",
                            "reason": "нет верхней границы времени нижестоящего аппарата"})

    selective: Optional[bool] = None
    margin = None
    if up_times[0] is not None and down_times[1] is not None:
        selective = up_times[0] > down_times[1]
        margin = up_times[0] - down_times[1]

    return _result(KIND_SELECTIVITY, {
        "fault_current_a": _round(fault_a, 3),
        "upstream": {"rating_a": up_rating, "t_min_s": _round(up_times[0], 6),
                     "t_max_s": _round(up_times[1], 6),
                     "multiple": _round(fault_a / up_rating, 3)},
        "downstream": {"rating_a": down_rating, "t_min_s": _round(down_times[0], 6),
                       "t_max_s": _round(down_times[1], 6),
                       "multiple": _round(fault_a / down_rating, 3)},
        "selective": selective,
        "margin_s": _round(margin, 6),
        "criterion": "t_min(вышестоящий) > t_max(нижестоящий)",
    }, notes=notes + flags, provenance=provenance, not_checked=not_checked)


# --------------------------------------------------------------------------
# диспетчер
# --------------------------------------------------------------------------

_DISPATCH = {
    KIND_CURRENT: calc_current,
    KIND_MAX_DEMAND: calc_max_demand,
    KIND_VOLTAGE_DROP: calc_voltage_drop,
    KIND_FAULT_CURRENT: calc_fault_current,
    KIND_DERATING: calc_derating,
    KIND_CABLE_SIZE: calc_cable_size,
    KIND_BREAKER_SELECT: calc_breaker_select,
    KIND_SELECTIVITY: calc_selectivity,
}


def calculate(payload: Any) -> Dict[str, Any]:
    """Единая точка входа: ``{"kind": ..., "tables": {...}}``."""
    data = _as_dict(payload)
    kind = _text(data.get("kind")).lower()
    if kind not in _DISPATCH:
        return {
            "kind": kind or None,
            "ok": False,
            "verdict": "refused",
            "result": {},
            "refusals": [_refusal("unknown_kind",
                                  "Неизвестный вид расчёта: %s." % (kind or "не задан"),
                                  known_kinds=list(KINDS))],
            "notes": [],
            "provenance": [],
            "not_checked": [],
            "limits": list(LIMITS),
        }
    return _DISPATCH[kind](data)
