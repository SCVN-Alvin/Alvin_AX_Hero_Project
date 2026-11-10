"""AI drafting: settings, connection check, payload preview, runs, and the import of selected drafts into candidates.

Importing is the only step that touches candidates. It is one transaction, never creates or approves a board task, never
overwrites a description a person wrote unless they choose to, and never confirms a classification or a primary clip:
those arrive as proposals that stay proposed until reviewed.
"""

import hashlib
import json
import re
import uuid

from ..ai import payload, settings as ai_settings, validate
from ..ai.adapters import MESSAGES
from ..ai.manager import versions
from ..db import tx, utc_now
from ..errors import Conflict, NotFound, ValidationError
from . import candidates as cand_svc


def _loads(text, default):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ---- settings and connection ------------------------------------------------------

def get_settings(conn, manager):
    s = ai_settings.get(conn)
    s["scripted_provider_available"] = manager.fake is not None
    s["active_runs"] = manager.busy()
    return s


def update_settings(conn, manager, data):
    if data.get("provider") == "fake" and manager.fake is None:
        raise ValidationError("The scripted test provider is not available in this run of the service", field="provider")
    ai_settings.update(conn, data)
    return get_settings(conn, manager)


def check_connection(conn, manager):
    s = ai_settings.get(conn)
    info = manager.adapter_for(s).check()
    info["enabled"] = s["enabled"]
    return info


# ---- previews and runs -------------------------------------------------------------

def preview(conn, doc_id, selection):
    s = ai_settings.get(conn)
    built = payload.build(conn, doc_id, selection or {}, s)
    return {"document_id": doc_id, "project_id": built["project_id"], "enabled": s["enabled"], "provider": s["provider"], "model": s["model"],
            "systems": [{"candidate_id": x["candidate_id"], "title": x["title"], "blocks": len(x["block_ids"]), "references": len(x["reference_ids"])} for x in built["systems"]],
            "blocks": built["display"]["blocks"], "references": built["display"]["references"], "scenarios": built["display"]["scenarios"],
            "known_questions": built["display"]["known_questions"], "problems": built["problems"], "notes": built["notes"], "limits": built["limits"],
            "request": dict(built["request"], request_id="(assigned when it is sent)"), "request_hash": built["request_hash"],
            "what_is_not_sent": "File paths, unrelated sections, historical sections you left out, and the pictures and videos themselves. Only captions are sent."}


def start_run(conn, manager, doc_id, selection, parent_run_id=None):
    run_id = manager.start(conn, doc_id, selection or {}, parent_run_id)
    return run_dict(conn, run_id)


_STATE_TEXT = {"queued": "Waiting to start", "running": "Waiting for the AI", "succeeded": "Drafts ready", "failed": "Did not finish", "cancelled": "Cancelled"}


def run_dict(conn, run_id, detail=False):
    r = conn.execute("SELECT * FROM ai_run WHERE id = ?", (run_id,)).fetchone()
    if r is None:
        raise NotFound("AI run not found")
    sel = _loads(r["selection_json"], {})
    titles = [x["title"] for x in (conn.execute("SELECT title FROM candidate WHERE id = ?", (c,)).fetchone() for c in sel.get("candidate_ids", [])) if x]
    drafts = conn.execute("SELECT state, parent_draft_id FROM ai_draft WHERE run_id = ?", (run_id,)).fetchall()
    out = {
        "id": r["id"], "project_id": r["project_id"], "document_id": r["document_id"], "state": r["state"], "state_text": _STATE_TEXT[r["state"]],
        "error_code": r["error_code"], "error_message": r["error_message"], "provider": r["provider"], "requested_model": r["requested_model"],
        "reported_model": r["reported_model"], "adapter_version": r["adapter_version"], "request_format": r["request_format"],
        "prompt_version": r["prompt_version"], "schema_version": r["schema_version"], "input_hash": r["input_hash"], "parent_run_id": r["parent_run_id"],
        "created_at": r["created_at"], "started_at": r["started_at"], "finished_at": r["finished_at"], "purpose": sel.get("purpose", "drafts"),
        "candidate_ids": sel.get("candidate_ids", []), "titles": titles, "task_drafts": sum(1 for d in drafts if d["parent_draft_id"] is None),
        "subtask_drafts": sum(1 for d in drafts if d["parent_draft_id"] is not None), "imported": sum(1 for d in drafts if d["state"] == "imported"),
        "issues": [i for i in _loads(r["issues_json"], []) if i.get("level") != "info" or detail],
    }
    if detail:
        out["request"] = _loads(r["input_json"], {})
        out["questions"] = _loads(r["questions_json"], [])
        out["output_text"] = r["output_text"] if r["state"] in ("failed",) or detail else ""
        rows = conn.execute("SELECT * FROM ai_draft WHERE run_id = ? ORDER BY position", (run_id,)).fetchall()
        blocks = {}
        for d in rows:
            p = _loads(d["payload_json"], {})
            for bid in p.get("source_block_ids", []):
                blocks.setdefault(bid, None)
        if blocks:
            q = ",".join("?" * len(blocks))
            for b in conn.execute(f"SELECT id, sequence, original_text FROM source_block WHERE id IN ({q})", list(blocks)):
                blocks[b["id"]] = {"block_id": b["id"], "sequence": b["sequence"], "text": b["original_text"]}
        out["drafts"] = [{"draft_id": d["draft_id"], "parent_draft_id": d["parent_draft_id"], "title": d["title"], "description": d["description"], "state": d["state"],
                          "imported_candidate_id": d["imported_candidate_id"], "issues": _loads(d["issues_json"], []), "payload": _loads(d["payload_json"], {}),
                          "evidence": [blocks[b] for b in _loads(d["payload_json"], {}).get("source_block_ids", []) if blocks.get(b)]} for d in rows]
        out["references"] = _run_references(conn, r)
    return out


def _run_references(conn, r):
    alias = _loads(r["alias_json"], {}).get("references", {})
    req = _loads(r["input_json"], {})
    out = {}
    for m in req.get("reference_manifest", []):
        real = alias.get(m["id"])
        if real:
            out[real] = m["caption"]
    return out


def list_runs(conn, doc_id, limit=15):
    rows = conn.execute("SELECT id FROM ai_run WHERE document_id = ? ORDER BY created_at DESC LIMIT ?", (doc_id, limit)).fetchall()
    return {"runs": [run_dict(conn, r["id"]) for r in rows]}


def cancel_run(conn, manager, run_id):
    manager.cancel(conn, run_id)
    return run_dict(conn, run_id)


# ---- import ------------------------------------------------------------------------

def _norm(text):
    return re.sub(r"[^\w]+", " ", (text or "").casefold()).strip()


def _identity(key, title, blocks):
    return "ai:" + hashlib.sha1(f"{key}|{_norm(title)}|{','.join(sorted(blocks))}".encode()).hexdigest()[:28]


def _family_blocks(conn, cid):
    fam = cand_svc._descendants(conn, [cid])
    q = ",".join("?" * len(fam))
    return {r["source_block_id"] for r in conn.execute(f"SELECT source_block_id FROM candidate_source WHERE candidate_id IN ({q}) AND role <> 'media'", fam)}


def _best_candidate(conn, blocks, selected, taken=()):
    best, score = None, 0
    for cid in selected:
        if cid in taken:
            continue
        n = len(set(blocks) & _family_blocks(conn, cid))
        if n > score:
            best, score = cid, n
    return best


def _proposals_for(conn, target_row, draft):
    out = []
    reason = draft.get("classification_reason") or ""
    have_disc = {r["discipline"] for r in conn.execute("SELECT discipline FROM candidate_discipline WHERE candidate_id = ?", (target_row["id"],))} if target_row else set()
    for d in draft.get("disciplines", []):
        if d not in have_disc:
            out.append({"field": "discipline", "value": d, "reason": reason})
    if draft.get("category") not in (None, "unassigned") and (not target_row or target_row["category"] != draft["category"]):
        out.append({"field": "category", "value": draft["category"], "reason": reason})
    if draft.get("impact") not in (None, "unassessed") and (not target_row or target_row["impact"] != draft["impact"]):
        out.append({"field": "impact", "value": draft["impact"], "reason": reason})
    if draft.get("suggested_primary_reference_id"):
        out.append({"field": "primary_reference", "value": draft["suggested_primary_reference_id"],
                    "reason": "Proposed from the clip's caption only. The AI has not seen it; you choose the main clip."})
    return out


def _plan(conn, run, body):
    """Work out, without writing anything, what importing the chosen drafts would do."""
    if run["state"] != "succeeded":
        raise Conflict("Only a finished run with drafts can be imported", code="run_not_ready")
    sel = _loads(run["selection_json"], {})
    selected = [c for c in sel.get("candidate_ids", []) if conn.execute("SELECT 1 FROM candidate WHERE id = ? AND state = 'needs_review'", (c,)).fetchone()]
    rows = conn.execute("SELECT * FROM ai_draft WHERE run_id = ? ORDER BY position", (run["id"],)).fetchall()
    by_id = {d["draft_id"]: d for d in rows}
    tops = [d for d in rows if d["parent_draft_id"] is None]
    choices = {c["draft_id"]: c for c in (body.get("drafts") or []) if isinstance(c, dict) and c.get("draft_id")}
    if body.get("drafts") is not None:
        unknown = [k for k in choices if k not in by_id]
        if unknown:
            raise ValidationError(f"Unknown draft {unknown[0]!r}", field="drafts")
    chosen = tops if body.get("drafts") is None else [d for d in tops if d["draft_id"] in choices]
    purpose = sel.get("purpose", "drafts")
    plan, taken = [], set()
    for d in chosen:
        p = _loads(d["payload_json"], {})
        ch = choices.get(d["draft_id"], {})
        issues = _loads(d["issues_json"], [])
        target = ch.get("target", "auto")
        if target == "auto":
            target = _best_candidate(conn, p.get("source_block_ids", []), selected, taken) or "new"
        if target != "new":
            row = conn.execute("SELECT * FROM candidate WHERE id = ?", (target,)).fetchone()
            if row is None or row["project_id"] != run["project_id"] or row["document_id"] != run["document_id"]:
                raise ValidationError("That candidate does not belong to this run's project and document", field="drafts")
            if row["kind"] != "task" or row["state"] != "needs_review":
                raise ValidationError(f"“{row['title']}” is not a task waiting for review", field="drafts")
            taken.add(target)
        else:
            row = None
        flagged = bool(p.get("unsupported_values"))
        ident = _identity("root", d["title"], p.get("source_block_ids", []))
        twin = conn.execute("SELECT * FROM candidate WHERE document_id = ? AND identity = ?", (run["document_id"], ident)).fetchone() if row is None else None
        judged = row or twin          # the candidate whose description would be replaced
        current = judged["description"] if judged else ""
        default_apply = bool(p.get("description")) and not flagged and (not current or current == p["description"] or (judged is not None and judged["description_origin"] == "ai"))
        apply_desc = ch.get("description", default_apply) if p.get("description") else False
        subs = []
        for s in [x for x in rows if x["parent_draft_id"] == d["draft_id"]]:
            sp = _loads(s["payload_json"], {})
            want = (s["draft_id"] in ch["subtasks"]) if isinstance(ch.get("subtasks"), list) else (purpose != "description")
            action, cand_id = "create", None
            if row is not None and row["level"] == "subtask":
                action = "skipped_nested"
            else:
                for k in conn.execute("SELECT id, title FROM candidate WHERE parent_id = ? AND kind = 'task' AND state <> 'merged'", (row["id"],)).fetchall() if row else []:
                    if _norm(k["title"]) == _norm(s["title"]):
                        action, cand_id = "update_existing", k["id"]
                ident = _identity(row["id"] if row else "root:" + d["draft_id"], s["title"], sp.get("source_block_ids", []))
                if action == "create" and conn.execute("SELECT id FROM candidate WHERE document_id = ? AND identity = ?", (run["document_id"], ident)).fetchone():
                    action = "exists"
            subs.append({"draft_id": s["draft_id"], "title": s["title"], "basis": sp.get("basis"), "description": s["description"], "action": action, "candidate_id": cand_id,
                         "selected": bool(want) and action != "skipped_nested", "already_imported": s["state"] == "imported", "issues": _loads(s["issues_json"], [])})
        exists = twin
        plan.append({"draft_id": d["draft_id"], "title": d["title"], "target": {"mode": "existing" if row else "new", "candidate_id": row["id"] if row else None,
                                                                                   "title": row["title"] if row else d["title"], "already_exists": bool(exists)},
                     "description": {"proposed": p.get("description", ""), "current": current, "apply": bool(apply_desc), "default_apply": default_apply,
                                     "keeps_previous": bool(current and apply_desc and current != p.get("description")), "flagged": flagged,
                                     "unsupported_values": p.get("unsupported_values", [])},
                     "proposals": _proposals_for(conn, row, p) if ch.get("proposals", True) and purpose != "description" else [],
                     "subtasks": subs, "already_imported": d["state"] == "imported", "issues": issues, "source_block_ids": p.get("source_block_ids", []),
                     "scenario_ids": p.get("scenario_ids", []), "_row": row, "_payload": p, "_ident": ident, "_draft_row": d})
    questions = _loads(run["questions_json"], [])
    want_q = body.get("questions")
    qplan = [{"index": i, "text": q["text"], "source_block_ids": q["source_block_ids"], "selected": (i in want_q) if isinstance(want_q, list) else True} for i, q in enumerate(questions)]
    return plan, qplan


def _public(plan):
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in plan]


def import_drafts(conn, run_id, body):
    run = conn.execute("SELECT * FROM ai_run WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise NotFound("AI run not found")
    if body.get("project_id") and body["project_id"] != run["project_id"]:
        raise Conflict("This AI run belongs to a different project. Nothing was imported.", code="wrong_project")
    plan, qplan = _plan(conn, run, body)
    summary = {"tasks": sum(1 for p in plan if not p["already_imported"]), "subtasks": sum(1 for p in plan for s in p["subtasks"] if s["selected"] and not s["already_imported"]),
               "descriptions": sum(1 for p in plan if p["description"]["apply"] and not p["already_imported"]), "proposals": sum(len(p["proposals"]) for p in plan if not p["already_imported"]),
               "questions": sum(1 for q in qplan if q["selected"])}
    if not body.get("confirm"):
        return {"run_id": run_id, "preview": True, "plan": _public(plan), "questions": qplan, "summary": summary,
                "note": "Importing adds needs-review candidates. Nothing becomes a board ticket, and classifications and the main clip stay proposals until you review them."}
    now = utc_now()
    result = {"created": [], "updated": [], "skipped": [], "proposals": 0, "questions": 0}
    touched = []
    with tx(conn):
        for p in plan:
            d, draft, row = p["_draft_row"], p["_payload"], p["_row"]
            if p["already_imported"]:
                result["skipped"].append({"title": p["title"], "reason": "Already imported from this run."})
                if d["imported_candidate_id"]:
                    touched.append(d["imported_candidate_id"])
                continue
            if row is None:
                cid = _create_candidate(conn, run, None, p["title"], draft["description"] if p["description"]["apply"] else "", draft["source_block_ids"], draft["scenario_ids"],
                                        level="task", ident=p["_ident"], draft_row=d, existing=p["target"]["already_exists"])
                if cid is None:
                    result["skipped"].append({"title": p["title"], "reason": "A candidate for this already exists."})
                    existing = conn.execute("SELECT id FROM candidate WHERE document_id = ? AND identity = ?", (run["document_id"], p["_ident"])).fetchone()
                    cid = existing["id"]
                    _apply_description(conn, cid, draft["description"], p["description"]["apply"], run, now)
                else:
                    result["created"].append({"id": cid, "title": p["title"], "kind": "task"})
            else:
                cid = row["id"]
                if p["description"]["apply"] and draft["description"]:
                    _apply_description(conn, cid, draft["description"], True, run, now)
                    result["updated"].append({"id": cid, "title": row["title"], "what": "description"})
            for pr in p["proposals"]:
                cur = conn.execute("INSERT OR IGNORE INTO ai_proposal(id, candidate_id, run_id, field, value, reason, state, created_at) VALUES (?,?,?,?,?,?,'suggested',?)",
                                   (uuid.uuid4().hex, cid, run["id"], pr["field"], pr["value"], pr["reason"], now))
                result["proposals"] += cur.rowcount
            for s in p["subtasks"]:
                if not s["selected"] or s["already_imported"]:
                    continue
                srow = conn.execute("SELECT * FROM ai_draft WHERE run_id = ? AND draft_id = ?", (run["id"], s["draft_id"])).fetchone()
                sdraft = _loads(srow["payload_json"], {})
                if s["action"] == "update_existing":
                    _apply_description(conn, s["candidate_id"], sdraft["description"], bool(sdraft["description"]) and not sdraft.get("unsupported_values")
                                       and not conn.execute("SELECT description FROM candidate WHERE id = ?", (s["candidate_id"],)).fetchone()["description"], run, now)
                    conn.execute("UPDATE ai_draft SET state = 'imported', imported_candidate_id = ?, imported_at = ? WHERE id = ?", (s["candidate_id"], now, srow["id"]))
                    result["updated"].append({"id": s["candidate_id"], "title": s["title"], "what": "description"})
                elif s["action"] == "create":
                    ident = _identity(cid, s["title"], sdraft.get("source_block_ids", []))
                    scid = _create_candidate(conn, run, cid, s["title"], "" if sdraft.get("unsupported_values") else sdraft["description"], sdraft.get("source_block_ids", []),
                                             sdraft.get("scenario_ids", []), level="subtask", ident=ident, draft_row=srow)
                    if scid:
                        result["created"].append({"id": scid, "title": s["title"], "kind": "subtask", "basis": sdraft.get("basis")})
                        conn.execute("UPDATE ai_draft SET state = 'imported', imported_candidate_id = ?, imported_at = ? WHERE id = ?", (scid, now, srow["id"]))
                        touched.append(scid)
                else:
                    result["skipped"].append({"title": s["title"], "reason": "A matching candidate already exists." if s["action"] == "exists" else "A subtask cannot contain subtasks."})
            conn.execute("UPDATE ai_draft SET state = 'imported', imported_candidate_id = ?, imported_at = ? WHERE id = ?", (cid, now, d["id"]))
            touched.append(cid)
        # questions stay attached to the draft whose evidence they cite
        targets = [t for t in dict.fromkeys(touched) if t]
        for q in qplan:
            if not q["selected"] or not targets:
                continue
            cid = _best_candidate(conn, q["source_block_ids"], targets) or targets[0]
            ident = "ai:" + hashlib.sha1((q["text"] + "|" + ",".join(sorted(q["source_block_ids"]))).encode()).hexdigest()[:28]
            cur = conn.execute("INSERT OR IGNORE INTO clarification(id, project_id, candidate_id, text, origin, state, identity, ai_run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                               (uuid.uuid4().hex, run["project_id"], cid, q["text"], "ai", "open", ident, run["id"], now))
            if cur.rowcount:
                cl = conn.execute("SELECT id FROM clarification WHERE candidate_id = ? AND identity = ?", (cid, ident)).fetchone()
                for b in q["source_block_ids"]:
                    conn.execute("INSERT OR IGNORE INTO clarification_source(clarification_id, source_block_id) VALUES (?,?)", (cl["id"], b))
                result["questions"] += 1
    return {"run_id": run_id, "preview": False, "summary": summary, **result, "candidate_ids": [t for t in dict.fromkeys(touched) if t]}


def _apply_description(conn, cid, text, apply, run, now):
    if not apply or not text:
        return
    cur = conn.execute("SELECT description, description_origin FROM candidate WHERE id = ?", (cid,)).fetchone()
    if cur["description"] == text:
        return
    if cur["description"]:
        cand_svc.keep_description_version(conn, cid, cur["description"], cur["description_origin"], "Replaced by an AI draft", run["id"])
    conn.execute("UPDATE candidate SET description = ?, description_origin = 'ai', updated_at = ? WHERE id = ?", (text, now, cid))


def _create_candidate(conn, run, parent_id, title, description, block_ids, scenario_ids, *, level, ident, draft_row, existing=False):
    if conn.execute("SELECT 1 FROM candidate WHERE document_id = ? AND identity = ?", (run["document_id"], ident)).fetchone():
        return None
    now = utc_now()
    pos = (conn.execute("SELECT COALESCE(MAX(position), 0) FROM candidate WHERE document_id = ?", (run["document_id"],)).fetchone()[0] or 0) + 1
    cid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO candidate(id, project_id, document_id, identity, kind, level, parent_id, position, title, title_origin, description, description_origin, state, origin, "
        "ai_run_id, ai_draft_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, run["project_id"], run["document_id"], ident, "task", level, parent_id, pos, title, "user", description, "ai" if description else "user", "needs_review", "ai",
         run["id"], draft_row["id"], now, now))
    for i, b in enumerate(dict.fromkeys(block_ids)):
        conn.execute("INSERT OR IGNORE INTO candidate_source(candidate_id, source_block_id, role, position) VALUES (?,?,?,?)", (cid, b, "evidence" if parent_id else "label" if i == 0 else "evidence", i))
    for sid in dict.fromkeys(scenario_ids):
        sc = conn.execute("SELECT id FROM scenario WHERE document_id = ? AND source_identifier = ?", (run["document_id"], sid)).fetchone()
        conn.execute("INSERT OR IGNORE INTO candidate_scenario(candidate_id, identifier, scenario_id, source_block_id) VALUES (?,?,?,NULL)", (cid, sid, sc["id"] if sc else None))
    return cid


# ---- proposals the AI made for fields a person decides ---------------------------------

def proposal_action(conn, cid, pid, action):
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise NotFound("Candidate not found")
    cand_svc._editable(row)
    p = conn.execute("SELECT * FROM ai_proposal WHERE id = ? AND candidate_id = ?", (pid, cid)).fetchone()
    if p is None:
        raise NotFound("Proposal not found")
    if action == "accept":
        if p["field"] == "discipline":
            with tx(conn):
                conn.execute("INSERT OR IGNORE INTO candidate_discipline(candidate_id, discipline) VALUES (?, ?)", (cid, p["value"]))
                conn.execute("UPDATE ai_proposal SET state = 'accepted' WHERE id = ?", (pid,))
        elif p["field"] in ("category", "impact"):
            with tx(conn):
                conn.execute(f"UPDATE candidate SET {p['field']} = ?, updated_at = ? WHERE id = ?", (p["value"], utc_now(), cid))
                conn.execute("UPDATE ai_proposal SET state = 'accepted' WHERE candidate_id = ? AND field = ? AND value = ?", (cid, p["field"], p["value"]))
                conn.execute("UPDATE ai_proposal SET state = 'rejected' WHERE candidate_id = ? AND field = ? AND value <> ? AND state = 'suggested'", (cid, p["field"], p["value"]))
        else:
            return cand_svc.set_primary_reference(conn, cid, {"reference_id": p["value"]})
    elif action == "reject":
        with tx(conn):
            conn.execute("UPDATE ai_proposal SET state = 'rejected' WHERE id = ?", (pid,))
    elif action == "restore":
        with tx(conn):
            conn.execute("UPDATE ai_proposal SET state = 'suggested' WHERE id = ? AND state = 'rejected'", (pid,))
    else:
        raise ValidationError("action must be accept, reject, or restore", field="action")
    return cand_svc.get_candidate(conn, cid)


__all__ = ["MESSAGES", "validate", "versions"]
