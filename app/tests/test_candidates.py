"""Milestone 3: candidate generation, rules with evidence, review actions, and approval onto the board."""

import glob
import os
import sqlite3
import time
import unittest
from unittest import mock

from export_fixture import make_export
from sources_case import SourcesCase

from organizer import candidates as gen
from organizer import rules as engine
from organizer.db import connect
from organizer.errors import ValidationError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES = sorted(glob.glob(os.path.join(ROOT, "RawData", "*.html")))

BRIEF = """<html><head><title>Mini brief</title></head><body><article><div class="page-body">
<h2>1. Scenario</h2>
<table class="simple-table"><thead><tr><th>Video No.</th><th>Concept</th><th>Scenario</th></tr></thead><tbody>
<tr><td>001</td><td>Right log</td><td>1. a<br/>2. b</td></tr><tr><td>002</td><td>Net</td><td>1. a<br/>2. b</td></tr>
<tr><td>003</td><td>Wrong log</td><td>1. a<br/>2. b</td></tr><tr><td>004</td><td>Wrong net</td><td>1. a<br/>2. b</td></tr></tbody></table>
<h2>2. Gameplay Sequences</h2>
<p class="block-color-yellow_background">Build fence</p>
<p>Scenario 001 + 003</p><ul><li>The player builds the wooden fence with a simple animation.</li></ul>
<p>Scenario 002 + 004</p><ul><li>No build fence animation</li></ul>
<p class="block-color-yellow_background">Fail to defend</p>
<ul><li>The fence fails and the bears break through when the wrong log is chosen.</li></ul>
<p class="block-color-yellow_background">Wood shave</p>
<p>Scenario 001 + 002</p><p>Shave clean wood: shave the log from start to end.</p>
<p>Scenario 003 + 004 + 099</p><p>Shave bad wood:</p><ul><li>The saw machine is already unlocked.</li><li>The log rotates while the saw glides past.</li></ul>
<p class="block-color-yellow_background">Spawning</p>
<ul><li>Enemies spawn continuously and use collision with the fence.</li></ul>
<h2>3. Implementation information</h2>
<p><strong>Prices:</strong></p>
<ul><li>Saw machine:<ul><li>50 coins</li></ul></li><li>Wood logs per shave:<ul><li>By hand: 10 wood logs</li><li>By saw machine: 40 wood logs</li></ul></li>
<li>Tower:<ul><li>5 wood logs</li></ul></li><li>Note: These prices should be adjustable.</li></ul>
<h3>Old ideas (01/07)</h3><p class="block-color-yellow_background">Rope bridge</p><ul><li>Use a rope to cross the river.</li></ul>
</div></article></body></html>"""


class CandidateCase(SourcesCase):
    def import_brief(self, html=BRIEF, name="Mini"):
        path, _ = make_export(self.work, name=name, html=html, files={}, with_hash=False)
        r = self.run_import(path)
        return r["document_id"]

    def generate(self, doc_id, body=None):
        return self.api.ok("POST", f"/api/documents/{doc_id}/candidates/generate", body or {})

    def by_title(self, listing, title, kind=None):
        found = [c for c in listing["candidates"] if c["title"] == title and (kind is None or c["kind"] == kind)]
        self.assertEqual(len(found), 1, f"{title!r}: {[c['title'] for c in listing['candidates']]}")
        return found[0]

    def kids(self, listing, parent):
        return [c for c in listing["candidates"] if c["parent_id"] == parent["id"]]

    def detail(self, cid):
        return self.api.ok("GET", f"/api/candidates/{cid}")


class GenerationUnitTests(unittest.TestCase):
    def test_label_and_scope_helpers(self):
        self.assertTrue(gen.is_scenario_label("Scenario 001 + 002 + 005 + 006"))
        self.assertTrue(gen.is_scenario_label("Scenarios 1, 2 and 3"))
        self.assertFalse(gen.is_scenario_label("Scenario planning for the team"))
        self.assertEqual(gen.find_values("Costs 50 coins, or 10 wood logs per shave; rotate 50 degrees to the right; ~10-12 seconds"),
                         ["50 coins", "10 wood logs", "50 degrees", "~10-12 seconds"])
        self.assertEqual(gen.find_values("The 1st floor and 2nd floor"), [])
        self.assertEqual(gen.clean_title("3.4. Gameplay Sequences Sample:", heading=True), "Gameplay Sequences Sample")
        self.assertEqual(gen.clean_title("Wood shave"), "Wood shave")

    def test_negation_is_an_exclusion_not_a_task(self):
        self.assertTrue(gen.NEG_RE.match("No build fence animation"))
        self.assertTrue(gen.NEG_RE.match("Không cần animation"))
        self.assertFalse(gen.NEG_RE.match("The net is already unlocked (No animation needed)."))
        self.assertFalse(gen.NEG_RE.match("Nothing happens"))


class RuleEngineTests(unittest.TestCase):
    def rule(self, **kw):
        base = {"id": "r", "project_id": None, "name": "R", "version": 1, "enabled": 1, "match_scope": "text", "operator": "contains",
                "pattern": "spawn*", "case_sensitive": 0, "output_field": "discipline", "proposed_value": "dev", "precedence": 0}
        base.update(kw)
        return base

    def texts(self, title="T", lines=(), path="A › B"):
        return {"title": (title, "b0"), "text": [(t, f"b{i + 1}") for i, t in enumerate(lines)], "heading_path": (path, "b0")}

    def current(self, **kw):
        c = {"category": "unassigned", "impact": "unassessed", "urgency": "unspecified", "disciplines": set()}
        c.update(kw)
        return c

    def test_phrase_matching_is_whole_word_with_optional_wildcard(self):
        r = self.rule(pattern="model*\nTexture")
        self.assertEqual(engine.match_rule(r, self.texts(lines=["Reuse the enemy model", "x"]))["matched"], "model")
        self.assertIsNotNone(engine.match_rule(r, self.texts(lines=["models are reused"])))
        self.assertIsNotNone(engine.match_rule(r, self.texts(lines=["texture pack"])))
        self.assertIsNone(engine.match_rule(r, self.texts(lines=["the texturing step"])))      # no wildcard: whole word only
        self.assertIsNone(engine.match_rule(self.rule(pattern="art"), self.texts(lines=["start the party"])))
        self.assertIsNotNone(engine.match_rule(self.rule(pattern="va chạm"), self.texts(lines=["Xử lý VA CHẠM của hàng rào"])))
        self.assertIsNone(engine.match_rule(self.rule(pattern="Texture", case_sensitive=1), self.texts(lines=["texture"])))

    def test_evidence_names_the_matched_text_and_where(self):
        m = engine.match_rule(self.rule(), self.texts(lines=["Nothing here", "Enemies spawn continuously"]))
        self.assertEqual((m["matched"], m["match_in"], m["block_id"]), ("spawn", "text", "b2"))
        self.assertIn("Enemies spawn continuously", m["snippet"])

    def test_both_expands_to_art_and_dev(self):
        found = engine.evaluate([self.rule(pattern="animation*", proposed_value="both")], self.texts(lines=["a simple animation"]))
        self.assertEqual(sorted(s["proposed_value"] for s in found), ["art", "dev"])

    def test_disciplines_accumulate_and_skip_what_is_already_set(self):
        found = engine.evaluate([self.rule(id="a", pattern="spawn*"), self.rule(id="b", pattern="texture*", proposed_value="art")], self.texts(lines=["spawn", "texture"]))
        res = engine.resolve(found, self.current(disciplines={"dev"}))
        self.assertEqual({(s["proposed_value"], s["state"]) for s in res}, {("dev", "superseded"), ("art", "suggested")})

    def test_project_rule_outranks_global_and_precedence_orders_within_a_scope(self):
        g = self.rule(id="g", output_field="impact", proposed_value="low", pattern="wood")
        p = self.rule(id="p", project_id="PRJ", output_field="impact", proposed_value="high", pattern="wood")
        res = engine.resolve(engine.evaluate([g, p], self.texts(lines=["wood"])), self.current())
        self.assertEqual({(s["rule_scope"], s["state"]) for s in res}, {("project", "suggested"), ("global", "superseded")})
        g2 = self.rule(id="g2", output_field="impact", proposed_value="medium", pattern="wood", precedence=5)
        res = engine.resolve(engine.evaluate([g, g2], self.texts(lines=["wood"])), self.current())
        self.assertEqual({s["proposed_value"]: s["state"] for s in res}, {"medium": "suggested", "low": "superseded"})

    def test_equal_rank_disagreement_is_a_conflict_and_chooses_nothing(self):
        a = self.rule(id="a", output_field="category", proposed_value="content", pattern="wood")
        b = self.rule(id="b", output_field="category", proposed_value="polish", pattern="wood")
        res = engine.resolve(engine.evaluate([a, b], self.texts(lines=["wood"])), self.current())
        self.assertEqual({s["state"] for s in res}, {"conflicting"})
        same = engine.resolve(engine.evaluate([a, self.rule(id="c", output_field="category", proposed_value="content", pattern="wood")], self.texts(lines=["wood"])), self.current())
        self.assertEqual({s["state"] for s in same}, {"suggested"})   # two rules agreeing is not a conflict

    def test_a_value_already_set_is_never_overwritten(self):
        a = self.rule(id="a", output_field="category", proposed_value="content", pattern="wood")
        res = engine.resolve(engine.evaluate([a], self.texts(lines=["wood"])), self.current(category="polish"))
        self.assertEqual(res[0]["state"], "superseded")

    def test_validation(self):
        ok = {"name": "N", "match_scope": "text", "operator": "contains", "pattern": "x", "output_field": "impact", "proposed_value": "high"}
        engine.validate_rule(ok)
        for bad in ({"name": ""}, {"pattern": ""}, {"pattern": "*"}, {"output_field": "status"}, {"proposed_value": "p0"}, {"proposed_value": "unassessed"},
                    {"match_scope": "everything"}, {"operator": "regex"}, {"precedence": 10 ** 6}, {"pattern": "a\n" * 50}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                engine.validate_rule({**ok, **bad})


class GenerationTests(CandidateCase):
    def setUp(self):
        super().setUp()
        self.doc = self.import_brief()
        self.listing = self.generate(self.doc)

    def test_groups_come_from_labels_not_from_every_bullet(self):
        titles = {(c["kind"], c["title"]) for c in self.listing["candidates"]}
        self.assertIn(("task", "Build fence"), titles)
        self.assertIn(("task", "Wood shave"), titles)
        self.assertIn(("task", "Fail to defend"), titles)
        tasks = [c for c in self.listing["candidates"] if c["kind"] == "task"]
        self.assertEqual(len(tasks), 6)  # Build fence, Fail to defend, Wood shave + 2 subtasks, Spawning
        fence = self.by_title(self.listing, "Build fence")
        lines = self.kids(self.listing, fence)
        self.assertEqual([c["kind"] for c in lines], ["requirement", "exclusion"])
        self.assertEqual(lines[1]["title"], "No build fence animation")
        self.assertEqual([s["identifier"] for s in lines[0]["scenarios"]], ["001", "003"])
        self.assertEqual([s["identifier"] for s in lines[1]["scenarios"]], ["002", "004"])

    def test_negative_instruction_stays_an_exclusion_and_feeds_no_rule(self):
        fence = self.by_title(self.listing, "Build fence")
        d = self.detail(fence["id"])
        # "simple animation" in the positive line suggests art+dev; the excluded "build fence animation" is not what triggered it
        anim = [s for s in d["suggestions"] if s["rule_id"] == "builtin-art-dev-animation"]
        self.assertTrue(anim)
        self.assertTrue(all("No build fence animation" not in s["matched_text"] for s in anim))
        only_neg = self.api.ok("POST", f"/api/projects/{self.pid}/rules/preview",
                               {"document_id": self.doc, "rule": {"name": "x", "match_scope": "text", "pattern": "build fence animation", "output_field": "category", "proposed_value": "polish"}})
        self.assertEqual(only_neg["matches"], [])  # the only text containing the phrase is the exclusion
        excl = self.by_title(self.listing, "No build fence animation", "exclusion")
        self.assertIsNone(excl["level"])

    def test_subtasks_from_scenario_scopes_with_sublabels(self):
        shave = self.by_title(self.listing, "Wood shave")
        subs = [c for c in self.kids(self.listing, shave) if c["kind"] == "task"]
        self.assertEqual([(c["title"], c["level"]) for c in subs], [("Shave clean wood", "subtask"), ("Shave bad wood", "subtask")])
        clean, bad = subs
        self.assertEqual([s["identifier"] for s in self.kids(self.listing, clean)[0]["scenarios"]], ["001", "002"])
        self.assertEqual([c["title"] for c in self.kids(self.listing, bad)], ["The saw machine is already unlocked.", "The log rotates while the saw glides past."])
        self.assertEqual({s["identifier"] for s in self.kids(self.listing, bad)[0]["scenarios"]}, {"003", "004", "099"})

    def test_original_wording_and_exact_source_blocks_are_kept(self):
        fence = self.by_title(self.listing, "Build fence")
        d = self.detail(fence["id"])
        self.assertEqual(d["sources"][0]["text"], "Build fence")
        self.assertEqual(d["sources"][0]["role"], "label")
        line = self.detail(self.kids(self.listing, fence)[0]["id"])
        self.assertEqual(line["original_text"], "The player builds the wooden fence with a simple animation.")
        roles = {s["role"]: s["text"] for s in line["sources"]}
        self.assertEqual(roles["scope"], "Scenario 001 + 003")
        blocks = self.api.ok("GET", f"/api/documents/{self.doc}/blocks?limit=1000")["blocks"]
        by_id = {b["id"]: b["text"] for b in blocks}
        self.assertTrue(all(by_id[s["block_id"]] == s["text"] for s in line["sources"]))

    def test_unresolved_scenario_identifier_is_reported_not_guessed(self):
        shave = self.by_title(self.listing, "Wood shave")
        bad = self.by_title(self.listing, "Shave bad wood")
        line = self.detail(self.kids(self.listing, bad)[0]["id"])
        by = {s["identifier"]: s["resolved"] for s in line["scenario_links"]}
        self.assertEqual(by, {"003": True, "004": True, "099": False})
        self.assertTrue(any("099" in w for w in line["warnings"]))

    def test_values_keep_their_quantities_and_units(self):
        wood = next(c for c in self.listing["candidates"] if c["title"] == "Wood logs per shave:")
        self.assertEqual(wood["values"], ["10 wood logs", "40 wood logs"])
        self.assertEqual(wood["original_text"], "Wood logs per shave:\nBy hand: 10 wood logs\nBy saw machine: 40 wood logs")
        self.assertEqual(wood["kind"], "requirement")
        self.assertIsNone(wood["parent_id"])

    def test_cross_section_values_are_offered_not_attached(self):
        wood = next(c for c in self.listing["candidates"] if c["title"] == "Wood logs per shave:")
        shave = self.by_title(self.listing, "Wood shave")
        self.assertEqual(wood["suggested_parent_id"], shave["id"])
        self.assertIn("shave", wood["suggestion_reason"].lower())
        self.assertIsNone(wood["parent_id"])
        note = next(c for c in self.listing["candidates"] if c["title"].startswith("Note: These prices"))
        self.assertIsNone(note["parent_id"])

    def test_historical_sections_are_excluded_unless_chosen(self):
        self.assertFalse(any("Rope" in c["title"] for c in self.listing["candidates"]))
        d = self.api.ok("GET", f"/api/documents/{self.doc}")
        old = next(s for s in d["sections"] if s["role"] == "historical")
        again = self.generate(self.doc, {"section_ids": [old["id"]]})
        self.assertTrue(any(c["title"] == "Rope bridge" for c in again["candidates"]))
        self.assertEqual(again["new"], 2)

    def test_regeneration_creates_no_duplicates_and_keeps_decisions(self):
        n = len(self.listing["candidates"])
        fence = self.by_title(self.listing, "Build fence")
        self.api.ok("PATCH", f"/api/candidates/{fence['id']}", {"title": "Implement fence building", "impact": "high"})
        spawn = self.by_title(self.listing, "Spawning")
        self.api.ok("POST", "/api/candidates/dismiss", {"ids": [spawn["id"]]})
        again = self.generate(self.doc)
        self.assertEqual(again["new"], 0)
        self.assertEqual(len(again["candidates"]), n)
        f = self.by_title(again, "Implement fence building")
        self.assertEqual((f["id"], f["impact"], f["title_origin"]), (fence["id"], "high", "user"))
        self.assertEqual(self.by_title(again, "Spawning")["state"], "dismissed")

    def test_rule_evidence_and_unclear_fields_stay_unassigned(self):
        spawn = self.detail(self.by_title(self.listing, "Spawning")["id"])
        dev = [s for s in spawn["suggestions"] if s["field"] == "discipline"]
        self.assertEqual({s["value"] for s in dev}, {"dev"})
        self.assertEqual({s["rule_name"] for s in dev}, {"Spawning, collision, and resource calculation"})
        self.assertTrue(any(s["matched_text"].lower().find("spawn") >= 0 for s in dev))
        for s in dev:
            self.assertEqual(s["state"], "suggested")
        self.assertEqual((spawn["category"], spawn["impact"], spawn["urgency"], spawn["disciplines"]), ("unassigned", "unassessed", "unspecified", []))
        cat = [s for s in spawn["suggestions"] if s["field"] == "category"]
        self.assertEqual([s["value"] for s in cat], ["core_gameplay"])   # a Gameplay heading suggests it; nothing is set
        self.assertEqual(spawn["priority"]["suggested"], None)
        self.assertEqual([s for s in spawn["suggestions"] if s["field"] in ("impact", "urgency")], [])

    def test_creative_failure_scenarios_are_not_bug_reports(self):
        fail = self.detail(self.by_title(self.listing, "Fail to defend")["id"])
        self.assertEqual([s for s in fail["suggestions"] if s["value"] == "bug_fix"], [])


class RuleManagementTests(CandidateCase):
    def setUp(self):
        super().setUp()
        self.doc = self.import_brief()
        self.listing = self.generate(self.doc)

    def test_builtin_rules_exist_and_cannot_be_deleted_only_disabled(self):
        rules = self.api.ok("GET", f"/api/projects/{self.pid}/rules")["rules"]
        self.assertTrue(all(r["scope"] == "global" for r in rules))
        bug = next(r for r in rules if r["id"] == "builtin-bug-report")
        status, err = self.api.call("DELETE", f"/api/rules/{bug['id']}")
        self.assertEqual((status, err["error"]["code"]), (409, "builtin_rule"))
        out = self.api.ok("PATCH", f"/api/rules/{bug['id']}", {"enabled": False})
        self.assertEqual((out["enabled"], out["version"]), (False, 2))

    def test_preview_shows_results_without_writing_then_project_rule_applies(self):
        draft = {"name": "Central mechanic", "match_scope": "title", "pattern": "wood shav*", "output_field": "impact", "proposed_value": "high"}
        before = self.api.ok("GET", f"/api/candidates/{self.by_title(self.listing, 'Wood shave')['id']}")
        pv = self.api.ok("POST", f"/api/projects/{self.pid}/rules/preview", {"document_id": self.doc, "rule": draft})
        self.assertEqual([(m["title"], m["value"], m["state"]) for m in pv["matches"]], [("Wood shave", "high", "suggested")])
        self.assertEqual(pv["matches"][0]["matched"].lower(), "wood shave")
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}/rules")["rules"].__len__(), 5)       # nothing saved
        self.assertEqual(self.api.ok("GET", f"/api/candidates/{before['id']}")["suggestions"], before["suggestions"])
        rule = self.api.ok("POST", f"/api/projects/{self.pid}/rules", draft)
        self.assertEqual(rule["scope"], "project")
        listing = self.api.ok("POST", f"/api/documents/{self.doc}/candidates/apply-rules")
        shave = self.detail(self.by_title(listing, "Wood shave")["id"])
        imp = [s for s in shave["suggestions"] if s["field"] == "impact"]
        self.assertEqual([(s["value"], s["rule_scope"], s["rule_name"]) for s in imp], [("high", "project", "Central mechanic")])
        self.assertEqual(shave["impact"], "unassessed")      # a suggestion is not a decision
        self.assertEqual(shave["priority"]["suggested"], None)

    def test_conflicting_rules_are_flagged_and_resolved_by_a_person(self):
        shave = self.by_title(self.listing, "Wood shave")
        for value in ("content", "polish"):
            self.api.ok("POST", f"/api/projects/{self.pid}/rules", {"name": f"to {value}", "match_scope": "title", "pattern": "wood shave",
                                                                      "output_field": "category", "proposed_value": value, "precedence": 5})
        listing = self.api.ok("POST", f"/api/documents/{self.doc}/candidates/apply-rules")
        d = self.detail(self.by_title(listing, "Wood shave")["id"])
        top = [s for s in d["suggestions"] if s["field"] == "category" and s["state"] == "conflicting"]
        self.assertEqual({s["value"] for s in top}, {"content", "polish"})
        self.assertEqual(self.by_title(listing, "Wood shave")["conflicts"], 2)
        self.assertEqual(d["category"], "unassigned")
        self.assertTrue(any("disagree" in w for w in d["warnings"]))
        pick = next(s for s in top if s["value"] == "polish")
        d = self.api.ok("POST", f"/api/candidates/{shave['id']}/suggestions/{pick['id']}", {"action": "accept"})
        self.assertEqual(d["category"], "polish")
        states = {s["value"]: s["state"] for s in d["suggestions"] if s["field"] == "category" and s["rule_name"].startswith("to ")}
        self.assertEqual(states, {"polish": "accepted", "content": "superseded"})

    def test_rerunning_rules_never_overwrites_manual_edits_or_reviewed_suggestions(self):
        spawn = self.by_title(self.listing, "Spawning")
        d = self.detail(spawn["id"])
        dev = next(s for s in d["suggestions"] if s["field"] == "discipline" and s["value"] == "dev")
        cat = next(s for s in d["suggestions"] if s["field"] == "category")
        self.api.ok("POST", f"/api/candidates/{spawn['id']}/suggestions/{dev['id']}", {"action": "accept"})
        self.api.ok("POST", f"/api/candidates/{spawn['id']}/suggestions/{cat['id']}", {"action": "reject"})
        self.api.ok("PATCH", f"/api/candidates/{spawn['id']}", {"urgency": "low"})
        for _ in range(2):
            self.api.ok("POST", f"/api/documents/{self.doc}/candidates/apply-rules")
        d = self.detail(spawn["id"])
        self.assertEqual((d["disciplines"], d["category"], d["urgency"]), (["dev"], "unassigned", "low"))
        states = {(s["field"], s["value"]): s["state"] for s in d["suggestions"]}
        self.assertEqual(states[("discipline", "dev")], "accepted")
        self.assertEqual(states[("category", "core_gameplay")], "rejected")     # not recreated as a fresh suggestion
        # disabling a rule removes only its untouched suggestions
        self.api.ok("PATCH", "/api/rules/builtin-art-dev-animation", {"enabled": False})
        listing = self.api.ok("POST", f"/api/documents/{self.doc}/candidates/apply-rules")
        d = self.detail(self.by_title(listing, "Build fence")["id"])
        self.assertEqual([s for s in d["suggestions"] if s["rule_id"] == "builtin-art-dev-animation"], [])

    def test_a_manually_set_field_supersedes_later_suggestions(self):
        shave = self.by_title(self.listing, "Wood shave")
        self.api.ok("PATCH", f"/api/candidates/{shave['id']}", {"category": "content"})
        d = self.detail(shave["id"])
        cat = [s for s in d["suggestions"] if s["field"] == "category"]
        self.assertTrue(cat and all(s["state"] == "superseded" for s in cat))

    def test_bad_rule_input_is_rejected(self):
        status, err = self.api.post(f"/api/projects/{self.pid}/rules", {"name": "x", "match_scope": "text", "pattern": "a", "output_field": "impact", "proposed_value": "p0"})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/projects/{self.pid}/rules/preview", {"document_id": "nope", "rule": {"name": "x", "pattern": "a", "output_field": "impact", "proposed_value": "high"}})
        self.assertEqual(status, 404)


class ReviewActionTests(CandidateCase):
    def setUp(self):
        super().setUp()
        self.doc = self.import_brief()
        self.listing = self.generate(self.doc)

    def test_edit_title_description_and_hierarchy_rules(self):
        shave = self.by_title(self.listing, "Wood shave")
        d = self.api.ok("PATCH", f"/api/candidates/{shave['id']}", {"title": "Implement wood shaving system", "description": "Shared by all scenarios."})
        self.assertEqual((d["title"], d["title_origin"], d["description"]), ("Implement wood shaving system", "user", "Shared by all scenarios."))
        self.assertEqual(self.detail(shave["id"])["sources"][0]["text"], "Wood shave")           # original wording unchanged
        sub = self.by_title(self.listing, "Shave bad wood")
        status, err = self.api.patch(f"/api/candidates/{shave['id']}", {"parent_id": sub["id"]})
        self.assertEqual(status, 400)                                                              # no cycles
        status, _ = self.api.patch(f"/api/candidates/{sub['id']}", {"parent_id": None})
        self.assertEqual(status, 400)                                                              # a subtask needs a parent
        status, _ = self.api.patch(f"/api/candidates/{shave['id']}", {"level": "epic", "parent_id": self.by_title(self.listing, "Build fence")["id"]})
        self.assertEqual(status, 400)
        status, _ = self.api.patch(f"/api/candidates/{shave['id']}", {"impact": "huge"})
        self.assertEqual(status, 400)

    def test_reparent_moves_a_requirement_line_to_another_task(self):
        fence = self.by_title(self.listing, "Build fence")
        line = self.kids(self.listing, fence)[0]
        spawn = self.by_title(self.listing, "Spawning")
        self.api.ok("PATCH", f"/api/candidates/{line['id']}", {"parent_id": spawn["id"]})
        self.assertEqual(self.detail(line["id"])["parent_id"], spawn["id"])
        status, _ = self.api.patch(f"/api/candidates/{line['id']}", {"parent_id": line["id"]})
        self.assertEqual(status, 400)

    def test_attach_a_cross_section_value_to_a_task(self):
        shave = self.by_title(self.listing, "Wood shave")
        wood = next(c for c in self.listing["candidates"] if c["title"] == "Wood logs per shave:")
        d = self.api.ok("PATCH", f"/api/candidates/{wood['id']}", {"parent_id": shave["id"]})
        self.assertEqual((d["parent_id"], d["suggested_parent_id"]), (shave["id"], None))

    def test_bulk_classification_and_accepting_suggestions(self):
        ids = [self.by_title(self.listing, t)["id"] for t in ("Wood shave", "Build fence")]
        self.api.ok("POST", "/api/candidates/classify", {"ids": ids, "disciplines": ["art", "dev"], "category": "core_gameplay", "impact": "high"})
        for cid in ids:
            d = self.detail(cid)
            self.assertEqual((d["disciplines"], d["category"], d["impact"], d["urgency"]), (["art", "dev"], "core_gameplay", "high", "unspecified"))
            self.assertEqual((d["priority"]["suggested"], d["priority"]["source"]), ("p1", "suggested"))
            self.assertIn("Impact High suggests P1", d["priority"]["reason"])
        status, _ = self.api.post("/api/candidates/classify", {"ids": ids})
        self.assertEqual(status, 400)
        spawn = self.by_title(self.listing, "Spawning")
        self.api.ok("POST", "/api/candidates/accept-suggestions", {"ids": [spawn["id"]]})
        d = self.detail(spawn["id"])
        self.assertEqual((d["disciplines"], d["category"]), (["dev"], "core_gameplay"))
        status, err = self.api.post("/api/candidates/classify", {"ids": [self.kids(self.listing, self.by_title(self.listing, "Build fence"))[0]["id"]], "impact": "high"})
        self.assertEqual(status, 400)

    def test_priority_is_explained_and_p0_is_only_ever_manual(self):
        shave = self.by_title(self.listing, "Wood shave")
        d = self.api.ok("PATCH", f"/api/candidates/{shave['id']}", {"impact": "critical", "urgency": "high"})
        self.assertEqual(d["priority"]["suggested"], "p1")
        self.assertIn("cannot promote beyond P1", d["priority"]["reason"])
        d = self.api.ok("PATCH", f"/api/candidates/{shave['id']}", {"priority_override": "p0", "priority_override_reason": "Blocks the demo"})
        self.assertEqual((d["priority"]["effective"], d["priority"]["source"], d["priority"]["suggested"]), ("p0", "overridden", "p1"))
        for impact in ("critical", "high", "medium", "low", "unassessed"):
            for urgency in ("high", "medium", "low", "unspecified"):
                from organizer.priority import suggest
                self.assertNotEqual(suggest(impact, urgency)[0], "p0")
        status, _ = self.api.patch(f"/api/candidates/{shave['id']}", {"priority_override": None, "priority_override_reason": "orphan reason"})
        self.assertEqual(status, 400)

    def test_dismiss_restore_and_cascade(self):
        fence = self.by_title(self.listing, "Build fence")
        self.api.ok("POST", "/api/candidates/dismiss", {"ids": [fence["id"]]})
        listing = self.api.ok("GET", f"/api/documents/{self.doc}/candidates")
        self.assertEqual({c["state"] for c in self.kids(listing, fence)}, {"dismissed"})
        self.api.ok("POST", "/api/candidates/restore", {"ids": [fence["id"]]})
        listing = self.api.ok("GET", f"/api/documents/{self.doc}/candidates")
        self.assertEqual({c["state"] for c in self.kids(listing, fence)}, {"needs_review"})

    def test_split_a_task_moves_lines_into_a_new_candidate(self):
        fence = self.by_title(self.listing, "Build fence")
        line, excl = self.kids(self.listing, fence)
        out = self.api.ok("POST", f"/api/candidates/{fence['id']}/split", {"title": "Fence exclusions", "child_ids": [excl["id"]], "mode": "subtask"})
        new = next(c for c in out["candidates"] if c["id"] == out["new_id"])
        self.assertEqual((new["title"], new["level"], new["parent_id"], new["origin"]), ("Fence exclusions", "subtask", fence["id"], "user"))
        self.assertEqual(self.detail(excl["id"])["parent_id"], new["id"])
        self.assertEqual([c["id"] for c in self.kids(out, fence) if c["kind"] != "task"], [line["id"]])
        sources = {s["text"] for s in self.detail(new["id"])["sources"]}
        self.assertIn("No build fence animation", sources)
        status, _ = self.api.post(f"/api/candidates/{fence['id']}/split", {"title": "x", "child_ids": ["nope"]})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/candidates/{new['id']}/split", {"title": "y", "child_ids": [excl["id"]], "mode": "subtask"})
        self.assertEqual(status, 400)   # a subtask cannot hold subtasks

    def test_merge_previews_then_preserves_lines_sources_media_and_scenarios(self):
        a, b = (self.by_title(self.listing, t) for t in ("Build fence", "Fail to defend"))
        self.api.ok("PATCH", f"/api/candidates/{b['id']}", {"category": "polish"})
        preview = self.api.ok("POST", "/api/candidates/merge", {"ids": [a["id"], b["id"]], "title": "Fence handling"})["preview"]
        self.assertEqual((preview["title"], preview["lines"], preview["merged"], preview["into_id"]), ("Fence handling", 3, ["Fail to defend"], a["id"]))
        self.assertEqual(self.detail(b["id"])["state"], "needs_review")     # a preview changes nothing
        out = self.api.ok("POST", "/api/candidates/merge", {"ids": [a["id"], b["id"]], "title": "Fence handling", "confirm": True})
        kept = self.detail(a["id"])
        self.assertEqual(kept["title"], "Fence handling")
        self.assertEqual(len(kept["children"]), 3)
        gone = self.detail(b["id"])
        self.assertEqual((gone["state"], gone["merged_into_id"]), ("merged", a["id"]))
        self.assertEqual(kept["category"], "polish")      # filled from the merged one because the kept one was unset
        texts = {s["text"] for s in kept["sources"]}
        self.assertTrue({"Build fence", "Fail to defend"} <= texts)
        status, _ = self.api.post("/api/candidates/merge", {"ids": [a["id"]]})
        self.assertEqual(status, 400)
        status, _ = self.api.patch(f"/api/candidates/{b['id']}", {"title": "x"})
        self.assertEqual(status, 409)

    def test_changing_a_group_to_a_note_keeps_it_out_of_approval(self):
        # a group with lines under it cannot become a note (the lines would be left under a note)
        spawn = self.by_title(self.listing, "Spawning")
        status, err = self.api.patch(f"/api/candidates/{spawn['id']}", {"kind": "note"})
        self.assertEqual(status, 400, err)
        self.assertEqual(self.api.ok("GET", f"/api/candidates/{spawn['id']}")["kind"], "task")
        # once its line is moved out it can, and a note is then refused at approval
        line = self.kids(self.listing, spawn)[0]
        self.api.ok("PATCH", f"/api/candidates/{line['id']}", {"parent_id": None})
        self.api.ok("PATCH", f"/api/candidates/{spawn['id']}", {"kind": "note"})
        status, err = self.api.post(f"/api/projects/{self.pid}/candidates/approve", {"ids": [spawn["id"]]})
        self.assertEqual((status, err["error"]["code"]), (409, "not_a_task"))
        status, _ = self.api.patch(f"/api/candidates/{spawn['id']}", {"kind": "asset"})
        self.assertEqual(status, 400)


class ApprovalTests(CandidateCase):
    def setUp(self):
        super().setUp()
        self.doc = self.import_brief()
        self.listing = self.generate(self.doc)
        self.shave = self.by_title(self.listing, "Wood shave")

    def approve(self, ids, **kw):
        return self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": ids, **kw})

    def test_nothing_reaches_the_board_until_approval(self):
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}")["task_count"], 0)
        board = self.api.ok("GET", f"/api/projects/{self.pid}/board")
        self.assertEqual(board["total_visible"], 0)

    def test_approving_a_shared_task_creates_board_tickets_with_source_links(self):
        self.api.ok("PATCH", f"/api/candidates/{self.shave['id']}", {"title": "Implement wood shaving system", "disciplines": ["art", "dev"], "category": "core_gameplay", "impact": "high"})
        wood = next(c for c in self.listing["candidates"] if c["title"] == "Wood logs per shave:")
        self.api.ok("PATCH", f"/api/candidates/{wood['id']}", {"parent_id": self.shave["id"]})
        out = self.approve([self.shave["id"]])
        self.assertEqual([t["key"] for t in out["tasks"]], ["BRF-1", "BRF-2", "BRF-3"])
        self.assertEqual(out["requirements"], 4)         # 1 line under the clean subtask, 2 under the bad one, and the attached values
        board = self.api.ok("GET", f"/api/projects/{self.pid}/board")
        cards = [c for col in board["columns"].values() for c in col]
        self.assertEqual(board["total_visible"], 1)       # the parent card; subtasks live inside it
        card = cards[0]
        self.assertEqual((card["title"], card["status"], len(card["children"])), ("Implement wood shaving system", "backlog", 2))
        self.assertEqual(sorted(card["disciplines"]), ["art", "dev"])
        parent = self.api.ok("GET", f"/api/tasks/{card['id']}")
        self.assertEqual((parent["review_state"], parent["provenance"], parent["category"], parent["impact"], parent["suggested_priority"]),
                         ("approved", "source_extraction", "core_gameplay", "high", "p1"))
        ev = self.api.ok("GET", f"/api/tasks/{card['id']}/evidence")
        self.assertEqual(ev["sources"][0]["text"], "Wood shave")
        self.assertEqual(ev["document_id"], self.doc)
        values = [r for r in ev["requirements"] if r["values"]]
        self.assertEqual(values[0]["values"], ["10 wood logs", "40 wood logs"])
        subs = {t["title"]: t for t in self.api.ok("GET", f"/api/projects/{self.pid}/tasks")["tasks"] if t["level"] == "subtask"}
        self.assertEqual(sorted(subs), ["Shave bad wood", "Shave clean wood"])
        bev = self.api.ok("GET", f"/api/tasks/{subs['Shave bad wood']['id']}/evidence")
        self.assertEqual([r["text"] for r in bev["requirements"]], ["The saw machine is already unlocked.", "The log rotates while the saw glides past."])
        self.assertEqual([s["identifier"] for s in bev["scenarios"]], ["003", "004"])   # 099 is not in the document, so no link was made
        self.assertEqual(bev["requirements"][0]["scenarios"], ["003", "004"])

    def test_field_origins_distinguish_accepted_rule_suggestions_from_manual_values(self):
        spawn = self.by_title(self.listing, "Spawning")
        d = self.detail(spawn["id"])
        dev = next(s for s in d["suggestions"] if s["field"] == "discipline")
        self.api.ok("POST", f"/api/candidates/{spawn['id']}/suggestions/{dev['id']}", {"action": "accept"})
        self.api.ok("PATCH", f"/api/candidates/{spawn['id']}", {"impact": "medium"})
        out = self.approve([spawn["id"]])
        task = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}")
        origins = task["field_origins"]
        self.assertEqual(origins["disciplines"], "rule")
        self.assertEqual(origins["impact"], "manual")
        self.assertNotIn("category", origins)      # never set, so never claimed as anyone's decision

    def test_a_subtask_cannot_be_approved_without_its_parent(self):
        bad = self.by_title(self.listing, "Shave bad wood")
        status, err = self.api.post(f"/api/projects/{self.pid}/candidates/approve", {"ids": [bad["id"]]})
        self.assertEqual((status, err["error"]["code"]), (409, "parent_not_approved"))
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}")["task_count"], 0)
        out = self.approve([bad["id"], self.shave["id"]], with_children=False)
        self.assertEqual(len(out["tasks"]), 2)       # the parent and the chosen subtask only
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}")["task_count"], 2)

    def test_exclusions_become_exclusion_requirements_never_tasks(self):
        fence = self.by_title(self.listing, "Build fence")
        out = self.approve([fence["id"]])
        self.assertEqual(len(out["tasks"]), 1)
        ev = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}/evidence")
        kinds = {r["text"]: r["kind"] for r in ev["requirements"]}
        self.assertEqual(kinds["No build fence animation"], "exclusion")
        excl = next(r for r in ev["requirements"] if r["kind"] == "exclusion")
        self.assertEqual(excl["scenarios"], ["002", "004"])

    def test_approval_is_all_or_nothing(self):
        real = __import__("organizer.services.tasks", fromlist=["x"]).create_task_in_tx
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("disk full")
            return real(*a, **k)

        with mock.patch("organizer.services.tasks.create_task_in_tx", flaky):
            status, err = self.api.post(f"/api/projects/{self.pid}/candidates/approve", {"ids": [self.shave["id"]]})
        self.assertEqual(status, 500)
        proj = self.api.ok("GET", f"/api/projects/{self.pid}")
        self.assertEqual((proj["task_count"], proj["next_ticket_number"]), (0, 1))      # no ticket number was spent
        listing = self.api.ok("GET", f"/api/documents/{self.doc}/candidates")
        self.assertEqual(self.by_title(listing, "Wood shave")["state"], "needs_review")
        conn = sqlite3.connect(self.storage.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM requirement").fetchone()[0], 0)
        conn.close()
        self.assertEqual(len(self.approve([self.shave["id"]])["tasks"]), 3)             # and it still works afterwards

    def test_approving_twice_or_a_dismissed_candidate_is_refused(self):
        self.approve([self.shave["id"]])
        status, err = self.api.post(f"/api/projects/{self.pid}/candidates/approve", {"ids": [self.shave["id"]]})
        self.assertEqual((status, err["error"]["code"]), (409, "not_reviewable"))
        status, err = self.api.patch(f"/api/candidates/{self.shave['id']}", {"title": "x"})
        self.assertEqual((status, err["error"]["code"]), (409, "candidate_locked"))
        spawn = self.by_title(self.listing, "Spawning")
        self.api.ok("POST", "/api/candidates/dismiss", {"ids": [spawn["id"]]})
        status, _ = self.api.post(f"/api/projects/{self.pid}/candidates/approve", {"ids": [spawn["id"]]})
        self.assertEqual(status, 409)

    def test_regenerating_after_approval_creates_nothing_new_and_keeps_the_task(self):
        self.approve([self.shave["id"]])
        again = self.generate(self.doc)
        self.assertEqual(again["new"], 0)
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}")["task_count"], 3)
        self.assertEqual(self.by_title(again, "Wood shave")["state"], "approved")
        self.assertEqual(self.by_title(again, "Wood shave")["ticket_key"], "BRF-1")

    def test_source_cannot_be_removed_while_tasks_cite_it_and_deleting_the_task_returns_the_candidates(self):
        out = self.approve([self.shave["id"]])
        status, err = self.api.call("DELETE", f"/api/documents/{self.doc}")
        self.assertEqual((status, err["error"]["code"]), (409, "cited_by_tasks"))
        self.assertEqual(self.api.ok("GET", f"/api/documents/{self.doc}")["counts"]["blocks"] > 0, True)
        tasks = self.api.ok("GET", f"/api/projects/{self.pid}/tasks?")["tasks"]
        for t in sorted(tasks, key=lambda t: t["level"] != "subtask"):
            self.api.ok("DELETE", f"/api/tasks/{t['id']}")
        listing = self.api.ok("GET", f"/api/documents/{self.doc}/candidates")
        self.assertEqual(self.by_title(listing, "Wood shave")["state"], "dismissed")
        conn = sqlite3.connect(self.storage.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM requirement").fetchone()[0], 0)
        conn.close()
        self.api.ok("DELETE", f"/api/documents/{self.doc}")

    def test_project_delete_still_works_with_approved_evidence(self):
        self.approve([self.shave["id"]])
        self.api.ok("DELETE", f"/api/projects/{self.pid}", {"confirm_prefix": "BRF"})
        conn = sqlite3.connect(self.storage.db_path)
        for t in ("task", "candidate", "requirement", "task_source", "source_document"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)
        conn.close()


class MediaSuggestionTests(CandidateCase):
    def test_media_is_suggested_by_proximity_and_attached_only_by_a_person(self):
        from export_fixture import PNG, GIF
        html = ('<html><body><h2>1. Gameplay</h2><p class="block-color-yellow_background">Wood shave</p><p>Scenario 001</p>'
                '<p>Shave clean wood: shave the giant log from start to end in one continuous pass.</p><figure class="image"><img src="Pics/a.png"></figure>'
                '<p class="block-color-yellow_background">Handheld wood shaver</p><figure class="image"><img src="Pics/b.png"></figure>'
                '<p class="block-color-yellow_background">Net catch</p><ul><li>The net bounces.</li></ul><figure class="image"><img src="Pics/gone.png"></figure></body></html>')
        path, _ = make_export(self.work, name="Pics", html=html, files={"a.png": PNG, "b.png": PNG + b"b"}, with_hash=False)
        r = self.run_import(path)
        listing = self.generate(r["document_id"])
        self.assertTrue(any(c["kind"] == "asset" and c["title"] == "Handheld wood shaver" for c in listing["candidates"]))
        shave = self.by_title(listing, "Wood shave")
        d = self.detail(shave["id"])
        reasons = [x["reason"] for x in d["references"]]
        self.assertTrue(any("Handheld wood shaver" in x and "share" in x for x in reasons), reasons)
        self.assertTrue(all(x["state"] == "suggested" for x in d["references"]))
        net = self.detail(self.by_title(listing, "Net catch")["id"])
        self.assertEqual([x["asset"]["availability"] for x in net["references"]], ["missing"])     # the missing file stays a visible reference
        ref = d["references"][0]
        d = self.api.ok("POST", f"/api/candidates/{shave['id']}/references", {"reference_id": ref["reference_id"], "state": "attached"})
        self.assertEqual(next(x for x in d["references"] if x["reference_id"] == ref["reference_id"])["state"], "attached")
        d = self.api.ok("POST", f"/api/candidates/{shave['id']}/references/all", {"state": "removed"})
        self.assertEqual({x["state"] for x in d["references"]}, {"attached", "removed"})
        out = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": [shave["id"]]})
        ev = self.api.ok("GET", f"/api/tasks/{out['tasks'][0]['id']}/evidence")
        self.assertEqual([x["reference_id"] for x in ev["references"]], [ref["reference_id"]])
        again = self.generate(r["document_id"])
        self.assertEqual(again["new"], 0)


@unittest.skipUnless(SAMPLES, "RawData sample not present")
class WoodShavingSampleTests(CandidateCase):
    """The review's complete working example, on the real brief."""

    def setUp(self):
        super().setUp()
        self.pid = self.project("Human Face Log", "HFL")["id"]
        run = self.run_import(SAMPLES[0], timeout=240)
        self.doc = run["document_id"]
        self.listing = self.generate(self.doc)

    def test_the_wood_shaving_candidates_match_the_source(self):
        shave = self.by_title(self.listing, "Wood shave")
        self.assertEqual((shave["kind"], shave["level"], shave["state"]), ("task", "task", "needs_review"))
        self.assertEqual(shave["heading_path"][-1], "3.4. Gameplay Sequences Sample:")
        subs = [c for c in self.kids(self.listing, shave) if c["kind"] == "task"]
        self.assertEqual([c["title"] for c in subs], ["Shave clean wood", "Shave bad wood"])
        clean, bad = subs
        clean_line = self.detail(self.kids(self.listing, clean)[0]["id"])
        self.assertTrue(clean_line["original_text"].startswith("Shave clean wood: Check how the giant wood log is shaved"))
        self.assertEqual([s["identifier"] for s in clean_line["scenario_links"]], ["001", "002", "005", "006"])
        self.assertTrue(all(s["resolved"] for s in clean_line["scenario_links"]))
        bad_lines = self.kids(self.listing, bad)
        self.assertEqual(bad_lines[0]["title"], "The saw machine is already unlocked in these scenarios.")
        self.assertEqual([s["identifier"] for s in self.detail(bad_lines[0]["id"])["scenario_links"]], ["003", "004", "007", "008"])
        for line in bad_lines + [self.kids(self.listing, clean)[0]]:
            self.assertEqual(line["kind"], "requirement")

    def test_resource_values_are_offered_with_exact_quantities_and_units(self):
        shave = self.by_title(self.listing, "Wood shave")
        per = self.by_title(self.listing, "Wood logs per shave:")
        self.assertEqual(per["values"], ["10 wood logs", "40 wood logs"])
        self.assertEqual(per["original_text"], "Wood logs per shave:\nBy hand: 10 wood logs\nBy saw machine: 40 wood logs")
        self.assertEqual(per["suggested_parent_id"], shave["id"])
        by_title = {c["title"]: c["values"] for c in self.listing["candidates"] if c["values"]}
        self.assertEqual(by_title["Saw machine:"], ["50 coins"])
        self.assertEqual(by_title["Expand new area:"], ["200 coins"])
        self.assertEqual(by_title["Automation Archery Tower:"], ["5 wood logs", "15 wood logs"])
        self.assertEqual(by_title["Rotating Ballista Tower:"], ["25 wood logs"])
        self.assertEqual(self.by_title(self.listing, "Saw machine:")["suggested_parent_id"], self.by_title(self.listing, "Unlock items")["id"])

    def test_historical_sections_are_not_read_by_default(self):
        d = self.api.ok("GET", f"/api/documents/{self.doc}")
        hist = {s["id"] for s in d["sections"] if s["role"] == "historical"}
        self.assertEqual(len(hist), 2)
        blocks = {b["id"]: b for b in self.api.ok("GET", f"/api/documents/{self.doc}/blocks?limit=1000")["blocks"]}
        for c in self.listing["candidates"]:
            for s in self.detail(c["id"])["sources"]:
                self.assertNotEqual(blocks[s["block_id"]]["section_role"], "historical", c["title"])

    def test_the_negative_instruction_is_an_exclusion_under_build_fence(self):
        fence = self.by_title(self.listing, "Build fence")
        excl = [c for c in self.kids(self.listing, fence) if c["kind"] == "exclusion"]
        self.assertEqual([c["title"] for c in excl], ["No build fence animation"])
        self.assertEqual([s["identifier"] for s in excl[0]["scenarios"]], ["002", "004", "006", "008"])
        self.assertFalse(any(c["kind"] == "task" and "animation" in c["title"].lower() for c in self.listing["candidates"]))

    def test_media_for_the_wood_shaving_task_comes_with_reasons(self):
        shave = self.detail(self.by_title(self.listing, "Wood shave")["id"])
        self.assertTrue(shave["references"])
        self.assertTrue(all(r["state"] == "suggested" and r["reason"] for r in shave["references"]))
        self.assertTrue(any("Handheld wood shaver" in r["reason"] for r in shave["references"]))
        self.assertTrue(all(r["asset"]["availability"] == "available" for r in shave["references"]))
        bad = self.detail(self.by_title(self.listing, "Shave bad wood")["id"])
        self.assertTrue(any("Shown directly under" in r["reason"] for r in bad["references"]))

    def test_approve_the_wood_shaving_system_onto_the_real_board(self):
        shave = self.by_title(self.listing, "Wood shave")
        per = self.by_title(self.listing, "Wood logs per shave:")
        self.api.ok("PATCH", f"/api/candidates/{shave['id']}", {"title": "Implement wood shaving system", "category": "core_gameplay", "disciplines": ["art", "dev"], "impact": "high"})
        self.api.ok("PATCH", f"/api/candidates/{per['id']}", {"parent_id": shave["id"]})
        d = self.detail(shave["id"])
        pics = [r for r in d["references"] if "Handheld wood shaver" in r["reason"]][:2]
        for r in pics:
            self.api.ok("POST", f"/api/candidates/{shave['id']}/references", {"reference_id": r["reference_id"], "state": "attached"})
        out = self.api.ok("POST", f"/api/projects/{self.pid}/candidates/approve", {"ids": [shave["id"]]})
        self.assertEqual([t["title"] for t in out["tasks"]], ["Implement wood shaving system", "Shave clean wood", "Shave bad wood"])
        board = self.api.ok("GET", f"/api/projects/{self.pid}/board")
        cards = [c for col in board["columns"].values() for c in col]
        self.assertEqual([c["title"] for c in cards], ["Implement wood shaving system"])
        ev = self.api.ok("GET", f"/api/tasks/{cards[0]['id']}/evidence")
        self.assertEqual(len(ev["references"]), len(pics))
        self.assertEqual(ev["sources"][0]["text"], "Wood shave")
        per_req = next(r for r in ev["requirements"] if r["values"])
        self.assertEqual(per_req["values"], ["10 wood logs", "40 wood logs"])
        # restart: everything is still there
        self.restart()
        again = self.api.ok("GET", f"/api/tasks/{cards[0]['id']}/evidence")
        self.assertEqual(again["requirements"], ev["requirements"])
        self.assertEqual(self.generate(self.doc)["new"], 0)


if __name__ == "__main__":
    unittest.main()
