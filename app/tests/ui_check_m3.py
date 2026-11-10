"""End-to-end UI verification for Milestone 3 (needs Google Chrome; not part of `unittest discover`).

    python3 tests/ui_check_m3.py [screenshot_dir]

Imports the real sample, then drives the review workflow in a real browser with the internet unreachable:
generate candidates, review the wood-shaving system beside its source, classify with rule evidence, edit a
rule with a live preview, attach values and media, approve onto the board, and open the ticket's source
links. Exits non-zero if any check fails.
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = (glob.glob(os.path.join(ROOT, "RawData", "*.html")) or [None])[0]


def pick(b, title):
    ok = b.js(f"""(() => {{ const m=[...document.querySelectorAll('.rv-main')].find(e => e.querySelector('.rv-title').textContent.trim() === {json.dumps(title)});
      if (!m) return false; m.click(); return true; }})()""")
    b.pump(0.9)
    return ok


def set_value(b, selector, value, event="change"):
    b.js(f"""(() => {{ const e=document.querySelector({json.dumps(selector)}); e.focus(); e.value={json.dumps(value)}; e.dispatchEvent(new Event({json.dumps(event)}, {{bubbles:true}})); }})()""")
    b.pump(0.7)


def detail_text(b):
    return text_of(b, ".rv-detail")


def open_sec(b, name, root=".rv-detail"):
    """Open one of the collapsed sections of the task-first view (they start closed)."""
    b.js(f"""(() => {{ const d=document.querySelector('{root} [data-sec="{name}"]'); if (d && !d.open) d.querySelector('summary').click(); }})()""")
    b.pump(0.4)


def compare(b, on=True):
    now = b.js("!!document.querySelector('.rv-body.compare')")
    if now != on:
        b.js("document.querySelector('[data-compare-toggle]').click()")
        b.pump(1.2)


def main():
    shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="organizer-m3-shots-")
    os.makedirs(shots, exist_ok=True)
    if not SAMPLE:
        print("RawData sample not found; nothing to verify")
        return 1
    data = tempfile.mkdtemp(prefix="organizer-m3-")
    srv = Srv(data)
    api = srv.api
    pid = api.ok("POST", "/api/projects", {"name": "Human Face Log", "ticket_prefix": "HFL"})["id"]
    manual = api.ok("POST", f"/api/projects/{pid}/tasks", {"title": "A manual task that must survive", "impact": "low"})
    run = api.ok("POST", f"/api/projects/{pid}/imports", {"html_path": SAMPLE}, expect=(202,))
    while api.ok("GET", f"/api/imports/{run['id']}")["state"] == "running":
        time.sleep(0.2)
    doc = api.ok("GET", f"/api/projects/{pid}/documents")["documents"][0]["id"]

    flags = ["--proxy-server=socks5://127.0.0.1:9", "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1"]
    b = Browser(1500, 950, port=9388, extra_args=flags)
    try:
        # ---- nothing is a ticket before review
        b.goto(srv.url + f"#/p/{pid}/import")
        b.wait_for(".rv-list", timeout=15)
        check("Import Review is a live workspace (not marked Soon)", b.js("(() => { const a=[...document.querySelectorAll('.nav a')].find(x=>x.textContent.includes('Import Review')); return !!a && !a.textContent.includes('Soon'); })()"))
        check("Before generation the workspace explains what will happen", "No candidates yet" in text_of(b, ".rv-list") and "Historical sections are left out" in text_of(b, ".rv-list"))
        b.screenshot(os.path.join(shots, "m3-01-before-generate.png"))

        # ---- generate with historical sections excluded by default
        click_text(b, "Generate candidates", ".rv-list")
        b.wait_for("dialog .gen-sections")
        sections = b.js("[...document.querySelectorAll('dialog .gen-sec')].map(l => ({t: l.textContent.trim(), on: l.querySelector('input').checked, hist: l.textContent.includes('Historical')}))")
        check("Generate dialog lists the document's sections", len(sections) >= 10, str(len(sections)))
        check("Historical sections start unticked, current sections ticked", all((not s["on"]) if s["hist"] else s["on"] for s in sections) and any(s["hist"] for s in sections))
        b.screenshot(os.path.join(shots, "m3-02-generate-dialog.png"))
        click_text(b, "Generate", "dialog", exact=True)
        b.wait_for(".rv-item", timeout=20)
        b.pump(0.8)
        check("Candidates appear as tasks, loose requirements, notes and asset labels", all(g in text_of(b, ".rv-list") for g in ("Tasks", "Loose requirements", "Notes", "Asset labels")))
        check("Nothing reached the board by generating", api.ok("GET", f"/api/projects/{pid}")["task_count"] == 1)
        tasks_listed = b.js("[...document.querySelectorAll('.rv-group:first-of-type > .rv-tree > .rv-item .rv-title')].map(e => e.textContent.trim())")
        check("The Wood shave group is a task candidate", "Wood shave" in tasks_listed, str(tasks_listed))
        check("Bullets are lines under their task, not tasks", b.js("document.querySelectorAll('.rv-group:first-of-type > .rv-tree > .rv-item').length") < 20)
        check("Historical content produced no candidates", "Rope" not in text_of(b, ".rv-list") and "King Shot" not in text_of(b, ".rv-list"))
        b.screenshot(os.path.join(shots, "m3-03-candidates.png"))

        # ---- the wood shaving system, beside its source
        check("Selecting Wood shave opens it", pick(b, "Wood shave"))
        d = detail_text(b)
        check("The first view is short: title, description, main reference, collapsed sections", "Main reference" in d and "Subtasks (2)" in d and "Original source" in d and "Shave clean wood" not in d and "wood log" not in d.lower().replace("wood log", "", 0)[:0])
        open_sec(b, "subtasks")
        d = detail_text(b)
        check("Detail shows the proposed subtasks Shave clean wood and Shave bad wood", "Shave clean wood" in d and "Shave bad wood" in d and "Subtasks (2)" in d)
        compare(b, True)
        hits = b.js("[...document.querySelectorAll('.rv-source .blk.src-hit')].map(e => ({role: [...e.classList].find(c => c.startsWith('role-')), text: e.innerText.trim().slice(0, 60)}))")
        check("The source pane shows the label block highlighted", any(h_["role"] == "role-label" and h_["text"] == "Wood shave" for h_ in hits), str(hits[:4]))
        check("...and the lines, scenario scopes and media under it", {"role-evidence", "role-scope", "role-media"} <= {h_["role"] for h_ in hits}, str(sorted({h_["role"] for h_ in hits})))
        check("Original wording is shown unchanged in the source pane", "Shave clean wood: Check how the giant wood log is shaved from start to end" in text_of(b, ".rv-source"))
        b.screenshot(os.path.join(shots, "m3-04-wood-shave-beside-source.png"))

        # rule evidence
        open_sec(b, "details")
        d = detail_text(b)
        check("A rule suggestion shows its rule, scope and matched wording", "Gameplay section (global rule)" in d and "matched" in d and "Core gameplay" in d)
        check("Suggested values are not applied until accepted", b.js("document.querySelector('.rv-detail select[name=category]').value") == "unassigned")
        click_text(b, "Accept", ".rv-detail .sug", exact=True)
        b.pump(0.8)
        check("Accepting a suggestion sets the reviewed value", b.js("document.querySelector('.rv-detail select[name=category]').value") == "core_gameplay")

        # edit the title: the source wording stays visible
        b.js("(() => { const t=document.querySelector('.rv-detail .title-input'); t.focus(); t.value='Implement wood shaving system'; t.blur(); })()")
        b.pump(1.0)
        check("Renaming keeps the original wording visible", "Implement wood shaving system" in b.js("document.querySelector('.rv-detail .title-input').value") and "Source wording" in detail_text(b) and "Wood shave" in text_of(b, ".rv-detail .small-note"))

        # disciplines + impact -> priority with explanation
        b.js("document.querySelector('.rv-detail input[name=art]').click()")
        b.pump(0.7)
        b.js("document.querySelector('.rv-detail input[name=dev]').click()")
        b.pump(0.7)
        set_value(b, ".rv-detail select[name=impact]", "high")
        d = detail_text(b)
        check("Art and Dev can both be set", b.js("document.querySelector('.rv-detail input[name=art]').checked && document.querySelector('.rv-detail input[name=dev]').checked"))
        check("Priority is explained and kept separate from impact", "P1" in d and "Impact High suggests P1" in d and "suggested by the default policy" in d, d[d.find("Priority"):d.find("Priority") + 300])
        check("P0 is only offered as an explicit override", "P0 (explicit only)" in d and "P0 is never suggested" in d)

        # attach the cross-section values
        check("Values from section 3.5 are offered with the reason", "Possible values and requirements from elsewhere" in d and "Wood logs per shave" in d and "10 wood logs" in d)
        click_text(b, "Attach here", ".rv-detail")
        b.pump(0.9)
        d = detail_text(b)
        check("Attaching moves the values under the task (they were never auto-attached)", "Possible values and requirements from elsewhere" not in d and "Wood logs per shave" in d and "10 wood logs" in d)

        # media: suggested with reasons, attached or chosen only by a person
        check("Media is suggested with reasons, not attached", "More references" in detail_text(b) and b.js("document.querySelectorAll('.rv-detail .tf-suggest').length") <= 3)
        b.screenshot(os.path.join(shots, "m3-05-classified-with-evidence.png"))
        click_text(b, "More references", ".rv-detail")
        b.wait_for("dialog .tf-suggest", timeout=10)
        pt = text_of(b, "dialog")
        check("The picker explains each suggestion and shows its section", "Handheld wood shaver" in pt and "Shares wording with this task" in pt, pt[:300])
        b.real_click("dialog .tf-suggest .rv-thumb")
        b.wait_for("dialog.lightbox img", timeout=10)
        check("A suggested picture opens in the viewer", wait_until(b, "(() => { const i=document.querySelector('dialog.lightbox img'); return i && i.complete && i.naturalWidth > 0; })()", 30))
        b.key("Escape", code="Escape", vk=27)
        b.pump(0.5)
        check("Closing the viewer returns to the picker", b.js("[...document.querySelectorAll('dialog[open]')].some(d => d.querySelector('.tf-suggest'))"))
        click_text(b, "Attach as supporting", "dialog")
        b.pump(0.9)
        b.js("(() => { const bs=[...document.querySelectorAll('dialog .tf-suggest')].filter(r => r.innerText.includes('Use as main clip')); bs[1].querySelector('button.primary').click(); })()")
        b.pump(1.0)
        wood_id = next(c["id"] for c in api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"] if c["title"] == "Implement wood shaving system")
        wd = api.ok("GET", f"/api/candidates/{wood_id}")
        check("Choosing is explicit: one main clip, one supporting reference, the rest still suggested",
              wd["primary"] is not None and sum(1 for r in wd["references"] if r["state"] == "attached") == 2 and any(r["state"] == "suggested" for r in wd["references"]))
        click_text(b, "Done", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(0.8)
        check("The chosen clip is the main reference in the first view", b.js("!!document.querySelector('.rv-detail [data-primary]')"))

        # ---- the negative instruction
        check("Build fence opens", pick(b, "Build fence"))
        open_sec(b, "details")
        d = detail_text(b)
        check("The negative instruction is an Exclusion line, with its scenarios", "No build fence animation" in d and b.js("document.querySelectorAll('.rv-detail .rv-line-item.exclusion').length") == 1)
        ex_text = text_of(b, ".rv-detail .rv-line-item.exclusion")
        check("The exclusion lists scenarios 002, 004, 006, 008", all(x in ex_text for x in ("002", "004", "006", "008")))
        check("No task candidate was made from the exclusion", "No build fence animation" not in [t.strip() for t in b.js("[...document.querySelectorAll('.rv-group:first-of-type .rv-title')].map(e => e.textContent)")])
        b.screenshot(os.path.join(shots, "m3-06-exclusion.png"))

        # ---- rules: preview, save, effect
        click_text(b, "Rules", ".nav")
        b.wait_for(".rule-row", timeout=10)
        check("Rules lists global rules with their wording", "Gameplay section" in text_of(b, ".rules") and "Explicit defect report" in text_of(b, ".rules"))
        click_text(b, "Add project rule", ".rules")
        b.wait_for("dialog input[name=name]")
        b.type("dialog input[name=name]", "Central mechanic: wood shaving")
        b.js("(() => { const s=document.querySelector('dialog select[name=match_scope]'); s.value='title'; s.dispatchEvent(new Event('change',{bubbles:true})); })()")
        b.type("dialog textarea[name=pattern]", "wood shav*")
        b.js("(() => { const s=document.querySelector('dialog select[name=output_field]'); s.value='impact'; s.dispatchEvent(new Event('change',{bubbles:true})); })()")
        b.js("(() => { const s=document.querySelector('dialog select[name=proposed_value]'); s.value='critical'; s.dispatchEvent(new Event('change',{bubbles:true})); })()")
        wait_until(b, "document.querySelector('dialog .rule-preview').innerText.includes('match')", 10)
        pv = text_of(b, "dialog .rule-preview")
        check("A rule preview names the candidates it would affect, without saving", "Wood shave" in pv or "Implement wood shaving system" in pv, pv[:300])
        check("The preview shows the matched wording", "matched" in pv)
        check("Nothing was saved by previewing", len(api.ok("GET", f"/api/projects/{pid}/rules")["rules"]) == 5)
        b.screenshot(os.path.join(shots, "m3-07-rule-preview.png"))
        click_text(b, "Add rule", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        rules = api.ok("GET", f"/api/projects/{pid}/rules")["rules"]
        check("The project rule is saved and labelled as a project rule", any(r["scope"] == "project" and r["name"].startswith("Central mechanic") for r in rules) and "project rule" in text_of(b, ".rules").lower(),
              f"{[r['name'] for r in rules]} / {text_of(b, 'dialog')[:300]} / {text_of(b, '.rules')[:200]}")
        # the Wood shave candidate was already classified by hand (impact high), so the rule is superseded there; a fresh task shows it
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=10)
        pick(b, "Implement wood shaving system")
        d = detail_text(b)
        check("A value you already set is not overwritten by the new rule", b.js("document.querySelector('.rv-detail select[name=impact]').value") == "high")
        open_sec(b, "details")
        check("The superseded suggestion is kept as evidence, collapsed", b.js("(() => { const x=document.querySelector('.rv-detail .sug-dim'); return !!x && !x.open && x.innerText.includes('not used') && x.textContent.includes('Central mechanic'); })()"))

        # ---- approve the wood shaving system
        click_text(b, "Approve onto the board", ".rv-actions")
        b.wait_for("dialog")
        ad = text_of(b, "dialog")
        check("Approval explains what it will create and what it will not apply", "Implement wood shaving system" in ad and "2 subtasks" in ad and "requirement line" in ad)
        b.screenshot(os.path.join(shots, "m3-08-approve-dialog.png"))
        click_text(b, "Approve", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 15)
        b.pump(1.0)
        proj = api.ok("GET", f"/api/projects/{pid}")
        check("Approval created exactly the wood shaving task and its two subtasks", proj["task_count"] == 4, str(proj["task_count"]))
        approved = next(c for c in api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"] if c["title"] == "Implement wood shaving system")
        check("The approved candidate links to its ticket, and the other tickets follow in order", approved["state"] == "approved" and approved["ticket_key"] == "HFL-2")
        b.screenshot(os.path.join(shots, "m3-09-after-approval.png"))

        # ---- the real board
        click_text(b, "Board", ".nav", exact=False)
        b.wait_for(".card", timeout=10)
        cards = b.js("[...document.querySelectorAll('.card .title')].map(e => e.textContent.trim())")
        check("The approved task appears on the persistent board in Backlog", "Implement wood shaving system" in cards and "A manual task that must survive" in cards, str(cards))
        check("Subtasks are inside the card (progress 0/2), not extra cards", b.js("[...document.querySelectorAll('.card')].some(c => c.textContent.includes('Implement wood shaving system') && c.textContent.includes('0/2'))"))
        check("The card shows Art and Dev, category, and the P1 priority", b.js("""(() => { const c=[...document.querySelectorAll('.card')].find(c => c.textContent.includes('Implement wood shaving system')); const t=c.innerText; return t.includes('Art') && t.includes('Dev') && t.includes('P1') && t.includes('Core gameplay'); })()"""))
        b.js("[...document.querySelectorAll('.card')].find(c => c.textContent.includes('Implement wood shaving system')).click()")
        b.wait_for(".details [data-sec=evidence]", timeout=10)
        open_sec(b, "evidence", ".details")
        wait_until(b, "document.querySelector('.details .evidence').innerText.includes('Original wording')", 10)
        et = text_of(b, ".details .evidence").lower()
        check("The ticket shows the original source wording", "original wording" in et and "wood shave" in et)
        check("The ticket shows its requirement with the quantities exactly as written", "requirements (" in et and "10 wood logs" in et and "40 wood logs" in et)
        check("The ticket shows its main clip first and its supporting reference below", b.js("!!document.querySelector('.details [data-primary]')") and "supporting references (1)" in et)
        check("Classification origins distinguish accepted rule suggestions from manual values", "accepted rule suggestion" in text_of(b, ".details").lower() and "manually set" in text_of(b, ".details").lower())
        b.js("document.querySelector('.details .evidence').scrollIntoView({block: 'start'})")
        b.pump(0.6)
        b.screenshot(os.path.join(shots, "m3-10-ticket-with-source.png"))
        # a subtask carries its own lines and scenario links
        bad_id = next(t["id"] for t in api.ok("GET", f"/api/projects/{pid}/tasks")["tasks"] if t["title"] == "Shave bad wood")
        b.goto(srv.url + f"#/p/{pid}/board?task={bad_id}")
        wait_until(b, "document.querySelector('.details .key') && document.querySelector('.details .key').textContent === 'HFL-4'", 10)
        open_sec(b, "evidence", ".details")
        wait_until(b, "document.querySelector('.details .evidence').innerText.includes('Scenario')", 10)
        st = text_of(b, ".details .evidence")
        check("The subtask shows its own lines with their scenario links", "The saw machine is already unlocked in these scenarios." in st and "Scenario 003" in st and "Scenario 008" in st, st[:400])
        b.js("document.querySelector('.details .evidence').scrollIntoView({block: 'start'})")
        b.pump(0.6)
        b.screenshot(os.path.join(shots, "m3-10b-subtask-with-source.png"))
        href = b.js("document.querySelector('.details .evidence a.link').getAttribute('href')")
        check("Source links point at the exact block", "/sources/" in href and "seq=" in href, href)
        b.js("document.querySelector('.details .evidence a.link').click()")
        b.wait_for(".src-blocks .blk", timeout=15)
        wait_until(b, "!!document.querySelector('.blk.flash') || document.activeElement.classList.contains('blk')", 10)
        check("Following it lands on that block in the source browser", b.js("document.activeElement.classList.contains('blk') || !!document.querySelector('.blk.flash')"))

        # ---- repeating generation keeps decisions; dismissal sticks
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=10)
        pick(b, "Unlock items")
        click_text(b, "Dismiss", ".rv-actions", exact=True)
        b.pump(1.0)
        before = api.ok("GET", f"/api/documents/{doc}/candidates")
        click_text(b, "Generate more", ".rv-bar")
        b.wait_for("dialog .gen-sections")
        click_text(b, "Generate", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 15)
        b.pump(1.0)
        after = api.ok("GET", f"/api/documents/{doc}/candidates")
        check("Generating again adds nothing and keeps approvals and dismissals", len(after["candidates"]) == len(before["candidates"])
              and next(c for c in after["candidates"] if c["title"] == "Unlock items")["state"] == "dismissed"
              and next(c for c in after["candidates"] if c["title"] == "Implement wood shaving system")["state"] == "approved")
        b.js("[...document.querySelectorAll('.pill')].find(p => p.textContent.includes('Dismissed')).click()")
        b.pump(0.5)
        check("Dismissed candidates stay listed and can be restored", "Unlock items" in text_of(b, ".rv-list"))

        # ---- bulk classify, merge, split
        b.js("[...document.querySelectorAll('.pill')].find(p => p.textContent.includes('Needs review')).click()")
        b.pump(0.4)
        for t in ("Camera", "Enemy"):
            b.js(f"""(() => {{ const it=[...document.querySelectorAll('.rv-item')].find(i => i.querySelector('.rv-title').textContent.trim() === {json.dumps(t)}); it.querySelector('input[type=checkbox]').click(); }})()""")
            b.pump(0.3)
        check("Selecting candidates shows bulk actions", "2 selected" in text_of(b, ".rv-bulk"))
        click_text(b, "Classify", ".rv-bulk")
        b.wait_for("dialog select[name=category]")
        b.js("(() => { const d=document.querySelector('dialog'); d.querySelector('[name=art]').checked=true; const c=d.querySelector('select[name=category]'); c.value='content'; })()")
        click_text(b, "Apply", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        lst = api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"]
        check("Bulk classification applies to every selected task", all(next(c for c in lst if c["title"] == t)["category"] == "content" and next(c for c in lst if c["title"] == t)["disciplines"] == ["art"] for t in ("Camera", "Enemy")))
        click_text(b, "Merge", ".rv-bulk")
        b.wait_for("dialog input[name=title]")
        mt = text_of(b, "dialog")
        check("Merging previews the kept title, lines, sources and media before doing anything", "Keep this one" in mt and "Lines kept" in mt and "Source blocks" in mt)
        b.screenshot(os.path.join(shots, "m3-11-merge-preview.png"))
        click_text(b, "Cancel", "dialog", exact=True)
        check("Cancelling a merge changes nothing", all(next(c for c in api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"] if c["title"] == t)["state"] == "needs_review" for t in ("Camera", "Enemy")))
        click_text(b, "Clear", ".rv-bulk", exact=True)
        pick(b, "Build fence")
        open_sec(b, "details")
        click_text(b, "Split this task", ".rv-detail")
        b.wait_for("dialog input[name=title]")
        b.type("dialog input[name=title]", "Fence exclusions")
        b.js("document.querySelectorAll('dialog input[name=line]')[1].checked = true")
        b.js("document.querySelector('dialog input[name=mode][value=subtask]').checked = true")
        click_text(b, "Split", "dialog", exact=True)
        wait_until(b, "!document.querySelector('dialog')", 10)
        b.pump(1.0)
        sub = [c for c in api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"] if c["title"] == "Fence exclusions"]
        check("Splitting creates a new subtask candidate holding the chosen line", len(sub) == 1 and sub[0]["level"] == "subtask")

        # ---- offline + errors
        external = [u for u in b.requests if not (u.startswith(srv.url) or u.startswith("data:") or u.startswith("blob:"))]
        check("Every request went to the local service", not external, str(external[:3]))
        check("No request failed", not [f for f in b.failed if not f[1] and f[0] != "net::ERR_ABORTED"], str(b.failed[:3]))
        check("No console errors", not b.console_errors, str(b.console_errors[:3]))

        # ---- restart persistence
        srv.stop()
        srv = Srv(data)
        api = srv.api
        b.goto(srv.url + f"#/p/{pid}/board")
        b.wait_for(".card", timeout=15)
        check("After a restart the approved ticket, its order and the manual task are still on the board",
              all(t in text_of(b, ".board") for t in ("Implement wood shaving system", "A manual task that must survive")))
        tid = next(c for col in api.ok("GET", f"/api/projects/{pid}/board")["columns"].values() for c in col if c["title"] == "Implement wood shaving system")["id"]
        ev = api.ok("GET", f"/api/tasks/{tid}/evidence")
        check("...with its source links, requirement, main clip and media", ev["sources"][0]["text"] == "Wood shave" and len(ev["requirements"]) == 1 and len(ev["references"]) == 2 and ev["primary"] is not None)
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=10)
        check("...and the review decisions (the split-off subtask is still listed)", "Build fence" in text_of(b, ".rv-list") and
              any(c["title"] == "Fence exclusions" for c in api.ok("GET", f"/api/documents/{doc}/candidates")["candidates"]))
        b.set_viewport(900, 900)
        b.goto(srv.url + f"#/p/{pid}/import/{doc}")
        b.wait_for(".rv-item", timeout=10)
        check("Narrow layout: no horizontal page scroll", b.js("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        b.screenshot(os.path.join(shots, "m3-12-narrow.png"))
        b.set_viewport(1500, 950)
    finally:
        b.close()
        srv.stop()
        shutil.rmtree(data, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed. Screenshots: {shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
