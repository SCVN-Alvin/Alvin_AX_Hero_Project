"""Offline checks by default; --http adds loopback HTTP integration checks.

All OpenAI responses are mocked. No credentials or paid API calls are required.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import local_server as app

SOURCE = "Map and Camera\nSetup a two-level map for videos 001 and 002.\nCamera must clearly show both levels.\nUse the camera from Video 123."


def task(task_id, parent, title, kind="task", discipline="Dev", dependencies=None):
    return {"id": task_id, "parent_id": parent, "kind": kind, "title": title,
            "description": title, "discipline": discipline, "videos": ["001", "002"],
            "criteria": ["Both levels are readable."], "dependencies": dependencies or [],
            "estimate_days": None, "notes": "", "evidence": [{"section": "Map and Camera", "quote": "Camera must clearly show both levels."}]}


def fixture_plan():
    return {"project_title": "Test factory", "summary": "A two-level map and camera.",
            "open_questions": ["Confirm map dimensions."],
            "base_projects": [{"id": "prior", "name": "Video 123", "aliases": [], "references": [{"requirement": "Camera", "category": "Camera", "use_type": "Reference", "section": "References", "quote": "Use the camera from Video 123."}]}],
            "tasks": [task("g1", None, "Map and Camera", "group", "Mixed"),
                      task("a1", "g1", "Assemble map", discipline="Art"),
                      task("d1", "g1", "Setup camera", dependencies=["a1"])]}


def response(plan=None):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(plan or fixture_plan())}]}],
        "usage": {"input_tokens": 100, "output_tokens": 200}}


class LogicTests(unittest.TestCase):
    def request(self):
        return app.validate_request({"title": "Test brief", "text": SOURCE, "model": "gpt-6-astra", "max_tasks": 40})

    def test_payload_schema_and_no_storage(self):
        payload = app.make_payload(self.request())
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertNotIn("Authorization", json.dumps(payload))
        self.assertIn(SOURCE, json.loads(payload["input"])["brief_text"])

    def test_complete_generation_keeps_hierarchy_and_quotes(self):
        result = app.generate_plan(self.request(), "test-only-key", lambda body, key: response())
        self.assertEqual(result["plan"]["tasks"][2]["dependencies"], ["a1"])
        self.assertTrue(result["plan"]["tasks"][1]["evidence"][0]["verified"])
        self.assertEqual(result["usage"]["output_tokens"], 200)
        self.assertTrue(result["plan"]["base_projects"][0]["references"][0]["verified"])

    def test_missing_key_never_calls_provider(self):
        requester = mock.Mock()
        with self.assertRaisesRegex(app.AppError, "OPENAI_API_KEY"):
            app.generate_plan(self.request(), "", requester)
        requester.assert_not_called()

    def test_parent_and_dependency_cycles_rejected(self):
        p = fixture_plan()
        p["tasks"][0]["parent_id"] = "d1"
        with self.assertRaisesRegex(app.AppError, "cycle"):
            app.validate_plan(p, SOURCE)
        p = fixture_plan()
        p["tasks"][1]["dependencies"] = ["d1"]
        with self.assertRaisesRegex(app.AppError, "cycle"):
            app.validate_plan(p, SOURCE)

    def test_forward_missing_parent_rejected(self):
        p = fixture_plan()
        p["tasks"] = list(reversed(p["tasks"]))
        p["tasks"][-1]["parent_id"] = "missing"
        with self.assertRaisesRegex(app.AppError, "parent"):
            app.validate_plan(p, SOURCE)

    def test_unverified_evidence_is_flagged(self):
        p = fixture_plan()
        p["tasks"][1]["evidence"][0]["quote"] = "Invented requirement."
        result = app.validate_plan(p, SOURCE)
        self.assertFalse(result["tasks"][1]["evidence"][0]["verified"])

    def test_refusal_incomplete_and_invalid_json(self):
        for result in [
            {"status": "incomplete"},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "bad"}]}]},
        ]:
            with self.assertRaises(app.AppError):
                app.generate_plan(self.request(), "test-only-key", lambda body, key: result)

    def test_invalid_requests_and_limits(self):
        for data in [{"text": "short"}, {"text": SOURCE, "max_tasks": True},
                     {"text": SOURCE, "model": "https://not-a-model"},
                     {"text": "x" * (app.MAX_SOURCE_CHARS + 1)}]:
            with self.assertRaises(app.AppError):
                app.validate_request(data)
        p = fixture_plan()
        with self.assertRaisesRegex(app.AppError, "limit"):
            app.validate_plan(p, SOURCE, 2)

    def test_provider_errors_do_not_expose_credentials(self):
        failure = HTTPError("https://api.openai.com/v1/responses", 401, "test-only-secret", {}, None)
        with mock.patch.object(app, "urlopen", side_effect=failure):
            with self.assertRaises(app.AppError) as caught:
                app.call_openai(app.make_payload(self.request()), "test-only-secret")
        self.assertNotIn("test-only-secret", str(caught.exception))

    def test_local_html_has_no_external_assets(self):
        html = (Path(__file__).parent / "local-build/index.html").read_text()
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fetch('sources.json')", html)
        self.assertNotIn('<script src=', html)
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertTrue(html.split('<style>', 1)[1].lstrip().startswith(':root{'))
        self.assertIn("EMBEDDED_SOURCES", html)
        self.assertIn("data:image/png;base64,", html)

    def test_project_reference_evidence_and_deduplication(self):
        refs = [{"requirement": "Camera", "category": "Camera", "use_type": "Reference",
                 "section": "References", "quote": "Use the camera from Video 123."}]
        projects = [{"id": "p1", "name": "Factory A", "aliases": ["Video 123"],
                     "references": [copy.deepcopy(refs[0]), copy.deepcopy(refs[0])]}]
        result = app.validate_base_projects(projects, "Use the camera from Video 123.")
        self.assertEqual(len(result[0]["references"]), 1)
        self.assertTrue(result[0]["references"][0]["verified"])
        projects[0]["references"] = [{**refs[0], "quote": "An invented quote."}]
        self.assertFalse(app.validate_base_projects(projects, SOURCE)[0]["references"][0]["verified"])

    def test_current_output_videos_do_not_count_as_source_projects(self):
        quote = "Camera for Video 003 stays on Level 2."
        projects = [{"id": "p1", "name": "Video 003", "aliases": [], "references": [{
            "requirement": "Camera", "category": "Camera", "use_type": "Reference", "section": "Flow", "quote": quote}]}]
        self.assertFalse(app.validate_base_projects(projects, quote)[0]["references"][0]["verified"])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        index = Path(cls.folder.name) / "index.html"
        index.write_text("<!doctype html><title>Briefboard test</title>")
        cls.server = app.make_server(0, index)
        cls.server.api_key = ""
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.folder.cleanup()

    def fetch(self, path, data=None, headers=None):
        h = headers or {}
        if data is not None:
            h.setdefault("Content-Type", "application/json")
            h.setdefault("X-Briefboard-Token", self.server.session_token)
        req = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            with urlopen(req, timeout=5) as res:
                return res.status, res.read(), dict(res.headers)
        except HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_serves_html_and_nonsecret_config(self):
        status, body, headers = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Briefboard test", body)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        status, body, _ = self.fetch("/api/config")
        data = json.loads(body)
        self.assertIn("session_token", data)
        self.assertNotIn("api_key", data)
        self.assertEqual(status, 200)

    def test_backend_generation_route_and_error_cases(self):
        payload = {"title": "Test", "text": SOURCE, "max_tasks": 40}
        status, body, _ = self.fetch("/api/generate", payload)
        self.assertEqual(status, 503)
        self.server.api_key = "test-only-key"
        try:
            with mock.patch.object(app, "call_openai", return_value=response()) as provider:
                status, body, _ = self.fetch("/api/generate", payload)
                self.assertEqual(status, 200)
                self.assertEqual(len(json.loads(body)["plan"]["tasks"]), 3)
                provider.assert_called_once()
        finally:
            self.server.api_key = ""

    def test_cross_origin_and_missing_session_blocked(self):
        data = {"title": "Test", "text": SOURCE}
        self.assertEqual(self.fetch("/api/generate", data, {"Origin": "https://unrelated.example"})[0], 403)
        self.assertEqual(self.fetch("/api/generate", data, {"X-Briefboard-Token": "wrong"})[0], 403)

    def test_private_files_not_served(self):
        for path in ("/.env", "/.git/config", "/server.py", "/../local_server.py"):
            self.assertEqual(self.fetch(path)[0], 404)


if __name__ == "__main__":
    http = "--http" in sys.argv
    suites = [unittest.defaultTestLoader.loadTestsFromTestCase(LogicTests)]
    if http:
        suites.append(unittest.defaultTestLoader.loadTestsFromTestCase(HTTPTests))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(suites))
    sys.exit(not result.wasSuccessful())
