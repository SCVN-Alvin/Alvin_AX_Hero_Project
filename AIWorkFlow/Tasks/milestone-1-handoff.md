# Milestone 1 Handoff: Local Foundation

Delivered for advisor review on 2026-10-31. Milestone 2 has **not** been started.

This is an implementation handoff, not a product review. Nothing in [`Reviews/acceptance-checklist.md`](../Reviews/acceptance-checklist.md) has been ticked; the results below describe what the implementer ran. The advisor's own review report belongs in `Reviews/`.

## Completed tasks

| ID | Task | Status | Where to look |
| --- | --- | --- | --- |
| IMP-001 | Locally runnable application with bundled interface assets | Delivered | `app/run.sh`, `app/static/` |
| IMP-002 | SQLite schema, migrations, project storage, stable ticket keys | Delivered | `app/organizer/migrations/0001_initial.sql`, `db.py`, `services/projects.py` |
| IMP-003 | Manual task and subtask editing with nullable classifications | Delivered | `services/tasks.py`, `static/js/details.js`, `taskform.js` |
| IMP-004 | Kanban columns, status actions, card ordering, filters | Delivered | `static/js/board.js`, `filters.js`, `list.js` |

Review gate: *the board works with manually created data stored in SQLite.* Every board action in the verification below was made in a running browser against the SQLite file; no in-memory or browser-storage data is used for tasks.

## Launch

```sh
cd /Volumes/Supercent/AIXJam/app
./run.sh                 # then open http://localhost:8765/
```

Requires Python 3 only (tested on 3.14.6; nothing to install). Data is stored in `app/data/` (`--data-dir` to change). Full instructions, options, and a usage walkthrough: [`app/README.md`](../../app/README.md).

Suggested review walkthrough (about five minutes):

1. Create a project (for example name "Human Face Log", prefix `LOG`), then create a second project.
2. Create a task with **Art + Dev**, category *Core gameplay*, impact *Critical*, urgency *High*; create an epic, then use **Add task** in its details to add children, and add a subtask to one of them.
3. Drag cards between and within columns; also try the `⋯` menu and, on a focused card, `Alt+Arrow` keys.
4. Filter **Discipline → Dev**, then **Both**; filter by a category that only a subtask has; search for `LOG-2`.
5. Move a parent with unfinished children to Done, and cancel, then confirm.
6. Stop the service with `Ctrl+C`, start it again, and confirm everything is where you left it.

## Evidence against the Milestone 1 completion criteria

| Criterion | Evidence | Result |
| --- | --- | --- |
| IMP-001 *Starts locally and the shell loads without runtime network access* | `ui_check.py` records every request Chrome makes during the whole session; all went to the local origin only. Inter font loads from `/fonts/`. Responses carry a CSP of `default-src 'none'` with `connect-src 'self'`. Launched from `/` through `run.sh`; socket listens on `127.0.0.1` only and a request to the machine's LAN address was refused | Observed. The machine was online during the test, so this shows the app makes no external requests, not that it works with the network physically disabled (see gaps) |
| IMP-002 *Two isolated projects survive restart* | `test_two_projects_are_isolated_and_survive_restart`; UI run restarts the server mid-session and compares column contents and order | Observed |
| IMP-002 *A failed write does not leave partial records* | `test_failed_write_leaves_no_partial_records` and `test_failed_update_rolls_back_every_field` inject a failure after the task, discipline, tag, and ticket-counter writes and confirm nothing remains and no ticket number was consumed. `MigrationTests` confirm a failing migration rolls back, keeps a backup, and leaves prior data intact | Observed |
| IMP-002 *Stable ticket keys* | Keys `LOG-1`, `LOG-2`…; prefix unique, validated, and immutable (also enforced by a database trigger); counter cannot decrease; deleted numbers are not reused (`test_ticket_numbers_are_never_recycled`); 8 threads creating 48 tasks in parallel received numbers 1 to 48 with no gaps or duplicates | Observed |
| IMP-003 *Create, edit, reparent, and reopen a real persisted task* | `test_create_edit_reparent_and_reopen` (API, with restart) and the UI run (create through the dialog, edit title and category in the panel, reparent a subtask, reload after restart) | Observed |
| IMP-003 *Nullable classifications* | Category defaults to Unassigned, impact to Unassessed, urgency to Unspecified, disciplines to none, assignee and due date to null; the UI labels these "Not set" and does not call them manual decisions | Observed |
| IMP-004 *Drag or menu changes persist* | Menu change, synthetic drag events, **native pointer drag** (Chrome drag interception, real `dragstart`/`drop`), and `Alt+Arrow` keyboard moves each verified in the interface. The menu and native-drag moves were also read back through the API, and the final column contents and order were compared after a service restart | Observed |
| IMP-004 *Combined Art/Dev task matches Dev and Both correctly* | `FilterTests.test_dev_includes_combined_and_both_requires_both` and the same check in the UI; a combined task appears once even when Dev, Art, and Both are all selected | Observed |

### Automated results

| Suite | Command | Result |
| --- | --- | --- |
| API, persistence, migrations, hierarchy, dependencies, priority policy, filters, security guards, concurrency | `cd app/tests && python3 -m unittest discover -s . -p "test_*.py"` | 45 tests, all passing |
| Real UI in headless Chrome against a live server | `python3 app/tests/ui_check.py` | 64 of 64 checks passing |

Defects found and fixed along the way, so the advisor knows they existed. Seen in the running app: details-panel contents collapsing in the flex layout; dropping a card into an **empty** column failing in a real browser drag because the handler hid the placeholder under the pointer; header overflow with long error text. Found by review and then fixed with a test where practical: Escape closing the panel while a text field held an unsaved edit; a late save response able to repaint a different task's panel; leaving a task with a failed edit discarding it silently; search not folding non-ASCII case ("đường" versus "Đường"); a rejected request leaving an unread body on a keep-alive connection.

### Performance reading (informational, relates to OPEN-005)

On a 1,500-task fixture (about 20 percent subtasks) on the development machine: board load 21 ms median, filtered board 6 ms, flat list 24 ms, a card move 2 ms. This is a single small synthetic fixture, not a representative brief.

## What was verified, in the interface

Screenshots from the final run are in [`milestone-1-evidence/`](milestone-1-evidence/): [board](milestone-1-evidence/03-board.png), [details](milestone-1-evidence/04-details.png), [parent shown through a matching subtask](milestone-1-evidence/06-filtered-parent.png), [Done with unfinished children](milestone-1-evidence/08-done-guard.png), [failed save with Retry](milestone-1-evidence/09-save-failed.png), [Backlog list](milestone-1-evidence/10-list.png), [narrow layout](milestone-1-evidence/12-narrow-details.png).

Behaviours exercised: no-project and empty-project states; project switcher and isolation; five board columns with visible counts; cards with key, title, Art and Dev badges, priority or *Not assessed*, category, assignee initials, blocked badge and reason, and child progress; expandable children on cards; status menu; drag within and across columns; keyboard moves with a screen-reader announcement and focus kept on the card; search by key; discipline, category and other filters with counts, *No cards match* with Clear filters; parent shown once through a matching subtask with an explanatory note and a "hidden by filter" note on progress; Done guard with remaining children listed and an explicit decision; save failure shown in the header and in the panel with Retry, Discard, and a prompt before navigating away from an unsaved edit; HTML typed into a title or description shown as inert text; settings page; narrow layout (sidebar collapses, board scrolls horizontally, details become a full-width panel).

## Known gaps

Scope not yet built (by plan):

- Brief import, sources, media, rules, candidate review, merge/split, requirement and scenario links, reimport, export/restore and CSV (Milestones 2 to 5). Sidebar entries for Import Review, Sources and Rules and the header Import button are visibly inert, and the details panel states that requirement and reference sections arrive later.
- The **Scenario** filter in the spec cannot exist until scenarios do (Milestone 4). The other six filters and search are in place.

Limits of what was tested:

- Tested with **Google Chrome (headless) only**, on macOS, with Python 3.14.6. Safari, Firefox, other Python versions, and Windows or Linux were not tried.
- The **disconnected-session** acceptance item is not demonstrated: the machine stayed online. Evidence is limited to "no request left the local origin" plus the CSP. A real offline run belongs with the Milestone 5 end-to-end check.
- Keyboard use is verified for board card moves (including focus and the announcement text) and the status menu. Dialogs and form controls use native elements but were not separately keyboard-tested. There was no screen-reader session, no automated contrast measurement, and no full keyboard-only walkthrough of every control.
- Touch input is not specifically supported; the menu and keyboard paths are the alternatives to dragging.

Behaviour worth knowing:

- Single-user model: last write wins per field, with no edit locking. A second tab shows stale data until it is focused again (it refreshes on focus).
- Descriptions and acceptance criteria are plain text. Acceptance criteria can be added, edited, ticked and deleted but not reordered. Sibling subtasks are listed by ticket number, not by a custom order.
- The details history shows the most recent 200 changes.
- Deleting a project removes its database records permanently; only its files go to `data/trash/`. There is no undo in the interface.
- No installer or single-file build; run from the repository folder with Python 3 (OPEN-002 distribution remains open).
- Task and project data cannot yet be exported. Until Milestone 5 the only backup is copying `app/data/` while the service is stopped.

## Product assumptions I made (please confirm or change)

Full text and reasons are in the [decision log](../Decisions/decision-log.md#implementation-decisions-milestone-1), IDEC-001 to IDEC-023. The ones most likely to matter to the user:

1. **Stack** (IDEC-001 to 003): Python standard library service, vanilla JavaScript interface, bundled Inter font, no third-party packages.
2. **Prefixes are unique across projects** and permanent (IDEC-010).
3. **Hierarchy is strict**: epic above task above subtask; a subtask must have a parent (IDEC-011).
4. **Manual tasks start Approved in Backlog** (IDEC-012).
5. **The default priority policy already runs for manual tasks** (IDEC-013), which is slightly ahead of IMP-010. Impact left Unassessed gives *Not assessed*; P0 is only reachable by manual override.
6. **Sidebar "Backlog" is a list of all tasks** at every level with a status quick-filter (IDEC-019). If the user expected only Backlog-status work, this is a one-line default change.
7. **Menu status changes put the card at the top of the target column; new tasks go to the bottom** (IDEC-017).
8. **Dependencies were implemented early** (IDEC-022), including cycle rejection; they never set the Blocked flag.

## Questions for the user or advisor

- Is the working title "Offline Task Organizer" acceptable for now, and should new projects suggest a prefix (as they do now) or leave it blank (OPEN-003)?
- Should the *Backlog* sidebar entry list only Backlog-status work, or everything (assumption 6)?
- Is Chrome the only browser that needs review, or should Safari be covered before Milestone 5?

## Next step

Awaiting review of Milestone 1. Milestone 2 (IMP-005 to IMP-007: managed source and asset import, parsing, sanitized browsing and media preview) will start only after the advisor responds.
