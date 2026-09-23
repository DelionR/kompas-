"""Каталог инструментов адаптера AutoCAD.

Устройство повторяет каталог моста КОМПАС, но живёт отдельно: адаптер
AutoCAD — самостоятельная поверхность со своим VERSION.json и своими
тестами, а не ветка в чужом дереве.

Здесь же лежит единственное место, где имя инструмента связывается с
действием в реестре воркера.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

TOOLS: List[Dict[str, Any]] = [
    {"name": "autocad_status",
     "description": "Report the AutoCAD connection state: which release is running, which ProgID was used, whether the operator has confirmed that release in the version matrix, which document is active and what its INSUNITS scale is. The adapter never launches AutoCAD - it only attaches to an instance the user already has open, so a missing instance is reported as a refusal rather than silently starting a second one. A document whose INSUNITS is 0 (unitless) is reported as a refusal too: without a scale, millimetres cannot be written into the drawing.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "release": {"type": "string",
                                     "description": "Release number, e.g. 23 for AutoCAD 2019."},
                         "profile": {"type": "string",
                                     "enum": ["plain", "electrical"],
                                     "description": "Which vertical the operator says is running. Not verified over COM."},
                         "version_matrix": {"type": "object",
                                            "description": "Operator-filled matrix of confirmed releases."}},
                     "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "autocad_version_check",
     "description": "Resolve an AutoCAD release to its COM ProgID and report the support level from the operator's version matrix. AutoCAD exposes AutoCAD.Application.<R> where R is the release number, not the year: 2019 is R23, 2020 is R23.1, 2021 is R24, 2024 is R24.3. A release outside that published table is refused instead of being guessed. The matrix is empty by default, so every release reports unverified - meaning not checked, not permitted - until the operator confirms it on a machine that actually has AutoCAD.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "release": {"type": "string",
                                     "description": "Release number or a version string such as '23.0s (LMS Tech)'."},
                         "version_matrix": {"type": "object"}},
                     "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "autocad_document_active",
     "description": "Read the active AutoCAD document: name, full path and INSUNITS. Nothing is modified and no dialog is touched.",
     "inputSchema": {"type": "object",
                     "properties": {"release": {"type": "string"}},
                     "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "autocad_units_read",
     "description": "Resolve the drawing scale from INSUNITS and return the factor that converts millimetres into drawing units. The panel domain works strictly in millimetres, while an AutoCAD drawing may be in inches, metres or unitless. INSUNITS 0 means unitless - not millimetres - and is refused: converting into a document with no agreed scale is impossible. An unknown unit code is never treated as millimetres.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "insunits": {"type": "integer",
                                      "description": "Value to interpret. Omitted means read it from the active document."},
                         "release": {"type": "string"}},
                     "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "autocad_layout_place",
     "description": "Translate a switchgear panel layout into AutoCAD block insertion points. The layout domain counts in millimetres with the origin at top-left and +y downwards; AutoCAD has the origin at bottom-left and +y upwards, so the Y coordinate is flipped about the plate height, not merely rescaled. This is why a plate with no known height is refused rather than estimated: flipping about an unknown height is impossible. Blocks are placed by their lower-left corner, which is a convention of this adapter rather than a property of AutoCAD - a block's base point is chosen when the block is created - so the convention is returned and marked. Elements that fall outside the plate are reported under outside instead of being silently clipped. Nothing is written to the drawing: this returns coordinates. Pure, no COM.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "plate": {"type": "object",
                                   "description": "Plate size: width_mm and height_mm. height_mm is required."},
                         "units": {"type": "object",
                                   "description": "Drawing units as returned by autocad_units_read: insunits, units_per_mm."},
                         "origin": {"type": "object",
                                    "description": "Insertion origin in drawing units: x, y. Defaults to 0,0."},
                         "elements": {"type": "array",
                                      "maxItems": 2000,
                                      "items": {"type": "object"},
                                      "description": "Layout elements: name, x_mm, y_mm, width_mm, height_mm, optional block and rotation_deg."}},
                     "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
]

ACTION_MAP: Dict[str, Tuple[str, Any]] = {
    "autocad_status": ("autocad.status", lambda a: a),
    "autocad_version_check": ("autocad.version_check", lambda a: a),
    "autocad_document_active": ("autocad.document_active", lambda a: a),
    "autocad_units_read": ("autocad.units_read", lambda a: a),
    "autocad_layout_place": ("autocad.layout_place", lambda a: a),
}

TOOL_INDEX: Dict[str, Dict[str, Any]] = {t["name"]: t for t in TOOLS}


def tool_names() -> List[str]:
    return [t["name"] for t in TOOLS]


def routing_table() -> Dict[str, str]:
    return {name: action for name, (action, _conv) in ACTION_MAP.items()}


def catalog_snapshot() -> Dict[str, Any]:
    return {"mcp_tool_count": len(TOOLS), "mcp_tools": tool_names()}
