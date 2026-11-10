"""Milestone 3 revision: requirement ownership, atomic hierarchy edits, and the task-first reference model."""

import sqlite3
import unittest

from export_fixture import GIF, MP4, PNG, make_export
from test_candidates import BRIEF, CandidateCase


class OwnershipCase(CandidateCase):
    def setUp(self):
        super().setUp()
        self.doc = self.import_brief()
        self.listing = self.generate(self.doc)

    def approve(self, ids, **kw):
        return self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": ids, **kw})

    def fence_line(self):
        fence = self.by_title(self.listing, "Build fence")
        line = next(c for c in self.kids(self.listing, fence) if c["kind"] == "requirement" and c["title"].startswith("The player builds"))
        return fence, line

    def db(self):
        conn = sqlite3.connect(self.storage.db_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn


class RequirementOwnershipTests(OwnershipCase):
    """M3 REV 001: a requirement approved before its parent must still end up on that parent's ticket."""

    def owned(self, task_id):
        return {r["text"]: r for r in self.api.ok("GET", f"/api/tasks/{task_id}/evidence")["requirements"]}

    def test_child_approved_before_its_parent_is_attached_when_the_parent_is_approved(self):
        fence, line = self.fence_line()
        first = self.approve([line["id"]])
        self.assertEqual(first["tasks"], [])
        self.assertEqual([w["parent_title"] for w in first["awaiting_parent"]], ["Build fence"])   # the person is told who will own it
        req_id = self.detail(line["id"])["id"]
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_requirement").fetchone()[0], 0)
        n_req = conn.execute("SELECT COUNT(*) FROM requirement").fetchone()[0]
        out = self.approve([fence["id"]])
        task_id = out["tasks"][0]["id"]
        self.assertEqual(out["attached_to_parent"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM requirement").fetchone()[0], n_req + len(self.kids(self.listing, fence)) - 1)   # not created twice
        mine = self.owned(task_id)
        self.assertIn(line["original_text"], mine)
        item = mine[line["original_text"]]
        self.assertEqual(item["scenarios"], ["001", "003"])                       # scenario links survive
        self.assertTrue(item["blocks"])                                           # and the exact source block
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM requirement WHERE candidate_id = ?", (line["id"],)).fetchone()[0], 1)
        self.assertEqual(self.detail(line["id"])["state"], "approved")
        _ = req_id

    def test_child_and_parent_together(self):
        fence, line = self.fence_line()
        out = self.approve([fence["id"], line["id"]])
        mine = self.owned(out["tasks"][0]["id"])
        self.assertIn(line["original_text"], mine)
        self.assertEqual(out["awaiting_parent"], [])
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM requirement WHERE candidate_id = ?", (line["id"],)).fetchone()[0], 1)

    def test_child_after_the_parent_is_already_a_ticket(self):
        fence, line = self.fence_line()
        parent = self.approve([fence["id"]])
        again = self.detail(line["id"])
        self.assertEqual(again["state"], "approved")   # approving a task brings its lines; nothing is left to approve afterwards
        self.assertIn(line["original_text"], self.owned(parent["tasks"][0]["id"]))
        # a line that was dismissed first and restored after the parent was approved attaches straight to the ticket
        spawn = self.by_title(self.listing, "Spawning")
        sline = self.kids(self.listing, spawn)[0]
        self.api.ok("POST", "/api/candidates/dismiss", {"ids": [sline["id"]]})
        task = self.approve([spawn["id"]])["tasks"][0]
        self.assertNotIn(sline["original_text"], self.owned(task["id"]))
        self.api.ok("POST", "/api/candidates/restore", {"ids": [sline["id"]]})
        res = self.approve([sline["id"]])
        self.assertEqual(res["awaiting_parent"], [])
        self.assertIn(sline["original_text"], self.owned(task["id"]))

    def test_a_standalone_shared_requirement_has_no_owner_and_says_so(self):
        loose = next(c for c in self.listing["candidates"] if c["kind"] == "requirement" and not c["parent_id"])
        out = self.approve([loose["id"]])
        self.assertEqual((out["tasks"], out["awaiting_parent"], out["attached_to_parent"]), ([], [], 0))
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_requirement").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM requirement WHERE candidate_id = ?", (loose["id"],)).fetchone()[0], 1)

    def test_a_line_under_a_subtask_follows_the_subtask(self):
        shave = self.by_title(self.listing, "Wood shave")
        clean = self.by_title(self.listing, "Shave clean wood")
        line = self.kids(self.listing, clean)[0]
        self.approve([line["id"]])
        out = self.approve([shave["id"]])
        sub = next(t for t in out["tasks"] if t["title"] == "Shave clean wood")
        self.assertIn(line["original_text"], self.owned(sub["id"]))


class HierarchyEditTests(OwnershipCase):
    """M3 REV 002 and 003: parent and level change together, and the children are checked too."""

    def state(self, cid):
        d = self.detail(cid)
        return d["kind"], d["level"], d["parent_id"]

    def test_a_top_level_task_becomes_a_subtask_in_one_operation(self):
        fence = self.by_title(self.listing, "Build fence")
        fail = self.by_title(self.listing, "Fail to defend")
        status, err = self.api.patch(f"/api/candidates/{fail['id']}", {"level": "subtask"})          # the old, separate controls
        self.assertEqual(status, 400, err)
        status, err = self.api.patch(f"/api/candidates/{fail['id']}", {"parent_id": fence["id"]})
        self.assertEqual(status, 400, err)
        self.assertEqual(self.state(fail["id"]), ("task", "task", None))                              # a refusal changes nothing
        d = self.api.ok("PATCH", f"/api/candidates/{fail['id']}", {"level": "subtask", "parent_id": fence["id"]})
        self.assertEqual((d["level"], d["parent_id"]), ("subtask", fence["id"]))
        out = self.approve([fence["id"]])
        self.assertEqual({t["title"] for t in out["tasks"]}, {"Build fence", "Fail to defend"})

    def test_a_subtask_returns_to_the_top_level_and_moves_between_parents(self):
        shave = self.by_title(self.listing, "Wood shave")
        fence = self.by_title(self.listing, "Build fence")
        clean = self.by_title(self.listing, "Shave clean wood")
        d = self.api.ok("PATCH", f"/api/candidates/{clean['id']}", {"level": "subtask", "parent_id": fence["id"]})
        self.assertEqual(d["parent_id"], fence["id"])
        d = self.api.ok("PATCH", f"/api/candidates/{clean['id']}", {"level": "task", "parent_id": None})
        self.assertEqual((d["level"], d["parent_id"]), ("task", None))
        self.assertEqual(self.state(shave["id"])[1], "task")
        status, _ = self.api.patch(f"/api/candidates/{clean['id']}", {"level": "subtask", "parent_id": None})
        self.assertEqual(status, 400)

    def test_demoting_a_parent_with_subtasks_is_refused_and_leaves_everything_unchanged(self):
        shave = self.by_title(self.listing, "Wood shave")
        fence = self.by_title(self.listing, "Build fence")
        before = {c["id"]: (c["kind"], c["level"], c["parent_id"]) for c in self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"]}
        status, err = self.api.patch(f"/api/candidates/{shave['id']}", {"level": "subtask", "parent_id": fence["id"]})
        self.assertEqual(status, 400)
        self.assertIn("Shave clean wood", err["error"]["message"])
        after = {c["id"]: (c["kind"], c["level"], c["parent_id"]) for c in self.api.ok("GET", f"/api/documents/{self.doc}/candidates")["candidates"]}
        self.assertEqual(before, after)
        status, _ = self.api.patch(f"/api/candidates/{shave['id']}", {"level": "subtask"})
        self.assertEqual(status, 400)
        out = self.approve([fence["id"]])                                                             # approval still works
        self.assertEqual(out["tasks"][0]["title"], "Build fence")

    def test_a_parent_with_children_cannot_become_a_note_but_a_valid_edit_keeps_the_tree_valid(self):
        shave = self.by_title(self.listing, "Wood shave")
        status, _ = self.api.patch(f"/api/candidates/{shave['id']}", {"kind": "note"})
        self.assertEqual(status, 400)
        d = self.api.ok("PATCH", f"/api/candidates/{shave['id']}", {"level": "epic"})                 # an epic may contain subtasks
        self.assertEqual(d["level"], "epic")
        out = self.approve([shave["id"]])
        by = {t["title"]: t for t in out["tasks"]}
        board = {t["title"]: t for t in self.api.ok("GET", f"/api/projects/{self.pid}/tasks")["tasks"]}
        self.assertEqual(board["Wood shave"]["level"], "epic")
        self.assertEqual(board["Shave clean wood"]["parent_id"], by["Wood shave"]["id"])

    def test_merging_across_levels_is_refused_and_same_level_merges_stay_valid(self):
        fence = self.by_title(self.listing, "Build fence")
        fail = self.by_title(self.listing, "Fail to defend")
        spawn = self.by_title(self.listing, "Spawning")
        self.api.ok("PATCH", f"/api/candidates/{fail['id']}", {"level": "subtask", "parent_id": fence["id"]})
        status, err = self.api.post("/api/candidates/merge", {"ids": [fail["id"], spawn["id"]], "confirm": True})
        self.assertEqual(status, 400)
        self.assertIn("same level", err["error"]["message"])
        self.assertEqual(self.state(fail["id"]), ("task", "subtask", fence["id"]))
        clean = self.by_title(self.listing, "Shave clean wood")
        bad = self.by_title(self.listing, "Shave bad wood")
        merged = self.api.ok("POST", "/api/candidates/merge", {"ids": [clean["id"], bad["id"]], "confirm": True})
        keep = self.detail(merged["merged_into"])
        self.assertEqual(keep["level"], "subtask")
        out = self.approve([self.by_title(self.listing, "Wood shave")["id"]])
        self.assertEqual(len(out["tasks"]), 2)                                                        # Wood shave + the merged subtask

    def test_merging_a_task_that_has_subtasks_into_another_keeps_the_hierarchy_valid(self):
        shave = self.by_title(self.listing, "Wood shave")
        spawn = self.by_title(self.listing, "Spawning")
        merged = self.api.ok("POST", "/api/candidates/merge", {"ids": [spawn["id"], shave["id"]], "into_id": spawn["id"], "confirm": True})
        into = self.detail(merged["merged_into"])
        self.assertEqual([c["level"] for c in into["children"] if c["kind"] == "task"], ["subtask", "subtask"])
        out = self.approve([into["id"]])
        self.assertEqual(len(out["tasks"]), 3)

    def test_splitting_cannot_move_a_subtask_under_a_new_subtask(self):
        shave = self.by_title(self.listing, "Wood shave")
        clean = self.by_title(self.listing, "Shave clean wood")
        status, err = self.api.post(f"/api/candidates/{shave['id']}/split", {"title": "Broken", "child_ids": [clean["id"]], "mode": "subtask"})
        self.assertEqual(status, 400, err)
        bad = self.by_title(self.listing, "Shave bad wood")
        line = self.kids(self.listing, bad)[0]
        res = self.api.ok("POST", f"/api/candidates/{bad['id']}/split", {"title": "Saw lines", "child_ids": [line["id"]], "mode": "sibling"})
        new = self.detail(res["new_id"])
        self.assertEqual((new["level"], new["parent_id"]), ("subtask", shave["id"]))                  # a sibling of a subtask is a subtask
        status, _ = self.api.post(f"/api/candidates/{bad['id']}/split", {"title": "Deeper", "child_ids": [line["id"]], "mode": "subtask"})
        self.assertEqual(status, 400)


MEDIA_BRIEF = """<html><head><title>Media brief</title></head><body><article><div class="page-body">
<h2>1. Gameplay</h2>
<p class="block-color-yellow_background">Log hammering</p>
<ul><li>The hammer moves in a looping rotation.</li><li>The log falls into the waterfall after the impact.</li></ul>
<figure id="f2" dir="ltr"><div class="source"><a href="Clips/hammer.mp4">attachment:7255cc48:hammer.mp4</a></div></figure>
<figure class="image"><img src="Clips/spin.gif"/></figure>
<figure class="image"><img src="Clips/model.png"/></figure>
<p class="block-color-yellow_background">Wood shave</p>
<ul><li>The shaver removes the bark in one pass.</li></ul>
<figure id="f9" dir="ltr"><div class="source"><a href="Clips/shave.mp4">attachment:aaaaaaaa:shave.mp4</a></div></figure>
</div></article></body></html>"""


class ReferenceRoleTests(CandidateCase):
    def setUp(self):
        super().setUp()
        path, _ = make_export(self.work, name="Clips", html=MEDIA_BRIEF, files={"hammer.mp4": MP4, "spin.gif": GIF, "model.png": PNG, "shave.mp4": MP4 + b"s"}, with_hash=False)
        self.doc = self.run_import(path)["document_id"]
        self.listing = self.generate(self.doc)
        self.hammer = self.by_title(self.listing, "Log hammering")
        self.refs = {r["asset"]["name"]: r for r in self.detail(self.hammer["id"])["references"]}

    def ref(self, name):
        return self.refs[name]["reference_id"]

    def primary(self, cid):
        return self.detail(cid)["primary"]

    def test_suggestions_are_ranked_and_only_a_person_picks_the_primary(self):
        d = self.detail(self.hammer["id"])
        self.assertIsNone(d["primary"])
        self.assertEqual(d["reference_decision"], "undecided")
        self.assertEqual({r["state"] for r in d["references"]}, {"suggested"})
        self.assertLessEqual(len([r for r in d["references"] if r.get("top")]), 3)
        self.assertTrue(all("label" in r and "section" in r for r in d["references"]))
        self.assertTrue(all(r["role"] != "primary_demo" for r in d["references"]))

    def test_choosing_a_primary_attaches_it_and_keeps_exactly_one(self):
        a, b = self.ref("hammer.mp4"), self.ref("spin.gif")
        d = self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": a})
        self.assertEqual((d["primary"]["reference_id"], d["primary"]["state"]), (a, "attached"))
        d = self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": b})
        self.assertEqual(d["primary"]["reference_id"], b)
        roles = {r["reference_id"]: r["role"] for r in d["references"] if r["state"] == "attached"}
        self.assertEqual(roles, {a: "supporting_demo", b: "primary_demo"})
        # clearing keeps the file attached
        d = self.api.ok("DELETE", f"/api/candidates/{self.hammer['id']}/primary-reference")
        self.assertIsNone(d["primary"])
        self.assertEqual({r["reference_id"] for r in d["references"] if r["state"] == "attached"}, {a, b})

    def test_a_reference_from_another_document_cannot_be_primary(self):
        other = self.run_import(make_export(self.work, name="Clips2", html=MEDIA_BRIEF.replace("Clips/", "Clips2/"), files={"hammer.mp4": MP4, "spin.gif": GIF, "model.png": PNG, "shave.mp4": MP4}, with_hash=False)[0])["document_id"]
        foreign = next(r for r in self.detail(self.by_title(self.generate(other), "Log hammering")["id"])["references"])
        status, err = self.api.post(f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": foreign["reference_id"]})
        self.assertEqual(status, 400, err)
        self.assertIsNone(self.primary(self.hammer["id"]))

    def test_the_database_itself_refuses_two_primaries(self):
        a, b = self.ref("hammer.mp4"), self.ref("spin.gif")
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": a})
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/references", {"reference_id": b, "state": "attached"})
        conn = sqlite3.connect(self.storage.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE candidate_reference SET role = 'primary_demo' WHERE candidate_id = ? AND reference_id = ?", (self.hammer["id"], b))
        with self.assertRaises(sqlite3.IntegrityError):    # and a primary must be attached
            conn.execute("UPDATE candidate_reference SET state = 'removed' WHERE candidate_id = ? AND reference_id = ?", (self.hammer["id"], a))
        conn.close()

    def test_removing_the_primary_demotes_it_instead_of_failing(self):
        a = self.ref("hammer.mp4")
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": a})
        d = self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/references", {"reference_id": a, "state": "removed"})
        self.assertIsNone(d["primary"])

    def test_no_reference_needed_is_an_explicit_decision_and_clears_when_a_clip_is_chosen(self):
        d = self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/reference-decision", {"decision": "none"})
        self.assertEqual(d["reference_decision"], "none")
        d = self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": self.ref("hammer.mp4")})
        self.assertEqual(d["reference_decision"], "undecided")
        status, _ = self.api.post(f"/api/candidates/{self.hammer['id']}/reference-decision", {"decision": "none"})
        self.assertEqual(status, 400)

    def test_playback_range_is_validated_stored_and_survives_approval_and_restart(self):
        a = self.ref("hammer.mp4")
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": a})
        url = f"/api/candidates/{self.hammer['id']}/references/{a}"
        for bad in ({"start_ms": 5000, "end_ms": 5000}, {"start_ms": -1}, {"start_ms": 1.5}, {"end_ms": 4000}, {"start_ms": True}):
            status, _ = self.api.patch(url, bad)
            self.assertEqual(status, 400, bad)
        status, _ = self.api.patch(f"/api/candidates/{self.hammer['id']}/references/{self.ref('spin.gif')}", {"start_ms": 0})
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/references", {"reference_id": self.ref("spin.gif"), "state": "attached"})
        status, _ = self.api.patch(f"/api/candidates/{self.hammer['id']}/references/{self.ref('spin.gif')}", {"start_ms": 0})
        self.assertEqual(status, 400)                                                                  # ranges apply to video only
        d = self.api.ok("PATCH", url, {"start_ms": 1500, "end_ms": 4200, "caption": "Hammer motion and log impact", "role": "primary_demo"})
        self.assertEqual((d["primary"]["start_ms"], d["primary"]["end_ms"], d["primary"]["label"]), (1500, 4200, "Hammer motion and log impact"))
        d = self.api.ok("PATCH", url, {"end_ms": None})
        self.assertEqual((d["primary"]["start_ms"], d["primary"]["end_ms"]), (1500, None))
        self.api.ok("PATCH", url, {"end_ms": 4200})
        self.api.ok("PATCH", f"/api/candidates/{self.hammer['id']}", {"description": "Looping hammer motion, then the impact."})
        out = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": [self.hammer["id"]]})
        tid = out["tasks"][0]["id"]
        for _ in range(2):                                                                             # and again after a restart
            ev = self.api.ok("GET", f"/api/tasks/{tid}/evidence")
            self.assertEqual((ev["primary"]["reference_id"], ev["primary"]["start_ms"], ev["primary"]["end_ms"], ev["primary"]["role"]), (a, 1500, 4200, "primary_demo"))
            self.assertEqual(ev["primary"]["label"], "Hammer motion and log impact")
            self.assertEqual(ev["primary"]["asset"]["name"], "hammer.mp4")                              # the original filename stays available
            self.restart()
            self.pid = self.pid
        task = self.api.ok("GET", f"/api/tasks/{tid}")
        self.assertEqual(task["description"], "Looping hammer motion, then the impact.")

    def test_board_side_changes_keep_one_primary_and_never_delete_the_file(self):
        a, b = self.ref("hammer.mp4"), self.ref("spin.gif")
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": a})
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/references", {"reference_id": b, "state": "attached"})
        tid = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": [self.hammer["id"]]})["tasks"][0]["id"]
        ev = self.api.ok("POST", f"/api/tasks/{tid}/primary-reference", {"reference_id": b})
        self.assertEqual(ev["primary"]["reference_id"], b)
        self.assertEqual(sorted(r["role"] for r in ev["references"]), ["primary_demo", "supporting_demo"])
        ev = self.api.ok("DELETE", f"/api/tasks/{tid}/primary-reference")
        self.assertIsNone(ev["primary"])
        self.assertEqual(len(ev["references"]), 2)
        ev = self.api.ok("POST", f"/api/tasks/{tid}/reference-decision", {"decision": "none"})
        self.assertEqual(ev["reference_decision"], "none")
        ev = self.api.ok("PATCH", f"/api/tasks/{tid}/references/{a}", {"start_ms": 1000, "end_ms": 2000})
        self.assertEqual(next(r for r in ev["references"] if r["reference_id"] == a)["end_ms"], 2000)

    def test_merging_keeps_a_single_primary(self):
        wood = self.by_title(self.listing, "Wood shave")
        self.api.ok("POST", f"/api/candidates/{self.hammer['id']}/primary-reference", {"reference_id": self.ref("hammer.mp4")})
        wref = next(r for r in self.detail(wood["id"])["references"])
        self.api.ok("POST", f"/api/candidates/{wood['id']}/primary-reference", {"reference_id": wref["reference_id"]})
        merged = self.api.ok("POST", "/api/candidates/merge", {"ids": [self.hammer["id"], wood["id"]], "into_id": self.hammer["id"], "confirm": True})
        d = self.detail(merged["merged_into"])
        self.assertEqual(sum(1 for r in d["references"] if r["role"] == "primary_demo"), 1)
        self.assertEqual(d["primary"]["reference_id"], self.ref("hammer.mp4"))


class ClarificationTests(OwnershipCase):
    def test_a_question_is_kept_visible_on_the_task_through_approval_and_until_resolved(self):
        fence, line = self.fence_line()
        block = next(s["block_id"] for s in self.detail(line["id"])["sources"] if s["role"] == "evidence")
        d = self.api.ok("POST", f"/api/candidates/{fence['id']}/clarifications", {"text": "Which log is selected when the sources disagree?", "source_block_ids": [block]})
        self.assertEqual([c["text"] for c in d["clarifications"]], ["Which log is selected when the sources disagree?"])
        self.assertEqual(self.detail(fence["id"])["open_questions"], 0 + 1)
        listing = self.api.ok("GET", f"/api/documents/{self.doc}/candidates")
        self.assertEqual(next(c for c in listing["candidates"] if c["id"] == fence["id"])["open_questions"], 1)
        self.api.ok("PATCH", f"/api/candidates/{fence['id']}", {"description": "Build the fence."})        # editing the summary keeps the question
        self.assertEqual(len(self.detail(fence["id"])["clarifications"]), 1)
        out = self.approve([fence["id"]])
        ev = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}/evidence")
        self.assertEqual([(c["state"], c["blocks"][0]["block_id"]) for c in ev["clarifications"]], [("open", block)])
        cid = ev["clarifications"][0]["id"]
        res = self.api.ok("PATCH", f"/api/clarifications/{cid}", {"state": "resolved", "resolution": "Use the clean log."})
        self.assertEqual(res["clarifications"][0]["state"], "resolved")
        ev = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}/evidence")
        self.assertEqual(ev["clarifications"][0]["resolution"], "Use the clean log.")

    def test_a_question_cannot_cite_a_block_from_another_document(self):
        other = self.import_brief(html=BRIEF.replace("Mini brief", "Another brief").replace("Spawning", "Spawning two"), name="Other")
        blk = self.api.ok("GET", f"/api/documents/{other}/blocks?limit=3")["blocks"][0]["id"]
        fence, _ = self.fence_line()
        status, _ = self.api.post(f"/api/candidates/{fence['id']}/clarifications", {"text": "?", "source_block_ids": [blk]})
        self.assertEqual(status, 400)

    def test_description_history_allows_recovery(self):
        fence, _ = self.fence_line()
        self.api.ok("PATCH", f"/api/candidates/{fence['id']}", {"description": "First version"})
        self.api.ok("PATCH", f"/api/candidates/{fence['id']}", {"description": "Second version"})
        d = self.detail(fence["id"])
        self.assertEqual([v["description"] for v in d["description_versions"]], ["First version"])
        d = self.api.ok("POST", f"/api/candidates/{fence['id']}/descriptions/{d['description_versions'][0]['id']}/restore")
        self.assertEqual(d["description"], "First version")
        self.assertEqual(d["description_versions"][0]["description"], "Second version")


if __name__ == "__main__":
    unittest.main()
