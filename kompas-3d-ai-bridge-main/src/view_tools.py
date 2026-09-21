from __future__ import annotations
import time
from pathlib import Path
from config import load_config
from model_tools import _doc3d, find_component
from winui import ctrl_arrow, main_window_handle

# Official KOMPAS ksViewProjectionType values.
VIEW_TYPES = {
    "front": 1,
    "rear": 2,
    "top": 3,
    "bottom": 4,
    "left": 5,
    "right": 6,
    "iso": 7,
    "dimetric": 10,
}


def _api5_active_document3d(app5):
    """
    KompasObject.ActiveDocument3D is an Automation METHOD:
        LPDISPATCH ActiveDocument3D()
    pywin32 therefore may expose it as a callable.
    """
    member = app5.ActiveDocument3D
    d3 = member() if callable(member) else member
    if d3 is None:
        raise RuntimeError("no_active_3d_document_in_api5")
    return d3


def _api5_file_name(d3):
    for getter in (
        lambda: d3.fileName,
        lambda: d3.GetFileName(),
    ):
        try:
            value = getter()
            if value and not callable(value):
                return str(value)
        except Exception:
            pass
    return ""


def _api5_is_active(d3):
    try:
        member = d3.IsActive
        value = member() if callable(member) else member
        return bool(value)
    except Exception:
        return None


def _active_api5(session):
    """
    Obtain API5 active 3D document and verify it matches API7 active document
    when both paths are available.
    """
    app5 = session.api5()
    d3 = _api5_active_document3d(app5)

    api7_path = str(session.active_path() or "")
    api5_path = _api5_file_name(d3)
    is_active = _api5_is_active(d3)

    if api7_path and api5_path:
        if Path(api7_path).name.lower() != Path(api5_path).name.lower():
            raise RuntimeError(
                "api5_api7_active_document_mismatch: "
                f"api7={api7_path!r}; api5={api5_path!r}; api5_is_active={is_active}"
            )

    return app5, d3, api7_path, api5_path, is_active


def _projection_rows(coll):
    rows = []
    count = int(coll.GetCount())
    for i in range(count):
        p = coll.GetByIndex(i)
        try:
            ptype = int(p.GetViewProjectonType())
        except Exception:
            ptype = None
        try:
            current = bool(p.IsCurrent())
        except Exception:
            current = None
        rows.append({
            "index": i,
            "name": str(getattr(p, "name", "") or ""),
            "type": ptype,
            "current": current,
        })
    return rows


def projections(session):
    # Preserve the existing structured-tool contract:
    # engineering_capabilities wraps this list as {"projections": [...]}
    _, d3, _, _, _ = _active_api5(session)
    return _projection_rows(d3.GetViewProjectionCollection())


def _projection_by_type(coll, desired_type):
    count = int(coll.GetCount())
    for i in range(count):
        p = coll.GetByIndex(i)
        try:
            if int(p.GetViewProjectonType()) == int(desired_type):
                return i, p
        except Exception:
            pass
    return None, None


def _refresh(app5):
    try:
        result = int(app5.ksRefreshActiveWindow())
    except Exception as exc:
        raise RuntimeError(f"ksRefreshActiveWindow_failed: {exc}")
    if result == 0:
        raise RuntimeError("ksRefreshActiveWindow_returned_0")
    return result


def set_view(session, view):
    key = str(view).strip().lower()
    if key not in VIEW_TYPES:
        raise ValueError(f"unsupported_view: {view}; allowed={sorted(VIEW_TYPES)}")

    app5, d3, api7_path, api5_path, is_active = _active_api5(session)
    coll = d3.GetViewProjectionCollection()
    desired_type = VIEW_TYPES[key]
    idx, p = _projection_by_type(coll, desired_type)

    if p is None:
        raise RuntimeError(
            f"projection_type_not_available: view={key}, type={desired_type}, "
            f"available={_projection_rows(coll)}"
        )

    before = _projection_rows(coll)

    try:
        set_result = bool(p.SetCurrent())
    except Exception as exc:
        raise RuntimeError(
            f"projection_SetCurrent_exception: view={key}, type={desired_type}, index={idx}: {exc}"
        )
    if not set_result:
        raise RuntimeError(
            f"projection_SetCurrent_returned_false: view={key}, type={desired_type}, index={idx}"
        )

    deadline = time.time() + 1.5
    current_verified = False
    while time.time() < deadline:
        try:
            if bool(p.IsCurrent()):
                current_verified = True
                break
        except Exception:
            pass
        time.sleep(0.05)

    if not current_verified:
        raise RuntimeError(
            f"projection_did_not_become_current: view={key}, type={desired_type}, "
            f"index={idx}, after={_projection_rows(coll)}"
        )

    refresh_result = _refresh(app5)

    # COM state is already verified. This pause only lets the graphics thread
    # paint the new projection before viewport.capture reads pixels.
    time.sleep(0.55)

    after = _projection_rows(coll)
    current_types = [r["type"] for r in after if r["current"]]
    if desired_type not in current_types:
        raise RuntimeError(
            f"projection_current_verification_failed: view={key}, "
            f"wanted_type={desired_type}, current_types={current_types}, after={after}"
        )

    return {
        "view": key,
        "projection_index": idx,
        "projection_type": desired_type,
        "projection_name": str(getattr(p, "name", "") or ""),
        "set_current_result": set_result,
        "is_current_verified": True,
        "refresh_result": refresh_result,
        "api7_path_name": api7_path,
        "api5_file_name": api5_path,
        "api5_is_active": is_active,
        "before": before,
        "after": after,
    }


def fit(session):
    app5, d3, api7_path, api5_path, is_active = _active_api5(session)
    result = d3.ZoomPrevNextOrAll(2)
    refresh_result = _refresh(app5)
    time.sleep(0.35)
    return {
        "fit": True,
        "zoom_result": result,
        "refresh_result": refresh_result,
        "api": "API5.ZoomPrevNextOrAll(2)+ksRefreshActiveWindow",
        "api7_path_name": api7_path,
        "api5_file_name": api5_path,
        "api5_is_active": is_active,
    }


def rotate(session, horizontal_steps=0, vertical_steps=0):
    import math
    from pathlib import Path as _Path
    from PIL import Image, ImageChops, ImageStat
    from screenshot import capture

    cfg = load_config(session.root)
    vc = cfg.get("view", {})
    limit = int(vc.get("max_rotation_steps_per_call", 12))
    degrees_per_step = 6.0

    h = int(horizontal_steps)
    v = int(vertical_steps)

    if abs(h) > limit or abs(v) > limit:
        raise ValueError(f"rotation_steps_exceed_limit: max={limit}")

    if h == 0 and v == 0:
        return {
            "requested_action": {"horizontal_steps": 0, "vertical_steps": 0, "degrees_per_step": degrees_per_step},
            "target": "viewport",
            "changed": False,
            "save_required": False,
            "warnings": [],
            "verification": {"no_op": True},
            "method": "API5 vp_None + ksPlacement.SetAxes",
            "model_geometry_changed": False,
            "saved": False,
        }

    app5, d3, api7_path, api5_path, is_active = _active_api5(session)
    coll = d3.GetViewProjectionCollection()

    def parse_axis(value, label):
        seq = list(value) if isinstance(value, (tuple, list)) else [value]
        if len(seq) == 4 and isinstance(seq[0], bool):
            if not seq[0]:
                raise RuntimeError(label + "_valid_false:" + repr(seq))
            seq = seq[1:4]
        elif len(seq) == 4 and isinstance(seq[0], (int, float)) and seq[0] in (0, 1):
            if int(seq[0]) != 1:
                raise RuntimeError(label + "_valid_false:" + repr(seq))
            seq = seq[1:4]
        elif len(seq) >= 3:
            seq = seq[-3:]
        else:
            raise RuntimeError(label + "_unexpected_shape:" + repr(seq))
        return [float(seq[0]), float(seq[1]), float(seq[2])]

    def get_axis(place, axis_type):
        value = place.GetAxis(0.0, 0.0, 0.0, int(axis_type))
        return parse_axis(value, f"GetAxis(0,0,0,{axis_type})")

    def rot_y(vec, degrees):
        a = math.radians(float(degrees)); c = math.cos(a); sn = math.sin(a)
        x, y, z = vec
        return [c*x + sn*z, y, -sn*x + c*z]

    def rot_x(vec, degrees):
        a = math.radians(float(degrees)); c = math.cos(a); sn = math.sin(a)
        x, y, z = vec
        return [x, c*y - sn*z, sn*y + c*z]

    def rotate_vec(vec):
        q = list(vec)
        if h:
            q = rot_y(q, h * degrees_per_step)
        if v:
            q = rot_x(q, v * degrees_per_step)
        return q

    def max_delta(a, b):
        return max(abs(float(x) - float(y)) for x, y in zip(a, b))

    def image_diff(path_a, path_b):
        a = Image.open(path_a).convert("RGB")
        b = Image.open(path_b).convert("RGB")
        if a.size != b.size:
            raise RuntimeError(f"viewport_capture_size_changed:before={a.size} after={b.size}")
        diff = ImageChops.difference(a, b)
        stat = ImageStat.Stat(diff)
        means = [float(x) for x in stat.mean]
        box = diff.getbbox()
        return {
            "same_size": True,
            "size": list(a.size),
            "mean_channels": means,
            "mean_abs_diff": sum(means) / len(means),
            "bbox": list(box) if box else None,
        }

    rows = _projection_rows(coll)
    current = [row for row in rows if bool(row.get("current"))]

    if len(current) == 1:
        base_projection = coll.GetByIndex(int(current[0]["index"]))
        base_place = base_projection.GetPlacement()
        if base_place is None:
            raise RuntimeError("current_projection_GetPlacement_returned_none")
        before_x = get_axis(base_place, 0)
        before_y = get_axis(base_place, 1)
        base_source = {"mode": "current_predefined_projection", "projection": current[0]}
    elif len(current) == 0:
        stored = getattr(session, "_cap2_native_rotate_axes", None)
        if not isinstance(stored, dict):
            raise RuntimeError("no_current_predefined_projection_and_no_CAP2_rotation_state")
        idx, iso_projection = _projection_by_type(coll, VIEW_TYPES["iso"])
        if iso_projection is None:
            raise RuntimeError("iso_projection_unavailable_for_vp_None_base")
        base_place = iso_projection.GetPlacement()
        if base_place is None:
            raise RuntimeError("iso_projection_GetPlacement_returned_none")
        before_x = [float(q) for q in stored["x"]]
        before_y = [float(q) for q in stored["y"]]
        base_source = {"mode": "previous_CAP2_vp_None_state", "base_projection_index": idx}
    else:
        raise RuntimeError("multiple_current_predefined_projections:" + repr(current))

    after_x = rotate_vec(before_x)
    after_y = rotate_vec(before_y)

    shot_before = capture(session.root, viewport_only=True, label="cap2-native-rotate-before", session=session)

    temp = coll.NewViewProjection()
    if temp is None:
        raise RuntimeError("NewViewProjection_returned_none")
    if int(temp.GetViewProjectonType()) != -1:
        raise RuntimeError("NewViewProjection_is_not_vp_None")

    set_base = temp.SetPlacement(base_place)
    if set_base is not None and not bool(set_base):
        raise RuntimeError("vp_None_SetPlacement_base_returned_false")

    place = temp.GetPlacement()
    if place is None:
        raise RuntimeError("vp_None_GetPlacement_returned_none")

    set_axes = place.SetAxes(
        float(after_x[0]), float(after_x[1]), float(after_x[2]),
        float(after_y[0]), float(after_y[1]), float(after_y[2]),
    )
    if set_axes is not None and not bool(set_axes):
        raise RuntimeError("ksPlacement_SetAxes_returned_false")

    read_x = get_axis(place, 0)
    read_y = get_axis(place, 1)
    delta_x = max_delta(after_x, read_x)
    delta_y = max_delta(after_y, read_y)
    if delta_x > 1e-6 or delta_y > 1e-6:
        raise RuntimeError(f"SetAxes_readback_mismatch: dx={delta_x} dy={delta_y}")

    set_rotated = temp.SetPlacement(place)
    if set_rotated is not None and not bool(set_rotated):
        raise RuntimeError("vp_None_SetPlacement_rotated_returned_false")

    mutated = False
    try:
        set_current = temp.SetCurrent()
        if set_current is not None and not bool(set_current):
            raise RuntimeError("vp_None_SetCurrent_returned_false")
        mutated = True

        refresh_result = _refresh(app5)
        if refresh_result == 0:
            raise RuntimeError("ksRefreshActiveWindow_returned_0_after_rotate")
        time.sleep(0.55)

        shot_after = capture(session.root, viewport_only=True, label="cap2-native-rotate-after", session=session)
        diff = image_diff(shot_before["path"], shot_after["path"])
        if float(diff["mean_abs_diff"]) < 1.0:
            raise RuntimeError(f"native_rotate_visual_postcondition_failed:mean_abs_diff={diff['mean_abs_diff']}")
    except Exception:
        if mutated:
            try:
                rollback = coll.NewViewProjection()
                if rollback is not None:
                    rollback.SetPlacement(base_place)
                    rp = rollback.GetPlacement()
                    rp.SetAxes(
                        float(before_x[0]), float(before_x[1]), float(before_x[2]),
                        float(before_y[0]), float(before_y[1]), float(before_y[2]),
                    )
                    rollback.SetPlacement(rp)
                    rollback.SetCurrent()
                    _refresh(app5)
                    time.sleep(0.4)
                    session._cap2_native_rotate_axes = {"x": list(before_x), "y": list(before_y)}
            except Exception:
                pass
        raise

    session._cap2_native_rotate_axes = {"x": list(read_x), "y": list(read_y)}

    return {
        "requested_action": {
            "horizontal_steps": h,
            "vertical_steps": v,
            "degrees_per_step": degrees_per_step,
            "horizontal_degrees": h * degrees_per_step,
            "vertical_degrees": v * degrees_per_step,
        },
        "active_document": api7_path,
        "target": "viewport",
        "before": {"axis_x": list(before_x), "axis_y": list(before_y), "screenshot": shot_before, "base_source": base_source},
        "after": {"axis_x": list(read_x), "axis_y": list(read_y), "screenshot": shot_after},
        "changed": True,
        "save_required": False,
        "warnings": [],
        "verification": {
            "vp_none_type_verified": True,
            "set_axes_return": set_axes,
            "axis_x_readback_max_abs_delta": delta_x,
            "axis_y_readback_max_abs_delta": delta_y,
            "axis_readback_pass": True,
            "set_current_return": set_current,
            "refresh_result": refresh_result,
            "screenshot_diff": diff,
            "screenshot_diff_threshold": 1.0,
            "screenshot_diff_pass": True,
            "capture_target_before_verified": bool(shot_before.get("capture_target_verified")),
            "capture_target_after_verified": bool(shot_after.get("capture_target_verified")),
            "api5_api7_same_document": ((not api7_path) or (not api5_path) or (_Path(api7_path).name.lower() == _Path(api5_path).name.lower())),
        },
        "method": "API5 NewViewProjection(vp_None) + ksPlacement.SetAxes + SetCurrent + verified HWND screenshot diff",
        "api7_path_name": api7_path,
        "api5_file_name": api5_path,
        "api5_is_active": is_active,
        "model_geometry_changed": False,
        "saved": False,
    }


def _walk_parts(parent, prefix=None):
    prefix = list(prefix or [])
    try:
        count = int(parent.Parts.Count)
    except Exception:
        count = 0
    for i in range(count):
        part = parent.Parts.Part(i)
        path = prefix + [i]
        yield part, path
        yield from _walk_parts(part, path)


def _snapshot_visibility(top):
    return {
        "/".join(map(str, path)): bool(part.Hidden)
        for part, path in _walk_parts(top)
    }


def restore_visibility(session):
    _, top = _doc3d(session)
    snap = session.visibility_snapshot
    if not isinstance(snap, dict):
        for part, _ in _walk_parts(top):
            part.Hidden = False
            part.Update()
        session.visibility_snapshot = None
        return {"restored": True, "mode": "show_all"}

    restored = 0
    for part, path in _walk_parts(top):
        key = "/".join(map(str, path))
        if key in snap:
            part.Hidden = bool(snap[key])
            part.Update()
            restored += 1
    session.visibility_snapshot = None
    return {"restored": True, "mode": "recursive_snapshot", "count": restored}


def visibility(session, operation, selector=None):
    _, top = _doc3d(session)
    op = str(operation).lower()

    if op == "restore":
        return restore_visibility(session)

    if session.visibility_snapshot is None:
        session.visibility_snapshot = _snapshot_visibility(top)

    if op in ("hide", "show"):
        part, path = find_component(session, selector)
        part.Hidden = op == "hide"
        part.Update()
        return {
            "operation": op,
            "selector": selector,
            "tree_path": path,
            "hidden": bool(part.Hidden),
        }

    if op == "isolate":
        target, target_path = find_component(session, selector)
        changed = []
        for part, path in _walk_parts(top):
            is_ancestor_or_target = (
                target_path[:len(path)] == path
                if len(path) <= len(target_path)
                else False
            )
            is_descendant = (
                path[:len(target_path)] == target_path
                if len(path) > len(target_path)
                else False
            )
            if is_ancestor_or_target:
                desired = False
            elif is_descendant:
                desired = session.visibility_snapshot.get(
                    "/".join(map(str, path)),
                    bool(part.Hidden),
                )
            else:
                desired = True

            if bool(part.Hidden) != bool(desired):
                part.Hidden = bool(desired)
                part.Update()
                changed.append(path)

        return {
            "operation": op,
            "selector": selector,
            "tree_path": target_path,
            "changed_count": len(changed),
        }

    if op == "show_all":
        count = 0
        for part, _ in _walk_parts(top):
            part.Hidden = False
            part.Update()
            count += 1
        return {"operation": op, "visible_count": count}

    raise ValueError("operation must be hide|show|isolate|show_all|restore")

# KOMPAS_IDEMPOTENT_VIEW_FIX_1_1_11B
_set_view_before_111b = set_view

_VIEW_TYPE_111B = {
    "front": 1,
    "rear": 2,
    "back": 2,
    "up": 3,
    "top": 3,
    "down": 4,
    "bottom": 4,
    "left": 5,
    "right": 6,
    "iso": 7,
    "isometric": 7,
    "dimetric": 10,
    "dio": 10,
}


def _view_key_111b(value):
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        for k in ("view", "name", "projection"):
            if k in value:
                return str(value[k]).strip().lower()
    return str(value).strip().lower()


def _current_projection_111b(session, target_type):
    try:
        items = projections(session)
    except Exception:
        return None
    if isinstance(items, dict):
        items = items.get("projections", [])
    if not isinstance(items, (list, tuple)):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            ptype = int(item.get("type"))
        except Exception:
            continue
        if ptype == int(target_type) and bool(item.get("current")):
            return item
    return None


def _api7_set_projection_111b(target_type):
    import time
    import win32com.client

    app = win32com.client.GetActiveObject("KOMPAS.Application.7")
    doc = app.ActiveDocument
    if doc is None:
        raise RuntimeError("api7_no_active_document")

    try:
        mgr = doc.GetViewProjectionManager()
    except Exception:
        mgr = doc.ViewProjectionManager

    if mgr is None:
        raise RuntimeError("api7_view_projection_manager_unavailable")

    try:
        count = int(mgr.GetCount())
    except Exception:
        count = int(mgr.Count)

    for index in range(count):
        vp = mgr.GetViewProjection(index)
        if vp is None:
            continue
        try:
            ptype = int(vp.GetViewProjectonType())
        except Exception:
            continue
        if ptype != int(target_type):
            continue

        try:
            before = bool(vp.IsCurrent())
        except Exception:
            before = False

        if before:
            return {
                "setter": "API7.IViewProjection.SetCurrent",
                "index": index,
                "type": ptype,
                "already_current": True,
                "current_verified": True,
            }

        vp.SetCurrent(True)

        for _ in range(10):
            time.sleep(0.05)
            try:
                if bool(vp.IsCurrent()):
                    return {
                        "setter": "API7.IViewProjection.SetCurrent",
                        "index": index,
                        "type": ptype,
                        "already_current": False,
                        "current_verified": True,
                    }
            except Exception:
                pass

        raise RuntimeError(
            "api7_projection_not_current_after_SetCurrent:"
            f"type={target_type}, index={index}"
        )

    raise RuntimeError(
        "api7_projection_type_not_found:"
        f"type={target_type}, count={count}"
    )


def set_view(session, view):
    key = _view_key_111b(view)
    target_type = _VIEW_TYPE_111B.get(key)

    if target_type is None:
        return _set_view_before_111b(session, view)

    current = _current_projection_111b(session, target_type)
    if current is not None:
        return {
            "view": key,
            "type": int(target_type),
            "index": current.get("index"),
            "current_verified": True,
            "already_current": True,
            "setter": "precheck.IsCurrent",
        }

    try:
        return _set_view_before_111b(session, view)
    except RuntimeError as exc:
        message = str(exc)
        if "projection_SetCurrent_returned_false" not in message:
            raise

        current = _current_projection_111b(session, target_type)
        if current is not None:
            return {
                "view": key,
                "type": int(target_type),
                "index": current.get("index"),
                "set_current_return": False,
                "current_verified": True,
                "already_current": False,
                "verified_after_false_return": True,
                "setter": "API5.SetCurrent+IsCurrent",
            }

        api7 = _api7_set_projection_111b(target_type)

        current = _current_projection_111b(session, target_type)
        if current is not None:
            api7["api5_inventory_verified"] = True
            api7["index_api5"] = current.get("index")

        return {
            "view": key,
            "type": int(target_type),
            "current_verified": True,
            **api7,
        }

