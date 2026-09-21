from __future__ import annotations

import math
from collections import OrderedDict


def _value(obj, name, default=None):
    try:
        value = getattr(obj, name)
        value = value() if callable(value) else value
        return value
    except Exception:
        return default


def _number(value):
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _walk(part, path, rows, max_depth):
    if len(path) >= max_depth:
        return
    parts = part.Parts
    count = int(parts.Count)
    for index in range(count):
        child = parts.Part(index)
        child_path = path + [index]
        source = str(_value(child, "FileName", "") or "")
        create_spc = _value(child, "CreateSpcObjects")
        inherit_exclude = _value(child, "InheritExclude")
        row = {
            "selector_path": child_path,
            "designation": str(_value(child, "Marking", "") or ""),
            "name": str(_value(child, "Name", "") or ""),
            "source_file": source,
            "material": str(_value(child, "Material", "") or ""),
            "mass": _number(_value(child, "Mass")),
            "density": _number(_value(child, "Density")),
            "standard_component": _value(child, "Standard"),
            "create_specification_objects": create_spc,
            "inherit_exclude": inherit_exclude,
            "parts_group_number": _value(child, "PartsGroupNumber"),
            "specification_eligible": bool(create_spc) and inherit_exclude is not True,
            "is_local": _value(child, "IsLocal"),
            "is_billet": _value(child, "IsBillet"),
        }
        rows.append(row)
        _walk(child, child_path, rows, max_depth)


def _description_row(description, index):
    row = {"index": index}
    for name in (
        "Name",
        "SpcName",
        "LayoutName",
        "StyleID",
        "Reference",
        "Type",
        "Valid",
    ):
        value = _value(description, name)
        if isinstance(value, (str, bool, int, float)) or value is None:
            row[name] = value
    return row


def bom_read(session, max_depth=8):
    import win32com.client as wc

    max_depth = int(max_depth)
    if max_depth < 1 or max_depth > 15:
        raise ValueError("max_depth_must_be_between_1_and_15")
    doc = session.active()
    active = str(session.active_path() or "")
    if doc is None or not active:
        raise RuntimeError("no_active_document_path")
    doc3 = wc.CastTo(doc, "IKompasDocument3D")
    top = wc.CastTo(doc3.TopPart, "IPart7")

    occurrences = []
    _walk(top, [], occurrences, max_depth)
    eligible = [row for row in occurrences if row["specification_eligible"]]
    grouped = OrderedDict()
    for row in eligible:
        key = (
            row["source_file"].casefold(),
            row["designation"],
            row["name"],
            row["material"],
        )
        if key not in grouped:
            grouped[key] = {
                "designation": row["designation"],
                "name": row["name"],
                "source_file": row["source_file"],
                "material": row["material"],
                "unit_mass": row["mass"],
                "standard_component": row["standard_component"],
                "occurrence_count": 0,
                "quantity_basis": "count of exact eligible assembly occurrences",
                "selector_paths": [],
            }
        item = grouped[key]
        item["occurrence_count"] += 1
        item["selector_paths"].append(row["selector_path"])
    bom_rows = list(grouped.values())

    descriptions = []
    description_error = None
    try:
        collection = doc3.SpecificationDescriptions
        description_count = int(collection.Count)
        for index in range(description_count):
            descriptions.append(_description_row(collection.Item(index), index))
    except Exception as exc:
        description_count = None
        description_error = type(exc).__name__ + ":" + str(exc)

    return {
        "requested_action": {"max_depth": max_depth},
        "active_document": active,
        "target": active,
        "occurrence_count": len(occurrences),
        "eligible_occurrence_count": len(eligible),
        "excluded_occurrence_count": len(occurrences) - len(eligible),
        "bom_row_count": len(bom_rows),
        "bom_rows": bom_rows,
        "occurrences": occurrences,
        "native_specification_descriptions": {
            "count": description_count,
            "items": descriptions,
            "error": description_error,
        },
        "verification": {
            "native_assembly_tree": "API7 IPart7.Parts recursion",
            "native_membership_switch": "IPart7.CreateSpcObjects",
            "native_exclusion_switch": "IPart7.InheritExclude",
            "native_specification_description_collection_read": description_error is None,
            "quantity_is_exact_occurrence_count": True,
            "no_floating_text_or_filename_inventory_substitution": True,
            "read_only": True,
        },
        "warnings": [
            "BOM quantity is the exact count of eligible occurrences grouped by source/designation/name/material; it is not an unsupported component Quantity property.",
            "A3D SpecificationDescriptions metadata is reported separately from occurrence-derived BOM rows.",
        ],
        "read_only": True,
        "saved": False,
        "save_required": False,
    }
