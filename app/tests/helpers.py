import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organizer.db import migrate  # noqa: E402
from organizer.server import make_server  # noqa: E402
from organizer.storage import Storage  # noqa: E402


class Api:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json"} if data is not None else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as err:
            raw = err.read()
            err.close()
            try:
                return err.code, json.loads(raw)
            except ValueError:
                return err.code, raw

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def patch(self, path, body):
        return self.call("PATCH", path, body)

    def delete(self, path, body=None):
        return self.call("DELETE", path, body)

    # convenience that asserts success
    def ok(self, method, path, body=None, expect=(200, 201)):
        status, payload = self.call(method, path, body)
        if status not in expect:
            raise AssertionError(f"{method} {path} -> {status}: {payload}")
        return payload


class AppCase(unittest.TestCase):
    """Starts a real server on a free port against a throw-away data directory."""

    def start(self, data_dir=None):
        self.data_dir = data_dir or tempfile.mkdtemp(prefix="organizer-test-")
        self.storage = Storage(self.data_dir)
        self.storage.init()
        migrate(self.storage.db_path)
        self.server = make_server(self.storage, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.api = Api(f"http://127.0.0.1:{self.port}")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def setUp(self):
        self.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.stop()
        finally:
            shutil.rmtree(self.data_dir, ignore_errors=True)

    def restart(self):
        self.stop()
        self.start(self.data_dir)

    def project(self, name="Alpha", prefix="ALP"):
        return self.api.ok("POST", "/api/projects", {"name": name, "ticket_prefix": prefix})

    def task(self, pid, title="A task", **fields):
        return self.api.ok("POST", f"/api/projects/{pid}/tasks", dict(title=title, **fields))
