# Offline Task Organizer (Milestone 1)

A local, offline task organizer with SQLite persistence, multiple projects, manual tasks and subtasks, and a Jira-inspired Kanban board. No account, AI model, cloud service, or internet connection is needed.

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
| `--open` | off | Open the browser after starting |

Stop with `Ctrl+C`. The database is created and migrated on first start; restarting recovers everything.

### Data folder

```
data/
  organizer.db          SQLite database (WAL mode)
  organizer.db.bak-*    automatic copies made before a schema migration on an existing database
  projects/<id>/        per-project sources/ and assets/ (used from Milestone 2)
  trash/                folders of deleted projects, kept for recovery
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

Import Review, Sources and Rules are placeholders until Milestones 2–3.

## Tests

```sh
cd app/tests
python3 -m unittest discover -s . -p "test_*.py"   # API, persistence, migrations, filters, guards
python3 ui_check.py [screenshot_dir]                # drives the real UI in headless Chrome
```

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
static/               browser interface (vanilla ES modules, no build step)
  fonts/              bundled Inter variable font (SIL OFL, licence included)
tests/
```

## Offline guarantees (how they are enforced)

- No third-party packages, no CDN: fonts, icons and scripts are served from `static/`.
- Every response carries `Content-Security-Policy: default-src 'none'; script-src 'self'; …; connect-src 'self'`, so the page cannot load anything from another origin.
- The service refuses requests whose `Host` or `Origin` is not the local address, and requires JSON for writes.
- Task text is only ever inserted as text, never as HTML.
