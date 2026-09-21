"""Проверка артефактов, которые мост получает от КОМПАСа.

Зачем этот модуль существует
---------------------------
Экспорт из КОМПАСа (STEP, DXF, PDF) возвращает управление успешно почти
всегда: COM-метод не бросает исключение и тогда, когда файл получился
пустым, обрезанным или содержит только заголовок. Раньше единственным
доказательством успеха экспорта было само отсутствие исключения, поэтому
агент мог объявить выгрузку выполненной по пустышке.

Здесь проверяется содержимое файла, а не факт вызова метода:

* STEP - сигнатура ISO-10303-21, закрывающий END-ISO-10303-21, число
  сущностей, наличие тел и граней;
* DXF - секции, наличие ENTITIES и EOF, число сущностей, единицы измерения;
* PDF - версия, завершающий %%EOF, число страниц;
* PNG - сигнатура, чанк IHDR, реальные ширина и высота.

Модуль намеренно не зависит от COM: он работает с уже лежащим на диске
файлом, поэтому его можно проверить тестами без запущенного КОМПАСа.

Границы честности
-----------------
Проверка структурная. Она отвечает на вопрос "файл целый и непустой", но
не на вопрос "геометрия в файле та же, что в модели". Для второго нужен
импорт обратно в КОМПАС и сверка габаритов - это отдельная операция,
и здесь она не имитируется.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings_for

# Больше этого не читаем в память: STEP-файл крупной сборки легко
# переваливает за сотню мегабайт, а ответ инструмента должен остаться
# компактным.
MAX_SCAN_BYTES = 32 * 1024 * 1024

KIND_BY_SUFFIX: Dict[str, str] = {
    ".step": "step",
    ".stp": "step",
    ".dxf": "dxf",
    ".pdf": "pdf",
    ".png": "png",
}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def detect_kind(name: str, head: bytes = b"") -> Optional[str]:
    """Определить формат по расширению, с откатом на сигнатуру."""
    suffix = Path(str(name)).suffix.lower()
    if suffix in KIND_BY_SUFFIX:
        return KIND_BY_SUFFIX[suffix]
    if head.startswith(PNG_SIGNATURE):
        return "png"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.lstrip()[:14].upper().startswith(b"ISO-10303-21"):
        return "step"
    return None


def _text(data: bytes) -> str:
    return data.decode("latin-1", errors="replace")


def _result(kind: str, ok: bool, findings: List[str], **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"kind": kind, "ok": bool(ok), "findings": list(findings)}
    payload.update(extra)
    return payload


def check_step(data: bytes) -> Dict[str, Any]:
    text = _text(data)
    findings: List[str] = []
    counts: Dict[str, Any] = {}

    starts_ok = text.lstrip()[:14].upper().startswith("ISO-10303-21")
    if not starts_ok:
        findings.append("missing_iso_10303_21_header")

    ends_ok = "END-ISO-10303-21" in text
    if not ends_ok:
        findings.append("missing_end_iso_10303_21_marker")

    entity_lines = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    counts["entities"] = len(entity_lines)
    if counts["entities"] == 0:
        findings.append("no_step_entities")

    for keyword, key in (
        ("MANIFOLD_SOLID_BREP", "solid_breps"),
        ("ADVANCED_FACE", "advanced_faces"),
        ("CARTESIAN_POINT", "cartesian_points"),
        ("PRODUCT", "products"),
    ):
        counts[key] = text.count(keyword)

    if counts["solid_breps"] == 0 and counts["advanced_faces"] == 0:
        findings.append("no_solid_or_face_geometry")

    schema = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text)
    counts["file_schema"] = schema.group(1) if schema else None

    header_ok = "HEADER" in text and "DATA" in text
    if not header_ok:
        findings.append("missing_header_or_data_section")

    ok = starts_ok and ends_ok and counts["entities"] > 0 and not findings
    return _result("step", ok, findings, counts=counts, header_section=header_ok)


def check_dxf(data: bytes) -> Dict[str, Any]:
    text = _text(data)
    lines = [line.strip() for line in text.splitlines()]
    findings: List[str] = []
    sections: List[str] = []
    entities: Dict[str, int] = {}
    header_values: Dict[str, str] = {}

    index = 0
    inside_entities = False
    while index < len(lines) - 1:
        code = lines[index].strip()
        value = lines[index + 1].strip()
        if code == "0" and value.upper() == "SECTION":
            name = ""
            if index + 3 < len(lines) and lines[index + 2].strip() == "2":
                name = lines[index + 3].strip().upper()
            sections.append(name)
            inside_entities = name == "ENTITIES"
        elif code == "0" and value.upper() == "ENDSEC":
            inside_entities = False
        elif inside_entities and code == "0" and value:
            key = value.upper()
            entities[key] = entities.get(key, 0) + 1
        elif code == "9" and value:
            if index + 3 < len(lines):
                header_values[value] = lines[index + 3].strip()
        index += 2

    if not sections:
        findings.append("no_dxf_sections")
    if "ENTITIES" not in sections:
        findings.append("missing_entities_section")
    if not any(line.strip().upper() == "EOF" for line in lines[-20:] or []):
        findings.append("missing_eof_marker")

    total_entities = sum(entities.values())
    if total_entities == 0:
        findings.append("no_dxf_entities")

    counts: Dict[str, Any] = {
        "sections": sections,
        "entities": total_entities,
        "entity_kinds": dict(sorted(entities.items(), key=lambda kv: -kv[1])[:20]),
        "acad_version": header_values.get("$ACADVER"),
        "units_code": header_values.get("$INSUNITS"),
    }

    ok = not findings
    return _result("dxf", ok, findings, counts=counts)


def check_pdf(data: bytes) -> Dict[str, Any]:
    text = _text(data)
    findings: List[str] = []

    version = None
    match = re.search(r"%PDF-(\d\.\d)", text[:1024])
    if match:
        version = match.group(1)
    else:
        findings.append("missing_pdf_header")

    tail = text[-4096:]
    if "%%EOF" not in tail:
        findings.append("missing_eof_marker")

    pages = len(re.findall(r"/Type\s*/Page(?![sA-Za-z])", text))
    counts: Dict[str, Any] = {"version": version, "pages": pages}
    if pages == 0:
        findings.append("no_pdf_pages")

    return _result("pdf", not findings, findings, counts=counts)


def check_png(data: bytes) -> Dict[str, Any]:
    findings: List[str] = []
    counts: Dict[str, Any] = {}

    if not data.startswith(PNG_SIGNATURE):
        return _result("png", False, ["missing_png_signature"], counts=counts)

    if data[12:16] != b"IHDR":
        findings.append("missing_ihdr_chunk")
        return _result("png", False, findings, counts=counts)

    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    counts["width"] = width
    counts["height"] = height
    counts["has_iend"] = data.rstrip().endswith(b"IEND\xaeB`\x82")

    if width <= 0 or height <= 0:
        findings.append("degenerate_png_size")
    if not counts["has_iend"]:
        findings.append("missing_iend_chunk")

    return _result("png", not findings, findings, counts=counts)


CHECKS = {
    "step": check_step,
    "dxf": check_dxf,
    "pdf": check_pdf,
    "png": check_png,
}


def _resolve(bridge_root: Any, relative_path: str) -> Path:
    """Найти файл только внутри разрешённых корней моста."""
    settings = settings_for(bridge_root)
    raw = str(relative_path or "").strip()
    if not raw:
        raise ValueError("relative_path_required")

    candidate = Path(raw)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        target = (settings.work_dir / candidate).resolve()

    allowed = (settings.work_dir,) + tuple(settings.write_roots)
    if not any(target == root or target.is_relative_to(root) for root in allowed):
        raise RuntimeError("path_outside_approved_roots:" + str(target))
    if settings.is_protected(target):
        raise RuntimeError("path_protected:" + str(target))
    if not target.is_file():
        raise FileNotFoundError(str(target))
    return target


def artifact_check(
    bridge_root: Any,
    relative_path: str,
    max_scan_bytes: int = MAX_SCAN_BYTES,
) -> Dict[str, Any]:
    """Проверить один артефакт экспорта и вернуть структурный вердикт."""
    target = _resolve(bridge_root, relative_path)
    size = target.stat().st_size

    with target.open("rb") as handle:
        data = handle.read(int(max_scan_bytes))
    truncated = size > len(data)

    kind = detect_kind(target.name, data[:64])
    warnings: List[str] = []
    if truncated:
        warnings.append(
            "scan_truncated: проверены первые "
            f"{len(data)} байт из {size}; хвост файла не анализировался"
        )

    if kind is None:
        return {
            "path_relative": str(relative_path),
            "kind": None,
            "size_bytes": size,
            "ok": False,
            "findings": ["unsupported_artifact_kind"],
            "counts": {},
            "warnings": warnings,
            "limits": [
                "Поддержаны только step/stp, dxf, pdf и png. "
                "Другие форматы не проверяются, а не считаются валидными."
            ],
        }

    outcome = CHECKS[kind](data)
    outcome.update({
        "path_relative": str(relative_path),
        "size_bytes": size,
        "scan_bytes": len(data),
        "scan_truncated": truncated,
        "warnings": warnings,
        "limits": [
            "Проверка структурная: целостность файла, а не совпадение "
            "геометрии с моделью.",
            "Совпадение геометрии подтверждается только обратным импортом "
            "в КОМПАС и сверкой габаритов - эта операция здесь не выполняется.",
        ],
    })
    return outcome
