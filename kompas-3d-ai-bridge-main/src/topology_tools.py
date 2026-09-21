from __future__ import annotations
from pathlib import Path

def _norm_out(value, expected, label):
    seq = list(value) if isinstance(value, (tuple, list)) else [value]
    if len(seq) == expected + 1 and isinstance(seq[0], bool):
        if not seq[0]:
            raise RuntimeError(label + "_valid_false:" + repr(seq))
        seq = seq[1:]
    elif len(seq) == expected + 1 and isinstance(seq[0], (int, float)) and seq[0] in (0, 1):
        if int(seq[0]) != 1:
            raise RuntimeError(label + "_valid_false:" + repr(seq))
        seq = seq[1:]
    if len(seq) != expected:
        raise RuntimeError(label + "_unexpected_shape:" + repr(seq))
    return [float(x) for x in seq]

def _call_out(obj, name, out_count, *tail):
    fn = getattr(obj, name)
    args = [0.0] * out_count + list(tail)
    return _norm_out(fn(*args), out_count, name)

def _safe_bool(obj, name):
    try:
        return bool(getattr(obj, name)())
    except Exception:
        return None

def _safe_float_call(obj, name):
    try:
        return float(getattr(obj, name)())
    except Exception:
        return None

def _safe_attr(obj, names):
    for name in names:
        try:
            value = getattr(obj, name)
            if callable(value):
                value = value()
            if value is not None:
                return value
        except Exception:
            pass
    return None

def _vertex_point(vertex):
    if vertex is None:
        return None
    try:
        return _norm_out(vertex.GetPoint(0.0, 0.0, 0.0), 3, "IVertexDefinition.GetPoint")
    except Exception:
        return None

def _point_key(p):
    return tuple(round(float(x), 9) for x in p)

def _edge_key(a, b):
    ka = _point_key(a)
    kb = _point_key(b)
    return tuple(sorted((ka, kb)))

def _body_bbox(body):
    try:
        return _call_out(body, "GetGabarit", 6)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}:{exc}"}

def _placement_info(place):
    result = {}
    try:
        result["origin"] = _call_out(place, "GetOrigin", 3)
    except Exception as exc:
        result["origin_error"] = f"{type(exc).__name__}:{exc}"
    axes = {}
    for axis_type, label in ((0, "x"), (1, "y"), (2, "z")):
        try:
            axes[label] = _call_out(place, "GetAxis", 3, int(axis_type))
        except Exception as exc:
            axes[label + "_error"] = f"{type(exc).__name__}:{exc}"
    result["axes"] = axes
    return result

def _component_by_top_level_index(d3, selector):
    try:
        index = int(selector)
    except Exception:
        raise ValueError("CAP2-A2 topology currently requires a top-level integer selector")
    parts = d3.PartCollection(True)
    if parts is None:
        raise RuntimeError("PartCollection(True)_returned_none")
    count = int(parts.GetCount())
    if index < 0 or index >= count:
        raise LookupError(f"selector_out_of_range:{index}/{count}")
    part = parts.GetByIndex(index)
    if part is None:
        raise RuntimeError(f"PartCollection.GetByIndex({index})_returned_none")
    return part, index, count

def component_topology(session, selector, body_index=None, max_faces=1000, max_edges=3000, max_vertices=3000):
    from view_tools import _active_api5

    active_path = str(session.active_path() or "")
    if not active_path:
        raise RuntimeError("no_active_document_path")

    app5, d3, api7_path, api5_path, is_active = _active_api5(session)
    part, selector_index, top_count = _component_by_top_level_index(d3, selector)

    component_name = _safe_attr(part, ("name", "Name", "GetName"))
    component_file = _safe_attr(part, ("fileName", "FileName", "GetFileName"))

    bodies = part.BodyCollection()
    if bodies is None:
        raise RuntimeError("part.BodyCollection_returned_none")

    body_count = int(bodies.GetCount())
    if body_index is None:
        selected_body_indexes = list(range(body_count))
    else:
        bi = int(body_index)
        if bi < 0 or bi >= body_count:
            raise LookupError(f"body_index_out_of_range:{bi}/{body_count}")
        selected_body_indexes = [bi]

    max_faces = max(1, min(int(max_faces), 5000))
    max_edges = max(1, min(int(max_edges), 10000))
    max_vertices = max(1, min(int(max_vertices), 10000))

    result = {
        "requested_action": {
            "selector": selector,
            "body_index": body_index,
            "max_faces": max_faces,
            "max_edges": max_edges,
            "max_vertices": max_vertices,
        },
        "active_document": api7_path or active_path,
        "target": {
            "selector": selector_index,
            "component_name": str(component_name) if component_name is not None else None,
            "component_file": str(component_file) if component_file is not None else None,
            "top_level_part_count": top_count,
        },
        "coordinate_space": {
            "reported": "coordinates returned directly by KOMPAS API5 Body/Face/Vertex objects",
            "additional_transform_applied_by_bridge": False,
        },
        "body_count": body_count,
        "selected_body_indexes": selected_body_indexes,
        "bodies": [],
        "faces": [],
        "edges": [],
        "vertices": [],
        "cylindrical_faces": [],
        "planar_faces": [],
        "surface_counts": {
            "planar": 0,
            "cylindrical": 0,
            "conical": 0,
            "spherical": 0,
            "toroidal": 0,
            "nurbs": 0,
            "revolved": 0,
            "swept": 0,
            "other": 0,
        },
        "errors": [],
        "truncated": {"faces": False, "edges": False, "vertices": False},
        "read_only": True,
        "save_required": False,
        "saved": False,
        "model_geometry_changed": False,
        "api7_path_name": api7_path,
        "api5_file_name": api5_path,
        "api5_is_active": bool(is_active),
    }

    vertices = {}
    edges = {}
    total_faces = 0
    total_edge_refs = 0

    for bi in selected_body_indexes:
        body = bodies.GetByIndex(bi)
        if body is None:
            result["errors"].append({"body_index": bi, "error": "body_none"})
            continue

        faces = body.FaceCollection()
        face_count = int(faces.GetCount()) if faces is not None else 0
        result["bodies"].append({
            "index": bi,
            "bbox": _body_bbox(body),
            "is_solid": _safe_bool(body, "IsSolid"),
            "face_count": face_count,
        })

        for fi in range(face_count):
            total_faces += 1
            try:
                face = faces.GetByIndex(fi)
                if face is None:
                    raise RuntimeError("face_none")

                flags = {
                    "planar": _safe_bool(face, "IsPlanar"),
                    "cylindrical": _safe_bool(face, "IsCylinder"),
                    "conical": _safe_bool(face, "IsCone"),
                    "spherical": _safe_bool(face, "IsSphere"),
                    "toroidal": _safe_bool(face, "IsTorus"),
                    "nurbs": _safe_bool(face, "IsNurbsSurface"),
                    "revolved": _safe_bool(face, "IsRevolved"),
                    "swept": _safe_bool(face, "IsSwept"),
                }

                primary = "other"
                for candidate in ("cylindrical", "planar", "conical", "spherical", "toroidal", "nurbs", "revolved", "swept"):
                    if flags.get(candidate):
                        primary = candidate
                        break
                result["surface_counts"][primary] += 1

                edge_collection = face.EdgeCollection()
                edge_count = int(edge_collection.GetCount()) if edge_collection is not None else 0
                total_edge_refs += edge_count

                face_row = {
                    "body_index": bi,
                    "face_index": fi,
                    "surface_type": primary,
                    "flags": flags,
                    "area": _safe_float_call(face, "GetArea"),
                    "edge_count": edge_count,
                }

                if len(result["faces"]) < max_faces:
                    result["faces"].append(face_row)
                else:
                    result["truncated"]["faces"] = True

                if primary == "planar":
                    result["planar_faces"].append({
                        "body_index": bi,
                        "face_index": fi,
                        "area": face_row["area"],
                        "edge_count": edge_count,
                    })

                for ei in range(edge_count):
                    edge = edge_collection.GetByIndex(ei)
                    if edge is None:
                        continue
                    a = _vertex_point(edge.GetVertex(True))
                    b = _vertex_point(edge.GetVertex(False))
                    if a is not None:
                        vertices[_point_key(a)] = a
                    if b is not None:
                        vertices[_point_key(b)] = b
                    if a is not None and b is not None:
                        ek = _edge_key(a, b)
                        if ek not in edges:
                            edges[ek] = {"start": a, "end": b}

                if primary == "cylindrical":
                    cyl = {
                        "body_index": bi,
                        "face_index": fi,
                        "classification": "cylindrical_face",
                        "detection": "native_topology",
                        "area": face_row["area"],
                        "edge_count": edge_count,
                    }
                    try:
                        h, r = _norm_out(face.GetCylinderParam(0.0, 0.0), 2, "GetCylinderParam")
                        cyl["height"] = h
                        cyl["radius"] = r
                        cyl["diameter"] = 2.0 * r
                    except Exception as exc:
                        cyl["cylinder_param_error"] = f"{type(exc).__name__}:{exc}"

                    try:
                        surface = face.GetSurface()
                        param = surface.GetSurfaceParam() if surface is not None else None
                        if param is not None:
                            placement = param.GetPlacement()
                            if placement is not None:
                                pi = _placement_info(placement)
                                cyl["placement"] = pi
                                if "origin" in pi:
                                    cyl["axis_origin"] = pi["origin"]
                                axis = pi.get("axes", {}).get("z")
                                if axis is not None:
                                    cyl["axis_vector"] = axis
                    except Exception as exc:
                        cyl["surface_param_error"] = f"{type(exc).__name__}:{exc}"

                    result["cylindrical_faces"].append(cyl)

            except Exception as exc:
                result["errors"].append({
                    "body_index": bi,
                    "face_index": fi,
                    "error": f"{type(exc).__name__}:{exc}",
                })

    all_vertices = list(vertices.values())
    all_edges = list(edges.values())
    result["vertices"] = all_vertices[:max_vertices]
    result["edges"] = all_edges[:max_edges]
    result["truncated"]["vertices"] = len(all_vertices) > max_vertices
    result["truncated"]["edges"] = len(all_edges) > max_edges

    result["topology"] = {
        "selected_body_count": len(selected_body_indexes),
        "face_count": total_faces,
        "edge_reference_count": total_edge_refs,
        "unique_edge_count": len(all_edges),
        "unique_vertex_count": len(all_vertices),
        "surface_counts": dict(result["surface_counts"]),
    }

    result["verification"] = {
        "body_collection_read": True,
        "faces_read": total_faces > 0,
        "edges_read": len(all_edges) > 0,
        "vertices_read": len(all_vertices) > 0,
        "native_surface_classification_executed": True,
        "cylindrical_faces_found": len(result["cylindrical_faces"]),
        "cylindrical_parameter_path_exercised": len(result["cylindrical_faces"]) > 0,
        "face_errors": len(result["errors"]),
        "api5_api7_same_document": (
            (not api7_path)
            or (not api5_path)
            or Path(api7_path).name.lower() == Path(api5_path).name.lower()
        ),
    }

    if total_faces < 1:
        result.setdefault("warnings", []).append(
            "no_direct_body_faces"
        )
        result["verification"]["zero_face_component_valid"] = True
        result["verification"]["faces_read"] = False
    else:
        result["verification"]["zero_face_component_valid"] = False

    return result
