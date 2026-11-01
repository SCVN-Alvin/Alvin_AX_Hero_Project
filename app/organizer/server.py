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
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .db import connect
from .errors import AppError, NotFound, ValidationError
from .meta import as_meta
from .services import projects as projects_svc
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
        self.storage = server.storage
        self.conn = conn
        self.query = query
        self.body = body


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


class OrganizerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, storage, extra_hosts=()):
        self.storage = storage
        self.bind_host = address[0]
        self.extra_hosts = {h.lower() for h in extra_hosts}
        super().__init__(address, Handler)


def make_server(storage, host="127.0.0.1", port=8765, extra_hosts=()):
    return OrganizerServer((host, port), storage, extra_hosts)
