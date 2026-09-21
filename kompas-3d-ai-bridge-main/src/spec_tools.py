"""Specification reporting.

The bridge does not invent a native .spw object model: reading a real
specification document (ISpcObject and friends) cannot be verified without a live
KOMPAS install, and a guessed object model is worse than an honest refusal.

What this module does instead: take the *native* assembly occurrence data
(API7 IPart7 tree with CreateSpcObjects / InheritExclude / Standard / IsBillet),
derive a GOST section layout from it, and label the result as derived. Section
assignment is pure logic and fully testable.
"""

from __future__ import annotations

from collections import OrderedDict

SECTION_ORDER = (
    "documentation",
    "assembly_units",
    "details",
    "standard_products",
    "other_products",
    "materials",
    "kits",
)

SECTION_TITLES = {
    "documentation": "Документация",
    "assembly_units": "Сборочные единицы",
    "details": "Детали",
    "standard_products": "Стандартные изделия",
    "other_products": "Прочие изделия",
    "materials": "Материалы",
    "kits": "Комплекты",
}


def mark_children(rows):
    """A component is an assembly unit when another occurrence path extends it."""
    paths = {tuple(row.get("selector_path") or ()) for row in rows}
    marked = []
    for row in rows:
        path = tuple(row.get("selector_path") or ())
        depth = len(path)
        has_children = any(
            len(other) == depth + 1 and other[:depth] == path for other in paths
        )
        item = dict(row)
        item["has_children"] = has_children
        marked.append(item)
    return marked


def classify(row):
    """Assign a GOST section from native flags. Order matters."""
    if row.get("standard_component") is True:
        return "standard_products"
    if row.get("is_billet") is True:
        return "materials"
    if row.get("has_children"):
        return "assembly_units"
    return "details"


def _group_key(row):
    return (
        str(row.get("source_file") or "").casefold(),
        str(row.get("designation") or ""),
        str(row.get("name") or ""),
        str(row.get("material") or ""),
    )


def group_rows(rows):
    """Group eligible occurrences into GOST sections with derived positions."""
    eligible = [row for row in rows if row.get("specification_eligible")]
    buckets = OrderedDict((key, OrderedDict()) for key in SECTION_ORDER)
    unclassified = []

    for row in eligible:
        section = classify(row)
        if section not in buckets:
            unclassified.append(row.get("selector_path"))
            continue
        key = _group_key(row)
        item = buckets[section].get(key)
        if item is None:
            item = {
                "designation": str(row.get("designation") or ""),
                "name": str(row.get("name") or ""),
                "source_file": str(row.get("source_file") or ""),
                "material": str(row.get("material") or ""),
                "standard_component": row.get("standard_component"),
                "unit_mass": row.get("mass"),
                "quantity": 0,
                "selector_paths": [],
            }
            buckets[section][key] = item
        item["quantity"] += 1
        item["selector_paths"].append(row.get("selector_path"))

    sections = []
    for key in SECTION_ORDER:
        items = []
        for position, item in enumerate(
            sorted(
                buckets[key].values(),
                key=lambda entry: (str(entry["designation"]), str(entry["name"])),
            ),
            start=1,
        ):
            entry = dict(item)
            entry["position"] = position
            entry["section"] = key
            unit_mass = entry.get("unit_mass")
            try:
                entry["total_mass"] = (
                    round(float(unit_mass) * int(entry["quantity"]), 6)
                    if unit_mass is not None
                    else None
                )
            except Exception:
                entry["total_mass"] = None
            items.append(entry)
        if not items:
            continue
        sections.append(
            {
                "key": key,
                "title": SECTION_TITLES[key],
                "item_count": len(items),
                "total_quantity": sum(int(item["quantity"]) for item in items),
                "items": items,
            }
        )

    return {
        "sections": sections,
        "section_count": len(sections),
        "item_count": sum(len(section["items"]) for section in sections),
        "total_quantity": sum(section["total_quantity"] for section in sections),
        "unclassified_count": len(unclassified),
        "unclassified_selector_paths": unclassified,
    }


def specification_read(session, max_depth=8):
    from bom_tools import bom_read

    depth = int(max_depth)
    if depth < 1 or depth > 15:
        raise ValueError("max_depth_must_be_between_1_and_15")

    data = bom_read(session, max_depth=depth)
    rows = mark_children(data.get("occurrences") or [])
    grouped = group_rows(rows)

    eligible = int(data.get("eligible_occurrence_count") or 0)
    derived_quantity = int(grouped["total_quantity"])
    cross_check_ok = derived_quantity == eligible

    return {
        "requested_action": {"max_depth": depth, "read_only": True},
        "active_document": str(session.active_path() or ""),
        "target": data.get("target") or data.get("active_document") or "",
        "source": "derived_from_native_assembly_tree",
        "occurrence_count": int(data.get("occurrence_count") or 0),
        "eligible_occurrence_count": eligible,
        "excluded_occurrence_count": int(data.get("excluded_occurrence_count") or 0),
        "section_count": grouped["section_count"],
        "item_count": grouped["item_count"],
        "total_quantity": derived_quantity,
        "sections": grouped["sections"],
        "native_specification_descriptions": data.get(
            "native_specification_descriptions"
        ),
        "verification": {
            "native_assembly_tree": "API7 IPart7.Parts recursion",
            "native_membership_switch": "IPart7.CreateSpcObjects",
            "native_exclusion_switch": "IPart7.InheritExclude",
            "native_standard_flag": "IPart7.Standard",
            "native_billet_flag": "IPart7.IsBillet",
            "quantity_is_exact_occurrence_count": True,
            "cross_check_derived_quantity_equals_eligible": cross_check_ok,
            "read_only": True,
        },
        "cross_check": {
            "derived_quantity": derived_quantity,
            "eligible_occurrence_count": eligible,
            "ok": cross_check_ok,
        },
        "warnings": (
            []
            if cross_check_ok
            else [
                "Derived quantity does not equal the eligible occurrence count; treat the section layout as unreliable and inspect the source tree."
            ]
        ),
        "limits": [
            "Section assignment is derived from native flags, not read from a specification document: Standard -> standard products, IsBillet -> materials, a component with children -> assembly units, otherwise details.",
            "Positions are numbered by the bridge inside each section; they are not native specification positions.",
            "A real .spw specification document is not read or created here - the native specification object model is not verified on any install the bridge was tested against.",
            "Documentation and Kits sections cannot be derived from the assembly tree and are therefore never filled.",
        ],
        "read_only": True,
        "saved": False,
        "save_required": False,
    }
