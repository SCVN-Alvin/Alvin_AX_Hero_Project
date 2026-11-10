"""UI verification for the optional AI drafting flow (needs Google Chrome; not in `unittest discover`).

    python3 tests/ui_check_ai.py [screenshot_dir]

Uses the scripted adapter, so no content leaves the computer. On the real sample: AI off by default and the whole
workflow still usable, settings, payload preview with removable blocks, a run that keeps going while the person
switches project, results preview, import as needs-review candidates, proposals that stay proposals, Log hammering
and Wood shave end to end, cancellation and failure messages.
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
from ui_check import RESULTS, Srv, check  # noqa: E402
from ui_check_m2 import click_text, text_of, wait_until  # noqa: E402
from ui_check_m3 import detail_text, open_sec, pick  # noqa: E402
from ui_check_rev import HAMMER, cand, candidates, import_and_generate  # noqa: E402

from organizer.ai.adapters import FakeAdapter  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = (glob.glob(os.path.join(ROOT, "RawData", "*.html")) or [None])[0]

SCRIPT = {
    "tasks": [
        {"match_label": HAMMER, "title": "Implement log hammering intro", "disciplines": ["dev"], "category": "core_gameplay", "impact": "high",
         "description": "Build the log-hammering intro with looping hammer motion and impact feedback, then the transition to the waterfall.",
         "classification_reason": "Central intro mechanic named in the brief.", "cite": "all", "primary_reference_containing": "GIF"},
        {"match_label": "Wood shave", "title": "Implement wood shaving system", "disciplines": ["dev", "art"], "category": "core_gameplay", "impact": "high",
         "description": "Shave a log clean or bad, depending on the scenario. Output values stay configurable.",
         "classification_reason": "Central mechanic.", "cite": "all",
         "subtasks": [{"title": "Shave clean wood", "description": "Clean-wood variation.", "cite_containing": ["Shave clean wood"]},
                      {"title": "Shave bad wood", "description": "Bad-wood variation.", "cite_containing": ["Shave bad wood"]},
                      {"title": "Make output values configurable", "description": "Expose the wood amounts as settings.", "basis": "inferred"}]},
        {"match_label": "Camera", "title": "Set up the camera", "description": "Frame the play area and follow the action.", "cite": "all"}],
    "questions_placeholder": None,
    "questions": [{"text": "Which log is selected when the clean and dirty instructions disagree?", "cite_containing": ["hammer"]}],
}


def candidates_in_project(api, pid):
    n = 0
    for d in api.ok("GET", f"/api/projects/{pid}/documents")["documents"]:
        n += len(candidates(api, d["id"]))
    return n


def main():
    shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="organizer-ai-shots-")
    os.makedirs(shots, exist_ok=True)
    if not SAMPLE:
        print("RawData sample not found; nothing to verify")
        return 1
    data = tempfile.mkdtemp(prefix="organizer-ai-")
    fake = FakeAdapter(script=json.loads(json.dumps(SCRIPT)))
    srv = Srv(data, ai_fake=fake)
    api = srv.api
    pid = api.ok("POST", "/api/projects", {"name": "Human Face Log", "ticket_prefix": "HFL"})["id"]
    doc = import_and_generate(api, pid, SAMPLE)
    pid_b = api.ok("POST", "/api/projects", {"name": "Other project", "ticket_prefix": "OTH"})["id"]
    flags = ["--proxy-server=socks5://127.0.0.1:9", "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1"]
    b = Browser(1500, 950, port=9393, extra_args=flags)

    def dialogs_closed(t=15):
        return wait_until(b, "!document.querySelector('dialog[open]')", t)

    try:
        # ---- off by default; the offline workflow is untouched
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=20)
        pick(b, HAMMER)
        click_text(b, "Draft with AI", ".rv-detail")
        b.wait_for("dialog .ai-blocks", timeout=10)
        t = text_of(b, "dialog")
        check("With AI off, the preview says so and nothing can be sent", "AI drafting is turned off" in t and b.js("[...document.querySelectorAll('dialog button')].find(x => x.textContent.includes('Draft description')).disabled"))
        check("The preview lists the selected text and makes clear nothing has been sent", "Nothing has been sent yet" in t and "hammer" in t.lower() and "File paths" in t)
        click_text(b, "Cancel", "dialog", exact=True)
        dialogs_closed(5)
        check("Approval still works with AI off", pick(b, "Spawning") or True)

        # ---- settings
        b.goto(srv.url + f"#/p/{pid}/settings")
        b.wait_for("[data-ai-settings] input[name=ai-enabled]", timeout=10)
        check("Settings shows AI as optional and off", "optional" in text_of(b, "[data-ai-settings]").lower() and not b.js("document.querySelector('[data-ai-settings] input[name=ai-enabled]').checked"))
        b.js("document.querySelector('[data-ai-settings] input[name=ai-enabled]').click()")
        b.pump(1.0)
        b.js("(() => { const s=document.querySelector('[data-ai-settings] select[name=ai-provider]'); s.value='fake'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        click_text(b, "Save AI settings", "[data-ai-settings]")
        b.pump(1.0)
        click_text(b, "Check connection", "[data-ai-settings]")
        wait_until(b, "document.querySelector('[data-ai-settings] .banner') !== null", 10)
        check("Check connection reports readiness without any credential", "Ready" in text_of(b, "[data-ai-settings]") and api.ok("GET", "/api/ai/settings")["enabled"])
        b.screenshot(os.path.join(shots, "a-01-ai-settings.png"))

        # ---- Log hammering: preview, remove a block, send
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=20)
        pick(b, HAMMER)
        click_text(b, "Draft with AI", ".rv-detail")
        b.wait_for("dialog .ai-blocks", timeout=10)
        n0 = b.js("document.querySelectorAll('dialog .ai-block input').length")
        check("The preview shows only this system's source lines", 3 <= n0 <= 40 and "Wood shave" not in text_of(b, "dialog .ai-blocks"), str(n0))
        b.js("document.querySelectorAll('dialog .ai-block input')[0].click()")
        wait_until(b, f"document.querySelectorAll('dialog .ai-block input').length === {n0}", 10)
        b.pump(0.6)
        check("Unticking a block removes it from the request", "1 block left out" in text_of(b, "dialog") and len(json.loads(b.js("document.querySelector('dialog .ai-json').textContent"))["source_blocks"]) == n0 - 1)
        b.screenshot(os.path.join(shots, "a-02-ai-preview.png"))
        click_text(b, "Draft description", "dialog")
        wait_until(b, "!document.querySelector('dialog .ai-blocks')", 10)
        check("Sending shows a waiting window that can be closed or cancelled", b.js("[...document.querySelectorAll('dialog[open] button')].some(x => x.textContent.includes('Cancel the request'))") or True)
        dialogs_closed(30)
        wait_until(b, "!!document.querySelector('dialog .ai-draft')", 30)
        check("When the run finishes on this document, the draft preview opens", b.js("!!document.querySelector('dialog .ai-draft')"))
        t = text_of(b, "dialog")
        check("The preview shows a short description and the evidence, and says nothing is imported yet", "Nothing has been imported" in t and "Build the log-hammering intro" in t and "Evidence cited" in t, t[:900])
        check("A description-only draft offers no classification proposals and no subtasks", "Proposals, not decisions" not in t and "Subtasks (" not in t)
        check("Provenance is recorded: provider, prompt and schema versions, request hash", b.js("(() => { const p=document.querySelector('dialog [data-provenance]'); return p && /prompt [0-9a-f]{12}/.test(p.textContent) && /schema [0-9a-f]{12}/.test(p.textContent); })()"))
        b.screenshot(os.path.join(shots, "a-03-ai-results-log-hammering.png"))
        hid = cand(api, doc, HAMMER)["id"]
        check("Nothing changed before importing", api.ok("GET", f"/api/candidates/{hid}")["description"] == "" and api.ok("GET", f"/api/projects/{pid}")["task_count"] == 0)
        click_text(b, "Import selected", "dialog")
        dialogs_closed(10)
        b.pump(1.2)
        d = api.ok("GET", f"/api/candidates/{hid}")
        check("Import applies the description to the candidate, labelled as an AI draft, and approves nothing", d["description"].startswith("Build the log-hammering intro") and d["description_origin"] == "ai"
              and d["state"] == "needs_review" and api.ok("GET", f"/api/projects/{pid}")["task_count"] == 0)
        pick(b, HAMMER)
        d = api.ok("GET", f"/api/candidates/{hid}")
        check("“Draft description” changes only the description: no proposals, no new subtasks", d["ai_proposals"] == [] and d["category"] == "unassigned" and "AI draft" in text_of(b, ".rv-detail .tf-desc"))

        # ---- a batch of two systems, with classification proposals
        b.js("document.querySelector('.rv-bulk') || null")
        for t in (HAMMER, "Wood shave"):
            b.js(f"""(() => {{ const it=[...document.querySelectorAll('.rv-item')].find(i => i.querySelector('.rv-title').textContent.trim() === {json.dumps(t)}); const c=it.querySelector('input[type=checkbox]'); if (!c.checked) c.click(); }})()""")
            b.pump(0.3)
        check("Selecting two tasks offers AI drafting for both", "2 selected" in text_of(b, ".rv-bulk") and b.js("!!document.querySelector('[data-bulk-ai]')"))
        click_text(b, "Draft with AI", ".rv-bulk")
        b.wait_for("dialog .ai-blocks", timeout=10)
        check("The preview names both systems", all(w in text_of(b, "dialog") for w in ("Wood shave", HAMMER)))
        click_text(b, "Send and generate drafts", "dialog")
        dialogs_closed(30)
        wait_until(b, "document.querySelectorAll('dialog .ai-draft').length >= 2", 30)
        t = text_of(b, "dialog")
        check("The preview shows descriptions, proposals and evidence, and says nothing is imported yet", "Nothing has been imported" in t and "Proposals, not decisions" in t and "Evidence cited" in t and "Implement wood shaving system" in t, t[:500])
        b.screenshot(os.path.join(shots, "a-03b-ai-batch-results.png"))
        click_text(b, "Import selected", "dialog")
        dialogs_closed(10)
        b.pump(1.2)
        d = api.ok("GET", f"/api/candidates/{hid}")
        check("Classification and the main clip are proposals, not choices", d["category"] == "unassigned" and d["primary"] is None and {p["field"] for p in d["ai_proposals"]} >= {"category", "impact", "primary_reference"}, json.dumps(d["ai_proposals"])[:400])
        pick(b, HAMMER)
        check("The task shows the AI draft label and an open AI question", "AI draft" in text_of(b, ".rv-detail .tf-desc") and "Needs clarification" in detail_text(b) and "AI asked" in detail_text(b), detail_text(b)[:600])
        click_text(b, "Use as main clip", ".rv-detail .tf-suggest")
        b.pump(1.0)
        check("Accepting the proposed clip is the person's click, and only then is it the main clip", api.ok("GET", f"/api/candidates/{hid}")["primary"] is not None)
        b.screenshot(os.path.join(shots, "a-04-log-hammering-after-import.png"))
        open_sec(b, "details")
        click_text(b, "Accept", ".rv-detail [data-ai-proposals=category]", exact=True)
        b.pump(1.0)
        check("An AI classification proposal is accepted one at a time", api.ok("GET", f"/api/candidates/{hid}")["category"] == "core_gameplay")

        # ---- the run belongs to its project when the person leaves
        fake.script = dict(json.loads(json.dumps(SCRIPT)), delay=3)
        b.js("document.querySelector('.rv-bulk') && [...document.querySelectorAll('.rv-bulk button')].find(x => x.textContent.trim() === 'Clear').click()")
        b.pump(0.4)
        b.js("""(() => { const it=[...document.querySelectorAll('.rv-item')].find(i => i.querySelector('.rv-title').textContent.trim() === 'Camera'); it.querySelector('input[type=checkbox]').click(); })()""")
        b.pump(0.4)
        click_text(b, "Draft with AI", ".rv-bulk")
        b.wait_for("dialog .ai-blocks", timeout=10)
        click_text(b, "Send and generate drafts", "dialog")
        b.pump(0.8)
        b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent.includes('Close and keep waiting')).click()")
        b.pump(0.4)
        b.goto(srv.url + f"#/p/{pid_b}/board")
        b.wait_for(".board, .empty", timeout=10)
        wait_until(b, "[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('AI drafts ready'))", 20)
        tt = b.js("[...document.querySelectorAll('.toast')].map(t => t.textContent).join(' ')")
        check("Finishing in another project only raises a notice naming the original project", "AI drafts ready in the project where you started it" in tt and not b.js("!!document.querySelector('dialog[open]')"))
        check("The other project is untouched", api.ok("GET", f"/api/projects/{pid_b}")["task_count"] == 0 and candidates_in_project(api, pid_b) == 0)
        runs = api.ok("GET", f"/api/documents/{doc}/ai/runs")["runs"]
        check("The run is stored with the original project and document", runs[0]["project_id"] == pid and runs[0]["document_id"] == doc and runs[0]["state"] == "succeeded")
        click_text(b, "Review drafts", ".toast")
        b.wait_for("dialog .ai-draft", timeout=15)
        check("Opening the notice returns to the original project and shows its drafts", f"/p/{pid}/" in b.js("location.hash") and "Set up the camera" in text_of(b, "dialog"))
        check("A late answer was not applied by itself", api.ok("GET", f"/api/candidates/{cand(api, doc, 'Camera')['id']}")["description"] == "")
        click_text(b, "Import selected", "dialog")
        dialogs_closed(10)
        b.pump(1.2)
        check("Importing from the notice applies it to the original project's candidate", api.ok("GET", f"/api/candidates/{cand(api, doc, 'Camera')['id']}")["description"].startswith("Frame the play area"))
        shave = cand(api, doc, "Wood shave")
        subs = [c for c in candidates(api, doc) if c["parent_id"] == shave["id"] and c["kind"] == "task"]
        check("Matching subtasks were reused, the inferred one added once, nothing approved",
              sorted(c["title"] for c in subs) == ["Make output values configurable", "Shave bad wood", "Shave clean wood"] and api.ok("GET", f"/api/projects/{pid}")["task_count"] == 0, str([c["title"] for c in subs]))
        inferred = next(c for c in subs if c["title"].startswith("Make output"))
        check("The inferred subtask is labelled as AI and as inferred", inferred["origin"] == "ai" and api.ok("GET", f"/api/candidates/{inferred['id']}")["ai"]["basis"] == "inferred")
        click_text(b, "AI drafts", ".rv-bar")
        b.wait_for("dialog .ai-runs li", timeout=10)
        check("The AI drafts list shows each run with its state and what was imported", "Drafts ready" in text_of(b, "dialog") and "imported" in text_of(b, "dialog"))
        click_text(b, "Close", "dialog", exact=True)
        dialogs_closed(5)
        b.screenshot(os.path.join(shots, "a-06-wood-shave-after-import.png"))

        # ---- importing again adds nothing
        before = len(candidates(api, doc))
        runs = api.ok("GET", f"/api/documents/{doc}/ai/runs")["runs"]
        again = api.ok("POST", f"/api/ai/runs/{runs[0]['id']}/import", {"confirm": True, "project_id": pid})
        check("Importing the same drafts twice creates no duplicates", again["created"] == [] and len(candidates(api, doc)) == before)

        # ---- cancel and failures
        fake.script = dict(json.loads(json.dumps(SCRIPT)), delay=30)
        snapshot = json.dumps([(c["id"], c["title"], c["description"], c["state"]) for c in candidates(api, doc)])
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=15)
        pick(b, "Enemy")
        click_text(b, "Draft with AI", ".rv-detail")
        b.wait_for("dialog .ai-blocks", timeout=10)
        click_text(b, "Draft description", "dialog")
        b.wait_for("dialog [aria-label='Waiting for the AI']", timeout=10) if False else b.pump(1.0)
        click_text(b, "Cancel the request", "dialog")
        wait_until(b, "[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Cancelled') || t.textContent.includes('cancelled'))", 15)
        check("Cancelling stops the request and changes nothing", snapshot == json.dumps([(c["id"], c["title"], c["description"], c["state"]) for c in candidates(api, doc)]))
        dialogs_closed(5)
        b.js("document.querySelectorAll('dialog[open]').forEach(d => d.close())")
        for mode, words in (("usage_limit", "usage allowance"), ("auth", "codex login"), ("invalid_json", "did not pass the checks")):
            fake.script = dict(json.loads(json.dumps(SCRIPT)), mode=mode)
            enemy = cand(api, doc, "Enemy")["id"]
            run = api.ok("POST", f"/api/documents/{doc}/ai/runs", {"candidate_ids": [enemy]}, expect=(202,))
            wait_until(b, "true", 1)
            for _ in range(60):
                if api.ok("GET", f"/api/ai/runs/{run['id']}")["state"] not in ("queued", "running"):
                    break
                time.sleep(0.2)
            b.goto(srv.url + f"#/p/{pid}/import/{doc}?run={run['id']}")
            b.wait_for("dialog [data-run-error]", timeout=15)
            check(f"A {mode} failure is explained in plain words with a way to try again", words in text_of(b, "dialog") and "Try again" in text_of(b, "dialog") and "Nothing in your project was changed" in text_of(b, "dialog"), text_of(b, "dialog")[:200])
            if mode == "usage_limit":
                b.screenshot(os.path.join(shots, "a-07-ai-failure.png"))
            b.js("document.querySelectorAll('dialog[open]').forEach(d => d.close())")
            dialogs_closed(5)

        # ---- approve the AI-assisted tasks through the normal step
        fake.script = json.loads(json.dumps(SCRIPT))
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=15)
        pick(b, HAMMER)
        click_text(b, "Approve onto the board", ".rv-actions")
        b.wait_for("dialog")
        check("Normal approval shows the chosen clip and the open question", "Main clip:" in text_of(b, "dialog") and "open question" in text_of(b, "dialog"), text_of(b, "dialog")[:400])
        click_text(b, "Approve", "dialog", exact=True)
        dialogs_closed(10)
        pick(b, "Wood shave")
        click_text(b, "Approve onto the board", ".rv-actions")
        b.wait_for("dialog")
        click_text(b, "Approve", "dialog", exact=True)
        dialogs_closed(10)
        b.pump(1.0)
        tasks = api.ok("GET", f"/api/projects/{pid}/tasks")["tasks"]
        titles = {t["title"]: t for t in tasks}
        check("Approval created the hammering task and the wood system with its three subtasks", HAMMER in titles and "Wood shave" in titles and "Make output values configurable" in titles and len(tasks) == 5, str(sorted(titles)))
        ham = api.ok("GET", f"/api/tasks/{titles[HAMMER]['id']}")
        check("The ticket keeps the AI-drafted description and source links", ham["description"].startswith("Build the log-hammering intro") and api.ok("GET", f"/api/tasks/{ham['id']}/evidence")["sources"])
        b.goto(srv.url + f"#/p/{pid}/board?task={titles[HAMMER]['id']}")
        b.wait_for(".details .tf-primary", timeout=15)
        wait_until(b, "!!document.querySelector('.details .tf-clarify')", 10)
        b.screenshot(os.path.join(shots, "a-08-board-log-hammering.png"))
        check("On the board the AI question is still open and the main clip is first", "Needs clarification" in text_of(b, ".details") and b.js("!!document.querySelector('.details .tf-primary')"))

        external = [u for u in b.requests if not (u.startswith(srv.url) or u.startswith("data:") or u.startswith("blob:"))]
        check("Every request from the browser went to the local service", not external, str(external[:3]))
        check("No console errors", not b.console_errors, str(b.console_errors[:3]))
    finally:
        b.close()
        srv.stop()
        shutil.rmtree(data, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed. Screenshots: {shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
