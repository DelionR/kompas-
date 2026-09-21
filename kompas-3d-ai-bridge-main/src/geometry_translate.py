from __future__ import annotations

import math
import os
from pathlib import Path
import time

from model_tools import component_info
from view_tools import _active_api5


ACTION = "component.translate_xy"
MAX_VECTOR_MM = 5.0
TOLERANCE_MM = 0.05


def _norm_path(value):
    return os.path.normcase(os.path.abspath(str(value)))


def _name(value):
    return " ".join(
        str(value or "")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .split()
    ).casefold()


def _basename(value):
    try:
        return Path(str(value or "")).name.casefold()
    except Exception:
        return ""


def _part_name(part):
    for attr in ("name", "Name", "GetName"):
        try:
            value = getattr(part, attr)
            value = value() if callable(value) else value
            text = str(value or "").strip()
            if text:
                return text
        except Exception:
            pass
    return ""


def _part_file(part):
    for attr in ("fileName", "FileName", "GetFileName"):
        try:
            value = getattr(part, attr)
            value = value() if callable(value) else value
            text = str(value or "").strip()
            if text:
                return text
        except Exception:
            pass
    return ""


def _collection(doc5):
    member = getattr(doc5, "PartCollection")
    coll = member(True) if callable(member) else member
    if coll is None:
        raise RuntimeError("geometry_PartCollection_returned_null")
    return coll


def _collection_count(coll):
    member = getattr(coll, "GetCount")
    return int(member() if callable(member) else member)


def _unique_api5_part(doc5, structured_name, structured_file):
    coll = _collection(doc5)
    count = _collection_count(coll)
    name_key = _name(structured_name)
    file_key = _basename(structured_file)

    exact_name_matches = []

    for index in range(count):
        part = coll.GetByIndex(index)
        if part is None:
            continue

        pname = _part_name(part)
        pfile = _part_file(part)

        if _name(pname) == name_key:
            exact_name_matches.append(
                {
                    "index": index,
                    "part": part,
                    "name": pname,
                    "file_name": pfile,
                }
            )

    if len(exact_name_matches) == 1:
        row = exact_name_matches[0]
        return row, count, "unique_exact_name"

    if len(exact_name_matches) > 1 and file_key:
        narrowed = [
            row
            for row in exact_name_matches
            if _basename(row["file_name"]) == file_key
        ]

        if len(narrowed) == 1:
            return narrowed[0], count, "name_plus_file"

    raise RuntimeError(
        "geometry_api5_component_mapping_not_unique:"
        + " structured_name="
        + repr(structured_name)
        + " structured_file="
        + repr(structured_file)
        + " exact_name_matches="
        + repr(
            [
                {
                    "index": row["index"],
                    "name": row["name"],
                    "file_name": row["file_name"],
                }
                for row in exact_name_matches
            ]
        )
    )


def _positioner(doc5):
    member = getattr(doc5, "ComponentPositioner")
    pos = member() if callable(member) else member
    if pos is None:
        raise RuntimeError("geometry_ComponentPositioner_returned_null")
    return pos


def _refresh(doc5, app5):
    rebuild_result = None

    try:
        member = getattr(doc5, "RebuildDocument")
        rebuild_result = member() if callable(member) else None
    except Exception:
        rebuild_result = None

    refresh_result = None

    try:
        member = getattr(app5, "ksRefreshActiveWindow")
        refresh_result = member() if callable(member) else None
    except Exception:
        refresh_result = None

    return {
        "rebuild_result": rebuild_result,
        "refresh_result": refresh_result,
    }


def _origin_from_info(info):
    origin = info.get("origin")

    if not isinstance(origin, (list, tuple)):
        raise RuntimeError(
            "geometry_component_origin_unavailable:"
            + repr(origin)
        )

    # KOMPAS IPlacement3D.GetOrigin may arrive through pywin32 as:
    # [valid, x, y, z].
    #
    # bool is a subclass of int in Python, therefore True must
    # never become coordinate 1.0.
    if len(origin) >= 4 and isinstance(origin[0], bool):
        if origin[0] is not True:
            raise RuntimeError(
                "geometry_component_origin_invalid_flag:"
                + repr(origin)
            )
        coords = origin[1:4]
    elif len(origin) >= 3:
        coords = origin[0:3]
    else:
        raise RuntimeError(
            "geometry_component_origin_unavailable:"
            + repr(origin)
        )

    try:
        return (
            float(coords[0]),
            float(coords[1]),
            float(coords[2]),
        )
    except Exception as exc:
        raise RuntimeError(
            "geometry_component_origin_invalid:"
            + repr(origin)
            + ":"
            + str(exc)
        )

def _close_enough(actual, expected):
    return all(
        abs(float(a) - float(e)) <= TOLERANCE_MM
        for a, e in zip(actual, expected)
    )


def _move_component_origin_xy(
    doc5,
    app5,
    part,
    current_origin,
    target_origin,
):
    ox, oy, oz = map(float, current_origin)
    tx, ty, tz = map(float, target_origin)

    if abs(tz - oz) > TOLERANCE_MM:
        raise RuntimeError("geometry_internal_Z_changed")

    positioner = _positioner(doc5)

    plane_ok = bool(
        positioner.SetPlaneByPoints(
            ox, oy, oz,
            ox + 1.0, oy, oz,
            ox, oy + 1.0, oz,
        )
    )

    if not plane_ok:
        raise RuntimeError(
            "geometry_SetPlaneByPoints_returned_false"
        )

    drag_ok = bool(
        positioner.SetDragPoint(
            0.0,
            0.0,
            0.0,
        )
    )

    if not drag_ok:
        raise RuntimeError(
            "geometry_SetDragPoint_returned_false"
        )

    # Official Positioner_Type: pnMove = 0.
    prepare = int(
        positioner.Prepare(
            part,
            0,
        )
    )

    if prepare != 0:
        raise RuntimeError(
            "geometry_Prepare_failed:return="
            + str(prepare)
        )

    move_error = None

    try:
        if not bool(
            positioner.MoveComponent(
                tx,
                ty,
                tz,
            )
        ):
            raise RuntimeError(
                "geometry_MoveComponent_returned_false"
            )
    except Exception as exc:
        move_error = exc
    finally:
        finish_ok = bool(
            positioner.Finish()
        )

    if not finish_ok:
        if move_error is not None:
            raise RuntimeError(
                "geometry_Move_and_Finish_failed:"
                + type(move_error).__name__
                + ":"
                + str(move_error)
            )
        raise RuntimeError(
            "geometry_Finish_returned_false"
        )

    if move_error is not None:
        raise move_error

    return _refresh(doc5, app5)


def register_geometry_translate(registry, ok_func):
    def action(session, payload):
        payload = payload or {}
        selector = payload.get("selector")

        if isinstance(selector, bool) or not isinstance(selector, int):
            raise ValueError(
                "selector must be a top-level integer index"
            )

        if selector < 0:
            raise ValueError("selector must be >= 0")

        dx = float(payload.get("dx_mm", 0.0))
        dy = float(payload.get("dy_mm", 0.0))
        apply_change = bool(payload.get("apply", False))
        expected_name = str(
            payload.get("expected_name") or ""
        ).strip()

        for label, number in (
            ("dx_mm", dx),
            ("dy_mm", dy),
        ):
            if not math.isfinite(number):
                raise ValueError(
                    label + " must be finite"
                )

        vector = math.hypot(dx, dy)

        if vector <= 1e-9:
            raise ValueError(
                "translation vector must be non-zero"
            )

        if vector > MAX_VECTOR_MM + 1e-9:
            raise ValueError(
                "translation vector exceeds 5 mm safety limit"
            )

        # IMPORTANT: use the accepted KompasSession path.
        # No direct API7 GetActiveObject call exists in this module.
        active_path = str(
            session.active_path() or ""
        ).strip()

        if not active_path:
            raise RuntimeError(
                "geometry_safety:no active KOMPAS document"
            )

        root = Path(session.root).resolve()
        work = (root / "work").resolve()
        active = Path(active_path).resolve()

        if _norm_path(active.parent) != _norm_path(work):
            raise RuntimeError(
                "geometry_safety:active document is not directly "
                "inside bridge work:"
                + str(active)
            )

        if not active.name.casefold().endswith(
            "_agent_copy.a3d"
        ):
            raise RuntimeError(
                "geometry_safety:active document is not "
                "*_AGENT_COPY.a3d:"
                + str(active)
            )

        info_before = component_info(
            session,
            selector,
        )

        if list(info_before.get("tree_path") or []) != [selector]:
            raise RuntimeError(
                "geometry_safety:selector did not resolve to "
                "one top-level component:"
                + repr(info_before.get("tree_path"))
            )

        structured_name = str(
            info_before.get("name") or ""
        ).strip()

        structured_file = str(
            info_before.get("file_name") or ""
        ).strip()

        if not structured_name:
            raise RuntimeError(
                "geometry_safety:component has no name"
            )

        fixed = info_before.get("fixed")
        before_origin = _origin_from_info(
            info_before
        )

        if apply_change:
            if not expected_name:
                raise ValueError(
                    "expected_name is required when apply=true"
                )

            if structured_name != expected_name:
                raise RuntimeError(
                    "geometry_safety:expected_name mismatch:"
                    + " expected="
                    + repr(expected_name)
                    + " actual="
                    + repr(structured_name)
                )

            if fixed is True:
                raise RuntimeError(
                    "geometry_safety:component is fixed"
                )

        # Use the same accepted API5/API7 validated path as view tools.
        app5, doc5, api7_path, api5_path, api5_is_active = _active_api5(
            session
        )

        api5_row, api5_count, mapping_mode = _unique_api5_part(
            doc5,
            structured_name,
            structured_file,
        )

        api5_part = api5_row["part"]

        result = {
            "active_path": active_path,
            "copy_verified": True,
            "selector": selector,
            "tree_path": [selector],
            "component_name": structured_name,
            "component_file": structured_file,
            "component_fixed": fixed,
            "before_origin_mm": list(before_origin),
            "dx_mm": dx,
            "dy_mm": dy,
            "vector_length_mm": vector,
            "max_vector_mm": MAX_VECTOR_MM,
            "api7_path_name": api7_path,
            "api5_file_name": api5_path,
            "api5_is_active": api5_is_active,
            "api5_part_count": api5_count,
            "api5_component_index": api5_row["index"],
            "api5_component_name": api5_row["name"],
            "api5_component_file": api5_row["file_name"],
            "mapping_mode": mapping_mode,
            "mapping_verified": True,
            "positioner_type": "pnMove",
            "positioner_value": 0,
            "apply": apply_change,
            "saved": False,
        }

        if not apply_change:
            result.update(
                {
                    "dry_run": True,
                    "changed": False,
                }
            )
            return ok_func(
                ACTION,
                result,
            )

        target = (
            before_origin[0] + dx,
            before_origin[1] + dy,
            before_origin[2],
        )

        move_meta = _move_component_origin_xy(
            doc5,
            app5,
            api5_part,
            before_origin,
            target,
        )

        time.sleep(0.35)

        info_after = component_info(
            session,
            selector,
        )

        if str(info_after.get("name") or "").strip() != structured_name:
            raise RuntimeError(
                "geometry_postcondition:component identity changed"
            )

        after_origin = _origin_from_info(
            info_after
        )

        if not _close_enough(
            after_origin,
            target,
        ):
            rollback = {
                "attempted": False,
                "verified": False,
            }

            try:
                rollback["attempted"] = True

                _move_component_origin_xy(
                    doc5,
                    app5,
                    api5_part,
                    after_origin,
                    before_origin,
                )

                time.sleep(0.35)

                restored = _origin_from_info(
                    component_info(
                        session,
                        selector,
                    )
                )

                rollback["restored_origin_mm"] = list(restored)
                rollback["verified"] = _close_enough(
                    restored,
                    before_origin,
                )

            except Exception as rollback_exc:
                rollback["error"] = (
                    type(rollback_exc).__name__
                    + ":"
                    + str(rollback_exc)
                )

            raise RuntimeError(
                "geometry_postcondition_failed:"
                + " before="
                + repr(before_origin)
                + " expected="
                + repr(target)
                + " actual="
                + repr(after_origin)
                + " rollback="
                + repr(rollback)
            )

        result.update(
            {
                "dry_run": False,
                "changed": True,
                "expected_name_verified": True,
                "expected_after_origin_mm": list(target),
                "after_origin_mm": list(after_origin),
                "delta_verified": True,
                "tolerance_mm": TOLERANCE_MM,
                "move_meta": move_meta,
                "saved": False,
            }
        )

        return ok_func(
            ACTION,
            result,
        )

    registry[ACTION] = action