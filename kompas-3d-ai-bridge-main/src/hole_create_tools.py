from __future__ import annotations

import math
import shutil
import uuid
from pathlib import Path

from view_tools import _active_api5


TOL = 1e-3
PLANE_TYPES = {
    "XOY": 1,
}
DIRECTION_TYPES = {
    "normal": 0,
    "reverse": 1,
    "both": 2,
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


def _number(value, name):
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(
            name + "_must_be_number"
        ) from exc


def _positive(value, name):
    v = _number(value, name)

    if not (v > 0.0):
        raise ValueError(
            name + "_must_be_positive"
        )

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
        raise RuntimeError(
            "Document3D_returned_none"
        )

    if not bool(
        doc.Open(str(path), False)
    ):
        raise RuntimeError(
            "ksDocument3D.Open_returned_false:"
            + str(path)
        )

    return doc


def _top_part(doc3d):
    part = doc3d.GetPart(-1)

    if part is None:
        raise RuntimeError(
            "GetPart(pTop_Part=-1)_returned_none"
        )

    return part


def _norm_out(value, expected, label):
    seq = (
        list(value)
        if isinstance(value, (tuple, list))
        else [value]
    )

    if (
        len(seq) == expected + 1
        and isinstance(seq[0], bool)
    ):
        if not seq[0]:
            raise RuntimeError(
                label + "_valid_false:"
                + repr(seq)
            )
        seq = seq[1:]

    if len(seq) != expected:
        raise RuntimeError(
            label + "_unexpected_shape:"
            + repr(seq)
        )

    return [float(x) for x in seq]


def _call_out(
    obj,
    name,
    out_count,
    *tail,
):
    fn = getattr(obj, name)

    return _norm_out(
        fn(
            *(
                [0.0] * out_count
                + list(tail)
            )
        ),
        out_count,
        name,
    )


def _body_and_bbox(part):
    bodies = part.BodyCollection()

    if bodies is None:
        raise RuntimeError(
            "BodyCollection_returned_none"
        )

    count = int(
        bodies.GetCount()
    )

    if count != 1:
        raise RuntimeError(
            "expected_exactly_one_body_got_"
            + str(count)
        )

    body = bodies.GetByIndex(0)

    if body is None:
        raise RuntimeError(
            "body_0_returned_none"
        )

    bbox = _call_out(
        body,
        "GetGabarit",
        6,
    )

    return body, bbox


def _placement_info(place):
    result = {
        "origin": None,
        "axis_z": None,
        "errors": [],
    }

    try:
        result["origin"] = _call_out(
            place,
            "GetOrigin",
            3,
        )
    except Exception as exc:
        result["errors"].append(
            "GetOrigin:"
            + f"{type(exc).__name__}:{exc}"
        )

    try:
        result["axis_z"] = _call_out(
            place,
            "GetAxis",
            3,
            2,
        )
    except Exception as exc:
        result["errors"].append(
            "GetAxis(z):"
            + f"{type(exc).__name__}:{exc}"
        )

    return result


def _circle_edge_rows(face):
    edges = face.EdgeCollection()

    if edges is None:
        return []

    count = int(
        edges.GetCount()
    )

    rows = []

    for ei in range(count):
        edge = edges.GetByIndex(ei)

        if edge is None:
            continue

        try:
            if not bool(
                edge.IsCircle()
            ):
                continue
        except Exception:
            continue

        row = {
            "edge_index": ei,
            "radius": None,
            "diameter": None,
            "curve_bbox": None,
            "center": None,
            "axis_origin": None,
            "axis_vector": None,
            "placement_errors": [],
        }

        try:
            curve = edge.GetCurve3D()

            if curve is None:
                raise RuntimeError(
                    "GetCurve3D_returned_none"
                )

            bbox = _call_out(
                curve,
                "GetGabarit",
                6,
            )

            row["curve_bbox"] = bbox
            row["center"] = [
                (bbox[0] + bbox[3]) / 2.0,
                (bbox[1] + bbox[4]) / 2.0,
                (bbox[2] + bbox[5]) / 2.0,
            ]

            param = (
                curve.GetCurveParam()
            )

            if param is not None:
                try:
                    row["radius"] = float(
                        param.radius
                    )
                except Exception:
                    try:
                        row["radius"] = float(
                            param.GetRadius()
                        )
                    except Exception:
                        pass

                if (
                    row["radius"]
                    is not None
                ):
                    row["diameter"] = (
                        2.0
                        * row["radius"]
                    )

                try:
                    place = (
                        param.GetPlacement()
                    )

                    if place is not None:
                        pi = (
                            _placement_info(
                                place
                            )
                        )

                        row[
                            "axis_origin"
                        ] = pi[
                            "origin"
                        ]

                        row[
                            "axis_vector"
                        ] = pi[
                            "axis_z"
                        ]

                        row[
                            "placement_errors"
                        ] = pi[
                            "errors"
                        ]
                except Exception as exc:
                    row[
                        "placement_error"
                    ] = (
                        f"{type(exc).__name__}:{exc}"
                    )

        except Exception as exc:
            row[
                "curve_error"
            ] = (
                f"{type(exc).__name__}:{exc}"
            )

        rows.append(
            row
        )

    return rows


def _cylinders(part):
    body, bbox = _body_and_bbox(
        part
    )

    faces = body.FaceCollection()

    count = (
        int(faces.GetCount())
        if faces is not None
        else 0
    )

    rows = []

    for fi in range(count):
        face = faces.GetByIndex(fi)

        if face is None:
            continue

        try:
            if not bool(
                face.IsCylinder()
            ):
                continue
        except Exception:
            continue

        row = {
            "body_index": 0,
            "face_index": fi,
            "surface_height": None,
            "radius": None,
            "diameter": None,
            "surface_axis_origin": None,
            "surface_axis_vector": None,
            "surface_placement_errors": [],
            "circle_edges": [],
        }

        try:
            h, r = _norm_out(
                face.GetCylinderParam(
                    0.0,
                    0.0,
                ),
                2,
                "GetCylinderParam",
            )

            row[
                "surface_height"
            ] = h
            row["radius"] = r
            row["diameter"] = (
                2.0 * r
            )
        except Exception as exc:
            row[
                "parameter_error"
            ] = (
                f"{type(exc).__name__}:{exc}"
            )

        # Keep analytic-cylinder placement as diagnostic evidence only.
        # The verified authoritative axis readback for CAP2-A7 is the
        # placement of the real circular boundary curves below.
        try:
            surface = (
                face.GetSurface()
            )

            param = (
                surface.GetSurfaceParam()
                if surface is not None
                else None
            )

            place = (
                param.GetPlacement()
                if param is not None
                else None
            )

            if place is not None:
                pi = _placement_info(
                    place
                )

                row[
                    "surface_axis_origin"
                ] = pi["origin"]

                row[
                    "surface_axis_vector"
                ] = pi["axis_z"]

                row[
                    "surface_placement_errors"
                ] = pi["errors"]

        except Exception as exc:
            row[
                "surface_param_error"
            ] = (
                f"{type(exc).__name__}:{exc}"
            )

        row[
            "circle_edges"
        ] = _circle_edge_rows(
            face
        )

        rows.append(
            row
        )

    return rows, bbox


def _diameter_matches(
    rows,
    diameter_mm,
):
    return [
        row
        for row in rows
        if (
            row.get(
                "diameter"
            )
            is not None
            and abs(
                float(
                    row["diameter"]
                )
                - diameter_mm
            )
            <= max(
                TOL,
                diameter_mm * 1e-6,
            )
        )
    ]


def _unit(vec):
    if (
        not isinstance(
            vec,
            (tuple, list),
        )
        or len(vec) != 3
    ):
        return None

    try:
        values = [
            float(x)
            for x in vec
        ]
    except Exception:
        return None

    mag = math.sqrt(
        sum(
            x * x
            for x in values
        )
    )

    if mag <= 1e-12:
        return None

    return [
        x / mag
        for x in values
    ]


def _dedupe_levels(
    values,
    tolerance,
):
    result = []

    for value in sorted(
        float(x)
        for x in values
    ):
        if (
            not result
            or abs(
                value
                - result[-1]
            )
            > tolerance
        ):
            result.append(
                value
            )

    return result


def _edge_geometry_candidate(
    row,
    *,
    diameter_mm,
    center_u_mm,
    center_v_mm,
    bbox,
):
    circle_edges = []

    for edge in (
        row.get(
            "circle_edges"
        )
        or []
    ):
        edge_diameter = edge.get(
            "diameter"
        )

        if (
            edge_diameter is not None
            and abs(
                float(
                    edge_diameter
                )
                - diameter_mm
            )
            > max(
                TOL,
                diameter_mm * 1e-6,
            )
        ):
            continue

        center = edge.get(
            "center"
        )
        curve_bbox = edge.get(
            "curve_bbox"
        )

        if (
            not isinstance(center, list)
            or len(center) != 3
            or not isinstance(curve_bbox, list)
            or len(curve_bbox) != 6
        ):
            continue

        edge_copy = dict(edge)

        edge_copy[
            "center_xy_error_mm"
        ] = math.hypot(
            float(center[0])
            - center_u_mm,
            float(center[1])
            - center_v_mm,
        )

        edge_copy[
            "z_plane_span_mm"
        ] = abs(
            float(curve_bbox[5])
            - float(curve_bbox[2])
        )

        circle_edges.append(
            edge_copy
        )

    if len(circle_edges) < 2:
        return None

    center_errors = [
        float(
            edge[
                "center_xy_error_mm"
            ]
        )
        for edge in circle_edges
    ]

    if max(center_errors) > TOL:
        return None

    # CAP2-A7 currently supports XOY only.
    # A real circular boundary of an XOY-normal hole must lie in a
    # constant-Z plane. This geometric condition is the authoritative
    # axis proof, not the unstable COM placement axis from a fresh cut.
    if any(
        float(
            edge[
                "z_plane_span_mm"
            ]
        )
        > TOL
        for edge in circle_edges
    ):
        return None

    z_levels = _dedupe_levels(
        [
            float(
                edge["center"][2]
            )
            for edge in circle_edges
        ],
        TOL,
    )

    if len(z_levels) < 2:
        return None

    z_min = float(bbox[2])
    z_max = float(bbox[5])

    edge_z_min = min(z_levels)
    edge_z_max = max(z_levels)

    trimmed_depth = (
        edge_z_max
        - edge_z_min
    )

    touch_min = (
        abs(
            edge_z_min
            - z_min
        )
        <= TOL
    )

    touch_max = (
        abs(
            edge_z_max
            - z_max
        )
        <= TOL
    )

    if (
        touch_min
        and touch_max
    ):
        through_blind = "through"
    elif (
        touch_min
        or touch_max
    ):
        through_blind = "blind"
    else:
        through_blind = "unknown"

    direct_axis_diagnostics = [
        {
            "edge_index": edge.get(
                "edge_index"
            ),
            "placement_axis_vector": edge.get(
                "axis_vector"
            ),
            "placement_axis_origin": edge.get(
                "axis_origin"
            ),
        }
        for edge in circle_edges
    ]

    axis_vector = [
        0.0,
        0.0,
        1.0,
    ]

    axis_origin = [
        float(center_u_mm),
        float(center_v_mm),
        (
            edge_z_min
            + edge_z_max
        )
        / 2.0,
    ]

    return {
        "circle_edges": (
            circle_edges
        ),
        "edge_level_z_mm": (
            z_levels
        ),
        "trimmed_depth_mm": (
            trimmed_depth
        ),
        "touches_body_z_min": (
            touch_min
        ),
        "touches_body_z_max": (
            touch_max
        ),
        "through_blind_readback": (
            through_blind
        ),
        "center_xy_error_mm": (
            max(
                center_errors
            )
        ),
        "axis_vector": (
            axis_vector
        ),
        "axis_origin": (
            axis_origin
        ),
        "axis_z_alignment_abs": 1.0,
        "axis_source": (
            "XOY_work_plane_normal_verified_by_constant_Z_circle_edge_gabarits"
        ),
        "circle_edge_planes_verified": True,
        "direct_COM_axis_diagnostics": (
            direct_axis_diagnostics
        ),
    }


def _readback(
    part,
    *,
    diameter_mm,
    center_u_mm,
    center_v_mm,
    plane,
    mode,
    depth_mm,
    before_matching_count,
):
    rows, bbox = _cylinders(
        part
    )

    matches = _diameter_matches(
        rows,
        diameter_mm,
    )

    if (
        len(matches)
        <= before_matching_count
    ):
        raise RuntimeError(
            "new_cylindrical_hole_not_detected:"
            f"before={before_matching_count},"
            f" after={len(matches)},"
            f" diameter={diameter_mm}"
        )

    candidates = []

    for row in matches:
        edge_geometry = (
            _edge_geometry_candidate(
                row,
                diameter_mm=diameter_mm,
                center_u_mm=center_u_mm,
                center_v_mm=center_v_mm,
                bbox=bbox,
            )
        )

        if (
            edge_geometry
            is None
        ):
            continue

        candidate = dict(
            row
        )

        candidate.update(
            edge_geometry
        )

        candidates.append(
            candidate
        )

    if not candidates:
        raise RuntimeError(
            "matching_cylinder_has_no_verified_circle_edge_geometry"
        )

    candidates.sort(
        key=lambda row: (
            row.get(
                "center_xy_error_mm"
            )
            if row.get(
                "center_xy_error_mm"
            )
            is not None
            else 999999.0,
            -(
                row.get(
                    "axis_z_alignment_abs"
                )
                or 0.0
            ),
        )
    )

    selected = (
        candidates[0]
    )

    if (
        selected[
            "center_xy_error_mm"
        ]
        > TOL
    ):
        raise RuntimeError(
            "hole_center_readback_mismatch:"
            + repr(
                selected[
                    "center_xy_error_mm"
                ]
            )
        )

    if (
        selected.get(
            "axis_z_alignment_abs"
        )
        is None
    ):
        raise RuntimeError(
            "hole_axis_vector_not_readable_from_circle_edge_placement"
        )

    if (
        selected[
            "axis_z_alignment_abs"
        ]
        < 0.999
    ):
        raise RuntimeError(
            "hole_axis_readback_not_parallel_to_XOY_normal:"
            + repr(
                selected[
                    "axis_z_alignment_abs"
                ]
            )
        )

    actual_mode = (
        selected[
            "through_blind_readback"
        ]
    )

    if (
        mode == "through"
        and actual_mode
        != "through"
    ):
        raise RuntimeError(
            "through_hole_edge_readback_not_through:"
            + repr(
                {
                    "levels": selected.get(
                        "edge_level_z_mm"
                    ),
                    "body_z": [
                        bbox[2],
                        bbox[5],
                    ],
                    "classification": actual_mode,
                }
            )
        )

    if (
        mode == "blind"
        and actual_mode
        != "blind"
    ):
        raise RuntimeError(
            "blind_hole_edge_readback_not_blind:"
            + repr(
                {
                    "levels": selected.get(
                        "edge_level_z_mm"
                    ),
                    "body_z": [
                        bbox[2],
                        bbox[5],
                    ],
                    "classification": actual_mode,
                }
            )
        )

    if (
        mode == "blind"
        and depth_mm is not None
        and abs(
            float(
                selected[
                    "trimmed_depth_mm"
                ]
            )
            - float(
                depth_mm
            )
        )
        > max(
            TOL,
            float(
                depth_mm
            )
            * 1e-5,
        )
    ):
        raise RuntimeError(
            "blind_depth_edge_readback_mismatch:"
            + repr(
                selected[
                    "trimmed_depth_mm"
                ]
            )
        )

    return {
        "cylindrical_face_count": len(
            rows
        ),
        "matching_diameter_count": len(
            matches
        ),
        "selected_cylinder": (
            selected
        ),
        "outer_bbox": bbox,
        "outer_dims": [
            bbox[3] - bbox[0],
            bbox[4] - bbox[1],
            bbox[5] - bbox[2],
        ],
        "center_verified": True,
        "axis_verified": True,
        "diameter_verified": True,
        "through_blind_verified": True,
        "readback_method": (
            "cylinder diameter + circular boundary edge "
            "GetCurve3D/GetGabarit/GetCurveParam/GetPlacement"
        ),
        "surface_height_used_for_depth": False,
    }


def _create_cut(
    part,
    *,
    plane,
    center_u_mm,
    center_v_mm,
    diameter_mm,
    mode,
    depth_mm,
    direction,
    body_bbox,
):
    plane_type = PLANE_TYPES[
        plane
    ]

    plane_entity = (
        part.GetDefaultEntity(
            plane_type
        )
    )

    if plane_entity is None:
        raise RuntimeError(
            "default_work_plane_none:"
            + plane
        )

    sketch = part.NewEntity(5)

    if sketch is None:
        raise RuntimeError(
            "NewEntity(o3d_sketch)_returned_none"
        )

    sketch_def = (
        sketch.GetDefinition()
    )

    if sketch_def is None:
        raise RuntimeError(
            "sketch_GetDefinition_none"
        )

    if not bool(
        sketch_def.SetPlane(
            plane_entity
        )
    ):
        raise RuntimeError(
            "sketch_SetPlane_failed"
        )

    if not bool(
        sketch.Create()
    ):
        raise RuntimeError(
            "sketch_Create_failed"
        )

    doc2 = (
        sketch_def.BeginEdit()
    )

    if doc2 is None:
        raise RuntimeError(
            "sketch_BeginEdit_returned_none"
        )

    circle_ref = int(
        doc2.ksCircle(
            center_u_mm,
            center_v_mm,
            diameter_mm / 2.0,
            1,
        )
        or 0
    )

    if circle_ref == 0:
        raise RuntimeError(
            "ksCircle_failed"
        )

    end_edit = (
        sketch_def.EndEdit()
    )

    if end_edit is False:
        raise RuntimeError(
            "sketch_EndEdit_returned_false"
        )

    cut = part.NewEntity(26)

    if cut is None:
        raise RuntimeError(
            "NewEntity(o3d_cutExtrusion=26)_returned_none"
        )

    cut_def = (
        cut.GetDefinition()
    )

    if cut_def is None:
        raise RuntimeError(
            "cutExtrusion_GetDefinition_none"
        )

    if not bool(
        cut_def.SetSketch(
            sketch
        )
    ):
        raise RuntimeError(
            "cutExtrusion_SetSketch_failed"
        )

    direction_value = (
        DIRECTION_TYPES[
            direction
        ]
    )

    try:
        cut_def.directionType = (
            direction_value
        )
    except Exception:
        cut_def.SetDirectionType(
            direction_value
        )

    normal_span = abs(
        float(body_bbox[5])
        - float(body_bbox[2])
    )

    if mode == "through":
        effective_depth = (
            normal_span
            + max(
                10.0,
                normal_span * 0.5,
            )
        )
    else:
        effective_depth = (
            float(depth_mm)
        )

    if direction in (
        "normal",
        "both",
    ):
        if not bool(
            cut_def.SetSideParam(
                True,
                0,
                effective_depth,
                0.0,
                False,
            )
        ):
            raise RuntimeError(
                "cutExtrusion_forward_SetSideParam_failed"
            )

    if direction in (
        "reverse",
        "both",
    ):
        if not bool(
            cut_def.SetSideParam(
                False,
                0,
                effective_depth,
                0.0,
                False,
            )
        ):
            raise RuntimeError(
                "cutExtrusion_reverse_SetSideParam_failed"
            )

    if not bool(
        cut.Create()
    ):
        raise RuntimeError(
            "cutExtrusion_Create_failed"
        )

    return {
        "plane": plane,
        "plane_type": plane_type,
        "circle_ref": circle_ref,
        "cut_extrusion_type": 26,
        "direction": direction,
        "direction_type_value": (
            direction_value
        ),
        "mode": mode,
        "requested_depth_mm": (
            depth_mm
        ),
        "effective_cut_depth_mm": (
            effective_depth
        ),
    }


def part_add_hole(
    session,
    filename,
    diameter_mm,
    center_u_mm=0.0,
    center_v_mm=0.0,
    plane="XOY",
    mode="through",
    depth_mm=None,
    direction="both",
):
    """
    Add one circular cut-extrusion hole to an existing native
    *_AGENT_COPY.m3d in approved work.

    Current verified work-plane support: XOY.
    Sketch coordinates:
      center_u -> X
      center_v -> Y

    mode:
      through - depth is calculated from body Z span and verified by
                cylinder-height readback
      blind   - requires depth_mm and verifies cylinder height < body span

    direction:
      normal=0, reverse=1, both=2 (KOMPAS Direction_Type)

    Safety:
      exact active target before Save, target transaction backup,
      Save -> Close -> Reopen, cylinder diameter/center/axis/depth readback,
      rollback exact target bytes on any failure, restore original AGENT_COPY.
    """
    filename = _validate_filename(
        filename
    )

    diameter_mm = _positive(
        diameter_mm,
        "diameter_mm",
    )

    center_u_mm = _number(
        center_u_mm,
        "center_u_mm",
    )
    center_v_mm = _number(
        center_v_mm,
        "center_v_mm",
    )

    plane = str(
        plane
    ).strip().upper()

    if plane not in PLANE_TYPES:
        raise ValueError(
            "plane_currently_must_be_XOY"
        )

    mode = str(
        mode
    ).strip().lower()

    if mode not in {
        "through",
        "blind",
    }:
        raise ValueError(
            "mode_must_be_through_or_blind"
        )

    direction = str(
        direction
    ).strip().lower()

    if (
        direction
        not in DIRECTION_TYPES
    ):
        raise ValueError(
            "direction_must_be_normal_reverse_or_both"
        )

    if mode == "blind":
        if depth_mm is None:
            raise ValueError(
                "blind_hole_requires_depth_mm"
            )

        depth_mm = _positive(
            depth_mm,
            "depth_mm",
        )
    else:
        if depth_mm is not None:
            raise ValueError(
                "depth_mm_only_valid_for_blind_mode"
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

    if (
        target.parent
        != approved_work
    ):
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
            "TXN_PART_HOLE_"
            + uuid.uuid4().hex
            + "_"
            + target.name
        )
    )

    shutil.copy2(
        target,
        txn_backup,
    )

    fixture = None
    reopened = None

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

        before_rows, body_bbox = (
            _cylinders(
                part
            )
        )

        before_matches = (
            _diameter_matches(
                before_rows,
                diameter_mm,
            )
        )

        before = {
            "cylindrical_face_count": len(
                before_rows
            ),
            "matching_diameter_count": len(
                before_matches
            ),
            "body_bbox": body_bbox,
        }

        build = _create_cut(
            part,
            plane=plane,
            center_u_mm=center_u_mm,
            center_v_mm=center_v_mm,
            diameter_mm=diameter_mm,
            mode=mode,
            depth_mm=depth_mm,
            direction=direction,
            body_bbox=body_bbox,
        )

        try:
            fixture.RebuildDocument()
        except Exception:
            pass

        after_create = _readback(
            part,
            diameter_mm=diameter_mm,
            center_u_mm=center_u_mm,
            center_v_mm=center_v_mm,
            plane=plane,
            mode=mode,
            depth_mm=depth_mm,
            before_matching_count=len(
                before_matches
            ),
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

        if not bool(
            fixture.Save()
        ):
            raise RuntimeError(
                "ksDocument3D.Save_returned_false"
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
                "reopened_target_not_exact_active:"
                + active_after_reopen
            )

        part2 = _top_part(
            reopened
        )

        after_reopen = _readback(
            part2,
            diameter_mm=diameter_mm,
            center_u_mm=center_u_mm,
            center_v_mm=center_v_mm,
            plane=plane,
            mode=mode,
            depth_mm=depth_mm,
            before_matching_count=len(
                before_matches
            ),
        )

        selected_before_save = (
            after_create[
                "selected_cylinder"
            ]
        )
        selected_after_reopen = (
            after_reopen[
                "selected_cylinder"
            ]
        )

        if abs(
            float(
                selected_before_save[
                    "diameter"
                ]
            )
            - float(
                selected_after_reopen[
                    "diameter"
                ]
            )
        ) > TOL:
            raise RuntimeError(
                "hole_diameter_changed_after_reopen"
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
                "diameter_mm": diameter_mm,
                "center_u_mm": center_u_mm,
                "center_v_mm": center_v_mm,
                "plane": plane,
                "mode": mode,
                "depth_mm": depth_mm,
                "direction": direction,
            },
            "active_document": restored,
            "target": str(target),
            "before": before,
            "after": after_reopen,
            "changed": True,
            "save_required": False,
            "saved": True,
            "warnings": [
                "native_IHole3D_feature_not_claimed; "
                "hole is a verified native cut-extrusion with "
                "cylindrical topology readback"
            ],
            "verification": {
                "work_plane": plane,
                "work_plane_support": "XOY_only_in_CAP2_A7",
                "native_sketch_circle": True,
                "native_cut_extrusion": True,
                "diameter_readback_verified": True,
                "center_readback_verified": True,
                "axis_readback_verified": True,
                "through_blind_readback_verified": True,
                "through_blind_readback_source": (
                    "trimmed circular boundary edge levels vs body bbox"
                ),
                "axis_readback_source": (
                    "XOY work-plane normal verified by constant-Z circular edge gabarits"
                ),
                "direct_COM_axis_is_authoritative": False,
                "analytic_surface_height_used_for_depth": False,
                "save_close_reopen_verified": True,
                "exact_target_active_before_save": True,
                "transaction_backup_created": True,
                "transaction_backup_removed_after_success": True,
                "original_active_restored": True,
                "original_saved": False,
                "original_geometry_changed": False,
                "native_IHole3D_feature": False,
            },
            "actual": {
                "diameter_mm": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "diameter"
                    )
                ),
                "axis_origin": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "axis_origin"
                    )
                ),
                "axis_vector": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "axis_vector"
                    )
                ),
                "axis_source": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "axis_source"
                    )
                ),
                "center_xyz": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "axis_origin"
                    )
                ),
                "center_xy_error_mm": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "center_xy_error_mm"
                    )
                ),
                "through_blind": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "through_blind_readback"
                    )
                ),
                "trimmed_depth_mm": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "trimmed_depth_mm"
                    )
                ),
                "edge_level_z_mm": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "edge_level_z_mm"
                    )
                ),
                "surface_height_diagnostic_mm": (
                    after_reopen[
                        "selected_cylinder"
                    ].get(
                        "surface_height"
                    )
                ),
            },
            "native_build": build,
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
            if fixture is not None:
                _close_doc(
                    fixture
                )
        except Exception:
            pass

        try:
            original_d3.SetActive()
        except Exception:
            pass

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

        try:
            original_d3.SetActive()
        except Exception:
            pass

        raise
