"""Imports the real brief in RawData/ (skipped when the sample is not present).

Set ORGANIZER_SAMPLE_HTML to test another export. Copies ~1 GB into a throw-away data folder.
"""

import glob
import os
import time
import unittest

from helpers import AppCase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sample_html():
    env = os.environ.get("ORGANIZER_SAMPLE_HTML")
    if env:
        return env
    found = sorted(glob.glob(os.path.join(ROOT, "RawData", "*.html")))
    return found[0] if found else None


@unittest.skipUnless(sample_html() and os.path.exists(sample_html()), "RawData sample not present")
class SampleImportTests(AppCase):
    @classmethod
    def setUpClass(cls):
        cls.html = sample_html()

    def setUp(self):
        super().setUp()
        self.pid = self.project("Human Face Log", "HFL")["id"]
        status, run = self.api.post(f"/api/projects/{self.pid}/imports", {"html_path": self.html})
        self.assertEqual(status, 202, run)
        end = time.time() + 240
        while time.time() < end:
            _, s = self.api.get(f"/api/imports/{run['id']}")
            if s["state"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(s["state"], "succeeded", s)
        self.doc = self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"][0]
        self.detail = self.api.ok("GET", f"/api/documents/{self.doc['id']}")
        self.blocks = self.api.ok("GET", f"/api/documents/{self.doc['id']}/blocks?limit=1000")["blocks"]

    def test_all_61_referenced_local_paths_resolve(self):
        assets = self.api.ok("GET", f"/api/documents/{self.doc['id']}/assets")["assets"]
        self.assertEqual(len(assets), 61)
        self.assertEqual({a["availability"] for a in assets}, {"available"})
        kinds = {}
        for a in assets:
            ext = a["name"].rsplit(".", 1)[-1].lower()
            kinds[ext] = kinds.get(ext, 0) + 1
        self.assertEqual(kinds, {"png": 38, "gif": 16, "jpg": 4, "mp4": 3})
        self.assertTrue(all(a["media_type"].split("/")[0] in ("image", "video") for a in assets))
        # names with spaces and parentheses survive percent-decoding
        names = {a["name"] for a in assets}
        self.assertIn("image 1.png", names)
        self.assertIn("0256_(1).jpg", names)
        self.assertEqual(self.detail["counts"]["assets_unavailable"], 0)

    def test_twelve_scenarios_with_identifiers_001_to_012(self):
        sc = self.api.ok("GET", f"/api/documents/{self.doc['id']}/scenarios")["scenarios"]
        self.assertEqual([s["identifier"] for s in sc], [f"{i:03d}" for i in range(1, 13)])
        first = sc[0]
        self.assertEqual(first["title"], "Choose right log → Defend (Put down fence) → Build up territory")
        self.assertEqual(first["preamble"], "⚔️ [Default theme]")
        self.assertEqual([s["label"] for s in first["steps"]][-4:], ["10", "10", "11", "12"])  # repeated number kept, order kept
        self.assertEqual(first["steps"][0]["text"], "Hammer right log")
        self.assertIn("Unlock saw machine", first["description"])
        self.assertEqual(sum(len(s["steps"]) for s in sc), 100)
        self.assertEqual([s["segmentation"] for s in sc[8:]], ["plain"] * 4)
        self.assertEqual(sc[8]["description"], "Video 001")
        self.assertEqual(sc[3]["identifier_raw"].strip(), "004")

    def test_historical_sections_are_kept_flagged_and_searchable(self):
        hist = [s for s in self.detail["sections"] if s["role"] == "historical"]
        self.assertEqual([s["title"] for s in hist], ["1st (01/07)", "2nd (20/09)"])
        in_hist = [b for b in self.blocks if b["section_role"] == "historical"]
        self.assertGreater(len(in_hist), 20)
        hits = self.api.ok("GET", f"/api/documents/{self.doc['id']}/search?q=king%20shot")["hits"]
        self.assertTrue(any(h["section_role"] == "historical" for h in hits))

    def test_heading_paths_and_original_wording(self):
        by_text = {b["text"]: b for b in self.blocks}
        neg = by_text["No build fence animation"]
        self.assertEqual(neg["heading_path"][-1], "3.4. Gameplay Sequences Sample:")
        self.assertEqual(neg["section_role"], "current")
        prices = {t: by_text[t]["heading_path"] for t in ("5 wood logs", "15 wood logs", "25 wood logs", "50 coins", "200 coins",
                                                           "By hand: 10 wood logs", "By saw machine: 40 wood logs")}
        for path in prices.values():
            self.assertEqual(path[-1], "3.5. Implementation information:")
        self.assertIn("Tiếng gấu/skeleton 1 lần mỗi wave", by_text)
        self.assertIn("Z: Hiển thị node archery tower 1 & 2", by_text)

    def test_mp4_attachment_links_are_recognised_as_video(self):
        videos = [b for b in self.blocks if b["type"] == "video"]
        self.assertEqual(len(videos), 3)
        for v in videos:
            self.assertTrue(v["text"].startswith("attachment:"))
            ref = v["references"][0]
            self.assertEqual(ref["asset"]["media_type"], "video/mp4")
            self.assertTrue(ref["asset"]["inline_ok"])
            self.assertFalse(ref["caption"].startswith("attachment:"))

    def test_project_metadata_is_kept_as_a_snapshot_not_copied_to_tasks(self):
        meta = next(s for s in self.detail["sections"] if s["role"] == "metadata")
        props = next(b for b in self.blocks if b["section_id"] == meta["id"] and b["type"] == "table")
        text = {c["text"] for c in props["cells"]}
        self.assertTrue({"Zoe", "60", "80"} <= text or any("Zoe" in t for t in text))
        self.assertEqual(self.api.ok("GET", f"/api/projects/{self.pid}")["task_count"], 0)

    def test_external_links_are_kept_as_links_and_never_fetched(self):
        refs = [r for b in self.blocks for r in b["references"] if r["kind"] == "external_link"]
        self.assertEqual(len(refs), 7)
        hosts = {r["external_url"].split("/")[2] for r in refs}
        self.assertEqual(hosts, {"x-ad-assets.s3.amazonaws.com", "appmagic.rocks", "drive.google.com"})
        notes = " ".join(d["message"] for d in self.detail["diagnostics"])
        self.assertIn("katex", notes)
        self.assertIn("not fetched", notes)

    def test_reimport_of_the_sample_is_identical(self):
        status, run = self.api.post(f"/api/projects/{self.pid}/imports", {"html_path": self.html})
        end = time.time() + 240
        while time.time() < end:
            _, s = self.api.get(f"/api/imports/{run['id']}")
            if s["state"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(s["state"], "identical", s)
        self.assertEqual(len(self.api.ok("GET", f"/api/projects/{self.pid}/documents")["documents"]), 1)
        self.assertEqual(self.api.ok("GET", f"/api/documents/{self.doc['id']}")["counts"]["assets"], 61)


if __name__ == "__main__":
    unittest.main()
