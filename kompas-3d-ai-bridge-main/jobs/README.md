# Task-specific COM jobs

This directory holds the only scripts `kompas_run_job` is allowed to execute.

## Allow-list manifest

Execution is controlled by `allowlist.json` in this directory. A script that is
not listed there is **never** executed, regardless of its content, its name or
its location inside this folder.

```json
{
  "version": 1,
  "jobs": [
    "job_template.py",
    { "name": "bom_export.py", "description": "BOM export", "timeout_sec": 120, "enabled": true }
  ]
}
```

* A bare string is equivalent to `{"enabled": true}`.
* `timeout_sec` is a per-job default; the caller may request less, never more
  than `job_timeout_max_sec` from `config/agent_config.json`.
* `enabled: false` keeps an entry documented but refuses to run it.
* A missing or malformed manifest means an **empty** allow-list. It never means
  "allow everything".

## What changed and why

The previous check read the script as text and rejected it if it contained a
protected source-root path as a substring:

```python
source = script.read_text(...).lower()
if any(str(x).lower() in source for x in PROTECTED):
    raise PermissionError("job_references_protected_path")
```

That is not a security boundary. The path can be assembled from fragments, from
`chr()` calls, or from an environment variable, and the check passes. The check
is now a **path** check: the job's own location must not sit inside a protected
root. Permission to execute comes from the manifest instead.

## Rules

1. Start from `job_template.py`.
2. Implement one engineering operation only.
3. Do not launch shells, network listeners, downloaders, or unrelated programs.
4. Do not write to project source/stable directories. Work on a copy under
   `KOMPAS_BRIDGE/work` or approved output.
5. Return a compact JSON/text result on stdout and exit non-zero on failure.
6. After any modification, run structured API QA plus `kompas_viewport_capture`.
7. If the same job becomes common, promote it into a reviewed stable tool
   (`mcp/tools_catalog.py`) and remove it from this directory.

## Scope of this boundary - read before relying on it

The manifest only governs what `kompas_run_job` will execute. It does not stop a
coding agent that has its own file-write tools from editing `allowlist.json` and
then running whatever it just added. The bridge's own write policy
(`src/safety.py`) permits writes only under `work/` and the approved output
roots, so no bridge tool can reach this directory - but a client-side file tool
is outside the bridge's control.

Practical consequences:

* treat `jobs/` and `allowlist.json` as part of the trusted code base, not as
  user data;
* review changes to this directory the same way you review changes to `src/`;
* prefer promoting recurring jobs into stable tools, where the argument schema
  and the write policy apply.

Historical project jobs from the source bridge are preserved in
`legacy/jobs_reference/` in the distribution archive and are **not installed into
the active executable allow-list**.
