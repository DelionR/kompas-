from __future__ import annotations
from pathlib import Path


def _doc3d(session):
    import win32com.client as wc
    d = session.active()
    if d is None:
        raise RuntimeError("no_active_document")
    try:
        d3 = wc.CastTo(d, "IKompasDocument3D")
        top = d3.TopPart
    except Exception:
        partdoc = wc.CastTo(d, "IPartDocument")
        top = partdoc.TopPart
    if top is None:
        raise RuntimeError("active_document_is_not_3d")
    return d, top


def _bbox(part):
    try:
        raw = list(part.GetGabarit(True, False))
        if raw and isinstance(raw[0], bool):
            return {"valid": bool(raw[0]), "raw": raw, "min": raw[1:4], "max": raw[4:7]}
        return {"raw": raw}
    except Exception as exc:
        return {"error": repr(exc)}


def _safe_get(obj, name, default=None):
    try: return getattr(obj, name)
    except Exception: return default


def _component_row(part, index=None, depth=0, tree_path=None):
    row = {
        "index": index,
        "tree_path": list(tree_path or []),
        "depth": depth,
        "name": str(_safe_get(part, "Name", "") or ""),
        "marking": str(_safe_get(part, "Marking", "") or ""),
        "file_name": str(_safe_get(part, "FileName", "") or ""),
        "material": str(_safe_get(part, "Material", "") or ""),
        "mass": _float_or_none(_safe_get(part, "Mass", None)),
        "density": _float_or_none(_safe_get(part, "Density", None)),
        "fixed": _bool_or_none(_safe_get(part, "Fixed", None)),
        "hidden": _bool_or_none(_safe_get(part, "Hidden", None)),
        "nested_count": _count_parts(part),
        "bbox": _bbox(part),
    }
    try:
        row["origin"] = list(part.Placement.GetOrigin())
    except Exception as exc:
        row["origin"] = {"error": repr(exc)}
    try:
        row["matrix"] = list(part.Placement.GetMatrix3D())
    except Exception as exc:
        row["matrix"] = {"error": repr(exc)}
    return row


def _float_or_none(value):
    try: return float(value)
    except Exception: return None


def _bool_or_none(value):
    try: return bool(value)
    except Exception: return None


def _count_parts(part):
    try: return int(part.Parts.Count)
    except Exception: return 0


def model_bbox(session):
    _, top = _doc3d(session)
    return {"top_name": str(getattr(top, "Name", "") or ""), "bbox": _bbox(top)}


def model_components(session):
    _, top = _doc3d(session)
    rows = []
    for i in range(top.Parts.Count):
        rows.append(_component_row(top.Parts.Part(i), i, 0, [i]))
    return {"count": len(rows), "components": rows}


def model_tree(session, max_depth=6):
    _, top = _doc3d(session)

    def walk(part, depth, index=None, tree_path=None):
        tree_path = list(tree_path or [])
        node = _component_row(part, index, depth, tree_path)
        node["children"] = []
        if depth >= max_depth:
            node["truncated"] = _count_parts(part) > 0
            return node
        try:
            for i in range(part.Parts.Count):
                node["children"].append(walk(part.Parts.Part(i), depth + 1, i, tree_path + [i]))
        except Exception:
            pass
        return node

    return {"tree": walk(top, 0, None, []), "max_depth": max_depth}


def _match(part, selector):
    if selector is None:
        return False
    s = str(selector).strip().lower()
    fields = [str(_safe_get(part, x, "") or "").lower() for x in ("Name", "Marking", "FileName")]
    return any(s == f or s in f for f in fields)


def _part_by_path(top, path):
    part = top
    for idx in path:
        idx = int(idx)
        if idx < 0 or idx >= part.Parts.Count:
            raise LookupError(f"component_path_out_of_range: {path}")
        part = part.Parts.Part(idx)
    return part


def _parse_path_selector(selector):
    if isinstance(selector, (list, tuple)) and selector and all(str(x).lstrip('-').isdigit() for x in selector):
        return [int(x) for x in selector]
    if isinstance(selector, str):
        raw = selector.strip().replace(".", "/")
        if raw and all(x.isdigit() for x in raw.split("/")):
            return [int(x) for x in raw.split("/")]
    return None


def find_component(session, selector):
    _, top = _doc3d(session)
    if isinstance(selector, int) or (isinstance(selector, str) and selector.isdigit()):
        path = [int(selector)]
        return _part_by_path(top, path), path
    path = _parse_path_selector(selector)
    if path:
        return _part_by_path(top, path), path

    matches = []
    def walk(parent, prefix):
        try: count = int(parent.Parts.Count)
        except Exception: count = 0
        for i in range(count):
            part = parent.Parts.Part(i); pth = prefix + [i]
            if _match(part, selector): matches.append((part, pth))
            walk(part, pth)
    walk(top, [])
    if not matches:
        raise LookupError(f"component_not_found: {selector}")
    if len(matches) > 1:
        exact = []
        needle = str(selector).strip().lower()
        for part, pth in matches:
            fields = [str(_safe_get(part, x, "") or "").lower() for x in ("Name", "Marking", "FileName")]
            if needle in fields: exact.append((part, pth))
        if len(exact) == 1:
            return exact[0]
        raise LookupError(f"component_selector_ambiguous: {selector}; paths={[p for _, p in matches]}")
    return matches[0]


def component_info(session, selector):
    part, path = find_component(session, selector)
    return _component_row(part, path[-1] if path else None, len(path), path)


def references_qa(session, package_root=None, max_depth=8):
    _, top = _doc3d(session)
    refs = []
    visited = set()

    def walk(part, depth, path_indices):
        if depth > max_depth:
            return
        try:
            count = part.Parts.Count
        except Exception:
            count = 0
        for i in range(count):
            child = part.Parts.Part(i)
            file_name = str(getattr(child, "FileName", "") or "")
            key = (tuple(path_indices + [i]), file_name)
            if key not in visited:
                visited.add(key)
                exists = None
                absolute = False
                outside_package = None
                if file_name:
                    p = Path(file_name)
                    absolute = p.is_absolute()
                    exists = p.exists()
                    if package_root:
                        try: outside_package = not p.resolve().is_relative_to(Path(package_root).resolve())
                        except Exception: outside_package = True
                refs.append({
                    "tree_path": path_indices + [i],
                    "name": str(getattr(child, "Name", "") or ""),
                    "file_name": file_name,
                    "exists": exists,
                    "absolute": absolute,
                    "outside_package": outside_package,
                })
            walk(child, depth + 1, path_indices + [i])

    walk(top, 0, [])
    missing = [r for r in refs if r["file_name"] and r["exists"] is False]
    absolute = [r for r in refs if r["absolute"]]
    outside = [r for r in refs if r["outside_package"] is True]
    return {
        "reference_count": len(refs),
        "missing_count": len(missing),
        "absolute_count": len(absolute),
        "outside_package_count": len(outside) if package_root else None,
        "pass_missing_refs": len(missing) == 0,
        "references": refs,
    }
