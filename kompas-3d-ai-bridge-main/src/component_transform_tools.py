from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path

from assembly_insert_tools import (
    _activate,
    _basename,
    _close,
    _doc3d,
    _norm,
    _open_exact,
    _sha256,
)
from model_tools import _component_row, find_component


TOL = 1e-6
READBACK_TOL = 1e-5


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
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


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _validate_axes(axis_x, axis_y, axis_z):
    axes = [axis_x, axis_y, axis_z]
    norms = [math.sqrt(_dot(axis, axis)) for axis in axes]
    if any(abs(norm - 1.0) > TOL for norm in norms):
        raise ValueError("rotation_axes_must_be_unit:no_scale")
    dots = {
        "xy": _dot(axis_x, axis_y),
        "xz": _dot(axis_x, axis_z),
        "yz": _dot(axis_y, axis_z),
    }
    if any(abs(value) > TOL for value in dots.values()):
        raise ValueError("rotation_axes_must_be_orthogonal:no_shear")
    determinant = _dot(axis_x, _cross(axis_y, axis_z))
    if abs(determinant - 1.0) > TOL:
        raise ValueError("rotation_determinant_must_equal_positive_one:no_reflection")
    return {"norms": norms, "dot_products": dots, "determinant": determinant}


def _out_vector(raw, label):
    values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    if len(values) == 4 and isinstance(values[0], bool):
        if not values[0]:
            raise RuntimeError(label + "_reported_false")
        values = values[1:]
    if len(values) != 3:
        raise RuntimeError(label + "_unexpected_shape:" + repr(values))
    return [float(value) for value in values]


def _placement(part):
    import win32com.client as wc

    place = wc.CastTo(part.Placement, "IPlacement3D")
    origin = _out_vector(place.GetOrigin(), "GetOrigin")
    axes = [
        _out_vector(place.GetVector(index), f"GetVector({index})")
        for index in range(3)
    ]
    matrix = [float(value) for value in list(place.GetMatrix3D())]
    if len(matrix) != 16 or not all(math.isfinite(value) for value in matrix):
        raise RuntimeError("GetMatrix3D_must_return_16_finite_values")
    return {
        "origin": origin,
        "axis_x": axes[0],
        "axis_y": axes[1],
        "axis_z": axes[2],
        "matrix": matrix,
    }


def _close_enough(actual, expected, tolerance=READBACK_TOL):
    return len(actual) == len(expected) and all(
        abs(float(a) - float(e)) <= tolerance
        for a, e in zip(actual, expected)
    )


def _placement_matches(actual, requested):
    keys = ("origin", "axis_x", "axis_y", "axis_z")
    return all(_close_enough(actual[key], requested[key]) for key in keys)


def component_transform(
    session,
    assembly_filename,
    selector,
    origin,
    axis_x,
    axis_y,
    axis_z,
    mode="absolute",
):
    assembly_filename = _basename(
        assembly_filename, "assembly_filename", (".a3d",)
    )
    if mode != "absolute":
        raise ValueError("only_absolute_transform_mode_is_supported")
    origin = _vector(origin, "origin")
    axis_x = _vector(axis_x, "axis_x")
    axis_y = _vector(axis_y, "axis_y")
    axis_z = _vector(axis_z, "axis_z")
    validation = _validate_axes(axis_x, axis_y, axis_z)
    requested = {
        "origin": origin,
        "axis_x": axis_x,
        "axis_y": axis_y,
        "axis_z": axis_z,
    }
    requested_matrix = (
        axis_x + [0.0]
        + axis_y + [0.0]
        + axis_z + [0.0]
        + origin + [1.0]
    )

    original_path = str(session.active_path() or "")
    if not original_path:
        raise RuntimeError("no_active_document_path")
    original = Path(original_path).resolve()
    if original.parent.name.lower() != "work" or "_agent_copy" not in original.stem.lower():
        raise RuntimeError("active_document_not_safe_AGENT_COPY:" + original_path)
    root = original.parent.parent.resolve()
    approved_work = (root / "work").resolve()
    if original.parent != approved_work:
        raise RuntimeError("active_document_not_directly_in_approved_work:" + original_path)

    assembly = (approved_work / assembly_filename).resolve()
    if assembly.parent != approved_work:
        raise RuntimeError("assembly_escaped_approved_work")
    if "_agent_copy" not in assembly.stem.lower():
        raise ValueError("assembly_filename_must_contain_AGENT_COPY")
    if not assembly.is_file():
        raise FileNotFoundError("assembly_not_found:" + str(assembly))

    app = session.connect()
    original_doc = session.active()
    if original_doc is None:
        raise RuntimeError("no_active_document")
    if _norm(session.document_path(original_doc) or "") != _norm(original):
        raise RuntimeError("active_document_changed_before_transaction")
    for index in range(int(app.Documents.Count)):
        open_doc = app.Documents.Item(index)
        open_path = str(session.document_path(open_doc) or "")
        if open_path and _norm(open_path) == _norm(assembly):
            raise RuntimeError("target_assembly_must_be_closed_before_transaction:" + open_path)

    backup_dir = (root / "_BACKUPS").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    txn_backup = backup_dir / (
        "TXN_COMPONENT_TRANSFORM_" + uuid.uuid4().hex + "_" + assembly.name
    )
    shutil.copy2(assembly, txn_backup)
    before_hash = _sha256(assembly)
    target_doc = None
    reopened_doc = None
    saved = False

    try:
        target_doc = _open_exact(app, assembly)
        if _norm(session.active_path() or "") != _norm(assembly):
            raise RuntimeError("opened_assembly_not_exact_active")
        _doc3d(target_doc)
        part, path = find_component(session, selector)
        before_row = _component_row(part, path[-1], len(path), path)
        before_placement = _placement(part)
        source_file = str(getattr(part, "FileName", "") or "")

        import win32com.client as wc

        place = wc.CastTo(part.Placement, "IPlacement3D")
        init_matrix_result = place.InitByMatrix3D(tuple(requested_matrix))
        update_result = part.UpdatePlacement(True)
        after_write = _placement(part)
        if (
            not _placement_matches(after_write, requested)
            or not _close_enough(after_write["matrix"], requested_matrix)
        ):
            raise RuntimeError(
                "transform_readback_mismatch_before_save:"
                + repr({"requested": requested, "actual": after_write})
            )
        after_write_row = _component_row(part, path[-1], len(path), path)

        active_before_save = str(session.active_path() or "")
        if _norm(active_before_save) != _norm(assembly):
            raise RuntimeError("active_document_changed_before_Save:" + active_before_save)
        save_result = target_doc.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True
        _close(target_doc)
        target_doc = None

        reopened_doc = _open_exact(app, assembly)
        if _norm(session.active_path() or "") != _norm(assembly):
            raise RuntimeError("reopened_assembly_not_exact_active")
        _doc3d(reopened_doc)
        reopened_part, reopened_path = find_component(session, selector)
        reopened_placement = _placement(reopened_part)
        if (
            not _placement_matches(reopened_placement, requested)
            or not _close_enough(reopened_placement["matrix"], requested_matrix)
        ):
            raise RuntimeError(
                "transform_readback_mismatch_after_reopen:"
                + repr({"requested": requested, "actual": reopened_placement})
            )
        reopened_source = str(getattr(reopened_part, "FileName", "") or "")
        if source_file and _norm(reopened_source) != _norm(source_file):
            raise RuntimeError("component_source_reference_changed_during_transform")
        reopened_row = _component_row(
            reopened_part,
            reopened_path[-1],
            len(reopened_path),
            reopened_path,
        )

        _close(reopened_doc)
        reopened_doc = None
        _activate(original_doc)
        restored = str(session.active_path() or "")
        if _norm(restored) != _norm(original):
            raise RuntimeError("original_active_document_not_restored:" + restored)
        after_hash = _sha256(assembly)
        txn_backup.unlink()

        return {
            "requested_action": {
                "assembly_filename": assembly_filename,
                "selector": selector,
                "mode": mode,
                "transform": requested,
            },
            "active_document": restored,
            "target": str(assembly),
            "before": {
                "component": before_row,
                "placement": before_placement,
                "assembly_sha256": before_hash,
            },
            "after": {
                "component": reopened_row,
                "placement": reopened_placement,
                "assembly_sha256": after_hash,
            },
            "changed": before_hash != after_hash,
            "save_required": False,
            "saved": saved,
            "warnings": [
                message
                for condition, message in (
                    (
                        init_matrix_result is False,
                        "IPlacement3D.InitByMatrix3D returned false; accepted only because native origin/axes/matrix readback matched before Save and after reopen.",
                    ),
                    (
                        update_result is False,
                        "IPart7.UpdatePlacement returned false; accepted only because native origin/axes/matrix readback matched before Save and after reopen.",
                    ),
                )
                if condition
            ],
            "verification": {
                "finite_values": True,
                "orthonormal_axes": True,
                "determinant_positive_one": True,
                "no_scale": True,
                "no_shear": True,
                "no_reflection": True,
                "exact_component_selector": True,
                "exact_active_path_before_write": True,
                "exact_active_path_before_save": True,
                "origin_readback_pass": _close_enough(reopened_placement["origin"], origin),
                "orientation_readback_pass": all(
                    _close_enough(reopened_placement[key], requested[key])
                    for key in ("axis_x", "axis_y", "axis_z")
                ),
                "matrix_readback_pass": _close_enough(
                    reopened_placement["matrix"], requested_matrix
                ),
                "source_reference_preserved": True,
                "init_by_matrix_return": init_matrix_result,
                "update_placement_return": update_result,
                "save_close_reopen_verified": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
            },
            "actual": {
                "selector_path": reopened_path,
                "source_file": reopened_source,
                "origin": reopened_placement["origin"],
                "axis_x": reopened_placement["axis_x"],
                "axis_y": reopened_placement["axis_y"],
                "axis_z": reopened_placement["axis_z"],
                "matrix": reopened_placement["matrix"],
                "requested_matrix": requested_matrix,
                "bbox": reopened_row.get("bbox"),
            },
            "readback": reopened_placement,
            "validation": validation,
        }

    except Exception:
        for doc in (reopened_doc, target_doc):
            if doc is not None:
                try:
                    _close(doc)
                except Exception:
                    pass
        try:
            _activate(original_doc)
        except Exception:
            pass
        try:
            if txn_backup.is_file():
                shutil.copy2(txn_backup, assembly)
        finally:
            try:
                txn_backup.unlink()
            except FileNotFoundError:
                pass
        try:
            _activate(original_doc)
        except Exception:
            pass
        raise
