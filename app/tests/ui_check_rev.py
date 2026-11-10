"""UI verification for the Milestone 3 corrections and the task-first view (needs Google Chrome; not in `unittest discover`).

    python3 tests/ui_check_rev.py [screenshot_dir]

On the real sample: Log hammering and Wood shave in the short first view, one explicitly chosen main clip, a flagged
question that stays visible, the combined Move dialog, requirement ownership when a line is approved before its task.
On a small fixture with video files: playback range, caption, approval and restart. Chrome runs with the internet cut off.
"""

import glob
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cdp import Browser  # noqa: E402
from export_fixture import GIF, MP4, PNG, make_export  # noqa: E402
from test_revision import MEDIA_BRIEF  # noqa: E402
from ui_check import RESULTS, Srv, check  # noqa: E402
from ui_check_m2 import click_text, text_of, wait_until  # noqa: E402
from ui_check_m3 import compare, detail_text, open_sec, pick  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = (glob.glob(os.path.join(ROOT, "RawData", "*.html")) or [None])[0]
HAMMER = "Log hammering (~5 seconds)"


def candidates(api, doc):
    return api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"]


def cand(api, doc, title, kind="task"):
    return next(c for c in candidates(api, doc) if c["title"] == title and c["kind"] == kind)


def import_and_generate(api, pid, html):
    run = api.ok("POST", f"/api/projects/{pid}/imports", {"html_path": html}, expect=(202,))
    while api.ok("GET", f"/api/imports/{run['id']}")["state"] == "running":
        time.sleep(0.2)
    doc = api.ok("GET", f"/api/projects/{pid}/documents")["documents"][0]["id"]
    api.ok("POST", f"/api/documents/{doc}/candidates/generate", {})
    return doc


def main():
    shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="organizer-rev-shots-")
    os.makedirs(shots, exist_ok=True)
    if not SAMPLE:
        print("RawData sample not found; nothing to verify")
        return 1
    data = tempfile.mkdtemp(prefix="organizer-rev-")
    work = tempfile.mkdtemp(prefix="organizer-rev-export-")
    srv = Srv(data)
    api = srv.api
    pid = api.ok("POST", "/api/projects", {"name": "Human Face Log", "ticket_prefix": "HFL"})["id"]
    doc = import_and_generate(api, pid, SAMPLE)
    flags = ["--proxy-server=socks5://127.0.0.1:9", "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1"]
    b = Browser(1500, 950, port=9392, extra_args=flags)
    try:
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=20)

        # ---- the short first view
        check("Opening a task does not open the three-pane evidence workspace", pick(b, HAMMER) and not b.js("!!document.querySelector('.rv-body.compare')"))
        d = detail_text(b)
        check("Log hammering shows its description box and one place for the main reference", "Description" in d and "Main reference" in d)
        check("...with at most three suggested clips, not every suggestion", b.js("document.querySelectorAll('.rv-detail .tf-suggest').length") <= 3)
        check("Requirements, scenarios and rule explanations stay collapsed", "Requirement lines" not in d and "Why" not in d and "Priority" not in d and b.js("[...document.querySelectorAll('.rv-detail > .rv-d-scroll > details.sec')].every(x => !x.open)"))
        labels = b.js("[...document.querySelectorAll('.rv-detail .tf-suggest b')].map(e => e.textContent)")
        check("Clips have friendly captions, not upload ids", all(len(l) < 70 and " " in l for l in labels), str(labels))
        b.screenshot(os.path.join(shots, "r-01-log-hammering-first-view.png"))

        # ---- description from the source lines, without inventing prose
        click_text(b, "Start from source lines", ".rv-detail")
        b.wait_for("dialog input[name=line]")
        lines = b.js("[...document.querySelectorAll('dialog input[name=line]')].map(i => i.value)")
        check("Starting content is the source's own lines", len(lines) >= 3 and all(isinstance(x, str) and x for x in lines), str(lines[:2]))
        b.js("document.querySelectorAll('dialog input[name=line]')[0].checked = true; document.querySelectorAll('dialog input[name=line]')[1].checked = true")
        click_text(b, "Put in description", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        hid = cand(api, doc, HAMMER)["id"]
        check("The chosen lines become an editable description", api.ok("GET", f"/api/candidates/{hid}")["description"] == lines[0] + "\n" + lines[1])
        b.js("(() => { const t=document.querySelector('.rv-detail textarea[name=description]'); t.focus(); t.value='Looping hammer motion, impact feedback, and the transition to the waterfall.'; t.blur(); })()")
        b.pump(1.0)
        check("Editing the summary does not touch the source", api.ok("GET", f"/api/candidates/{hid}")["description"].startswith("Looping hammer motion")
              and any("hammer" in s["text"].lower() for s in api.ok("GET", f"/api/candidates/{hid}")["sources"] + [{"text": l} for l in lines]))
        check("The previous wording is kept as an earlier version", len(api.ok("GET", f"/api/candidates/{hid}")["description_versions"]) == 1)

        # ---- an open question stays visible
        click_text(b, "Flag a question", ".rv-detail")
        b.wait_for("dialog textarea[name=text]")
        b.type("dialog textarea[name=text]", "Which log is selected when the clean and dirty instructions disagree?")
        b.js("document.querySelectorAll('dialog input[name=blk]')[0] && (document.querySelectorAll('dialog input[name=blk]')[0].checked = true)")
        click_text(b, "Add question", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        d = detail_text(b)
        check("A flagged question shows as Needs clarification above the collapsed sections", "Needs clarification (1)" in d and "Which log is selected" in d and b.js("document.querySelector('.rv-detail .tf-clarify.has-open') !== null"))
        check("It carries a link to the lines it is about", "Show the lines" in d)
        click_text(b, "Show the lines", ".rv-detail")
        wait_until(b, "!!document.querySelector('.rv-body.compare .blk')", 15)
        check("Showing the lines opens the source beside the task, on demand", b.js("!!document.querySelector('.rv-body.compare')"))
        b.screenshot(os.path.join(shots, "r-02-question-and-compare.png"))
        compare(b, False)
        check("The source pane can be hidden again", not b.js("!!document.querySelector('.rv-body.compare')"))

        # ---- choose the main clip: explicit, one only
        check("Nothing is chosen for the person", b.js("document.querySelector('.rv-detail [data-primary]') === null") and api.ok("GET", f"/api/candidates/{hid}")["primary"] is None)
        click_text(b, "Use as main clip", ".rv-detail")
        b.pump(1.0)
        pd = api.ok("GET", f"/api/candidates/{hid}")
        check("One click chooses it, and it moves into the main reference card", pd["primary"] is not None and b.js("document.querySelectorAll('.rv-detail [data-primary]').length") == 1)
        check("The clip stays unloaded until Play is pressed", b.js("document.querySelectorAll('.rv-detail video, .rv-detail .clip-img').length") == 0 and b.js("!!document.querySelector('.rv-detail .clip-start')"))
        b.screenshot(os.path.join(shots, "r-03-main-clip-chosen.png"))
        click_text(b, "Choose another", ".rv-detail")
        b.wait_for("dialog .tf-suggest")
        b.screenshot(os.path.join(shots, "r-04-more-references.png"))
        check("More references lists the retained suggestions with reasons", b.js("document.querySelectorAll('dialog .tf-suggest').length") >= 4 and "Directly under this task" in text_of(b, "dialog"))
        click_text(b, "Close", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 5)
        click_text(b, "Clear", ".rv-detail .tf-primary", exact=True)
        b.pump(1.0)
        pd = api.ok("GET", f"/api/candidates/{hid}")
        check("Clearing the choice keeps the file attached and does not delete it", pd["primary"] is None and any(r["state"] == "attached" for r in pd["references"]))
        click_text(b, "No reference needed", ".rv-detail")
        b.pump(1.0)
        check("“No reference needed” is an explicit decision that can be undone", api.ok("GET", f"/api/candidates/{hid}")["reference_decision"] == "none" and "No reference needed" in detail_text(b))
        click_text(b, "Choose a reference instead", ".rv-detail")
        b.pump(1.0)
        click_text(b, "Use as main clip", ".rv-detail")
        b.pump(1.0)

        # ---- Wood shave: a system description, variations as subtasks
        check("Wood shave opens", pick(b, "Wood shave"))
        d = detail_text(b)
        check("Its first view is short; the clean and bad variations are a collapsed Subtasks section", "Subtasks (2)" in d and "Shave clean wood" not in d)
        open_sec(b, "subtasks")
        check("Opening Subtasks shows both variations", "Shave clean wood" in detail_text(b) and "Shave bad wood" in detail_text(b))
        b.screenshot(os.path.join(shots, "r-05-wood-shave-first-view.png"))

        # ---- move: level and parent change together (M3 REV 002) and the child check (REV 003)
        pick(b, "Net catch")
        b.js("document.querySelector('.rv-detail .rv-d-head [aria-label^=\"More actions\"]').click()")
        b.pump(0.4)
        click_text(b, "Move or change level", "body")
        b.wait_for("dialog select[name=place]")
        opts = b.js("[...document.querySelectorAll('dialog select[name=place] option')].map(o => o.textContent)")
        check("The move dialog offers top-level, epic and every valid parent in one list", "A top-level task" in opts and "An epic (top level)" in opts and any("A subtask of “Build fence”" == o for o in opts), str(opts[:5]))
        b.screenshot(os.path.join(shots, "r-06-move-dialog.png"))
        # keyboard: the select and the Move button are native controls, so Enter on the focused button submits
        b.js("(() => { const s=document.querySelector('dialog select[name=place]'); const o=[...s.options].find(o => o.textContent === 'A subtask of “Build fence”'); s.value=o.value; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        b.js("[...document.querySelectorAll('dialog button')].find(x => x.textContent.trim() === 'Move').focus()")
        b.press_enter()
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        nc = api.ok("GET", f"/api/candidates/{cand(api, doc, 'Net catch')['id']}")
        check("A top-level task became a subtask in one step, using only the keyboard to submit", (nc["level"], nc["parent_id"]) == ("subtask", cand(api, doc, "Build fence")["id"]))
        b.js("document.querySelector('.rv-detail .rv-d-head [aria-label^=\"More actions\"]').click()")
        b.pump(0.4)
        click_text(b, "Move or change level", "body")
        b.wait_for("dialog select[name=place]")
        b.js("(() => { const s=document.querySelector('dialog select[name=place]'); s.value='task|'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        click_text(b, "Move", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        nc = api.ok("GET", f"/api/candidates/{nc['id']}")
        check("...and back to the top level in one step", (nc["level"], nc["parent_id"]) == ("task", None))
        pick(b, "Wood shave")
        b.js("document.querySelector('.rv-detail .rv-d-head [aria-label^=\"More actions\"]').click()")
        b.pump(0.4)
        click_text(b, "Move or change level", "body")
        b.wait_for("dialog select[name=place]")
        opts = b.js("[...document.querySelectorAll('dialog select[name=place] option')].map(o => o.textContent)")
        check("A task with subtasks is never offered as a subtask", not any(o.startswith("A subtask of") for o in opts) and "has 2 subtasks" in text_of(b, "dialog").replace("It has 2 subtasks", "has 2 subtasks"), str(opts[:4]))
        click_text(b, "Cancel", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 5)
        ws = api.ok("GET", f"/api/candidates/{cand(api, doc, 'Wood shave')['id']}")
        check("Cancelling leaves the candidate unchanged", (ws["level"], ws["parent_id"]) == ("task", None))

        # ---- requirement ownership (M3 REV 001)
        fence = cand(api, doc, "Build fence")
        line = next(c for c in candidates(api, doc) if c["parent_id"] == fence["id"] and c["kind"] == "requirement")
        pick(b, "Build fence")
        b.js(f"""(() => {{ const it=document.querySelector('.rv-item[data-id="{fence['id']}"]'); const t=it.querySelector('.tog'); if (t && t.getAttribute('aria-expanded') === 'false') t.click(); }})()""")
        b.pump(0.6)
        b.js(f"""document.querySelector('.rv-item[data-id="{line['id']}"] .rv-main').click()""")
        b.pump(1.0)
        check("A line under a task says it is approved now and attached later", "Approve this line now" in text_of(b, ".rv-actions"))
        click_text(b, "Approve this line now", ".rv-actions")
        b.wait_for("dialog")
        t = text_of(b, "dialog")
        check("The dialog distinguishes a child line from a shared requirement", "belongs to “Build fence”" in t and "attached to that task's ticket as soon as the task is approved" in t, t[:400])
        b.screenshot(os.path.join(shots, "r-07-approve-line-before-task.png"))
        click_text(b, "Approve", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        check("Approving the line first creates no ticket and no duplicate later", api.ok("GET", f"/api/projects/{pid}")["task_count"] == 0)
        pick(b, "Build fence")
        click_text(b, "Approve onto the board", ".rv-actions")
        b.wait_for("dialog")
        click_text(b, "Approve", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        fence_task = next(t for t in api.ok("GET", f"/api/projects/{pid}/tasks")["tasks"] if t["title"] == "Build fence")
        ev = api.ok("GET", f"/api/tasks/{fence_task['id']}/evidence")
        check("The earlier-approved line is on its ticket with its scenarios and source", any(r["text"] == line["original_text"] and r["scenarios"] and r["blocks"] for r in ev["requirements"]),
              str([r["text"][:30] for r in ev["requirements"]]))

        # ---- approve Log hammering and read it on the board
        pick(b, HAMMER)
        click_text(b, "Approve onto the board", ".rv-actions")
        b.wait_for("dialog")
        t = text_of(b, "dialog")
        check("The approval dialog says which clip is primary and that the question stays", "Main clip:" in t and "open question" in t, t[:500])
        b.screenshot(os.path.join(shots, "r-08-approve-dialog.png"))
        click_text(b, "Approve", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        ham = next(t for t in api.ok("GET", f"/api/projects/{pid}/tasks")["tasks"] if t["title"] == HAMMER)
        b.goto(srv.url + f"#/p/{pid}/board?task={ham['id']}")
        b.wait_for(".details [data-task-first] .tf-primary", timeout=15)
        dt = text_of(b, ".details")
        check("On the board the description, open question and main clip come first", b.js("document.querySelector('.details textarea[name=description]').value").startswith("Looping hammer motion") and "Needs clarification (1)" in dt and b.js("!!document.querySelector('.details .tf-primary')"))
        check("The source and supporting references are collapsed by default", b.js("[...document.querySelectorAll('.details details.sec')].filter(x => x.dataset.sec === 'evidence').every(x => !x.open)"))
        b.js("document.querySelector('.details [data-task-first]').scrollIntoView({block: 'start'})")
        b.pump(0.5)
        b.screenshot(os.path.join(shots, "r-09-board-task-first.png"))

        # ---- the fixture with real video files: range, caption, approval, restart
        pid2 = api.ok("POST", "/api/projects", {"name": "Clips", "ticket_prefix": "CLP"})["id"]
        path, _ = make_export(work, name="Clips", html=MEDIA_BRIEF, files={"hammer.mp4": MP4, "spin.gif": GIF, "model.png": PNG, "shave.mp4": MP4 + b"s"}, with_hash=False)
        doc2 = import_and_generate(api, pid2, path)
        b.goto(srv.url + f"#/p/{pid2}/import/{doc2}")
        b.wait_for(".rv-item", timeout=15)
        pick(b, "Log hammering")
        click_text(b, "Use as main clip", ".rv-detail")
        b.pump(1.0)
        check("A video can be chosen as the main clip", b.js("!!document.querySelector('.rv-detail [data-primary]')"))
        click_text(b, "Set range", ".rv-detail")
        b.wait_for("dialog input[name=start]")
        b.type("dialog input[name=start]", "0:01.5")
        b.type("dialog input[name=end]", "0:04")
        click_text(b, "Save range", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        h2 = cand(api, doc2, "Log hammering")["id"]
        pr = api.ok("GET", f"/api/candidates/{h2}")["primary"]
        check("A playback range is saved on the reference, not by copying the video", (pr["start_ms"], pr["end_ms"]) == (1500, 4000) and "0:01.5 – 0:04" in text_of(b, ".rv-detail"))
        click_text(b, "Change range", ".rv-detail")
        b.wait_for("dialog input[name=start]")
        b.type("dialog input[name=start]", "0:05")
        b.type("dialog input[name=end]", "0:02")
        click_text(b, "Save range", "dialog", exact=True)
        check("An end before the start is refused with a message", "end must be after the start" in text_of(b, "dialog"))
        click_text(b, "Cancel", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 5)
        click_text(b, "Rename", ".rv-detail .tf-primary", exact=False)
        b.wait_for("dialog input[name=caption]")
        b.type("dialog input[name=caption]", "Hammer motion and log impact")
        click_text(b, "Save", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 5)
        b.pump(1.0)
        check("The caption is editable without renaming the file", "Hammer motion and log impact" in text_of(b, ".rv-detail .tf-primary")
              and "hammer.mp4" in b.js("document.querySelector('.rv-detail .tf-file').textContent")
              and api.ok("GET", f"/api/candidates/{h2}")["primary"]["asset"]["name"] == "hammer.mp4")
        idle = b.js("document.querySelectorAll('.rv-detail .tf-primary video').length") == 0
        b.js("window.__vid = []; new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => n.tagName === 'VIDEO' && window.__vid.push(n.getAttribute('src'))))).observe(document.body, {childList: true, subtree: true})")
        b.real_click(".rv-detail .clip-start")
        wait_until(b, "window.__vid.length > 0", 10)
        srcs = b.js("window.__vid")
        check("The player is created only when Play is pressed, from the local service", idle and srcs and srcs[0].startswith("/api/assets/"), str(srcs))
        b.screenshot(os.path.join(shots, "r-10-video-main-clip.png"))
        pick(b, "Log hammering")
        click_text(b, "Approve onto the board", ".rv-actions")
        b.wait_for("dialog")
        click_text(b, "Approve", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        tid = next(t for t in api.ok("GET", f"/api/projects/{pid2}/tasks")["tasks"] if t["title"] == "Log hammering")["id"]

        external = [u for u in b.requests if not (u.startswith(srv.url) or u.startswith("data:") or u.startswith("blob:"))]
        check("Every request went to the local service", not external, str(external[:3]))
        check("No console errors", not b.console_errors, str(b.console_errors[:3]))

        srv.stop()
        srv = Srv(data)
        api = srv.api
        b.goto(srv.url + f"#/p/{pid2}/board?task={tid}")
        b.wait_for(".details .tf-primary", timeout=15)
        ev = api.ok("GET", f"/api/tasks/{tid}/evidence")
        check("After a restart the main clip, caption and range are still on the task", (ev["primary"]["start_ms"], ev["primary"]["end_ms"], ev["primary"]["label"]) == (1500, 4000, "Hammer motion and log impact")
              and "0:01.5 – 0:04" in text_of(b, ".details .tf-primary"))
        check("...and the supporting media keep their roles", {r["role"] for r in ev["references"]} <= {"primary_demo", "supporting_demo", "asset_reference", "source_evidence"})
        b.goto(srv.url + f"#/p/{pid}/board?task={ham['id']}")
        b.wait_for(".details [data-task-first]", timeout=15)
        wait_until(b, "!!document.querySelector('.details .tf-clarify')", 10)
        check("After a restart the open question is still on the ticket", "Needs clarification (1)" in text_of(b, ".details"))
        b.set_viewport(900, 900)
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=10)
        pick(b, "Wood shave")
        check("Narrow layout: no horizontal page scroll", b.js("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        b.screenshot(os.path.join(shots, "r-11-narrow.png"))
    finally:
        b.close()
        srv.stop()
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed. Screenshots: {shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
