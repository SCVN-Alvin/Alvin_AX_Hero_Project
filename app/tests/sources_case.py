"""Shared test base: a running server, a project, and helpers to import a fixture export."""

import http.client
import os
import shutil
import sqlite3
import tempfile
import time

from export_fixture import make_export
from helpers import AppCase


class SourcesCase(AppCase):
    def setUp(self):
        super().setUp()
        self.work = tempfile.mkdtemp(prefix="organizer-export-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.pid = self.project("Brief", "BRF")["id"]

    def run_import(self, html, folder=None, pid=None, expect="succeeded", timeout=60):
        pid = pid or self.pid
        status, run = self.api.post(f"/api/projects/{pid}/imports", {"html_path": html, "companion_path": folder})
        self.assertEqual(status, 202, run)
        end = time.time() + timeout
        while time.time() < end:
            _, s = self.api.get(f"/api/imports/{run['id']}")
            if s["state"] != "running":
                self.assertEqual(s["state"], expect, s)
                return s
            time.sleep(0.05)
        self.fail("import did not finish")

    def export(self, **kw):
        html, folder = make_export(self.work, **kw)
        return html, folder

    def raw(self, path, headers=None, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, headers=headers or {})
        r = conn.getresponse()
        body = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        return r.status, hdrs, body

    def counts(self):
        conn = sqlite3.connect(self.storage.db_path)
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                    ("source_document", "source_block", "source_cell", "asset", "reference", "scenario", "scenario_step", "source_section")}
        finally:
            conn.close()
