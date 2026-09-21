from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from model_tools import _component_row


def _basename(value, label, suffixes):
    if not isinstance(value, str):
        raise TypeError(label + "_must_be_string")
    raw = value.strip()
    if not raw:
        raise ValueError(label + "_is_empty")
    path = Path(raw)
    if path.name != raw:
        raise ValueError(label + "_must_be_basename_only")
    if path.suffix.lower() not in suffixes:
        raise ValueError(
            label + "_must_end_with_" + "_or_".join(
                suffix.lstrip(".") for suffix in suffixes
            )
        )
    return raw


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _activate(doc):
    errors = []
    for name in ("Activate", "SetActive"):
        try:
            member = getattr(doc, name)
            member() if callable(member) else member
            return name
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}:{exc}")
    try:
        doc.Active = True
        return "Active=True"
    except Exception as exc:
        errors.append(f"Active=True:{type(exc).__name__}:{exc}")
    raise RuntimeError("document_activation_failed:" + " | ".join(errors))


def _close(doc):
    errors = []
    for args in ((0,), ()):
        try:
            doc.Close(*args)
            return "Close" + repr(args)
        except Exception as exc:
            errors.append(f"Close{args}:{type(exc).__name__}:{exc}")
    raise RuntimeError("document_close_failed:" + " | ".join(errors))


def _doc3d(doc):
    import win32com.client as wc

    result = wc.CastTo(doc, "IKompasDocument3D")
    top = result.TopPart
    if top is None:
        raise RuntimeError("IKompasDocument3D.TopPart_returned_none")
    if bool(getattr(result, "IsDetail", False)):
        raise RuntimeError("target_document_is_detail_not_assembly")
    return result, top


def _rows(top):
    count = int(top.Parts.Count)
    return [
        _component_row(top.Parts.Part(index), index, 0, [index])
        for index in range(count)
    ]


def _reference_count(rows, source):
    expected = _norm(source)
    return sum(
        1
        for row in rows
        if row.get("file_name") and _norm(row["file_name"]) == expected
    )


def _open_exact(app, path):
    doc = app.Documents.Open(str(path), True, False)
    if doc is None:
        raise RuntimeError("Documents.Open_returned_none:" + str(path))
    return doc


def assembly_insert_component(
    session,
    assembly_filename,
    component_filename,
    allow_duplicate=False,
):
    """Insert one external M3D/A3D reference into a safe assembly.

    The bounded transaction uses API7 IParts7.AddFromFile with
    ExternalFile=True. It snapshots exact target bytes, refuses an already-open
    target and duplicate source references by default, validates the active
    PathName immediately before Save, then closes/reopens and verifies the
    persisted component count and exact FileName reference.
    """
    assembly_filename = _basename(
        assembly_filename, "assembly_filename", (".a3d",)
    )
    component_filename = _basename(
        component_filename, "component_filename", (".m3d", ".a3d")
    )
    allow_duplicate = bool(allow_duplicate)

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
    component = (approved_work / component_filename).resolve()
    if assembly.parent != approved_work or component.parent != approved_work:
        raise RuntimeError("target_or_source_escaped_approved_work")
    if "_agent_copy" not in assembly.stem.lower():
        raise ValueError("assembly_filename_must_contain_AGENT_COPY")
    if not assembly.is_file():
        raise FileNotFoundError("assembly_not_found:" + str(assembly))
    if not component.is_file():
        raise FileNotFoundError("component_not_found:" + str(component))
    if _norm(assembly) == _norm(component):
        raise ValueError("assembly_and_component_must_differ")

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
        "TXN_ASSEMBLY_INSERT_" + uuid.uuid4().hex + "_" + assembly.name
    )
    shutil.copy2(assembly, txn_backup)

    before_hash = _sha256(assembly)
    before_size = int(assembly.stat().st_size)
    target_doc = None
    reopened_doc = None
    saved = False

    try:
        target_doc = _open_exact(app, assembly)
        active_open = str(session.active_path() or "")
        if _norm(active_open) != _norm(assembly):
            raise RuntimeError("opened_assembly_not_exact_active:" + active_open)

        target_d3, top = _doc3d(target_doc)
        before_rows = _rows(top)
        before_count = len(before_rows)
        source_before = _reference_count(before_rows, component)
        if source_before and not allow_duplicate:
            raise ValueError(
                "duplicate_source_reference_refused:" + str(component)
            )

        inserted = top.Parts.AddFromFile(str(component), True, True)
        if inserted is None:
            raise RuntimeError("IParts7.AddFromFile_returned_none")

        after_add_rows = _rows(top)
        if len(after_add_rows) != before_count + 1:
            raise RuntimeError(
                "component_count_after_add_mismatch:"
                + f"before={before_count},after={len(after_add_rows)}"
            )
        source_after_add = _reference_count(after_add_rows, component)
        if source_after_add != source_before + 1:
            raise RuntimeError(
                "source_reference_after_add_mismatch:"
                + f"before={source_before},after={source_after_add}"
            )

        active_before_save = str(session.active_path() or "")
        if _norm(active_before_save) != _norm(assembly):
            raise RuntimeError(
                "active_document_changed_before_Save:" + active_before_save
            )

        save_result = target_doc.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True

        _close(target_doc)
        target_doc = None

        reopened_doc = _open_exact(app, assembly)
        active_reopen = str(session.active_path() or "")
        if _norm(active_reopen) != _norm(assembly):
            raise RuntimeError("reopened_assembly_not_exact_active:" + active_reopen)

        _, reopened_top = _doc3d(reopened_doc)
        reopened_rows = _rows(reopened_top)
        source_after_reopen = _reference_count(reopened_rows, component)
        if len(reopened_rows) != before_count + 1:
            raise RuntimeError(
                "component_count_after_reopen_mismatch:"
                + f"expected={before_count + 1},actual={len(reopened_rows)}"
            )
        if source_after_reopen != source_before + 1:
            raise RuntimeError(
                "source_reference_after_reopen_mismatch:"
                + f"expected={source_before + 1},actual={source_after_reopen}"
            )

        exact_rows = [
            row
            for row in reopened_rows
            if row.get("file_name") and _norm(row["file_name"]) == _norm(component)
        ]
        if not exact_rows:
            raise RuntimeError("exact_FileName_readback_missing")
        missing = [
            row["file_name"]
            for row in reopened_rows
            if row.get("file_name") and not Path(row["file_name"]).is_file()
        ]
        if missing:
            raise RuntimeError("missing_references_after_reopen:" + repr(missing))

        _close(reopened_doc)
        reopened_doc = None
        _activate(original_doc)
        restored = str(session.active_path() or "")
        if _norm(restored) != _norm(original):
            raise RuntimeError("original_active_document_not_restored:" + restored)

        after_hash = _sha256(assembly)
        after_size = int(assembly.stat().st_size)
        txn_backup.unlink()

        return {
            "requested_action": {
                "assembly_filename": assembly_filename,
                "component_filename": component_filename,
                "external_file": True,
                "allow_duplicate": allow_duplicate,
            },
            "active_document": restored,
            "target": str(assembly),
            "before": {
                "component_count": before_count,
                "source_reference_count": source_before,
                "file_size_bytes": before_size,
                "sha256": before_hash,
            },
            "after": {
                "component_count": len(reopened_rows),
                "source_reference_count": source_after_reopen,
                "file_size_bytes": after_size,
                "sha256": after_hash,
                "missing_reference_count": 0,
            },
            "changed": before_hash != after_hash,
            "save_required": False,
            "saved": saved,
            "warnings": [],
            "verification": {
                "native_api": "API7 IKompasDocument3D.TopPart.Parts.AddFromFile",
                "external_file": True,
                "exact_active_path_before_write": True,
                "exact_active_path_before_save": True,
                "component_count_increment": True,
                "exact_FileName_reference_readback": True,
                "save_close_reopen_verified": True,
                "missing_refs_zero": True,
                "transaction_snapshot_created": True,
                "transaction_snapshot_removed_after_commit": True,
                "original_active_restored": True,
                "original_saved": False,
            },
            "actual": {
                "assembly_path": str(assembly),
                "component_path": str(component),
                "inserted_component": exact_rows[-1],
                "components": reopened_rows,
            },
            "readback": {
                "component_count": len(reopened_rows),
                "exact_source_path": str(component),
                "exact_source_occurrences": source_after_reopen,
                "missing_references": [],
            },
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
