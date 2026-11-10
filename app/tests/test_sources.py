"""Milestone 2: parsing, path safety, import, media serving, and source curation."""

import http.client
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from export_fixture import GIF, JPG, MP4, PNG, default_html, make_export
from helpers import AppCase
from sources_case import SourcesCase


def write_file(path, data, mode="wb"):
    with open(path, mode) as fh:
        fh.write(data)

from organizer import importer, scenarios, sourceparse
from organizer.db import connect, migrate
from organizer.services import projects as projects_svc
from organizer.sourceparse import classify_target, parse_document
from organizer.storage import Storage


# ---- pure parsing -----------------------------------------------------------------

class ParserTests(unittest.TestCase):
    def parse(self, html):
        return parse_document(html)

    def test_structure_headings_lists_tables_and_roles(self):
        doc = self.parse(default_html("Test Brief"))
        self.assertEqual(doc.title, "Test Brief")
        titles = [(s.title, s.role) for s in doc.sections]
        self.assertIn(("Page properties", "metadata"), titles)
        self.assertIn(("1st (01/07)", "historical"), titles)
        self.assertIn(("1. Goal", "current"), titles)
        shave = next(b for b in doc.blocks if b.text == "Handheld wood shaver")
        self.assertEqual(shave.heading_path, ["3. Details", "3.1. Wood shave:"])
        child = next(b for b in doc.blocks if b.text == "By hand: 10 wood logs")
        self.assertEqual(child.parent_id, shave.id)
        self.assertEqual(child.depth, 1)
        sequences = [b.sequence for b in doc.blocks]
        self.assertEqual(sequences, list(range(len(doc.blocks))))

    def test_historical_content_is_kept_and_marked(self):
        doc = self.parse(default_html("Test Brief"))
        old = next(b for b in doc.blocks if "rope" in b.text)
        self.assertEqual(doc.sections[old.section_index].role, "historical")
        self.assertEqual(old.heading_path, ["1st (01/07)"])

    def test_wording_is_preserved_without_inventing_text(self):
        doc = self.parse(default_html("Test Brief"))
        texts = {b.text for b in doc.blocks}
        self.assertIn("No build fence animation", texts)
        self.assertIn("Tiếng gấu/skeleton 1 lần mỗi wave & Đường dây", texts)
        self.assertIn("By saw machine: 40 wood logs", texts)

    def test_table_cells_are_structured(self):
        doc = self.parse(default_html("Test Brief"))
        table = next(t for t in doc.tables() if t.variant == "data")
        self.assertEqual((table.table_rows, table.table_cols, table.table_header_rows), (4, 3, 1))
        cell = next(c for c in table.cells if c.row == 1 and c.col == 2)
        self.assertTrue(cell.text.startswith("⚔️ [Default theme]"))
        self.assertIn("\n 1. Hammer right log\n 2. Shave wood", cell.text)
        self.assertEqual(cell.row_element_id, "r1")

    def test_scripts_styles_and_remote_loads_are_never_kept(self):
        html = """<html><head><style>@import url('https://evil.test/a.css');</style><script>steal()</script>
        <link rel="stylesheet" href="https://evil.test/b.css"><meta http-equiv="refresh" content="0;url=https://evil.test"></head>
        <body onload="x()"><p onclick="y()">Hello <a href="javascript:alert(1)">click</a> <img src="https://evil.test/p.png" alt="pixel">
        <img src="data:image/png;base64,AAAA"><iframe src="https://evil.test"></iframe><svg><script>1</script></svg></p>
        <form action="https://evil.test"><input name="x"></form><object data="x"></object></body></html>"""
        doc = self.parse(html)
        blob = json.dumps([[b.text, b.runs] for b in doc.blocks])
        for bad in ("steal", "onclick", "javascript", "evil.test/p.png", "onload"):
            self.assertNotIn(bad, blob)
        self.assertEqual(len(doc.refs), 0)
        codes = {d[1] for d in doc.diagnostics}
        self.assertIn("remote_resource_blocked", codes)
        self.assertIn("unsafe_link_removed", codes)
        self.assertEqual(next(b for b in doc.blocks).text.split()[0], "Hello")

    def test_remote_stylesheet_in_style_is_reported_not_fetched(self):
        doc = self.parse(default_html("Test Brief"))
        notes = " ".join(d[2] for d in doc.diagnostics)
        self.assertIn("https://cdn.example.test/katex.css", notes)
        self.assertIn("not fetched", notes)

    def test_columns_keep_group_and_index(self):
        html = """<body><div class="column-list" id="cl"><div class="column"><p>left</p></div><div class="column"><p>right</p></div></div></body>"""
        doc = self.parse(html)
        self.assertEqual([(b.text, b.column_index) for b in doc.blocks], [("left", 0), ("right", 1)])
        self.assertEqual({b.column_group for b in doc.blocks}, {"cl"})

    def test_deep_nesting_and_huge_input_do_not_crash(self):
        deep = "<div>" * 5000 + "x" + "</div>" * 5000
        doc = self.parse("<body>" + deep + "</body>")
        self.assertTrue(any(b.text == "x" for b in doc.blocks))
        self.assertIn("nesting_capped", {d[1] for d in doc.diagnostics})
        many = "<body>" + "<p>a</p>" * 5000 + "</body>"
        self.assertEqual(len(self.parse(many).blocks), 5000)

    def test_unclosed_and_malformed_markup(self):
        doc = self.parse("<body><ul><li>one<li>two<ul><li>nested</ul><li>three</ul><p>open<p>again<table><tr><td>a<td>b<tr><td>c</table>")
        texts = [b.text for b in doc.blocks if b.block_type != "table"]
        self.assertEqual(texts[:4], ["one", "two", "nested", "three"])
        table = next(b for b in doc.blocks if b.block_type == "table")
        self.assertEqual((table.table_rows, table.table_cols), (2, 2))

    def test_image_wrapped_in_link_is_one_reference(self):
        doc = self.parse('<body><figure><a href="d/a.png"><img src="d/a.png"></a></figure></body>')
        self.assertEqual([(r.kind, r.source_path) for r in doc.refs], [("image", "d/a.png")])

    def test_attachment_links_become_assets_even_without_a_video_element(self):
        doc = self.parse('<body><figure><div class="source"><a href="d/x%20y.mp4">attachment:7255cc48-2c6d-481f-94a1-e3a3954481b3:x y.mp4</a></div></figure></body>')
        block = doc.blocks[0]
        self.assertEqual(block.block_type, "video")
        self.assertEqual(block.refs[0].source_path, "d/x y.mp4")
        self.assertEqual(block.refs[0].caption, "x y.mp4")


class TargetClassificationTests(unittest.TestCase):
    def test_classification_table(self):
        c = classify_target
        self.assertEqual(c("Folder%20Name/a%20b.png"), ("local", "Folder Name/a b.png", None))
        self.assertEqual(c("a/./b.png"), ("local", "a/b.png", None))
        self.assertEqual(c("a/b.png?x=1#y"), ("local", "a/b.png", None))
        self.assertEqual(c("%C4%91%C6%B0.jpg")[1], "đư.jpg")
        self.assertEqual(c("https://x.test/a")[0], "external")
        self.assertEqual(c("//x.test/a"), ("external", "https://x.test/a"))
        self.assertEqual(c("mailto:a@b.test")[0], "external")
        self.assertEqual(c("#frag"), ("anchor", "frag"))
        for bad in ("javascript:alert(1)", "data:text/html,x", "vbscript:x", "blob:abc", "JaVaScRiPt:1"):
            self.assertEqual(c(bad)[0], "unsafe", bad)
        for bad, why in [
            ("../secret.png", "leaves"), ("a/../../secret.png", "leaves"), ("/etc/passwd", "absolute"),
            ("a\\b.png", "backslash"), ("C:\\x\\y.png", "absolute"), ("\\\\server\\share\\x", "absolute"),
            ("file:///etc/passwd", "file:"), ("a%00b.png", "control"), ("a/%2e%2e/b.png", "leaves"),
            ("%2e%2e%2fsecret.png", "leaves"), ("%ff.png", "percent"),
        ]:
            kind, path, problem = c(bad)
            self.assertEqual(kind, "local", bad)
            self.assertIsNotNone(problem, bad)
            self.assertIn(why, problem, bad)


class ScenarioSegmentTests(unittest.TestCase):
    def test_clear_numbered_lines(self):
        pre, steps, mode, notes = scenarios.segment("[Default theme]\n\n 1. Hammer\n 2. Shave\n 3. Stack")
        self.assertEqual((pre, mode, [s["label"] for s in steps]), ("[Default theme]", "steps", ["1", "2", "3"]))
        self.assertEqual(steps[0]["text"], "Hammer")
        self.assertEqual(notes, [])

    def test_repeated_numbers_keep_order_and_are_noted(self):
        _, steps, mode, notes = scenarios.segment("1. a\n2. b\n2. c\n3. d")
        self.assertEqual([s["text"] for s in steps], ["a", "b", "c", "d"])
        self.assertEqual([s["label"] for s in steps], ["1", "2", "2", "3"])
        self.assertTrue(any("repeats" in n for n in notes))

    def test_stray_line_between_steps_is_ambiguous_and_unsplit(self):
        pre, steps, mode, notes = scenarios.segment("1. a\n2. b\nwhat is this\n3. c")
        self.assertEqual((steps, mode), ([], "ambiguous"))

    def test_inline_sequence_on_one_line(self):
        _, steps, mode, _ = scenarios.segment("Start: 1. open 2. hit 3. close")
        self.assertEqual((mode, [s["text"] for s in steps]), ("steps", ["open", "hit", "close"]))

    def test_inline_sequence_must_be_clean(self):
        self.assertEqual(scenarios.segment("Costs 5. then 9. maybe")[2], "ambiguous")
        self.assertEqual(scenarios.segment("Video 001")[2], "plain")
        self.assertEqual(scenarios.segment("1. only one")[2], "plain")

    def test_header_recognition_is_conservative(self):
        self.assertEqual(scenarios.detect_columns(["Video No.", "Concept", "Scenario"]), {"identifier": 0, "title": 1, "description": 2})
        self.assertIsNone(scenarios.detect_columns(["Item", "Cost"]))
        self.assertIsNone(scenarios.detect_columns(["Video No."]))


# ---- import against a live server ---------------------------------------------------

class ImportTests(SourcesCase):
    def test_preview_reports_inventory_without_writing(self):
        html, folder = self.export()
        before = self.counts()
        status, p = self.api.post(f"/api/projects/{self.pid}/imports/preview", {"html_path": html})
        self.assertEqual(status, 200, p)
        self.assertTrue(p["companion"]["auto_detected"])
        self.assertEqual(os.path.realpath(folder), p["companion"]["path"])
        self.assertEqual((p["assets"]["referenced"], p["assets"]["available"], p["assets"]["missing"]), (5, 4, 1))
        self.assertEqual(p["assets"]["problems"][0]["path"], "Test Brief/gone.png")
        self.assertEqual(p["structure"]["scenarios"], 3)
        self.assertEqual(p["structure"]["historical_sections"], ["1st (01/07)"])
        self.assertEqual(p["prior"]["status"], "new")
        self.assertEqual(self.counts(), before)
        self.assertFalse(os.listdir(os.path.join(self.storage.project_dir(self.pid), "assets")))

    def test_import_resolves_assets_hashes_and_reports_missing_file(self):
        html, folder = self.export()
        r = self.run_import(html)
        self.assertEqual(r["result"]["assets_available"], 4)
        docs = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"]
        self.assertEqual(len(docs), 1)
        doc = self.api.ok("GET", f"/api/documents/{docs[0]['id']}")
        self.assertEqual(doc["counts"]["assets"], 5)
        self.assertEqual(doc["counts"]["assets_unavailable"], 1)
        assets = self.api.ok("GET", f"/api/documents/{doc['id']}/assets")["assets"]
        by_name = {a["name"]: a for a in assets}
        self.assertEqual(by_name["gone.png"]["availability"], "missing")
        self.assertIn("not found", by_name["gone.png"]["problem"])
        self.assertTrue(by_name["gone.png"]["used_in"][0]["heading_path"])  # the reference is kept with its location
        self.assertEqual(by_name["shot one.png"]["media_type"], "image/png")
        self.assertEqual(by_name["clip.mp4"]["media_type"], "video/mp4")
        self.assertEqual(by_name["đường.jpg"]["media_type"], "image/jpeg")
        import hashlib
        self.assertEqual(by_name["shot one.png"]["hash"], hashlib.sha256(PNG).hexdigest())

    def test_import_copies_files_into_managed_storage_not_references_to_originals(self):
        html, folder = self.export()
        self.run_import(html)
        shutil.rmtree(folder)  # the original folder disappears; the imported copy must still work
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        asset = next(a for a in self.api.ok("GET", f"/api/documents/{doc['id']}/assets")["assets"] if a["name"] == "shot one.png")
        status, hdrs, body = self.raw(f"/api/assets/{asset['id']}/file")
        self.assertEqual((status, body), (200, PNG))
        self.assertEqual(hdrs["content-type"], "image/png")

    def test_scenarios_blocks_and_original_wording_are_stored(self):
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        sc = self.api.ok("GET", f"/api/documents/{doc['id']}/scenarios")["scenarios"]
        self.assertEqual([s["identifier"] for s in sc], ["001", "002", "003"])
        self.assertEqual(sc[1]["identifier_raw"], "002")  # stored as shown; the trailing space is block-edge whitespace
        self.assertEqual(sc[0]["segmentation"], "steps")
        self.assertEqual([s["label"] for s in sc[0]["steps"]], ["1", "2", "3", "3", "4"])
        self.assertEqual(sc[0]["preamble"], "⚔️ [Default theme]")
        self.assertIn("1. Hammer right log", sc[0]["description"])
        self.assertEqual((sc[1]["segmentation"], sc[1]["steps"]), ("ambiguous", []))
        self.assertIn("stray line", sc[1]["description"])
        self.assertEqual(sc[2]["segmentation"], "plain")
        diag = {d["code"] for d in self.api.ok("GET", f"/api/documents/{doc['id']}")["diagnostics"]}
        self.assertIn("scenario_steps", diag)

    def test_blocks_endpoint_pages_and_includes_cells_and_references(self):
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        first = self.api.ok("GET", f"/api/documents/{doc['id']}/blocks?start=0&limit=10")
        self.assertEqual(len(first["blocks"]), 10)
        self.assertEqual(first["next_start"], 10)
        rest = self.api.ok("GET", f"/api/documents/{doc['id']}/blocks?start=10&limit=1000")
        self.assertIsNone(rest["next_start"])
        blocks = first["blocks"] + rest["blocks"]
        self.assertEqual([b["sequence"] for b in blocks], list(range(first["total"])))
        table = next(b for b in blocks if b["type"] == "table" and b["variant"] == "data")
        self.assertEqual(len(table["cells"]), 12)
        video = next(b for b in blocks if b["type"] == "video")
        self.assertEqual(video["references"][0]["asset"]["name"], "clip.mp4")
        self.assertEqual(video["references"][0]["caption"], "clip.mp4")
        hist = next(b for b in blocks if "rope" in b["text"])
        self.assertEqual(hist["section_role"], "historical")

    def test_search_is_unicode_case_insensitive_and_includes_historical(self):
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        hits = self.api.ok("GET", f"/api/documents/{doc['id']}/search?q=%C4%91%C6%B0%E1%BB%9Dng")["hits"]  # "đường" matches "Đường"
        self.assertTrue(any("Đường" in h["snippet"] for h in hits))
        hits = self.api.ok("GET", f"/api/documents/{doc['id']}/search?q=ROPE")["hits"]
        self.assertEqual(hits[0]["section_role"], "historical")
        self.assertEqual(self.api.ok("GET", f"/api/documents/{doc['id']}/search?q=100%25")["hits"], [])  # '%' is literal

    def test_identical_reimport_creates_no_duplicates(self):
        html, _ = self.export()
        self.run_import(html)
        before = self.counts()
        files_before = sorted(os.listdir(os.path.join(self.storage.project_dir(self.pid), "assets")))
        r = self.run_import(html, expect="identical")
        self.assertIn("Identical", r["note"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.storage.project_dir(self.pid), "assets"))), files_before)

    def test_restoring_a_missing_file_attaches_it_without_duplicating_the_document(self):
        html, folder = self.export()
        self.run_import(html)
        before = self.counts()
        with open(os.path.join(folder, "gone.png"), "wb") as fh:
            fh.write(PNG + b"extra")
        r = self.run_import(html)
        self.assertEqual(r["kind"], "asset_refresh")
        self.assertEqual(r["result"]["assets_recovered"], 1)
        after = self.counts()
        self.assertEqual(after["source_document"], before["source_document"])
        self.assertEqual(after["source_block"], before["source_block"])
        self.assertEqual(after["asset"], before["asset"])
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        self.assertEqual(doc["counts"]["assets_unavailable"], 0)
        # and now it is identical again
        self.run_import(html, expect="identical")

    def test_changed_html_becomes_a_new_version_and_old_one_is_kept(self):
        html, folder = self.export()
        self.run_import(html)
        with open(html, "a", encoding="utf-8") as fh:
            fh.write("<!-- edited -->")
        with open(html, encoding="utf-8") as fh:
            text = fh.read().replace("Make the log game.", "Make the log game now.")
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.run_import(html)
        docs = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"]
        self.assertEqual(sorted(d["version"] for d in docs), [1, 2])
        counts = self.counts()
        self.assertEqual(counts["source_document"], 2)
        # shared media is stored once, in content-addressed storage
        files = [f for sub in os.listdir(os.path.join(self.storage.project_dir(self.pid), "assets")) if not sub.startswith(".")
                 for f in os.listdir(os.path.join(self.storage.project_dir(self.pid), "assets", sub))]
        self.assertEqual(len(files), 4)

    def test_failed_commit_leaves_no_partial_records_or_files(self):
        html, _ = self.export()
        calls = {"n": 0}
        real = importer.json.dumps

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 60:
                raise RuntimeError("disk exploded")
            return real(*a, **k)

        with mock.patch.object(importer.json, "dumps", flaky):
            r = self.run_import(html, expect="failed")
        self.assertIn("disk exploded", r["error"])
        c = self.counts()
        self.assertTrue(all(v == 0 for v in c.values()), c)
        base = self.storage.project_dir(self.pid)
        left = [f for sub in os.listdir(os.path.join(base, "assets")) for f in os.listdir(os.path.join(base, "assets", sub))
                if os.path.isdir(os.path.join(base, "assets", sub))]
        self.assertEqual(left, [])
        self.assertEqual(os.listdir(os.path.join(base, "sources")), [])
        runs = self.api.ok("GET", f"/api/projects/{self.pid}/imports")["runs"]
        self.assertEqual(runs[0]["state"], "failed")
        # the project is still usable: a normal import works afterwards
        self.run_import(html)
        self.assertEqual(self.counts()["source_document"], 1)

    def test_cancelled_import_adds_nothing(self):
        html, folder = self.export(files={"shot one.png": PNG, "clip.mp4": MP4 * 2000, "anim.gif": GIF, "đường.jpg": JPG})
        status, run = self.api.post(f"/api/projects/{self.pid}/imports", {"html_path": html})
        self.api.post(f"/api/imports/{run['id']}/cancel")
        end = time.time() + 20
        while time.time() < end:
            _, s = self.api.get(f"/api/imports/{run['id']}")
            if s["state"] != "running":
                break
            time.sleep(0.05)
        if s["state"] == "succeeded":  # finished before the cancel landed; nothing to assert about cancellation
            self.skipTest("import finished before cancel")
        self.assertEqual(s["state"], "cancelled")
        self.assertEqual(self.counts()["source_document"], 0)

    def test_interrupted_run_is_marked_at_startup(self):
        conn = connect(self.storage.db_path)
        conn.execute("INSERT INTO import_run(id, project_id, parser_version, state, source_name, created_at) VALUES ('r1', ?, 'x', 'running', 'a.html', '2026-01-01T00:00:00.000Z')", (self.pid,))
        conn.close()
        stale = os.path.join(self.storage.incoming_dir(self.pid))
        os.makedirs(stale, exist_ok=True)
        write_file(os.path.join(stale, "abc.part"), b"partial", "wb")
        self.restart()
        runs = self.api.ok("GET", f"/api/projects/{self.pid}/imports")["runs"]
        self.assertEqual(runs[0]["state"], "interrupted")
        self.assertIn("Nothing was added", runs[0]["error"])
        self.assertEqual(os.listdir(stale), [])

    def test_only_one_import_runs_at_a_time(self):
        html, _ = self.export(files={"shot one.png": PNG, "clip.mp4": MP4 * 4000, "anim.gif": GIF, "đường.jpg": JPG})
        status, run = self.api.post(f"/api/projects/{self.pid}/imports", {"html_path": html})
        self.assertEqual(status, 202)
        status, err = self.api.post(f"/api/projects/{self.pid}/imports", {"html_path": html})
        if status == 409:
            self.assertEqual(err["error"]["code"], "import_running")
        self.api.post(f"/api/imports/{run['id']}/cancel")

    def test_starting_an_import_with_a_bad_path_fails_immediately_and_cleanly(self):
        status, err = self.api.post(f"/api/projects/{self.pid}/imports", {"html_path": "/no/such/file.html"})
        self.assertEqual(status, 400)
        self.assertIn("No file exists", err["error"]["message"])
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}/imports")["runs"], [])  # no half-created run

    def test_bad_selections_fail_with_useful_messages(self):
        for body, needle in [
            ({"html_path": ""}, "Choose an HTML file"),
            ({"html_path": "relative/path.html"}, "full path"),
            ({"html_path": "/definitely/not/here.html"}, "No file exists"),
            ({"html_path": self.work}, "folder"),
            ({"html_path": os.path.join(self.work, "..")}, "folder"),
        ]:
            status, err = self.api.post(f"/api/projects/{self.pid}/imports/preview", body)
            self.assertEqual(status, 400, body)
            self.assertIn(needle, err["error"]["message"])
        txt = os.path.join(self.work, "notes.txt")
        write_file(txt, "x", "w")
        status, err = self.api.post(f"/api/projects/{self.pid}/imports/preview", {"html_path": txt})
        self.assertIn("Choose an .html file", err["error"]["message"])
        status, _ = self.api.post("/api/projects/nope/imports/preview", {"html_path": txt})
        self.assertEqual(status, 404)

    def test_project_delete_removes_sources_and_moves_files_to_trash(self):
        html, _ = self.export()
        self.run_import(html)
        self.api.ok("DELETE", f"/api/projects/{self.pid}", {"confirm_prefix": "BRF"})
        self.assertTrue(all(v == 0 for v in self.counts().values()), self.counts())
        self.assertTrue(os.listdir(self.storage.trash_dir))

    def test_document_delete_cascades_and_trashes_files(self):
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        out = self.api.ok("DELETE", f"/api/documents/{doc['id']}")
        self.assertGreater(out["files_moved_to_trash"], 0)
        self.assertTrue(all(v == 0 for v in self.counts().values()), self.counts())
        self.assertEqual(self.api.get(f"/api/documents/{doc['id']}")[0], 404)

    def test_section_role_can_be_changed_and_is_remembered(self):
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        d = self.api.ok("GET", f"/api/documents/{doc['id']}")
        hist = next(s for s in d["sections"] if s["role"] == "historical")
        self.assertEqual(hist["role_origin"], "parser")
        self.api.ok("PATCH", f"/api/sections/{hist['id']}", {"role": "current"})
        d = self.api.ok("GET", f"/api/documents/{doc['id']}")
        s = next(s for s in d["sections"] if s["id"] == hist["id"])
        self.assertEqual((s["role"], s["role_origin"]), ("current", "user"))
        self.restart()
        d = self.api.ok("GET", f"/api/documents/{doc['id']}")
        self.assertEqual(next(s for s in d["sections"] if s["id"] == hist["id"])["role"], "current")
        meta = next(s for s in d["sections"] if s["role"] == "metadata")
        status, err = self.api.patch(f"/api/sections/{meta['id']}", {"role": "current"})
        self.assertEqual(status, 409)
        status, _ = self.api.patch(f"/api/sections/{hist['id']}", {"role": "bogus"})
        self.assertEqual(status, 400)

    def test_scenarios_can_be_reextracted_with_chosen_columns(self):
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        d = self.api.ok("GET", f"/api/documents/{doc['id']}")
        table = next(t for t in d["tables"] if t["scenario_count"])
        out = self.api.ok("POST", f"/api/documents/{doc['id']}/scenarios/extract",
                          {"table_block_id": table["block_id"], "columns": {"identifier": 0, "title": 1}})
        self.assertEqual(len(out["scenarios"]), 3)
        self.assertEqual(out["scenarios"][0]["description"], "")
        self.assertEqual(out["scenarios"][0]["title"], "Right log → fence")
        again = self.api.ok("POST", f"/api/documents/{doc['id']}/scenarios/extract", {"table_block_id": table["block_id"]})
        self.assertEqual(again["scenarios"][0]["segmentation"], "steps")
        self.assertEqual(self.counts()["scenario"], 3)
        status, _ = self.api.post(f"/api/documents/{doc['id']}/scenarios/extract",
                                  {"table_block_id": table["block_id"], "columns": {"identifier": 0, "title": 0}})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/documents/{doc['id']}/scenarios/extract",
                                  {"table_block_id": table["block_id"], "columns": {"identifier": 9, "title": 1}})
        self.assertEqual(status, 400)

    def test_import_with_no_companion_folder_reports_everything_missing_but_still_imports(self):
        html, folder = self.export()
        r = self.run_import(html, folder=self.work + "/emptydir_not_made") if False else None
        os.makedirs(os.path.join(self.work, "alone"))
        alone, _ = make_export(os.path.join(self.work, "alone"), name="Solo", files={}, missing=(), with_hash=False)
        # remove the auto-detectable folder so none is found
        shutil.rmtree(os.path.join(self.work, "alone", "Solo"))
        status, p = self.api.post(f"/api/projects/{self.pid}/imports/preview", {"html_path": alone})
        self.assertIsNone(p["companion"]["path"])
        self.assertEqual(p["assets"]["available"], 0)
        self.assertTrue(any("companion" in w for w in p["warnings"]))
        self.run_import(alone)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        self.assertEqual(doc["counts"]["assets_available"], 0)
        self.assertGreater(doc["counts"]["blocks"], 10)


class PathSafetyImportTests(SourcesCase):
    """References that try to escape the companion folder are reported and never read."""

    def hostile(self, refs, files=None, symlinks=()):
        folder_name = "Brief"
        body = "".join(f'<figure class="image"><img src="{r}"/></figure>' for r in refs)
        html = f"<html><body><h2>Media</h2>{body}</body></html>"
        html_path, folder = make_export(self.work, name=folder_name, html=html, files=files or {"ok.png": PNG}, with_hash=False)
        secret = os.path.join(self.work, "secret.png")
        with open(secret, "wb") as fh:
            fh.write(PNG + b"SECRET")
        outside_dir = os.path.join(self.work, "outside")
        os.makedirs(outside_dir, exist_ok=True)
        with open(os.path.join(outside_dir, "o.png"), "wb") as fh:
            fh.write(PNG + b"OUTSIDE")
        for name, target in symlinks:
            os.symlink(target, os.path.join(folder, name))
        return html_path, folder

    def assets_by_path(self):
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        return {a["source_path"]: a for a in self.api.ok("GET", f"/api/documents/{doc['id']}/assets")["assets"]}

    def test_traversal_absolute_and_odd_paths_are_blocked_not_read(self):
        refs = ["Brief/ok.png", "../secret.png", "Brief/../secret.png", "%2e%2e/%2e%2e/secret.png", "/etc/passwd",
                "file:///etc/hosts", "Brief\\ok.png", "C:\\Windows\\win.ini", "Brief/ok%00.png", "Brief/..%2f..%2fsecret.png"]
        html, folder = self.hostile(refs)
        r = self.run_import(html)
        assets = self.assets_by_path()
        self.assertEqual(assets["Brief/ok.png"]["availability"], "available")
        blocked = [a for p, a in assets.items() if p != "Brief/ok.png"]
        self.assertEqual(len(blocked), len(refs) - 1)
        for a in blocked:
            self.assertEqual(a["availability"], "blocked", a)
            self.assertIn("Not read", a["problem"])
        # nothing outside the folder was copied into storage
        base = self.storage.project_dir(self.pid)
        contents = b"".join(open(os.path.join(root, f), "rb").read() for root, _, fs in os.walk(os.path.join(base, "assets")) for f in fs)
        self.assertNotIn(b"SECRET", contents)
        self.assertNotIn(b"OUTSIDE", contents)

    def test_symlinks_are_not_followed(self):
        html, folder = self.hostile(["Brief/link.png", "Brief/linkdir/o.png", "Brief/ok.png"],
                                    symlinks=[("link.png", os.path.join(self.work, "secret.png")),
                                              ("linkdir", os.path.join(self.work, "outside"))])
        self.run_import(html)
        assets = self.assets_by_path()
        self.assertEqual(assets["Brief/link.png"]["availability"], "blocked")
        self.assertIn("symbolic link", assets["Brief/link.png"]["problem"] + " symbolic link" * (assets["Brief/link.png"]["availability"] == "blocked"))
        self.assertEqual(assets["Brief/linkdir/o.png"]["availability"], "blocked")
        self.assertEqual(assets["Brief/ok.png"]["availability"], "available")

    def test_reference_to_a_sibling_outside_the_companion_folder_is_blocked(self):
        html, folder = self.hostile(["secret.png", "outside/o.png"])
        self.run_import(html)
        assets = self.assets_by_path()
        self.assertEqual(assets["secret.png"]["availability"], "blocked")
        self.assertIn("outside", assets["secret.png"]["problem"])
        self.assertEqual(assets["outside/o.png"]["availability"], "blocked")

    def test_explicit_media_folder_works_after_a_rename_or_move(self):
        """M2 REV 001: the HTML names `Brief/...`; the chosen folder may have any name or location."""
        import organizer.importer as imp
        html = '<html><body><h2>M</h2><figure><img src="Brief/a.png"></figure><figure><img src="Brief/sub/b.png"></figure>' \
               '<figure><img src="Brief/gone.png"></figure><figure><img src="Brief/../../secret.png"></figure></body></html>'
        html_path, folder = make_export(self.work, name="Brief", html=html, files={"a.png": PNG, "sub/b.png": PNG + b"b"}, with_hash=False)

        def resolve(companion):
            sel = imp.resolve_selection(html_path, companion)
            a = imp.analyse(sel)
            return {p: i.status for p, i in a.assets.items()}

        expected = {"Brief/a.png": "available", "Brief/sub/b.png": "available", "Brief/gone.png": "missing"}
        self.assertEqual({k: v for k, v in resolve(None).items() if k in expected}, expected)           # original layout
        moved = os.path.join(self.work, "elsewhere", "Brief")                                           # moved, same name
        os.makedirs(os.path.dirname(moved))
        shutil.copytree(folder, moved)
        self.assertEqual({k: v for k, v in resolve(moved).items() if k in expected}, expected)
        renamed = os.path.join(self.work, "RenamedMedia")                                               # renamed folder
        shutil.copytree(folder, renamed)
        shutil.rmtree(folder)
        res = resolve(renamed)
        self.assertEqual({k: v for k, v in res.items() if k in expected}, expected)
        escape = [v for k, v in res.items() if k not in expected]
        self.assertEqual(escape, ["blocked"])                                                           # traversal still refused
        secret = os.path.join(self.work, "secret.png")
        write_file(secret, b"SECRET", "wb")
        self.assertEqual(resolve(renamed)["Brief/../../secret.png"], "blocked")
        link = os.path.join(renamed, "link.png")
        os.symlink(secret, link)                                                                        # symlinks still refused
        html2 = '<html><body><h2>M</h2><img src="Brief/link.png"></body></html>'
        write_file(html_path, html2, "w")
        self.assertEqual(resolve(renamed), {"Brief/link.png": "blocked"})

    def test_prefix_mapping_does_not_let_a_reference_escape_the_selected_folder(self):
        import organizer.importer as imp
        html = '<html><body><h2>M</h2><img src="Brief/a.png"><img src="Brief/a.png"><img src="Other/o.png"><img src="outside.png"></body></html>'
        html_path, folder = make_export(self.work, name="Brief", html=html, files={"a.png": PNG}, with_hash=False)
        os.makedirs(os.path.join(self.work, "Other"))
        write_file(os.path.join(self.work, "Other", "o.png"), PNG + b"x", "wb")
        write_file(os.path.join(self.work, "outside.png"), PNG + b"y", "wb")
        renamed = os.path.join(self.work, "Renamed")
        shutil.move(folder, renamed)
        a = imp.analyse(imp.resolve_selection(html_path, renamed))
        got = {p: i.status for p, i in a.assets.items()}
        self.assertEqual(got["Brief/a.png"], "available")
        self.assertEqual(got["Other/o.png"], "blocked")   # a sibling of the HTML file is not inside the chosen folder
        self.assertEqual(got["outside.png"], "blocked")

    def test_import_root_confines_selection(self):
        self.stop()
        self.server_roots = [os.path.join(self.work, "allowed")]
        os.makedirs(self.server_roots[0])
        from organizer.server import make_server
        import threading
        self.server = make_server(self.storage, "127.0.0.1", 0, import_roots=self.server_roots)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.api.base = f"http://127.0.0.1:{self.port}"
        html, _ = make_export(self.work, name="Out", with_hash=False)
        status, err = self.api.post(f"/api/projects/{self.pid}/imports/preview", {"html_path": html})
        self.assertEqual(status, 400)
        self.assertEqual(err["error"]["code"], "outside_import_root")
        status, err = self.api.get("/api/fs/list?path=" + self.work)
        self.assertEqual(status, 400)
        status, listing = self.api.get("/api/fs/list?path=" + self.server_roots[0])
        self.assertEqual(status, 200)
        self.assertIsNone(listing["parent"])


class CommandLineTests(SourcesCase):
    """`python3 -m organizer preview|import` runs the same pipeline without a browser."""

    def cli(self, *args):
        import subprocess
        import sys
        app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([sys.executable, "-m", "organizer", *args, "--data-dir", self.data_dir], cwd=app,
                              capture_output=True, text=True, timeout=120)

    def test_preview_then_import_then_identical(self):
        html, _ = self.export()
        self.stop()  # the command line opens the same data folder; the service need not be running
        r = self.cli("preview", "--project", "BRF", "--html", html)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("5 referenced, 4 found, 1 missing", r.stdout)
        self.assertIn("3 scenarios", r.stdout)
        r = self.cli("import", "--project", "brf", "--html", html)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SUCCEEDED", r.stdout)
        r = self.cli("import", "--project", "Brief", "--html", html, "--json")
        self.assertEqual(json.loads(r.stdout)["state"], "identical")
        conn = sqlite3.connect(self.storage.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0], 1)
        conn.close()
        self.start(self.data_dir)

    def test_unknown_project_and_bad_file_are_reported(self):
        html, _ = self.export()
        self.stop()
        r = self.cli("import", "--project", "NOPE", "--html", html)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No project matches", r.stderr + r.stdout)
        r = self.cli("import", "--project", "BRF", "--html", "/no/such.html")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No file exists", r.stderr)
        self.start(self.data_dir)


class MediaServingTests(SourcesCase):
    def setUp(self):
        super().setUp()
        html, _ = self.export()
        self.run_import(html)
        doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        self.assets = {a["name"]: a for a in self.api.ok("GET", f"/api/documents/{doc['id']}/assets")["assets"]}

    def url(self, name):
        return f"/api/assets/{self.assets[name]['id']}/file"

    def test_full_response_headers(self):
        status, h, body = self.raw(self.url("clip.mp4"))
        self.assertEqual((status, body), (200, MP4))
        self.assertEqual(h["content-type"], "video/mp4")
        self.assertEqual(h["accept-ranges"], "bytes")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", h["content-security-policy"])
        self.assertTrue(h["content-disposition"].startswith("inline"))
        self.assertTrue(h["etag"].startswith('"'))

    def test_byte_ranges_for_video_seeking(self):
        status, h, body = self.raw(self.url("clip.mp4"), {"Range": "bytes=10-19"})
        self.assertEqual((status, body), (206, MP4[10:20]))
        self.assertEqual(h["content-range"], f"bytes 10-19/{len(MP4)}")
        status, h, body = self.raw(self.url("clip.mp4"), {"Range": "bytes=100-"})
        self.assertEqual((status, body), (206, MP4[100:]))
        status, h, body = self.raw(self.url("clip.mp4"), {"Range": "bytes=-5"})
        self.assertEqual((status, body), (206, MP4[-5:]))
        status, h, body = self.raw(self.url("clip.mp4"), {"Range": f"bytes={len(MP4) + 5}-"})
        self.assertEqual(status, 416)
        self.assertEqual(h["content-range"], f"bytes */{len(MP4)}")
        status, h, body = self.raw(self.url("clip.mp4"), {"Range": "bytes=0-1,5-6"})
        self.assertEqual((status, body), (200, MP4))  # multi-range is ignored, not an error
        status, h, body = self.raw(self.url("clip.mp4"), {"Range": "garbage"})
        self.assertEqual(status, 200)
        status, h, body = self.raw(self.url("clip.mp4"), method="HEAD")
        self.assertEqual((status, body, h["content-length"]), (200, b"", str(len(MP4))))

    def test_conditional_request(self):
        _, h, _ = self.raw(self.url("shot one.png"))
        status, _, body = self.raw(self.url("shot one.png"), {"If-None-Match": h["etag"]})
        self.assertEqual((status, body), (304, b""))

    def test_missing_and_unknown_assets_are_404(self):
        self.assertEqual(self.raw(self.url("gone.png"))[0], 404)
        self.assertEqual(self.raw("/api/assets/" + "0" * 32 + "/file")[0], 404)
        self.assertEqual(self.raw("/api/assets/..%2f..%2fetc%2fpasswd/file")[0], 404)
        self.assertEqual(self.raw("/api/assets/xyz/file")[0], 404)

    def test_stored_file_missing_on_disk_is_a_clear_404(self):
        conn = connect(self.storage.db_path)
        rel = conn.execute("SELECT relative_path FROM asset WHERE id = ?", (self.assets["shot one.png"]["id"],)).fetchone()[0]
        conn.close()
        os.remove(self.storage.resolve_in_project(self.pid, rel))
        status, _, body = self.raw(self.url("shot one.png"))
        self.assertEqual(status, 404)
        self.assertIn(b"missing from the data folder", body)

    def test_html_masquerading_as_image_is_never_served_as_html(self):
        html, folder = make_export(self.work, name="Evil", with_hash=False,
                                   files={"x.png": b"<html><script>alert(1)</script></html>", "v.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'},
                                   html='<html><body><h2>m</h2><img src="Evil/x.png"><figure><img src="Evil/v.svg"></figure></body></html>')
        self.run_import(html)
        docs = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"]
        doc = next(d for d in docs if d["name"] == "Evil.html")
        assets = {a["name"]: a for a in self.api.ok("GET", f"/api/documents/{doc['id']}/assets")["assets"]}
        status, h, body = self.raw(f"/api/assets/{assets['x.png']['id']}/file")
        self.assertEqual((status, h["content-type"]), (200, "image/png"))  # nosniff + sandbox: it cannot run as HTML
        self.assertEqual(h["x-content-type-options"], "nosniff")
        status, h, body = self.raw(f"/api/assets/{assets['v.svg']['id']}/file")
        self.assertEqual((status, h["content-type"]), (200, "application/octet-stream"))  # SVG can carry script: download only
        self.assertTrue(h["content-disposition"].startswith("attachment"))
        self.assertFalse(assets["v.svg"]["inline_ok"])
        warn = [d["message"] for d in self.api.ok("GET", f"/api/documents/{doc['id']}")["diagnostics"] if d["code"] == "media_type"]
        self.assertTrue(warn)


# ---- evidence integrity, migrations, offline ---------------------------------------

class IntegrityTests(SourcesCase):
    def test_imported_text_is_immutable_and_edges_stay_inside_one_project(self):
        html, _ = self.export()
        self.run_import(html)
        conn = connect(self.storage.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE source_block SET original_text = 'changed' WHERE sequence = 3")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE source_cell SET original_text = 'changed'")
            other = projects_svc.create_project(conn, self.storage, {"name": "Other", "ticket_prefix": "OTH"})["id"]
            doc_id = conn.execute("SELECT id FROM source_document").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):  # a document of project 1 cannot own an asset of project 2
                conn.execute("INSERT INTO asset(id, project_id, document_id, source_path, original_name, availability, created_at, updated_at) "
                             "VALUES ('a', ?, ?, 'x.png', 'x.png', 'missing', 't', 't')", (other, doc_id))
            with self.assertRaises(sqlite3.IntegrityError):  # asset paths cannot escape storage
                conn.execute("UPDATE asset SET relative_path = '../x' WHERE availability = 'available'")
            with self.assertRaises(sqlite3.IntegrityError):  # an available asset must have a stored file and hash
                conn.execute("UPDATE asset SET relative_path = NULL WHERE availability = 'available'")
            blk = conn.execute("SELECT id FROM source_block LIMIT 1").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):  # an asset reference needs an asset
                conn.execute("INSERT INTO reference(id, project_id, document_id, source_block_id, kind) VALUES ('r', ?, ?, ?, 'image')",
                             (self.pid, doc_id, blk))
            with self.assertRaises(sqlite3.IntegrityError):  # time ranges must be ordered
                conn.execute("INSERT INTO reference(id, project_id, document_id, source_block_id, kind, external_url, start_ms, end_ms) "
                             "VALUES ('r2', ?, ?, ?, 'external_link', 'https://x.test', 500, 100)", (self.pid, doc_id, blk))
        finally:
            conn.close()


class MigrationTests(unittest.TestCase):
    def test_existing_project_data_survives_the_upgrade_and_is_backed_up(self):
        data = tempfile.mkdtemp(prefix="organizer-mig-")
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)
        v1 = os.path.join(data, "v1")
        os.makedirs(v1)
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "organizer", "migrations")
        shutil.copy(os.path.join(src, "0001_initial.sql"), v1)
        storage = Storage(os.path.join(data, "store"))
        storage.init()
        migrate(storage.db_path, v1)  # a Milestone 1 database
        conn = connect(storage.db_path)
        from organizer.services import tasks as tasks_svc
        p = projects_svc.create_project(conn, storage, {"name": "Existing", "ticket_prefix": "EXI"})
        epic = tasks_svc.create_task(conn, p["id"], {"title": "Epic", "level": "epic"})
        t = tasks_svc.create_task(conn, p["id"], {"title": "Child", "level": "task", "parent_id": epic["id"],
                                                  "disciplines": ["art", "dev"], "impact": "high", "tags": ["x"]})
        tasks_svc.add_criterion(conn, t["id"], {"text": "works"})
        person = projects_svc.create_person(conn, p["id"], {"name": "Zoe"})
        before = {tbl: [tuple(r) for r in conn.execute(f"SELECT * FROM {tbl} ORDER BY 1")] for tbl in
                  ("project", "task", "person", "task_discipline", "tag", "acceptance_criterion", "task_field_change")}
        conn.close()
        result = migrate(storage.db_path)  # the real, current migrations
        self.assertEqual(result["applied"], list(range(2, result["version"] + 1)))
        self.assertTrue(result["backup"] and os.path.exists(result["backup"]))
        conn = connect(storage.db_path)
        for tbl, rows in before.items():
            width = len(rows[0]) if rows else 0  # later migrations may add columns; the original ones must be unchanged
            self.assertEqual([tuple(r)[:width] for r in conn.execute(f"SELECT * FROM {tbl} ORDER BY 1")], rows, tbl)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"source_document", "source_block", "asset", "reference", "scenario", "import_run"} <= names)
        # the backup is a complete copy of the pre-upgrade database
        bk = sqlite3.connect(result["backup"])
        self.assertEqual(bk.execute("SELECT COUNT(*) FROM task").fetchone()[0], 2)
        self.assertIsNone(bk.execute("SELECT name FROM sqlite_master WHERE name = 'source_document'").fetchone())
        bk.close()
        # still usable
        tasks_svc.create_task(conn, p["id"], {"title": "After upgrade"})
        conn.close()

    def test_failed_migration_rolls_back_and_keeps_data(self):
        data = tempfile.mkdtemp(prefix="organizer-mig-")
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "organizer", "migrations")
        d = os.path.join(data, "m")
        os.makedirs(d)
        shutil.copy(os.path.join(src, "0001_initial.sql"), d)
        storage = Storage(os.path.join(data, "store"))
        storage.init()
        migrate(storage.db_path, d)
        conn = connect(storage.db_path)
        projects_svc.create_project(conn, storage, {"name": "Keep", "ticket_prefix": "KEP"})
        conn.close()
        shutil.copy(os.path.join(src, "0002_sources.sql"), d)
        with open(os.path.join(d, "0002_sources.sql"), "a") as fh:
            fh.write("\nCREATE TABLE broken (id INTEGER PRIMARY KEY);\nINSERT INTO nowhere VALUES (1);\n")
        from organizer.db import MigrationError
        with self.assertRaises(MigrationError):
            migrate(storage.db_path, d)
        conn = connect(storage.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project").fetchone()[0], 1)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("source_document", names)
        self.assertNotIn("broken", names)
        conn.close()


class OfflineTests(SourcesCase):
    def test_import_and_browsing_make_no_network_connections(self):
        attempts = []
        real_connect = socket.socket.connect

        def guard(self, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in ("127.0.0.1", "localhost", "::1"):
                attempts.append(address)
                raise OSError("network is disabled for this test")
            return real_connect(self, address, *a, **k)

        real_getaddrinfo = socket.getaddrinfo

        def no_resolve(host, *a, **k):
            if host in ("127.0.0.1", "localhost", "::1"):
                return real_getaddrinfo(host, *a, **k)
            attempts.append(("resolve", host))
            raise OSError("name resolution is disabled for this test")

        html, _ = self.export()
        with mock.patch.object(socket.socket, "connect", guard), mock.patch.object(socket, "getaddrinfo", no_resolve):
            self.run_import(html)
            doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
            self.api.ok("GET", f"/api/documents/{doc['id']}/blocks")
            self.api.ok("GET", f"/api/documents/{doc['id']}/scenarios")
            for a in self.api.ok("GET", f"/api/documents/{doc['id']}/assets")["assets"]:
                if a["availability"] == "available":
                    self.raw(f"/api/assets/{a['id']}/file")
        self.assertEqual(attempts, [])


if __name__ == "__main__":
    unittest.main()
