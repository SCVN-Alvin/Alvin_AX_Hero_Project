"""Runs AI drafting jobs in the background, one at a time, and records every run.

A run is created before anything is sent and belongs to one project and document for its whole life. Whatever the
interface is showing when the answer arrives, the answer is stored with its own run; it never overwrites another run's
drafts and it never touches candidates or tasks. Only an explicit import (services.ai) does that.
"""

import hashlib
import json
import os
import threading
import uuid

from . import REQUEST_FORMAT, payload, settings as ai_settings, validate
from .adapters import AdapterError, CodexCliAdapter, MESSAGES
from ..db import connect, tx, utc_now
from ..errors import Conflict, NotFound, ValidationError

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def read_asset(name):
    with open(os.path.join(ASSETS, name), encoding="utf-8") as fh:
        return fh.read()


def versions():
    ins, sch = read_asset("instructions.txt"), read_asset("task-draft.schema.json")
    return {"prompt": hashlib.sha256(ins.encode()).hexdigest()[:12], "schema": hashlib.sha256(sch.encode()).hexdigest()[:12]}


class AiManager:
    def __init__(self, storage, fake_adapter=None, work_root=None):
        self.storage = storage
        self.fake = fake_adapter
        self.work_root = work_root
        self._lock = threading.Lock()
        self._jobs = {}

    # -- adapters
    def adapter_for(self, settings):
        if settings["provider"] == "fake":
            if self.fake is None:
                raise ValidationError("The scripted test provider is not available in this run of the service", field="provider")
            return self.fake
        return CodexCliAdapter(settings["binary"], settings["model"], self.work_root)

    def recover(self):
        """At start-up: a run left queued or running by a stopped service can never finish."""
        conn = connect(self.storage.db_path)
        try:
            with tx(conn):
                conn.execute("UPDATE ai_run SET state = 'failed', error_code = 'interrupted', error_message = ?, finished_at = ? WHERE state IN ('queued', 'running')",
                             (MESSAGES["interrupted"], utc_now()))
        finally:
            conn.close()

    def busy(self):
        return [rid for rid, j in self._jobs.items() if j["thread"].is_alive()]

    # -- start / cancel
    def start(self, conn, doc_id, selection, parent_run_id=None):
        settings = ai_settings.get(conn)
        if not settings["enabled"]:
            raise Conflict(MESSAGES["disabled"], code="ai_disabled")
        built = payload.build(conn, doc_id, selection, settings)
        if built["problems"]:
            raise ValidationError(built["problems"][0], details={"problems": built["problems"]})
        adapter = self.adapter_for(settings)
        with self._lock:
            if self.busy():
                raise Conflict("Another AI draft is still running. Wait for it to finish or cancel it.", code="ai_busy")
            if parent_run_id and conn.execute("SELECT 1 FROM ai_run WHERE id = ?", (parent_run_id,)).fetchone() is None:
                raise NotFound("The run to retry no longer exists")
            run_id, request_id = uuid.uuid4().hex, uuid.uuid4().hex
            request = dict(built["request"], request_id=request_id)
            v = versions()
            ctx_scen = set(request["scenario_ids"])
            with tx(conn):
                conn.execute(
                    "INSERT INTO ai_run(id, project_id, document_id, provider, requested_model, adapter_version, request_format, prompt_version, schema_version, request_id,"
                    " parent_run_id, state, selection_json, input_hash, input_json, alias_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, built["project_id"], doc_id, settings["provider"], settings["model"], "", REQUEST_FORMAT, v["prompt"], v["schema"], request_id, parent_run_id,
                     "queued", json.dumps({"candidate_ids": [s["candidate_id"] for s in built["systems"]], "purpose": (selection or {}).get("purpose", "drafts"),
                                           "exclude_block_ids": (selection or {}).get("exclude_block_ids") or [], "exclude_reference_ids": (selection or {}).get("exclude_reference_ids") or []}),
                     built["request_hash"], json.dumps(request, ensure_ascii=False), json.dumps(built["aliases"]), utc_now()))
            cancel = threading.Event()
            ctx = validate.Context(request_id, built["aliases"]["blocks"], built["aliases"]["references"], ctx_scen, request["instructions"]["task_count_limit"])
            ctx.block_text = {b["id"]: b["text"] for b in request["source_blocks"]}
            t = threading.Thread(target=self._work, args=(run_id, adapter, request, ctx, settings, cancel), daemon=True)
            self._jobs[run_id] = {"thread": t, "cancel": cancel}
            t.start()
        return run_id

    def cancel(self, conn, run_id):
        job = self._jobs.get(run_id)
        row = conn.execute("SELECT state FROM ai_run WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound("AI run not found")
        if row["state"] not in ("queued", "running"):
            return {"cancelled": False, "state": row["state"]}
        if job is not None and job["thread"].is_alive():
            job["cancel"].set()
            return {"cancelled": True, "state": "cancelling"}
        with tx(conn):  # nothing is working on it any more
            conn.execute("UPDATE ai_run SET state = 'cancelled', error_code = 'cancelled', error_message = ?, finished_at = ? WHERE id = ? AND state IN ('queued', 'running')",
                         (MESSAGES["cancelled"], utc_now(), run_id))
        return {"cancelled": True, "state": "cancelled"}

    def wait(self, run_id, timeout=30):
        job = self._jobs.get(run_id)
        if job:
            job["thread"].join(timeout)

    # -- the job
    def _work(self, run_id, adapter, request, ctx, settings, cancel):
        conn = connect(self.storage.db_path)
        try:
            with tx(conn):
                conn.execute("UPDATE ai_run SET state = 'running', started_at = ? WHERE id = ? AND state = 'queued'", (utc_now(), run_id))
            instructions = read_asset("instructions.txt")
            schema_text = read_asset("task-draft.schema.json")
            try:
                result = adapter.run(request, instructions, schema_text, cancel, settings["timeout_seconds"])
            except AdapterError as exc:
                self._finish(conn, run_id, "cancelled" if exc.code == "cancelled" else "failed", code=exc.code, message=exc.message)
                return
            if cancel.is_set():
                self._finish(conn, run_id, "cancelled", code="cancelled", message=MESSAGES["cancelled"], output=result.text, model=result.model, version=result.version)
                return
            try:
                obj = validate.parse_json(result.text)
                clean, issues = validate.validate(obj, ctx)
            except validate.InvalidOutput as exc:
                msg = MESSAGES["empty_output"] if exc.code == "empty_output" else MESSAGES["invalid_output"]
                self._finish(conn, run_id, "failed", code=exc.code, message=msg, output=result.text, model=result.model, version=result.version,
                             issues=[{"level": "error", "code": exc.code, "draft_id": None, "text": e} for e in exc.errors])
                return
            self._finish(conn, run_id, "succeeded", output=result.text, model=result.model, version=result.version, issues=issues, clean=clean)
        except Exception as exc:  # noqa: BLE001 - a job must always end in a recorded state
            try:
                self._finish(conn, run_id, "failed", code="adapter_error", message=f"{MESSAGES['adapter_error']} ({type(exc).__name__})")
            except Exception:  # noqa: BLE001
                pass
        finally:
            conn.close()

    @staticmethod
    def _finish(conn, run_id, state, code="", message="", output="", model="", version="", issues=None, clean=None):
        with tx(conn):
            cur = conn.execute(
                "UPDATE ai_run SET state = ?, error_code = ?, error_message = ?, output_text = ?, reported_model = ?, adapter_version = ?, issues_json = ?, finished_at = ? "
                "WHERE id = ? AND state IN ('queued', 'running')", (state, code, message, output, model, version, json.dumps(issues or []), utc_now(), run_id))
            if cur.rowcount == 0 or clean is None:
                return
            issues_by = {}
            for i in issues or []:
                issues_by.setdefault(i.get("draft_id"), []).append(i)
            pos = 0
            for t in clean["tasks"]:
                pos += 1
                conn.execute("INSERT INTO ai_draft(id, run_id, draft_id, parent_draft_id, position, title, description, payload_json, issues_json) VALUES (?,?,?,?,?,?,?,?,?)",
                             (uuid.uuid4().hex, run_id, t["draft_id"], None, pos, t["title"], t["description"], json.dumps(t, ensure_ascii=False), json.dumps(issues_by.get(t["draft_id"], []))))
                for s in t["subtasks"]:
                    pos += 1
                    conn.execute("INSERT INTO ai_draft(id, run_id, draft_id, parent_draft_id, position, title, description, payload_json, issues_json) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (uuid.uuid4().hex, run_id, s["draft_id"], t["draft_id"], pos, s["title"], s["description"], json.dumps(s, ensure_ascii=False), json.dumps(issues_by.get(s["draft_id"], []))))
            conn.execute("UPDATE ai_run SET questions_json = ? WHERE id = ?", (json.dumps(clean["questions"], ensure_ascii=False), run_id))
