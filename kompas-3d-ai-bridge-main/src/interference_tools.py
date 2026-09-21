from __future__ import annotations

from pathlib import Path

from view_tools import _active_api5


INTERSECTION_TYPE_LEGEND = {
    1: "itTangentPoint",
    2: "itTangentCurve",
    3: "itTangentSurface",
    4: "itBody",
}


def _norm_out(value, expected, label):
    seq = list(value) if isinstance(value, (tuple, list)) else [value]

    if len(seq) == expected + 1 and isinstance(seq[0], bool):
        if not seq[0]:
            raise RuntimeError(label + "_valid_false:" + repr(seq))
        seq = seq[1:]

    if len(seq) != expected:
        raise RuntimeError(label + "_unexpected_shape:" + repr(seq))

    return [float(x) for x in seq]


def _body_bbox(body):
    return _norm_out(
        body.GetGabarit(
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ),
        6,
        "GetGabarit",
    )


def _bbox_overlap_info(a, b, tolerance=1e-7):
    dx = min(a[3], b[3]) - max(a[0], b[0])
    dy = min(a[4], b[4]) - max(a[1], b[1])
    dz = min(a[5], b[5]) - max(a[2], b[2])

    return {
        "dx": dx,
        "dy": dy,
        "dz": dz,
        "touch_or_overlap": (
            dx >= -tolerance
            and dy >= -tolerance
            and dz >= -tolerance
        ),
        "positive_volume_overlap": (
            dx > tolerance
            and dy > tolerance
            and dz > tolerance
        ),
    }


def _safe_name(part, fallback):
    for name in ("name", "Name", "GetName"):
        try:
            value = getattr(part, name)
            value = value() if callable(value) else value
            if value:
                return str(value)
        except Exception:
            pass
    return fallback


def _intersection_result(obj, check_tangent):
    if obj is None:
        return {
            "present": False,
            "count": 0,
            "types": [],
            "type_names": [],
            "check_tangent": bool(check_tangent),
        }

    count = int(obj.GetCount())
    types = [
        int(obj.GetIntersectionType(i))
        for i in range(count)
    ]

    return {
        "present": True,
        "count": count,
        "types": types,
        "type_names": [
            INTERSECTION_TYPE_LEGEND.get(
                t,
                f"unknown_{t}",
            )
            for t in types
        ],
        "check_tangent": bool(check_tangent),
    }


def _native_check(body_a, body_b, check_tangent):
    raw = body_a.CheckIntersectionWithBody(
        body_b,
        bool(check_tangent),
    )
    return _intersection_result(
        raw,
        check_tangent,
    )


def _component(d3, selector):
    parts = d3.PartCollection(True)
    if parts is None:
        raise RuntimeError(
            "PartCollection(True)_returned_none"
        )

    count = int(parts.GetCount())
    index = int(selector)

    if index < 0 or index >= count:
        raise LookupError(
            f"selector_out_of_range:{index}/{count}"
        )

    part = parts.GetByIndex(index)

    if part is None:
        raise RuntimeError(
            f"PartCollection.GetByIndex({index})_returned_none"
        )

    bodies = part.BodyCollection()
    body_count = (
        int(bodies.GetCount())
        if bodies is not None
        else 0
    )

    rows = []

    for bi in range(body_count):
        body = bodies.GetByIndex(bi)

        if body is None:
            continue

        rows.append({
            "body_index": bi,
            "body": body,
            "bbox": _body_bbox(body),
        })

    return {
        "selector": index,
        "name": _safe_name(
            part,
            f"selector_{index}",
        ),
        "body_count": body_count,
        "bodies": rows,
        "top_level_part_count": count,
    }


def _component_pair(
    comp_a,
    comp_b,
    check_contact,
    include_body_pairs,
    max_body_pair_details,
):
    body_pairs_total = (
        len(comp_a["bodies"])
        * len(comp_b["bodies"])
    )

    bbox_rejected = 0
    bbox_candidates = 0
    native_checks = 0
    native_errors = []
    body_pair_details = []

    interference = False
    contact_detected = False

    for ba in comp_a["bodies"]:
        for bb in comp_b["bodies"]:
            overlap = _bbox_overlap_info(
                ba["bbox"],
                bb["bbox"],
            )

            if not overlap["touch_or_overlap"]:
                bbox_rejected += 1

                if (
                    include_body_pairs
                    and len(body_pair_details)
                    < max_body_pair_details
                ):
                    body_pair_details.append({
                        "body_a": ba["body_index"],
                        "body_b": bb["body_index"],
                        "bbox_quick_reject": True,
                        "bbox_overlap": overlap,
                        "interference": False,
                        "contact_detected": False,
                    })

                continue

            bbox_candidates += 1

            strict = None
            tangent = None
            pair_interference = False
            pair_contact = False

            try:
                strict = _native_check(
                    ba["body"],
                    bb["body"],
                    False,
                )
                native_checks += 1
                pair_interference = (
                    4 in strict["types"]
                )
            except Exception as exc:
                native_errors.append({
                    "body_a": ba["body_index"],
                    "body_b": bb["body_index"],
                    "check_tangent": False,
                    "error": (
                        f"{type(exc).__name__}:{exc}"
                    ),
                })

            if check_contact:
                try:
                    tangent = _native_check(
                        ba["body"],
                        bb["body"],
                        True,
                    )
                    native_checks += 1
                    pair_contact = (
                        tangent["count"] > 0
                    )
                except Exception as exc:
                    native_errors.append({
                        "body_a": ba["body_index"],
                        "body_b": bb["body_index"],
                        "check_tangent": True,
                        "error": (
                            f"{type(exc).__name__}:{exc}"
                        ),
                    })

            interference = (
                interference
                or pair_interference
            )
            contact_detected = (
                contact_detected
                or pair_contact
            )

            if (
                include_body_pairs
                and len(body_pair_details)
                < max_body_pair_details
            ):
                body_pair_details.append({
                    "body_a": ba["body_index"],
                    "body_b": bb["body_index"],
                    "bbox_quick_reject": False,
                    "bbox_overlap": overlap,
                    "native_no_tangent": strict,
                    "native_with_tangent": tangent,
                    "interference": pair_interference,
                    "contact_detected": pair_contact,
                    "contact_only": (
                        pair_contact
                        and not pair_interference
                    ),
                })

    contact_only = (
        bool(contact_detected)
        and not bool(interference)
    )

    warnings = []

    if not comp_a["bodies"]:
        warnings.append(
            "component_a_has_no_direct_bodies"
        )

    if not comp_b["bodies"]:
        warnings.append(
            "component_b_has_no_direct_bodies"
        )

    if native_errors:
        warnings.append(
            "one_or_more_native_body_checks_failed"
        )

    row = {
        "selector_a": comp_a["selector"],
        "name_a": comp_a["name"],
        "selector_b": comp_b["selector"],
        "name_b": comp_b["name"],
        "body_count_a": len(comp_a["bodies"]),
        "body_count_b": len(comp_b["bodies"]),
        "body_pairs_total": body_pairs_total,
        "bbox_quick_reject_body_pairs": bbox_rejected,
        "bbox_candidate_body_pairs": bbox_candidates,
        "native_checks_attempted": native_checks,
        "native_check_error_count": len(native_errors),
        "native_check_errors": native_errors,
        "interference": bool(interference),
        "contact_detected": bool(contact_detected),
        "contact_only": bool(contact_only),
        "intersection_volume_mm3": None,
        "intersection_volume_reliable": False,
        "volume_limitation": (
            "IBody.CheckIntersectionWithBody / "
            "IIntersectionResult exposes "
            "intersection count/types, not "
            "intersection volume."
        ),
        "warnings": warnings,
    }

    if include_body_pairs:
        row["body_pair_details"] = (
            body_pair_details
        )
        row["body_pair_details_truncated"] = (
            (
                bbox_rejected
                + bbox_candidates
            )
            > len(body_pair_details)
        )

    return row


def interference_check(
    session,
    selector_a=None,
    selector_b=None,
    selectors=None,
    check_contact=True,
    include_body_pairs=False,
    max_body_pair_details=20,
):
    """
    Read-only KOMPAS native interference/contact check.

    Accept either:
      - selector_a + selector_b
      - selectors=[...], pairwise set check

    BBox quick-reject runs before every native body-body check.
    """
    app5, d3, api7_path, api5_path, is_active = (
        _active_api5(session)
    )

    if selectors is not None:
        selected = [
            int(x)
            for x in selectors
        ]

        if len(selected) < 2:
            raise ValueError(
                "selectors must contain at least 2 components"
            )

        if len(selected) > 32:
            raise ValueError(
                "selectors is limited to 32 components per call"
            )
    else:
        if (
            selector_a is None
            or selector_b is None
        ):
            raise ValueError(
                "provide selector_a+selector_b "
                "or selectors[]"
            )

        selected = [
            int(selector_a),
            int(selector_b),
        ]

    if len(set(selected)) != len(selected):
        raise ValueError(
            "duplicate component selectors are not allowed"
        )

    max_body_pair_details = max(
        0,
        min(
            int(max_body_pair_details),
            200,
        ),
    )

    components = {
        selector: _component(
            d3,
            selector,
        )
        for selector in selected
    }

    pair_results = []

    for i in range(len(selected)):
        for j in range(
            i + 1,
            len(selected),
        ):
            pair_results.append(
                _component_pair(
                    components[selected[i]],
                    components[selected[j]],
                    bool(check_contact),
                    bool(include_body_pairs),
                    max_body_pair_details,
                )
            )

    interference_pairs = [
        row
        for row in pair_results
        if row["interference"]
    ]

    contact_only_pairs = [
        row
        for row in pair_results
        if row["contact_only"]
    ]

    native_error_count = sum(
        int(row["native_check_error_count"])
        for row in pair_results
    )

    bbox_rejected = sum(
        int(
            row[
                "bbox_quick_reject_body_pairs"
            ]
        )
        for row in pair_results
    )

    bbox_candidates = sum(
        int(
            row[
                "bbox_candidate_body_pairs"
            ]
        )
        for row in pair_results
    )

    native_checks = sum(
        int(row["native_checks_attempted"])
        for row in pair_results
    )

    return {
        "requested_action": {
            "selector_a": selector_a,
            "selector_b": selector_b,
            "selectors": selected,
            "check_contact": bool(
                check_contact
            ),
            "include_body_pairs": bool(
                include_body_pairs
            ),
            "max_body_pair_details": (
                max_body_pair_details
            ),
        },
        "active_document": (
            api7_path
            or str(session.active_path() or "")
        ),
        "target": {
            "selectors": selected,
            "pair_count": len(pair_results),
        },
        "pair_results": pair_results,
        "summary": {
            "component_count": len(selected),
            "component_pair_count": len(
                pair_results
            ),
            "interference_pair_count": len(
                interference_pairs
            ),
            "contact_only_pair_count": len(
                contact_only_pairs
            ),
            "any_interference": bool(
                interference_pairs
            ),
            "any_contact_only": bool(
                contact_only_pairs
            ),
            "bbox_quick_reject_body_pairs": (
                bbox_rejected
            ),
            "bbox_candidate_body_pairs": (
                bbox_candidates
            ),
            "native_checks_attempted": (
                native_checks
            ),
            "native_check_error_count": (
                native_error_count
            ),
        },
        "intersection_type_legend": {
            str(k): v
            for k, v in (
                INTERSECTION_TYPE_LEGEND.items()
            )
        },
        "intersection_volume_mm3": None,
        "intersection_volume_reliable": False,
        "volume_limitation": (
            "The native API path used here "
            "does not expose a reliable "
            "intersection volume."
        ),
        "verification": {
            "bbox_quick_reject_executed": True,
            "native_method": (
                "IBody.CheckIntersectionWithBody"
            ),
            "native_result_interface": (
                "IIntersectionResult"
            ),
            "strict_interference_type": (
                "itBody (4)"
            ),
            "contact_check_enabled": bool(
                check_contact
            ),
            "api5_api7_same_document": (
                (not api7_path)
                or (not api5_path)
                or (
                    Path(api7_path)
                    .name.lower()
                    == Path(api5_path)
                    .name.lower()
                )
            ),
        },
        "warnings": (
            [
                "one_or_more_native_body_checks_failed"
            ]
            if native_error_count
            else []
        ),
        "read_only": True,
        "save_required": False,
        "saved": False,
        "model_geometry_changed": False,
    }
