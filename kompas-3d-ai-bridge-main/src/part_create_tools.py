from __future__ import annotations

from pathlib import Path

from view_tools import _active_api5


def _safe_close(doc):
    errors = []

    for name in ("close", "Close"):
        try:
            member = getattr(doc, name)
            value = member() if callable(member) else member
            return {
                "ok": True,
                "method": name,
                "return": value,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(
                f"{name}:{type(exc).__name__}:{exc}"
            )

    return {
        "ok": False,
        "method": None,
        "return": None,
        "errors": errors,
    }


def _validate_filename(filename):
    if not isinstance(filename, str):
        raise TypeError("filename_must_be_string")

    raw = filename.strip()

    if not raw:
        raise ValueError("filename_is_empty")

    p = Path(raw)

    if p.name != raw:
        raise ValueError(
            "filename_must_be_basename_only"
        )

    if p.suffix.lower() != ".m3d":
        raise ValueError(
            "filename_must_end_with_m3d"
        )

    if "_agent_copy" not in p.stem.lower():
        raise ValueError(
            "filename_must_contain_AGENT_COPY"
        )

    return raw


def part_create(session, filename):
    """
    Create one new native KOMPAS .m3d detail in the approved bridge work
    directory. No overwrite. The previously active *_AGENT_COPY document
    is restored active after creation.

    The new document is created with the proven API5 path:
      KompasObject.Document3D()
      ksDocument3D.Create(False, True)
      ksDocument3D.SaveAs(target)
    """
    filename = _validate_filename(filename)

    original_path = str(
        session.active_path() or ""
    )

    if not original_path:
        raise RuntimeError(
            "no_active_document_path"
        )

    original = Path(
        original_path
    ).resolve()

    if original.parent.name.lower() != "work":
        raise RuntimeError(
            "active_document_not_in_work:"
            + original_path
        )

    if (
        "_agent_copy"
        not in original.stem.lower()
    ):
        raise RuntimeError(
            "active_document_lacks_AGENT_COPY:"
            + original_path
        )

    root = original.parent.parent.resolve()
    approved_work = (
        root / "work"
    ).resolve()

    if original.parent != approved_work:
        raise RuntimeError(
            "active_document_not_directly_in_approved_work:"
            + original_path
        )

    target = (
        approved_work / filename
    ).resolve()

    if target.parent != approved_work:
        raise RuntimeError(
            "target_escaped_approved_work"
        )

    if target.exists():
        raise FileExistsError(
            "target_exists_no_overwrite:"
            + str(target)
        )

    app5, original_d3, api7_path, api5_path, is_active = (
        _active_api5(session)
    )

    if not is_active:
        raise RuntimeError(
            "original_api5_document_not_active"
        )

    if (
        api7_path
        and Path(api7_path).resolve()
        != original
    ):
        raise RuntimeError(
            "api7_active_path_changed_before_write:"
            + str(api7_path)
        )

    if (
        api5_path
        and Path(api5_path).resolve()
        != original
    ):
        raise RuntimeError(
            "api5_active_path_changed_before_write:"
            + str(api5_path)
        )

    before = {
        "active_document": original_path,
        "target_exists": False,
        "target": str(target),
    }

    newdoc = None
    created = False
    saved = False
    close_info = None

    try:
        newdoc = app5.Document3D()

        if newdoc is None:
            raise RuntimeError(
                "KompasObject.Document3D_returned_none"
            )

        create_result = bool(
            newdoc.Create(
                False,
                True,
            )
        )

        if not create_result:
            raise RuntimeError(
                "ksDocument3D.Create_returned_false"
            )

        created = True

        # The new document is unsaved at this point, so it has no exact path.
        # Safety before first SaveAs is therefore based on:
        #   1) target is a non-existing basename-only *_AGENT_COPY.m3d
        #   2) target is inside exact approved work
        #   3) we hold the exact newly created COM document instance.
        if target.exists():
            raise RuntimeError(
                "target_appeared_before_SaveAs:"
                + str(target)
            )

        save_result = bool(
            newdoc.SaveAs(
                str(target)
            )
        )

        if not save_result:
            raise RuntimeError(
                "ksDocument3D.SaveAs_returned_false"
            )

        saved = True

        if not target.is_file():
            raise RuntimeError(
                "target_missing_after_SaveAs:"
                + str(target)
            )

        size_bytes = int(
            target.stat().st_size
        )

        if size_bytes <= 0:
            raise RuntimeError(
                "target_file_is_empty"
            )

        active_after_save = str(
            session.active_path() or ""
        )

        if (
            not active_after_save
            or Path(active_after_save).resolve()
            != target
        ):
            raise RuntimeError(
                "unexpected_active_document_after_SaveAs:"
                + active_after_save
            )

        # Exact path is now available and must match before any close/restore.
        if Path(active_after_save).resolve() != target:
            raise RuntimeError(
                "target_path_mismatch_before_close"
            )

        close_info = _safe_close(
            newdoc
        )

        if not close_info["ok"]:
            raise RuntimeError(
                "new_part_close_failed:"
                + " | ".join(
                    close_info["errors"]
                )
            )

        original_d3.SetActive()

        restored_path = str(
            session.active_path() or ""
        )

        if (
            not restored_path
            or Path(restored_path).resolve()
            != original
        ):
            raise RuntimeError(
                "original_active_document_not_restored:"
                + restored_path
            )

        after = {
            "created_file": str(target),
            "exists": target.is_file(),
            "size_bytes": size_bytes,
            "restored_active_document": restored_path,
        }

        return {
            "requested_action": {
                "filename": filename,
                "create_native_part": True,
            },
            "active_document": restored_path,
            "target": str(target),
            "before": before,
            "after": after,
            "changed": True,
            "save_required": False,
            "saved": True,
            "warnings": [],
            "verification": {
                "native_create_api": (
                    "API5 ksDocument3D.Create(False, True)"
                ),
                "native_save_api": (
                    "API5 ksDocument3D.SaveAs"
                ),
                "native_detail": True,
                "target_inside_approved_work": True,
                "target_preexisted": False,
                "overwrite_performed": False,
                "target_exists_after_save": True,
                "target_nonzero_size": (
                    size_bytes > 0
                ),
                "new_document_closed": True,
                "original_active_restored": True,
                "original_saved": False,
                "original_geometry_changed": False,
            },
            "actual": {
                "path": str(target),
                "size_bytes": size_bytes,
                "file_type": ".m3d",
            },
        }

    except Exception:
        # Always attempt to restore the original document.
        try:
            original_d3.SetActive()
        except Exception:
            pass

        # If creation never saved, no filesystem artifact should remain.
        # If SaveAs succeeded, retain the newly created approved-work file:
        # a caller can inspect it and no existing file was overwritten.
        raise
