from __future__ import annotations
from pathlib import Path
from protocol import ok
from safety import safe_write
from screenshot import capture
from model_tools import model_tree, model_components, component_info, model_bbox, references_qa
from topology_tools import component_topology
from holes_tools import component_holes
from interference_tools import interference_check
from part_create_tools import part_create
from primitive_tools import part_add_primitive
from hole_create_tools import part_add_hole
from assembly_create_tools import assembly_create
from assembly_insert_tools import assembly_insert_component
from component_transform_tools import component_transform
from material_tools import material_read, material_set
from pmi_tools import pmi_dimension_create, pmi_read
from component_properties_tools import component_properties_read, component_properties_set
from bom_tools import bom_read
from component_pattern_tools import component_pattern_linear
from mate_tools import mate_create, mate_read
from artifact_check import artifact_check
from dialogs import classify, describe_policy, dismiss_plan, summarize
from version_matrix import classify_version
from export_tools import export_file, manage_checkpoints
from drawing_tools import drawing_info, stamp_read, stamp_write
from spec_tools import specification_read
from dryrun import preflight
from view_tools import set_view, fit, rotate, visibility, projections
from cabinet_tools import (cabinet_spec_check, cabinet_layout_check, cabinet_layout_build,
                           cabinet_materialize_plan, cabinet_materialize_apply,
                           cabinet_bom_reconcile, cabinet_calc,
                           cabinet_catalog_search, cabinet_spec_build,
                           cabinet_schematic_build)
from docs_tools import docs_search_handler
from normative_tools import normative_tables_handler


def _doc(s):
    d = s.active()
    if d is None:
        raise RuntimeError("no_active_document")
    return d


def document_open(s, p):
    path = Path(p["path"])
    if not path.is_file():
        raise FileNotFoundError(str(path))
    d = s.connect().Documents.Open(str(path), False)
    s.last_document_path = str(path)
    return ok("document.open", {"file_name": s.document_path(d) or str(path), "path_name": s.document_path(d) or str(path)})


def document_open_agent_copy(s, p):
    raw = str(p.get("filename") or "").strip()
    path = Path(raw)
    if not raw or path.name != raw:
        raise ValueError("filename_must_be_nonempty_basename")
    if path.suffix.lower() not in (".m3d", ".a3d"):
        raise ValueError("filename_must_end_with_m3d_or_a3d")
    if "_agent_copy" not in path.stem.lower():
        raise ValueError("filename_must_contain_AGENT_COPY")
    work = (Path(s.root).resolve() / "work").resolve()
    target = (work / raw).resolve()
    if target.parent != work:
        raise RuntimeError("target_escaped_approved_work")
    if not target.is_file():
        raise FileNotFoundError(str(target))
    app = s.connect()
    for index in range(int(app.Documents.Count)):
        candidate = app.Documents.Item(index)
        candidate_path = str(s.document_path(candidate) or "")
        if candidate_path and Path(candidate_path).resolve() == target:
            for name in ("Activate", "SetActive"):
                try:
                    getattr(candidate, name)()
                    break
                except Exception:
                    pass
            else:
                candidate.Active = True
            active = str(s.active_path() or "")
            if not active or Path(active).resolve() != target:
                raise RuntimeError("existing_document_activation_failed:" + active)
            return ok("document.open_agent_copy", {"file_name": active, "path_name": active, "already_open": True, "read_only": False, "saved": False})
    opened = app.Documents.Open(str(target), True, False)
    if opened is None:
        raise RuntimeError("Documents.Open_returned_none:" + str(target))
    active = str(s.active_path() or "")
    if not active or Path(active).resolve() != target:
        raise RuntimeError("opened_document_not_exact_active:" + active)
    return ok("document.open_agent_copy", {"file_name": active, "path_name": active, "already_open": False, "read_only": False, "saved": False})


def document_activate(s, p):
    target = str(p.get("document") or p.get("path") or p.get("name") or "").lower()
    if not target:
        raise ValueError("document selector required")
    app = s.connect(); matches = []
    for i in range(app.Documents.Count):
        d = app.Documents.Item(i)
        fields = [str(getattr(d, "Name", "") or ""), str(s.document_path(d) or "")]
        if any(target == f.lower() or target in f.lower() for f in fields):
            matches.append(d)
    if len(matches) != 1:
        raise LookupError(f"document_selector_matches={len(matches)}")
    d = matches[0]
    activated = False
    for method in ("Activate", "SetActive"):
        try:
            getattr(d, method)(); activated = True; break
        except Exception:
            pass
    if not activated:
        try:
            d.Active = True; activated = True
        except Exception:
            pass
    if not activated:
        raise RuntimeError("document_activation_not_supported_for_this_document")
    return ok("document.activate", {"file_name": s.document_path(d) or "", "path_name": s.document_path(d) or "", "activated": True})


def document_reopen(s, p):
    d = _doc(s)
    path = str(s.document_path(d) or s.last_document_path or "")
    if not path:
        raise RuntimeError("active_document_has_no_path")
    save = bool(p.get("save", False))
    if save:
        safe_write(path, s.root); d.Save()
    d.Close(0)
    reopened = s.connect().Documents.Open(path, False)
    s.last_document_path = path
    s.visibility_snapshot = None
    return ok("document.reopen", {"file_name": s.document_path(reopened) or path, "path_name": s.document_path(reopened) or path, "saved_before_reopen": save})


def document_info(s, p):
    d = _doc(s)
    return ok("document.info", {
        "name": str(getattr(d, "Name", "") or ""),
        "file_name": s.document_path(d) or "",
        "path_name": s.document_path(d) or "",
        "document_type": str(getattr(d, "DocumentType", "") or ""),
        "read_only": getattr(d, "ReadOnly", None),
    })


def tree(s, p): return ok("model.tree", model_tree(s, int(p.get("max_depth", 6))))
def components(s, p): return ok("model.components", model_components(s))
def comp_info(s, p): return ok("component.info", component_info(s, p["selector"]))
def comp_topology(s, p): return ok("component.topology", component_topology(s, p["selector"], p.get("body_index"), p.get("max_faces", 1000), p.get("max_edges", 3000), p.get("max_vertices", 3000)))
def comp_holes(s, p): return ok("component.holes", component_holes(s, p["selector"], p.get("include_inferred", True)))
def interference(s, p): return ok("interference.check", interference_check(s, p.get("selector_a"), p.get("selector_b"), p.get("selectors"), p.get("check_contact", True), p.get("include_body_pairs", False), p.get("max_body_pair_details", 20)))
def create_part(s, p): return ok("part.create", part_create(s, p["filename"]))
def add_primitive(s, p): return ok("part.primitive", part_add_primitive(s, p["filename"], p["kind"], p["width_mm"], p["height_mm"], p["depth_mm"], p.get("wall_mm")))
def add_hole(s, p): return ok("part.hole", part_add_hole(s, p["filename"], p["diameter_mm"], p.get("center_u_mm", 0.0), p.get("center_v_mm", 0.0), p.get("plane", "XOY"), p.get("mode", "through"), p.get("depth_mm"), p.get("direction", "both")))
def create_assembly(s, p): return ok("assembly.create", assembly_create(s, p["filename"]))
def insert_assembly_component(s, p): return ok("assembly.insert_component", assembly_insert_component(s, p["assembly_filename"], p["component_filename"], p.get("allow_duplicate", False)))
def transform_component(s, p): return ok("component.transform", component_transform(s, p["assembly_filename"], p["selector"], p["origin"], p["axis_x"], p["axis_y"], p["axis_z"], p.get("mode", "absolute")))
def read_material(s, p): return ok("material.read", material_read(s, p["relative_path"]))
def set_material(s, p): return ok("material.set", material_set(s, p["filename"], p["material_name"], p["density"], p["evidence_source_relative_path"]))
def read_pmi(s, p): return ok("pmi.read", pmi_read(s, p.get("max_dimensions", 500)))
def create_pmi_dimension(s, p): return ok("pmi.dimension_create", pmi_dimension_create(s, p["filename"], p["point1"], p["point2"], p["text_position"], p.get("plane", "XOY"), p.get("name", "CAP2_LINEAR_DIMENSION"), p.get("point_tolerance", 0.001)))
def read_component_properties(s, p): return ok("component.properties_read", component_properties_read(s, p["selector"]))
def set_component_properties(s, p): return ok("component.properties_set", component_properties_set(s, p["assembly_filename"], p["selector"], p.get("designation"), p.get("name")))
def read_bom(s, p): return ok("bom.read", bom_read(s, p.get("max_depth", 8)))
def create_component_linear_pattern(s, p): return ok("component.pattern_linear", component_pattern_linear(s, p["assembly_filename"], p["source_selector"], p["direction"], p["spacing"], p["count"], p.get("name", "CAP2_LINEAR_COMPONENT_PATTERN")))
def read_mates(s, p): return ok("mate.read", mate_read(s, p.get("max_items", 200)))
def create_mate(s, p): return ok("mate.create", mate_create(s, p["assembly_filename"], p["kind"], p["selector_a"], p["selector_b"], p.get("value"), p.get("align", "auto"), p.get("fixed", False)))
def check_artifact(s, p): return ok("artifact.check", artifact_check(s.root, p["relative_path"]))


def export_document(s, p): return ok("export.file", export_file(s, p["relative_path"], p["format"], p.get("apply", False)))
def checkpoints(s, p): return ok("checkpoint.manage", manage_checkpoints(s.root, p))
def drawing_information(s, p): return ok("drawing.info", drawing_info(s, p.get("sheet_index"), p.get("max_views", 50), p.get("include_views", True)))
def stamp_information(s, p): return ok("stamp.read", stamp_read(s, p.get("cells"), p.get("sheet_index")))
def stamp_update(s, p): return ok("stamp.write", stamp_write(s, p["cells"], p.get("sheet_index"), p.get("checkpoint", True)))
def specification_report(s, p): return ok("specification.read", specification_read(s, p.get("max_depth", 8)))
def dry_run(s, p): return ok("dryrun.plan", preflight(s.root, p["tool"], p.get("arguments"), p.get("version")))


def dialogs_watch(s, p):
    """Сторож модальных диалогов. Работает без COM: если КОМПАС висит на
    модальном окне, обращение через Automation тоже повиснет, поэтому
    процесс ищется через tasklist, а окна - через Win32."""
    from winui import close_dialog, enumerate_dialogs, kompas_pids

    warnings = []
    try:
        pids = sorted(kompas_pids())
    except Exception as exc:
        pids = []
        warnings.append("kompas_process_lookup_failed: " + type(exc).__name__)

    if not pids:
        warnings.append(
            "KOMPAS.exe не найден среди процессов. Если система запущена под "
            "другим пользователем или с повышением прав, сторож её не увидит."
        )

    classified = []
    for pid in pids:
        try:
            windows = enumerate_dialogs(pid)
        except Exception as exc:
            warnings.append(f"enumeration_failed_pid_{pid}: {type(exc).__name__}")
            continue
        for row in windows:
            verdict = classify(
                row.get("title"),
                row.get("class_name", ""),
                bool(row.get("visible")),
                bool(row.get("enabled")),
            )
            classified.append({**row, **verdict})

    summary = summarize(classified)
    apply_changes = bool(p.get("apply", False))
    plan = dismiss_plan(classified, apply=apply_changes)

    closed = []
    if apply_changes:
        for item in plan["selected"]:
            try:
                closed.append(close_dialog(item["hwnd"]))
            except Exception as exc:
                closed.append({
                    "hwnd": item.get("hwnd"),
                    "closed": False,
                    "reason": type(exc).__name__,
                })

    return ok("dialogs.watch", {
        "kompas_pids": pids,
        "dialog_count": len(classified),
        "dialogs": classified[:50],
        "summary": summary,
        "plan": plan,
        "applied": apply_changes,
        "closed": closed,
        "policy": describe_policy(),
        "read_only": not apply_changes,
        "saved": False,
        "warnings": warnings,
    })


def version_check(s, p):
    """Версия КОМПАСа и её место в матрице подтверждённых версий."""
    from settings import settings_for

    matrix = settings_for(s.root).version_matrix
    version = str(p.get("version") or "").strip()
    warnings = []
    if not version:
        try:
            version = str(s.status().get("version") or "")
        except Exception as exc:
            warnings.append("kompas_version_unavailable: " + type(exc).__name__)

    verdict = classify_version(version, matrix)
    verdict["warnings"] = list(verdict.get("warnings") or []) + warnings
    verdict["matrix"] = {
        "entries": list(matrix.get("entries") or [])[:20],
        "default_level": matrix.get("default_level"),
        "guarded_actions": list(matrix.get("guarded_actions") or []),
        "notes": matrix.get("notes") or "",
        "source": str(settings_for(s.root).config_path),
    }
    verdict["read_only"] = True
    return ok("version.check", verdict)
def bbox(s, p): return ok("model.bbox", model_bbox(s))
def refs(s, p): return ok("qa.references", references_qa(s, p.get("package_root"), int(p.get("max_depth", 8))))
def view_projections(s, p): return ok("view.projections", {"projections": projections(s)})
def view_set(s, p): return ok("view.set", set_view(s, p["view"]))
def view_fit(s, p): return ok("view.fit", fit(s))
def view_rotate(s, p): return ok("view.rotate", rotate(s, p.get("horizontal_steps", 0), p.get("vertical_steps", 0)))
def view_visibility(s, p): return ok("view.visibility", visibility(s, p["operation"], p.get("selector")))
def viewport_capture(s, p): return ok("viewport.capture", capture(s.root, bool(p.get("viewport_only", True)), p.get("label"), session=s))


registry = {
    "document.open": document_open,
    "document.open_agent_copy": document_open_agent_copy,
    "document.activate": document_activate,
    "document.reopen": document_reopen,
    "document.info": document_info,
    "model.tree": tree,
    "model.components": components,
    "component.info": comp_info,
    "component.topology": comp_topology,
    "component.holes": comp_holes,
    "interference.check": interference,
    "part.create": create_part,
    "part.primitive": add_primitive,
    "part.hole": add_hole,
    "assembly.create": create_assembly,
    "assembly.insert_component": insert_assembly_component,
    "component.transform": transform_component,
    "material.read": read_material,
    "material.set": set_material,
    "pmi.read": read_pmi,
    "pmi.dimension_create": create_pmi_dimension,
    "component.properties_read": read_component_properties,
    "component.properties_set": set_component_properties,
    "bom.read": read_bom,
    "component.pattern_linear": create_component_linear_pattern,
    "mate.read": read_mates,
    "mate.create": create_mate,
    "artifact.check": check_artifact,
    "export.file": export_document,
    "checkpoint.manage": checkpoints,
    "drawing.info": drawing_information,
    "stamp.read": stamp_information,
    "stamp.write": stamp_update,
    "specification.read": specification_report,
    "dryrun.plan": dry_run,
    "dialogs.watch": dialogs_watch,
    "version.check": version_check,
    "model.bbox": bbox,
    "qa.references": refs,
    "view.projections": view_projections,
    "view.set": view_set,
    "view.fit": view_fit,
    "view.rotate": view_rotate,
    "view.visibility": view_visibility,
    "viewport.capture": viewport_capture,
    "cabinet.spec_check": cabinet_spec_check,
    "cabinet.layout_check": cabinet_layout_check,
    "cabinet.layout_build": cabinet_layout_build,
    "cabinet.materialize_plan": cabinet_materialize_plan,
    "cabinet.materialize_apply": cabinet_materialize_apply,
    "cabinet.bom_reconcile": cabinet_bom_reconcile,
    "cabinet.calc": cabinet_calc,
    "cabinet.catalog_search": cabinet_catalog_search,
    "cabinet.spec": cabinet_spec_build,
    "docs.search": docs_search_handler,
    "normative.tables": normative_tables_handler,
    "cabinet.schematic": cabinet_schematic_build,
}
