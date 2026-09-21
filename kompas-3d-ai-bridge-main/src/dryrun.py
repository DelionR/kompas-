"""Dry run (preflight) for mutating tools.

A dry run answers one question: *what stands between these arguments and the
first COM call?* It never opens, writes or saves anything, and it never claims
the operation would succeed - only that nothing detectable blocks it.

Everything here is pure: file policy, argument schema, path resolution, the
version guard and the job allow-list. Anything that needs Automation is
reported under ``unverifiable`` instead of being guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# declarative manifest: what each mutating action touches
# --------------------------------------------------------------------------
#
# kind:
#   create  - makes a new file, target must NOT exist
#   modify  - rewrites an existing file
#   export  - writes a new artifact next to the source
#   restore - overwrites an existing file from a snapshot
#   script  - arbitrary code behind the allow-list
#   view    - temporary visual state, no file write
#   ui      - window management, no file write
#   document - document lifecycle (save/close/reopen)
#
ACTION_PREFLIGHT: Dict[str, Dict[str, Any]] = {
    "part.create": {"kind": "create", "target_key": "filename", "suffix": ".m3d"},
    "part.primitive": {"kind": "modify", "target_key": "filename", "suffix": ".m3d", "agent_copy": True},
    "part.hole": {"kind": "modify", "target_key": "filename", "suffix": ".m3d", "agent_copy": True},
    "assembly.create": {"kind": "create", "target_key": "filename", "suffix": ".a3d"},
    "assembly.insert_component": {"kind": "modify", "target_key": "assembly_filename", "suffix": ".a3d", "agent_copy": True},
    "component.transform": {"kind": "modify", "target_key": "assembly_filename", "suffix": ".a3d", "agent_copy": True},
    "component.pattern_linear": {"kind": "modify", "target_key": "assembly_filename", "suffix": ".a3d", "agent_copy": True},
    "component.properties_set": {"kind": "modify", "target_key": "assembly_filename", "suffix": ".a3d", "agent_copy": True},
    "mate.create": {"kind": "modify", "target_key": "assembly_filename", "suffix": ".a3d", "agent_copy": True},
    "material.set": {"kind": "modify", "target_key": "filename", "suffix": ".m3d", "agent_copy": True},
    "pmi.dimension_create": {"kind": "modify", "target_key": "filename", "suffix": ".m3d", "agent_copy": True},
    "stamp.write": {"kind": "modify", "target_key": None, "active_document": True, "agent_copy": True},
    "export.file": {"kind": "export", "target_key": "relative_path", "suffix": None, "agent_copy": False},
    "checkpoint.manage": {"kind": "restore", "target_key": "target", "suffix": None, "agent_copy": False},
    "cabinet.materialize_apply": {"kind": "modify", "target_key": "assembly_filename", "suffix": ".a3d", "agent_copy": True},
    "run_job": {"kind": "script", "target_key": "script", "allowlist": True},
    "dialogs.watch": {"kind": "ui", "target_key": None},
    "view.set": {"kind": "view", "target_key": None},
    "view.fit": {"kind": "view", "target_key": None},
    "view.rotate": {"kind": "view", "target_key": None},
    "view.visibility": {"kind": "view", "target_key": None},
    "document.save": {"kind": "document", "target_key": None},
    "document.close": {"kind": "document", "target_key": None},
    "document.reopen": {"kind": "document", "target_key": None},
    "dryrun.plan": {"kind": "none", "target_key": None},
}

KIND_NOTES = {
    "create": "Creates a new file; the target must not exist yet.",
    "modify": "Rewrites an existing file.",
    "export": "Writes a new artifact; the source document is not modified.",
    "restore": "Overwrites an existing file from a snapshot.",
    "script": "Runs a script from the allow-list; its own effects are not predictable from arguments.",
    "view": "Temporary visual state only; no file is written.",
    "ui": "Window management only; no file is written.",
    "document": "Document lifecycle; acts on the active document.",
    "none": "Does not touch KOMPAS at all.",
}

GENERAL_UNVERIFIABLE = [
    "Whether the KOMPAS interface this action needs exists on the running build.",
    "Whether the operation produces the intended geometry or text.",
    "Whether readback after Save, Close and Reopen confirms the change.",
    "Whether a modal dialog appears during the call.",
]


# --------------------------------------------------------------------------
# minimal JSON Schema validation (the subset the catalog actually uses)
# --------------------------------------------------------------------------


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    actual = _type_name(value)
    if expected == "number":
        return actual in ("integer", "number")
    if expected == "integer":
        return actual == "integer"
    return actual == expected


def validate_value(value: Any, schema: Dict[str, Any], path: str, problems: List[Dict[str, str]]):
    if not isinstance(schema, dict):
        return

    if "oneOf" in schema:
        if not any(_try_schema(value, sub) for sub in schema["oneOf"]):
            problems.append({"path": path, "problem": "value matches none of the oneOf branches"})
        return

    expected = schema.get("type")
    if expected:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, name) for name in names):
            problems.append(
                {"path": path, "problem": "expected type %s, got %s" % ("/".join(names), _type_name(value))}
            )
            return

    if "enum" in schema and value not in schema["enum"]:
        problems.append(
            {"path": path, "problem": "value %r is not one of %s" % (value, schema["enum"])}
        )

    if isinstance(value, bool):
        return

    if isinstance(value, (int, float)):
        for key, word in (("minimum", "below minimum"), ("exclusiveMinimum", "at or below exclusive minimum")):
            limit = schema.get(key)
            if limit is not None and value < limit:
                problems.append({"path": path, "problem": "%s %s" % (word, limit)})
        for key, word in (("maximum", "above maximum"), ("exclusiveMaximum", "at or above exclusive maximum")):
            limit = schema.get(key)
            if limit is not None and value > limit:
                problems.append({"path": path, "problem": "%s %s" % (word, limit)})

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append({"path": path, "problem": "shorter than minLength %s" % schema["minLength"]})
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append({"path": path, "problem": "longer than maxLength %s" % schema["maxLength"]})

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append({"path": path, "problem": "fewer than minItems %s" % schema["minItems"]})
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append({"path": path, "problem": "more than maxItems %s" % schema["maxItems"]})
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                validate_value(item, items, "%s[%d]" % (path, index), problems)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append({"path": path or "<root>", "problem": "missing required property %r" % key})
        properties = schema.get("properties", {})
        for key, item in value.items():
            sub_schema = properties.get(key)
            if sub_schema is None:
                extra = schema.get("additionalProperties")
                if extra is False:
                    problems.append({"path": path, "problem": "unexpected property %r" % key})
                elif isinstance(extra, dict):
                    validate_value(item, extra, "%s.%s" % (path, key), problems)
            else:
                validate_value(item, sub_schema, "%s.%s" % (path, key), problems)
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            problems.append({"path": path, "problem": "fewer than minProperties %s" % schema["minProperties"]})
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            problems.append({"path": path, "problem": "more than maxProperties %s" % schema["maxProperties"]})


def _try_schema(value: Any, schema: Dict[str, Any]) -> bool:
    problems: List[Dict[str, str]] = []
    validate_value(value, schema, "", problems)
    return not problems


def validate_arguments(schema: Dict[str, Any], arguments: Dict[str, Any]) -> List[Dict[str, str]]:
    problems: List[Dict[str, str]] = []
    validate_value(arguments, schema, "", problems)
    return problems


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------


def _check(name: str, status: str, detail: str = "") -> Dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def _resolve_target(root: Path, work: Path, raw: str) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(raw, str) or not raw.strip():
        return None, "target argument is empty"
    candidate = Path(raw.strip().replace("/", "\\"))
    if candidate.is_absolute():
        return None, "target must be relative to the bridge work directory"
    target = (work / candidate).resolve()
    try:
        target.relative_to(work)
    except Exception:
        return None, "target escapes the bridge work directory"
    return target, None


def preflight(
    bridge_root: Any,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    version: Optional[str] = None,
    tool_index: Optional[Dict[str, Dict[str, Any]]] = None,
    action_map: Optional[Dict[str, Tuple[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a dry-run report. Never touches KOMPAS and never writes anything."""
    from settings import settings_for

    if tool_index is None or action_map is None:
        from tools_catalog import ACTION_MAP, TOOL_INDEX

        tool_index = tool_index or TOOL_INDEX
        action_map = action_map or ACTION_MAP

    arguments = dict(arguments or {})
    checks: List[Dict[str, str]] = []
    unverifiable = list(GENERAL_UNVERIFIABLE)
    notes: List[str] = []

    tool = tool_index.get(str(tool_name or ""))
    if tool is None:
        return {
            "tool": str(tool_name or ""),
            "action": None,
            "known_tool": False,
            "would_execute": False,
            "verdict": "blocked",
            "checks": [_check("tool_known", "fail", "unknown tool %r" % str(tool_name or ""))],
            "unverifiable": unverifiable,
            "limits": ["A dry run for an unknown tool cannot say anything useful."],
        }

    entry = action_map.get(tool["name"])
    action = entry[0] if entry else None
    annotations = tool.get("annotations") or {}
    read_only = annotations.get("readOnlyHint") is True
    spec = ACTION_PREFLIGHT.get(action or "", {})

    checks.append(_check("tool_known", "pass", tool["name"]))
    checks.append(
        _check(
            "mutating",
            "pass" if not read_only else "note",
            "action=%s" % action if not read_only else "tool is read-only; a dry run adds nothing",
        )
    )

    if not spec:
        checks.append(
            _check(
                "manifest_coverage",
                "unknown",
                "action %r has no preflight manifest entry; only the argument schema was checked" % action,
            )
        )
        unverifiable.append("What files this action touches (not described in the preflight manifest).")

    # --- arguments -------------------------------------------------------
    problems = validate_arguments(tool.get("inputSchema") or {}, arguments)
    checks.append(
        _check(
            "arguments",
            "pass" if not problems else "fail",
            "schema ok" if not problems else "%d problem(s)" % len(problems),
        )
    )

    # --- target ----------------------------------------------------------
    root = Path(bridge_root).resolve()
    policy = settings_for(root)
    work = Path(policy.work_dir).resolve()
    kind = spec.get("kind")
    target_key = spec.get("target_key")
    target: Optional[Path] = None

    if kind in ("view", "ui", "document"):
        checks.append(_check("file_target", "note", KIND_NOTES.get(kind, "")))
        if kind == "document":
            unverifiable.append("Which document will be active when the call runs.")
    elif target_key is None:
        checks.append(
            _check(
                "file_target",
                "unknown",
                "action %r works on the active document; run kompas_active_document to know what that is" % action,
            )
        )
    elif target_key not in arguments:
        checks.append(_check("file_target", "unknown", "no %r argument supplied" % target_key))
    else:
        raw = arguments[target_key]
        if kind == "script":
            jobs = _allowlist_names(root)
            name = str(raw or "")
            allowed = name in jobs
            checks.append(
                _check(
                    "job_allowlist",
                    "pass" if allowed else "fail",
                    "allowed jobs: %s" % (sorted(jobs) or "none - allow-list is empty or missing"),
                )
            )
        else:
            target, error = _resolve_target(root, work, raw)
            if error:
                checks.append(_check("target_path", "fail", error))
            else:
                checks.append(_check("target_path", "pass", str(target)))
                exists = target.is_file()
                if kind == "create":
                    checks.append(
                        _check(
                            "target_absent",
                            "pass" if not exists else "fail",
                            "must not exist for a create action",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "target_exists",
                            "pass" if exists else "fail",
                            str(target) if exists else "file not found",
                        )
                    )
                    suffix = spec.get("suffix")
                    if suffix:
                        ok = target.suffix.lower() == suffix
                        checks.append(
                            _check("target_suffix", "pass" if ok else "fail", "expected %s" % suffix)
                        )
                    if spec.get("agent_copy"):
                        ok = "_agent_copy" in target.stem.lower()
                        checks.append(
                            _check(
                                "agent_copy",
                                "pass" if ok else "fail",
                                "stem must contain _AGENT_COPY",
                            )
                        )
                    if exists:
                        writable = policy.is_writable(target)
                        checks.append(
                            _check("write_root", "pass" if writable else "fail", str(target.parent))
                        )
                        protected = policy.is_protected(target)
                        checks.append(
                            _check(
                                "not_protected",
                                "pass" if not protected else "fail",
                                "path is inside a protected root" if protected else "",
                            )
                        )

    # --- version guard ---------------------------------------------------
    guard = _version_guard(root, action, version)
    checks.append(guard["check"])
    if guard["check"]["status"] == "unknown":
        unverifiable.append("Whether the version guard allows this action on the running build.")

    # --- verdict ---------------------------------------------------------
    failed = [row for row in checks if row["status"] == "fail"]
    unknown = [row for row in checks if row["status"] == "unknown"]
    if failed:
        verdict = "blocked"
    elif unknown:
        verdict = "ready_with_unknowns"
    else:
        verdict = "ready"

    if not read_only:
        notes.append("Nothing was executed, opened, written or saved.")
    if kind == "modify":
        notes.append("A checkpoint is taken before the write when the tool supports it.")
    if kind == "script":
        notes.append("The allow-list is a boundary for the script name, not for what the script does.")

    return {
        "tool": tool["name"],
        "action": action,
        "known_tool": True,
        "read_only_tool": read_only,
        "kind": kind,
        "kind_note": KIND_NOTES.get(kind, ""),
        "would_execute": False,
        "verdict": verdict,
        "blocking_checks": [row["check"] for row in failed],
        "unknown_checks": [row["check"] for row in unknown],
        "checks": checks,
        "argument_problems": problems,
        "target": str(target) if target is not None else None,
        "work_directory": str(work),
        "version_guard": guard["detail"],
        "unverifiable": unverifiable,
        "notes": notes,
        "limits": [
            "A dry run proves that nothing detectable blocks the call. It does not prove the call succeeds.",
            "Checks that need Automation are listed under unverifiable instead of being guessed.",
        ],
    }


def _allowlist_names(root: Path) -> set:
    try:
        from jobs import allowed_jobs

        return {str(row.get("name") or row.get("id") or "") for row in allowed_jobs(root)}
    except Exception:
        return set()


def _version_guard(root: Path, action: Optional[str], version: Optional[str]) -> Dict[str, Any]:
    """Версия читается только если действие защищено - как и в воркере.

    Без переданной версии честный ответ «неизвестно»: поднимать «заблокировано»
    значило бы утверждать, что мы знаем версию running-КОМПАСа.
    """
    try:
        from settings import settings_for
        from version_matrix import guard_action, load_matrix

        matrix = settings_for(root).version_matrix
        guarded = (action or "") in load_matrix(matrix)["guarded_actions"]
    except Exception as exc:
        return {
            "check": _check("version_guard", "unknown", "matrix unavailable: %s" % type(exc).__name__),
            "detail": {"guarded": None, "allowed": None},
        }

    if not guarded:
        return {
            "check": _check("version_guard", "pass", "action is not version-guarded"),
            "detail": {"guarded": False, "allowed": True},
        }

    if not version:
        return {
            "check": _check(
                "version_guard",
                "unknown",
                "action is version-guarded; pass version to evaluate it",
            ),
            "detail": {"guarded": True, "allowed": None},
        }

    try:
        result = guard_action(action or "", version, matrix)
    except RuntimeError as exc:
        return {
            "check": _check("version_guard", "fail", str(exc)),
            "detail": {"guarded": True, "allowed": False},
        }
    return {
        "check": _check("version_guard", "pass", "guarded action allowed on this version"),
        "detail": result,
    }
