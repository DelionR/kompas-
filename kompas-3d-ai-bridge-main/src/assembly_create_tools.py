from __future__ import annotations

from pathlib import Path

from view_tools import _active_api5


def _validate_filename(filename):
    if not isinstance(filename, str):
        raise TypeError(
            "filename_must_be_string"
        )

    raw = filename.strip()

    if not raw:
        raise ValueError(
            "filename_is_empty"
        )

    p = Path(raw)

    if p.name != raw:
        raise ValueError(
            "filename_must_be_basename_only"
        )

    if p.suffix.lower() != ".a3d":
        raise ValueError(
            "filename_must_end_with_a3d"
        )

    if "_agent_copy" not in p.stem.lower():
        raise ValueError(
            "filename_must_contain_AGENT_COPY"
        )

    return raw


def _close_doc(doc):
    errors = []

    for name in ("close", "Close"):
        try:
            member = getattr(doc, name)
            member() if callable(member) else member

            return {
                "ok": True,
                "method": name,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(
                f"{name}:{type(exc).__name__}:{exc}"
            )

    return {
        "ok": False,
        "method": None,
        "errors": errors,
    }


def _open_api5(app5, path):
    doc = app5.Document3D()

    if doc is None:
        raise RuntimeError(
            "Document3D_returned_none"
        )

    if not bool(
        doc.Open(
            str(path),
            False,
        )
    ):
        raise RuntimeError(
            "ksDocument3D.Open_returned_false:"
            + str(path)
        )

    return doc


def assembly_create(
    session,
    filename,
):
    """
    Create one new native KOMPAS assembly .a3d in the exact approved
    bridge work directory.

    Native API5 proof path:
      KompasObject.Document3D()
      ksDocument3D.Create(False, False)
        invisible=False
        typeDoc=False -> assembly
      ksDocument3D.IsDetail() must be False
      SaveAs -> Close -> Reopen -> IsDetail False

    Safety:
      - basename only
      - .a3d
      - *_AGENT_COPY
      - no overwrite
      - exact approved work
      - original active *_AGENT_COPY is restored
      - original document is never saved or mutated
      - newly-created target is removed if transaction fails
    """
    filename = _validate_filename(
        filename
    )

    original_path = str(
        session.active_path()
        or ""
    )

    if not original_path:
        raise RuntimeError(
            "no_active_document_path"
        )

    original = Path(
        original_path
    ).resolve()

    if (
        original.parent.name.lower()
        != "work"
        or "_agent_copy"
        not in original.stem.lower()
    ):
        raise RuntimeError(
            "active_document_not_safe_AGENT_COPY:"
            + original_path
        )

    root = (
        original.parent.parent
    ).resolve()

    approved_work = (
        root / "work"
    ).resolve()

    if (
        original.parent
        != approved_work
    ):
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

    (
        app5,
        original_d3,
        api7_path,
        api5_path,
        is_active,
    ) = _active_api5(
        session
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
            "api7_active_path_changed_before_create:"
            + str(api7_path)
        )

    if (
        api5_path
        and Path(api5_path).resolve()
        != original
    ):
        raise RuntimeError(
            "api5_active_path_changed_before_create:"
            + str(api5_path)
        )

    before = {
        "active_document": original_path,
        "target": str(target),
        "target_exists": False,
    }

    newdoc = None
    reopened = None
    created_on_disk = False

    try:
        newdoc = app5.Document3D()

        if newdoc is None:
            raise RuntimeError(
                "KompasObject.Document3D_returned_none"
            )

        # Official API5:
        # typeDoc=False -> assembly.
        if not bool(
            newdoc.Create(
                False,
                False,
            )
        ):
            raise RuntimeError(
                "ksDocument3D.Create_assembly_returned_false"
            )

        # IsDetail False is the native type proof for assembly.
        if bool(
            newdoc.IsDetail()
        ):
            raise RuntimeError(
                "new_document_reports_detail_not_assembly"
            )

        if target.exists():
            raise RuntimeError(
                "target_appeared_before_SaveAs:"
                + str(target)
            )

        if not bool(
            newdoc.SaveAs(
                str(target)
            )
        ):
            raise RuntimeError(
                "ksDocument3D.SaveAs_returned_false"
            )

        created_on_disk = True

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
            session.active_path()
            or ""
        )

        if (
            not active_after_save
            or Path(
                active_after_save
            ).resolve()
            != target
        ):
            raise RuntimeError(
                "unexpected_active_document_after_SaveAs:"
                + active_after_save
            )

        close_info = _close_doc(
            newdoc
        )

        if not close_info["ok"]:
            raise RuntimeError(
                "new_assembly_close_failed:"
                + " | ".join(
                    close_info[
                        "errors"
                    ]
                )
            )

        newdoc = None

        reopened = _open_api5(
            app5,
            target,
        )

        active_after_reopen = str(
            session.active_path()
            or ""
        )

        if (
            not active_after_reopen
            or Path(
                active_after_reopen
            ).resolve()
            != target
        ):
            raise RuntimeError(
                "reopened_assembly_not_exact_active:"
                + active_after_reopen
            )

        if bool(
            reopened.IsDetail()
        ):
            raise RuntimeError(
                "reopened_document_reports_detail_not_assembly"
            )

        reopen_close_info = (
            _close_doc(
                reopened
            )
        )

        if not reopen_close_info[
            "ok"
        ]:
            raise RuntimeError(
                "reopened_assembly_close_failed:"
                + " | ".join(
                    reopen_close_info[
                        "errors"
                    ]
                )
            )

        reopened = None

        original_d3.SetActive()

        restored = str(
            session.active_path()
            or ""
        )

        if (
            not restored
            or Path(
                restored
            ).resolve()
            != original
        ):
            raise RuntimeError(
                "original_active_document_not_restored:"
                + restored
            )

        return {
            "requested_action": {
                "filename": filename,
                "create_native_assembly": True,
            },
            "active_document": restored,
            "target": str(target),
            "before": before,
            "after": {
                "created_file": str(target),
                "exists": True,
                "size_bytes": (
                    size_bytes
                ),
                "is_detail": False,
                "is_assembly": True,
                "restored_active_document": (
                    restored
                ),
            },
            "changed": True,
            "save_required": False,
            "saved": True,
            "warnings": [],
            "verification": {
                "native_create_api": (
                    "API5 ksDocument3D.Create(False, False)"
                ),
                "native_type_api": (
                    "API5 ksDocument3D.IsDetail()"
                ),
                "typeDoc_false_means_assembly": True,
                "is_detail_after_create": False,
                "is_detail_after_reopen": False,
                "assembly_type_verified": True,
                "target_inside_approved_work": True,
                "target_preexisted": False,
                "overwrite_performed": False,
                "target_nonzero_size": True,
                "save_close_reopen_verified": True,
                "original_active_restored": True,
                "original_saved": False,
                "original_geometry_changed": False,
            },
            "actual": {
                "path": str(target),
                "size_bytes": (
                    size_bytes
                ),
                "file_type": ".a3d",
                "document_kind": (
                    "assembly"
                ),
            },
        }

    except Exception:
        try:
            if reopened is not None:
                _close_doc(
                    reopened
                )
        except Exception:
            pass

        try:
            if newdoc is not None:
                _close_doc(
                    newdoc
                )
        except Exception:
            pass

        try:
            original_d3.SetActive()
        except Exception:
            pass

        # The file did not exist before this transaction.
        # Remove only the deterministic target created by this failed call.
        if (
            created_on_disk
            and target.exists()
        ):
            try:
                target.unlink()
            except Exception:
                pass

        try:
            original_d3.SetActive()
        except Exception:
            pass

        raise
