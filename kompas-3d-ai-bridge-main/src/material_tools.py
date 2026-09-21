from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path

from assembly_insert_tools import _activate, _close, _norm, _open_exact, _sha256


TOL = 1e-9


def _relative_target(session, relative_path, write=False):
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path_must_be_nonempty_string")
    raw = relative_path.strip().replace("/", "\\")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("relative_path_must_not_be_absolute")
    work = (Path(session.root).resolve() / "work").resolve()
    target = (work / candidate).resolve()
    if not target.is_relative_to(work):
        raise RuntimeError("relative_path_escaped_approved_work")
    if target.suffix.lower() != ".m3d":
        raise ValueError("material_target_must_end_with_m3d")
    if not target.is_file():
        raise FileNotFoundError(str(target))
    if write:
        if target.parent != work:
            raise RuntimeError("write_target_must_be_directly_in_approved_work")
        if "_agent_copy" not in target.stem.lower():
            raise ValueError("write_target_must_contain_AGENT_COPY")
    return target


def _detail(doc):
    import win32com.client as wc

    d3 = wc.CastTo(doc, "IKompasDocument3D")
    top = d3.TopPart
    if top is None:
        raise RuntimeError("IKompasDocument3D.TopPart_returned_none")
    return d3, top


def _number(value):
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _read_top(top, path):
    return {
        "file_name": str(path),
        "name": str(getattr(top, "Name", "") or ""),
        "marking": str(getattr(top, "Marking", "") or ""),
        "material": str(getattr(top, "Material", "") or ""),
        "density": _number(getattr(top, "Density", None)),
        "mass": _number(getattr(top, "Mass", None)),
    }


def _ensure_closed(app, session, target):
    for index in range(int(app.Documents.Count)):
        doc = app.Documents.Item(index)
        path = str(session.document_path(doc) or "")
        if path and _norm(path) == _norm(target):
            raise RuntimeError("target_document_must_be_closed:" + path)


def _restore_exact_active(app, session, original_path, original_doc):
    """Restore the prior document and verify its exact path.

    KOMPAS can post a delayed activation when another document is closed.  Resolve
    the still-open document by path and pump pending COM messages before accepting
    the restoration instead of trusting the return from Activate alone.
    """
    import pythoncom

    candidates = [original_doc]
    for index in range(int(app.Documents.Count)):
        doc = app.Documents.Item(index)
        path = str(session.document_path(doc) or "")
        if path and _norm(path) == _norm(original_path):
            candidates.append(doc)
            break
    errors = []
    for doc in candidates:
        for _ in range(3):
            try:
                _activate(doc)
                pythoncom.PumpWaitingMessages()
                restored = str(session.active_path() or "")
                if _norm(restored) == _norm(original_path):
                    return restored
                errors.append("active_after_activate:" + restored)
            except Exception as exc:
                errors.append(type(exc).__name__ + ":" + str(exc))
    raise RuntimeError(
        "original_active_document_not_restored:"
        + str(session.active_path() or "")
        + " | "
        + " | ".join(errors)
    )


def _read_file(session, target):
    app = session.connect()
    original_doc = session.active()
    original_path = str(session.active_path() or "")
    if original_doc is None or not original_path:
        raise RuntimeError("no_active_document_path")
    _ensure_closed(app, session, target)
    opened = None
    try:
        opened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("opened_material_document_not_exact_active")
        _, top = _detail(opened)
        result = _read_top(top, target)
        _close(opened)
        opened = None
        _restore_exact_active(app, session, original_path, original_doc)
        return result
    finally:
        if opened is not None:
            try:
                _close(opened)
            except Exception:
                pass
        try:
            _restore_exact_active(app, session, original_path, original_doc)
        except Exception:
            pass


def material_read(session, relative_path):
    target = _relative_target(session, relative_path, write=False)
    result = _read_file(session, target)
    return {
        "requested_action": {"relative_path": relative_path, "read_only": True},
        "active_document": str(session.active_path() or ""),
        "target": str(target),
        "material": result["material"],
        "density": result["density"],
        "mass": result["mass"],
        "actual": result,
        "readback": result,
        "warnings": [],
        "read_only": True,
        "save_required": False,
        "saved": False,
    }


def material_set(
    session,
    filename,
    material_name,
    density,
    evidence_source_relative_path,
):
    target = _relative_target(session, filename, write=True)
    if not isinstance(material_name, str) or not material_name.strip():
        raise ValueError("material_name_must_be_nonempty_string")
    material_name = material_name.strip()
    try:
        density = float(density)
    except Exception as exc:
        raise ValueError("density_must_be_number") from exc
    if not math.isfinite(density) or density <= 0.0:
        raise ValueError("density_must_be_positive_finite")

    evidence_target = _relative_target(
        session, evidence_source_relative_path, write=False
    )
    evidence = _read_file(session, evidence_target)
    if evidence["material"] != material_name:
        raise ValueError(
            "requested_material_does_not_match_native_evidence_source:"
            + repr({"requested": material_name, "source": evidence["material"]})
        )
    if evidence["density"] is None or abs(evidence["density"] - density) > TOL:
        raise ValueError(
            "requested_density_does_not_match_native_evidence_source:"
            + repr({"requested": density, "source": evidence["density"]})
        )

    original_path = str(session.active_path() or "")
    original = Path(original_path).resolve() if original_path else None
    if original is None or original.parent.name.lower() != "work" or "_agent_copy" not in original.stem.lower():
        raise RuntimeError("active_document_not_safe_AGENT_COPY:" + original_path)
    root = original.parent.parent.resolve()
    app = session.connect()
    original_doc = session.active()
    if original_doc is None:
        raise RuntimeError("no_active_document")
    _ensure_closed(app, session, target)

    backup_dir = (root / "_BACKUPS").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    txn_backup = backup_dir / (
        "TXN_MATERIAL_SET_" + uuid.uuid4().hex + "_" + target.name
    )
    shutil.copy2(target, txn_backup)
    before_hash = _sha256(target)
    opened = None
    reopened = None
    saved = False

    try:
        opened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("opened_material_target_not_exact_active")
        _, top = _detail(opened)
        before = _read_top(top, target)
        set_result = top.SetMaterial(material_name, density)
        update_result = top.Update()
        after_write = _read_top(top, target)
        if after_write["material"] != material_name:
            raise RuntimeError("material_name_readback_mismatch_before_save")
        if after_write["density"] is None or abs(after_write["density"] - density) > TOL:
            raise RuntimeError("material_density_readback_mismatch_before_save")
        active_before_save = str(session.active_path() or "")
        if _norm(active_before_save) != _norm(target):
            raise RuntimeError("active_document_changed_before_Save:" + active_before_save)
        save_result = opened.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True
        _close(opened)
        opened = None

        reopened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError("reopened_material_target_not_exact_active")
        _, reopened_top = _detail(reopened)
        after = _read_top(reopened_top, target)
        if after["material"] != material_name:
            raise RuntimeError(
                "material_name_readback_mismatch_after_reopen:"
                + repr({"requested": material_name, "actual": after["material"]})
            )
        if after["density"] is None or abs(after["density"] - density) > TOL:
            raise RuntimeError("material_density_readback_mismatch_after_reopen")
        if after["mass"] is None or after["mass"] < 0.0:
            raise RuntimeError("material_mass_readback_unavailable_after_reopen")
        _close(reopened)
        reopened = None
        restored = _restore_exact_active(app, session, original, original_doc)
        after_hash = _sha256(target)
        txn_backup.unlink()

        return {
            "requested_action": {
                "filename": filename,
                "material_name": material_name,
                "density": density,
                "evidence_source_relative_path": evidence_source_relative_path,
            },
            "active_document": restored,
            "target": str(target),
            "before": {**before, "sha256": before_hash},
            "after": {**after, "sha256": after_hash},
            "changed": before_hash != after_hash,
            "save_required": False,
            "saved": saved,
            "warnings": (
                ["IPart7.SetMaterial returned false; accepted only because native material and density readback persisted after reopen."]
                if set_result is False
                else []
            ),
            "verification": {
                "native_api": "API7 IPart7.SetMaterial(Name, Density)",
                "native_evidence_source_match": True,
                "exact_active_path_before_write": True,
                "exact_active_path_before_save": True,
                "material_readback_pass": True,
                "density_readback_pass": True,
                "mass_readback_available": True,
                "save_close_reopen_verified": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
                "set_material_return": set_result,
                "top_part_update_return": update_result,
            },
            "actual": after,
            "readback": after,
            "evidence_source": evidence,
        }

    except Exception:
        for doc in (reopened, opened):
            if doc is not None:
                try:
                    _close(doc)
                except Exception:
                    pass
        try:
            _restore_exact_active(app, session, original, original_doc)
        except Exception:
            pass
        try:
            if txn_backup.is_file():
                shutil.copy2(txn_backup, target)
                if _sha256(target) != before_hash:
                    raise RuntimeError("byte_exact_material_rollback_hash_mismatch")
        finally:
            if txn_backup.is_file() and _sha256(target) == before_hash:
                txn_backup.unlink()
        try:
            _restore_exact_active(app, session, original, original_doc)
        except Exception:
            pass
        raise
