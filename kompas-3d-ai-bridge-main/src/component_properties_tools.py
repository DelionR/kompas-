from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path

from assembly_insert_tools import _close, _norm, _open_exact, _sha256
from material_tools import _ensure_closed, _restore_exact_active
from model_tools import find_component


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


def _native_properties(part, selector_path):
    return {
        "selector_path": list(selector_path),
        "designation": str(_value(part, "Marking", "") or ""),
        "name": str(_value(part, "Name", "") or ""),
        "file_name": str(_value(part, "FileName", "") or ""),
        "material": str(_value(part, "Material", "") or ""),
        "mass": _number(_value(part, "Mass")),
        "density": _number(_value(part, "Density")),
        "standard_component": _value(part, "Standard"),
        "create_specification_objects": _value(part, "CreateSpcObjects"),
        "parts_group_number": _value(part, "PartsGroupNumber"),
        "fixed": _value(part, "Fixed"),
        "is_local": _value(part, "IsLocal"),
        "is_billet": _value(part, "IsBillet"),
        "read_only_state": _value(part, "ReadOnly"),
        "quantity": None,
        "role": None,
        "purchased_manufactured": None,
    }


def component_properties_read(session, selector):
    active = str(session.active_path() or "")
    if not active:
        raise RuntimeError("no_active_document_path")
    part, path = find_component(session, selector)
    actual = _native_properties(part, path)
    return {
        "requested_action": {"selector": selector, "read_only": True},
        "active_document": active,
        "target": actual["file_name"] or active,
        "actual": actual,
        "readback": actual,
        "field_availability": {
            "designation": "native IPart7.Marking",
            "name": "native IPart7.Name",
            "material": "native IPart7.Material",
            "mass": "native IPart7.Mass",
            "density": "native IPart7.Density",
            "standard_component": "native IPart7.Standard",
            "bom_inclusion_switch": "native IPart7.CreateSpcObjects",
            "quantity": "not exposed by the validated IPart7 occurrence interface",
            "role": "not exposed by the validated IPart7 occurrence interface",
            "purchased_manufactured": "not exposed; Standard is not promoted to a purchased/manufactured classification",
        },
        "read_only": True,
        "saved": False,
        "save_required": False,
        "warnings": [
            "Null quantity/role/purchased_manufactured values are deliberate; no unsupported semantic inference is made."
        ],
    }


def _text(value, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(label + "_must_be_string_or_null")
    value = value.strip()
    if not value:
        raise ValueError(label + "_must_be_nonempty_when_supplied")
    if len(value) > 200:
        raise ValueError(label + "_too_long")
    return value


def component_properties_set(
    session, assembly_filename, selector, designation=None, name=None
):
    designation = _text(designation, "designation")
    name = _text(name, "name")
    if designation is None and name is None:
        raise ValueError("at_least_one_of_designation_or_name_is_required")
    if not isinstance(assembly_filename, str) or Path(assembly_filename).name != assembly_filename:
        raise ValueError("assembly_filename_must_be_basename")
    if not assembly_filename.lower().endswith(".a3d"):
        raise ValueError("assembly_filename_must_end_with_a3d")
    if "_agent_copy" not in Path(assembly_filename).stem.lower():
        raise ValueError("assembly_filename_must_contain_AGENT_COPY")

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
        "TXN_COMPONENT_PROPERTIES_" + uuid.uuid4().hex + "_" + target.name
    )
    shutil.copy2(target, snapshot)
    before_hash = _sha256(target)
    opened = None
    reopened_doc = None
    saved = False
    try:
        opened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("opened_properties_target_not_exact_active")
        part, path = find_component(session, selector)
        before = _native_properties(part, path)
        source = Path(before["file_name"]).resolve() if before["file_name"] else None
        source_hash_before = _sha256(source) if source and source.is_file() else None

        if designation is not None:
            part.Marking = designation
        if name is not None:
            part.Name = name
        update_result = part.Update()
        after_write = _native_properties(part, path)
        if designation is not None and after_write["designation"] != designation:
            raise RuntimeError("designation_readback_mismatch_before_save")
        if name is not None and after_write["name"] != name:
            raise RuntimeError("name_readback_mismatch_before_save")
        if after_write["file_name"] != before["file_name"]:
            raise RuntimeError("component_source_reference_changed")
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("active_document_changed_before_properties_Save")
        save_result = opened.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True
        _close(opened)
        opened = None

        reopened_doc = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("reopened_properties_target_not_exact_active")
        reopened_part, reopened_path = find_component(session, selector)
        after = _native_properties(reopened_part, reopened_path)
        if designation is not None and after["designation"] != designation:
            raise RuntimeError("designation_not_persistent_after_reopen")
        if name is not None and after["name"] != name:
            raise RuntimeError("name_not_persistent_after_reopen")
        if after["file_name"] != before["file_name"]:
            raise RuntimeError("component_source_reference_changed_after_reopen")

        _close(reopened_doc)
        reopened_doc = None
        restored = _restore_exact_active(app, session, original_path, original_doc)
        after_hash = _sha256(target)
        source_hash_after = _sha256(source) if source and source.is_file() else None
        if source_hash_before != source_hash_after:
            raise RuntimeError("external_component_source_bytes_changed")
        snapshot.unlink()
        return {
            "requested_action": {
                "assembly_filename": assembly_filename,
                "selector": selector,
                "designation": designation,
                "name": name,
            },
            "active_document": restored,
            "target": str(target),
            "before": {"properties": before, "assembly_sha256": before_hash},
            "after": {"properties": after, "assembly_sha256": after_hash},
            "actual": after,
            "readback": after,
            "changed": before_hash != after_hash,
            "saved": saved,
            "save_required": False,
            "verification": {
                "native_interface": "API7 IPart7 occurrence",
                "designation_persisted": designation is None or after["designation"] == designation,
                "name_persisted": name is None or after["name"] == name,
                "material_mass_preserved_as_native_readback": True,
                "source_reference_preserved": True,
                "external_source_sha256_before": source_hash_before,
                "external_source_sha256_after": source_hash_after,
                "external_source_unchanged": source_hash_before == source_hash_after,
                "save_close_reopen_verified": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
                "part_update_return": update_result,
            },
            "warnings": [
                "Only native writable IPart7.Name and IPart7.Marking are accepted by this setter.",
                "Material and mass are readback fields here; material changes use kompas_material_set on a controlled source part.",
                "Quantity, role, and purchased/manufactured are not inferred or written."
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
