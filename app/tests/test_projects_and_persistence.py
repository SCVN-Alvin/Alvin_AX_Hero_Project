import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from helpers import AppCase

from organizer import db
from organizer.services import tasks as tasks_svc


class ProjectTests(AppCase):
    def test_two_projects_are_isolated_and_survive_restart(self):
        a = self.project("Alpha", "ALP")
        b = self.project("Beta", "BET")
        ta = self.task(a["id"], "Alpha one", disciplines=["dev"], category="core_gameplay")
        tb = self.task(b["id"], "Beta one")
        self.assertEqual(ta["key"], "ALP-1")
        self.assertEqual(tb["key"], "BET-1")

        self.restart()

        projects = self.api.ok("GET", "/api/projects")["projects"]
        self.assertEqual({p["ticket_prefix"] for p in projects}, {"ALP", "BET"})
        alpha = self.api.ok("GET", f"/api/projects/{a['id']}/tasks")["tasks"]
        beta = self.api.ok("GET", f"/api/projects/{b['id']}/tasks")["tasks"]
        self.assertEqual([t["title"] for t in alpha], ["Alpha one"])
        self.assertEqual([t["title"] for t in beta], ["Beta one"])
        self.assertEqual(alpha[0]["category"], "core_gameplay")
        self.assertEqual(alpha[0]["disciplines"], ["dev"])
        for p in (a, b):
            self.assertTrue(os.path.isdir(os.path.join(self.data_dir, "projects", p["id"], "sources")))
            self.assertTrue(os.path.isdir(os.path.join(self.data_dir, "projects", p["id"], "assets")))

    def test_prefix_is_unique_validated_and_immutable(self):
        a = self.project("Alpha", "ALP")
        status, body = self.api.post("/api/projects", {"name": "Dup", "ticket_prefix": "alp"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "prefix_taken")
        for bad in ("A", "1AB", "AB-C", "ABCDEFGHIJK", ""):
            status, _ = self.api.post("/api/projects", {"name": "Bad", "ticket_prefix": bad})
            self.assertEqual(status, 400, bad)
        status, _ = self.api.patch(f"/api/projects/{a['id']}", {"ticket_prefix": "ZZZ"})
        self.assertEqual(status, 400)
        # The database itself also refuses to change a prefix.
        conn = db.connect(self.storage.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE project SET ticket_prefix = 'ZZZ' WHERE id = ?", (a["id"],))
        conn.close()

    def test_ticket_numbers_are_never_recycled(self):
        p = self.project()
        t1 = self.task(p["id"], "one")
        t2 = self.task(p["id"], "two")
        self.api.ok("DELETE", f"/api/tasks/{t2['id']}")
        t3 = self.task(p["id"], "three")
        self.assertEqual([t1["key"], t2["key"], t3["key"]], ["ALP-1", "ALP-2", "ALP-3"])
        conn = db.connect(self.storage.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE project SET next_ticket_number = 1 WHERE id = ?", (p["id"],))
        conn.close()

    def test_failed_write_leaves_no_partial_records(self):
        p = self.project()
        self.task(p["id"], "baseline")
        conn = db.connect(self.storage.db_path)

        def boom(*a, **k):
            raise RuntimeError("disk full (simulated)")

        # The task row, discipline rows, tags, and the ticket counter are written
        # before history is recorded; failing there must undo all of it.
        with mock.patch.object(tasks_svc, "_record", boom):
            with self.assertRaises(RuntimeError):
                tasks_svc.create_task(conn, p["id"], {"title": "doomed", "disciplines": ["art", "dev"], "tags": ["x", "y"]})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM task").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_discipline").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tag").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT next_ticket_number FROM project").fetchone()[0], 2)
        conn.close()
        # The next ticket is still ALP-2: the failed attempt consumed nothing.
        self.assertEqual(self.task(p["id"], "after")["key"], "ALP-2")

    def test_failed_update_rolls_back_every_field(self):
        p = self.project()
        t = self.task(p["id"], "original", disciplines=["dev"], tags=["keep"])
        conn = db.connect(self.storage.db_path)

        calls = {"n": 0}
        real = tasks_svc._record

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("write failed (simulated)")
            return real(*a, **k)

        with mock.patch.object(tasks_svc, "_record", flaky):
            with self.assertRaises(RuntimeError):
                tasks_svc.update_task(conn, t["id"], {"title": "changed", "category": "ui_ux", "disciplines": ["art"], "tags": ["new"]})
        conn.close()
        fresh = self.api.ok("GET", f"/api/tasks/{t['id']}")
        self.assertEqual(fresh["title"], "original")
        self.assertEqual(fresh["category"], "unassigned")
        self.assertEqual(fresh["disciplines"], ["dev"])
        self.assertEqual(fresh["tags"], ["keep"])

    def test_delete_project_moves_files_to_trash_and_removes_rows(self):
        p = self.project()
        t = self.task(p["id"], "x")
        self.task(p["id"], "child", parent_id=t["id"])
        status, body = self.api.delete(f"/api/projects/{p['id']}", {"confirm_prefix": "NOPE"})
        self.assertEqual(status, 400)
        out = self.api.ok("DELETE", f"/api/projects/{p['id']}", {"confirm_prefix": "ALP"})
        self.assertTrue(out["files_moved_to"] and os.path.isdir(out["files_moved_to"]))
        self.assertEqual(self.api.ok("GET", "/api/projects")["projects"], [])
        conn = db.connect(self.storage.db_path)
        for table in ("task", "task_discipline", "person", "tag"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        conn.close()

    def test_people_and_assignee_ownership(self):
        a = self.project("Alpha", "ALP")
        b = self.project("Beta", "BET")
        ann = self.api.ok("POST", f"/api/projects/{a['id']}/people", {"name": "Ann"})
        bob = self.api.ok("POST", f"/api/projects/{b['id']}/people", {"name": "Bob"})
        t = self.task(a["id"], "t", assignee_id=ann["id"])
        self.assertEqual(t["assignee_name"], "Ann")
        status, _ = self.api.patch(f"/api/tasks/{t['id']}", {"assignee_id": bob["id"]})
        self.assertEqual(status, 400)  # another project's person
        status, body = self.api.delete(f"/api/people/{ann['id']}")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "person_in_use")
        self.api.ok("PATCH", f"/api/tasks/{t['id']}", {"assignee_id": None})
        self.api.ok("DELETE", f"/api/people/{ann['id']}")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="organizer-mig-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.mig = os.path.join(self.dir, "migrations")
        os.makedirs(self.mig)
        self.db = os.path.join(self.dir, "o.db")

    def write(self, name, sql):
        with open(os.path.join(self.mig, name), "w") as fh:
            fh.write(sql)

    def test_failed_migration_rolls_back_and_keeps_prior_data(self):
        self.write("0001_base.sql", "CREATE TABLE a(x TEXT);\nINSERT INTO a VALUES ('keep me');\n")
        db.migrate(self.db, self.mig)
        self.write("0002_bad.sql", "CREATE TABLE b(y TEXT);\nINSERT INTO b VALUES ('half');\nINSERT INTO missing_table VALUES (1);\n")
        with self.assertRaises(db.MigrationError) as ctx:
            db.migrate(self.db, self.mig)
        self.assertIn("backup", str(ctx.exception))
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT x FROM a").fetchone()[0], "keep me")
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'b'").fetchone())
        self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0], 1)
        conn.close()
        backups = [f for f in os.listdir(self.dir) if ".bak-" in f]
        self.assertEqual(len(backups), 1)
        restored = sqlite3.connect(os.path.join(self.dir, backups[0]))
        self.assertEqual(restored.execute("SELECT x FROM a").fetchone()[0], "keep me")
        restored.close()

    def test_newer_database_is_refused(self):
        self.write("0001_base.sql", "CREATE TABLE a(x TEXT);\n")
        db.migrate(self.db, self.mig)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO schema_migration VALUES (99, 'future', 'now')")
        conn.commit()
        conn.close()
        with self.assertRaises(db.MigrationError):
            db.migrate(self.db, self.mig)

    def test_migrate_is_idempotent(self):
        self.write("0001_base.sql", "CREATE TABLE a(x TEXT);\n")
        self.assertEqual(db.migrate(self.db, self.mig)["applied"], [1])
        self.assertEqual(db.migrate(self.db, self.mig)["applied"], [])


if __name__ == "__main__":
    unittest.main()
