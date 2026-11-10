"""Optional AI drafting (AI-001..AI-006), driven by the scripted adapter, plus the Codex CLI adapter against a stand-in executable."""

import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest

from export_fixture import GIF, MP4, PNG, make_export
from test_candidates import BRIEF, CandidateCase
from test_revision import MEDIA_BRIEF

from organizer.ai import payload, validate
from organizer.ai.adapters import AdapterError, CodexCliAdapter, FakeAdapter, classify_failure
from organizer.ai.manager import ASSETS, read_asset
from organizer.db import migrate
from organizer.server import make_server
from organizer.storage import Storage

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WOOD_SCRIPT = {"tasks": [{
    "match_label": "Wood shave", "title": "Implement wood shaving", "description": "Shave a log clean or bad. Hand shaving uses 10 wood logs and the saw machine 40.",
    "disciplines": ["dev", "art"], "category": "core_gameplay", "impact": "high", "classification_reason": "Central mechanic named in the brief.",
    "cite": "all",
    "subtasks": [{"title": "Shave clean wood", "description": "Shave the log from start to end.", "cite_containing": ["clean wood"], "scenario_ids": ["001", "002"]},
                 {"title": "Shave bad wood", "description": "The saw machine is already unlocked.", "cite_containing": ["saw machine"]},
                 {"title": "Configurable output values", "description": "Keep the wood amounts adjustable.", "basis": "inferred"}]}],
    "questions": [{"text": "Is the saw machine always unlocked in the bad-wood scenarios?", "cite_containing": ["saw machine is already"]}]}


class AiCase(CandidateCase):
    script = {}

    def start(self, data_dir=None):
        if not hasattr(self, "fake"):
            self.fake = FakeAdapter(script=dict(self.script))
        self.data_dir = data_dir or tempfile.mkdtemp(prefix="organizer-test-")
        self.storage = Storage(self.data_dir)
        self.storage.init()
        migrate(self.storage.db_path)
        self.server = make_server(self.storage, "127.0.0.1", 0, ai_fake=self.fake)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        from helpers import Api
        self.api = Api(f"http://127.0.0.1:{self.port}")

    def setUp(self):
        super().setUp()
        self.doc = self.import_brief()
        self.listing = self.generate(self.doc)

    def enable(self, **extra):
        return self.api.ok("PATCH", "/api/ai/settings", {"enabled": True, "provider": "fake", **extra})

    def board_count(self, pid=None):
        return self.api.ok("GET", f"/api/projects/{pid or self.pid}")["task_count"]

    def cand_state(self):
        return {c["id"]: (c["title"], c["description"], c["state"], c["parent_id"], c["category"], c["impact"], tuple(c["disciplines"])) for c in
                self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"]}

    def start_run(self, titles, *, wait=True, expect="succeeded", **sel):
        ids = [self.by_title(self.listing, t)["id"] for t in titles]
        status, run = self.api.post(f"/api/documents/{self.doc}/ai/runs", {"candidate_ids": ids, **sel})
        self.assertEqual(status, 202, run)
        return self.finish(run["id"], expect) if wait else run

    def finish(self, run_id, expect="succeeded", timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            run = self.api.ok("GET", f"/api/ai/runs/{run_id}")
            if run["state"] not in ("queued", "running"):
                if expect:
                    self.assertEqual(run["state"], expect, run)
                return run
            time.sleep(0.05)
        self.fail("run did not finish")

    def importing(self, run_id, body=None, confirm=True):
        return self.api.ok("POST", f"/api/ai/runs/{run_id}/import", dict(body or {}, confirm=confirm))


class PreviewTests(AiCase):
    """AI-001: the selected payload is exactly what is sent, and nothing else."""

    def preview(self, titles, **sel):
        ids = [self.by_title(self.listing, t)["id"] for t in titles]
        return self.api.ok("POST", f"/api/documents/{self.doc}/ai/preview", {"candidate_ids": ids, **sel})

    def test_the_preview_contains_only_the_selected_system(self):
        p = self.preview(["Wood shave"])
        texts = " ".join(b["text"] for b in p["blocks"])
        self.assertIn("Shave clean wood", texts)
        self.assertIn("saw machine", texts)
        for other in ("The fence fails", "Enemies spawn", "rope", "Saw machine:"):     # other systems, historical section, unrelated values
            self.assertNotIn(other, texts)
        self.assertEqual([s["title"] for s in p["systems"]], ["Wood shave"])
        self.assertIn("001", {s["id"] for s in p["scenarios"]})
        self.assertIn("File paths", p["what_is_not_sent"])

    def test_the_request_uses_temporary_ids_and_leaks_no_paths(self):
        p = self.preview(["Wood shave"])
        blob = json.dumps(p["request"])
        real_ids = {b["block_id"] for b in p["blocks"]}
        self.assertFalse(any(r in blob for r in real_ids))
        self.assertTrue(all(re.fullmatch(r"b\d+", b["id"]) for b in p["request"]["source_blocks"]))
        for needle in (self.data_dir, self.work, "/Users/", "/private/", "file://"):
            self.assertNotIn(needle, blob)
        self.assertEqual(p["request"]["request_id"], "(assigned when it is sent)")
        self.assertTrue(all(m["visual_content_included"] is False for m in p["request"]["reference_manifest"]))

    def test_removing_a_block_removes_it_from_the_request(self):
        p = self.preview(["Wood shave"])
        victim = next(b for b in p["blocks"] if "saw machine" in b["text"])
        q = self.preview(["Wood shave"], exclude_block_ids=[victim["block_id"]])
        self.assertEqual(len(q["blocks"]), len(p["blocks"]) - 1)
        self.assertNotIn(victim["text"], json.dumps(q["request"]))

    def test_limits_are_reported_before_anything_is_sent(self):
        self.api.ok("PATCH", "/api/ai/settings", {"max_blocks": 2})
        p = self.preview(["Wood shave"])
        self.assertTrue(any("limit" in x for x in p["problems"]), p["problems"])
        self.enable()
        ids = [self.by_title(self.listing, "Wood shave")["id"]]
        status, err = self.api.post(f"/api/documents/{self.doc}/ai/runs", {"candidate_ids": ids})
        self.assertEqual(status, 400, err)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.api.ok("GET", f"/api/documents/{self.doc}/ai/runs")["runs"], [])

    def test_a_batch_is_capped_and_a_child_is_covered_by_its_parent(self):
        self.api.ok("PATCH", "/api/ai/settings", {"batch_size": 1})
        p = self.preview(["Wood shave", "Spawning"])
        self.assertTrue(any("batch" in x for x in p["problems"]))
        p = self.preview(["Wood shave", "Shave clean wood"])
        self.assertEqual([s["title"] for s in p["systems"]], ["Wood shave"])
        self.assertTrue(p["notes"])

    def test_only_a_task_waiting_for_review_can_be_selected(self):
        line = next(c for c in self.listing["candidates"] if c["kind"] == "requirement")
        status, _ = self.api.post(f"/api/documents/{self.doc}/ai/preview", {"candidate_ids": [line["id"]]})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/documents/{self.doc}/ai/preview", {"candidate_ids": []})
        self.assertEqual(status, 400)

    def test_known_open_questions_travel_with_the_request(self):
        fence = self.by_title(self.listing, "Build fence")
        self.api.ok("POST", f"/api/candidates/{fence['id']}/clarifications", {"text": "Does the animation play for every scenario?"})
        p = self.preview(["Build fence"])
        self.assertEqual([q["text"] for q in p["request"]["known_questions"]], ["Does the animation play for every scenario?"])


class SettingsAndSwitchTests(AiCase):
    def test_ai_is_off_by_default_and_the_offline_workflow_is_unaffected(self):
        s = self.api.ok("GET", "/api/ai/settings")
        self.assertEqual((s["enabled"], s["provider"], s["binary"]), (False, "codex_cli", "codex"))
        ids = [self.by_title(self.listing, "Wood shave")["id"]]
        status, err = self.api.post(f"/api/documents/{self.doc}/ai/runs", {"candidate_ids": ids})
        self.assertEqual((status, err["error"]["code"]), (409, "ai_disabled"))
        out = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": ids})     # approval needs no AI
        self.assertEqual(len(out["tasks"]), 3)

    def test_settings_are_validated(self):
        for bad in ({"enabled": "yes"}, {"timeout_seconds": 3}, {"max_blocks": 0}, {"binary": ""}, {"model": "-evil"}, {"model": "two words"}, {"provider": "other"}, {"batch_size": 99}):
            status, _ = self.api.patch("/api/ai/settings", bad)
            self.assertEqual(status, 400, bad)
        s = self.api.ok("PATCH", "/api/ai/settings", {"model": "some-model", "timeout_seconds": 90})
        self.assertEqual((s["model"], s["timeout_seconds"]), ("some-model", 90))

    def test_a_missing_codex_tool_fails_visibly_and_changes_nothing(self):
        self.api.ok("PATCH", "/api/ai/settings", {"enabled": True, "provider": "codex_cli", "binary": "/definitely/not/codex"})
        check = self.api.ok("POST", "/api/ai/check")
        self.assertFalse(check["available"])
        before = self.cand_state()
        run = self.start_run(["Wood shave"], expect="failed")
        self.assertEqual(run["error_code"], "cli_missing")
        self.assertEqual(self.cand_state(), before)

    def test_no_secret_or_credential_field_exists_in_the_stored_settings(self):
        self.api.ok("PATCH", "/api/ai/settings", {"enabled": True})
        conn = sqlite3.connect(self.storage.db_path)
        stored = conn.execute("SELECT value FROM app_setting WHERE key = 'ai'").fetchone()[0]
        conn.close()
        self.assertFalse(re.search(r"key|token|secret|password", stored, re.I), stored)


class RunAndValidationTests(AiCase):
    """AI-003 and AI-004: background runs, and a response is rejected whole when it fails the checks."""

    def setUp(self):
        super().setUp()
        self.enable()
        self.fake.script = dict(WOOD_SCRIPT)

    def test_a_good_run_records_provenance_and_versions(self):
        run = self.start_run(["Wood shave"])
        self.assertEqual((run["provider"], run["task_drafts"], run["subtask_drafts"]), ("fake", 1, 3))
        self.assertEqual(run["reported_model"], "fake-model")
        v = self.api.ok("GET", f"/api/ai/runs/{run['id']}")
        self.assertEqual(v["request_format"], 1)
        self.assertRegex(v["prompt_version"], r"^[0-9a-f]{12}$")
        self.assertRegex(v["schema_version"], r"^[0-9a-f]{12}$")
        self.assertEqual(len(v["input_hash"]), 64)
        self.assertEqual(v["request"]["request_id"], json.loads(v["output_text"])["request_id"])
        self.assertEqual(self.board_count(), 0)

    def assert_rejected(self, mode, code="invalid_output", fragment=None):
        self.fake.script = dict(WOOD_SCRIPT, mode=mode)
        before = self.cand_state()
        run = self.start_run(["Wood shave"], expect="failed")
        self.assertEqual(run["error_code"], code, run)
        self.assertEqual(run["task_drafts"], 0)
        if fragment:
            self.assertTrue(any(fragment in i["text"] for i in run["issues"]), run["issues"])
        self.assertEqual(self.cand_state(), before)
        self.assertEqual(self.board_count(), 0)
        status, err = self.api.post(f"/api/ai/runs/{run['id']}/import", {"confirm": True})
        self.assertEqual(status, 409, err)
        return run

    def test_unknown_ids_are_rejected(self):
        self.assert_rejected("unknown_ids", fragment="b999")
        self.assert_rejected("unknown_reference", fragment="r999")
        self.assert_rejected("unknown_scenario", fragment="777")

    def test_malformed_empty_and_foreign_answers_are_rejected(self):
        self.assert_rejected("invalid_json", fragment="not valid JSON")
        self.assert_rejected("empty", code="empty_output")
        self.assert_rejected("wrong_request_id", fragment="different request")

    def test_shape_enum_and_duplicate_violations_are_rejected(self):
        self.assert_rejected("bad_enum", fragment="category")
        self.assert_rejected("extra_field", fragment="unexpected field priority")
        self.assert_rejected("no_evidence", fragment="cites no source block")
        self.fake.script = dict(WOOD_SCRIPT, mode="duplicate_ids")
        self.assert_rejected("duplicate_ids", fragment="used more than once")

    def test_an_invented_quantity_is_flagged_and_its_description_is_not_applied_by_default(self):
        self.fake.script = dict(WOOD_SCRIPT, mode="invented_value")
        run = self.start_run(["Wood shave"])
        flagged = [i for i in run["issues"] if i["code"] == "unsupported_value"]
        self.assertEqual(len(flagged), 1)
        self.assertIn("4242", flagged[0]["text"])
        plan = self.importing(run["id"], confirm=False)["plan"][0]
        self.assertTrue(plan["description"]["flagged"])
        self.assertFalse(plan["description"]["apply"])

    def test_the_inferred_basis_is_surfaced_as_information(self):
        run = self.start_run(["Wood shave"])
        detail = self.api.ok("GET", f"/api/ai/runs/{run['id']}")
        self.assertTrue(any(i["code"] == "inferred" for i in detail["issues"]))

    def test_provider_failures_are_shown_and_leave_the_project_unchanged(self):
        for mode, code in (("auth", "not_signed_in"), ("usage_limit", "usage_limit"), ("network", "network"), ("error", "adapter_error")):
            self.fake.script = dict(WOOD_SCRIPT, mode=mode)
            before = self.cand_state()
            run = self.start_run(["Wood shave"], expect="failed")
            self.assertEqual(run["error_code"], code)
            self.assertTrue(run["error_message"])
            self.assertEqual(self.cand_state(), before)
        self.fake.script = dict(WOOD_SCRIPT)
        self.assertEqual(self.start_run(["Wood shave"])["state"], "succeeded")   # and a later try works

    def test_a_timeout_ends_the_run(self):
        conn = sqlite3.connect(self.storage.db_path)
        conn.execute("UPDATE app_setting SET value = json_set(value, '$.timeout_seconds', 1) WHERE key = 'ai'")
        conn.commit()
        conn.close()
        self.fake.script = dict(WOOD_SCRIPT, mode="hang")
        run = self.start_run(["Wood shave"], expect="failed")
        self.assertEqual(run["error_code"], "timeout")

    def test_cancelling_a_slow_run_discards_its_answer(self):
        self.fake.script = dict(WOOD_SCRIPT, delay=30)
        run = self.start_run(["Wood shave"], wait=False)
        status, err = self.api.post(f"/api/documents/{self.doc}/ai/runs", {"candidate_ids": [self.by_title(self.listing, "Spawning")["id"]]})
        self.assertEqual((status, err["error"]["code"]), (409, "ai_busy"))
        self.api.ok("POST", f"/api/ai/runs/{run['id']}/cancel")
        done = self.finish(run["id"], "cancelled")
        self.assertEqual((done["error_code"], done["task_drafts"]), ("cancelled", 0))
        self.fake.script = dict(WOOD_SCRIPT)
        self.assertEqual(self.start_run(["Wood shave"])["state"], "succeeded")

    def test_a_run_interrupted_by_a_restart_is_marked_not_left_waiting(self):
        self.fake.script = dict(WOOD_SCRIPT, delay=30)
        run = self.start_run(["Wood shave"], wait=False)
        conn = sqlite3.connect(self.storage.db_path)
        conn.execute("UPDATE ai_run SET state = 'running' WHERE id = ?", (run["id"],))
        conn.commit()
        conn.close()
        self.fake.script = dict(WOOD_SCRIPT)
        self.restart()
        again = self.api.ok("GET", f"/api/ai/runs/{run['id']}")
        self.assertEqual((again["state"], again["error_code"]), ("failed", "interrupted"))

    def test_a_late_answer_cannot_change_a_run_that_already_ended(self):
        run = self.start_run(["Wood shave"])
        conn = sqlite3.connect(self.storage.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_draft WHERE run_id = ?", (run["id"],)).fetchone()[0], 4)
        conn.close()
        from organizer.ai.manager import AiManager
        from organizer.db import connect
        c = connect(self.storage.db_path)
        AiManager._finish(c, run["id"], "failed", code="adapter_error", message="late")
        c.close()
        self.assertEqual(self.api.ok("GET", f"/api/ai/runs/{run['id']}")["state"], "succeeded")


class ImportTests(AiCase):
    """AI-004 and AI-005: selected drafts become needs-review candidates; nothing is approved and nothing is overwritten."""

    def setUp(self):
        super().setUp()
        self.enable()
        self.shave = self.by_title(self.listing, "Wood shave")
        # the section 3 values are attached by a person first, so their wording is part of the selected source
        per = self.by_title(self.listing, "Wood logs per shave:")
        self.api.ok("PATCH", f"/api/candidates/{per['id']}", {"parent_id": self.shave["id"]})
        self.fake.script = json.loads(json.dumps(WOOD_SCRIPT))

    def run_and_import(self, **body):
        run = self.start_run(["Wood shave"])
        return run, self.importing(run["id"], body)

    def test_preview_writes_nothing_then_import_adds_candidates_only(self):
        run = self.start_run(["Wood shave"])
        before = self.cand_state()
        pre = self.importing(run["id"], confirm=False)
        self.assertTrue(pre["preview"])
        self.assertEqual(self.cand_state(), before)
        plan = pre["plan"][0]
        self.assertEqual((plan["target"]["mode"], plan["target"]["candidate_id"]), ("existing", self.shave["id"]))
        self.assertTrue(plan["description"]["apply"])
        self.assertEqual(len(pre["questions"]), 1)
        out = self.importing(run["id"])
        self.assertEqual(self.board_count(), 0)
        d = self.detail(self.shave["id"])
        self.assertEqual(d["state"], "needs_review")
        self.assertTrue(d["description"].startswith("Shave a log clean or bad"))
        self.assertEqual(d["description_origin"], "ai")
        self.assertIn("10 wood logs", d["description"])
        self.assertTrue(out["summary"]["descriptions"])

    def test_classification_and_primary_reference_stay_proposed_until_reviewed(self):
        run, out = self.run_and_import()
        d = self.detail(self.shave["id"])
        self.assertEqual((d["category"], d["impact"], d["disciplines"], d["primary"]), ("unassigned", "unassessed", [], None))
        got = {(p["field"], p["value"]) for p in d["ai_proposals"]}
        self.assertEqual(got, {("discipline", "dev"), ("discipline", "art"), ("category", "core_gameplay"), ("impact", "high")})
        self.assertTrue(all(p["state"] == "suggested" for p in d["ai_proposals"]))
        cat = next(p for p in d["ai_proposals"] if p["field"] == "category")
        d = self.api.ok("POST", f"/api/candidates/{self.shave['id']}/ai-proposals/{cat['id']}", {"action": "accept"})
        self.assertEqual(d["category"], "core_gameplay")
        imp = next(p for p in d["ai_proposals"] if p["field"] == "impact")
        d = self.api.ok("POST", f"/api/candidates/{self.shave['id']}/ai-proposals/{imp['id']}", {"action": "reject"})
        self.assertEqual((d["impact"], next(p for p in d["ai_proposals"] if p["field"] == "impact")["state"]), ("unassessed", "rejected"))

    def test_ai_subtasks_match_existing_ones_instead_of_duplicating_and_inferred_ones_are_marked(self):
        before = {c["title"] for c in self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"]}
        run, out = self.run_and_import()
        titles = [c["title"] for c in self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"] if c["kind"] == "task"]
        self.assertEqual(titles.count("Shave clean wood"), 1)
        self.assertEqual(titles.count("Shave bad wood"), 1)
        self.assertEqual([c["title"] for c in out["created"]], ["Configurable output values"])
        new = self.detail(out["created"][0]["id"])
        self.assertEqual((new["origin"], new["state"], new["level"], new["parent_id"]), ("ai", "needs_review", "subtask", self.shave["id"]))
        self.assertEqual((new["ai"]["basis"], new["ai"]["provider"]), ("inferred", "fake"))
        self.assertEqual(new["sources"], [])
        clean = self.detail(self.by_title(self.listing, "Shave clean wood")["id"])
        self.assertEqual(clean["description"], "Shave the log from start to end.")
        self.assertEqual(clean["origin"], "generated")                                               # the deterministic candidate keeps its own label
        self.assertEqual(self.board_count(), 0)
        _ = before

    def test_import_twice_and_a_retry_never_duplicate(self):
        run, first = self.run_and_import()
        n = len(self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"])
        again = self.importing(run["id"])
        self.assertEqual(again["created"], [])
        self.assertTrue(again["skipped"])
        self.assertEqual(len(self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"]), n)
        status, retry = self.api.post(f"/api/documents/{self.doc}/ai/runs", {"candidate_ids": [self.shave["id"]], "retry_of": run["id"]})
        self.assertEqual(status, 202)
        retry = self.finish(retry["id"])
        self.assertEqual(retry["parent_run_id"], run["id"])
        out = self.importing(retry["id"])
        self.assertEqual(out["created"], [])                                                          # same title and sources: nothing new
        self.assertEqual(len(self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"]), n)
        self.assertEqual(self.board_count(), 0)

    def test_a_manual_description_is_kept_unless_the_person_chooses_to_replace_it(self):
        self.api.ok("PATCH", f"/api/candidates/{self.shave['id']}", {"description": "My own words."})
        run = self.start_run(["Wood shave"])
        plan = self.importing(run["id"], confirm=False)["plan"][0]
        self.assertFalse(plan["description"]["apply"])
        self.importing(run["id"])
        self.assertEqual(self.detail(self.shave["id"])["description"], "My own words.")
        retry = self.finish(self.api.ok("POST", f"/api/documents/{self.doc}/ai/runs", {"candidate_ids": [self.shave["id"]], "retry_of": run["id"]}, expect=(202,))["id"])
        self.importing(retry["id"], {"drafts": [{"draft_id": "d1", "description": True}]})
        d = self.detail(self.shave["id"])
        self.assertTrue(d["description"].startswith("Shave a log clean"))
        prev = next(v for v in d["description_versions"] if v["description"] == "My own words.")
        d = self.api.ok("POST", f"/api/candidates/{self.shave['id']}/descriptions/{prev['id']}/restore")
        self.assertEqual((d["description"], d["description_origin"]), ("My own words.", "user"))

    def test_a_chosen_draft_can_be_imported_without_its_subtasks(self):
        run = self.start_run(["Wood shave"])
        out = self.importing(run["id"], {"drafts": [{"draft_id": "d1", "subtasks": []}], "questions": []})
        self.assertEqual((out["created"], out["questions"]), ([], 0))
        self.assertEqual(self.detail(self.shave["id"])["description_origin"], "ai")

    def test_a_draft_with_no_matching_candidate_becomes_a_new_task_candidate(self):
        self.fake.script = {"tasks": [{"title": "Brand new system", "description": "Do the new thing.", "cite_containing": ["Enemies spawn"]}]}
        run = self.start_run(["Wood shave"])      # the draft cites text that is not in the selection's own blocks
        self.fake.script = json.loads(json.dumps(WOOD_SCRIPT))
        d = run["task_drafts"]
        self.assertEqual(d, 1)
        plan = self.importing(run["id"], {"drafts": [{"draft_id": "d1", "target": "new"}]}, confirm=False)["plan"][0]
        self.assertEqual(plan["target"]["mode"], "new")
        out = self.importing(run["id"], {"drafts": [{"draft_id": "d1", "target": "new"}]})
        new = self.detail(out["created"][0]["id"])
        self.assertEqual((new["kind"], new["level"], new["parent_id"], new["origin"], new["state"]), ("task", "task", None, "ai", "needs_review"))
        self.assertTrue(new["sources"])
        again = self.importing(run["id"], {"drafts": [{"draft_id": "d1", "target": "new"}]})
        self.assertEqual(again["created"], [])

    def test_questions_stay_open_on_the_candidate_and_reach_the_board(self):
        run, out = self.run_and_import()
        self.assertEqual(out["questions"], 1)
        d = self.detail(self.shave["id"])
        qs = [c for c in d["clarifications"] if c["origin"] == "ai"]
        self.assertEqual([(c["state"], c["blocks"] != []) for c in qs], [("open", True)])
        again = self.importing(run["id"])
        self.assertEqual(again["questions"], 0)
        approved = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": [self.shave["id"]]})
        ev = self.api.ok("GET", f"/api/tasks/{approved['tasks'][0]['id']}/evidence")
        self.assertTrue(any(c["origin"] == "ai" and c["state"] == "open" for c in ev["clarifications"]))

    def test_a_task_cannot_be_imported_into_the_wrong_project(self):
        run = self.start_run(["Wood shave"])
        other = self.project("Other", "OTH")
        status, err = self.api.post(f"/api/ai/runs/{run['id']}/import", {"confirm": True, "project_id": other["id"]})
        self.assertEqual((status, err["error"]["code"]), (409, "wrong_project"))
        foreign = self.import_into(other["id"])
        status, err = self.api.post(f"/api/ai/runs/{run['id']}/import", {"confirm": True, "drafts": [{"draft_id": "d1", "target": foreign}]})
        self.assertEqual(status, 400, err)
        self.assertEqual(self.board_count(other["id"]), 0)

    def import_into(self, pid):
        path, _ = make_export(self.work, name="Elsewhere", html=BRIEF.replace("Mini brief", "Elsewhere brief").replace("Spawning", "Spawning elsewhere"), files={}, with_hash=False)
        doc = self.run_import(path, pid=pid)["document_id"]
        listing = self.api.ok("POST", f"/api/documents/{doc}/candidates/generate", {})
        return next(c["id"] for c in listing["candidates"] if c["title"] == "Wood shave")

    def test_runs_stay_with_their_own_project_when_the_user_switches(self):
        run = self.start_run(["Wood shave"])
        other = self.project("Other", "OTH")
        other_doc_run = self.api.ok("GET", f"/api/documents/{self.import_doc(other['id'])}/ai/runs")["runs"]
        self.assertEqual(other_doc_run, [])
        mine = self.api.ok("GET", f"/api/documents/{self.doc}/ai/runs")["runs"]
        self.assertEqual([r["id"] for r in mine], [run["id"]])
        self.assertEqual(mine[0]["project_id"], self.pid)

    def import_doc(self, pid):
        self.import_into(pid)
        return self.api.ok("GET", f"/api/projects/{pid}/documents")["documents"][0]["id"]

    def test_imported_work_survives_restart(self):
        run, out = self.run_and_import()
        self.restart()
        d = self.detail(self.shave["id"])
        self.assertEqual((d["description_origin"], len(d["ai_proposals"]), len(d["clarifications"])), ("ai", 4, 1))
        again = self.api.ok("GET", f"/api/ai/runs/{run['id']}")
        self.assertEqual((again["imported"], again["state"]), (4, "succeeded"))

    def test_a_failed_import_leaves_everything_as_it_was(self):
        run = self.start_run(["Wood shave"])
        before = self.cand_state()
        conn = sqlite3.connect(self.storage.db_path)
        conn.execute("CREATE TRIGGER boom BEFORE INSERT ON ai_proposal BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        conn.commit()
        conn.close()
        status, _ = self.api.post(f"/api/ai/runs/{run['id']}/import", {"confirm": True})
        self.assertGreaterEqual(status, 400)
        self.assertEqual(self.cand_state(), before)
        d = self.detail(self.shave["id"])
        self.assertEqual((d["description_origin"], d["clarifications"]), ("user", []))
        conn = sqlite3.connect(self.storage.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_draft WHERE state = 'imported'").fetchone()[0], 0)
        conn.close()


class LogHammeringTests(CandidateCase):
    """The first acceptance demonstration: filtered text, a short drafted description, valid evidence, and a person-chosen clip."""

    script = {}
    start = AiCase.start
    enable = AiCase.enable
    finish = AiCase.finish
    importing = AiCase.importing

    def test_log_hammering_end_to_end(self):
        path, _ = make_export(self.work, name="Clips", html=MEDIA_BRIEF, files={"hammer.mp4": MP4, "spin.gif": GIF, "model.png": PNG, "shave.mp4": MP4 + b"s"}, with_hash=False)
        doc = self.run_import(path)["document_id"]
        listing = self.generate(doc)
        hammer = self.by_title(listing, "Log hammering")
        self.enable()
        self.fake.script = {"tasks": [{"match_label": "Log hammering", "title": "Implement log hammering", "description": "Looping hammer motion, impact feedback, then the log drops into the waterfall.",
                                       "disciplines": ["dev"], "category": "core_gameplay", "impact": "high", "classification_reason": "Core mechanic.", "cite": "all",
                                       "primary_reference_containing": "hammer"}],
                            "questions": [{"text": "Which log is selected when the sources disagree?", "cite_containing": ["waterfall"]}]}
        status, run = self.api.post(f"/api/documents/{doc}/ai/runs", {"candidate_ids": [hammer["id"]]})
        run = self.finish(run["id"])
        req = self.api.ok("GET", f"/api/ai/runs/{run['id']}")["request"]
        self.assertNotIn("rope", json.dumps(req))
        self.assertIn("hammer", " ".join(m["caption"].lower() for m in req["reference_manifest"]))
        self.importing(run["id"])
        d = self.detail(hammer["id"])
        self.assertIsNone(d["primary"])                                              # proposed, not chosen
        prop = next(p for p in d["ai_proposals"] if p["field"] == "primary_reference")
        self.assertEqual(prop["state"], "suggested")
        d = self.api.ok("POST", f"/api/candidates/{hammer['id']}/ai-proposals/{prop['id']}", {"action": "accept"})
        self.assertEqual(d["primary"]["reference_id"], prop["value"])               # a person's click makes it the main clip
        self.assertEqual(d["primary"]["asset"]["name"], "hammer.mp4")
        out = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": [hammer["id"]]})
        task = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}")
        self.assertTrue(task["description"].startswith("Looping hammer motion"))
        ev = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}/evidence")
        self.assertEqual(ev["primary"]["asset"]["name"], "hammer.mp4")
        self.assertTrue(ev["sources"])
        self.assertTrue(any(not c["state"] != "open" for c in ev["clarifications"]))


class ValidatorUnitTests(unittest.TestCase):
    def ctx(self, limit=2):
        c = validate.Context("req1", {"b1": "real-1", "b2": "real-2"}, {"r1": "ref-1"}, ["001", "002"], limit)
        c.block_text = {"b1": "Costs 10 wood logs.", "b2": "Scenario 001"}
        return c

    def good(self, **over):
        t = {"draft_id": "d1", "title": "T", "description": "Costs 10 wood logs.", "disciplines": ["dev"], "category": "core_gameplay", "impact": "high",
             "classification_reason": "why", "source_block_ids": ["b1"], "scenario_ids": ["001"], "suggested_primary_reference_id": "r1",
             "subtasks": [{"draft_id": "d2", "title": "S", "description": "", "basis": "inferred", "source_block_ids": [], "scenario_ids": []}]}
        t.update(over)
        return {"request_id": "req1", "tasks": [t], "questions": [{"text": "q?", "source_block_ids": ["b2"]}]}

    def test_a_good_answer_is_translated_to_real_ids(self):
        clean, issues = validate.validate(self.good(), self.ctx())
        self.assertEqual(clean["tasks"][0]["source_block_ids"], ["real-1"])
        self.assertEqual(clean["tasks"][0]["suggested_primary_reference_id"], "ref-1")
        self.assertEqual(clean["questions"][0]["source_block_ids"], ["real-2"])
        self.assertEqual([i["code"] for i in issues], ["inferred"])

    def test_each_kind_of_violation_is_caught(self):
        cases = {
            "title": self.good(title=""), "enum": self.good(impact="huge"), "disc": self.good(disciplines=["qa"]), "scen": self.good(scenario_ids=["009"]),
            "type": self.good(source_block_ids="b1"), "long": self.good(description="x" * 2500), "extra": dict(self.good(), note="x"),
            "subsrc": self.good(subtasks=[{"draft_id": "d2", "title": "S", "description": "", "basis": "source_supported", "source_block_ids": [], "scenario_ids": []}]),
        }
        for name, obj in cases.items():
            with self.assertRaises(validate.InvalidOutput, msg=name):
                validate.validate(obj, self.ctx())
        two = self.good()
        two["tasks"].append(dict(two["tasks"][0], draft_id="d9"))
        two["tasks"].append(dict(two["tasks"][0], draft_id="d8"))
        with self.assertRaises(validate.InvalidOutput):   # more tasks than the request allowed
            validate.validate(two, self.ctx(limit=2))

    def test_parse_accepts_a_code_fence_but_nothing_else(self):
        self.assertEqual(validate.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        for bad in ("", "  ", "[]", "plain text", '{"a": '):
            with self.assertRaises(validate.InvalidOutput):
                validate.parse_json(bad)

    def test_quantities_missing_from_the_source_are_flagged(self):
        clean, issues = validate.validate(self.good(description="Costs 10 wood logs and 99 coins."), self.ctx())
        flagged = [i for i in issues if i["code"] == "unsupported_value"]
        self.assertEqual(len(flagged), 1)
        self.assertIn("99", flagged[0]["text"])
        self.assertNotIn("10", flagged[0]["text"].split("mentions")[1].split(",")[0].replace("99", ""))

    def test_packaged_assets_match_the_advisor_files_when_present(self):
        for name in ("instructions.txt", "task-draft.schema.json"):
            src = os.path.join(ROOT, "AIWorkFlow", "Architecture", "AIIntegration", name)
            if os.path.exists(src):
                with open(src, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), read_asset(name), f"{name} has drifted from the advisor's copy")

    def test_the_schema_the_cli_receives_describes_the_same_shape_the_validator_enforces(self):
        schema = json.loads(read_asset("task-draft.schema.json"))
        task = schema["properties"]["tasks"]["items"]
        self.assertEqual(set(task["required"]), {"draft_id", "title", "description", "disciplines", "category", "impact", "classification_reason", "source_block_ids",
                                                  "scenario_ids", "suggested_primary_reference_id", "subtasks"})
        self.assertEqual(task["properties"]["category"]["enum"], list(validate.CATEGORIES))
        self.assertEqual(task["properties"]["impact"]["enum"], list(validate.IMPACTS))
        self.assertEqual(task["properties"]["subtasks"]["items"]["properties"]["basis"]["enum"], list(validate.BASIS))


# ---- the Codex CLI adapter, against a stand-in executable -----------------------------------

FAKE_CODEX = r'''#!__PYTHON__
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode.txt")).read().strip() if os.path.exists(os.path.join(here, "mode.txt")) else "ok"
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if args[:2] == ["login", "status"]:
    if mode == "signedout":
        print("Not logged in", file=sys.stderr); sys.exit(1)
    print("Logged in using ChatGPT"); sys.exit(0)
if args[:2] == ["exec", "--help"]:
    flags = ["--output-schema", "--output-last-message", "--sandbox", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--ignore-rules", "--json"]
    if mode == "oldcli":
        flags.remove("--output-schema")
    print("Usage: codex exec\n" + "\n".join(flags)); sys.exit(0)
if args[:2] == ["features", "list"]:
    print("shell_tool stable true\napps stable true\nplugins stable true\nsome_other_feature stable true"); sys.exit(0)
# the real request
stdin = sys.stdin.read()
record = {"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(), "env_keys": sorted(os.environ), "listing": sorted(os.listdir(os.getcwd())), "pid": os.getpid()}
json.dump(record, open(os.path.join(here, "record.json"), "w"))
if mode == "sleep":
    time.sleep(60)
if mode == "usage":
    print("You've hit your usage limit. Try again later.", file=sys.stderr); sys.exit(1)
if mode == "autherr":
    print("401 Unauthorized: please log in again", file=sys.stderr); sys.exit(1)
if mode == "crash":
    print("something exploded", file=sys.stderr); sys.exit(3)
out = args[args.index("--output-last-message") + 1]
if mode == "noanswer":
    sys.exit(0)
req = json.loads(stdin)
ans = {"request_id": req["request_id"], "questions": [], "tasks": [{"draft_id": "d1", "title": "Drafted", "description": "ok", "disciplines": [], "category": "unassigned",
       "impact": "unassessed", "classification_reason": "", "source_block_ids": [req["source_blocks"][0]["id"]], "scenario_ids": [], "suggested_primary_reference_id": None, "subtasks": []}]}
open(out, "w").write(json.dumps(ans))
print(json.dumps({"type": "thread.started", "model": "stand-in-model"}))
'''


class CodexCliAdapterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fake-codex-")
        self.addCleanup(__import__("shutil").rmtree, self.dir, ignore_errors=True)
        self.bin = os.path.join(self.dir, "codex")
        with open(self.bin, "w") as fh:
            fh.write(FAKE_CODEX.replace("__PYTHON__", sys.executable))
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IXUSR)
        self.adapter = CodexCliAdapter(self.bin, "")
        self.request = {"request_id": "r-1", "task_label": "x", "source_blocks": [{"id": "b1", "heading": "", "text": "'; touch PWNED; $(touch PWNED2) `touch PWNED3`"}],
                        "scenario_ids": [], "reference_manifest": [], "known_questions": [], "instructions": {"task_count_limit": 1}}

    def mode(self, m):
        with open(os.path.join(self.dir, "mode.txt"), "w") as fh:
            fh.write(m)

    def run_adapter(self, timeout=20, cancel=None):
        return self.adapter.run(self.request, read_asset("instructions.txt"), read_asset("task-draft.schema.json"), cancel or threading.Event(), timeout)

    def record(self):
        with open(os.path.join(self.dir, "record.json")) as fh:
            return json.load(fh)

    def test_check_reports_version_sign_in_and_restrictions_without_reading_credentials(self):
        info = self.adapter.check()
        self.assertEqual((info["available"], info["version"], info["signed_in"], info["auth_mode"], info["flags_ok"]), (True, "codex-cli 9.9.9", True, "chatgpt", True))
        self.assertIn("shell tool off", info["restrictions"])
        self.assertNotIn("some other feature off", info["restrictions"])
        self.mode("signedout")
        self.assertFalse(self.adapter.check()["signed_in"])
        self.mode("oldcli")
        info = self.adapter.check()
        self.assertEqual((info["flags_ok"], info["missing_flags"]), (False, ["--output-schema"]))

    def test_the_command_is_an_argument_array_with_the_request_on_stdin_only(self):
        res = self.run_adapter()
        self.assertEqual(json.loads(res.text)["request_id"], "r-1")
        self.assertEqual(res.model, "stand-in-model")
        rec = self.record()
        argv = rec["argv"]
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--json"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "never")
        self.assertEqual(argv.index("--ask-for-approval") < argv.index("exec"), True)
        self.assertIn("mcp_servers={}", argv)
        self.assertIn('web_search="disabled"', argv)
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--disable"], ["shell_tool", "apps", "plugins"])
        self.assertEqual(argv[-1], read_asset("instructions.txt"))                    # the instructions are one argument
        self.assertNotIn("PWNED", " ".join(argv))                                      # source text never reaches the command line
        self.assertEqual(json.loads(rec["stdin"])["source_blocks"][0]["text"], self.request["source_blocks"][0]["text"])
        self.assertFalse(os.path.exists("PWNED") or os.path.exists(os.path.join(self.dir, "PWNED")))

    def test_it_runs_in_a_dedicated_temporary_folder_with_a_minimal_environment(self):
        os.environ["OPENAI_API_KEY"] = "sk-should-not-leak"
        self.addCleanup(os.environ.pop, "OPENAI_API_KEY", None)
        self.run_adapter()
        rec = self.record()
        self.assertTrue(os.path.realpath(rec["cwd"]).startswith(os.path.realpath(tempfile.gettempdir())))
        self.assertNotEqual(os.path.realpath(rec["cwd"]), os.path.realpath(ROOT))
        self.assertEqual(sorted(set(rec["listing"]) - {"events.jsonl", "stderr.txt"}), ["request.json", "schema.json"])
        self.assertNotIn("OPENAI_API_KEY", rec["env_keys"])
        self.assertFalse(os.path.exists(rec["cwd"]), "the temporary folder is removed afterwards")

    def test_failures_are_classified(self):
        for mode, code in (("usage", "usage_limit"), ("autherr", "not_signed_in"), ("crash", "adapter_error"), ("noanswer", "empty_output")):
            self.mode(mode)
            with self.assertRaises(AdapterError) as cm:
                self.run_adapter()
            self.assertEqual(cm.exception.code, code, mode)
        self.mode("signedout")
        with self.assertRaises(AdapterError) as cm:
            self.run_adapter()
        self.assertEqual(cm.exception.code, "not_signed_in")
        self.mode("oldcli")
        with self.assertRaises(AdapterError) as cm:
            self.run_adapter()
        self.assertEqual(cm.exception.code, "unsupported_cli")
        with self.assertRaises(AdapterError) as cm:
            CodexCliAdapter("/no/such/codex").run(self.request, "i", "{}", threading.Event(), 5)
        self.assertEqual(cm.exception.code, "cli_missing")

    def test_timeout_terminates_the_child_process(self):
        self.mode("sleep")
        t0 = time.time()
        with self.assertRaises(AdapterError) as cm:
            self.run_adapter(timeout=1.5)
        self.assertEqual(cm.exception.code, "timeout")
        self.assertLess(time.time() - t0, 15)
        self.assertProcessGone(self.record()["pid"])

    def test_cancellation_terminates_the_child_process(self):
        self.mode("sleep")
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        with self.assertRaises(AdapterError) as cm:
            self.run_adapter(timeout=60, cancel=cancel)
        self.assertEqual(cm.exception.code, "cancelled")
        self.assertProcessGone(self.record()["pid"])

    def assertProcessGone(self, pid):
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        self.fail("the child process is still running")

    def test_failure_text_classification(self):
        self.assertEqual(classify_failure("Error: You have reached your usage limit"), "usage_limit")
        self.assertEqual(classify_failure("HTTP 429 Too Many Requests"), "usage_limit")
        self.assertEqual(classify_failure("Not logged in. Run `codex login`"), "not_signed_in")
        self.assertEqual(classify_failure("error sending request for url (could not resolve host)"), "network")
        self.assertEqual(classify_failure("boom"), "adapter_error")


if __name__ == "__main__":
    unittest.main()
