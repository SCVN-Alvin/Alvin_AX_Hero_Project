import unittest

from helpers import AppCase


class HierarchyTests(AppCase):
    def setUp(self):
        super().setUp()
        self.p = self.project()["id"]

    def test_create_edit_reparent_and_reopen(self):
        epic = self.task(self.p, "Epic", level="epic")
        parent = self.task(self.p, "Parent task")
        sub = self.task(self.p, "Sub", parent_id=parent["id"])
        self.assertEqual(sub["level"], "subtask")
        self.assertEqual(sub["parent"]["key"], parent["key"])

        # Edit title/description with nullable classifications left alone.
        t = self.api.ok("PATCH", f"/api/tasks/{parent['id']}", {"title": "Renamed", "description": "line1\nline2"})
        self.assertEqual(t["category"], "unassigned")
        self.assertEqual(t["impact"], "unassessed")
        self.assertEqual(t["disciplines"], [])
        self.assertIsNone(t["assignee_id"])
        self.assertIsNone(t["due_at"])

        # Reparent the task under the epic; the subtask moves with it.
        self.api.ok("PATCH", f"/api/tasks/{parent['id']}", {"parent_id": epic["id"], "level": "task"})
        # Reparent the subtask directly under the epic.
        self.api.ok("PATCH", f"/api/tasks/{sub['id']}", {"parent_id": epic["id"]})

        self.restart()
        again = self.api.ok("GET", f"/api/tasks/{sub['id']}")
        self.assertEqual(again["parent_id"], epic["id"])
        e = self.api.ok("GET", f"/api/tasks/{epic['id']}")
        self.assertEqual({c["key"] for c in e["children"]}, {parent["key"], sub["key"]})
        reopened = self.api.ok("GET", f"/api/tasks/{parent['id']}")
        self.assertEqual(reopened["title"], "Renamed")
        self.assertEqual(reopened["description"], "line1\nline2")

    def test_invalid_hierarchy_is_rejected(self):
        epic = self.task(self.p, "Epic", level="epic")
        task = self.task(self.p, "Task", parent_id=epic["id"], level="task")
        sub = self.task(self.p, "Sub", parent_id=task["id"])
        patch = lambda tid, body: self.api.patch(f"/api/tasks/{tid}", body)[0]
        self.assertEqual(patch(task["id"], {"parent_id": task["id"]}), 400)            # self parent
        self.assertEqual(patch(task["id"], {"parent_id": sub["id"]}), 400)             # task below subtask (cycle)
        self.assertEqual(patch(epic["id"], {"parent_id": task["id"]}), 400)            # epic below task
        self.assertEqual(patch(task["id"], {"level": "subtask"}), 400)                 # would contain a subtask
        self.assertEqual(patch(sub["id"], {"parent_id": None}), 400)                   # subtask needs a parent
        self.assertEqual(patch(epic["id"], {"level": "task"}), 400)                    # epic holds a task
        status, _ = self.api.post(f"/api/projects/{self.p}/tasks", {"title": "x", "level": "subtask"})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/projects/{self.p}/tasks", {"title": "x", "level": "epic", "parent_id": epic["id"]})
        self.assertEqual(status, 400)
        # nothing above changed
        self.assertEqual(self.api.ok("GET", f"/api/tasks/{task['id']}")["parent_id"], epic["id"])

    def test_database_triggers_back_up_hierarchy_rules(self):
        import sqlite3
        from organizer import db
        epic = self.task(self.p, "Epic", level="epic")
        sub = self.task(self.p, "Sub", parent_id=self.task(self.p, "T")["id"])
        conn = db.connect(self.storage.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE task SET parent_id = ? WHERE id = ?", (sub["id"], epic["id"]))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE task SET parent_id = id WHERE id = ?", (epic["id"],))
        conn.close()

    def test_cross_project_edges_are_rejected(self):
        other = self.project("Beta", "BET")["id"]
        mine = self.task(self.p, "mine")
        theirs = self.task(other, "theirs")
        self.assertEqual(self.api.patch(f"/api/tasks/{mine['id']}", {"parent_id": theirs["id"], "level": "subtask"})[0], 400)
        status, _ = self.api.post(f"/api/projects/{self.p}/tasks", {"title": "x", "parent_id": theirs["id"]})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/tasks/{mine['id']}/dependencies", {"depends_on_id": theirs["id"]})
        self.assertEqual(status, 400)
        # Database-level: composite foreign keys also refuse it.
        import sqlite3
        from organizer import db
        conn = db.connect(self.storage.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE task SET parent_id = ? WHERE id = ?", (theirs["id"], mine["id"]))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO task_dependency(project_id, task_id, depends_on_task_id, created_at) VALUES (?,?,?, 'x')",
                         (self.p, mine["id"], theirs["id"]))
        conn.close()

    def test_delete_requires_no_children(self):
        parent = self.task(self.p, "P")
        self.task(self.p, "C", parent_id=parent["id"])
        status, body = self.api.delete(f"/api/tasks/{parent['id']}")
        self.assertEqual((status, body["error"]["code"]), (409, "has_children"))


class DependencyAndCriteriaTests(AppCase):
    def setUp(self):
        super().setUp()
        self.p = self.project()["id"]

    def test_dependency_cycles_and_duplicates(self):
        a, b, c = (self.task(self.p, n) for n in "abc")
        self.api.ok("POST", f"/api/tasks/{a['id']}/dependencies", {"depends_on_id": b["id"]})
        self.api.ok("POST", f"/api/tasks/{b['id']}/dependencies", {"depends_on_id": c["id"]})
        self.assertEqual(self.api.post(f"/api/tasks/{c['id']}/dependencies", {"depends_on_id": a["id"]})[0], 400)   # cycle
        self.assertEqual(self.api.post(f"/api/tasks/{a['id']}/dependencies", {"depends_on_id": a["id"]})[0], 400)   # self
        self.assertEqual(self.api.post(f"/api/tasks/{a['id']}/dependencies", {"depends_on_id": b["id"]})[0], 409)   # duplicate
        detail = self.api.ok("GET", f"/api/tasks/{b['id']}")
        self.assertEqual([d["title"] for d in detail["depends_on"]], ["c"])
        self.assertEqual([d["title"] for d in detail["dependents"]], ["a"])
        self.api.ok("DELETE", f"/api/tasks/{a['id']}/dependencies/{b['id']}")
        self.assertEqual(self.api.ok("GET", f"/api/tasks/{a['id']}")["depends_on"], [])

    def test_deleting_a_task_removes_its_links_without_touching_others(self):
        a, b, c = (self.task(self.p, n) for n in "abc")
        self.api.ok("POST", f"/api/tasks/{a['id']}/dependencies", {"depends_on_id": b["id"]})
        self.api.ok("POST", f"/api/tasks/{c['id']}/dependencies", {"depends_on_id": b["id"]})
        self.api.ok("POST", f"/api/tasks/{b['id']}/criteria", {"text": "x"})
        self.api.ok("PATCH", f"/api/tasks/{b['id']}", {"tags": ["only-here"]})
        self.api.ok("DELETE", f"/api/tasks/{b['id']}")
        self.assertEqual(self.api.ok("GET", f"/api/tasks/{a['id']}")["depends_on"], [])
        self.assertEqual(self.api.get(f"/api/tasks/{b['id']}")[0], 404)
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.p}/tags")["tags"], [])
        self.assertEqual(self.task(self.p, "d")["key"], "ALP-4")

    def test_marking_dependency_done_does_not_clear_blocked_reason(self):
        a, b = self.task(self.p, "a"), self.task(self.p, "b")
        self.api.ok("POST", f"/api/tasks/{a['id']}/dependencies", {"depends_on_id": b["id"]})
        self.api.ok("PATCH", f"/api/tasks/{a['id']}", {"is_blocked": True, "blocked_reason": "waiting on b"})
        self.api.ok("PATCH", f"/api/tasks/{b['id']}", {"status": "done"})
        a2 = self.api.ok("GET", f"/api/tasks/{a['id']}")
        self.assertTrue(a2["is_blocked"])
        self.assertEqual(a2["blocked_reason"], "waiting on b")

    def test_acceptance_criteria(self):
        t = self.task(self.p, "t")
        c1 = self.api.ok("POST", f"/api/tasks/{t['id']}/criteria", {"text": "first"})
        c2 = self.api.ok("POST", f"/api/tasks/{t['id']}/criteria", {"text": "second"})
        self.api.ok("PATCH", f"/api/criteria/{c1['id']}", {"checked": True})
        self.api.ok("PATCH", f"/api/criteria/{c2['id']}", {"text": "second!"})
        got = self.api.ok("GET", f"/api/tasks/{t['id']}")["criteria"]
        self.assertEqual([(c["text"], c["checked"]) for c in got], [("first", True), ("second!", False)])
        self.assertEqual(self.api.post(f"/api/tasks/{t['id']}/criteria", {"text": "  "})[0], 400)
        self.api.ok("DELETE", f"/api/criteria/{c1['id']}")
        self.assertEqual(len(self.api.ok("GET", f"/api/tasks/{t['id']}")["criteria"]), 1)


class ClassificationTests(AppCase):
    def setUp(self):
        super().setUp()
        self.p = self.project()["id"]

    def test_priority_policy_and_overrides(self):
        def prio(impact, urgency="unspecified"):
            t = self.task(self.p, "t", impact=impact, urgency=urgency)
            return t["suggested_priority"], t["priority"], t["priority_source"]

        self.assertEqual(prio("critical"), ("p1", "p1", "suggested"))
        self.assertEqual(prio("high"), ("p1", "p1", "suggested"))
        self.assertEqual(prio("medium"), ("p2", "p2", "suggested"))
        self.assertEqual(prio("low"), ("p3", "p3", "suggested"))
        self.assertEqual(prio("unassessed"), (None, None, "not_assessed"))
        self.assertEqual(prio("unassessed", "high"), (None, None, "not_assessed"))   # urgency alone never assesses
        self.assertEqual(prio("medium", "high")[0], "p1")
        self.assertEqual(prio("low", "high")[0], "p2")
        self.assertEqual(prio("high", "high")[0], "p1")
        self.assertEqual(prio("medium", "low")[0], "p2")
        for impact in ("critical", "high", "medium", "low", "unassessed"):
            for urgency in ("high", "medium", "low", "unspecified"):
                self.assertNotEqual(prio(impact, urgency)[0], "p0")

        t = self.task(self.p, "x", impact="low")
        o = self.api.ok("PATCH", f"/api/tasks/{t['id']}", {"priority_override": "p0", "priority_override_reason": "ship blocker"})
        self.assertEqual((o["priority"], o["priority_source"], o["suggested_priority"]), ("p0", "overridden", "p3"))
        self.assertTrue(o["priority_reason"])
        # Changing impact re-suggests but keeps the override.
        o = self.api.ok("PATCH", f"/api/tasks/{t['id']}", {"impact": "high"})
        self.assertEqual((o["priority"], o["suggested_priority"]), ("p0", "p1"))
        o = self.api.ok("PATCH", f"/api/tasks/{t['id']}", {"priority_override": None})
        self.assertEqual((o["priority"], o["priority_source"], o["priority_override_reason"]), ("p1", "suggested", ""))

    def test_values_validated(self):
        base = f"/api/projects/{self.p}/tasks"
        for field, value in (("category", "nope"), ("impact", "huge"), ("urgency", "now"), ("status", "wip"),
                             ("level", "story"), ("disciplines", ["sound"]), ("priority_override", "p9"), ("due_at", "tomorrow")):
            status, _ = self.api.post(base, {"title": "t", field: value})
            self.assertEqual(status, 400, field)
        self.assertEqual(self.api.post(base, {"title": "   "})[0], 400)
        self.assertEqual(self.api.post(base, {})[0], 400)

    def test_due_dates_keep_their_form(self):
        d = self.task(self.p, "date only", due_at="2026-12-01")
        self.assertEqual(d["due_at"], "2026-12-01")
        dt = self.task(self.p, "with time", due_at="2026-12-01T09:30:00Z")
        self.assertEqual(dt["due_at"], "2026-12-01T09:30:00Z")
        cleared = self.api.ok("PATCH", f"/api/tasks/{d['id']}", {"due_at": None})
        self.assertIsNone(cleared["due_at"])

    def test_history_records_origin_and_changes(self):
        t = self.task(self.p, "t", category="content")
        self.api.ok("PATCH", f"/api/tasks/{t['id']}", {"category": "polish", "is_blocked": True, "tags": ["Fx", "fx", "Audio"]})
        d = self.api.ok("GET", f"/api/tasks/{t['id']}")
        self.assertEqual(d["tags"], ["Audio", "Fx"])
        self.assertEqual(d["field_origins"]["category"], "manual")
        by_field = {h["field"]: h for h in reversed(d["history"])}  # newest wins
        self.assertEqual((by_field["category"]["old_value"], by_field["category"]["new_value"]), ("content", "polish"))
        self.assertEqual((by_field["is_blocked"]["old_value"], by_field["is_blocked"]["new_value"]), ("no", "yes"))
        self.assertNotIn("impact", d["field_origins"])   # never set => not claimed as manual
        self.assertEqual(d["provenance"], "manual")


class BoardTests(AppCase):
    def setUp(self):
        super().setUp()
        self.p = self.project()["id"]

    def keys(self, query="", status=None):
        board = self.api.ok("GET", f"/api/projects/{self.p}/board{query}")
        if status:
            return [c["title"] for c in board["columns"][status]]
        return board

    def test_columns_counts_and_default_backlog(self):
        t = self.task(self.p, "a")
        self.assertEqual(t["status"], "backlog")
        self.assertEqual(t["review_state"], "approved")
        b = self.keys()
        self.assertEqual(list(b["columns"]), ["backlog", "to_do", "in_progress", "in_review", "done"])
        self.assertEqual(b["counts"]["backlog"], 1)

    def test_status_change_and_ordering_persist(self):
        a, b, c = (self.task(self.p, n) for n in "abc")
        self.assertEqual(self.keys(status="backlog"), ["a", "b", "c"])
        self.api.ok("POST", f"/api/tasks/{c['id']}/move", {"status": "backlog", "before_id": a["id"]})
        self.assertEqual(self.keys(status="backlog"), ["c", "a", "b"])
        self.api.ok("POST", f"/api/tasks/{a['id']}/move", {"status": "backlog", "before_id": None})
        self.assertEqual(self.keys(status="backlog"), ["c", "b", "a"])
        self.api.ok("POST", f"/api/tasks/{b['id']}/move", {"status": "in_progress"})
        self.api.ok("POST", f"/api/tasks/{a['id']}/move", {"status": "in_progress", "before_id": b["id"]})
        self.restart()
        self.assertEqual(self.keys(status="in_progress"), ["a", "b"])
        self.assertEqual(self.keys(status="backlog"), ["c"])
        # Menu-style change (PATCH status) lands at the top of the target column.
        self.api.ok("PATCH", f"/api/tasks/{c['id']}", {"status": "in_progress"})
        self.assertEqual(self.keys(status="in_progress"), ["c", "a", "b"])

    def test_ranking_survives_many_inserts_in_the_same_gap(self):
        first = self.task(self.p, "first")
        last = self.task(self.p, "last")
        ids = []
        for i in range(25):   # forces gap exhaustion and a rebalance
            t = self.task(self.p, f"n{i}", status="to_do")
            self.api.ok("POST", f"/api/tasks/{t['id']}/move", {"status": "backlog", "before_id": last["id"]})
            ids.append(f"n{i}")
        self.assertEqual(self.keys(status="backlog"), ["first"] + ids + ["last"])
        self.assertEqual(first["title"], "first")

    def test_move_rejects_bad_targets(self):
        a, b = self.task(self.p, "a"), self.task(self.p, "b", status="to_do")
        self.assertEqual(self.api.post(f"/api/tasks/{a['id']}/move", {"status": "nope"})[0], 400)
        self.assertEqual(self.api.post(f"/api/tasks/{a['id']}/move", {"status": "backlog", "before_id": b["id"]})[0], 400)
        self.assertEqual(self.api.post(f"/api/tasks/{a['id']}/move", {"status": "backlog", "before_id": a["id"]})[0], 400)

    def test_blocked_stays_in_column(self):
        t = self.task(self.p, "t", status="in_review")
        self.api.ok("PATCH", f"/api/tasks/{t['id']}", {"is_blocked": True, "blocked_reason": "needs art"})
        card = self.keys()["columns"]["in_review"][0]
        self.assertTrue(card["is_blocked"])
        self.assertEqual(card["blocked_reason"], "needs art")
        self.assertEqual(card["status"], "in_review")

    def test_done_with_unfinished_children_needs_explicit_decision(self):
        parent = self.task(self.p, "parent")
        c1 = self.task(self.p, "c1", parent_id=parent["id"])
        c2 = self.task(self.p, "c2", parent_id=parent["id"], status="done")
        status, body = self.api.post(f"/api/tasks/{parent['id']}/move", {"status": "done"})
        self.assertEqual((status, body["error"]["code"]), (409, "unfinished_children"))
        self.assertEqual([u["title"] for u in body["error"]["details"]["unfinished"]], ["c1"])
        status, body = self.api.patch(f"/api/tasks/{parent['id']}", {"status": "done"})
        self.assertEqual(status, 409)
        self.assertEqual(self.api.ok("GET", f"/api/tasks/{parent['id']}")["status"], "backlog")
        self.api.ok("POST", f"/api/tasks/{parent['id']}/move", {"status": "done", "confirm_unfinished": True})
        self.assertEqual(self.api.ok("GET", f"/api/tasks/{parent['id']}")["status"], "done")
        # children are never completed automatically
        self.assertEqual(self.api.ok("GET", f"/api/tasks/{c1['id']}")["status"], "backlog")
        card = self.keys()["columns"]["done"][0]
        self.assertEqual((card["children_done"], card["children_total"]), (1, 2))
        self.assertEqual(c2["status"], "done")

    def test_top_level_only_with_progress_and_children(self):
        parent = self.task(self.p, "parent")
        self.task(self.p, "child", parent_id=parent["id"])
        b = self.keys()
        self.assertEqual(b["total_visible"], 1)
        card = b["columns"]["backlog"][0]
        self.assertEqual((card["children_done"], card["children_total"]), (0, 1))
        self.assertEqual([c["title"] for c in card["children"]], ["child"])

    def test_non_approved_tasks_stay_off_the_board(self):
        from organizer import db
        t = self.task(self.p, "hidden")
        conn = db.connect(self.storage.db_path)
        conn.execute("UPDATE task SET review_state = 'needs_review' WHERE id = ?", (t["id"],))
        conn.close()
        self.assertEqual(self.keys()["total_visible"], 0)
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.p}/tasks")["tasks"], [])


class FilterTests(AppCase):
    def setUp(self):
        super().setUp()
        self.p = self.project()["id"]
        self.ann = self.api.ok("POST", f"/api/projects/{self.p}/people", {"name": "Ann"})
        self.dev = self.task(self.p, "Dev only", disciplines=["dev"], category="technical", impact="high")
        self.art = self.task(self.p, "Art only", disciplines=["art"], category="content", impact="low", assignee_id=self.ann["id"])
        self.both = self.task(self.p, "Both disciplines", disciplines=["art", "dev"], category="core_gameplay", impact="critical", urgency="high")
        self.none = self.task(self.p, "Unclassified")

    def titles(self, query):
        return sorted(t["title"] for t in self.api.ok("GET", f"/api/projects/{self.p}/tasks?{query}")["tasks"])

    def card_titles(self, query):
        b = self.api.ok("GET", f"/api/projects/{self.p}/board?{query}")
        return sorted(c["title"] for col in b["columns"].values() for c in col)

    def test_dev_includes_combined_and_both_requires_both(self):
        self.assertEqual(self.titles("discipline=dev"), ["Both disciplines", "Dev only"])
        self.assertEqual(self.titles("discipline=art"), ["Art only", "Both disciplines"])
        self.assertEqual(self.titles("discipline=both"), ["Both disciplines"])
        self.assertEqual(self.titles("discipline=none"), ["Unclassified"])
        self.assertEqual(self.titles("discipline=dev,art"), ["Art only", "Both disciplines", "Dev only"])
        self.assertEqual(self.card_titles("discipline=both"), ["Both disciplines"])
        # A combined Art/Dev task appears once, even when several selections match it.
        self.assertEqual(self.card_titles("discipline=dev,art,both").count("Both disciplines"), 1)

    def test_dimensions_and_values(self):
        self.assertEqual(self.titles("category=technical,content"), ["Art only", "Dev only"])
        self.assertEqual(self.titles("category=technical&discipline=dev"), ["Dev only"])
        self.assertEqual(self.titles("category=technical&discipline=art"), [])
        self.assertEqual(self.titles("category=unassigned"), ["Unclassified"])
        self.assertEqual(self.titles("impact=unassessed"), ["Unclassified"])
        self.assertEqual(self.titles("impact=high,critical"), ["Both disciplines", "Dev only"])
        self.assertEqual(self.titles("urgency=high"), ["Both disciplines"])
        self.assertEqual(self.titles("urgency=unspecified"), ["Art only", "Dev only", "Unclassified"])
        self.assertEqual(self.titles("priority=p1"), ["Both disciplines", "Dev only"])
        self.assertEqual(self.titles("priority=p3"), ["Art only"])
        self.assertEqual(self.titles("priority=not_assessed"), ["Unclassified"])
        self.assertEqual(self.titles("priority=not_assessed,p3"), ["Art only", "Unclassified"])
        self.assertEqual(self.titles(f"assignee={self.ann['id']}"), ["Art only"])
        self.assertEqual(self.titles("assignee=unassigned"), ["Both disciplines", "Dev only", "Unclassified"])

    def test_priority_filter_uses_effective_priority(self):
        self.api.ok("PATCH", f"/api/tasks/{self.none['id']}", {"priority_override": "p0"})
        self.assertEqual(self.titles("priority=p0"), ["Unclassified"])
        self.assertEqual(self.titles("priority=not_assessed"), [])

    def test_search_title_description_and_key(self):
        self.api.ok("PATCH", f"/api/tasks/{self.art['id']}", {"description": "needs 100% polish_pass"})
        self.assertEqual(self.titles("q=art"), ["Art only"])
        self.assertEqual(self.titles("q=dev%20only"), ["Dev only"])
        self.assertEqual(self.titles("q=polish_pass"), ["Art only"])
        self.assertEqual(self.titles("q=100%25"), ["Art only"])
        self.assertEqual(self.titles("q=%25"), ["Art only"])        # literal percent, not a wildcard
        self.assertEqual(self.titles("q=_"), ["Art only"])          # literal underscore
        self.assertEqual(self.titles("q=ALP-2"), ["Art only"])
        self.assertEqual(self.titles("q=alp-"), ["Art only", "Both disciplines", "Dev only", "Unclassified"])
        self.assertEqual(self.titles("q=zzz"), [])

    def test_search_folds_non_ascii_case(self):
        self.task(self.p, "Đường dẫn gỗ", description="Hiệu ứng ÉCLAT")
        self.assertEqual(self.titles("q=%C4%91%C6%B0%E1%BB%9Dng"), ["Đường dẫn gỗ"])      # "đường"
        self.assertEqual(self.titles("q=%C3%A9clat"), ["Đường dẫn gỗ"])                  # "éclat" vs "ÉCLAT"
        self.assertEqual(self.titles("q=%C4%90%C6%AF%E1%BB%9CNG"), ["Đường dẫn gỗ"])      # "ĐƯỜNG"

    def test_bad_filter_values_rejected(self):
        status, _ = self.api.get(f"/api/projects/{self.p}/board?discipline=sound")
        self.assertEqual(status, 400)

    def test_parent_shown_once_when_only_a_subtask_matches(self):
        parent = self.task(self.p, "Container", disciplines=["dev"])
        self.task(self.p, "Sub A", parent_id=parent["id"], disciplines=["art"])
        self.task(self.p, "Sub B", parent_id=parent["id"], disciplines=["art"])
        self.task(self.p, "Sub C", parent_id=parent["id"], disciplines=["dev"])
        b = self.api.ok("GET", f"/api/projects/{self.p}/board?discipline=art&category=unassigned")
        cards = [c for col in b["columns"].values() for c in col]
        cont = [c for c in cards if c["title"] == "Container"]
        self.assertEqual(len(cont), 1)
        self.assertFalse(cont[0]["self_match"])
        self.assertEqual(cont[0]["matched_via_children"], 2)
        self.assertEqual(cont[0]["children_total"], 3)
        self.assertEqual(cont[0]["children_hidden_by_filter"], 1)
        self.assertEqual(sorted(k["title"] for k in cont[0]["children"] if k["matches"]), ["Sub A", "Sub B"])
        self.assertTrue(b["filters_active"])
        # Without filters nothing is flagged.
        plain = self.api.ok("GET", f"/api/projects/{self.p}/board")
        card = [c for col in plain["columns"].values() for c in col if c["title"] == "Container"][0]
        self.assertEqual((card["matched_via_children"], card["children_hidden_by_filter"]), (0, 0))

    def test_status_filter_in_list_view(self):
        self.api.ok("POST", f"/api/tasks/{self.dev['id']}/move", {"status": "done"})
        self.assertEqual(self.titles("status=done"), ["Dev only"])


class ConcurrencyTests(AppCase):
    def test_parallel_creates_get_unique_gapless_ticket_numbers(self):
        import threading
        p = self.project()["id"]
        errors, keys = [], []

        def worker(n):
            for i in range(6):
                try:
                    keys.append(self.task(p, f"t{n}-{i}")["ticket_number"])
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(keys), list(range(1, 49)))


class GuardTests(AppCase):
    def test_binds_to_loopback_by_default(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_foreign_host_and_origin_rejected(self):
        p = self.project()
        status, _ = self.api.call("GET", "/api/projects", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)
        status, _ = self.api.call("POST", f"/api/projects/{p['id']}/tasks", {"title": "x"}, headers={"Origin": "http://evil.example.com"})
        self.assertEqual(status, 403)

    def test_rejected_request_does_not_poison_keep_alive_connection(self):
        import http.client, json
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        body = json.dumps({"name": "x", "ticket_prefix": "XX"})
        conn.request("POST", "/api/projects", body=body, headers={"Content-Type": "application/json", "Origin": "http://evil.example"})
        r = conn.getresponse(); r.read()
        self.assertEqual(r.status, 403)
        # Same client keeps talking: the follow-up must be answered normally on a fresh connection.
        conn.request("GET", "/api/health")
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        r.read()
        conn.close()

    def test_non_json_writes_rejected(self):
        import urllib.request
        req = urllib.request.Request(self.api.base + "/api/projects", data=b"name=x", method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 415)
        ctx.exception.close()

    def test_static_shell_headers_and_traversal(self):
        import urllib.request, urllib.error
        with urllib.request.urlopen(self.api.base + "/") as r:
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.api.base + "/..%2f..%2forganizer/db.py")
        self.assertEqual(ctx.exception.code, 404)
        ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
