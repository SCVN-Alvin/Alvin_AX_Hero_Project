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
        self.assertIn("data:font/woff2;base64,", html)
        self.assertNotIn("url(assets/", html)

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


FAKE_CODEX = """#!PYTHON
import json, os, sys, time
from pathlib import Path
here = Path(__file__).parent
cfg = json.loads((here / "cfg.json").read_text())
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if args == ["login", "status"]:
    print("Not logged in" if cfg.get("signed_out") else "Logged in using ChatGPT"); sys.exit(1 if cfg.get("signed_out") else 0)
if args == ["exec", "--help"]:
    print(cfg["help"]); sys.exit(0)
if args == ["features", "list"]:
    print("shell_tool  stable  true\\nsome_new_feature  beta  false"); sys.exit(0)
(here / "call.json").write_text(json.dumps({"argv": args, "env": dict(os.environ), "stdin": sys.stdin.read(), "cwd": os.getcwd()}))
if cfg.get("hang"):
    time.sleep(60)
if "stderr" in cfg:
    sys.stderr.write(cfg["stderr"]); sys.exit(1)
if "answer" in cfg:
    Path(args[args.index("--output-last-message") + 1]).write_text(cfg["answer"])
"""


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        self.binary = self.dir / "codex"
        self.binary.write_text(FAKE_CODEX.replace("PYTHON", sys.executable))
        self.binary.chmod(0o755)
        patcher = mock.patch.dict(app.os.environ, {"CODEX_BINARY": str(self.binary), "OPENAI_API_KEY": "must-not-reach-codex"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.configure(answer=json.dumps(fixture_plan()))

    def configure(self, **cfg):
        cfg.setdefault("help", " ".join(app.CODEX_REQUIRED_FLAGS))
        (self.dir / "cfg.json").write_text(json.dumps(cfg))

    def call(self):
        return json.loads((self.dir / "call.json").read_text())

    def request(self, model=""):
        return app.validate_request({"title": "Test", "text": SOURCE, "provider": "codex", "model": model, "max_tasks": 40})

    def test_ready_status_reports_chatgpt_sign_in(self):
        status = app.codex_status()
        self.assertTrue(status["installed"] and status["ready"])
        self.assertEqual((status["auth_mode"], status["version"]), ("chatgpt", "codex-cli 9.9.9"))

    def test_install_is_checked_first(self):
        self.binary.unlink()
        status = app.codex_status()
        self.assertFalse(status["installed"] or status["ready"])
        self.assertIn("not installed", status["message"])
        with self.assertRaisesRegex(app.AppError, "not installed"):
            app.run_codex(self.request())

    def test_signed_out_and_unsupported_version_block_generation(self):
        self.configure(signed_out=True)
        status = app.codex_status()
        self.assertTrue(status["installed"])
        self.assertFalse(status["signed_in"])
        self.assertIn("codex login", status["message"])
        with self.assertRaisesRegex(app.AppError, "not signed in"):
            app.run_codex(self.request())
        self.assertFalse((self.dir / "call.json").exists())
        self.configure(help="--json only")
        status = app.codex_status()
        self.assertFalse(status["ready"])
        self.assertIn("--output-schema", status["missing_flags"])

    def test_generation_is_restricted_and_validated(self):
        result = app.generate_plan_codex(self.request())
        self.assertEqual(len(result["plan"]["tasks"]), 3)
        self.assertTrue(result["plan"]["tasks"][1]["evidence"][0]["verified"])
        self.assertEqual(result["model"], "Codex default model")
        call = self.call()
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "mcp_servers={}", "shell_tool"):
            self.assertIn(flag, argv)
        self.assertNotIn("some_new_feature", argv)
        self.assertNotIn("-m", argv)
        self.assertNotIn(SOURCE, " ".join(argv))
        self.assertIn(SOURCE, json.loads(call["stdin"])["brief_text"])
        self.assertNotIn("OPENAI_API_KEY", call["env"])
        self.assertFalse(Path(call["cwd"]).exists())

    def test_plan_language_reaches_both_providers(self):
        self.assertIn("structured plan in English.", app.make_payload(self.request())["instructions"])
        data = app.validate_request({"title": "Test", "text": SOURCE, "provider": "codex", "language": "ko", "max_tasks": 40})
        self.assertIn("structured plan in Korean.", app.make_payload(data)["instructions"])
        self.assertIn("original language", app.make_payload(data)["instructions"])
        app.generate_plan_codex(data)
        prompt = self.call()["argv"][-1]
        self.assertIn("structured plan in Korean.", prompt)
        self.assertNotIn("structured plan in English.", prompt)
        vietnamese = app.validate_request({"text": SOURCE, "language": "vi"})
        self.assertIn("structured plan in Vietnamese.", app.make_payload(vietnamese)["instructions"])

    def test_unknown_language_rejected(self):
        for language in ("fr", ["ko"], 1):
            with self.assertRaisesRegex(app.AppError, "language"):
                app.validate_request({"text": SOURCE, "language": language})

    def test_requested_model_is_passed(self):
        result = app.generate_plan_codex(self.request("gpt-test"))
        argv = self.call()["argv"]
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-test")
        self.assertEqual(result["model"], "gpt-test")

    def test_option_like_model_rejected(self):
        with self.assertRaises(app.AppError):
            self.request("--dangerous")

    def test_bad_output_is_rejected(self):
        for answer, pattern in (("not json", "readable"), ("", "returned nothing"), (json.dumps({"tasks": "x"}), "invalid task plan")):
            self.configure(answer=answer)
            with self.assertRaisesRegex(app.AppError, pattern):
                app.generate_plan_codex(self.request())

    def test_failures_are_classified_without_leaking_output(self):
        for stderr, pattern, status in (("Error: 429 usage limit reached /Users/secret", "allowance", 429),
                                        ("401 Unauthorized /Users/secret", "not signed in", 503),
                                        ("The model is not supported /Users/secret", "Clear the Model", 400),
                                        ("something else /Users/secret", "stopped with an error", 502)):
            self.configure(stderr=stderr)
            with self.assertRaisesRegex(app.AppError, pattern) as caught:
                app.run_codex(self.request())
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn("/Users/secret", str(caught.exception))

    def test_cancel_and_timeout_stop_the_process(self):
        self.configure(hang=True)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        with self.assertRaisesRegex(app.AppError, "Cancelled") as caught:
            app.run_codex(self.request(), cancel)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(app.AppError) as caught:
            app.run_codex(self.request(), timeout=1)
        self.assertEqual(caught.exception.status, 504)

    def test_unknown_provider_rejected_and_openai_default_unchanged(self):
        with self.assertRaisesRegex(app.AppError, "provider"):
            app.validate_request({"text": SOURCE, "provider": "other"})
        data = app.validate_request({"text": SOURCE})
        self.assertEqual(data["provider"], "openai")
        self.assertTrue(data["model"])


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

    def test_codex_routes(self):
        ready = {"installed": True, "ready": True, "message": "Ready"}
        with mock.patch.object(app, "codex_status", return_value=ready):
            status, body, _ = self.fetch("/api/codex")
        self.assertEqual((status, json.loads(body)["ready"]), (200, True))
        payload = {"title": "Test", "text": SOURCE, "max_tasks": 40, "provider": "codex"}
        with mock.patch.object(app, "run_codex", return_value=json.dumps(fixture_plan())) as runner:
            status, body, _ = self.fetch("/api/generate", payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["model"], "Codex default model")
        self.assertIs(runner.call_args.args[1], self.server.cancel)
        self.assertEqual(self.fetch("/api/cancel", {})[0], 200)
        self.assertEqual(self.fetch("/api/cancel", {}, {"X-Briefboard-Token": "wrong"})[0], 403)
        self.server.cancel.clear()

    def test_cross_origin_and_missing_session_blocked(self):
        data = {"title": "Test", "text": SOURCE}
        self.assertEqual(self.fetch("/api/generate", data, {"Origin": "https://unrelated.example"})[0], 403)
        self.assertEqual(self.fetch("/api/generate", data, {"X-Briefboard-Token": "wrong"})[0], 403)

    def test_private_files_not_served(self):
        for path in ("/.env", "/.git/config", "/server.py", "/../local_server.py"):
            self.assertEqual(self.fetch(path)[0], 404)


if __name__ == "__main__":
    http = "--http" in sys.argv
    suites = [unittest.defaultTestLoader.loadTestsFromTestCase(LogicTests),
              unittest.defaultTestLoader.loadTestsFromTestCase(CodexTests)]
    if http:
        suites.append(unittest.defaultTestLoader.loadTestsFromTestCase(HTTPTests))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(suites))
    sys.exit(not result.wasSuccessful())
