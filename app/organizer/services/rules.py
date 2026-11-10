"""Editable classification rules: global (shared) and project-specific."""

import hashlib
import uuid

from .. import rules as engine
from ..db import tx, utc_now
from ..errors import Conflict, NotFound, ValidationError
from .projects import get_project_row

COLS = ("id", "project_id", "name", "version", "enabled", "match_scope", "operator", "pattern", "case_sensitive",
        "output_field", "proposed_value", "precedence", "origin", "note")


def _dict(r):
    d = {k: r[k] for k in COLS}
    d["scope"] = "project" if r["project_id"] else "global"
    d["phrases"] = engine.phrases_of(r["pattern"])
    d["enabled"] = bool(r["enabled"])
    d["case_sensitive"] = bool(r["case_sensitive"])
    return d


def effective_rules(conn, project_id):
    """Rules the engine runs: every global rule plus this project's own, as plain dicts."""
    rows = conn.execute("SELECT * FROM rule WHERE project_id IS NULL OR project_id = ? ORDER BY project_id IS NULL, name", (project_id,)).fetchall()
    return [{k: r[k] for k in COLS} for r in rows]


def ruleset_version(conn, project_id):
    parts = [f"{r['id']}:{r['version']}:{r['enabled']}" for r in effective_rules(conn, project_id)]
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()[:12]


def list_rules(conn, project_id):
    get_project_row(conn, project_id)
    rows = conn.execute("SELECT * FROM rule WHERE project_id IS NULL OR project_id = ? ORDER BY project_id IS NULL, output_field, name", (project_id,)).fetchall()
    return {"rules": [_dict(r) for r in rows], "ruleset_version": ruleset_version(conn, project_id),
            "fields": {f: {"label": engine.FIELD_LABELS[f], "values": list(engine.VALUES[f])} for f in engine.FIELDS}}


def _row(conn, rid):
    r = conn.execute("SELECT * FROM rule WHERE id = ?", (rid,)).fetchone()
    if r is None:
        raise NotFound("Rule not found")
    return r


def create_rule(conn, project_id, data):
    get_project_row(conn, project_id)
    clean = engine.validate_rule(data)
    rid, now = uuid.uuid4().hex, utc_now()
    with tx(conn):
        conn.execute(
            "INSERT INTO rule(id, project_id, name, version, enabled, match_scope, operator, pattern, case_sensitive, output_field, proposed_value, precedence, origin, note, created_at, updated_at)"
            " VALUES (?,?,?,1,?,?,?,?,?,?,?,?,'user',?,?,?)",
            (rid, project_id, clean["name"], clean["enabled"], clean["match_scope"], clean["operator"], clean["pattern"], clean["case_sensitive"],
             clean["output_field"], clean["proposed_value"], clean["precedence"], clean["note"], now, now))
    return _dict(_row(conn, rid))


def update_rule(conn, rid, data):
    r = _row(conn, rid)
    clean = engine.validate_rule(data, partial_from={k: r[k] for k in COLS})
    changed = any(clean[k] != r[k] for k in clean)
    now = utc_now()
    with tx(conn):
        if changed:
            conn.execute(
                "UPDATE rule SET name=?, enabled=?, match_scope=?, operator=?, pattern=?, case_sensitive=?, output_field=?, proposed_value=?, precedence=?, note=?,"
                " version = version + 1, updated_at = ? WHERE id = ?",
                (clean["name"], clean["enabled"], clean["match_scope"], clean["operator"], clean["pattern"], clean["case_sensitive"], clean["output_field"],
                 clean["proposed_value"], clean["precedence"], clean["note"], now, rid))
    return _dict(_row(conn, rid))


def delete_rule(conn, rid):
    r = _row(conn, rid)
    if r["origin"] == "builtin":
        raise Conflict("Built-in rules cannot be deleted. Turn the rule off instead.", code="builtin_rule")
    with tx(conn):
        conn.execute("DELETE FROM rule WHERE id = ?", (rid,))
    return {"deleted": rid}


def preview_rule(conn, project_id, doc_id, data, rule_id=None):
    """What a rule (as drafted, saved or not) would do to the candidates of one document. Writes nothing."""
    from . import candidates as cand_svc
    get_project_row(conn, project_id)
    base = {}
    if rule_id:
        r = _row(conn, rule_id)
        base = {k: r[k] for k in COLS}
    clean = engine.validate_rule(data, partial_from=base)
    existing = [r for r in effective_rules(conn, project_id) if r["id"] != rule_id]
    draft = {**clean, "id": "preview", "version": (base.get("version", 0) + 1), "project_id": base.get("project_id", project_id) if rule_id else project_id}
    draft["enabled"] = 1  # a preview always shows what the rule would do if it were on
    rules = existing + [draft]
    doc = conn.execute("SELECT id FROM source_document WHERE id = ? AND project_id = ?", (doc_id, project_id)).fetchone()
    if doc is None:
        raise NotFound("Source document not found in this project")
    rows = conn.execute("SELECT * FROM candidate WHERE document_id = ? AND kind = 'task' AND state = 'needs_review' ORDER BY position", (doc_id,)).fetchall()
    results = []
    for cand in rows:
        found = engine.evaluate(rules, cand_svc._texts_for(conn, cand))
        resolved = engine.resolve(found, cand_svc._current(conn, cand["id"], cand))
        mine = [s for s in resolved if s["rule_ref"] == "preview"]
        for s in mine:
            rivals = sorted({o["rule_name"] for o in resolved if o["field"] == s["field"] and o["rule_ref"] != "preview" and o["state"] in ("suggested", "conflicting")
                             and (o["proposed_value"] != s["proposed_value"] or o["state"] == "conflicting")})
            results.append({"candidate_id": cand["id"], "title": cand["title"], "field": s["field"], "value": s["proposed_value"], "state": s["state"],
                            "matched": s["matched"], "matched_text": s["matched_text"], "match_in": s["match_in"], "conflicts_with": rivals})
    return {"matches": results, "candidates_checked": len(rows), "candidates_matched": len({m["candidate_id"] for m in results}), "rule": {k: clean[k] for k in clean}}
