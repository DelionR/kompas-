from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from view_tools import _active_api5


TOL = 1e-4


def _validate_filename(filename):
    if not isinstance(filename, str):
        raise TypeError("filename_must_be_string")

    raw = filename.strip()

    if not raw:
        raise ValueError("filename_is_empty")

    p = Path(raw)

    if p.name != raw:
        raise ValueError("filename_must_be_basename_only")

    if p.suffix.lower() != ".m3d":
        raise ValueError("filename_must_end_with_m3d")

    if "_agent_copy" not in p.stem.lower():
        raise ValueError("filename_must_contain_AGENT_COPY")

    return raw


def _positive(value, name):
    try:
        v = float(value)
    except Exception as exc:
        raise ValueError(name + "_must_be_number") from exc

    if not (v > 0.0):
        raise ValueError(name + "_must_be_positive")

    return v


def _close_doc(doc):
    errors = []

    for name in ("close", "Close"):
        try:
            member = getattr(doc, name)
            member() if callable(member) else member
            return name
        except Exception as exc:
            errors.append(
                f"{name}:{type(exc).__name__}:{exc}"
            )

    raise RuntimeError(
        "document_close_failed:"
        + " | ".join(errors)
    )


def _open_api5(app5, path):
    doc = app5.Document3D()

    if doc is None:
        raise RuntimeError("Document3D_returned_none")

    if not bool(doc.Open(str(path), False)):
        raise RuntimeError(
            "ksDocument3D.Open_returned_false:"
            + str(path)
        )

    return doc


def _top_part(doc3d):
    part = doc3d.GetPart(-1)  # pTop_Part

    if part is None:
        raise RuntimeError(
            "GetPart(pTop_Part=-1)_returned_none"
        )

    return part


def _body_rows(part):
    collection = part.BodyCollection()

    if collection is None:
        return []

    count = int(collection.GetCount())
    rows = []

    for i in range(count):
        body = collection.GetByIndex(i)

        if body is None:
            continue

        bbox_raw = body.GetGabarit(
            0.0, 0.0, 0.0,
            0.0, 0.0, 0.0,
        )

        seq = (
            list(bbox_raw)
            if isinstance(bbox_raw, (tuple, list))
            else [bbox_raw]
        )

        if (
            len(seq) == 7
            and isinstance(seq[0], bool)
        ):
            if not seq[0]:
                raise RuntimeError(
                    "GetGabarit_valid_false"
                )
            seq = seq[1:]

        if len(seq) != 6:
            raise RuntimeError(
                "GetGabarit_unexpected_shape:"
                + repr(seq)
            )

        bbox = [float(x) for x in seq]

        faces = body.FaceCollection()
        face_count = (
            int(faces.GetCount())
            if faces is not None
            else 0
        )

        rows.append({
            "body_index": i,
            "bbox": bbox,
            "dims": [
                bbox[3] - bbox[0],
                bbox[4] - bbox[1],
                bbox[5] - bbox[2],
            ],
            "face_count": face_count,
        })

    return rows


def _rect_lines(doc2, width, height):
    x1 = -width / 2.0
    x2 = +width / 2.0
    y1 = -height / 2.0
    y2 = +height / 2.0

    refs = [
        doc2.ksLineSeg(x1, y1, x2, y1, 1),
        doc2.ksLineSeg(x2, y1, x2, y2, 1),
        doc2.ksLineSeg(x2, y2, x1, y2, 1),
        doc2.ksLineSeg(x1, y2, x1, y1, 1),
    ]

    if any(int(ref or 0) == 0 for ref in refs):
        raise RuntimeError(
            "one_or_more_ksLineSeg_failed:"
            + repr(refs)
        )

    return [int(ref) for ref in refs]


def _build_primitive(
    part,
    kind,
    width_mm,
    height_mm,
    depth_mm,
    wall_mm,
):
    # Verified API5 constants/path:
    # o3d_planeXOY = 1
    # o3d_sketch = 5
    # o3d_baseExtrusion = 24
    # End_Type.etBlind = 0

    plane = part.GetDefaultEntity(1)

    if plane is None:
        raise RuntimeError(
            "XOY_default_plane_none"
        )

    sketch = part.NewEntity(5)

    if sketch is None:
        raise RuntimeError(
            "NewEntity(o3d_sketch)_returned_none"
        )

    sketch_def = sketch.GetDefinition()

    if sketch_def is None:
        raise RuntimeError(
            "sketch_GetDefinition_none"
        )

    if not bool(
        sketch_def.SetPlane(plane)
    ):
        raise RuntimeError(
            "sketch_SetPlane_failed"
        )

    if not bool(sketch.Create()):
        raise RuntimeError(
            "sketch_Create_failed"
        )

    doc2 = sketch_def.BeginEdit()

    if doc2 is None:
        raise RuntimeError(
            "sketch_BeginEdit_returned_none"
        )

    outer_refs = _rect_lines(
        doc2,
        width_mm,
        height_mm,
    )

    inner_refs = []

    if kind == "rect_tube":
        inner_width = (
            width_mm
            - 2.0 * wall_mm
        )
        inner_height = (
            height_mm
            - 2.0 * wall_mm
        )

        if (
            inner_width <= 0.0
            or inner_height <= 0.0
        ):
            raise ValueError(
                "wall_mm_too_large_for_rect_tube"
            )

        inner_refs = _rect_lines(
            doc2,
            inner_width,
            inner_height,
        )

    end_edit = sketch_def.EndEdit()

    if end_edit is False:
        raise RuntimeError(
            "sketch_EndEdit_returned_false"
        )

    extrusion = part.NewEntity(24)

    if extrusion is None:
        raise RuntimeError(
            "NewEntity(o3d_baseExtrusion)_returned_none"
        )

    extrusion_def = extrusion.GetDefinition()

    if extrusion_def is None:
        raise RuntimeError(
            "baseExtrusion_GetDefinition_none"
        )

    if not bool(
        extrusion_def.SetSketch(sketch)
    ):
        raise RuntimeError(
            "baseExtrusion_SetSketch_failed"
        )

    if not bool(
        extrusion_def.SetSideParam(
            True,
            0,  # etBlind
            depth_mm,
            0.0,
            False,
        )
    ):
        raise RuntimeError(
            "baseExtrusion_SetSideParam_failed"
        )

    if not bool(extrusion.Create()):
        raise RuntimeError(
            "baseExtrusion_Create_failed"
        )

    return {
        "outer_sketch_line_refs": outer_refs,
        "inner_sketch_line_refs": inner_refs,
        "nested_inner_contour": (
            kind == "rect_tube"
        ),
        "sketch_created": True,
        "base_extrusion_created": True,
    }


def _verify_geometry(
    doc3d,
    kind,
    expected_dims,
    stage,
):
    part = _top_part(doc3d)
    bodies = _body_rows(part)

    if len(bodies) != 1:
        raise RuntimeError(
            f"{stage}:expected_one_body_got_{len(bodies)}"
        )

    row = bodies[0]

    for actual, expected in zip(
        row["dims"],
        expected_dims,
    ):
        if abs(actual - expected) > TOL:
            raise RuntimeError(
                f"{stage}:bbox_dim_mismatch:"
                f"actual={row['dims']},"
                f" expected={expected_dims}"
            )

    if kind in ("block", "plate"):
        if row["face_count"] < 6:
            raise RuntimeError(
                f"{stage}:solid_face_count_too_low:"
                + str(row["face_count"])
            )

    if kind == "rect_tube":
        # A rectangular solid block has six planar faces.
        # Requiring >=10 demonstrates that the nested inner contour
        # survived extrusion as a hollow rectangular section rather than
        # degenerating to a plain block.
        if row["face_count"] < 10:
            raise RuntimeError(
                f"{stage}:rect_tube_not_hollow_enough:"
                f"face_count={row['face_count']}"
            )

    return {
        "body_count": 1,
        "bbox": row["bbox"],
        "dims": row["dims"],
        "face_count": row["face_count"],
        "hollow_section_verified": (
            kind == "rect_tube"
            and row["face_count"] >= 10
        ),
    }


def part_add_primitive(
    session,
    filename,
    kind,
    width_mm,
    height_mm,
    depth_mm,
    wall_mm=None,
):
    """
    Populate one EMPTY existing native *_AGENT_COPY.m3d in approved work
    with a bounded native primitive:
      block      - centered rectangular prism
      plate      - same native rectangular extrusion, semantic plate
      rect_tube  - rectangular hollow profile member from nested contours

    Coordinates:
      width -> X
      height -> Y
      depth -> +Z extrusion

    Safety:
      - target basename only, exact approved work
      - target must already exist and must be EMPTY
      - target must not already be the active document
      - transaction file copy before mutation
      - exact active target check before Save
      - Save -> Close -> Reopen -> geometry verification
      - rollback target bytes on failure
      - original active *_AGENT_COPY restored
    """
    filename = _validate_filename(
        filename
    )

    kind = str(kind).strip().lower()

    if kind not in {
        "block",
        "plate",
        "rect_tube",
    }:
        raise ValueError(
            "kind_must_be_block_plate_or_rect_tube"
        )

    width_mm = _positive(
        width_mm,
        "width_mm",
    )
    height_mm = _positive(
        height_mm,
        "height_mm",
    )
    depth_mm = _positive(
        depth_mm,
        "depth_mm",
    )

    if kind == "rect_tube":
        wall_mm = _positive(
            wall_mm,
            "wall_mm",
        )

        if (
            wall_mm * 2.0
            >= min(width_mm, height_mm)
        ):
            raise ValueError(
                "wall_mm_too_large_for_rect_tube"
            )
    else:
        if wall_mm is not None:
            raise ValueError(
                "wall_mm_only_valid_for_rect_tube"
            )

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

    if not target.is_file():
        raise FileNotFoundError(
            "target_part_missing:"
            + str(target)
        )

    if target == original:
        raise RuntimeError(
            "target_must_not_already_be_active"
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
            "api7_active_path_changed_before_transaction"
        )

    if (
        api5_path
        and Path(api5_path).resolve()
        != original
    ):
        raise RuntimeError(
            "api5_active_path_changed_before_transaction"
        )

    backup_dir = (
        root / "_BACKUPS"
    ).resolve()
    backup_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    txn_backup = (
        backup_dir
        / (
            "TXN_PART_PRIMITIVE_"
            + uuid.uuid4().hex
            + "_"
            + target.name
        )
    )

    shutil.copy2(
        target,
        txn_backup,
    )

    before_size = int(
        target.stat().st_size
    )

    fixture = None
    reopened = None
    saved = False

    expected_dims = [
        width_mm,
        height_mm,
        depth_mm,
    ]

    try:
        fixture = _open_api5(
            app5,
            target,
        )

        active_target = str(
            session.active_path() or ""
        )

        if (
            not active_target
            or Path(active_target).resolve()
            != target
        ):
            raise RuntimeError(
                "target_not_exact_active_before_write:"
                + active_target
            )

        part = _top_part(
            fixture
        )

        before_bodies = _body_rows(
            part
        )

        if before_bodies:
            raise RuntimeError(
                "target_part_not_empty_refusing_to_mutate:"
                + str(len(before_bodies))
            )

        create_info = _build_primitive(
            part,
            kind,
            width_mm,
            height_mm,
            depth_mm,
            wall_mm,
        )

        try:
            fixture.RebuildDocument()
        except Exception:
            pass

        after_create = _verify_geometry(
            fixture,
            kind,
            expected_dims,
            "after_create",
        )

        active_before_save = str(
            session.active_path() or ""
        )

        if (
            not active_before_save
            or Path(active_before_save).resolve()
            != target
        ):
            raise RuntimeError(
                "active_path_changed_before_Save:"
                + active_before_save
            )

        if not bool(fixture.Save()):
            raise RuntimeError(
                "ksDocument3D.Save_returned_false"
            )

        saved = True

        size_after_save = int(
            target.stat().st_size
        )

        _close_doc(
            fixture
        )
        fixture = None

        reopened = _open_api5(
            app5,
            target,
        )

        active_after_reopen = str(
            session.active_path() or ""
        )

        if (
            not active_after_reopen
            or Path(active_after_reopen).resolve()
            != target
        ):
            raise RuntimeError(
                "reopened_target_not_active:"
                + active_after_reopen
            )

        after_reopen = _verify_geometry(
            reopened,
            kind,
            expected_dims,
            "after_reopen",
        )

        for a, b in zip(
            after_create["bbox"],
            after_reopen["bbox"],
        ):
            if abs(a - b) > TOL:
                raise RuntimeError(
                    "bbox_changed_after_SaveCloseReopen"
                )

        _close_doc(
            reopened
        )
        reopened = None

        original_d3.SetActive()

        restored = str(
            session.active_path() or ""
        )

        if (
            not restored
            or Path(restored).resolve()
            != original
        ):
            raise RuntimeError(
                "original_active_not_restored:"
                + restored
            )

        try:
            txn_backup.unlink()
        except FileNotFoundError:
            pass

        return {
            "requested_action": {
                "filename": filename,
                "kind": kind,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "depth_mm": depth_mm,
                "wall_mm": wall_mm,
            },
            "active_document": restored,
            "target": str(target),
            "before": {
                "body_count": 0,
                "file_size_bytes": before_size,
            },
            "after": {
                "body_count": (
                    after_reopen["body_count"]
                ),
                "bbox": (
                    after_reopen["bbox"]
                ),
                "dims": (
                    after_reopen["dims"]
                ),
                "face_count": (
                    after_reopen["face_count"]
                ),
                "file_size_bytes": (
                    size_after_save
                ),
                "hollow_section_verified": (
                    after_reopen[
                        "hollow_section_verified"
                    ]
                ),
            },
            "changed": True,
            "save_required": False,
            "saved": True,
            "warnings": [],
            "verification": {
                "native_sketch_api": (
                    "API5 o3d_sketch=5"
                ),
                "native_extrusion_api": (
                    "API5 o3d_baseExtrusion=24"
                ),
                "extrusion_end_type": (
                    "etBlind=0"
                ),
                "target_was_empty": True,
                "transaction_backup_created": True,
                "geometry_verified_before_save": True,
                "exact_target_active_before_save": True,
                "save_close_reopen_verified": True,
                "bbox_persisted": True,
                "single_body": True,
                "rect_tube_nested_contour": (
                    kind == "rect_tube"
                ),
                "hollow_section_verified": (
                    after_reopen[
                        "hollow_section_verified"
                    ]
                ),
                "original_active_restored": True,
                "original_saved": False,
                "original_geometry_changed": False,
                "transaction_backup_removed_after_success": True,
            },
            "actual": {
                "kind": kind,
                "path": str(target),
                "body_count": 1,
                "bbox": (
                    after_reopen["bbox"]
                ),
                "dims": (
                    after_reopen["dims"]
                ),
                "face_count": (
                    after_reopen["face_count"]
                ),
            },
            "native_build": create_info,
        }

    except Exception:
        # Close target documents before byte-level rollback.
        try:
            if reopened is not None:
                _close_doc(reopened)
        except Exception:
            pass

        try:
            if fixture is not None:
                _close_doc(fixture)
        except Exception:
            pass

        try:
            original_d3.SetActive()
        except Exception:
            pass

        # Restore exact target bytes from the pre-transaction snapshot.
        try:
            if txn_backup.is_file():
                shutil.copy2(
                    txn_backup,
                    target,
                )
        finally:
            try:
                txn_backup.unlink()
            except FileNotFoundError:
                pass

        # Best effort to keep the exact original active document.
        try:
            original_d3.SetActive()
        except Exception:
            pass

        raise
