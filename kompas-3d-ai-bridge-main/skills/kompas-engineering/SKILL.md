---
name: kompas-engineering
description: Rules for autonomous engineering work in KOMPAS-3D v25 through structured tools, COM jobs, and visual QA.
---

# KOMPAS Engineering Agent Rules

## Core operating loop
Always use both channels:
1. exact channel: KOMPAS API/COM structured data;
2. visual channel: a fresh KOMPAS viewport capture.

Normal loop:
`status -> tree/bbox/component data -> set view -> fresh capture -> reason -> modify through stable tool or bounded COM job -> fresh capture -> API QA -> visual QA -> restore temporary view state -> save -> close/reopen -> final API + visual QA`.

A pretty screenshot is not proof of engineering correctness. API PASS alone is not proof of visual correctness.

## Model structure
- Prefer native M3D/A3D over exchange-only geometry when native structure is required.
- Each real manufactured part should be a separate M3D when project requirements require separate parts.
- Use meaningful subassemblies and engineering structure.
- Account for purchased fasteners/components according to project requirements; do not silently omit them.
- Each main assembly needs deliberate bases/origins/placements.
- Native dimensions must be real dimension objects, not text pretending to be dimensions.

## Truthfulness of engineering data
Never invent:
- material;
- density;
- mass;
- fastener type/grade;
- thickness;
- tolerance;
- coordinates;
- dimensions;
- structural parameters.

Read them from the model/source requirements or explicitly mark them unresolved. If mass is not natively available or material is unknown, report that rather than creating a plausible value.

## Assembly mates
- A set of positioned components is not an assembly. Related parts must be tied by real mate constraints, not only by absolute placement.
- Read the current mate set with `kompas_mate_read` before adding anything. `interface.confirmed=false` means the mate interface was not found in this KOMPAS build - it does NOT mean the assembly has no mates. Report it as unresolved instead of assuming an empty assembly.
- Give each component a deliberate origin first, then constrain it. Mates are not a substitute for explicit placement.
- Add mates one at a time with `kompas_mate_create` and confirm the mate count grew after each call. Never batch several mates into one step.
- `distance` and `angle` require a value in mm or degrees. `coincident`, `parallel`, `perpendicular`, `concentric` and `tangent` must not carry a value.
- `mate_create_interface_not_available` means the create interface could not be resolved. Report it as an unresolved operation; never present it as a created mate.
- Record the `interface.create_method` value from the response when working on a new KOMPAS version.

## Export verification
- Use `kompas_export` for STEP/DXF/PDF: it exports and verifies the file in the same call, so "the call returned" and "the file is good" become the same event.
- A KOMPAS export method raises nothing when the result is empty, truncated or header-only. `ok: false` from `kompas_export` means the export failed - fix the cause, do not hand the file to the user. The bad file is kept on purpose so it can be inspected.
- `apply=false` is a dry run: it reports the plan and the method names it would try, without any COM call. Use it before a first export on an unfamiliar build.
- For files produced by `kompas_run_job`, run `kompas_artifact_check` separately - that is the only confirmation available for them.
- The check is structural. When STEP is a deliverable, additionally import it back and compare overall dimensions: neither `kompas_export` nor `kompas_artifact_check` proves geometry equivalence.

## Checkpoints and rollback
- `kompas_checkpoints list` shows the byte snapshots taken before mutating operations. Before a risky multi-step change, know which snapshot you could return to.
- `restore` needs the exact id and the matching target name, and works only inside approved bridge roots. After a restore, close and reopen the document in KOMPAS: an open document still holds the old content in memory.
- Snapshots accumulate. Run `prune` with a `keep` value as regular maintenance.
- A snapshot is file-level. It does not undo a change to a document that KOMPAS still holds open and unsaved.

## Dry run before a mutating call
- Run `kompas_dry_run` with the tool name and the arguments you intend to pass. It checks the argument schema, resolves the target file, and verifies path policy, the version guard and the job allow-list - without opening, writing or saving anything.
- `verdict: blocked` means a named check failed. Read `blocking_checks` and fix the cause; do not retry the real call hoping it behaves differently.
- `verdict: ready_with_unknowns` means nothing detectable blocks the call, but something could not be answered without Automation. Look at `unknown_checks` before proceeding - for actions on the active document, run `kompas_active_document` first and then re-run the dry run.
- `ready` is not a promise that the operation succeeds. Everything that needs the live application is listed under `unverifiable` instead of being guessed.
- A dry run for a read-only tool adds nothing; it says so instead of inventing work.

## Drawings, title block, specification
- A 3D model and a 2D drawing are different documents. Before any drawing work, confirm which document is active with `kompas_active_document`; `kompas_drawing_info` refuses a 3D document with a clear error instead of silently reading nothing.
- Use `kompas_drawing_info` to learn the sheet count, the format of each sheet and the sheet index the stamp tools need. It also lists views when the view collection resolves; `confirmed: false` means the bridge could not reach the view collection - that is not the same answer as "there are no views".
- The bridge does **not** name title block cells. Cell numbering depends on the block style of the template, so `kompas_stamp_read` reports numbers and values. Read the first 60 cells once on an unfamiliar template and map them yourself; never assume cell 1 means "name".
- `kompas_stamp_write` takes at most 20 cells per call, rejects control characters and text over 255 characters, and requires a saved `_AGENT_COPY` inside approved roots. It takes a checkpoint, reads every cell back, then Save, Close, Reopen and reads back again; a mismatch rolls the file back from the checkpoint.
- `kompas_specification_read` reports a **derived** section layout, not a real `.spw` document: Standard -> standard products, IsBillet -> materials, a component with children -> assembly units, otherwise details. Positions are numbered by the bridge. Documentation and Kits cannot come from the assembly tree and are never filled.
- If `cross_check.ok` is false, the derived quantity disagrees with the eligible occurrence count - treat the layout as unreliable and inspect the tree instead of reporting it.

## Stuck modal dialogs
- A stuck modal dialog freezes the whole bridge: COM calls hang and every later tool times out. When a call times out with no obvious cause, run `kompas_dialogs` before retrying anything.
- `kompas_dialogs` avoids COM deliberately: an Automation call would hang on the very dialog being diagnosed. It works even when KOMPAS is unresponsive.
- Only informational dialogs are closable. Dialogs that ask to save, choose or confirm are never closed automatically - report them and stop. The decision belongs to the human.
- `apply=true` closes at most three windows per call. Do not raise that limit by calling it in a loop.
- If the scan reports a blocking dialog, do not continue the engineering task as if it succeeded.

## KOMPAS version
- Before trusting a write on an unfamiliar KOMPAS build, run `kompas_version_check`. An empty matrix means "not verified", not "supported".
- The matrix lives in `config/agent_config.json` under `kompas.version_matrix` and is filled by the operator. Do not add entries for builds you have not actually exercised.
- Actions listed in `guarded_actions` are refused on unverified versions with the code `kompas_version_not_verified`. Do not work around it by editing the config.
- When an interface was resolved by trying known name variants, record the one that worked in the matrix notes so the next session does not rediscover it.

## Temporary visual state
`hide`, `isolate`, view rotation, and similar operations are inspection aids. Restore temporary visibility before saving unless changed visibility is explicitly part of the requested deliverable.

## References and portability
Before final completion:
- `missing refs = 0`;
- inspect external/absolute dependencies;
- verify assembly structure after reopen;
- verify STEP by opening/importing the exported STEP when STEP is a deliverable.

## Save discipline
For write operations:
- work on a COPY or approved output path;
- never write to protected source/stable roots;
- save;
- close;
- reopen;
- re-read exact data;
- capture a fresh viewport;
- only then declare persistence.

## Unsupported operations
Do not build a universal wrapper of the whole KOMPAS SDK. If a required engineering operation is not a stable MCP tool:
1. create a narrowly scoped Python COM job under `KOMPAS_BRIDGE/jobs` only;
2. execute with `kompas_run_job`;
3. inspect its structured stdout/error;
4. verify the result through normal API tools;
5. capture a fresh viewport and perform visual QA;
6. if the operation becomes common, promote it into a stable tool later.

## QA minimum
API QA should cover, as applicable:
- component counts and tree;
- material/mass/density provenance;
- dimensions and transforms;
- bounding boxes;
- references and missing refs;
- save/reopen persistence;
- mate set: count before and after each added mate, kind and value readback;
- exported artifacts verified with `kompas_artifact_check`, not by the absence of an export error;
- blocking modal dialogs cleared or reported before the task is declared finished;
- specifications and exports when required.

Vision QA should cover, as applicable:
- front;
- rear;
- iso;
- side/top;
- local assemblies;
- hidden/isolate inspection;
- sections where supported;
- overlaps, wrong-side placement, clipping, strange display, and readable annotations.

Never declare the CAD task complete after only one QA channel passes.
