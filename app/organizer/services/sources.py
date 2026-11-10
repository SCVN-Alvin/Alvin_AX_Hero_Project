"""Reading and curating imported source documents."""

import json
import os

from .. import validation as v
from ..db import tx
from ..errors import Conflict, NotFound, ValidationError
from ..importer import INLINE_TYPES
from ..scenarios import detect_columns, extract_rows
from .projects import get_project_row

import uuid


def _doc_row(conn, doc_id):
    row = conn.execute("SELECT * FROM source_document WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise NotFound("Source document not found")
    return row


def _loads(text, default):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def asset_dict(row):
    media = row["media_type"] or ""
    return {
        "id": row["id"], "name": row["original_name"], "source_path": row["source_path"], "media_type": media,
        "size": row["size_bytes"], "availability": row["availability"], "problem": row["problem"],
        "hash": row["content_hash"], "inline_ok": media in INLINE_TYPES,
        "kind": (media.split("/")[0] if media.split("/")[0] in ("image", "video", "audio") else "file"),
    }


def _counts(conn, doc_id):
    c = conn.execute(
        "SELECT (SELECT COUNT(*) FROM source_block WHERE document_id = ?) AS blocks,"
        " (SELECT COUNT(*) FROM source_section WHERE document_id = ?) AS sections,"
        " (SELECT COUNT(*) FROM source_block WHERE document_id = ? AND block_type = 'table') AS tables,"
        " (SELECT COUNT(*) FROM scenario WHERE document_id = ?) AS scenarios,"
        " (SELECT COUNT(*) FROM asset WHERE document_id = ?) AS assets,"
        " (SELECT COUNT(*) FROM asset WHERE document_id = ? AND availability = 'available') AS assets_available,"
        " (SELECT COALESCE(SUM(size_bytes), 0) FROM asset WHERE document_id = ? AND availability = 'available') AS asset_bytes,"
        " (SELECT COUNT(*) FROM import_diagnostic WHERE document_id = ?) AS diagnostics,"
        " (SELECT COUNT(*) FROM source_section WHERE document_id = ? AND role = 'historical') AS historical_sections",
        (doc_id,) * 9).fetchone()
    d = dict(c)
    d["assets_unavailable"] = d["assets"] - d["assets_available"]
    return d


def document_dict(row, counts):
    return {
        "id": row["id"], "project_id": row["project_id"], "name": row["original_name"], "title": row["title"],
        "version": row["version"], "size": row["size_bytes"], "sha256": row["content_hash"], "companion_name": row["companion_name"],
        "parser_version": row["parser_version"], "created_at": row["created_at"], "counts": counts,
    }


def list_documents(conn, project_id):
    get_project_row(conn, project_id)
    rows = conn.execute("SELECT * FROM source_document WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
    return [document_dict(r, _counts(conn, r["id"])) for r in rows]


def list_runs(conn, project_id, limit=15):
    get_project_row(conn, project_id)
    rows = conn.execute("SELECT * FROM import_run WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
    return [{"id": r["id"], "document_id": r["document_id"], "kind": r["kind"], "state": r["state"], "source_name": r["source_name"],
             "error": r["error"], "note": r["note"], "created_at": r["created_at"], "finished_at": r["finished_at"]} for r in rows]


def get_document(conn, doc_id):
    row = _doc_row(conn, doc_id)
    sections = conn.execute("SELECT * FROM source_section WHERE document_id = ? ORDER BY position", (doc_id,)).fetchall()
    tables = conn.execute(
        "SELECT id, sequence, heading_path, table_rows, table_cols, table_header_rows, variant FROM source_block "
        "WHERE document_id = ? AND block_type = 'table' ORDER BY sequence", (doc_id,)).fetchall()
    table_list = []
    for t in tables:
        head = conn.execute(
            "SELECT col_index, original_text FROM source_cell WHERE block_id = ? AND row_index = ? ORDER BY col_index",
            (t["id"], max((t["table_header_rows"] or 1) - 1, 0))).fetchall()
        table_list.append({
            "block_id": t["id"], "sequence": t["sequence"], "heading_path": _loads(t["heading_path"], []),
            "rows": t["table_rows"], "cols": t["table_cols"], "header_rows": t["table_header_rows"] or 0,
            "kind": t["variant"], "header": [h["original_text"] for h in head],
            "scenario_count": conn.execute("SELECT COUNT(*) FROM scenario WHERE source_block_id = ?", (t["id"],)).fetchone()[0],
        })
    diags = conn.execute(
        "SELECT d.*, b.sequence AS seq FROM import_diagnostic d LEFT JOIN source_block b ON b.id = d.source_block_id "
        "WHERE d.document_id = ? ORDER BY d.position", (doc_id,)).fetchall()
    out = document_dict(row, _counts(conn, doc_id))
    out["sections"] = [{
        "id": s["id"], "parent_id": s["parent_section_id"], "position": s["position"], "level": s["level"], "title": s["title"],
        "heading_path": _loads(s["heading_path"], []), "first_sequence": s["first_sequence"], "last_sequence": s["last_sequence"],
        "role": s["role"], "role_origin": s["role_origin"], "role_reason": s["role_reason"],
    } for s in sections]
    out["tables"] = table_list
    out["diagnostics"] = [{"severity": d["severity"], "code": d["code"], "message": d["message"], "sequence": d["seq"]} for d in diags]
    return out


def _block_rows_to_dicts(conn, rows):
    ids = [r["id"] for r in rows]
    cells, refs = {}, {}
    if ids:
        marks = ",".join("?" * len(ids))
        for c in conn.execute(f"SELECT * FROM source_cell WHERE block_id IN ({marks}) ORDER BY row_index, col_index", ids):
            cells.setdefault(c["block_id"], []).append({
                "row": c["row_index"], "col": c["col_index"], "header": bool(c["is_header"]), "text": c["original_text"],
                "runs": _loads(c["inline_json"], []), "element_id": c["source_element_id"]})
        q = (f"SELECT r.*, a.original_name AS a_name, a.source_path AS a_path, a.media_type AS a_type, a.size_bytes AS a_size, "
             f"a.availability AS a_avail, a.problem AS a_problem, a.content_hash AS a_hash "
             f"FROM reference r LEFT JOIN asset a ON a.id = r.asset_id WHERE r.source_block_id IN ({marks}) ORDER BY r.position")
        for r in conn.execute(q, ids):
            asset = None
            if r["asset_id"]:
                asset = asset_dict({"id": r["asset_id"], "original_name": r["a_name"], "source_path": r["a_path"], "media_type": r["a_type"],
                                    "size_bytes": r["a_size"], "availability": r["a_avail"], "problem": r["a_problem"],
                                    "content_hash": r["a_hash"]})
            refs.setdefault(r["source_block_id"], []).append({
                "id": r["id"], "kind": r["kind"], "placement": r["placement"], "asset": asset, "external_url": r["external_url"],
                "raw_target": r["raw_target"], "link_text": r["link_text"], "caption": r["caption"]})
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "sequence": r["sequence"], "type": r["block_type"], "variant": r["variant"], "depth": r["depth"],
            "parent_id": r["parent_block_id"], "element_id": r["source_element_id"], "heading_level": r["heading_level"],
            "list_kind": r["list_kind"], "list_index": r["list_index"], "checked": r["checked"],
            "text": r["original_text"], "runs": _loads(r["inline_json"], []), "heading_path": _loads(r["heading_path"], []),
            "section_id": r["section_id"], "section_role": r["section_role"],
            "column_group": r["column_group"], "column_index": r["column_index"],
            "rows": r["table_rows"], "cols": r["table_cols"], "header_rows": r["table_header_rows"],
            "cells": cells.get(r["id"], []), "references": refs.get(r["id"], []),
        })
    return out


_BLOCK_SELECT = ("SELECT b.*, s.role AS section_role FROM source_block b LEFT JOIN source_section s ON s.id = b.section_id ")


def get_blocks(conn, doc_id, start=0, limit=400, section_id=None):
    _doc_row(conn, doc_id)
    limit = max(1, min(int(limit), 1000))
    start = max(0, int(start))
    total = conn.execute("SELECT COUNT(*) FROM source_block WHERE document_id = ?", (doc_id,)).fetchone()[0]
    rows = conn.execute(_BLOCK_SELECT + "WHERE b.document_id = ? AND b.sequence >= ? ORDER BY b.sequence LIMIT ?",
                        (doc_id, start, limit)).fetchall()
    blocks = _block_rows_to_dicts(conn, rows)
    next_start = blocks[-1]["sequence"] + 1 if blocks and blocks[-1]["sequence"] + 1 < total else None
    return {"blocks": blocks, "start": start, "next_start": next_start, "total": total}


def _like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_blocks(conn, doc_id, q, limit=200):
    _doc_row(conn, doc_id)
    q = (q or "").strip()
    if not q:
        return {"query": "", "hits": [], "truncated": False}
    needle = q.casefold()
    rows = conn.execute(
        "SELECT b.id, b.sequence, b.block_type, b.original_text, b.heading_path, s.role AS section_role, s.title AS section_title "
        "FROM source_block b LEFT JOIN source_section s ON s.id = b.section_id "
        "WHERE b.document_id = ? AND fold(b.original_text) LIKE ? ESCAPE '\\' ORDER BY b.sequence LIMIT ?",
        (doc_id, f"%{_like_escape(needle)}%", limit + 1)).fetchall()
    hits = []
    for r in rows[:limit]:
        text = r["original_text"]
        i = text.casefold().find(needle)
        i = max(i, 0)
        a = max(0, i - 50)
        snippet = (("…" if a else "") + " ".join(text[a:i + len(needle) + 90].split()) + ("…" if i + len(needle) + 90 < len(text) else ""))
        hits.append({"block_id": r["id"], "sequence": r["sequence"], "type": r["block_type"], "snippet": snippet,
                     "heading_path": _loads(r["heading_path"], []), "section_role": r["section_role"]})
    return {"query": q, "hits": hits, "truncated": len(rows) > limit}


def list_assets(conn, doc_id):
    _doc_row(conn, doc_id)
    rows = conn.execute(
        "SELECT a.*, (SELECT COUNT(*) FROM reference r WHERE r.asset_id = a.id) AS uses FROM asset a WHERE a.document_id = ? ORDER BY a.rowid",
        (doc_id,)).fetchall()
    out = []
    for r in rows:
        d = asset_dict(r)
        d["uses"] = r["uses"]
        first = conn.execute(
            "SELECT b.sequence, b.heading_path, b.id FROM reference r JOIN source_block b ON b.id = r.source_block_id "
            "WHERE r.asset_id = ? ORDER BY b.sequence LIMIT 5", (r["id"],)).fetchall()
        d["used_in"] = [{"block_id": f["id"], "sequence": f["sequence"], "heading_path": _loads(f["heading_path"], [])} for f in first]
        out.append(d)
    return {"assets": out}


def asset_for_serving(conn, storage, asset_id):
    row = conn.execute("SELECT * FROM asset WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise NotFound("Asset not found")
    if row["availability"] != "available" or not row["relative_path"]:
        raise NotFound("This file was not imported (" + (row["problem"] or row["availability"]) + ")")
    try:
        path = storage.resolve_in_project(row["project_id"], row["relative_path"])
    except ValueError:
        raise NotFound("Asset path is not valid")
    if not os.path.isfile(path):
        raise NotFound("The stored copy of this file is missing from the data folder")
    return {"path": path, "media_type": row["media_type"] or "application/octet-stream", "name": row["original_name"],
            "hash": row["content_hash"], "size": os.path.getsize(path)}


def list_scenarios(conn, doc_id):
    _doc_row(conn, doc_id)
    rows = conn.execute(
        "SELECT s.*, b.sequence AS seq FROM scenario s JOIN source_block b ON b.id = s.source_block_id "
        "WHERE s.document_id = ? ORDER BY s.position", (doc_id,)).fetchall()
    out = []
    for s in rows:
        steps = conn.execute("SELECT number_label, text, raw_text FROM scenario_step WHERE scenario_id = ? ORDER BY sequence", (s["id"],)).fetchall()
        out.append({
            "id": s["id"], "identifier": s["source_identifier"], "identifier_raw": s["identifier_raw"], "title": s["title"],
            "description": s["description"], "preamble": s["preamble"], "segmentation": s["segmentation"],
            "source_block_id": s["source_block_id"], "source_sequence": s["seq"], "row_index": s["row_index"],
            "steps": [{"label": st["number_label"], "text": st["text"], "raw": st["raw_text"]} for st in steps],
        })
    return {"scenarios": out}


def set_section_role(conn, section_id, data):
    row = conn.execute("SELECT * FROM source_section WHERE id = ?", (section_id,)).fetchone()
    if row is None:
        raise NotFound("Section not found")
    role = v.choice(data, "role", ("current", "historical"))
    if role is None:
        raise ValidationError("role must be 'current' or 'historical'", field="role")
    if row["role"] == "metadata":
        raise Conflict("Page properties describe the exported page itself and cannot be reclassified.", code="metadata_section")
    cascade = v.boolean(data, "include_subsections", True)
    ids = [section_id]
    if cascade:
        frontier = [section_id]
        while frontier:
            marks = ",".join("?" * len(frontier))
            kids = [r["id"] for r in conn.execute(f"SELECT id FROM source_section WHERE parent_section_id IN ({marks})", frontier)]
            ids.extend(kids)
            frontier = kids
    reason = f"Marked {role} by you."
    with tx(conn):
        marks = ",".join("?" * len(ids))
        conn.execute(f"UPDATE source_section SET role = ?, role_origin = 'user', role_reason = ? WHERE id IN ({marks}) AND role <> 'metadata'",
                     [role, reason, *ids])
    rows = conn.execute("SELECT id, role, role_origin, role_reason FROM source_section WHERE document_id = ?", (row["document_id"],)).fetchall()
    return {"sections": [dict(r) for r in rows], "changed": len(ids)}


def _table_rows(conn, block_id):
    cells = conn.execute("SELECT row_index, col_index, original_text, row_element_id FROM source_cell WHERE block_id = ? "
                         "ORDER BY row_index, col_index", (block_id,)).fetchall()
    rows = {}
    for c in cells:
        rows.setdefault(c["row_index"], []).append({"text": c["original_text"], "row_element_id": c["row_element_id"]})
    return [rows[k] for k in sorted(rows)]


def reextract_scenarios(conn, doc_id, data):
    """Replace the scenarios of one table, using detected or person-chosen columns."""
    _doc_row(conn, doc_id)
    block_id = v.text(data, "table_block_id", max_len=64, required=True)
    block = conn.execute("SELECT * FROM source_block WHERE id = ? AND document_id = ? AND block_type = 'table'", (block_id, doc_id)).fetchone()
    if block is None:
        raise NotFound("That table is not part of this document")
    rows = _table_rows(conn, block_id)
    header_rows = block["table_header_rows"] or 0
    cols = data.get("columns")
    ncols = block["table_cols"] or 0
    if cols is None:
        if not header_rows:
            raise ValidationError("This table has no header row; choose the columns yourself.", field="columns")
        mapping = detect_columns([c["text"] for c in rows[header_rows - 1]])
        if mapping is None:
            raise ValidationError("The table header was not recognised; choose the columns yourself.", field="columns")
        first_data = header_rows
    else:
        if not isinstance(cols, dict):
            raise ValidationError("columns must be an object", field="columns")
        mapping = {}
        for role in ("identifier", "title", "description"):
            val = cols.get(role)
            if val is None or val == "":
                continue
            if not isinstance(val, int) or isinstance(val, bool) or not 0 <= val < ncols:
                raise ValidationError(f"{role} must be a column number of this table", field="columns")
            mapping[role] = val
        if "identifier" not in mapping or not ({"title", "description"} & set(mapping)):
            raise ValidationError("Choose an identifier column and at least a title or description column", field="columns")
        if len(set(mapping.values())) != len(mapping):
            raise ValidationError("Each role needs its own column", field="columns")
        first_data = data.get("first_data_row", header_rows)
        if not isinstance(first_data, int) or not 0 <= first_data <= len(rows):
            raise ValidationError("first_data_row is outside the table", field="first_data_row")
    drafts, notes = extract_rows(rows, mapping, first_data_row=first_data)
    with tx(conn):
        conn.execute("DELETE FROM scenario WHERE source_block_id = ?", (block_id,))
        conn.execute("DELETE FROM import_diagnostic WHERE source_block_id = ? AND code LIKE 'scenario_%'", (block_id,))
        for d in drafts:
            sid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO scenario(id, project_id, document_id, import_run_id, position, source_identifier, identifier_raw, title, "
                "description, preamble, segmentation, source_block_id, row_index, row_element_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, block["project_id"], doc_id, block["import_run_id"], 0, d["identifier"], d["identifier_raw"], d["title"],
                 d["description"], d["preamble"], d["segmentation"], block_id, d["row_index"], d["row_element_id"]))
            for i, step in enumerate(d["steps"]):
                conn.execute("INSERT INTO scenario_step(id, scenario_id, sequence, number_label, text, raw_text) VALUES (?,?,?,?,?,?)",
                             (uuid.uuid4().hex, sid, i, step["label"], step["text"], step["raw"]))
        for sev, code, msg, row in notes:
            conn.execute(
                "INSERT INTO import_diagnostic(id, project_id, document_id, import_run_id, severity, code, message, source_block_id, position) "
                "VALUES (?,?,?,?,?,?,?,?, 1000)",
                (uuid.uuid4().hex, block["project_id"], doc_id, block["import_run_id"], sev, code, msg, block_id))
        order = conn.execute(
            "SELECT s.id FROM scenario s JOIN source_block b ON b.id = s.source_block_id WHERE s.document_id = ? "
            "ORDER BY b.sequence, s.row_index", (doc_id,)).fetchall()
        for pos, r in enumerate(order):
            conn.execute("UPDATE scenario SET position = ? WHERE id = ?", (pos, r["id"]))
    return list_scenarios(conn, doc_id)


def delete_document(conn, storage, doc_id):
    row = _doc_row(conn, doc_id)
    pid = row["project_id"]
    cited = conn.execute(
        "SELECT COUNT(DISTINCT t.id) FROM task_source ts JOIN source_block b ON b.id = ts.source_block_id JOIN task t ON t.id = ts.task_id WHERE b.document_id = ?",
        (doc_id,)).fetchone()[0]
    if cited:
        raise Conflict(
            f"{cited} task{'s' if cited != 1 else ''} on the board cite{'s' if cited == 1 else ''} this document as their source, so it cannot be removed. "
            "Delete those tasks first if you really want it gone.", code="cited_by_tasks", details={"tasks": cited})
    unapproved = conn.execute("SELECT COUNT(*) FROM candidate WHERE document_id = ? AND state IN ('needs_review')", (doc_id,)).fetchone()[0]
    with tx(conn):
        conn.execute("DELETE FROM source_document WHERE id = ?", (doc_id,))
    moved = 0
    try:
        if storage.to_trash(storage.sources_dir(pid, doc_id), "source"):
            moved += 1
        used = {r["relative_path"] for r in conn.execute("SELECT relative_path FROM asset WHERE project_id = ? AND relative_path IS NOT NULL", (pid,))}
        assets_root = os.path.join(storage.project_dir(pid), "assets")
        if os.path.isdir(assets_root):
            for sub in os.listdir(assets_root):
                if sub.startswith("."):
                    continue
                d = os.path.join(assets_root, sub)
                if not os.path.isdir(d):
                    continue
                for name in os.listdir(d):
                    rel = f"assets/{sub}/{name}"
                    if rel not in used:
                        storage.to_trash(os.path.join(d, name), "orphan-asset")
                        moved += 1
    except OSError:
        pass  # the database change is done; leftover files are harmless
    return {"deleted": doc_id, "files_moved_to_trash": moved, "candidates_discarded": unapproved}
