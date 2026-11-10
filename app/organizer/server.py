"""Local HTTP service: JSON API plus the bundled static interface.

Standard library only. No outbound requests are ever made by the service, and the
interface is served with a Content-Security-Policy that blocks any external resource.
"""

import json
import logging
import mimetypes
import os
import re
import sqlite3
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__
from .db import connect
from . import fsbrowse, importer
from .ai.manager import AiManager
from . import validation as _v
from .errors import AppError, NotFound, ValidationError
from .meta import as_meta
from .services import projects as projects_svc
from .services import ai as ai_svc
from .services import candidates as cand_svc
from .services import rules as rules_svc
from .services import sources as sources_svc
from .services import tasks as tasks_svc

log = logging.getLogger("organizer")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MAX_BODY = 2 * 1024 * 1024
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")

_ASSET_RE = re.compile(r"^/api/assets/([0-9a-f]{32})/file$")
ASSET_CSP = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"

# (method, regex, handler name)
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn

    return deco


class Context:
    """Per-request data handed to route handlers."""

    def __init__(self, server, conn, query, body):
        self.server = server
        self.storage = server.storage
        self.conn = conn
        self.query = query
        self.body = body


def v_text(data, field):
    return _v.text(data, field, max_len=64, required=True)


def _body_dict(ctx):
    if not isinstance(ctx.body, dict):
        raise ValidationError("Request body must be a JSON object")
    return ctx.body


# ---- routes -----------------------------------------------------------------

@route("GET", "/api/health")
def health(ctx):
    n = ctx.conn.execute("SELECT version FROM schema_migration ORDER BY version DESC LIMIT 1").fetchone()
    return {"status": "ok", "version": __version__, "schema_version": n[0] if n else 0, "data_dir": ctx.storage.data_dir}


@route("GET", "/api/meta")
def meta(ctx):
    return as_meta()


@route("GET", "/api/projects")
def list_projects(ctx):
    return {"projects": projects_svc.list_projects(ctx.conn)}


@route("POST", "/api/projects")
def create_project(ctx):
    return 201, projects_svc.create_project(ctx.conn, ctx.storage, _body_dict(ctx))


@route("GET", "/api/projects/<pid>")
def get_project(ctx, pid):
    return projects_svc.get_project(ctx.conn, pid)


@route("PATCH", "/api/projects/<pid>")
def update_project(ctx, pid):
    return projects_svc.update_project(ctx.conn, pid, _body_dict(ctx))


@route("DELETE", "/api/projects/<pid>")
def delete_project(ctx, pid):
    confirm = (_body_dict(ctx).get("confirm_prefix") if ctx.body is not None else None)
    return projects_svc.delete_project(ctx.conn, ctx.storage, pid, confirm)


@route("GET", "/api/projects/<pid>/people")
def list_people(ctx, pid):
    return {"people": projects_svc.list_people(ctx.conn, pid)}


@route("POST", "/api/projects/<pid>/people")
def create_person(ctx, pid):
    return 201, projects_svc.create_person(ctx.conn, pid, _body_dict(ctx))


@route("PATCH", "/api/people/<person_id>")
def rename_person(ctx, person_id):
    return projects_svc.rename_person(ctx.conn, person_id, _body_dict(ctx))


@route("DELETE", "/api/people/<person_id>")
def delete_person(ctx, person_id):
    return projects_svc.delete_person(ctx.conn, person_id)


@route("GET", "/api/projects/<pid>/board")
def board(ctx, pid):
    return tasks_svc.board(ctx.conn, pid, tasks_svc.parse_filters(ctx.query))


@route("GET", "/api/projects/<pid>/tasks")
def list_tasks(ctx, pid):
    return {"tasks": tasks_svc.list_tasks(ctx.conn, pid, tasks_svc.parse_filters(ctx.query))}


@route("POST", "/api/projects/<pid>/tasks")
def create_task(ctx, pid):
    return 201, tasks_svc.create_task(ctx.conn, pid, _body_dict(ctx))


@route("GET", "/api/projects/<pid>/tags")
def list_tags(ctx, pid):
    projects_svc.get_project_row(ctx.conn, pid)
    rows = ctx.conn.execute("SELECT name FROM tag WHERE project_id = ? ORDER BY lower(name)", (pid,))
    return {"tags": [r["name"] for r in rows]}


@route("GET", "/api/tasks/<tid>")
def get_task(ctx, tid):
    return tasks_svc.get_task(ctx.conn, tid)


@route("PATCH", "/api/tasks/<tid>")
def update_task(ctx, tid):
    return tasks_svc.update_task(ctx.conn, tid, _body_dict(ctx))


@route("DELETE", "/api/tasks/<tid>")
def delete_task(ctx, tid):
    return tasks_svc.delete_task(ctx.conn, tid)


@route("POST", "/api/tasks/<tid>/move")
def move_task(ctx, tid):
    b = _body_dict(ctx)
    return tasks_svc.move_task(ctx.conn, tid, b.get("status"), b.get("before_id") or None, bool(b.get("confirm_unfinished")))


@route("POST", "/api/tasks/<tid>/criteria")
def add_criterion(ctx, tid):
    return 201, tasks_svc.add_criterion(ctx.conn, tid, _body_dict(ctx))


@route("PATCH", "/api/criteria/<cid>")
def update_criterion(ctx, cid):
    return tasks_svc.update_criterion(ctx.conn, cid, _body_dict(ctx))


@route("DELETE", "/api/criteria/<cid>")
def delete_criterion(ctx, cid):
    return tasks_svc.delete_criterion(ctx.conn, cid)


@route("POST", "/api/tasks/<tid>/dependencies")
def add_dependency(ctx, tid):
    return 201, tasks_svc.add_dependency(ctx.conn, tid, _body_dict(ctx).get("depends_on_id"))


@route("DELETE", "/api/tasks/<tid>/dependencies/<dep_id>")
def remove_dependency(ctx, tid, dep_id):
    return tasks_svc.remove_dependency(ctx.conn, tid, dep_id)


# ---- sources (Milestone 2) ----------------------------------------------------

def _first(ctx, name, default=""):
    return (ctx.query.get(name) or [default])[0]


def _int(ctx, name, default):
    try:
        return int(_first(ctx, name, str(default)))
    except ValueError:
        raise ValidationError(f"{name} must be a whole number", field=name)


@route("GET", "/api/fs/list")
def fs_list(ctx):
    return fsbrowse.list_dir(_first(ctx, "path"), ctx.server.import_roots, _first(ctx, "hidden") == "1")


@route("POST", "/api/projects/<pid>/imports/preview")
def import_preview(ctx, pid):
    projects_svc.get_project_row(ctx.conn, pid)
    b = _body_dict(ctx)
    return importer.preview(ctx.storage, ctx.conn, pid, b.get("html_path"), b.get("companion_path"), ctx.server.import_roots)


@route("POST", "/api/projects/<pid>/imports")
def import_start(ctx, pid):
    b = _body_dict(ctx)
    run_id = ctx.server.imports.start(pid, b.get("html_path"), b.get("companion_path"))
    return 202, ctx.server.imports.status(ctx.conn, run_id)


@route("GET", "/api/projects/<pid>/imports")
def import_runs(ctx, pid):
    return {"runs": sources_svc.list_runs(ctx.conn, pid)}


@route("GET", "/api/imports/<run_id>")
def import_status(ctx, run_id):
    return ctx.server.imports.status(ctx.conn, run_id)


@route("POST", "/api/imports/<run_id>/cancel")
def import_cancel(ctx, run_id):
    return ctx.server.imports.cancel(run_id)


@route("GET", "/api/projects/<pid>/documents")
def list_documents(ctx, pid):
    return {"documents": sources_svc.list_documents(ctx.conn, pid)}


@route("GET", "/api/documents/<doc_id>")
def get_document(ctx, doc_id):
    return sources_svc.get_document(ctx.conn, doc_id)


@route("DELETE", "/api/documents/<doc_id>")
def delete_document(ctx, doc_id):
    return sources_svc.delete_document(ctx.conn, ctx.storage, doc_id)


@route("GET", "/api/documents/<doc_id>/blocks")
def document_blocks(ctx, doc_id):
    return sources_svc.get_blocks(ctx.conn, doc_id, _int(ctx, "start", 0), _int(ctx, "limit", 400))


@route("GET", "/api/documents/<doc_id>/search")
def document_search(ctx, doc_id):
    return sources_svc.search_blocks(ctx.conn, doc_id, _first(ctx, "q"))


@route("GET", "/api/documents/<doc_id>/assets")
def document_assets(ctx, doc_id):
    return sources_svc.list_assets(ctx.conn, doc_id)


@route("GET", "/api/documents/<doc_id>/scenarios")
def document_scenarios(ctx, doc_id):
    return sources_svc.list_scenarios(ctx.conn, doc_id)


@route("POST", "/api/documents/<doc_id>/scenarios/extract")
def document_scenarios_extract(ctx, doc_id):
    return sources_svc.reextract_scenarios(ctx.conn, doc_id, _body_dict(ctx))


@route("PATCH", "/api/sections/<section_id>")
def update_section(ctx, section_id):
    return sources_svc.set_section_role(ctx.conn, section_id, _body_dict(ctx))


# ---- candidates, rules, approval (Milestone 3) ---------------------------------

@route("GET", "/api/documents/<doc_id>/candidates")
def doc_candidates(ctx, doc_id):
    return cand_svc.list_candidates(ctx.conn, doc_id)


@route("POST", "/api/documents/<doc_id>/candidates/generate")
def doc_generate(ctx, doc_id):
    return 201, cand_svc.generate(ctx.conn, doc_id, ctx.body if isinstance(ctx.body, dict) else {})


@route("POST", "/api/documents/<doc_id>/candidates/apply-rules")
def doc_apply_rules(ctx, doc_id):
    return cand_svc.apply_rules(ctx.conn, doc_id)


@route("GET", "/api/candidates/<cid>")
def get_candidate(ctx, cid):
    return cand_svc.get_candidate(ctx.conn, cid)


@route("PATCH", "/api/candidates/<cid>")
def patch_candidate(ctx, cid):
    return cand_svc.update_candidate(ctx.conn, cid, _body_dict(ctx))


@route("POST", "/api/candidates/<cid>/suggestions/<sid>")
def candidate_suggestion(ctx, cid, sid):
    return cand_svc.suggestion_action(ctx.conn, cid, sid, _body_dict(ctx).get("action"))


@route("POST", "/api/candidates/<cid>/accept-suggestions")
def candidate_accept_all(ctx, cid):
    return cand_svc.accept_all_suggestions(ctx.conn, cid)


@route("POST", "/api/candidates/<cid>/references")
def candidate_reference(ctx, cid):
    return cand_svc.set_reference(ctx.conn, cid, _body_dict(ctx))


@route("POST", "/api/candidates/<cid>/references/all")
def candidate_references_all(ctx, cid):
    return cand_svc.bulk_references(ctx.conn, cid, _body_dict(ctx))


@route("PATCH", "/api/candidates/<cid>/references/<rid>")
def candidate_reference_update(ctx, cid, rid):
    return cand_svc.update_reference(ctx.conn, cid, rid, _body_dict(ctx))


@route("GET", "/api/candidates/<cid>/reference-options")
def candidate_reference_options(ctx, cid):
    return cand_svc.reference_options(ctx.conn, cid, (ctx.query.get("q") or [""])[0])


@route("POST", "/api/candidates/<cid>/primary-reference")
def candidate_primary_set(ctx, cid):
    return cand_svc.set_primary_reference(ctx.conn, cid, _body_dict(ctx))


@route("DELETE", "/api/candidates/<cid>/primary-reference")
def candidate_primary_clear(ctx, cid):
    return cand_svc.clear_primary_reference(ctx.conn, cid)


@route("POST", "/api/candidates/<cid>/reference-decision")
def candidate_reference_decision(ctx, cid):
    return cand_svc.set_reference_decision(ctx.conn, cid, _body_dict(ctx))


@route("POST", "/api/candidates/<cid>/clarifications")
def candidate_clarification_add(ctx, cid):
    return 201, cand_svc.add_clarification(ctx.conn, cid, _body_dict(ctx))


@route("PATCH", "/api/clarifications/<clid>")
def clarification_update(ctx, clid):
    return cand_svc.update_clarification(ctx.conn, clid, _body_dict(ctx))


@route("DELETE", "/api/clarifications/<clid>")
def clarification_delete(ctx, clid):
    return cand_svc.delete_clarification(ctx.conn, clid)


@route("POST", "/api/candidates/<cid>/descriptions/<vid>/restore")
def candidate_description_restore(ctx, cid, vid):
    return cand_svc.restore_description(ctx.conn, cid, vid)


@route("PATCH", "/api/tasks/<tid>/references/<rid>")
def task_reference_update(ctx, tid, rid):
    return cand_svc.update_task_reference(ctx.conn, tid, rid, _body_dict(ctx))


@route("POST", "/api/tasks/<tid>/primary-reference")
def task_primary_set(ctx, tid):
    return cand_svc.set_task_primary(ctx.conn, tid, v_text(_body_dict(ctx), "reference_id"))


@route("DELETE", "/api/tasks/<tid>/primary-reference")
def task_primary_clear(ctx, tid):
    return cand_svc.clear_task_primary(ctx.conn, tid)


@route("POST", "/api/tasks/<tid>/reference-decision")
def task_reference_decision(ctx, tid):
    return cand_svc.set_task_reference_decision(ctx.conn, tid, _body_dict(ctx))


@route("DELETE", "/api/candidates/<cid>/scenarios/<identifier>")
def candidate_remove_scenario(ctx, cid, identifier):
    return cand_svc.remove_scenario(ctx.conn, cid, identifier)


@route("POST", "/api/candidates/<cid>/split")
def candidate_split(ctx, cid):
    return cand_svc.split(ctx.conn, cid, _body_dict(ctx))


@route("POST", "/api/candidates/merge")
def candidates_merge(ctx):
    return cand_svc.merge(ctx.conn, _body_dict(ctx))


@route("POST", "/api/candidates/dismiss")
def candidates_dismiss(ctx):
    b = _body_dict(ctx)
    return cand_svc.dismiss(ctx.conn, b.get("ids"), b.get("cascade", True) is not False)


@route("POST", "/api/candidates/restore")
def candidates_restore(ctx):
    return cand_svc.restore(ctx.conn, _body_dict(ctx).get("ids"))


@route("POST", "/api/candidates/classify")
def candidates_classify(ctx):
    b = _body_dict(ctx)
    return cand_svc.bulk_classify(ctx.conn, b.get("ids"), b)


@route("POST", "/api/candidates/accept-suggestions")
def candidates_accept(ctx):
    return cand_svc.bulk_accept(ctx.conn, _body_dict(ctx).get("ids"))


@route("POST", "/api/projects/<pid>/candidates/approve")
def candidates_approve(ctx, pid):
    b = _body_dict(ctx)
    return 201, cand_svc.approve(ctx.conn, pid, b.get("ids"), b.get("with_children", True) is not False)


# ---- optional AI drafting ------------------------------------------------------

@route("GET", "/api/ai/settings")
def ai_get_settings(ctx):
    return ai_svc.get_settings(ctx.conn, ctx.server.ai)


@route("PATCH", "/api/ai/settings")
def ai_patch_settings(ctx):
    return ai_svc.update_settings(ctx.conn, ctx.server.ai, _body_dict(ctx))


@route("POST", "/api/ai/check")
def ai_check(ctx):
    return ai_svc.check_connection(ctx.conn, ctx.server.ai)


@route("POST", "/api/documents/<doc_id>/ai/preview")
def ai_preview(ctx, doc_id):
    return ai_svc.preview(ctx.conn, doc_id, _body_dict(ctx))


@route("POST", "/api/documents/<doc_id>/ai/runs")
def ai_start(ctx, doc_id):
    b = _body_dict(ctx)
    return 202, ai_svc.start_run(ctx.conn, ctx.server.ai, doc_id, b, b.get("retry_of"))


@route("GET", "/api/documents/<doc_id>/ai/runs")
def ai_list_runs(ctx, doc_id):
    return ai_svc.list_runs(ctx.conn, doc_id)


@route("GET", "/api/ai/runs/<run_id>")
def ai_get_run(ctx, run_id):
    return ai_svc.run_dict(ctx.conn, run_id, detail=True)


@route("POST", "/api/ai/runs/<run_id>/cancel")
def ai_cancel(ctx, run_id):
    return ai_svc.cancel_run(ctx.conn, ctx.server.ai, run_id)


@route("POST", "/api/ai/runs/<run_id>/import")
def ai_import(ctx, run_id):
    return ai_svc.import_drafts(ctx.conn, run_id, _body_dict(ctx))


@route("POST", "/api/candidates/<cid>/ai-proposals/<pid>")
def ai_proposal(ctx, cid, pid):
    return ai_svc.proposal_action(ctx.conn, cid, pid, _body_dict(ctx).get("action"))


@route("GET", "/api/tasks/<tid>/evidence")
def task_evidence(ctx, tid):
    return cand_svc.task_evidence(ctx.conn, tid)


@route("GET", "/api/projects/<pid>/rules")
def list_rules(ctx, pid):
    return rules_svc.list_rules(ctx.conn, pid)


@route("POST", "/api/projects/<pid>/rules")
def create_rule(ctx, pid):
    return 201, rules_svc.create_rule(ctx.conn, pid, _body_dict(ctx))


@route("POST", "/api/projects/<pid>/rules/preview")
def preview_rule(ctx, pid):
    b = _body_dict(ctx)
    return rules_svc.preview_rule(ctx.conn, pid, b.get("document_id"), b.get("rule") or {}, b.get("rule_id"))


@route("PATCH", "/api/rules/<rid>")
def update_rule(ctx, rid):
    return rules_svc.update_rule(ctx.conn, rid, _body_dict(ctx))


@route("DELETE", "/api/rules/<rid>")
def delete_rule(ctx, rid):
    return rules_svc.delete_rule(ctx.conn, rid)


# ---- request handling -------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "OfflineTaskOrganizer/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # route access logs through logging
        log.info("%s %s", self.address_string(), fmt % args)

    # -- guards
    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name in LOCAL_HOSTS or name == self.server.bind_host.lower():
            return True
        return name in self.server.extra_hosts

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc.lower() == (self.headers.get("Host") or "").lower()

    # -- dispatch
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method):
        self._body_consumed = False
        try:
            if not self._host_ok():
                return self._send_json(403, {"error": {"code": "bad_host", "message": "Unexpected Host header", "details": {}}})
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            m = _ASSET_RE.match(path)
            if m and method in ("GET", "HEAD"):
                return self._asset(m.group(1), head=(method == "HEAD"))
            if path.startswith("/api/"):
                return self._api(method, path, parse_qs(parts.query))
            if method not in ("GET", "HEAD"):
                return self._send_json(405, {"error": {"code": "method_not_allowed", "message": "Method not allowed", "details": {}}})
            return self._static(path, head=(method == "HEAD"))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValidationError("Request body is too large")
        if length == 0:
            return None
        raw = self.rfile.read(length)
        self._body_consumed = True
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError("Request body is not valid JSON")

    def _api(self, method, path, query):
        if method == "HEAD":
            method = "GET"
        conn = None
        try:
            if method in ("POST", "PATCH", "PUT", "DELETE"):
                if not self._origin_ok():
                    raise AppError("Cross-origin request refused", code="bad_origin")
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if int(self.headers.get("Content-Length") or 0) and ctype != "application/json":
                    raise AppError("Content-Type must be application/json", code="bad_content_type")
            body = self._read_body() if method in ("POST", "PATCH", "PUT", "DELETE") else None
            allowed = False
            for m, rx, fn in ROUTES:
                match = rx.match(path)
                if not match:
                    continue
                allowed = True
                if m != method:
                    continue
                conn = connect(self.server.storage.db_path)
                ctx = Context(self.server, conn, query, body)
                result = fn(ctx, **match.groupdict())
                status, payload = result if isinstance(result, tuple) else (200, result)
                return self._send_json(status, payload)
            if allowed:
                raise AppError("Method not allowed", code="method_not_allowed")
            raise NotFound("No such endpoint")
        except AppError as exc:
            status = 405 if exc.code == "method_not_allowed" else exc.status
            if exc.code in ("bad_origin", "bad_content_type"):
                status = 403 if exc.code == "bad_origin" else 415
            self._send_json(status, exc.to_dict())
        except sqlite3.OperationalError as exc:
            msg = "The database is busy; please retry." if "locked" in str(exc) else f"The database could not complete the write: {exc}"
            log.error("sqlite operational error: %s", exc)
            self._send_json(503, {"error": {"code": "database_unavailable", "message": msg, "details": {}}})
        except sqlite3.IntegrityError as exc:
            log.error("integrity error: %s", exc)
            self._send_json(409, {"error": {"code": "constraint_failed", "message": f"The change was rejected by a data rule: {exc}", "details": {}}})
        except Exception:  # noqa: BLE001 - last-resort handler
            log.error("unhandled error\n%s", traceback.format_exc())
            self._send_json(500, {"error": {"code": "internal_error", "message": "Unexpected server error. The change was not saved.", "details": {}}})
        finally:
            if conn is not None:
                conn.close()

    # -- responses
    def _common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if int(self.headers.get("Content-Length") or 0) > 0 and not getattr(self, "_body_consumed", True):
            # We answered without reading the request body; do not reuse this connection.
            self.send_header("Connection", "close")
            self.close_connection = True
        self._common_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


    # -- imported media: controlled route with byte-range support (needed to seek in video)
    def _asset(self, asset_id, head=False):
        conn = connect(self.server.storage.db_path)
        try:
            info = sources_svc.asset_for_serving(conn, self.server.storage, asset_id)
        except AppError as exc:
            return self._send_json(exc.status, exc.to_dict())
        finally:
            conn.close()
        size, ctype = info["size"], info["media_type"]
        inline = ctype in importer.INLINE_TYPES
        etag = '"' + (info["hash"] or "") + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self._asset_headers(info, inline)
            self.end_headers()
            return
        rng = _parse_range(self.headers.get("Range"), size)
        if rng == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self._asset_headers(info, inline)
            self.end_headers()
            return
        start, end = rng if rng else (0, size - 1)
        length = end - start + 1 if size else 0
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype if inline else "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        self._asset_headers(info, inline)
        self.end_headers()
        if head or not size:
            return
        with open(info["path"], "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(262144, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _asset_headers(self, info, inline):
        name = quote(info["name"], safe="")
        self.send_header("Content-Disposition", ("inline" if inline else "attachment") + f"; filename*=UTF-8''{name}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", ASSET_CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _static(self, path, head=False):
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        base = os.path.realpath(STATIC_DIR)
        target = os.path.realpath(os.path.join(base, rel))
        if os.path.commonpath([base, target]) != base or not os.path.isfile(target):
            # Hash-routed single page: unknown non-asset paths are real 404s.
            return self._send_json(404, {"error": {"code": "not_found", "message": "Not found", "details": {}}})
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        size = os.path.getsize(target)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        self._common_headers()
        self.end_headers()
        if not head:
            with open(target, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)


def _parse_range(header, size):
    """None (serve everything), (start, end) inclusive, or 'unsatisfiable'. A malformed or
    multi-range header is ignored, which RFC 9110 allows."""
    if not header:
        return None
    m = re.match(r"^\s*bytes=(\d*)-(\d*)\s*$", header)
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    a, b = m.group(1), m.group(2)
    if a == "":
        n = int(b)
        if n == 0 or size == 0:
            return "unsatisfiable"
        return max(size - n, 0), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size:
        return "unsatisfiable"
    if end < start:
        return None
    return start, min(end, size - 1)


class OrganizerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128  # the default of 5 can refuse connections when a page opens many requests at once

    def __init__(self, address, storage, extra_hosts=(), import_roots=(), ai_fake=None):
        self.storage = storage
        self.ai = AiManager(storage, fake_adapter=ai_fake)
        self.ai.recover()
        self.bind_host = address[0]
        self.extra_hosts = {h.lower() for h in extra_hosts}
        self.import_roots = tuple(import_roots)
        self.imports = importer.ImportManager(storage, self.import_roots)
        self.imports.recover()
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        # A browser closing a connection mid-transfer (video seeking, tab closed) is routine, not an error.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(storage, host="127.0.0.1", port=8765, extra_hosts=(), import_roots=(), ai_fake=None):
    return OrganizerServer((host, port), storage, extra_hosts, import_roots, ai_fake)
