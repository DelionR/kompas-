from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path

from assembly_insert_tools import _close, _norm, _open_exact, _sha256
from material_tools import _ensure_closed, _restore_exact_active


COLLECTIONS = (
    ("linear", "LineDimensions3D", "LineDimension3D"),
    ("radial", "RadialDimensions3D", "RadialDimension3D"),
    ("diametral", "DiametralDimensions3D", "DiametralDimension3D"),
    ("angular", "AngleDimensions3D", "AngleDimension3D"),
    ("arc_length", "ArcDimensions3D", "ArcDimension3D"),
)


def _scalar(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _value(obj, name, default=None):
    try:
        value = getattr(obj, name)
        value = value() if callable(value) else value
        converted = _scalar(value)
        return default if converted is None else converted
    except Exception:
        return default


def _object_ref(obj):
    if obj is None:
        return None
    return {
        "name": str(_value(obj, "Name", "") or ""),
        "type": _value(obj, "Type"),
        "model_object_type": _value(obj, "ModelObjectType"),
        "reference": _value(obj, "Reference"),
        "valid": _value(obj, "Valid"),
    }


def _position(dim):
    import win32com.client as wc

    try:
        common = wc.CastTo(dim, "IDimension3D")
        raw = common.GetTextPosition()
        if isinstance(raw, (tuple, list)):
            values = [float(value) for value in raw if isinstance(value, (int, float))]
            if len(values) >= 3:
                return values[-3:]
    except Exception:
        pass
    return None


def _text(dim):
    import win32com.client as wc

    try:
        text = wc.CastTo(dim, "IDimensionText")
    except Exception:
        return {
            "available": False,
            "nominal_value": None,
            "tolerance": None,
            "high_deviation": None,
            "low_deviation": None,
        }
    return {
        "available": True,
        "nominal_value": _value(text, "NominalValue"),
        "auto_nominal_value": _value(text, "AutoNominalValue"),
        "tolerance_on": _value(text, "ToleranceOn"),
        "tolerance": _value(text, "Tolerance"),
        "deviation_on": _value(text, "DeviationOn"),
        "has_tolerance": _value(text, "HasTolerance"),
        "high_deviation": _value(text, "HighDeviationValue"),
        "low_deviation": _value(text, "LowDeviationValue"),
        "accuracy": _value(text, "Accuracy"),
        "accuracy_decimals_count": _value(text, "AccuracyDecimalsCount"),
    }


def pmi_read(session, max_dimensions=500):
    import win32com.client as wc

    max_dimensions = int(max_dimensions)
    if max_dimensions < 1 or max_dimensions > 2000:
        raise ValueError("max_dimensions_must_be_between_1_and_2000")
    doc = session.active()
    active = str(session.active_path() or "")
    if doc is None or not active:
        raise RuntimeError("no_active_document_path")
    doc3 = wc.CastTo(doc, "IKompasDocument3D")
    top = wc.CastTo(doc3.TopPart, "IPart7")
    symbols = wc.CastTo(top, "ISymbols3DContainer")

    dimensions = []
    collection_counts = {}
    errors = []
    for kind, collection_name, getter_name in COLLECTIONS:
        try:
            collection = getattr(symbols, collection_name)
            count = int(collection.Count)
        except Exception as exc:
            collection_counts[collection_name] = None
            errors.append(
                collection_name + ":" + type(exc).__name__ + ":" + str(exc)
            )
            continue
        collection_counts[collection_name] = count
        getter = getattr(collection, getter_name)
        for index in range(count):
            if len(dimensions) >= max_dimensions:
                break
            try:
                dim = getter(index)
                row = {
                    "collection": collection_name,
                    "index": index,
                    "dimension_type": kind,
                    "name": str(_value(dim, "Name", "") or ""),
                    "type": _value(dim, "Type"),
                    "model_object_type": _value(dim, "ModelObjectType"),
                    "reference": _value(dim, "Reference"),
                    "valid": _value(dim, "Valid"),
                    "hidden": _value(dim, "Hidden"),
                    "value": _value(dim, "Length"),
                    "position": _position(dim),
                    "associated_geometry": [],
                    "text": _text(dim),
                }
                for object_name in ("Object1", "Object2"):
                    try:
                        linked = _object_ref(getattr(dim, object_name))
                    except Exception:
                        linked = None
                    if linked is not None:
                        linked["role"] = object_name.lower()
                        row["associated_geometry"].append(linked)
                row["geometry_linked"] = bool(row["associated_geometry"])
                dimensions.append(row)
            except Exception as exc:
                errors.append(
                    collection_name
                    + "["
                    + str(index)
                    + "]:"
                    + type(exc).__name__
                    + ":"
                    + str(exc)
                )

    return {
        "requested_action": {"max_dimensions": max_dimensions},
        "active_document": active,
        "target": active,
        "dimension_count": len(dimensions),
        "collection_counts": collection_counts,
        "dimensions": dimensions,
        "errors": errors,
        "truncated": sum(value or 0 for value in collection_counts.values())
        > len(dimensions),
        "verification": {
            "native_container": "API7 ISymbols3DContainer",
            "native_dimension_collections_read": True,
            "floating_model_texts_excluded": True,
            "geometry_links_reported_only_from_Object1_Object2": True,
            "read_only": True,
        },
        "warnings": [
            "Text/tolerance fields are null when the dimension does not expose IDimensionText on this KOMPAS object."
        ],
        "read_only": True,
        "save_required": False,
        "saved": False,
    }


def _point(value, label):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(label + "_must_have_exactly_3_values")
    result = []
    for item in value:
        try:
            number = float(item)
        except Exception as exc:
            raise ValueError(label + "_must_be_numeric") from exc
        if not math.isfinite(number):
            raise ValueError(label + "_must_be_finite")
        result.append(number)
    return result


def _objects(value):
    if value is None:
        return []
    if isinstance(value, (tuple, list)):
        result = []
        for item in value:
            result.extend(_objects(item))
        return result
    return [value]


def _vertex_at(top, point, tolerance):
    raw = top.FindObjectsByPointEx(
        point[0], point[1], point[2], True, tolerance
    )
    candidates = []
    for item in _objects(raw):
        row = _object_ref(item)
        if row is None:
            continue
        candidates.append((item, row))
    vertices = [item for item, row in candidates if row.get("model_object_type") == 8]
    if not vertices:
        raise LookupError(
            "no_native_vertex_at_point:"
            + repr({"point": point, "candidates": [row for _, row in candidates]})
        )
    return vertices[0], [row for _, row in candidates]


def _created_dimension_readback(symbols, name):
    collection = symbols.LineDimensions3D
    matches = []
    for index in range(int(collection.Count)):
        dim = collection.LineDimension3D(index)
        if str(_value(dim, "Name", "") or "") != name:
            continue
        row = {
            "collection": "LineDimensions3D",
            "index": index,
            "dimension_type": "linear",
            "name": name,
            "type": _value(dim, "Type"),
            "model_object_type": _value(dim, "ModelObjectType"),
            "reference": _value(dim, "Reference"),
            "valid": _value(dim, "Valid"),
            "hidden": _value(dim, "Hidden"),
            "value": _value(dim, "Length"),
            "position": _position(dim),
            "associated_geometry": [],
            "text": _text(dim),
        }
        for object_name in ("Object1", "Object2"):
            try:
                linked = _object_ref(getattr(dim, object_name))
            except Exception:
                linked = None
            if linked is not None:
                linked["role"] = object_name.lower()
                row["associated_geometry"].append(linked)
        row["geometry_linked"] = bool(row["associated_geometry"])
        matches.append(row)
    if len(matches) != 1:
        raise RuntimeError(
            "created_dimension_name_match_count_must_equal_one:"
            + repr({"name": name, "count": len(matches)})
        )
    return matches[0], int(collection.Count)


def pmi_dimension_create(
    session,
    filename,
    point1,
    point2,
    text_position,
    plane="XOY",
    name="CAP2_LINEAR_DIMENSION",
    point_tolerance=0.001,
):
    import win32com.client as wc

    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("filename_must_be_basename")
    if not filename.lower().endswith(".m3d"):
        raise ValueError("filename_must_end_with_m3d")
    if "_agent_copy" not in Path(filename).stem.lower():
        raise ValueError("filename_must_contain_AGENT_COPY")
    if plane != "XOY":
        raise ValueError("only_XOY_plane_is_verified_for_pmi_linear_create")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name_must_be_nonempty_string")
    name = name.strip()
    if len(name) > 80:
        raise ValueError("name_too_long")
    point1 = _point(point1, "point1")
    point2 = _point(point2, "point2")
    text_position = _point(text_position, "text_position")
    point_tolerance = float(point_tolerance)
    if not math.isfinite(point_tolerance) or not 0.0 < point_tolerance <= 1.0:
        raise ValueError("point_tolerance_must_be_in_0_to_1_mm")
    expected_nominal = math.dist(point1, point2)
    if expected_nominal <= point_tolerance:
        raise ValueError("dimension_points_must_be_distinct")

    original_doc = session.active()
    original_path = str(session.active_path() or "")
    if original_doc is None or not original_path:
        raise RuntimeError("no_active_document_path")
    original = Path(original_path).resolve()
    work = (Path(session.root).resolve() / "work").resolve()
    if original.parent != work or "_agent_copy" not in original.stem.lower():
        raise RuntimeError("active_document_not_safe_direct_work_AGENT_COPY")
    target = (work / filename).resolve()
    if target.parent != work or not target.is_file():
        raise FileNotFoundError(str(target))

    app = session.connect()
    _ensure_closed(app, session, target)
    backup_dir = (Path(session.root).resolve() / "_BACKUPS").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot = backup_dir / (
        "TXN_PMI_DIMENSION_CREATE_" + uuid.uuid4().hex + "_" + target.name
    )
    shutil.copy2(target, snapshot)
    before_hash = _sha256(target)
    target_doc = None
    reopened_doc = None
    saved = False

    try:
        target_doc = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("opened_pmi_target_not_exact_active")
        doc3 = wc.CastTo(target_doc, "IKompasDocument3D")
        top = wc.CastTo(doc3.TopPart, "IPart7")
        symbols = wc.CastTo(top, "ISymbols3DContainer")
        collection = symbols.LineDimensions3D
        before_count = int(collection.Count)
        for index in range(before_count):
            existing = collection.LineDimension3D(index)
            if str(_value(existing, "Name", "") or "") == name:
                raise ValueError("dimension_name_already_exists:" + name)

        object1, candidates1 = _vertex_at(top, point1, point_tolerance)
        object2, candidates2 = _vertex_at(top, point2, point_tolerance)
        if _value(object1, "Reference") == _value(object2, "Reference"):
            raise ValueError("dimension_vertices_must_be_distinct_native_objects")

        # ILineDimensions3D.Add expects ksObj3dTypeEnum, not a zero-based
        # variant index.  o3d_lineDimension3D=81 creates the plane-based
        # ILineDimension3D family; o3d_baselineDimension3D=80 is deliberately
        # not substituted because this operation promises two-vertex planar PMI.
        dim = collection.Add(81)
        if dim is None:
            raise RuntimeError("LineDimensions3D.Add_returned_none")
        base = wc.CastTo(dim, "IBaseLineDimension3D")
        line = wc.CastTo(dim, "ILineDimension3D")
        common = wc.CastTo(dim, "IDimension3D")
        base.Object1 = object1
        base.Object2 = object2
        line.Plane = top.DefaultObject(1)
        base.Name = name
        set_position_result = common.SetTextPosition(*text_position)
        update_result = base.Update()
        top_update_result = top.Update()

        before_save, after_write_count = _created_dimension_readback(symbols, name)
        if after_write_count != before_count + 1:
            raise RuntimeError("dimension_count_did_not_increase_by_one")
        if not before_save["geometry_linked"] or len(before_save["associated_geometry"]) != 2:
            raise RuntimeError("created_dimension_is_not_linked_to_two_native_objects")
        if before_save["valid"] is False:
            raise RuntimeError("created_dimension_valid_false_before_save")
        nominal = before_save["text"].get("nominal_value")
        if nominal is None or abs(float(nominal) - expected_nominal) > 0.01:
            raise RuntimeError(
                "created_dimension_nominal_mismatch_before_save:"
                + repr({"expected": expected_nominal, "actual": nominal})
            )

        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("active_document_changed_before_pmi_Save")
        save_result = target_doc.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True
        _close(target_doc)
        target_doc = None

        reopened_doc = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("reopened_pmi_target_not_exact_active")
        reopened3 = wc.CastTo(reopened_doc, "IKompasDocument3D")
        reopened_top = wc.CastTo(reopened3.TopPart, "IPart7")
        reopened_symbols = wc.CastTo(reopened_top, "ISymbols3DContainer")
        reopened, reopened_count = _created_dimension_readback(
            reopened_symbols, name
        )
        if reopened_count != before_count + 1:
            raise RuntimeError("dimension_count_not_persistent_after_reopen")
        if not reopened["geometry_linked"] or len(reopened["associated_geometry"]) != 2:
            raise RuntimeError("dimension_geometry_links_not_persistent_after_reopen")
        if reopened["valid"] is False:
            raise RuntimeError("created_dimension_valid_false_after_reopen")
        reopened_nominal = reopened["text"].get("nominal_value")
        if reopened_nominal is None or abs(float(reopened_nominal) - expected_nominal) > 0.01:
            raise RuntimeError(
                "created_dimension_nominal_mismatch_after_reopen:"
                + repr({"expected": expected_nominal, "actual": reopened_nominal})
            )

        _close(reopened_doc)
        reopened_doc = None
        restored = _restore_exact_active(
            app, session, original_path, original_doc
        )
        after_hash = _sha256(target)
        snapshot.unlink()
        return {
            "requested_action": {
                "filename": filename,
                "dimension_family": "linear_two_vertex_planar",
                "plane": plane,
                "point1": point1,
                "point2": point2,
                "text_position": text_position,
                "name": name,
            },
            "active_document": restored,
            "target": str(target),
            "before": {
                "dimension_count": before_count,
                "sha256": before_hash,
            },
            "after": {
                "dimension_count": reopened_count,
                "sha256": after_hash,
                "dimension": reopened,
            },
            "actual": reopened,
            "readback": reopened,
            "changed": before_hash != after_hash,
            "saved": saved,
            "save_required": False,
            "verification": {
                "native_collection": "API7 ISymbols3DContainer.LineDimensions3D",
                "native_dimension_type": "ILineDimension3D",
                "native_object1_object2": True,
                "geometry_linked": True,
                "two_distinct_vertex_references": True,
                "nominal_value_expected_mm": expected_nominal,
                "nominal_value_readback_mm": reopened_nominal,
                "save_close_reopen_verified": True,
                "exact_active_path_before_write": True,
                "exact_active_path_before_save": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
                "set_text_position_return": set_position_result,
                "dimension_update_return": update_result,
                "top_part_update_return": top_update_result,
            },
            "selection_diagnostics": {
                "point1_candidates": candidates1,
                "point2_candidates": candidates2,
            },
            "warnings": [
                "This verified writer is intentionally limited to two-vertex planar linear PMI on XOY.",
                "Coordinate/baseline, diameter, and radius write families are not claimed by this operation."
            ],
        }
    except Exception:
        for doc in (reopened_doc, target_doc):
            if doc is not None:
                try:
                    _close(doc)
                except Exception:
                    pass
        try:
            _restore_exact_active(app, session, original_path, original_doc)
        except Exception:
            pass
        try:
            if snapshot.is_file():
                shutil.copy2(snapshot, target)
        finally:
            try:
                snapshot.unlink()
            except FileNotFoundError:
                pass
        try:
            _restore_exact_active(app, session, original_path, original_doc)
        except Exception:
            pass
        raise
