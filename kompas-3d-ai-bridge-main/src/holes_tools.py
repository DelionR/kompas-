
from __future__ import annotations

import math
from pathlib import Path

import win32com.client as wc

from topology_tools import component_topology


MAX_NATIVE_HOLES = 512


def _cast(obj, interface_name):
    try:
        return wc.CastTo(obj, interface_name), None
    except Exception as exc:
        return None, f"{type(exc).__name__}:{exc}"


def _safe_scalar(obj, names):
    errors = []
    for name in names:
        try:
            member = getattr(obj, name)
            value = member() if callable(member) else member
            if value is None or isinstance(value, (str, int, float, bool)):
                return value, name, errors
            return repr(value), name, errors
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}:{exc}")
    return None, None, errors


def _normalize_parts(value):
    if value is None:
        return []
    if isinstance(value, (tuple, list)):
        return list(value)
    return [value]


def _api7_part(session, selector):
    index = int(selector)
    doc = session.active()

    doc3, err = _cast(doc, "IKompasDocument3D")
    if doc3 is None:
        raise RuntimeError("CastTo_IKompasDocument3D_failed:" + str(err))

    top, err = _cast(doc3.TopPart, "IPart7")
    if top is None:
        raise RuntimeError("CastTo_TopPart_IPart7_failed:" + str(err))

    parts = _normalize_parts(top.PartsEx(0))
    if index < 0 or index >= len(parts):
        raise LookupError(f"selector_out_of_range:{index}/{len(parts)}")

    part, err = _cast(parts[index], "IPart7")
    if part is None:
        raise RuntimeError("CastTo_component_IPart7_failed:" + str(err))

    return part, len(parts)


def _enumerate_native_holes(holes):
    diagnostics = {
        "strategy": None,
        "iterator_error": None,
        "terminal_index_error": None,
        "hard_limit": MAX_NATIVE_HOLES,
    }

    try:
        values = list(holes)
        diagnostics["strategy"] = "python_iter_IEnumVARIANT"
        return values, diagnostics
    except Exception as exc:
        diagnostics["iterator_error"] = f"{type(exc).__name__}:{exc}"

    getter = None
    getter_name = None
    for name in ("Hole3D", "GetHole3D"):
        try:
            member = getattr(holes, name)
            if callable(member):
                getter = member
                getter_name = name
                break
        except Exception:
            pass

    if getter is None:
        raise RuntimeError("IHoles3D_has_no_enumeration_accessor")

    diagnostics["strategy"] = "Hole3D_indexed_until_missing"
    diagnostics["index_getter"] = getter_name

    values = []
    for i in range(MAX_NATIVE_HOLES):
        try:
            value = getter(i)
        except Exception as exc:
            diagnostics["terminal_index_error"] = (
                f"index={i}:{type(exc).__name__}:{exc}"
            )
            break
        if value is None:
            diagnostics["terminal_index_error"] = f"index={i}:returned_none"
            break
        values.append(value)
    else:
        raise RuntimeError(
            f"native_hole_scan_reached_hard_limit:{MAX_NATIVE_HOLES}"
        )

    return values, diagnostics


def _depth_type_label(value):
    try:
        value = int(value)
    except Exception:
        return None, "unknown"

    if value == 0:
        return "ksDTValue", "blind"
    if value == 1:
        return "ksDTReachThrough", "through"
    if value == 2:
        return "ksDTObject", "to_object"
    return f"unknown_{value}", "unknown"


def _norm3(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        vec = [float(x) for x in value]
    except Exception:
        return None
    mag = math.sqrt(sum(x * x for x in vec))
    if mag <= 1e-12:
        return None
    return [x / mag for x in vec]


def _midpoint(origin, axis, height):
    n = _norm3(axis)
    if n is None:
        return None
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        return None
    try:
        o = [float(x) for x in origin]
        h = float(height)
    except Exception:
        return None
    return [o[i] + n[i] * h * 0.5 for i in range(3)]


def _bbox_axis_span(bbox, axis):
    n = _norm3(axis)
    if n is None:
        return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 6:
        return None
    try:
        xmin, ymin, zmin, xmax, ymax, zmax = [float(x) for x in bbox]
    except Exception:
        return None

    values = []
    for x in (xmin, xmax):
        for y in (ymin, ymax):
            for z in (zmin, zmax):
                values.append(x * n[0] + y * n[1] + z * n[2])
    return max(values) - min(values)


def _infer_through_blind(cylinder, body_bbox):
    try:
        height = abs(float(cylinder.get("height")))
    except Exception:
        return "unknown", {
            "method": "bbox_projection_vs_cylinder_height",
            "reason": "cylinder_height_unavailable",
        }

    span = _bbox_axis_span(body_bbox, cylinder.get("axis_vector"))
    if span is None or span <= 1e-9:
        return "unknown", {
            "method": "bbox_projection_vs_cylinder_height",
            "reason": "body_axis_span_unavailable",
        }

    tolerance = max(1e-4, span * 1e-5)

    if abs(height - span) <= tolerance:
        return "through", {
            "method": "bbox_projection_vs_cylinder_height",
            "body_axis_span": span,
            "cylinder_height": height,
            "tolerance": tolerance,
            "confidence": "medium",
        }

    if height < span - tolerance:
        return "blind_candidate", {
            "method": "bbox_projection_vs_cylinder_height",
            "body_axis_span": span,
            "cylinder_height": height,
            "tolerance": tolerance,
            "confidence": "low",
        }

    return "unknown", {
        "method": "bbox_projection_vs_cylinder_height",
        "body_axis_span": span,
        "cylinder_height": height,
        "tolerance": tolerance,
    }


def _read_native(session, selector):
    result = {
        "status": "unknown",
        "holes": [],
        "warnings": [],
        "enumeration": None,
    }

    part, part_count = _api7_part(session, selector)
    result["api7_top_level_part_count"] = part_count

    container, err = _cast(part, "IModelContainer")
    if container is None:
        result["status"] = "unavailable"
        result["warnings"].append("IModelContainer cast failed:" + str(err))
        return result

    try:
        holes = container.Holes3D
    except Exception as exc:
        result["status"] = "unavailable"
        result["warnings"].append(
            f"Holes3D property failed:{type(exc).__name__}:{exc}"
        )
        return result

    if holes is None:
        result["status"] = "empty_or_not_present"
        return result

    try:
        values, diagnostics = _enumerate_native_holes(holes)
        result["enumeration"] = diagnostics
    except Exception as exc:
        result["status"] = "collection_present_but_not_enumerable"
        result["warnings"].append(
            f"native hole enumeration failed:{type(exc).__name__}:{exc}"
        )
        return result

    for index, raw in enumerate(values):
        hole, err = _cast(raw, "IHole3D")
        if hole is None:
            result["warnings"].append(f"hole[{index}] cast failed:{err}")
            continue

        row = {
            "index": index,
            "detection": "native",
            "classification": "native_hole_feature",
        }

        props = {
            "name": ("Name", "GetName"),
            "diameter": ("Diameter", "GetDiameter"),
            "depth": ("Depth", "GetDepth"),
            "depth_type": ("DepthType", "GetDepthType"),
            "hole_type": ("HoleType", "GetHoleType"),
            "axis_created": ("Axis", "GetAxis"),
            "show_thread": ("ShowThread", "GetShowThread"),
            "thread": ("Thread", "GetThread"),
            "end_face_type": ("EndFaceType", "GetEndFaceType"),
            "end_face_angle": ("EndFaceAngle", "GetEndFaceAngle"),
        }

        for out_name, names in props.items():
            value, source, _errors = _safe_scalar(hole, names)
            row[out_name] = value
            if source is not None:
                row[out_name + "_source"] = source

        label, through_blind = _depth_type_label(row.get("depth_type"))
        row["depth_type_label"] = label
        row["through_blind"] = through_blind
        row["through_blind_source"] = "native_depth_type"

        row["center_xyz"] = None
        row["axis_origin"] = None
        row["axis_vector"] = None
        row["geometry_association"] = "pending_topology_match"

        result["holes"].append(row)

    result["status"] = "read"
    return result


def _diameter_match(a, b):
    try:
        a = abs(float(a))
        b = abs(float(b))
    except Exception:
        return False
    tolerance = max(1e-4, max(a, b) * 1e-6)
    return abs(a - b) <= tolerance


def component_holes(session, selector, include_inferred=True):
    topology = component_topology(
        session,
        selector,
        max_faces=5000,
        max_edges=10000,
        max_vertices=10000,
    )

    cylinders = list(topology.get("cylindrical_faces") or [])
    bodies = {
        int(row["index"]): row
        for row in (topology.get("bodies") or [])
        if row.get("index") is not None
    }

    native = _read_native(session, selector)

    result = {
        "requested_action": {
            "selector": int(selector),
            "include_inferred": bool(include_inferred),
        },
        "active_document": topology.get("active_document"),
        "target": topology.get("target"),
        "holes": [],
        "hole_count": 0,
        "native_hole_count": 0,
        "inferred_hole_count": 0,
        "native_api": {
            "status": native.get("status"),
            "enumeration": native.get("enumeration"),
            "warnings": native.get("warnings") or [],
        },
        "warnings": list(topology.get("warnings") or []),
        "read_only": True,
        "save_required": False,
        "saved": False,
        "model_geometry_changed": False,
    }
    result["warnings"].extend(native.get("warnings") or [])

    matched_cylinders = set()

    for native_hole in native.get("holes") or []:
        row = dict(native_hole)

        matches = [
            (index, cylinder)
            for index, cylinder in enumerate(cylinders)
            if _diameter_match(
                row.get("diameter"),
                cylinder.get("diameter"),
            )
        ]

        if len(matches) == 1:
            cylinder_index, cylinder = matches[0]
            matched_cylinders.add(cylinder_index)

            row["body_index"] = cylinder.get("body_index")
            row["source_face_index"] = cylinder.get("face_index")
            row["axis_origin"] = cylinder.get("axis_origin")
            row["axis_vector"] = cylinder.get("axis_vector")
            row["center_xyz"] = _midpoint(
                cylinder.get("axis_origin"),
                cylinder.get("axis_vector"),
                cylinder.get("height"),
            )
            row["geometry_association"] = "unique_diameter_topology_match"

            if row.get("depth") is None:
                row["depth"] = cylinder.get("height")
                row["depth_source"] = "matched_cylinder_height"
        elif len(matches) > 1:
            row["geometry_association"] = "ambiguous_diameter_topology_match"
            row["geometry_match_count"] = len(matches)
        else:
            row["geometry_association"] = "no_topology_match"

        result["holes"].append(row)

    result["native_hole_count"] = len(result["holes"])

    if include_inferred:
        for cylinder_index, cylinder in enumerate(cylinders):
            if cylinder_index in matched_cylinders:
                continue

            body_index = cylinder.get("body_index")
            body_bbox = None
            if body_index is not None:
                body_bbox = (bodies.get(int(body_index)) or {}).get("bbox")

            through_blind, inference = _infer_through_blind(
                cylinder,
                body_bbox,
            )

            result["holes"].append({
                "index": len(result["holes"]),
                "detection": "inferred",
                "classification": "cylindrical_void_candidate",
                "confidence": "medium" if through_blind == "through" else "low",
                "component_selector": int(selector),
                "body_index": body_index,
                "source_face_index": cylinder.get("face_index"),
                "diameter": cylinder.get("diameter"),
                "radius": cylinder.get("radius"),
                "depth": cylinder.get("height"),
                "depth_source": "cylindrical_face_height",
                "axis_origin": cylinder.get("axis_origin"),
                "axis_vector": cylinder.get("axis_vector"),
                "center_xyz": _midpoint(
                    cylinder.get("axis_origin"),
                    cylinder.get("axis_vector"),
                    cylinder.get("height"),
                ),
                "through_blind": through_blind,
                "through_blind_source": inference,
                "native_hole_feature": False,
                "warning": (
                    "Inferred from a cylindrical face. This API path does not "
                    "prove that the cylinder is an internal void."
                ),
            })

    result["inferred_hole_count"] = sum(
        1
        for row in result["holes"]
        if row.get("detection") == "inferred"
    )
    result["hole_count"] = len(result["holes"])

    verification = topology.get("verification") or {}
    result["verification"] = {
        "topology_read": True,
        "direct_face_count": (topology.get("topology") or {}).get(
            "face_count",
            0,
        ),
        "native_api_attempted": True,
        "native_api_status": native.get("status"),
        "native_feature_path_exercised": result["native_hole_count"] > 0,
        "cylindrical_face_count": len(cylinders),
        "inferred_path_exercised": result["inferred_hole_count"] > 0,
        "topology_face_errors": verification.get("face_errors", 0),
        "zero_face_component_valid": verification.get(
            "zero_face_component_valid",
            False,
        ),
        "api5_api7_same_document": verification.get(
            "api5_api7_same_document",
            False,
        ),
    }

    return result
