"""2D drawing operations: sheet/format inventory, view inventory, title block (stamp).

Split deliberately:
* validation and comparison are pure functions, testable without KOMPAS;
* only the calls that touch Automation are allowed to fail with COM errors.

Cell numbering of a title block depends on the block style in use, so this module
never guesses a "name" for a cell number. It reads and writes numbers and lets the
operator declare labels in config if wanted.
"""

from __future__ import annotations

import json
from pathlib import Path

from assembly_insert_tools import _close, _norm, _open_exact, _sha256
from safety import assert_writable
import checkpoint as checkpoint_store

MAX_CELL_NUMBER = 200
MAX_CELLS_PER_CALL = 200
DEFAULT_READ_CELLS = 60
MAX_STAMP_WRITES = 20
MAX_TEXT_LENGTH = 255
MAX_SHEETS = 100
MAX_VIEWS_HARD = 200
DEFAULT_MAX_VIEWS = 50

# --------------------------------------------------------------------------
# pure validation
# --------------------------------------------------------------------------


def normalize_cell_number(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("stamp_cell_number_must_be_integer_in_1_200")
    if value < 1 or value > MAX_CELL_NUMBER:
        raise ValueError("stamp_cell_number_out_of_range_1_%d:%s" % (MAX_CELL_NUMBER, value))
    return int(value)


def range_cells(count):
    try:
        count = int(count)
    except Exception as exc:
        raise ValueError("stamp_read_cells_must_be_integer") from exc
    if count < 1 or count > MAX_CELL_NUMBER:
        raise ValueError("stamp_read_cells_out_of_range_1_%d:%s" % (MAX_CELL_NUMBER, count))
    return list(range(1, count + 1))


def normalize_cells(values, default=DEFAULT_READ_CELLS):
    """Accept None (means "read the first N cells") or a list of cell numbers."""
    if values is None:
        return range_cells(default)
    if isinstance(values, int) and not isinstance(values, bool):
        return range_cells(values)
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("stamp_cells_must_be_nonempty_list_of_integers")
    selected = sorted({normalize_cell_number(item) for item in values})
    if len(selected) > MAX_CELLS_PER_CALL:
        raise ValueError(
            "stamp_cells_exceed_limit_%d:%d" % (MAX_CELLS_PER_CALL, len(selected))
        )
    return selected


def _as_cell_key(key):
    if isinstance(key, bool):
        raise ValueError("stamp_cell_key_must_be_integer_or_numeric_string")
    if isinstance(key, int):
        return int(key)
    if isinstance(key, str) and key.strip().lstrip("+-").isdigit():
        return int(key.strip())
    raise ValueError("stamp_cell_key_must_be_integer_or_numeric_string:" + repr(key))


def clean_stamp_text(value):
    if not isinstance(value, str):
        raise ValueError("stamp_text_must_be_string")
    if len(value) > MAX_TEXT_LENGTH:
        raise ValueError("stamp_text_too_long_max_%d:%d" % (MAX_TEXT_LENGTH, len(value)))
    for char in value:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            raise ValueError("stamp_text_contains_control_characters")
    return value


def normalize_stamp_writes(cells):
    if not isinstance(cells, dict) or not cells:
        raise ValueError("stamp_write_cells_must_be_nonempty_object")
    if len(cells) > MAX_STAMP_WRITES:
        raise ValueError(
            "stamp_write_cells_exceed_limit_%d:%d" % (MAX_STAMP_WRITES, len(cells))
        )
    writes = {}
    for key, value in cells.items():
        number = normalize_cell_number(_as_cell_key(key))
        writes[number] = clean_stamp_text(value)
    return writes


def normalize_sheet_index(value, default=0):
    if value is None:
        return int(default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("sheet_index_must_be_integer")
    if value < 0 or value > MAX_SHEETS - 1:
        raise ValueError("sheet_index_out_of_range_0_%d:%s" % (MAX_SHEETS - 1, value))
    return int(value)


def normalize_max_views(value, default=DEFAULT_MAX_VIEWS):
    if value is None:
        return int(default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_views_must_be_integer")
    if value < 1 or value > MAX_VIEWS_HARD:
        raise ValueError("max_views_out_of_range_1_%d:%s" % (MAX_VIEWS_HARD, value))
    return int(value)


def stamp_diff(expected, actual):
    """Compare requested cell values against what the document actually holds."""
    missing = []
    mismatched = []
    for number in sorted(expected):
        text = expected[number]
        if number not in actual:
            missing.append(number)
            continue
        if actual[number] != text:
            mismatched.append(
                {"cell": number, "expected": text, "actual": actual[number]}
            )
    return {
        "missing": missing,
        "mismatched": mismatched,
        "ok": not missing and not mismatched,
    }


def non_empty_cells(values):
    return sorted(number for number, text in values.items() if str(text or "").strip())


# --------------------------------------------------------------------------
# Automation access
# --------------------------------------------------------------------------


def _attr(obj, name, default=None):
    """Read an Automation member. Never call it: COM methods need arguments."""
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _number(value):
    try:
        result = float(value)
    except Exception:
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _document(session):
    doc = session.active()
    if doc is None:
        raise RuntimeError("no_active_document")
    return doc


def _layout_sheets(doc):
    sheets = _attr(doc, "LayoutSheets")
    if sheets is None:
        raise RuntimeError(
            "active_document_has_no_layout_sheets_not_a_2d_document:document_type="
            + str(_attr(doc, "DocumentType", "") or "")
        )
    return sheets


def _sheet(sheets, index):
    count = int(_attr(sheets, "Count", 0) or 0)
    if count <= 0:
        raise RuntimeError("document_has_no_layout_sheets")
    if index >= count:
        raise RuntimeError("sheet_index_out_of_range:%d_of_%d" % (index, count))
    return sheets.Item(int(index))


def _format_row(sheet):
    fmt = _attr(sheet, "Format")
    row = {
        "index": None,
        "name": str(_attr(sheet, "Name", "") or ""),
        "width": None,
        "height": None,
        "format": None,
    }
    if fmt is not None:
        row["width"] = _number(_attr(fmt, "Width"))
        row["height"] = _number(_attr(fmt, "Height"))
        row["format"] = _attr(fmt, "Format")
    return row


def sheet_rows(doc):
    sheets = _layout_sheets(doc)
    count = int(_attr(sheets, "Count", 0) or 0)
    rows = []
    for index in range(min(count, MAX_SHEETS)):
        row = _format_row(sheets.Item(index))
        row["index"] = index
        rows.append(row)
    return rows, count


def _views(doc, max_views):
    """Read the view inventory. Degrades honestly instead of returning []."""
    attempts = []
    try:
        import win32com.client as wc
    except Exception as exc:
        return [], {
            "confirmed": False,
            "access": None,
            "attempts": ["import:win32com.client"],
            "error": type(exc).__name__ + ":" + str(exc),
        }

    try:
        flat = wc.CastTo(doc, "IKompasDocument2D")
    except Exception as exc:
        return [], {
            "confirmed": False,
            "access": None,
            "attempts": ["CastTo:IKompasDocument2D"],
            "error": type(exc).__name__ + ":" + str(exc),
        }

    for path in ("ViewsAndLayersManager.Views", "Views"):
        node = flat
        ok = True
        for part in path.split("."):
            node = _attr(node, part)
            if node is None:
                ok = False
                break
        if not ok:
            attempts.append(path)
            continue
        try:
            count = int(_attr(node, "Count", 0) or 0)
        except Exception as exc:
            attempts.append(path + ".Count")
            continue
        rows = []
        for index in range(min(count, max_views)):
            try:
                view = node.Item(index)
            except Exception:
                continue
            rows.append(
                {
                    "index": index,
                    "name": str(_attr(view, "Name", "") or ""),
                    "scale": _number(_attr(view, "Scale")),
                    "projection_type": _attr(view, "ProjectionType"),
                    "state": _attr(view, "State"),
                }
            )
        return rows, {
            "confirmed": True,
            "access": path,
            "attempts": attempts,
            "view_count": count,
            "error": None,
        }

    return [], {
        "confirmed": False,
        "access": None,
        "attempts": attempts,
        "error": "no_view_collection_resolved",
    }


def _stamp(sheet):
    stamp = _attr(sheet, "Stamp")
    if stamp is None:
        raise RuntimeError("sheet_has_no_stamp")
    return stamp


def _read_cells(stamp, numbers):
    values = {}
    errors = []
    for number in numbers:
        try:
            cell = stamp.Text(int(number))
            values[number] = "" if cell is None else str(_attr(cell, "Str", "") or "")
        except Exception as exc:
            errors.append(
                {"cell": number, "error": type(exc).__name__ + ":" + str(exc)}
            )
    return values, errors


def _write_cells(stamp, writes):
    applied = {}
    for number in sorted(writes):
        cell = stamp.Text(int(number))
        cell.Str = writes[number]
        applied[number] = writes[number]
    return applied


# --------------------------------------------------------------------------
# read operations
# --------------------------------------------------------------------------


def drawing_info(session, sheet_index=None, max_views=DEFAULT_MAX_VIEWS, include_views=True):
    doc = _document(session)
    index = normalize_sheet_index(sheet_index)
    rows, count = sheet_rows(doc)
    if index >= count:
        raise RuntimeError("sheet_index_out_of_range:%d_of_%d" % (index, count))
    result = {
        "requested_action": {
            "sheet_index": index,
            "max_views": normalize_max_views(max_views),
            "include_views": bool(include_views),
            "read_only": True,
        },
        "active_document": str(session.active_path() or ""),
        "document_type": str(_attr(doc, "DocumentType", "") or ""),
        "sheet_count": count,
        "sheets": rows,
        "requested_sheet_index": index,
        "interface": {"confirmed": True, "access": "IKompasDocument.LayoutSheets"},
        "read_only": True,
        "saved": False,
        "save_required": False,
    }
    if include_views:
        view_rows, view_meta = _views(doc, normalize_max_views(max_views))
        result["views"] = view_rows
        result["views_interface"] = view_meta
    return result


def stamp_read(session, cells=None, sheet_index=None):
    doc = _document(session)
    index = normalize_sheet_index(sheet_index)
    sheets = _layout_sheets(doc)
    sheet = _sheet(sheets, index)
    stamp = _stamp(sheet)
    wanted = normalize_cells(cells)
    values, errors = _read_cells(stamp, wanted)
    return {
        "requested_action": {
            "cells": wanted,
            "sheet_index": index,
            "read_only": True,
        },
        "active_document": str(session.active_path() or ""),
        "document_type": str(_attr(doc, "DocumentType", "") or ""),
        "sheet_index": index,
        "sheet_count": int(_attr(sheets, "Count", 0) or 0),
        "requested_cell_count": len(wanted),
        "read_cell_count": len(values),
        "non_empty_cell_count": len(non_empty_cells(values)),
        "non_empty_cells": non_empty_cells(values),
        "cells": {str(number): values[number] for number in sorted(values)},
        "read_errors": errors,
        "interface": {"confirmed": True, "access": "ILayoutSheet.Stamp.Text(n).Str"},
        "limits": [
            "Cell numbering depends on the title block style of this document; this tool reports numbers, it does not name them.",
            "An empty cell is reported as an empty string - it does not prove the cell does not exist in the block.",
        ],
        "read_only": True,
        "saved": False,
        "save_required": False,
    }


# --------------------------------------------------------------------------
# write operation
# --------------------------------------------------------------------------


def _rollback(root, row, target):
    if row is None:
        return False
    try:
        checkpoint_store.restore(root, row["id"], expected_target=target)
        return True
    except Exception:
        return False


def stamp_write(session, cells, sheet_index=None, use_checkpoint=True):
    writes = normalize_stamp_writes(cells)
    index = normalize_sheet_index(sheet_index)

    app = session.connect()
    doc = session.active()
    if doc is None:
        raise RuntimeError("no_active_document")
    raw_target = str(session.active_path() or "")
    if not raw_target:
        raise RuntimeError("stamp_write_requires_a_saved_active_document")
    target = Path(raw_target).resolve()
    if not target.is_file():
        raise RuntimeError("stamp_write_target_is_not_a_file:" + str(target))
    if "_agent_copy" not in target.stem.lower():
        raise RuntimeError("active_document_not_safe_AGENT_COPY:" + str(target))
    assert_writable(target, session.root)

    root = Path(session.root)
    snapshot_row = None
    if use_checkpoint:
        snapshot_row = checkpoint_store.create(root, target, action="stamp.write")
    before_hash = _sha256(target)

    sheets = _layout_sheets(doc)
    sheet = _sheet(sheets, index)
    stamp = _stamp(sheet)
    before_values, _ = _read_cells(stamp, sorted(writes))

    reopened = None
    saved = False
    closed_active = False
    try:
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError(
                "active_document_is_not_the_write_target:"
                + str(session.active_path() or "")
            )
        _write_cells(stamp, writes)
        try:
            update_return = stamp.Update()
        except Exception:
            update_return = None

        after_write, _ = _read_cells(stamp, sorted(writes))
        diff_before = stamp_diff(writes, after_write)
        if not diff_before["ok"]:
            raise RuntimeError(
                "stamp_values_readback_mismatch_before_save:"
                + json.dumps(diff_before, ensure_ascii=False)
            )

        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError(
                "active_document_changed_before_Save:" + str(session.active_path() or "")
            )
        save_result = doc.Save()
        if save_result is False:
            raise RuntimeError("IKompasDocument.Save_returned_false")
        saved = True

        _close(doc)
        closed_active = True
        reopened = _open_exact(app, target)
        if _norm(session.active_path() or "") != _norm(target):
            raise RuntimeError(
                "reopened_stamp_target_not_exact_active:"
                + str(session.active_path() or "")
            )
        reopened_sheets = _layout_sheets(reopened)
        reopened_sheet = _sheet(reopened_sheets, index)
        after_values, _ = _read_cells(_stamp(reopened_sheet), sorted(writes))
        diff_after = stamp_diff(writes, after_values)
        if not diff_after["ok"]:
            raise RuntimeError(
                "stamp_values_readback_mismatch_after_reopen:"
                + json.dumps(diff_after, ensure_ascii=False)
            )
        after_hash = _sha256(target)

        return {
            "requested_action": {
                "cells": {str(k): writes[k] for k in sorted(writes)},
                "sheet_index": index,
            },
            "active_document": str(session.active_path() or ""),
            "target": str(target),
            "before": {
                "cells": {str(k): before_values.get(k) for k in sorted(writes)},
                "sha256": before_hash,
            },
            "after": {
                "cells": {str(k): after_values.get(k) for k in sorted(writes)},
                "sha256": after_hash,
            },
            "changed": before_hash != after_hash,
            "saved": saved,
            "save_required": False,
            "checkpoint_id": (snapshot_row or {}).get("id"),
            "warnings": (
                []
                if update_return is not False
                else [
                    "IStamp.Update returned false; accepted only because every written cell read back correctly after Save, Close and Reopen."
                ]
            ),
            "verification": {
                "native_api": "ILayoutSheet.Stamp.Text(n).Str + IStamp.Update",
                "exact_active_path_before_write": True,
                "exact_active_path_before_save": True,
                "readback_before_save_pass": True,
                "readback_after_reopen_pass": True,
                "save_close_reopen_verified": True,
                "checkpoint_created": snapshot_row is not None,
                "write_count": len(writes),
                "sheet_index": index,
                "stamp_update_return": update_return,
            },
            "actual": {str(k): after_values.get(k) for k in sorted(writes)},
            "readback": {str(k): after_values.get(k) for k in sorted(writes)},
            "limits": [
                "Cell numbers are not named by the bridge: the style of the title block decides what cell 1 means.",
                "This writes an existing title block; it does not change the block style or add sheets.",
            ],
        }

    except Exception as exc:
        if reopened is not None:
            try:
                _close(reopened)
            except Exception:
                pass
        if not closed_active:
            try:
                _close(doc)
            except Exception:
                pass
        rolled_back = _rollback(root, snapshot_row, target)
        try:
            _open_exact(app, target)
        except Exception:
            pass
        raise RuntimeError(
            "stamp_write_failed:%s:rolled_back=%s:checkpoint=%s"
            % (exc, rolled_back, (snapshot_row or {}).get("id"))
        ) from None
