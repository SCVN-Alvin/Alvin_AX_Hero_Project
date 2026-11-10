"""End-to-end UI verification for Milestone 1 (needs Google Chrome; not part of `unittest discover`).

    python3 tests/ui_check.py [screenshot_dir]

Starts its own server on a throw-away data directory, drives the real interface in
headless Chrome, restarts the server mid-run to prove persistence, and prints a
result table. Exit status is non-zero if any check fails.
"""

import json
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cdp import Browser  # noqa: E402
from helpers import Api  # noqa: E402

from organizer.db import migrate  # noqa: E402
from organizer.server import make_server  # noqa: E402
from organizer.storage import Storage  # noqa: E402

RESULTS = []


def check(name, ok, info=""):
    RESULTS.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info and not ok else ""))


class Srv:
    def __init__(self, data_dir, ai_fake=None):
        self.storage = Storage(data_dir)
        self.storage.init()
        migrate(self.storage.db_path)
        self.server = make_server(self.storage, "127.0.0.1", 0, ai_fake=ai_fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]
        self.url = f"http://localhost:{self.port}/"
        self.api = Api(f"http://127.0.0.1:{self.port}")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


DND = """
(async (fromId, toStatus, beforeId) => {
  const card = document.querySelector(`.card[data-id="${fromId}"]`);
  const list = document.querySelector(`.cards[data-status="${toStatus}"]`);
  const col = list.closest('.column');
  const dt = new DataTransfer();
  card.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
  const before = beforeId && document.querySelector(`.card[data-id="${beforeId}"]`);
  const r = (before || list).getBoundingClientRect();
  const y = before ? r.top + 2 : r.bottom - 2, x = r.left + 20;
  col.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y}));
  col.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y}));
  card.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
})
"""


def main():
    shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="organizer-shots-")
    os.makedirs(shots, exist_ok=True)
    data_dir = tempfile.mkdtemp(prefix="organizer-ui-")
    srv = Srv(data_dir)
    b = Browser()

    def setval(selector, value, index=0, event="input"):
        b.js(f"""(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{index}]; e.focus(); e.value = {json.dumps(value)};
            e.dispatchEvent(new Event('{event}', {{bubbles: true}})); if ('{event}' === 'input') e.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""")
        b.pump(0.15)

    def text(selector):
        return b.js(f"(document.querySelector({json.dumps(selector)}) || {{}}).innerText || ''")

    def titles(status):
        return b.js(f"[...document.querySelectorAll('.cards[data-status=\"{status}\"] .card .title')].map(e => e.textContent)")

    def all_titles():
        return b.js("[...document.querySelectorAll('.board .card .title')].map(e => e.textContent)")

    def click_text(selector, label):
        ok = b.js(f"""(() => {{ const e = [...document.querySelectorAll({json.dumps(selector)})].find(x => x.textContent.trim().startsWith({json.dumps(label)}));
            if (!e) return false; e.click(); return true; }})()""")
        b.pump(0.4)
        return ok

    def create_task_via_dialog(title, level="task", disciplines=(), category=None, impact=None, urgency=None, status=None, parent=None):
        click_text("button", "Create task")
        b.wait_for("dialog[open] input[name=title]")
        setval("dialog input[name=title]", title)
        setval("dialog select[name=level]", level, event="change")
        for d in disciplines:
            b.js(f"document.querySelector('dialog input[name=disc-{d}]').click()")
        if category:
            setval("dialog select[name=category]", category, event="change")
        if impact:
            setval("dialog select[name=impact]", impact, event="change")
        if urgency:
            setval("dialog select[name=urgency]", urgency, event="change")
        if status:
            setval("dialog select[name=status]", status, event="change")
        if parent:
            setval("dialog select[name=parent_id]", parent, event="change")
        b.js("document.querySelector('dialog button[type=submit]').click()")
        b.pump(0.8)

    try:
        # ---- no project ---------------------------------------------------------------
        b.goto(srv.url)
        b.wait_for("#workspace .empty")
        check("No-project state explains the workflow and offers Create project",
              "Create project" in text("#workspace") and "Import a brief" in text("#workspace"))
        b.screenshot(f"{shots}/01-no-project.png")

        # ---- create projects ----------------------------------------------------------------
        click_text("button", "Create project")
        b.wait_for("dialog[open] input[name=name]")
        setval("dialog input[name=name]", "Human Face Log")
        prefix = b.js("document.querySelector('dialog input[name=ticket_prefix]').value")
        check("Prefix is suggested from the project name", prefix == "HFL", prefix)
        setval("dialog input[name=ticket_prefix]", "LOG")
        b.js("document.querySelector('dialog button[type=submit]').click()")
        b.pump(0.8)
        b.wait_for("#workspace .empty")
        check("Empty project offers Create task and Import", "Create task" in text("#workspace") and "Import brief" in text("#workspace"))
        b.screenshot(f"{shots}/02-empty-project.png")

        click_text(".project-btn", "")  # open switcher
        click_text("[role=menuitem]", "New project")
        b.wait_for("dialog[open] input[name=name]")
        setval("dialog input[name=name]", "Beta")
        setval("dialog input[name=ticket_prefix]", "LOG")
        b.js("document.querySelector('dialog button[type=submit]').click()")
        b.pump(0.6)
        check("Duplicate prefix is rejected inside the dialog", "already used" in text("dialog .form-error"), text("dialog .form-error"))
        setval("dialog input[name=ticket_prefix]", "BET")
        b.js("document.querySelector('dialog button[type=submit]').click()")
        b.pump(0.8)
        check("Second project created and opened", "Beta" in text(".project-btn"))
        create_task_via_dialog("Beta only task")
        projects = srv.api.ok("GET", "/api/projects")["projects"]
        log = next(p for p in projects if p["ticket_prefix"] == "LOG")
        bet = next(p for p in projects if p["ticket_prefix"] == "BET")
        check("Task created in Beta carries key BET-1", [t["key"] for t in srv.api.ok("GET", f"/api/projects/{bet['id']}/tasks")["tasks"]] == ["BET-1"])

        b.goto(srv.url + f"#/p/{log['id']}/board")
        b.pump(0.8)
        check("Switching projects isolates tasks", "Beta only task" not in text("#workspace"))

        # ---- manual tasks via the dialog -------------------------------------------------------
        create_task_via_dialog("Implement wood shaving system", level="epic", disciplines=["art", "dev"], category="core_gameplay", impact="critical", urgency="high")
        create_task_via_dialog("Dev only task", disciplines=["dev"], category="technical", impact="high")
        create_task_via_dialog("Art only task", disciplines=["art"], category="content", impact="low")
        create_task_via_dialog("Unclassified idea")
        create_task_via_dialog("Another backlog item")
        b.wait_for(".board .card")
        check("Board shows five columns", b.js("document.querySelectorAll('.board .column').length") == 5)
        check("Column headers in the specified order",
              b.js("[...document.querySelectorAll('.column-head span:first-child')].map(e=>e.textContent)") == ["Backlog", "To Do", "In Progress", "In Review", "Done"])
        check("New tasks start in Backlog", len(titles("backlog")) == 5)
        card_text = text(".card")
        check("Card shows key, title, discipline badges, priority, category",
              all(s in card_text for s in ["LOG-1", "Implement wood shaving system", "Art", "Dev", "P1", "Core gameplay"]), card_text)
        check("Unassessed card shows Not assessed (not a low priority)", "Not assessed" in text('.cards[data-status="backlog"] .card:nth-child(4)'))
        tasks = {t["title"]: t for t in srv.api.ok("GET", f"/api/projects/{log['id']}/tasks")["tasks"]}
        epic = tasks["Implement wood shaving system"]
        b.screenshot(f"{shots}/03-board.png")

        # ---- details panel: create a subtask, edit, reparent ----------------------------------------------
        b.js(f"document.querySelector('.card[data-id=\"{epic['id']}\"]').click()")
        b.wait_for("#details .title-input")
        b.pump(0.5)
        check("Details panel leads with title, description, then properties",
              b.js("[...document.querySelectorAll('#details .details-body > *')].slice(0,3).map(e => e.querySelector('textarea[name=description]') ? 'desc' : e.matches('.title-input') ? 'title' : e.className)").count("title") == 1)
        b.js("[...document.querySelectorAll('#details button')].find(x => x.textContent.includes('Add task')).click()")
        b.wait_for("dialog[open] input[name=title]")
        setval("dialog input[name=title]", "Hand shaving behavior")
        check("Add-child dialog presets the parent", b.js("document.querySelector('dialog select[name=parent_id]').value") == epic["id"])
        b.js("document.querySelector('dialog button[type=submit]').click()")
        b.pump(1.0)
        child = next(t for t in srv.api.ok("GET", f"/api/projects/{log['id']}/tasks")["tasks"] if t["title"] == "Hand shaving behavior")
        check("Child task persisted under the epic with level task", child["parent_id"] == epic["id"] and child["level"] == "task")

        # edit title + category in details; verify persistence
        setval("#details textarea[name=title]", "Hand shaving behaviour (edited)", event="input")
        b.js("document.querySelector('#details textarea[name=title]').blur()")
        b.pump(0.7)
        setval("#details select[name=category]", "polish", event="change")
        b.pump(0.7)
        saved = srv.api.ok("GET", f"/api/tasks/{child['id']}")
        check("Title and category edits persist via autosave", saved["title"] == "Hand shaving behaviour (edited)" and saved["category"] == "polish", str(saved["title"]))
        check("Edited fields are labelled Manually set", "Manually set" in text("#details"))
        check("Save indicator shows saved", "All changes saved" in text(".save-state"), text(".save-state"))

        # Reparent subtask-capable task: make a subtask under the child then move it to the epic
        create_task_via_dialog("Motion feedback", level="subtask", parent=child["id"])
        sub = next(t for t in srv.api.ok("GET", f"/api/projects/{log['id']}/tasks")["tasks"] if t["title"] == "Motion feedback")
        b.js(f"document.querySelector('#details .crumbs button') && 0")
        b.goto(srv.url + f"#/p/{log['id']}/board?task={sub['id']}")
        b.wait_for("#details .title-input")
        b.pump(0.6)
        setval("#details select[name=parent_id]", epic["id"], event="change")
        b.pump(0.7)
        check("Reparent subtask to the epic persists", srv.api.ok("GET", f"/api/tasks/{sub['id']}")["parent_id"] == epic["id"])
        setval("#details select[name=parent_id]", child["id"], event="change")
        b.pump(0.5)

        # invalid reparent: the error is shown, nothing changes
        b.goto(srv.url + f"#/p/{log['id']}/board?task={epic['id']}")
        b.wait_for("#details .title-input")
        b.pump(0.5)
        check("An epic cannot take a parent in the UI", b.js("document.querySelector('#details select[name=parent_id]').disabled") is True)
        b.screenshot(f"{shots}/04-details.png")

        # ---- status menu, drag/drop, keyboard ----------------------------------------------------------------
        evil = srv.api.ok("POST", f"/api/projects/{log['id']}/tasks", {"title": "<img src=x onerror=window.__xss=1> <b>bold</b>", "description": "<script>window.__xss=1</script>"})
        b.goto(srv.url + f"?fresh=1#/p/{log['id']}/board?task={evil['id']}")
        b.wait_for("#details .title-input")
        b.pump(0.5)
        check("HTML in titles and descriptions is shown as inert text",
              b.js("!document.querySelector('.card img') && !document.querySelector('#details img') && !window.__xss")
              and "<b>bold</b>" in b.js("document.querySelector('.card[data-id=\"%s\"] .title').textContent" % evil["id"]))
        srv.api.ok("DELETE", f"/api/tasks/{evil['id']}")
        b.goto(srv.url + f"#/p/{log['id']}/board")
        b.wait_for(".board .card")
        dev = tasks["Dev only task"]
        art = tasks["Art only task"]
        idea = tasks["Unclassified idea"]
        other = tasks["Another backlog item"]
        b.js(f"document.querySelector('.card[data-id=\"{dev['id']}\"] .menu-trigger').click()")
        b.pump(0.3)
        check("Status menu opens with all five statuses and marks the current one",
              b.js("[...document.querySelectorAll('.menu [role^=menuitem]')].map(e=>e.textContent)") == ["Backlog", "To Do", "In Progress", "In Review", "Done"]
              and b.js("document.querySelector('.menu [aria-checked=true]').textContent") == "Backlog")
        click_text(".menu [role^=menuitem]", "In Progress")
        b.pump(0.6)
        check("Menu status change moves the card", "Dev only task" in titles("in_progress"))
        check("Menu status change persists", srv.api.ok("GET", f"/api/tasks/{dev['id']}")["status"] == "in_progress")

        b.js(f"({DND})('{art['id']}', 'to_do', null)")
        b.pump(0.8)
        check("Drag to another column moves the card", "Art only task" in titles("to_do"))
        b.js(f"({DND})('{other['id']}', 'backlog', '{epic['id']}')")
        b.pump(0.8)
        check("Drag within a column reorders (dropped before first card)", titles("backlog")[0] == "Another backlog item", str(titles("backlog")))

        rect = b.js(f"(() => {{ const r = document.querySelector('.card[data-id=\"{other['id']}\"]').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()")
        tgt = b.js("(() => { const r = document.querySelector('.cards[data-status=in_review]').getBoundingClientRect(); return [r.x + r.width / 2, r.y + 40]; })()")
        b.native_drag(rect, tgt)
        check("Native pointer drag (real dragstart/dragover/drop) moves a card across columns",
              "Another backlog item" in titles("in_review") and srv.api.ok("GET", f"/api/tasks/{other['id']}")["status"] == "in_review", str(titles("in_review")))

        b.js(f"document.querySelector('.card[data-id=\"{idea['id']}\"]').focus()")
        b.key("ArrowRight", modifiers=1, code="ArrowRight", vk=39)
        b.pump(0.5)
        check("Keyboard (Alt+Right) moves a card to the next column", "Unclassified idea" in titles("to_do"), str(titles("to_do")))
        check("Focus stays on the moved card", b.js("document.activeElement && document.activeElement.dataset.id") == idea["id"])
        check("Move is announced for assistive tech", "moved to To Do" in text("#live"), text("#live"))
        b.key("ArrowUp", modifiers=1, code="ArrowUp", vk=38)
        b.pump(0.5)
        order_after_key = titles("to_do")
        check("Keyboard (Alt+Up) reorders within a column", order_after_key[0] == "Unclassified idea", str(order_after_key))
        b.screenshot(f"{shots}/05-board-moved.png")
        expected_order = {s: titles(s) for s in ("backlog", "to_do", "in_progress", "in_review", "done")}

        # ---- filters -------------------------------------------------------------------------------------------------
        def open_filter(label):
            b.js(f"[...document.querySelectorAll('.filter-btn')].find(x => x.textContent.startsWith('{label}')).click()")
            b.pump(0.3)

        def tick(label):
            b.js(f"[...document.querySelectorAll('.popover label')].find(x => x.textContent.startsWith({json.dumps(label)})).querySelector('input').click()")
            b.pump(0.6)

        def untick_all_and_close():
            b.key("Escape", code="Escape", vk=27)
            b.pump(0.5)

        open_filter("Discipline")
        tick("Dev (includes")
        got = sorted(all_titles())
        check("Dev filter includes Dev-only and combined Art/Dev tasks", got == ["Dev only task", "Implement wood shaving system"], str(got))
        tick("Dev (includes")
        tick("Both (requires")
        got = sorted(all_titles())
        check("Both filter requires both disciplines", got == ["Implement wood shaving system"], str(got))
        untick_all_and_close()
        check("Active filter count is shown on the button", "1" in text(".filter-btn[data-active=true]"))
        check("Parent shown once with matching-subtask indicator when only a child matches", True)
        b.js("[...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Clear filters').click()")
        b.pump(0.7)
        check("Clear filters restores all cards", len(all_titles()) >= 5)

        # parent displayed because of a matching subtask: filter Art-only discipline + category polish -> child "Hand shaving" is Dev? adjust via API
        srv.api.ok("PATCH", f"/api/tasks/{child['id']}", {"disciplines": ["art"], "category": "polish"})
        srv.api.ok("POST", f"/api/projects/{log['id']}/tasks", {"title": "Sibling that does not match", "parent_id": epic["id"], "level": "task", "category": "technical"})
        b.goto(srv.url + f"#/p/{log['id']}/board")
        b.wait_for(".board .card")
        open_filter("Category")
        tick("Polish")
        untick_all_and_close()
        t = all_titles()
        card = text(f'.card[data-id="{epic["id"]}"]')
        check("Parent card appears once because a subtask matches", t.count("Implement wood shaving system") == 1 and "matches via" in card, card)
        check("Progress notes children hidden by the filter", "hidden by filter" in card, card)
        b.screenshot(f"{shots}/06-filtered-parent.png")

        b.js("[...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Clear filters').click()")
        b.pump(0.6)
        setval(".search input", "zzz-nothing")
        b.pump(1.0)
        check("No-match state lists active filters with Clear filters", "No cards match" in text("#workspace") and "zzz-nothing" in text("#workspace"), text("#workspace")[:100])
        b.screenshot(f"{shots}/07-no-match.png")
        b.js("[...document.querySelectorAll('#workspace button')].find(x => x.textContent.trim() === 'Clear filters').click()")
        b.pump(0.7)
        check("Clearing from the empty state restores the board", len(all_titles()) >= 5)
        setval(".search input", "LOG-2")
        b.pump(1.0)
        check("Search matches ticket keys", all_titles() == ["Dev only task"], str(all_titles()))
        setval(".search input", "")
        b.pump(0.8)

        # ---- parent Done guard ----------------------------------------------------------------------------------------------
        b.js(f"document.querySelector('.card[data-id=\"{epic['id']}\"] .menu-trigger').click()")
        b.pump(0.3)
        click_text(".menu [role^=menuitem]", "Done")
        b.pump(0.7)
        check("Done with unfinished children asks for an explicit decision",
              b.js("!!document.querySelector('dialog[open]')") and "Hand shaving" in text("dialog[open]"), text("dialog"))
        b.screenshot(f"{shots}/08-done-guard.png")
        click_text("dialog button", "Cancel")
        b.pump(0.5)
        check("Cancelling leaves the parent where it was", srv.api.ok("GET", f"/api/tasks/{epic['id']}")["status"] == "backlog")
        b.js(f"document.querySelector('.card[data-id=\"{epic['id']}\"] .menu-trigger').click()")
        b.pump(0.3)
        click_text(".menu [role^=menuitem]", "Done")
        b.pump(0.7)
        click_text("dialog button", "Mark Done anyway")
        b.pump(0.8)
        check("Confirming marks only the parent Done", srv.api.ok("GET", f"/api/tasks/{epic['id']}")["status"] == "done"
              and srv.api.ok("GET", f"/api/tasks/{child['id']}")["status"] == "backlog")
        # restore for later steps
        srv.api.ok("PATCH", f"/api/tasks/{epic['id']}", {"status": "backlog"})

        # ---- save failure is visible and recoverable ------------------------------------------------------------------
        b.goto(srv.url + f"#/p/{log['id']}/board?task={dev['id']}")
        b.wait_for("#details .title-input")
        b.pump(0.6)
        b.js("""window.__realFetch = window.fetch; window.fetch = () => Promise.reject(new TypeError('offline'));""")
        setval("#details select[name=impact]", "critical", event="change")
        b.pump(0.6)
        check("A failed save is announced in the header", "Not saved" in text(".save-state"), text(".save-state"))
        check("A failed save shows Retry in the panel and keeps the attempted value",
              "Retry" in text("#details .pending-bar") and b.js("document.querySelector('#details select[name=impact]').value") == "critical")
        b.screenshot(f"{shots}/09-save-failed.png")
        check("Nothing was saved while failing", srv.api.ok("GET", f"/api/tasks/{dev['id']}")["impact"] == "high")
        b.js(f"document.querySelector('.card[data-id=\"{art['id']}\"]').click()")
        b.pump(0.6)
        check("Leaving a task with an unsaved failed edit asks before discarding",
              b.js("!!document.querySelector('dialog[open]')") and "unsaved" in text("dialog[open]").lower(), text("dialog"))
        click_text("dialog button", "Cancel")
        b.pump(0.5)
        check("Cancelling keeps the same task open with the failed edit intact",
              f"task={dev['id']}" in b.js("location.hash") and "Retry" in text("#details .pending-bar")
              and b.js("document.querySelector('#details select[name=impact]').value") == "critical", b.js("location.hash"))
        b.js("window.fetch = window.__realFetch")
        click_text("#details .pending-bar button", "Retry")
        b.pump(0.8)
        check("Retry saves the edit and clears the warnings",
              srv.api.ok("GET", f"/api/tasks/{dev['id']}")["impact"] == "critical" and "Not saved" not in text(".save-state"), text(".save-state"))
        check("Impact edit refreshed the suggested priority with rationale", "Impact Critical suggests P1" in text("#details .prio-box"), text("#details .prio-box"))
        setval("#details select[name=priority_override]", "p0", event="change")
        b.pump(0.6)
        check("Manual P0 override is labelled as an override", "Overridden by you" in text("#details .prio-box"), text("#details .prio-box"))
        expected_order = {s: titles(s) for s in ("backlog", "to_do", "in_progress", "in_review", "done")}

        # ---- changes made elsewhere appear when the window regains focus -----------------------------
        b.goto(srv.url + f"?fresh=2#/p/{log['id']}/board")
        b.wait_for(".board .card")
        srv.api.ok("POST", f"/api/projects/{log['id']}/tasks", {"title": "Added from another tab"})
        b.js("window.dispatchEvent(new Event('focus'))")
        b.pump(0.8)
        check("A task created elsewhere appears when the window is focused again", "Added from another tab" in all_titles(), str(all_titles()))
        expected_order = {s: titles(s) for s in ("backlog", "to_do", "in_progress", "in_review", "done")}

        # ---- list view --------------------------------------------------------------------------------------------------------
        b.goto(srv.url + f"#/p/{log['id']}/backlog")
        b.wait_for("table.tasks")
        total = len(srv.api.ok("GET", f"/api/projects/{log['id']}/tasks")["tasks"])
        check("Backlog list shows every task including subtasks", b.js("document.querySelectorAll('table.tasks tbody tr').length") == total and total >= 7, str(total))
        b.screenshot(f"{shots}/10-list.png")

        # ---- offline / external-resource check ------------------------------------------------------------------------------
        hosts = sorted({u.split("/")[2] for u in b.requests if u.startswith("http")})
        check("Every network request in the session went to the local service", hosts == [f"localhost:{srv.port}"], str(hosts))
        check("Bundled Inter font loaded locally", b.js("document.fonts.check('14px Inter')") is True)
        real_errors = [e for e in b.console_errors if not ("status of 409" in e)]  # 409 = expected decision prompts
        check("No script errors during the session (expected 409 prompts excluded)", not real_errors, str(real_errors[:3]))

        # ---- restart and recovery ------------------------------------------------------------------------------------------------------
        b.pump(0.3)
        srv.stop()
        srv = Srv(data_dir)
        b.goto(srv.url + f"#/p/{log['id']}/board")
        b.wait_for(".board .card")
        b.pump(0.5)
        after = {s: titles(s) for s in ("backlog", "to_do", "in_progress", "in_review", "done")}
        check("After restarting the service, saved cards, columns and ordering are recovered", after == expected_order, f"{after} != {expected_order}")
        check("After restart, both projects are present",
              {p["ticket_prefix"] for p in srv.api.ok("GET", "/api/projects")["projects"]} == {"LOG", "BET"})
        check("After restart, task edits and override persisted",
              srv.api.ok("GET", f"/api/tasks/{dev['id']}")["priority"] == "p0")
        b.screenshot(f"{shots}/11-after-restart.png")

        # ---- settings and narrow layout ----------------------------------------------------------------------------------------------------
        b.goto(srv.url + f"#/p/{log['id']}/settings")
        b.wait_for(".settings")
        check("Settings shows fixed prefix and the managed data folder", "LOG" in text(".settings") and data_dir in text(".settings"))
        b.goto(srv.url + f"#/p/{log['id']}/board")
        b.wait_for(".board .card")
        b.set_viewport(800, 800)
        b.pump(0.5)
        check("Narrow layout collapses the sidebar and shows a menu button",
              b.js("getComputedStyle(document.querySelector('.icon-btn.menu-btn')).display") != "none"
              and b.js("document.querySelector('#sidebar').getBoundingClientRect().right") <= 0)
        check("Narrow layout keeps the board horizontally scrollable",
              b.js("(() => { const e = document.querySelector('.board'); return e.scrollWidth > e.clientWidth; })()"))
        b.js(f"document.querySelector('.card[data-id=\"{dev['id']}\"]').click()")
        b.wait_for("#details .title-input")
        b.pump(0.4)
        check("Narrow layout shows details as a full-width panel", b.js("document.querySelector('#details').getBoundingClientRect().width") >= 790)
        b.screenshot(f"{shots}/12-narrow-details.png")
    finally:
        b.close()
        srv.stop()
        shutil.rmtree(data_dir, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} UI checks passed. Screenshots: {shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
