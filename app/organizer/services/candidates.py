"""Candidates: generate from a source document, classify with rule evidence, review, and approve.

A candidate is a proposal. It becomes a board task (with a ticket number, an approved review state,
and links back to its exact source blocks) only through `approve`, which is one transaction.
"""

import json
import re
import uuid
from collections import defaultdict

from .. import candidates as gen
from .. import priority as prio
from .. import rules as rules_mod
from .. import validation as v
from ..db import tx, utc_now
from ..errors import Conflict, NotFound, ValidationError
from ..meta import CATEGORY_KEYS, IMPACT_KEYS, PRIORITY_KEYS, URGENCY_KEYS
from . import rules as rules_svc
from . import tasks as tasks_svc
from .projects import get_project_row
from .sources import asset_dict

DEFAULTS = {"category": "unassigned", "impact": "unassessed", "urgency": "unspecified"}
KINDS = ("task", "requirement", "exclusion", "note", "asset")
EDITABLE_KIND_CHANGES = {"task": {"note"}, "note": {"task"}, "requirement": {"exclusion"}, "exclusion": {"requirement"}}
LEVEL_RANK = {"epic": 0, "task": 1, "subtask": 2}


def _id():
    return uuid.uuid4().hex


def _loads(text, default):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _cand(conn, cid):
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise NotFound("Candidate not found")
    return row


def _doc(conn, doc_id):
    row = conn.execute("SELECT * FROM source_document WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise NotFound("Source document not found")
    return row


def _editable(row):
    if row["state"] != "needs_review":
        raise Conflict(
            {"approved": "This candidate is already approved; edit the task on the board instead.",
             "dismissed": "This candidate is dismissed. Restore it to edit it.",
             "merged": "This candidate was merged into another one."}[row["state"]], code="candidate_locked")


# ---- generation ---------------------------------------------------------------------

def _load_doc_blocks(conn, doc_id):
    refs = defaultdict(list)
    for r in conn.execute("SELECT id, source_block_id, kind FROM reference WHERE document_id = ? ORDER BY position", (doc_id,)):
        refs[r["source_block_id"]].append({"id": r["id"], "kind": r["kind"]})
    blocks = []
    for b in conn.execute("SELECT * FROM source_block WHERE document_id = ? ORDER BY sequence", (doc_id,)):
        blocks.append({"id": b["id"], "sequence": b["sequence"], "type": b["block_type"], "variant": b["variant"], "depth": b["depth"],
                       "text": b["original_text"], "runs": _loads(b["inline_json"], []), "heading_path": _loads(b["heading_path"], []),
                       "section_id": b["section_id"], "column_group": b["column_group"], "column_index": b["column_index"],
                       "refs": refs.get(b["id"], [])})
    return blocks


def _ident(x):
    x = str(x).strip()
    return str(int(x)) if x.isdigit() else x.casefold()


def default_sections(conn, doc_id):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM source_section WHERE document_id = ? AND role = 'current' ORDER BY position", (doc_id,))]


def generate(conn, doc_id, data=None):
    """Create candidates for the chosen sections (default: current ones). Safe to repeat: existing candidates,
    their edits, approvals and dismissals are left alone; only new groups are added."""
    doc = _doc(conn, doc_id)
    data = data or {}
    pid = doc["project_id"]
    sections = [dict(r) for r in conn.execute("SELECT id, title, role, position, level FROM source_section WHERE document_id = ?", (doc_id,))]
    known = {s["id"] for s in sections}
    chosen = data.get("section_ids")
    if chosen is None:
        chosen = default_sections(conn, doc_id)
    if not isinstance(chosen, list) or not chosen or any(c not in known for c in chosen):
        raise ValidationError("Choose at least one section of this document", field="section_ids")
    chosen_set = set(chosen)
    blocks = _load_doc_blocks(conn, doc_id)
    scen = {_ident(r["source_identifier"]): r["id"] for r in conn.execute("SELECT id, source_identifier FROM scenario WHERE document_id = ?", (doc_id,))}
    drafts = gen.generate(blocks, sections, chosen_set, set(scen))
    now = utc_now()
    run_id = _id()
    ruleset = rules_svc.ruleset_version(conn, pid)
    key_to_id, new_ids = {}, []
    existing = 0
    removed_refs = {(r["candidate_id"], r["reference_id"]) for r in conn.execute(
        "SELECT cr.candidate_id, cr.reference_id FROM candidate_reference cr JOIN candidate c ON c.id = cr.candidate_id WHERE c.document_id = ?", (doc_id,))}
    with tx(conn):
        conn.execute("INSERT INTO generation_run(id, project_id, document_id, generator_version, ruleset_version, created_at) VALUES (?,?,?,?,?,?)",
                     (run_id, pid, doc_id, gen.GENERATOR_VERSION, ruleset, now))
        for sid in chosen:
            conn.execute("INSERT INTO generation_section(run_id, section_id) VALUES (?, ?)", (run_id, sid))
        for d in drafts:
            row = conn.execute("SELECT id FROM candidate WHERE document_id = ? AND identity = ?", (doc_id, d["identity"])).fetchone()
            if row is not None:
                key_to_id[d["key"]] = row["id"]
                existing += 1
                continue
            cid = _id()
            key_to_id[d["key"]] = cid
            new_ids.append((cid, d))
            parent = key_to_id.get(d["parent_key"]) if d["parent_key"] else None
            conn.execute(
                "INSERT INTO candidate(id, project_id, document_id, generation_run_id, identity, kind, level, parent_id, position, title, original_text,"
                " values_json, state, origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'needs_review','generated',?,?)",
                (cid, pid, doc_id, run_id, d["identity"], d["kind"], d["level"], parent, d["position"], d["title"], d["original_text"],
                 json.dumps(d["values"], ensure_ascii=False), now, now))
        for cid, d in new_ids:
            seen = set()
            for pos, (bid, role) in enumerate(d["sources"]):
                if bid in seen:
                    continue
                seen.add(bid)
                conn.execute("INSERT INTO candidate_source(candidate_id, source_block_id, role, position) VALUES (?,?,?,?)", (cid, bid, role, pos))
            for ident, bid in d["scenarios"]:
                conn.execute("INSERT OR IGNORE INTO candidate_scenario(candidate_id, identifier, scenario_id, source_block_id) VALUES (?,?,?,?)",
                             (cid, ident, scen.get(_ident(ident)), bid))
            for pos, (rid, reason) in enumerate(d["refs"]):
                if (cid, rid) in removed_refs:
                    continue
                conn.execute("INSERT OR IGNORE INTO candidate_reference(candidate_id, reference_id, state, reason, position) VALUES (?,?,?,?,?)",
                             (cid, rid, "suggested", reason, pos))
            sp = d.get("suggested_parent_key")
            if sp and key_to_id.get(sp):
                conn.execute("UPDATE candidate SET suggested_parent_id = ?, suggestion_reason = ? WHERE id = ?",
                             (key_to_id[sp], d.get("suggestion_reason", ""), cid))
        conn.execute("UPDATE generation_run SET new_candidates = ?, existing_candidates = ? WHERE id = ?", (len(new_ids), existing, run_id))
        _apply_rules(conn, pid, doc_id, None, now)
    return {"run_id": run_id, "new": len(new_ids), "existing": existing, **list_candidates(conn, doc_id)}


# ---- rules over candidates ----------------------------------------------------------

def _texts_for(conn, cand):
    """What rules may read for one task candidate: title, its own non-exclusion lines, and its heading path.
    Exclusion lines are left out on purpose: a negative instruction must never produce a positive suggestion."""
    label = conn.execute("SELECT source_block_id FROM candidate_source WHERE candidate_id = ? ORDER BY CASE role WHEN 'label' THEN 0 ELSE 1 END, position LIMIT 1",
                         (cand["id"],)).fetchone()
    first_block = label["source_block_id"] if label else None
    lines = []
    for ch in conn.execute("SELECT * FROM candidate WHERE parent_id = ? AND kind = 'requirement' AND state <> 'dismissed' ORDER BY position", (cand["id"],)):
        src = conn.execute("SELECT source_block_id FROM candidate_source WHERE candidate_id = ? AND role = 'evidence' ORDER BY position LIMIT 1", (ch["id"],)).fetchone()
        lines.append((ch["original_text"], src["source_block_id"] if src else first_block))
    path = ""
    if first_block:
        hp = conn.execute("SELECT heading_path FROM source_block WHERE id = ?", (first_block,)).fetchone()
        path = " › ".join(_loads(hp["heading_path"], [])) if hp else ""
    return {"title": (cand["title"], first_block), "text": lines, "heading_path": (path, first_block)}


def _current(conn, cid, row):
    return {"category": row["category"], "impact": row["impact"], "urgency": row["urgency"],
            "disciplines": {r["discipline"] for r in conn.execute("SELECT discipline FROM candidate_discipline WHERE candidate_id = ?", (cid,))}}


def _apply_rules(conn, pid, doc_id, only_ids, now, rules=None):
    """Recompute rule suggestions. Accepted and rejected suggestions are kept as the person left them."""
    rules = rules if rules is not None else rules_svc.effective_rules(conn, pid)
    q = "SELECT * FROM candidate WHERE document_id = ? AND kind = 'task' AND state = 'needs_review'"
    args = [doc_id]
    if only_ids:
        q += f" AND id IN ({','.join('?' * len(only_ids))})"
        args += list(only_ids)
    for cand in conn.execute(q, args).fetchall():
        found = rules_mod.evaluate(rules, _texts_for(conn, cand))
        resolved = rules_mod.resolve(found, _current(conn, cand["id"], cand))
        kept = {(r["rule_ref"], r["field"], r["proposed_value"]): r for r in conn.execute(
            "SELECT * FROM rule_suggestion WHERE candidate_id = ?", (cand["id"],))}
        new_keys = set()
        for s in resolved:
            key = (s["rule_ref"], s["field"], s["proposed_value"])
            new_keys.add(key)
            old = kept.get(key)
            if old is not None and old["state"] in ("accepted", "rejected"):
                continue
            if old is None:
                conn.execute(
                    "INSERT INTO rule_suggestion(id, project_id, candidate_id, rule_ref, rule_name, rule_version, rule_scope, field, proposed_value,"
                    " matched_text, match_in, source_block_id, state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_id(), pid, cand["id"], s["rule_ref"], s["rule_name"], s["rule_version"], s["rule_scope"], s["field"], s["proposed_value"],
                     s["matched_text"], s["match_in"], s["source_block_id"], s["state"], now))
            else:
                conn.execute("UPDATE rule_suggestion SET state = ?, matched_text = ?, match_in = ?, source_block_id = ?, rule_name = ?, rule_version = ? WHERE id = ?",
                             (s["state"], s["matched_text"], s["match_in"], s["source_block_id"], s["rule_name"], s["rule_version"], old["id"]))
        for key, old in kept.items():
            if key not in new_keys and old["state"] not in ("accepted", "rejected"):
                conn.execute("DELETE FROM rule_suggestion WHERE id = ?", (old["id"],))


def apply_rules(conn, doc_id, data=None):
    doc = _doc(conn, doc_id)
    with tx(conn):
        _apply_rules(conn, doc["project_id"], doc_id, None, utc_now())
    return list_candidates(conn, doc_id)


# ---- reading ------------------------------------------------------------------------

def _suggestions(conn, ids):
    out = defaultdict(list)
    if not ids:
        return out
    q = ",".join("?" * len(ids))
    for r in conn.execute(f"SELECT * FROM rule_suggestion WHERE candidate_id IN ({q}) ORDER BY field, proposed_value, rule_name", list(ids)):
        out[r["candidate_id"]].append({
            "id": r["id"], "field": r["field"], "value": r["proposed_value"], "state": r["state"], "rule_id": r["rule_ref"], "rule_name": r["rule_name"],
            "rule_version": r["rule_version"], "rule_scope": r["rule_scope"], "matched_text": r["matched_text"], "match_in": r["match_in"],
            "source_block_id": r["source_block_id"]})
    return out


def _priority(row):
    suggested, reason = prio.suggest(row["impact"], row["urgency"])
    override = row["priority_override"]
    return {"suggested": suggested, "reason": reason, "override": override, "override_reason": row["priority_override_reason"],
            "effective": override or suggested, "source": "overridden" if override else ("suggested" if suggested else "not_assessed")}


def _summary(row, extra):
    sug = [s for s in extra["suggestions"] if s["state"] in ("suggested", "conflicting")]
    return {
        "id": row["id"], "document_id": row["document_id"], "kind": row["kind"], "level": row["level"], "parent_id": row["parent_id"],
        "suggested_parent_id": row["suggested_parent_id"], "suggestion_reason": row["suggestion_reason"], "position": row["position"],
        "title": row["title"], "title_origin": row["title_origin"], "description": row["description"], "state": row["state"],
        "origin": row["origin"], "category": row["category"], "impact": row["impact"], "urgency": row["urgency"],
        "disciplines": sorted(extra["disciplines"]), "original_text": row["original_text"], "values": _loads(row["values_json"], []),
        "task_id": row["task_id"], "ticket_key": extra.get("ticket_key"), "merged_into_id": row["merged_into_id"],
        "first_sequence": extra.get("first_sequence"), "heading_path": extra.get("heading_path", []),
        "scenarios": extra["scenarios"], "pending_suggestions": len(sug), "conflicts": sum(1 for s in sug if s["state"] == "conflicting"),
        "priority": _priority(row), "media": extra["media"], "description_origin": row["description_origin"],
        "reference_decision": row["reference_decision"], "has_primary": extra.get("has_primary", False), "open_questions": extra.get("open_questions", 0),
        "ai_run_id": row["ai_run_id"],
    }


def _bulk_extras(conn, rows):
    ids = [r["id"] for r in rows]
    ex = {r["id"]: {"disciplines": set(), "scenarios": [], "suggestions": [], "media": {"suggested": 0, "attached": 0}, "has_primary": False, "open_questions": 0} for r in rows}
    if not ids:
        return ex
    q = ",".join("?" * len(ids))
    for r in conn.execute(f"SELECT candidate_id, discipline FROM candidate_discipline WHERE candidate_id IN ({q})", ids):
        ex[r["candidate_id"]]["disciplines"].add(r["discipline"])
    for r in conn.execute(f"SELECT candidate_id, identifier, scenario_id FROM candidate_scenario WHERE candidate_id IN ({q}) ORDER BY identifier", ids):
        ex[r["candidate_id"]]["scenarios"].append({"identifier": r["identifier"], "resolved": r["scenario_id"] is not None})
    for cid, lst in _suggestions(conn, ids).items():
        ex[cid]["suggestions"] = lst
    for r in conn.execute(f"SELECT candidate_id, state, COUNT(*) n FROM candidate_reference WHERE candidate_id IN ({q}) AND state <> 'removed' GROUP BY candidate_id, state", ids):
        ex[r["candidate_id"]]["media"][r["state"]] = r["n"]
    for r in conn.execute(f"SELECT candidate_id FROM candidate_reference WHERE candidate_id IN ({q}) AND role = 'primary_demo'", ids):
        ex[r["candidate_id"]]["has_primary"] = True
    for r in conn.execute(f"SELECT candidate_id, COUNT(*) n FROM clarification WHERE candidate_id IN ({q}) AND state = 'open' GROUP BY candidate_id", ids):
        ex[r["candidate_id"]]["open_questions"] = r["n"]
    for r in conn.execute(
            f"SELECT cs.candidate_id, MIN(b.sequence) AS seq FROM candidate_source cs JOIN source_block b ON b.id = cs.source_block_id "
            f"WHERE cs.candidate_id IN ({q}) GROUP BY cs.candidate_id", ids):
        ex[r["candidate_id"]]["first_sequence"] = r["seq"]
    for r in conn.execute(
            f"SELECT cs.candidate_id, b.heading_path FROM candidate_source cs JOIN source_block b ON b.id = cs.source_block_id "
            f"WHERE cs.candidate_id IN ({q}) AND cs.position = (SELECT MIN(position) FROM candidate_source WHERE candidate_id = cs.candidate_id)", ids):
        ex[r["candidate_id"]]["heading_path"] = _loads(r["heading_path"], [])
    tids = [r["task_id"] for r in rows if r["task_id"]]
    if tids:
        tq = ",".join("?" * len(tids))
        keys = {r["id"]: f"{r['ticket_prefix']}-{r['ticket_number']}" for r in conn.execute(
            f"SELECT t.id, t.ticket_number, p.ticket_prefix FROM task t JOIN project p ON p.id = t.project_id WHERE t.id IN ({tq})", tids)}
        for r in rows:
            if r["task_id"]:
                ex[r["id"]]["ticket_key"] = keys.get(r["task_id"])
    return ex


def list_candidates(conn, doc_id):
    doc = _doc(conn, doc_id)
    rows = conn.execute("SELECT * FROM candidate WHERE document_id = ? ORDER BY position, created_at", (doc_id,)).fetchall()
    ex = _bulk_extras(conn, rows)
    counts = defaultdict(int)
    for r in rows:
        counts[r["state"]] += 1
    run = conn.execute("SELECT * FROM generation_run WHERE document_id = ? ORDER BY created_at DESC LIMIT 1", (doc_id,)).fetchone()
    return {
        "document": {"id": doc["id"], "name": doc["original_name"], "title": doc["title"], "version": doc["version"]},
        "candidates": [_summary(r, ex[r["id"]]) for r in rows],
        "counts": dict(counts), "generated": run is not None,
        "last_run": {"id": run["id"], "created_at": run["created_at"], "new": run["new_candidates"], "existing": run["existing_candidates"]} if run else None,
    }


def get_candidate(conn, cid):
    row = _cand(conn, cid)
    ex = _bulk_extras(conn, [row])[cid]
    d = _summary(row, ex)
    srcs = conn.execute(
        "SELECT cs.role, cs.position, b.id, b.sequence, b.block_type, b.original_text, b.heading_path, b.source_element_id FROM candidate_source cs "
        "JOIN source_block b ON b.id = cs.source_block_id WHERE cs.candidate_id = ? ORDER BY b.sequence", (cid,)).fetchall()
    d["sources"] = [{"block_id": s["id"], "sequence": s["sequence"], "type": s["block_type"], "role": s["role"], "text": s["original_text"],
                     "heading_path": _loads(s["heading_path"], [])} for s in srcs]
    d["suggestions"] = ex["suggestions"]
    scen = conn.execute(
        "SELECT cs.identifier, cs.scenario_id, s.title FROM candidate_scenario cs LEFT JOIN scenario s ON s.id = cs.scenario_id WHERE cs.candidate_id = ? ORDER BY cs.identifier", (cid,)).fetchall()
    d["scenario_links"] = [{"identifier": s["identifier"], "resolved": s["scenario_id"] is not None, "title": s["title"] or ""} for s in scen]
    d["references"] = _candidate_refs(conn, cid)
    d["primary"] = next((r for r in d["references"] if r["state"] == "attached" and r["role"] == "primary_demo"), None)
    d["description_versions"] = [{"id": x["id"], "description": x["description"], "origin": x["origin"], "note": x["note"], "created_at": x["created_at"]}
                                 for x in conn.execute("SELECT * FROM candidate_description_version WHERE candidate_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 20", (cid,))]
    d["ai_proposals"] = [{"id": x["id"], "field": x["field"], "value": x["value"], "reason": x["reason"], "state": x["state"], "run_id": x["run_id"]}
                         for x in conn.execute("SELECT * FROM ai_proposal WHERE candidate_id = ? ORDER BY field, value", (cid,))]
    d["ai"] = None
    if row["ai_draft_id"]:
        x = conn.execute("SELECT d.draft_id, d.payload_json, r.id AS run_id, r.provider, r.requested_model, r.reported_model, r.created_at FROM ai_draft d "
                         "JOIN ai_run r ON r.id = d.run_id WHERE d.id = ?", (row["ai_draft_id"],)).fetchone()
        if x:
            pl = _loads(x["payload_json"], {})
            d["ai"] = {"run_id": x["run_id"], "draft_id": x["draft_id"], "provider": x["provider"], "model": x["reported_model"] or x["requested_model"], "created_at": x["created_at"],
                       "basis": pl.get("basis", "source_supported"), "classification_reason": pl.get("classification_reason", "")}
    kids = conn.execute("SELECT * FROM candidate WHERE parent_id = ? ORDER BY position, created_at", (cid,)).fetchall()
    kex = _bulk_extras(conn, kids)
    d["children"] = [_summary(k, kex[k["id"]]) for k in kids]
    if d["kind"] == "task":
        agg = {}
        for k in d["children"]:
            for s in k["scenarios"]:
                agg[s["identifier"]] = s["resolved"]
        for k in kids:
            for gk in conn.execute("SELECT * FROM candidate WHERE parent_id = ?", (k["id"],)):
                for s in conn.execute("SELECT identifier, scenario_id FROM candidate_scenario WHERE candidate_id = ?", (gk["id"],)):
                    agg[s["identifier"]] = s["scenario_id"] is not None
        d["scenarios_all"] = [{"identifier": i, "resolved": r} for i, r in sorted(agg.items())]
    # the whole family's source blocks, so the source pane can show everything under a task, not just its label
    family = _descendants(conn, [cid])
    fq = ",".join("?" * len(family))
    d["family_sources"] = [{"sequence": r["sequence"], "role": r["role"], "candidate_id": r["candidate_id"]} for r in conn.execute(
        f"SELECT b.sequence, cs.role, cs.candidate_id FROM candidate_source cs JOIN source_block b ON b.id = cs.source_block_id WHERE cs.candidate_id IN ({fq}) ORDER BY b.sequence", family)]
    d["clarifications"] = []
    for fid in family:
        for c in _clarifications(conn, fid):
            c["candidate_title"] = conn.execute("SELECT title FROM candidate WHERE id = ?", (fid,)).fetchone()["title"] if fid != cid else d["title"]
            d["clarifications"].append(c)
    d["warnings"] = _warnings(d)
    sp = d["suggested_parent_id"]
    if sp:
        r = conn.execute("SELECT title FROM candidate WHERE id = ?", (sp,)).fetchone()
        d["suggested_parent_title"] = r["title"] if r else ""
    if d["parent_id"]:
        r = conn.execute("SELECT title FROM candidate WHERE id = ?", (d["parent_id"],)).fetchone()
        d["parent_title"] = r["title"] if r else ""
    return d


def _warnings(d):
    w = []
    unresolved = [s["identifier"] for s in d.get("scenario_links", []) if not s["resolved"]]
    if unresolved:
        w.append(f"Scenario {', '.join(unresolved)} named in the source is not defined in the document's scenario table.")
    if d["kind"] == "exclusion":
        w.append("This is a negative instruction. It is kept as an exclusion and never becomes a task.")
    if d["conflicts"]:
        w.append("Rules disagree about a field. Nothing was chosen; pick one.")
    return w


ROLES = ("primary_demo", "supporting_demo", "asset_reference", "source_evidence")
_GENERATED = re.compile(r"[A-Za-z0-9]{20,}")      # an upload id or hash: says nothing to a reader
DIRECT_REASON = "Shown directly under"


def ref_label(link_caption, ref_caption, link_text, name, kind="", media_type="", section=""):
    """A short human caption for a reference. The original filename stays in the details."""
    if link_caption and link_caption.strip():                       # a name a person typed always wins
        return " ".join(link_caption.split())[:120]
    stem = (name or "").rsplit(".", 1)[0]

    def readable(text):
        words = " ".join((text or "").replace("_", " ").replace("-", " ").split())
        if not words or words.casefold() in (stem.casefold(), (name or "").casefold(), " ".join(stem.replace("_", " ").replace("-", " ").split()).casefold()):
            return ""                                              # the importer's caption is just the file name
        return "" if _GENERATED.search(text or "") else words
    for c in (ref_caption, link_text):
        if readable(c):
            return readable(c)[:120]
    words = " ".join(stem.replace("_", " ").replace("-", " ").split())
    if words and not _GENERATED.search(stem):
        return words[:60]
    word = "GIF animation" if media_type == "image/gif" else "Video" if kind == "video" else "Picture"
    return f"{word} from {section.strip().rstrip(':')}"[:80] if section.strip() else word


def _ref_dict(r, tier=None):
    a = asset_dict(r)
    section = (_loads(r["heading_path"], []) or [""])[-1]
    return {"reference_id": r["ref_id"], "role": r["role"], "caption": r["link_caption"],
            "label": ref_label(r["link_caption"], r["ref_caption"], r["link_text"], a["name"], r["kind"], a["media_type"], section),
            "start_ms": r["start_ms"], "end_ms": r["end_ms"], "kind": r["kind"], "sequence": r["sequence"], "asset": a,
            "section": section}


def _candidate_refs(conn, cid):
    out = []
    own = {x["source_block_id"] for x in conn.execute("SELECT source_block_id FROM candidate_source WHERE candidate_id = ? AND role IN ('label', 'evidence', 'scope')", (cid,))}
    q = ("SELECT cr.state, cr.reason, cr.position, cr.role, cr.caption AS link_caption, cr.start_ms, cr.end_ms, r.id AS ref_id, r.kind, r.caption AS ref_caption, r.link_text, "
         "r.source_block_id, b.sequence, b.heading_path, a.* "
         "FROM candidate_reference cr JOIN reference r ON r.id = cr.reference_id JOIN source_block b ON b.id = r.source_block_id "
         "JOIN asset a ON a.id = r.asset_id WHERE cr.candidate_id = ? ORDER BY CASE cr.state WHEN 'attached' THEN 0 WHEN 'suggested' THEN 1 ELSE 2 END, cr.position")
    for r in conn.execute(q, (cid,)):
        d = _ref_dict(r)
        d["state"], d["reason"] = r["state"], r["reason"]
        d["tier"] = 0 if r["source_block_id"] in own else 1 if (r["reason"] or "").startswith(DIRECT_REASON) else 2
        out.append(d)
    seen_labels = {}
    for d in out:   # two different files with the same generated caption are told apart by number
        seen_labels.setdefault(d["label"], []).append(d)
    for label, items in seen_labels.items():
        if len(items) > 1:
            for i, d in enumerate(items, 1):
                d["label"] = f"{label} ({i})"
    # strongest suggestions first: the task's own lines, then media directly under its group, then shared wording
    attached = sorted([d for d in out if d["state"] == "attached"], key=lambda d: d["role"] != "primary_demo")
    suggested = sorted([d for d in out if d["state"] == "suggested"],
                       key=lambda d: (d["tier"], 0 if d["asset"]["kind"] == "video" or d["asset"]["media_type"] == "image/gif" else 1, d["sequence"]))
    seen = set()
    for d in [d for d in suggested if d["tier"] <= 1]:    # the same file used twice is one suggestion
        if d["asset"]["id"] in seen or len(seen) >= 3:
            continue
        seen.add(d["asset"]["id"])
        d["top"] = True
    return attached + suggested + [d for d in out if d["state"] == "removed"]


def _clarifications(conn, cid, with_sources=True):
    rows = conn.execute("SELECT * FROM clarification WHERE candidate_id = ? ORDER BY CASE state WHEN 'open' THEN 0 ELSE 1 END, created_at", (cid,)).fetchall()
    out = []
    for r in rows:
        blocks = [{"block_id": x["id"], "sequence": x["sequence"], "text": x["original_text"]} for x in conn.execute(
            "SELECT b.id, b.sequence, b.original_text FROM clarification_source cs JOIN source_block b ON b.id = cs.source_block_id WHERE cs.clarification_id = ? ORDER BY b.sequence", (r["id"],))] if with_sources else []
        out.append({"id": r["id"], "candidate_id": cid, "text": r["text"], "origin": r["origin"], "state": r["state"], "resolution": r["resolution"], "blocks": blocks})
    return out


# ---- editing ------------------------------------------------------------------------

def _descends(conn, cid, ancestor_id):
    seen, cur = set(), cid
    while cur and cur not in seen:
        if cur == ancestor_id:
            return True
        seen.add(cur)
        r = conn.execute("SELECT parent_id FROM candidate WHERE id = ?", (cur,)).fetchone()
        cur = r["parent_id"] if r else None
    return False


def _check_parent(conn, row, kind, level, parent_id):
    if parent_id is None:
        if kind == "task" and level == "subtask":
            raise ValidationError("A subtask needs a parent task", field="parent_id")
        return
    p = conn.execute("SELECT * FROM candidate WHERE id = ?", (parent_id,)).fetchone()
    if p is None or p["document_id"] != row["document_id"]:
        raise ValidationError("The parent must be a candidate from the same document", field="parent_id")
    if p["kind"] != "task":
        raise ValidationError("Only a task candidate can contain other candidates", field="parent_id")
    if _descends(conn, parent_id, row["id"]):
        raise ValidationError("A candidate cannot be placed inside itself or its own children", field="parent_id")
    if kind == "task":
        if level == "epic":
            raise ValidationError("An epic is always top level", field="parent_id")
        if LEVEL_RANK[p["level"] or "task"] >= LEVEL_RANK[level or "task"]:
            raise ValidationError("A parent must be above its child: epic, then task, then subtask", field="parent_id")


def keep_description_version(conn, cid, text, origin, note, run_id=None):
    """Remember a description before it is replaced. Identical consecutive versions are not stored twice."""
    last = conn.execute("SELECT description FROM candidate_description_version WHERE candidate_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (cid,)).fetchone()
    if last is not None and last["description"] == text:
        return
    conn.execute("INSERT INTO candidate_description_version(id, candidate_id, description, origin, ai_run_id, note, created_at) VALUES (?,?,?,?,?,?,?)",
                 (_id(), cid, text, origin, run_id, note, utc_now()))


def _check_children(conn, row, kind, level):
    """The candidates under this one must still fit once its kind or level changes (M3 REV 003)."""
    kids = conn.execute("SELECT * FROM candidate WHERE parent_id = ? AND state <> 'merged'", (row["id"],)).fetchall()
    if not kids:
        return
    if kind != "task":
        word = {"note": "a note", "asset": "an asset label", "requirement": "a requirement", "exclusion": "an exclusion"}.get(kind, kind)
        raise ValidationError(f"“{row['title']}” has {len(kids)} line{'s' if len(kids) != 1 else ''} or subtask{'s' if len(kids) != 1 else ''} under it, "
                              f"so it cannot become {word}. Move or dismiss them first.", field="kind")
    for k in kids:
        if k["kind"] == "task" and LEVEL_RANK[level or "task"] >= LEVEL_RANK[k["level"] or "task"]:
            raise ValidationError(
                f"“{k['title']}” is a {k['level'] or 'task'} under “{row['title']}”. A {level or 'task'} cannot contain it. "
                "Move that candidate somewhere else first.", field="level")


def update_candidate(conn, cid, data):
    row = _cand(conn, cid)
    _editable(row)
    sets, args = {}, []
    keep_description = None
    kind = row["kind"]
    if "kind" in data:
        nk = v.choice(data, "kind", KINDS)
        if nk != kind:
            if nk not in EDITABLE_KIND_CHANGES.get(kind, set()):
                raise ValidationError(f"A {kind} cannot be changed into a {nk}. Task and note, or requirement and exclusion, can be swapped.", field="kind")
            kind = nk
            sets["kind"] = kind
            if kind == "task":
                sets["level"] = "task"
            elif row["kind"] == "task":
                sets["level"] = None
    if "title" in data:
        t = " ".join(v.text(data, "title", max_len=300, required=True).split())
        if t != row["title"]:
            sets["title"], sets["title_origin"] = t, "user"
    if "description" in data:
        text = v.text(data, "description", max_len=20000, default="", strip=False) or ""
        if text != row["description"]:
            sets["description"], sets["description_origin"] = text, "user"
            keep_description = (row["description"], row["description_origin"], "Replaced by your edit") if row["description"] else None
    level = row["level"] if "level" not in sets else sets["level"]
    if "level" in data and kind == "task":
        level = v.choice(data, "level", ("epic", "task", "subtask"))
        sets["level"] = level
    parent_id = row["parent_id"]
    if "parent_id" in data:
        parent_id = data["parent_id"] or None
        sets["parent_id"] = parent_id
    if "parent_id" in data or "level" in data or "kind" in data:
        if kind != "task" and "level" in data:
            raise ValidationError("Only tasks have a level", field="level")
        if kind in ("task", "requirement", "exclusion"):
            _check_parent(conn, row, kind, level, parent_id)
        elif parent_id is not None:
            raise ValidationError("Notes and asset labels cannot be placed inside a task", field="parent_id")
        if kind != row["kind"] or level != row["level"]:
            _check_children(conn, row, kind, level)
    if "parent_id" in data and parent_id is not None and parent_id == row["suggested_parent_id"]:
        sets["suggested_parent_id"], sets["suggestion_reason"] = None, ""
    if kind == "task" or row["kind"] == "task":
        if "category" in data:
            sets["category"] = v.choice(data, "category", CATEGORY_KEYS)
        if "impact" in data:
            sets["impact"] = v.choice(data, "impact", IMPACT_KEYS)
        if "urgency" in data:
            sets["urgency"] = v.choice(data, "urgency", URGENCY_KEYS)
        if "priority_override" in data:
            ov = v.choice(data, "priority_override", PRIORITY_KEYS, nullable=True)
            sets["priority_override"] = ov
            if ov is None:
                sets["priority_override_reason"] = ""
        if "priority_override_reason" in data:
            reason = v.text(data, "priority_override_reason", max_len=1000, default="")
            if reason and not (sets.get("priority_override") or (row["priority_override"] if "priority_override" not in sets else None)):
                raise ValidationError("An override reason needs an override priority", field="priority_override_reason")
            sets["priority_override_reason"] = reason
    disciplines = None
    if "disciplines" in data:
        raw = data["disciplines"]
        if not isinstance(raw, list) or any(x not in ("art", "dev") for x in raw):
            raise ValidationError("disciplines must be a list of art and/or dev", field="disciplines")
        disciplines = sorted(set(raw))
    now = utc_now()
    with tx(conn):
        if keep_description:
            keep_description_version(conn, cid, *keep_description)
        if sets:
            sets["updated_at"] = now
            conn.execute(f"UPDATE candidate SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", [*sets.values(), cid])
        if disciplines is not None:
            conn.execute("DELETE FROM candidate_discipline WHERE candidate_id = ?", (cid,))
            for d_ in disciplines:
                conn.execute("INSERT INTO candidate_discipline(candidate_id, discipline) VALUES (?, ?)", (cid, d_))
        changed_class = bool({"category", "impact", "urgency"} & set(sets)) or disciplines is not None or "title" in sets
        if changed_class and kind == "task":
            _apply_rules(conn, row["project_id"], row["document_id"], [cid], now)
        if parent_id != row["parent_id"] and row["parent_id"]:
            _apply_rules(conn, row["project_id"], row["document_id"], [row["parent_id"]], now)
        if parent_id and parent_id != row["parent_id"]:
            _apply_rules(conn, row["project_id"], row["document_id"], [parent_id], now)
    return get_candidate(conn, cid)


def suggestion_action(conn, cid, sid, action):
    row = _cand(conn, cid)
    _editable(row)
    s = conn.execute("SELECT * FROM rule_suggestion WHERE id = ? AND candidate_id = ?", (sid, cid)).fetchone()
    if s is None:
        raise NotFound("Suggestion not found")
    now = utc_now()
    with tx(conn):
        if action == "accept":
            _accept(conn, row, s)
        elif action == "reject":
            conn.execute("UPDATE rule_suggestion SET state = 'rejected' WHERE id = ?", (sid,))
            _apply_rules(conn, row["project_id"], row["document_id"], [cid], now)
        elif action == "restore":
            conn.execute("UPDATE rule_suggestion SET state = 'suggested' WHERE id = ? AND state = 'rejected'", (sid,))
            _apply_rules(conn, row["project_id"], row["document_id"], [cid], now)
        else:
            raise ValidationError("action must be accept, reject, or restore", field="action")
    return get_candidate(conn, cid)


def _accept(conn, row, s):
    cid = row["id"]
    if s["field"] == "discipline":
        conn.execute("INSERT OR IGNORE INTO candidate_discipline(candidate_id, discipline) VALUES (?, ?)", (cid, s["proposed_value"]))
    else:
        conn.execute(f"UPDATE candidate SET {s['field']} = ?, updated_at = ? WHERE id = ?", (s["proposed_value"], utc_now(), cid))
        conn.execute("UPDATE rule_suggestion SET state = 'superseded' WHERE candidate_id = ? AND field = ? AND id <> ? AND state IN ('suggested', 'conflicting')",
                     (cid, s["field"], s["id"]))
    conn.execute("UPDATE rule_suggestion SET state = 'accepted' WHERE id = ?", (s["id"],))
    conn.execute("UPDATE rule_suggestion SET state = 'accepted' WHERE candidate_id = ? AND field = ? AND proposed_value = ? AND state IN ('suggested', 'conflicting')",
                 (cid, s["field"], s["proposed_value"]))


def accept_all_suggestions(conn, cid):
    row = _cand(conn, cid)
    _editable(row)
    with tx(conn):
        for s in conn.execute("SELECT * FROM rule_suggestion WHERE candidate_id = ? AND state = 'suggested'", (cid,)).fetchall():
            cur = conn.execute("SELECT state FROM rule_suggestion WHERE id = ?", (s["id"],)).fetchone()
            if cur["state"] == "suggested":
                _accept(conn, _cand(conn, cid), s)
    return get_candidate(conn, cid)


def _ref_row(conn, cid, rid):
    return conn.execute(
        "SELECT cr.*, r.document_id, r.project_id, r.kind AS rkind, r.asset_id, a.media_type FROM candidate_reference cr JOIN reference r ON r.id = cr.reference_id "
        "LEFT JOIN asset a ON a.id = r.asset_id WHERE cr.candidate_id = ? AND cr.reference_id = ?", (cid, rid)).fetchone()


def _same_document_ref(conn, row, rid):
    ref = conn.execute("SELECT r.id, r.document_id, r.project_id, r.kind, a.media_type FROM reference r LEFT JOIN asset a ON a.id = r.asset_id WHERE r.id = ?", (rid,)).fetchone()
    if ref is None or ref["document_id"] != row["document_id"] or ref["project_id"] != row["project_id"]:
        raise ValidationError("That media does not belong to this candidate's document", field="reference_id")
    return ref


def _default_role(ref):
    return "supporting_demo" if ref["kind"] == "video" or ref["media_type"] == "image/gif" else "asset_reference"


def set_reference(conn, cid, data):
    row = _cand(conn, cid)
    _editable(row)
    rid = v.text(data, "reference_id", max_len=64, required=True)
    state = v.choice(data, "state", ("suggested", "attached", "removed"))
    if state is None:
        raise ValidationError("state must be suggested, attached, or removed", field="state")
    ref = _same_document_ref(conn, row, rid)
    with tx(conn):
        cur = conn.execute("SELECT * FROM candidate_reference WHERE candidate_id = ? AND reference_id = ?", (cid, rid)).fetchone()
        if cur is None:
            conn.execute("INSERT INTO candidate_reference(candidate_id, reference_id, state, reason, position, role) VALUES (?,?,?,?,999,?)",
                         (cid, rid, state, "Added by you" if state == "attached" else "", _default_role(ref)))
        else:
            role = cur["role"] if state == "attached" or cur["role"] != "primary_demo" else "supporting_demo"
            conn.execute("UPDATE candidate_reference SET state = ?, role = ? WHERE candidate_id = ? AND reference_id = ?", (state, role, cid, rid))
    return get_candidate(conn, cid)


def bulk_references(conn, cid, data):
    row = _cand(conn, cid)
    _editable(row)
    state = v.choice(data, "state", ("attached", "removed"))
    if state is None:
        raise ValidationError("state must be attached or removed", field="state")
    with tx(conn):  # acts on the still-undecided suggestions only; what a person already attached stays
        conn.execute("UPDATE candidate_reference SET state = ? WHERE candidate_id = ? AND state = 'suggested'", (state, cid))
    return get_candidate(conn, cid)


def _range(data, cur, ref_kind):
    """Validate an optional playback range (milliseconds). Unknown ranges stay empty; nothing is inferred."""
    start, end = cur["start_ms"], cur["end_ms"]
    if "start_ms" in data:
        start = data["start_ms"]
    if "end_ms" in data:
        end = data["end_ms"]
    for name, val in (("start_ms", start), ("end_ms", end)):
        if val is not None and (isinstance(val, bool) or not isinstance(val, int) or val < 0 or val > 24 * 3600 * 1000):
            raise ValidationError("A playback time must be a whole number of milliseconds, zero or more", field=name)
    if end is not None and start is None:
        raise ValidationError("Set a start time before an end time", field="end_ms")
    if start is not None and end is not None and end <= start:
        raise ValidationError("The end of the clip must be after its start", field="end_ms")
    if (start is not None or end is not None) and ref_kind not in ("video", "audio"):
        raise ValidationError("A playback range can only be set on a video", field="start_ms")
    return start, end


def update_reference(conn, cid, rid, data):
    """Change the role, caption or playback range of an attached reference. Choosing a primary demotes the old one."""
    row = _cand(conn, cid)
    _editable(row)
    cur = _ref_row(conn, cid, rid)
    if cur is None or cur["state"] == "removed":
        raise NotFound("That media is not attached to this candidate")
    sets = {}
    if "role" in data:
        role = v.choice(data, "role", ROLES)
        if role is None:
            raise ValidationError("role must be one of " + ", ".join(ROLES), field="role")
        if role == "primary_demo" and cur["state"] != "attached":
            raise ValidationError("Only an attached reference can be the main clip", field="role")
        sets["role"] = role
    if "caption" in data:
        c = data["caption"]
        if c is not None and not isinstance(c, str):
            raise ValidationError("caption must be text", field="caption")
        sets["caption"] = " ".join((c or "").split())[:200]
    if "start_ms" in data or "end_ms" in data:
        s_, e_ = _range(data, cur, cur["rkind"])
        sets["start_ms"], sets["end_ms"] = s_, e_
    if not sets:
        return get_candidate(conn, cid)
    with tx(conn):
        if sets.get("role") == "primary_demo":
            conn.execute("UPDATE candidate_reference SET role = 'supporting_demo' WHERE candidate_id = ? AND role = 'primary_demo' AND reference_id <> ?", (cid, rid))
            conn.execute("UPDATE candidate SET reference_decision = 'undecided', updated_at = ? WHERE id = ?", (utc_now(), cid))
        conn.execute(f"UPDATE candidate_reference SET {', '.join(k + ' = ?' for k in sets)} WHERE candidate_id = ? AND reference_id = ?", [*sets.values(), cid, rid])
    return get_candidate(conn, cid)


def set_primary_reference(conn, cid, data):
    """The one explicit choice of the main demonstration. A suggested reference becomes attached by the same click."""
    row = _cand(conn, cid)
    _editable(row)
    if row["kind"] != "task":
        raise ValidationError("Only a task can have a main clip", field="reference_id")
    rid = v.text(data, "reference_id", max_len=64, required=True)
    ref = _same_document_ref(conn, row, rid)
    with tx(conn):
        cur = conn.execute("SELECT * FROM candidate_reference WHERE candidate_id = ? AND reference_id = ?", (cid, rid)).fetchone()
        conn.execute("UPDATE candidate_reference SET role = 'supporting_demo' WHERE candidate_id = ? AND role = 'primary_demo' AND reference_id <> ?", (cid, rid))
        if cur is None:
            conn.execute("INSERT INTO candidate_reference(candidate_id, reference_id, state, reason, position, role) VALUES (?,?,'attached','Chosen by you',999,'primary_demo')", (cid, rid))
        else:
            conn.execute("UPDATE candidate_reference SET state = 'attached', role = 'primary_demo' WHERE candidate_id = ? AND reference_id = ?", (cid, rid))
        conn.execute("UPDATE candidate SET reference_decision = 'undecided', updated_at = ? WHERE id = ?", (utc_now(), cid))
        conn.execute("UPDATE ai_proposal SET state = 'accepted' WHERE candidate_id = ? AND field = 'primary_reference' AND value = ?", (cid, rid))
    return get_candidate(conn, cid)


def clear_primary_reference(conn, cid):
    """Un-choose the main clip. The file and its attachment stay; only its role changes."""
    row = _cand(conn, cid)
    _editable(row)
    with tx(conn):
        conn.execute("UPDATE candidate_reference SET role = 'supporting_demo' WHERE candidate_id = ? AND role = 'primary_demo'", (cid,))
    return get_candidate(conn, cid)


def set_reference_decision(conn, cid, data):
    row = _cand(conn, cid)
    _editable(row)
    decision = v.choice(data, "decision", ("undecided", "none"))
    if decision is None:
        raise ValidationError("decision must be none or undecided", field="decision")
    if decision == "none" and conn.execute("SELECT 1 FROM candidate_reference WHERE candidate_id = ? AND role = 'primary_demo'", (cid,)).fetchone():
        raise ValidationError("Clear the main clip first, then choose “no reference needed”", field="decision")
    with tx(conn):
        conn.execute("UPDATE candidate SET reference_decision = ?, updated_at = ? WHERE id = ?", (decision, utc_now(), cid))
    return get_candidate(conn, cid)


# ---- clarifications -----------------------------------------------------------------

def add_clarification(conn, cid, data):
    row = _cand(conn, cid)
    if row["state"] in ("merged",):
        raise Conflict("This candidate was merged into another one.", code="candidate_locked")
    text = " ".join(v.text(data, "text", max_len=1000, required=True).split())
    blocks = data.get("source_block_ids") or []
    if not isinstance(blocks, list) or any(not isinstance(b, str) for b in blocks):
        raise ValidationError("source_block_ids must be a list of source block ids", field="source_block_ids")
    if blocks:
        q = ",".join("?" * len(blocks))
        found = {r["id"] for r in conn.execute(f"SELECT id FROM source_block WHERE document_id = ? AND id IN ({q})", [row["document_id"], *blocks])}
        if found != set(blocks):
            raise ValidationError("A linked source block does not belong to this document", field="source_block_ids")
    cl_id = _id()
    ident = "user:" + _id()
    with tx(conn):
        conn.execute("INSERT INTO clarification(id, project_id, candidate_id, text, origin, state, identity, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (cl_id, row["project_id"], cid, text, "user", "open", ident, utc_now()))
        for b in dict.fromkeys(blocks):
            conn.execute("INSERT INTO clarification_source(clarification_id, source_block_id) VALUES (?,?)", (cl_id, b))
    return get_candidate(conn, cid)


def update_clarification(conn, clid, data):
    r = conn.execute("SELECT * FROM clarification WHERE id = ?", (clid,)).fetchone()
    if r is None:
        raise NotFound("Question not found")
    state = v.choice(data, "state", ("open", "resolved"), nullable=True)
    resolution = " ".join(v.text(data, "resolution", max_len=1000, default="").split())
    sets = {}
    if state:
        sets["state"] = state
        sets["resolved_at"] = utc_now() if state == "resolved" else None
        if state == "open":
            sets["resolution"] = ""
    if "resolution" in data and state != "open":
        sets["resolution"] = resolution
    if not sets:
        raise ValidationError("Nothing to change", field="state")
    with tx(conn):
        conn.execute(f"UPDATE clarification SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", [*sets.values(), clid])
    return {"candidate_id": r["candidate_id"], "clarifications": _clarifications(conn, r["candidate_id"])}


def delete_clarification(conn, clid):
    r = conn.execute("SELECT * FROM clarification WHERE id = ?", (clid,)).fetchone()
    if r is None:
        raise NotFound("Question not found")
    with tx(conn):
        conn.execute("DELETE FROM clarification WHERE id = ?", (clid,))
    return {"candidate_id": r["candidate_id"], "clarifications": _clarifications(conn, r["candidate_id"])}


def restore_description(conn, cid, version_id):
    row = _cand(conn, cid)
    _editable(row)
    ver = conn.execute("SELECT * FROM candidate_description_version WHERE id = ? AND candidate_id = ?", (version_id, cid)).fetchone()
    if ver is None:
        raise NotFound("That earlier description no longer exists")
    with tx(conn):
        if row["description"]:
            keep_description_version(conn, cid, row["description"], row["description_origin"], "Replaced when you restored an earlier version")
        conn.execute("UPDATE candidate SET description = ?, description_origin = ?, updated_at = ? WHERE id = ?", (ver["description"], ver["origin"], utc_now(), cid))
    return get_candidate(conn, cid)


def remove_scenario(conn, cid, identifier):
    row = _cand(conn, cid)
    _editable(row)
    with tx(conn):
        conn.execute("DELETE FROM candidate_scenario WHERE candidate_id = ? AND identifier = ?", (cid, identifier))
    return get_candidate(conn, cid)


# ---- dismiss, restore, bulk ---------------------------------------------------------

def _descendants(conn, ids):
    out, frontier = list(ids), list(ids)
    while frontier:
        q = ",".join("?" * len(frontier))
        kids = [r["id"] for r in conn.execute(f"SELECT id FROM candidate WHERE parent_id IN ({q})", frontier)]
        frontier = [k for k in kids if k not in out]
        out.extend(frontier)
    return out


def _rows(conn, ids, project_id=None):
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ValidationError("Choose at least one candidate", field="ids")
    q = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM candidate WHERE id IN ({q})", ids).fetchall()
    if len(rows) != len(set(ids)):
        raise NotFound("One of the candidates no longer exists")
    projects = {r["project_id"] for r in rows}
    if len(projects) != 1 or (project_id and projects != {project_id}):
        raise ValidationError("Candidates must all belong to one project", field="ids")
    return rows


def dismiss(conn, ids, cascade=True):
    rows = _rows(conn, ids)
    bad = [r["title"] for r in rows if r["state"] == "approved"]
    if bad:
        raise Conflict(f"“{bad[0]}” is already approved. Remove or edit the task on the board instead.", code="candidate_locked")
    now = utc_now()
    with tx(conn):
        for r in rows:
            if r["state"] == "needs_review":
                conn.execute("UPDATE candidate SET state = 'dismissed', updated_at = ? WHERE id = ?", (now, r["id"]))
        if cascade:
            for did in _descendants(conn, [r["id"] for r in rows]):
                conn.execute("UPDATE candidate SET state = 'dismissed', updated_at = ? WHERE id = ? AND state = 'needs_review'", (now, did))
    return {"dismissed": len(rows)}


def restore(conn, ids):
    rows = _rows(conn, ids)
    now = utc_now()
    with tx(conn):
        for r in rows:
            if r["state"] == "dismissed":
                conn.execute("UPDATE candidate SET state = 'needs_review', updated_at = ? WHERE id = ?", (now, r["id"]))
                for did in _descendants(conn, [r["id"]]):
                    conn.execute("UPDATE candidate SET state = 'needs_review', updated_at = ? WHERE id = ? AND state = 'dismissed'", (now, did))
    return {"restored": len(rows)}


def bulk_classify(conn, ids, data):
    rows = [r for r in _rows(conn, ids)]
    for r in rows:
        _editable(r)
        if r["kind"] != "task":
            raise ValidationError(f"“{r['title']}” is not a task, so it has nothing to classify", field="ids")
    mode = data.get("disciplines_mode", "add")
    if mode not in ("add", "set"):
        raise ValidationError("disciplines_mode must be add or set", field="disciplines_mode")
    disc = data.get("disciplines")
    if disc is not None and (not isinstance(disc, list) or any(x not in ("art", "dev") for x in disc)):
        raise ValidationError("disciplines must be a list of art and/or dev", field="disciplines")
    cat = v.choice(data, "category", CATEGORY_KEYS)
    imp = v.choice(data, "impact", IMPACT_KEYS)
    urg = v.choice(data, "urgency", URGENCY_KEYS)
    if all(x is None for x in (disc, cat, imp, urg)):
        raise ValidationError("Choose at least one value to apply", field="category")
    now = utc_now()
    with tx(conn):
        for r in rows:
            sets = {k: val for k, val in (("category", cat), ("impact", imp), ("urgency", urg)) if val is not None}
            if sets:
                sets["updated_at"] = now
                conn.execute(f"UPDATE candidate SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", [*sets.values(), r["id"]])
            if disc is not None:
                if mode == "set":
                    conn.execute("DELETE FROM candidate_discipline WHERE candidate_id = ?", (r["id"],))
                for d_ in disc:
                    conn.execute("INSERT OR IGNORE INTO candidate_discipline(candidate_id, discipline) VALUES (?, ?)", (r["id"], d_))
            _apply_rules(conn, r["project_id"], r["document_id"], [r["id"]], now)
    return {"classified": len(rows)}


def bulk_accept(conn, ids):
    rows = _rows(conn, ids)
    n = 0
    with tx(conn):
        for r in rows:
            if r["state"] != "needs_review" or r["kind"] != "task":
                continue
            for s in conn.execute("SELECT * FROM rule_suggestion WHERE candidate_id = ? AND state = 'suggested'", (r["id"],)).fetchall():
                cur = conn.execute("SELECT state FROM rule_suggestion WHERE id = ?", (s["id"],)).fetchone()
                if cur["state"] == "suggested":
                    _accept(conn, _cand(conn, r["id"]), s)
                    n += 1
    return {"accepted": n}


# ---- split and merge ----------------------------------------------------------------

def split(conn, cid, data):
    row = _cand(conn, cid)
    _editable(row)
    if row["kind"] != "task":
        raise ValidationError("Only a task candidate can be split", field="kind")
    title = " ".join(v.text(data, "title", max_len=300, required=True).split())
    child_ids = data.get("child_ids")
    if not isinstance(child_ids, list) or not child_ids:
        raise ValidationError("Choose the lines to move into the new candidate", field="child_ids")
    mode = data.get("mode", "sibling")
    if mode not in ("sibling", "subtask"):
        raise ValidationError("mode must be sibling or subtask", field="mode")
    kids = {r["id"]: r for r in conn.execute("SELECT * FROM candidate WHERE parent_id = ? AND state = 'needs_review'", (cid,))}
    if any(c not in kids for c in child_ids):
        raise ValidationError("Only lines directly under this candidate can be moved", field="child_ids")
    if mode == "subtask" and row["level"] != "task":
        raise ValidationError("Only a task can hold subtasks", field="mode")
    level = "subtask" if mode == "subtask" else row["level"]
    parent = cid if mode == "subtask" else row["parent_id"]
    if level == "subtask" and parent is None:
        raise ValidationError("A subtask needs a parent task", field="mode")
    for c in child_ids:  # a subtask can only move into something that can still contain it
        k = kids[c]
        if k["kind"] == "task" and LEVEL_RANK[level or "task"] >= LEVEL_RANK[k["level"] or "task"]:
            raise ValidationError(f"“{k['title']}” is a {k['level'] or 'task'} and cannot be moved under a new {level or 'task'}. Move only requirement lines.", field="child_ids")
    now = utc_now()
    new_id = _id()
    with tx(conn):
        conn.execute(
            "INSERT INTO candidate(id, project_id, document_id, identity, kind, level, parent_id, position, title, title_origin, state, origin, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'user','needs_review','user',?,?)",
            (new_id, row["project_id"], row["document_id"], f"user:{new_id}", "task", level, parent, row["position"], title, now, now))
        for kid in child_ids:
            conn.execute("UPDATE candidate SET parent_id = ?, updated_at = ? WHERE id = ?", (new_id, now, kid))
            for s in conn.execute("SELECT * FROM candidate_source WHERE candidate_id = ? AND role = 'evidence'", (kid,)).fetchall():
                conn.execute("INSERT OR IGNORE INTO candidate_source(candidate_id, source_block_id, role, position) VALUES (?,?,?,?)",
                             (new_id, s["source_block_id"], "evidence", s["position"]))
        for s in conn.execute("SELECT * FROM candidate_source WHERE candidate_id = ? AND role = 'label'", (cid,)):
            conn.execute("INSERT OR IGNORE INTO candidate_source(candidate_id, source_block_id, role, position) VALUES (?,?,?,?)",
                         (new_id, s["source_block_id"], "context", s["position"]))
        _apply_rules(conn, row["project_id"], row["document_id"], [cid, new_id], now)
    return {"new_id": new_id, **list_candidates(conn, row["document_id"])}


def merge(conn, data):
    ids = data.get("ids")
    rows = _rows(conn, ids)
    if len(rows) < 2:
        raise ValidationError("Choose at least two task candidates to merge", field="ids")
    if len({r["document_id"] for r in rows}) != 1:
        raise ValidationError("Candidates from different documents cannot be merged", field="ids")
    for r in rows:
        _editable(r)
        if r["kind"] != "task":
            raise ValidationError(f"“{r['title']}” is not a task candidate", field="ids")
    into = data.get("into_id") or sorted(rows, key=lambda r: (r["position"], r["created_at"]))[0]["id"]
    target = next((r for r in rows if r["id"] == into), None)
    if target is None:
        raise ValidationError("into_id must be one of the chosen candidates", field="into_id")
    others = [r for r in rows if r["id"] != into]
    for o in others:
        if _descends(conn, into, o["id"]) or _descends(conn, o["id"], into):
            raise ValidationError("A candidate cannot be merged with its own parent or child; reparent it first", field="ids")
    if len({r["level"] or "task" for r in rows}) > 1:
        raise ValidationError("Only candidates at the same level can be merged (two tasks, or two subtasks). Change one candidate's level first.", field="ids")
    for o in others:  # everything that moves under the kept candidate must still fit below its level
        for k in conn.execute("SELECT * FROM candidate WHERE parent_id = ? AND kind = 'task' AND state <> 'merged'", (o["id"],)):
            if LEVEL_RANK[target["level"] or "task"] >= LEVEL_RANK[k["level"] or "task"]:
                raise ValidationError(f"“{k['title']}” could not sit under “{target['title']}” after the merge. Move it first.", field="ids")
    title = " ".join(v.text(data, "title", max_len=300, default=target["title"]).split()) or target["title"]
    # preview: exactly what would be kept
    kids = []
    for r in rows:
        kids += [c for c in conn.execute("SELECT * FROM candidate WHERE parent_id = ?", (r["id"],)) if c["id"] not in {x["id"] for x in rows}]
    refs = {}
    for r in rows:
        for cr in conn.execute("SELECT * FROM candidate_reference WHERE candidate_id = ? AND state <> 'removed'", (r["id"],)):
            prev = refs.get(cr["reference_id"])
            if prev is None or cr["state"] == "attached":
                refs[cr["reference_id"]] = cr["state"]
    scen = {s["identifier"] for r in rows for s in conn.execute("SELECT identifier FROM candidate_scenario WHERE candidate_id = ?", (r["id"],))}
    sources = {s["source_block_id"] for r in rows for s in conn.execute("SELECT source_block_id FROM candidate_source WHERE candidate_id = ?", (r["id"],))}
    notes = []
    for field, default in DEFAULTS.items():
        vals = {r[field] for r in rows if r[field] != default}
        if len(vals) > 1:
            notes.append(f"{field.capitalize()} differs ({', '.join(sorted(vals))}); the kept candidate's value is used.")
    preview = {"into_id": into, "title": title, "kept_title_from": target["title"], "merged": [o["title"] for o in others],
               "lines": len(kids), "references": len(refs), "scenarios": sorted(scen), "sources": len(sources), "notes": notes}
    if not data.get("confirm"):
        return {"preview": preview}
    now = utc_now()
    with tx(conn):
        conn.execute("UPDATE candidate SET title = ?, title_origin = CASE WHEN title <> ? THEN 'user' ELSE title_origin END, updated_at = ? WHERE id = ?",
                     (title, title, now, into))
        for o in others:
            for c in conn.execute("SELECT id FROM candidate WHERE parent_id = ?", (o["id"],)).fetchall():
                conn.execute("UPDATE candidate SET parent_id = ?, updated_at = ? WHERE id = ?", (into, now, c["id"]))
            for s in conn.execute("SELECT * FROM candidate_source WHERE candidate_id = ?", (o["id"],)).fetchall():
                role = "context" if s["role"] == "label" else s["role"]
                conn.execute("INSERT OR IGNORE INTO candidate_source(candidate_id, source_block_id, role, position) VALUES (?,?,?,?)",
                             (into, s["source_block_id"], role, s["position"]))
            has_primary = conn.execute("SELECT 1 FROM candidate_reference WHERE candidate_id = ? AND role = 'primary_demo'", (into,)).fetchone() is not None
            for cr in conn.execute("SELECT * FROM candidate_reference WHERE candidate_id = ? AND state <> 'removed'", (o["id"],)).fetchall():
                role = cr["role"] if not (cr["role"] == "primary_demo" and has_primary) else "supporting_demo"
                if role == "primary_demo":
                    has_primary = True
                conn.execute("INSERT INTO candidate_reference(candidate_id, reference_id, state, reason, position, role, caption, start_ms, end_ms) VALUES (?,?,?,?,?,?,?,?,?) "
                             "ON CONFLICT(candidate_id, reference_id) DO UPDATE SET state = CASE WHEN excluded.state = 'attached' THEN 'attached' ELSE state END",
                             (into, cr["reference_id"], cr["state"], cr["reason"], cr["position"], role, cr["caption"], cr["start_ms"], cr["end_ms"]))
            conn.execute("UPDATE OR IGNORE clarification SET candidate_id = ? WHERE candidate_id = ?", (into, o["id"]))
            conn.execute("UPDATE OR IGNORE ai_proposal SET candidate_id = ? WHERE candidate_id = ?", (into, o["id"]))
            for cs in conn.execute("SELECT * FROM candidate_scenario WHERE candidate_id = ?", (o["id"],)).fetchall():
                conn.execute("INSERT OR IGNORE INTO candidate_scenario(candidate_id, identifier, scenario_id, source_block_id) VALUES (?,?,?,?)",
                             (into, cs["identifier"], cs["scenario_id"], cs["source_block_id"]))
            for d_ in conn.execute("SELECT discipline FROM candidate_discipline WHERE candidate_id = ?", (o["id"],)).fetchall():
                conn.execute("INSERT OR IGNORE INTO candidate_discipline(candidate_id, discipline) VALUES (?, ?)", (into, d_["discipline"]))
            for field, default in DEFAULTS.items():
                if _cand(conn, into)[field] == default and o[field] != default:
                    conn.execute(f"UPDATE candidate SET {field} = ? WHERE id = ?", (o[field], into))
            if o["description"]:
                conn.execute("UPDATE candidate SET description = CASE WHEN description = '' THEN ? ELSE description || char(10) || ? END WHERE id = ?",
                             (o["description"], o["description"], into))
            conn.execute("UPDATE candidate SET state = 'merged', merged_into_id = ?, parent_id = NULL, updated_at = ? WHERE id = ?", (into, now, o["id"]))
            conn.execute("UPDATE candidate SET suggested_parent_id = ? WHERE suggested_parent_id = ?", (into, o["id"]))
        _apply_rules(conn, target["project_id"], target["document_id"], [into], now)
    return {"merged_into": into, "preview": preview, **list_candidates(conn, target["document_id"])}


# ---- approval -----------------------------------------------------------------------

def approve(conn, project_id, ids, with_children=True):
    """Approve candidates onto the board in one transaction. Selected tasks bring their requirement lines and
    (optionally) their subtask candidates; nothing is created if any part is refused."""
    get_project_row(conn, project_id)
    rows = _rows(conn, ids, project_id)
    selected = {r["id"]: r for r in rows}
    todo = dict(selected)
    for r in rows:
        if r["kind"] != "task":
            continue
        frontier = [r["id"]]
        while frontier:
            q = ",".join("?" * len(frontier))
            kids = conn.execute(f"SELECT * FROM candidate WHERE parent_id IN ({q}) AND state = 'needs_review'", frontier).fetchall()
            frontier = []
            for k in kids:
                if k["kind"] in ("requirement", "exclusion") or (k["kind"] == "task" and with_children):
                    if k["id"] not in todo:
                        todo[k["id"]] = k
                        if k["kind"] == "task":
                            frontier.append(k["id"])
    for r in todo.values():
        if r["state"] != "needs_review":
            raise Conflict(f"“{r['title']}” is {r['state'].replace('_', ' ')} and cannot be approved.", code="not_reviewable")
        if r["kind"] in ("note", "asset"):
            raise Conflict(f"“{r['title']}” is a {r['kind']}, not a task. Change its type to Task to approve it, or dismiss it.", code="not_a_task")
    tasks = [r for r in todo.values() if r["kind"] == "task"]
    for t in tasks:
        if t["parent_id"] and t["parent_id"] not in todo:
            p = conn.execute("SELECT * FROM candidate WHERE id = ?", (t["parent_id"],)).fetchone()
            if p is not None and p["state"] != "approved":
                raise Conflict(f"“{t['title']}” is a subtask of “{p['title']}”. Approve the parent together with it.", code="parent_not_approved")
    order, placed = [], set()
    while len(order) < len(tasks):
        progressed = False
        for t in sorted(tasks, key=lambda r: (r["position"], r["created_at"])):
            if t["id"] in placed:
                continue
            if t["parent_id"] in todo and todo[t["parent_id"]]["kind"] == "task" and t["parent_id"] not in placed:
                continue
            order.append(t)
            placed.add(t["id"])
            progressed = True
        if not progressed:
            raise Conflict("The selection contains a cycle of parents", code="cycle")
    now = utc_now()
    created, task_of = [], {}
    waiting, attached_later = [], 0
    with tx(conn):
        for t in order:
            parent_task = None
            if t["parent_id"]:
                parent_task = task_of.get(t["parent_id"]) or conn.execute("SELECT task_id FROM candidate WHERE id = ?", (t["parent_id"],)).fetchone()["task_id"]
            level = t["level"] or "task"
            if parent_task is None and level == "subtask":
                raise Conflict(f"“{t['title']}” is a subtask without an approved parent task.", code="parent_not_approved")
            discs = [d["discipline"] for d in conn.execute("SELECT discipline FROM candidate_discipline WHERE candidate_id = ?", (t["id"],))]
            payload = {"title": t["title"], "description": t["description"], "level": level, "parent_id": parent_task, "disciplines": discs,
                       "category": t["category"], "impact": t["impact"], "urgency": t["urgency"], "status": "backlog",
                       "priority_override": t["priority_override"], "priority_override_reason": t["priority_override_reason"]}
            tid = tasks_svc.create_task_in_tx(conn, project_id, payload, origin="source_extraction", provenance="source_extraction")
            task_of[t["id"]] = tid
            accepted = {r["field"] for r in conn.execute("SELECT field FROM rule_suggestion WHERE candidate_id = ? AND state = 'accepted'", (t["id"],))}
            # A person reviewed every value: it is "manual" unless it came from a rule suggestion they accepted.
            for field in ("category", "impact", "urgency", "disciplines", "priority_override"):
                tasks_svc.set_field_origin(conn, tid, field, "rule" if (field in accepted or (field == "disciplines" and "discipline" in accepted)) else "manual")
            for s in conn.execute("SELECT * FROM candidate_source WHERE candidate_id = ? ORDER BY position", (t["id"],)):
                conn.execute("INSERT OR IGNORE INTO task_source(task_id, source_block_id, role, position) VALUES (?,?,?,?)",
                             (tid, s["source_block_id"], s["role"], s["position"]))
            for i, r in enumerate(conn.execute("SELECT * FROM candidate_reference WHERE candidate_id = ? AND state = 'attached' ORDER BY position", (t["id"],))):
                conn.execute("INSERT OR IGNORE INTO task_reference(project_id, task_id, reference_id, position, role, caption, start_ms, end_ms) VALUES (?,?,?,?,?,?,?,?)",
                             (project_id, tid, r["reference_id"], i, r["role"], r["caption"], r["start_ms"], r["end_ms"]))
            conn.execute("UPDATE task SET reference_decision = ? WHERE id = ?", (t["reference_decision"], tid))
            conn.execute("UPDATE candidate SET state = 'approved', task_id = ?, updated_at = ? WHERE id = ?", (tid, now, t["id"]))
            created.append(tid)
        for r in sorted((x for x in todo.values() if x["kind"] in ("requirement", "exclusion")), key=lambda x: (x["position"], x["created_at"])):
            req_id = _id()
            conn.execute("INSERT INTO requirement(id, project_id, kind, original_text, values_json, provenance, candidate_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (req_id, project_id, r["kind"], r["original_text"] or r["title"], r["values_json"], "source_extraction", r["id"], now))
            for s in conn.execute("SELECT * FROM candidate_source WHERE candidate_id = ? AND role IN ('evidence','scope','context') ORDER BY position", (r["id"],)):
                conn.execute("INSERT OR IGNORE INTO requirement_source(requirement_id, source_block_id, role) VALUES (?,?,?)", (req_id, s["source_block_id"], s["role"]))
            owner = task_of.get(r["parent_id"]) or (conn.execute("SELECT task_id FROM candidate WHERE id = ?", (r["parent_id"],)).fetchone()["task_id"] if r["parent_id"] else None)
            if owner:
                conn.execute("INSERT OR IGNORE INTO task_requirement(project_id, task_id, requirement_id, position) VALUES (?,?,?,?)", (project_id, owner, req_id, r["position"]))
            for cs in conn.execute("SELECT * FROM candidate_scenario WHERE candidate_id = ? AND scenario_id IS NOT NULL", (r["id"],)):
                conn.execute("INSERT OR IGNORE INTO scenario_requirement(project_id, scenario_id, requirement_id, review_state) VALUES (?,?,?, 'confirmed')",
                             (project_id, cs["scenario_id"], req_id))
            conn.execute("UPDATE candidate SET state = 'approved', requirement_id = ?, updated_at = ? WHERE id = ?", (req_id, now, r["id"]))
            if r["parent_id"] and not owner:
                waiting.append({"title": r["title"], "parent_id": r["parent_id"]})
        # A requirement approved earlier keeps its place in the candidate tree: once its parent becomes a ticket it is
        # attached to that ticket. The requirement is never created a second time.
        for pcid, ptid in task_of.items():
            for ch in conn.execute("SELECT id, requirement_id, position FROM candidate WHERE parent_id = ? AND kind IN ('requirement', 'exclusion') "
                                   "AND state = 'approved' AND requirement_id IS NOT NULL", (pcid,)).fetchall():
                if conn.execute("SELECT 1 FROM task_requirement WHERE requirement_id = ?", (ch["requirement_id"],)).fetchone() is None:
                    conn.execute("INSERT OR IGNORE INTO task_requirement(project_id, task_id, requirement_id, position) VALUES (?,?,?,?)",
                                 (project_id, ptid, ch["requirement_id"], ch["position"]))
                    attached_later += 1
    unattached = conn.execute(
        f"SELECT COUNT(*) FROM candidate_reference WHERE state = 'suggested' AND candidate_id IN ({','.join('?' * len(todo))})", list(todo)).fetchone()[0]
    keys = []
    for tid in created:
        t = conn.execute("SELECT t.ticket_number, t.title, p.ticket_prefix FROM task t JOIN project p ON p.id = t.project_id WHERE t.id = ?", (tid,)).fetchone()
        keys.append({"id": tid, "key": f"{t['ticket_prefix']}-{t['ticket_number']}", "title": t["title"]})
    for w in waiting:
        p = conn.execute("SELECT title FROM candidate WHERE id = ?", (w["parent_id"],)).fetchone()
        w["parent_title"] = p["title"] if p else ""
    return {"tasks": keys, "requirements": sum(1 for x in todo.values() if x["kind"] in ("requirement", "exclusion")), "unattached_suggested_media": unattached,
            "awaiting_parent": waiting, "attached_to_parent": attached_later}


# ---- evidence on the board side -----------------------------------------------------

def task_evidence(conn, task_id):
    t = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if t is None:
        raise NotFound("Task not found")
    sources = conn.execute(
        "SELECT ts.role, b.id, b.sequence, b.block_type, b.original_text, b.heading_path, b.document_id, d.original_name, d.title AS doc_title FROM task_source ts "
        "JOIN source_block b ON b.id = ts.source_block_id JOIN source_document d ON d.id = b.document_id WHERE ts.task_id = ? ORDER BY b.sequence", (task_id,)).fetchall()
    reqs = conn.execute(
        "SELECT r.* FROM requirement r JOIN task_requirement tr ON tr.requirement_id = r.id WHERE tr.task_id = ? ORDER BY tr.position", (task_id,)).fetchall()
    out_reqs, scen_seen = [], {}
    for r in reqs:
        scs = conn.execute(
            "SELECT s.id, s.source_identifier, s.title, sr.review_state FROM scenario_requirement sr JOIN scenario s ON s.id = sr.scenario_id WHERE sr.requirement_id = ? ORDER BY s.position",
            (r["id"],)).fetchall()
        for s in scs:
            scen_seen[s["id"]] = {"id": s["id"], "identifier": s["source_identifier"], "title": s["title"], "state": s["review_state"]}
        blocks = conn.execute(
            "SELECT b.id, b.sequence FROM requirement_source rs JOIN source_block b ON b.id = rs.source_block_id WHERE rs.requirement_id = ? AND rs.role = 'evidence' ORDER BY b.sequence", (r["id"],)).fetchall()
        out_reqs.append({"id": r["id"], "kind": r["kind"], "text": r["original_text"], "values": _loads(r["values_json"], []),
                         "scenarios": [s["source_identifier"] for s in scs], "blocks": [{"block_id": b["id"], "sequence": b["sequence"]} for b in blocks]})
    refs = _task_refs(conn, task_id)
    cand = conn.execute("SELECT id, document_id FROM candidate WHERE task_id = ?", (task_id,)).fetchone()
    clar = []
    if cand:
        for fid in _descendants(conn, [cand["id"]]):
            title = conn.execute("SELECT title FROM candidate WHERE id = ?", (fid,)).fetchone()["title"]
            for c in _clarifications(conn, fid):
                c["candidate_title"] = title
                clar.append(c)
    return {
        "task_id": task_id, "candidate_id": cand["id"] if cand else None, "document_id": cand["document_id"] if cand else None,
        "reference_decision": t["reference_decision"], "primary": next((r for r in refs if r["role"] == "primary_demo"), None), "clarifications": clar,
        "sources": [{"block_id": s["id"], "sequence": s["sequence"], "role": s["role"], "type": s["block_type"], "text": s["original_text"],
                     "heading_path": _loads(s["heading_path"], []), "document_id": s["document_id"], "document": s["doc_title"] or s["original_name"]} for s in sources],
        "requirements": out_reqs, "scenarios": sorted(scen_seen.values(), key=lambda s: s["identifier"]), "references": refs,
    }


def _task_refs(conn, task_id):
    out = []
    q = ("SELECT tr.role, tr.caption AS link_caption, tr.start_ms, tr.end_ms, tr.position, r.id AS ref_id, r.kind, r.caption AS ref_caption, r.link_text, b.sequence, b.heading_path, a.* "
         "FROM task_reference tr JOIN reference r ON r.id = tr.reference_id JOIN source_block b ON b.id = r.source_block_id "
         "JOIN asset a ON a.id = r.asset_id WHERE tr.task_id = ? ORDER BY CASE tr.role WHEN 'primary_demo' THEN 0 ELSE 1 END, tr.position")
    for r in conn.execute(q, (task_id,)):
        out.append(_ref_dict(r))
    return out


def _task_ref_row(conn, task_id, rid):
    row = conn.execute("SELECT tr.*, r.kind AS rkind FROM task_reference tr JOIN reference r ON r.id = tr.reference_id WHERE tr.task_id = ? AND tr.reference_id = ?", (task_id, rid)).fetchone()
    if row is None:
        raise NotFound("That media is not attached to this task")
    return row


def update_task_reference(conn, task_id, rid, data):
    """Board side of update_reference: the role, caption and playback range of one reference on a task."""
    t = conn.execute("SELECT id FROM task WHERE id = ?", (task_id,)).fetchone()
    if t is None:
        raise NotFound("Task not found")
    cur = _task_ref_row(conn, task_id, rid)
    sets = {}
    if "role" in data:
        role = v.choice(data, "role", ROLES)
        if role is None:
            raise ValidationError("role must be one of " + ", ".join(ROLES), field="role")
        sets["role"] = role
    if "caption" in data:
        c = data["caption"]
        if c is not None and not isinstance(c, str):
            raise ValidationError("caption must be text", field="caption")
        sets["caption"] = " ".join((c or "").split())[:200]
    if "start_ms" in data or "end_ms" in data:
        s_, e_ = _range(data, cur, cur["rkind"])
        sets["start_ms"], sets["end_ms"] = s_, e_
    if sets:
        with tx(conn):
            if sets.get("role") == "primary_demo":
                conn.execute("UPDATE task_reference SET role = 'supporting_demo' WHERE task_id = ? AND role = 'primary_demo' AND reference_id <> ?", (task_id, rid))
                conn.execute("UPDATE task SET reference_decision = 'undecided' WHERE id = ?", (task_id,))
            conn.execute(f"UPDATE task_reference SET {', '.join(k + ' = ?' for k in sets)} WHERE task_id = ? AND reference_id = ?", [*sets.values(), task_id, rid])
    return task_evidence(conn, task_id)


def set_task_primary(conn, task_id, rid):
    _task_ref_row(conn, task_id, rid)
    return update_task_reference(conn, task_id, rid, {"role": "primary_demo"})


def clear_task_primary(conn, task_id):
    if conn.execute("SELECT id FROM task WHERE id = ?", (task_id,)).fetchone() is None:
        raise NotFound("Task not found")
    with tx(conn):
        conn.execute("UPDATE task_reference SET role = 'supporting_demo' WHERE task_id = ? AND role = 'primary_demo'", (task_id,))
    return task_evidence(conn, task_id)


def set_task_reference_decision(conn, task_id, data):
    if conn.execute("SELECT id FROM task WHERE id = ?", (task_id,)).fetchone() is None:
        raise NotFound("Task not found")
    decision = v.choice(data, "decision", ("undecided", "none"))
    if decision is None:
        raise ValidationError("decision must be none or undecided", field="decision")
    if decision == "none" and conn.execute("SELECT 1 FROM task_reference WHERE task_id = ? AND role = 'primary_demo'", (task_id,)).fetchone():
        raise ValidationError("Clear the main clip first, then choose “no reference needed”", field="decision")
    with tx(conn):
        conn.execute("UPDATE task SET reference_decision = ? WHERE id = ?", (decision, task_id))
    return task_evidence(conn, task_id)


def reference_options(conn, cid, q=""):
    """Every picture and video in the candidate's document, so a main clip can be chosen even when nothing was suggested."""
    row = _cand(conn, cid)
    linked = {r["reference_id"]: r["state"] for r in conn.execute("SELECT reference_id, state FROM candidate_reference WHERE candidate_id = ?", (cid,))}
    like = f"%{(q or '').strip().casefold()}%"
    out = []
    for r in conn.execute(
            "SELECT r.id AS ref_id, r.kind, r.caption AS ref_caption, r.link_text, '' AS link_caption, NULL AS start_ms, NULL AS end_ms, 'supporting_demo' AS role, b.sequence, b.heading_path, a.* "
            "FROM reference r JOIN source_block b ON b.id = r.source_block_id JOIN asset a ON a.id = r.asset_id "
            "WHERE r.document_id = ? AND r.kind IN ('image', 'video') AND a.availability = 'available' ORDER BY b.sequence LIMIT 400", (row["document_id"],)):
        d = _ref_dict(r)
        hay = f"{d['label']} {d['asset']['name']} {d['section']}".casefold()
        if q and like.strip("%") not in hay:
            continue
        d["link_state"] = linked.get(r["ref_id"])
        out.append(d)
    return {"references": out[:200], "total": len(out)}
