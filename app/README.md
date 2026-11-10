# Offline Task Organizer (Milestones 1 to 3)

A local, offline task organizer with SQLite persistence, multiple projects, manual tasks and subtasks, a Jira-inspired Kanban board, **import of an HTML creative brief with its media** into a browsable, searchable source library, and a **review workflow that turns the brief into approved, classified board tickets** with links back to the exact source text. No account, AI model, cloud service, or internet connection is needed.

Product documentation lives in [`../AIWorkFlow/`](../AIWorkFlow/README.md). This directory is the application.

## Requirements

- Python 3 (developed and tested on **3.14.6**; uses only the standard library, so there is nothing to install)
- A current desktop browser (tested with Google Chrome)

## Launch

```sh
cd app
./run.sh            # or: python3 -m organizer
```

Then open <http://localhost:8765/>.

| Option | Default | Meaning |
| --- | --- | --- |
| `--port N` | `8765` | Port to listen on (`0` picks a free one) |
| `--host H` | `127.0.0.1` | Interface to bind. The default is this computer only. Binding elsewhere prints a warning because the service has no authentication |
| `--data-dir PATH` | `app/data` (or `$ORGANIZER_DATA_DIR`) | Folder for the database and project files |
| `--import-root FOLDER` | any folder you can read | Confine the file picker and imports to this folder (repeat for several) |
| `--open` | off | Open the browser after starting |

Stop with `Ctrl+C`. The database is created and migrated on first start; restarting recovers everything. When an existing database needs a schema upgrade (as when moving from Milestone 1 to 2), a complete copy is written first as `organizer.db.bak-pre-v<version>-<time>` and the upgrade runs in one transaction.

### Data folder

```
data/
  organizer.db          SQLite database (WAL mode)
  organizer.db.bak-*    automatic copies made before a schema migration on an existing database
  projects/<id>/
    sources/<document>/ the imported HTML file, byte for byte
    assets/<xx>/        imported media, one file per distinct content (named by SHA-256)
  trash/                folders of deleted projects, removed imports, and unused media, kept for recovery
```

To back up this milestone's data, stop the service and copy the `data/` folder. (Portable project bundles arrive in Milestone 5.)

## Using it

1. **Create project** — choose a name and a ticket prefix (for example `LOG`, giving keys `LOG-1`, `LOG-2`…). The prefix is permanent and unique; ticket numbers are never reused.
2. **Create task** — type Epic / Task / Subtask, optional parent, description, Art and/or Dev (none = unassigned), category, impact, urgency, status, assignee, due date, tags. Unknown values stay *Unassigned*, *Unassessed*, or *Not assessed*.
3. **Board** — five columns. Move cards by dragging, with the `⋯` menu on each card, or with the keyboard: focus a card and press `Alt+←/→` to change column and `Alt+↑/↓` to reorder. `Enter` opens details.
4. **Details panel** — edits save automatically. A failed save stays visible with **Retry** and **Discard change**; nothing is thrown away silently. Press `Esc` to close.
5. **Filters** — search (title, description, key) and Discipline / Category / Impact / Urgency / Priority / Assignee. Different filters combine with AND, several values in one filter with OR. *Dev* includes combined Art+Dev tasks; *Both* requires both labels. If only a subtask matches, its parent is shown once with a "matches via" note.
6. **Backlog** — a flat list of every task (all levels) with a status quick-filter.
7. **Settings** — project description, local people (assignees), data location, project deletion.



## Importing a brief

1. Click **Import** in the header (or **Sources → Import brief**).
2. Choose the exported `.html` file with **Browse…** (or type its full path). The media folder next to it is found automatically; choose another with the second **Browse…**.
3. **Preview selection** shows what was found and writes nothing: structure, scenarios, historical sections, media found and missing (with reasons), web links, free disk space, and whether the brief was imported before.
4. **Import brief** copies the media into the project and records the document in one step. Progress is shown; the import can be cancelled and nothing is added unless it finishes.
5. **Sources** lists imported briefs. Opening one gives **Content** (outline, original text with heading paths, tables, images, video, search), **Scenarios**, **Media** and **Notes**.

What import keeps and what it does not do:

- Original wording, heading paths, table cells, and numbered steps are stored as written; imported text cannot be edited afterwards. The original HTML file is also kept unchanged.
- Dated or revision-style sections (for example `1st (01/07)`) are marked **historical**: still viewable and searchable, left out of candidate generation by default. Use the `⋯` beside a section in the outline to change this; the choice is stored.
- Page properties (assignee, due date, percentages) are shown as a snapshot and are never copied onto tasks.
- Local files are referenced by their decoded paths. A file that is missing, outside the media folder, a symbolic link, or otherwise unsafe is reported, kept as a reference, and never read. Restoring a missing file and importing the same brief again attaches it.
- Web links are kept and open only when you click them (they need an internet connection). Web images are shown as a placeholder and never loaded.
- Importing the same files again changes nothing. A different HTML file with the same name becomes version 2 beside version 1.
- Nothing becomes a task until you approve it in **Import Review** (see below).

### From a terminal

```sh
python3 -m organizer preview --project HFL --html "../RawData/…html"          # inventory only, writes nothing
python3 -m organizer import  --project HFL --html "../RawData/…html"          # the same import as the interface
python3 -m organizer import  --project HFL --html FILE --assets DIR --data-dir /tmp/try --json
```

`--project` takes the project id, ticket prefix, or exact name. Add `--import-root` to restrict where files may be read from.

## Reviewing a brief into tickets

1. Open a source in **Sources** and press **Review candidates** (or open **Import Review** in the sidebar), then **Generate candidates…**. Current sections are ticked and historical ones are not; tick any you want read.
2. **Candidates** are proposals, grouped as **Tasks**, **Loose requirements** (values and lines not under a task), **Notes**, and **Asset labels**. A task is a labelled group such as *Wood shave*; its bullets are **requirement lines**, not tasks. Lines under a `Scenario 001 + 002` heading keep those scenarios; a line starting with *No…* stays an **exclusion**.
3. Select a candidate. The first view is short: **title and badges**, a **description** you write or edit, and **one main reference clip**. Up to three suggested clips are offered with their section and reason; **Use as main clip** is the only way one becomes the main clip, **More references…** lists every retained suggestion and every picture or video in the document, and **No reference needed** is a recorded choice. For a video you can set a start and end time (the file is never copied or cut). A clip loads only when you press Play.
4. Open **Subtasks**, **Details** (classification with rule evidence, priority, requirement lines, scenarios), **Supporting references** or **Original source** only when you need them. **Compare source** shows the original text beside the task with its lines highlighted.
5. **Needs clarification.** If the source contradicts itself or leaves something open, **Flag a question…** (optionally linked to the lines). Open questions stay visible above the collapsed sections, in the approval dialog and on the ticket until you mark them resolved.
6. **Classify.** Rules suggest Art, Dev, category, impact, or urgency and show the rule and the wording that matched. Accept or reject each suggestion, or set the value yourself. Priority is shown with its explanation; P0 is never suggested.
7. **Organize.** Rename (the source wording stays visible), **Move or change level…** (level and parent change together; invalid moves are refused and change nothing), move a line, make a line a subtask, **split**, **merge** (with a preview, same level only), bulk-classify, or dismiss. A line approved before its task is attached to that task's ticket when the task is approved.
8. **Approve onto the board.** One transaction. Tickets start in Backlog with a link to the exact source blocks, their requirements (quantities as written), scenarios, the main clip with its caption and range, supporting media with their roles, and any open questions.

### Optional AI drafting

Off by default; nothing in the workflow above needs it. **Settings → AI drafting** turns it on and checks the Codex command (`codex --version`, `codex login status`). It uses the Codex CLI signed in with your own ChatGPT account; the organizer never reads, stores or asks for credentials (run `codex login` in a terminal if needed).

Select a task and choose **Draft with AI…** (description only), or tick up to three tasks and choose **Draft with AI…** in the selection bar (descriptions, subtasks, proposals, questions). A preview shows exactly what would be sent: the selected source text, scenario names and reference **captions** only (no pictures, videos, file paths or unrelated sections). Untick anything unrelated, then send. The run continues in the background and belongs to its project; a notice tells you when it finishes. You then choose what to import. Imported work is **needs-review candidates** only; classifications and the main clip stay proposals until you accept them; a description you wrote is kept unless you choose to replace it (earlier versions are restorable). Nothing is approved automatically. **AI drafts** in the Import Review bar lists every run with its state.

**Rules** (sidebar) lists global and project rules. Add a project rule, edit a phrase list, and watch a **preview** of the candidates it would affect before saving. A project rule outranks a global rule; rules that disagree equally are marked Conflicting and nothing is chosen. Changing a rule never overwrites a value you set or a suggestion you accepted or rejected.

Generating again adds only what is new: edits, approvals, dismissals, and rejected media are kept. A source that approved tasks cite cannot be removed.

## Tests

```sh
cd app/tests
python3 -m unittest discover -s . -p "test_*.py"   # API, persistence, migrations, import, path safety, media serving
python3 ui_check.py [screenshot_dir]                # Milestone 1: drives the board in headless Chrome
python3 ui_check_m2.py [screenshot_dir]             # Milestone 2: imports RawData/ through the UI, offline
python3 ui_check_m3.py [screenshot_dir]             # Milestone 3: review, rules and approval of the wood-shaving system
python3 ui_check_rev.py [screenshot_dir]            # M3 corrections and the task-first view (main clip, range, questions, move)
python3 ui_check_ai.py [screenshot_dir]             # optional AI flow with the scripted adapter (nothing is sent anywhere)
```

`test_ai.py` drives the AI service with a scripted adapter and runs the Codex adapter against a stand-in executable (argument array, stdin, working folder, environment, timeout, cancellation). A real Codex request is never part of the automated suite; see the revision handoff for the deliberate synthetic smoke test.

`test_sample_import.py` imports the real brief from `../RawData/` (about 1 GB copied into a temporary folder) and skips itself when it is absent; set `ORGANIZER_SAMPLE_HTML` to test another export. `ui_check_m2.py` starts Chrome with a dead proxy and no DNS so any attempt to reach the internet fails and is reported.

`ui_check.py` needs Google Chrome at `/Applications/Google Chrome.app` (edit `CHROME` in `tests/cdp.py` for another path). It starts its own server on a temporary data folder, so it never touches your data.

## Layout

```
organizer/            Python package
  __main__.py         command line entry
  server.py           HTTP routes, local-only guards, static files
  db.py               connections, transactions, migrations
  migrations/         numbered .sql schema migrations
  services/           projects, people, tasks (all writes are single transactions)
  priority.py         default impact/urgency -> priority policy
  htmlparse.py        tolerant HTML tree builder (depth and size capped)
  sourceparse.py      HTML -> ordered blocks, sections, tables, references
  scenarios.py        scenario table recognition and numbered-step segmentation
  importer.py         path validation, inventory, copy and hash, preview, import runs
  fsbrowse.py         folder listing for the file picker
  candidates.py       grouping source blocks into task, requirement, exclusion, note and asset candidates
  rules.py            classification rule matching, evidence and conflict resolution
  services/sources.py reading, searching, and curating imported documents
  services/candidates.py generation, review actions, approval, task evidence
  services/rules.py   global and project rules, preview
  services/ai.py      AI settings, payload preview, runs, import of selected drafts
  ai/                 payload.py (request), adapters.py (Codex CLI, scripted), validate.py, manager.py (jobs), assets/ (instructions, schema)
static/               browser interface (vanilla ES modules, no build step)
  fonts/              bundled Inter variable font (SIL OFL, licence included)
tests/
```

## Offline guarantees (how they are enforced)

- No third-party packages, no CDN: fonts, icons and scripts are served from `static/`.
- Every response carries `Content-Security-Policy: default-src 'none'; script-src 'self'; …; connect-src 'self'`, so the page cannot load anything from another origin.
- The service refuses requests whose `Host` or `Origin` is not the local address, and requires JSON for writes.
- Task text and imported source text are only ever inserted as text, never as HTML. Imported markup is never rendered: scripts, styles, frames, forms, SVG, event handlers, and `javascript:` links are removed at parse time.
- Imported media is served only by `/api/assets/<id>/file`, with the type taken from the file's content, `nosniff`, a sandboxing policy, and download-only handling for SVG and unknown types.
- The importer opens no network connection; tests block all non-local sockets and name lookups while importing and browsing.
