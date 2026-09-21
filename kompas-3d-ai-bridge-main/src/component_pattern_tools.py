from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path

from assembly_insert_tools import _close, _norm, _open_exact, _sha256
from component_transform_tools import _placement
from material_tools import _ensure_closed, _restore_exact_active
from model_tools import find_component


AXES = {
    (1, 0, 0): (71, True, "OX"),
    (-1, 0, 0): (71, False, "OX"),
    (0, 1, 0): (72, True, "OY"),
    (0, -1, 0): (72, False, "OY"),
    (0, 0, 1): (73, True, "OZ"),
    (0, 0, -1): (73, False, "OZ"),
}


def _direction(value):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("direction_must_have_exactly_3_values")
    result = []
    for item in value:
        try:
            number = float(item)
        except Exception as exc:
            raise ValueError("direction_must_be_numeric") from exc
        if not math.isfinite(number):
            raise ValueError("direction_must_be_finite")
        result.append(number)
    rounded = tuple(int(round(item)) for item in result)
    if rounded not in AXES or any(abs(item - r) > 1e-9 for item, r in zip(result, rounded)):
        raise ValueError("verified_native_pattern_direction_must_be_signed_X_Y_or_Z_unit_axis")
    return result, AXES[rounded]


def _count_pair(raw):
    values = list(raw) if isinstance(raw, (tuple, list)) else [raw]
    if len(values) == 3 and isinstance(values[0], bool):
        if not values[0]:
            raise RuntimeError("GetExemplarsCounts_returned_false")
        values = values[1:]
    if len(values) != 2:
        raise RuntimeError("GetExemplarsCounts_unexpected_shape:" + repr(values))
    return [int(values[0]), int(values[1])]


def _model_ref(obj):
    def value(name, default=None):
        try:
            v = getattr(obj, name)
            return v() if callable(v) else v
        except Exception:
            return default
    return {
        "name": str(value("Name", "") or ""),
        "type": value("Type"),
        "model_object_type": value("ModelObjectType"),
        "reference": value("Reference"),
        "valid": value("Valid"),
    }


def _part_tree_snapshot(top):
    """Read concrete assembly occurrences, including native array exemplars."""
    rows = []

    def walk(parent, prefix):
        try:
            child_count = int(parent.Parts.Count)
        except Exception:
            child_count = 0
        for index in range(child_count):
            child = parent.Parts.Part(index)
            path = prefix + [index]
            rows.append({
                "tree_path": path,
                "name": str(getattr(child, "Name", "") or ""),
                "marking": str(getattr(child, "Marking", "") or ""),
                "file_name": str(getattr(child, "FileName", "") or ""),
                "placement": _placement(child),
            })
            walk(child, path)

    walk(top, [])
    return rows


def _source_occurrences(rows, source_file):
    source_norm = _norm(source_file)
    return [row for row in rows if _norm(row.get("file_name", "")) == source_norm]


def _pattern_readback(top, name):
    import win32com.client as wc

    container = wc.CastTo(top, "IModelContainer")
    collection = container.FeaturePatterns
    matches = []
    for index in range(int(collection.Count)):
        base = collection.FeaturePattern(index)
        if str(getattr(base, "Name", "") or "") == name:
            matches.append((index, wc.CastTo(base, "ILinearPattern")))
    if len(matches) != 1:
        raise RuntimeError(
            "linear_pattern_name_match_count_must_equal_one:"
            + repr({"name": name, "count": len(matches)})
        )
    index, pattern = matches[0]
    counts = _count_pair(pattern.GetExemplarsCounts())
    exemplars = []
    # IFeaturePattern.GetExemplar indexes are 1-based in the SDK contract.
    for i in range(1, counts[0] + 1):
        for j in range(1, counts[1] + 1):
            exemplar = pattern.Exemplar(i, j)
            row = _model_ref(exemplar)
            try:
                row["placement"] = _placement(wc.CastTo(exemplar, "IPart7"))
                row["file_name"] = str(getattr(wc.CastTo(exemplar, "IPart7"), "FileName", "") or "")
            except Exception as exc:
                row["placement_error"] = type(exc).__name__ + ":" + str(exc)
            row["index1"] = i
            row["index2"] = j
            exemplars.append(row)
    initial_objects = []
    try:
        raw = pattern.InitialObjects
        values = list(raw) if isinstance(raw, (tuple, list)) else [raw]
        initial_objects = [_model_ref(item) for item in values if item is not None]
    except Exception:
        pass
    return {
        "collection_index": index,
        "name": name,
        "type": getattr(pattern, "Type", None),
        "model_object_type": getattr(pattern, "ModelObjectType", None),
        "reference": getattr(pattern, "Reference", None),
        "valid": getattr(pattern, "Valid", None),
        "step1": float(pattern.Step1),
        "count1": int(pattern.Count1),
        "direction1": bool(pattern.Direction1),
        "count2": int(pattern.Count2),
        "building_type": int(pattern.BuildingType),
        "exemplar_counts": counts,
        "exemplars": exemplars,
        "initial_objects": initial_objects,
        "feature_pattern_collection_count": int(collection.Count),
    }


def component_pattern_linear(
    session,
    assembly_filename,
    source_selector,
    direction,
    spacing,
    count,
    name="CAP2_LINEAR_COMPONENT_PATTERN",
):
    direction, (axis_type, positive, axis_name) = _direction(direction)
    try:
        spacing = float(spacing)
    except Exception as exc:
        raise ValueError("spacing_must_be_number") from exc
    if not math.isfinite(spacing) or spacing <= 0.0:
        raise ValueError("spacing_must_be_positive_finite")
    count = int(count)
    if count < 2 or count > 20:
        raise ValueError("count_must_be_between_2_and_20")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("name_must_be_nonempty_at_most_100_chars")
    name = name.strip()
    if not isinstance(assembly_filename, str) or Path(assembly_filename).name != assembly_filename:
        raise ValueError("assembly_filename_must_be_basename")
    if not assembly_filename.lower().endswith(".a3d") or "_agent_copy" not in Path(assembly_filename).stem.lower():
        raise ValueError("assembly_filename_must_be_AGENT_COPY_a3d")

    original_doc = session.active()
    original_path = str(session.active_path() or "")
    if original_doc is None or not original_path:
        raise RuntimeError("no_active_document_path")
    original = Path(original_path).resolve()
    work = (Path(session.root).resolve() / "work").resolve()
    if original.parent != work or "_agent_copy" not in original.stem.lower():
        raise RuntimeError("active_document_not_safe_direct_work_AGENT_COPY")
    target = (work / assembly_filename).resolve()
    if target.parent != work or not target.is_file():
        raise FileNotFoundError(str(target))

    app = session.connect()
    _ensure_closed(app, session, target)
    backup_dir = (Path(session.root).resolve() / "_BACKUPS").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot = backup_dir / (
        "TXN_COMPONENT_PATTERN_LINEAR_" + uuid.uuid4().hex + "_" + target.name
    )
    shutil.copy2(target, snapshot)
    before_hash = _sha256(target)
    opened = None
    reopened_doc = None
    saved = False
    try:
        opened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("opened_pattern_target_not_exact_active")
        import win32com.client as wc

        doc3 = wc.CastTo(opened, "IKompasDocument3D")
        top = wc.CastTo(doc3.TopPart, "IPart7")
        source, source_path = find_component(session, source_selector)
        source_file = str(getattr(source, "FileName", "") or "")
        source_placement = _placement(source)
        before_part_tree = _part_tree_snapshot(top)
        before_source_occurrences = _source_occurrences(before_part_tree, source_file)
        source_hash_before = _sha256(Path(source_file)) if source_file and Path(source_file).is_file() else None
        container = wc.CastTo(top, "IModelContainer")
        patterns = container.FeaturePatterns
        before_pattern_count = int(patterns.Count)
        pattern_base = patterns.Add(39)  # ksObj3dTypeEnum.o3d_meshPartArray
        if pattern_base is None:
            raise RuntimeError("FeaturePatterns.Add(o3d_meshPartArray)_returned_none")
        pattern = wc.CastTo(pattern_base, "ILinearPattern")
        pattern.Name = name
        suitable = pattern.IsSuitableObject(source)
        add_initial_result = pattern.AddInitialObjects((source,))
        pattern.Axis1 = top.DefaultObject(axis_type)
        pattern.Direction1 = positive
        pattern.Step1 = spacing
        pattern.Count1 = count
        pattern.Count2 = 1
        pattern.BuildingType = 0
        update_result = pattern.Update()
        top_update_result = top.Update()
        before_save = _pattern_readback(top, name)
        if before_save["valid"] is False:
            raise RuntimeError("native_linear_pattern_valid_false_before_save")
        if before_save["exemplar_counts"] != [count, 1]:
            raise RuntimeError("native_linear_pattern_count_mismatch_before_save")
        if before_save["feature_pattern_collection_count"] != before_pattern_count + 1:
            raise RuntimeError("feature_pattern_collection_count_did_not_increase")
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("active_document_changed_before_pattern_Save")
        save_result = opened.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True
        _close(opened)
        opened = None

        reopened_doc = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("reopened_pattern_target_not_exact_active")
        reopened3 = wc.CastTo(reopened_doc, "IKompasDocument3D")
        reopened_top = wc.CastTo(reopened3.TopPart, "IPart7")
        after = _pattern_readback(reopened_top, name)
        if after["valid"] is False or after["exemplar_counts"] != [count, 1]:
            raise RuntimeError("native_linear_pattern_not_persistent_after_reopen")
        if abs(after["step1"] - spacing) > 1e-6 or after["count1"] != count:
            raise RuntimeError("native_linear_pattern_parameters_not_persistent")
        after_part_tree = _part_tree_snapshot(reopened_top)
        after_source_occurrences = _source_occurrences(after_part_tree, source_file)
        expected_occurrence_count = len(before_source_occurrences) + count - 1
        if len(after_source_occurrences) != expected_occurrence_count:
            raise RuntimeError(
                "pattern_concrete_occurrence_count_mismatch:"
                + repr({
                    "actual": len(after_source_occurrences),
                    "expected": expected_occurrence_count,
                    "part_tree": after_part_tree,
                })
            )
        origins = [row["placement"]["origin"] for row in after_source_occurrences]
        expected_origins = []
        for index in range(count):
            expected_origins.append([
                source_placement["origin"][axis] + direction[axis] * spacing * index
                for axis in range(3)
            ])
        sort_key = lambda row: sum(row[axis] * direction[axis] for axis in range(3))
        sorted_origins = sorted(origins, key=sort_key)
        sorted_expected = sorted(expected_origins, key=sort_key)
        if any(
            any(abs(a - e) > 1e-5 for a, e in zip(actual, expected))
            for actual, expected in zip(sorted_origins, sorted_expected)
        ):
            raise RuntimeError(
                "pattern_exemplar_origins_mismatch:"
                + repr({"actual": sorted_origins, "expected": sorted_expected})
            )
        after["concrete_source_occurrences"] = after_source_occurrences
        after["concrete_part_tree"] = after_part_tree

        _close(reopened_doc)
        reopened_doc = None
        restored = _restore_exact_active(app, session, original_path, original_doc)
        after_hash = _sha256(target)
        source_hash_after = _sha256(Path(source_file)) if source_file and Path(source_file).is_file() else None
        if source_hash_before != source_hash_after:
            raise RuntimeError("pattern_source_part_bytes_changed")
        snapshot.unlink()
        return {
            "requested_action": {
                "assembly_filename": assembly_filename,
                "source_selector": source_selector,
                "direction": direction,
                "spacing": spacing,
                "count": count,
                "name": name,
            },
            "active_document": restored,
            "target": str(target),
            "before": {
                "assembly_sha256": before_hash,
                "feature_pattern_count": before_pattern_count,
                "source_selector_path": source_path,
                "source_file": source_file,
                "source_placement": source_placement,
                "part_tree": before_part_tree,
                "source_occurrences": before_source_occurrences,
            },
            "after": {"assembly_sha256": after_hash, "pattern": after},
            "actual": after,
            "readback": after,
            "changed": before_hash != after_hash,
            "saved": saved,
            "save_required": False,
            "verification": {
                "native_feature": "API7 IFeaturePatterns.Add(ksObjectPartsLinearPattern) / ILinearPattern",
                "axis": axis_name,
                "signed_direction": direction,
                "source_is_suitable_return": suitable,
                "add_initial_objects_return": add_initial_result,
                "pattern_update_return": update_result,
                "top_part_update_return": top_update_result,
                "exemplar_count_pass": True,
                "placement_origins_pass": True,
                "expected_origins": expected_origins,
                "actual_origins": origins,
                "concrete_occurrence_count_before": len(before_source_occurrences),
                "concrete_occurrence_count_after": len(after_source_occurrences),
                "source_reference": source_file,
                "source_sha256_before": source_hash_before,
                "source_sha256_after": source_hash_after,
                "source_unchanged": source_hash_before == source_hash_after,
                "save_close_reopen_verified": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
            },
            "warnings": [
                "Verified direction support is the six signed global coordinate axes only.",
                "pattern_by_points is not implemented; it is non-blocking under the CAP2 brief."
            ],
        }
    except Exception:
        for doc in (reopened_doc, opened):
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
