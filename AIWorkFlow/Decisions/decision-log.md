# Product Decision Log

This log captures choices established in the planning conversation and technical recommendations for the implementing developer. Update it when implementation or product review changes an assumption.

## Established product direction

| ID | Decision | Reason |
| --- | --- | --- |
| DEC-001 | Operate locally and offline after installation | The user's workflow must not depend on a connection or cloud service |
| DEC-002 | Do not require an AI model in the first release | Use parsing, editable rules, and human review |
| DEC-003 | Represent work as a typed graph | Shared systems span scenarios and have dependencies and evidence |
| DEC-004 | Use a Jira-inspired Kanban interface | The user requested a familiar ticket and board presentation |
| DEC-005 | Support Art, Dev, and combined tasks | The same requirement can need both disciplines |
| DEC-006 | Preserve source details and media alongside simplified tasks | A developer or artist must be able to verify the original requirement |
| DEC-007 | Keep subtasks and shared requirements | Large systems need smaller work items without duplicated shared tickets |
| DEC-008 | Separate impact from urgency | Core importance and time pressure are different decisions |
| DEC-009 | Support multiple projects | Later briefs should fit the same workflow and remain isolated |
| DEC-010 | The assistant acts as advisor and reviewer | Current work is briefs, product review, and suggestions |

## Implementation recommendations

| ID | Recommendation | Reason |
| --- | --- | --- |
| REC-001 | Store typed records and relations in SQLite | Portable local persistence without a dedicated graph database |
| REC-002 | Use a local service and browser interface | Supports HTML processing, local files, media, and durable storage |
| REC-003 | Require review before candidates enter the production board | Rules do not reliably establish semantic meaning |
| REC-004 | Keep Blocked separate from board status | Blocking can occur at several production stages |
| REC-005 | Reserve automatic P0 assignment and numeric weighting for later review | Avoid false precision and unexplained scheduling decisions |
| REC-006 | Store graph relationships now and defer a graph canvas | Relationships are useful before visualization is necessary |
| REC-007 | Preserve manual edits during source and rule reprocessing | Reimport must not erase human decisions |
| REC-008 | Use complete project bundles for backup and transfer | CSV cannot preserve the graph and local assets |

REC-005 means P0 is manual-only in the initial policy. The default P1–P3 mapping is specified in the implementation brief and can change after reviewing actual task results.

## Implementation decisions (Milestone 1)

Recorded by the implementing developer on 2026-10-31. Items marked **Assumption** go beyond or interpret the written requirements and are listed for the advisor and user to confirm or change. The application lives in [`../../app/`](../../app/README.md).

### Stack and packaging (resolves OPEN-001 and OPEN-002 for the first local build)

| ID | Decision | Reason and trade-off |
| --- | --- | --- |
| IDEC-001 | **Python 3 standard library only** for the local service (`http.server`, `sqlite3`); no third-party runtime packages. Developed and tested on Python 3.14.6 | The development machine had no Node. Nothing to install means the offline guarantee holds by construction. Trade-off: `http.server` is a simple threaded server, adequate for one local user but not for exposure to a network |
| IDEC-002 | **Vanilla JavaScript ES modules** in the browser, served as static files; no framework, no bundler, no build step | Removes any build toolchain and runtime dependency. Trade-off: more hand-written UI plumbing than a framework; the structure (`views` as render functions over one store) keeps a later framework migration possible |
| IDEC-003 | **Bundled assets**: Inter variable font (SIL OFL 1.1, licence kept next to it) and a small inline SVG icon set. A Content-Security-Policy (`default-src 'none'`, `connect-src 'self'`) blocks any external resource | Satisfies "bundle fonts and interface dependencies locally" and makes accidental remote loading fail visibly |
| IDEC-004 | **Launch**: `app/run.sh` or `python3 -m organizer` from `app/`; binds `127.0.0.1:8765`. Non-loopback `--host` prints a warning (no authentication). `Host`/`Origin` checks and JSON-only writes protect against other web pages talking to the local service | DEC-001, brief section "Offline operation" |
| IDEC-005 | **Data directory**: `app/data/` by default, overridable with `--data-dir` or `ORGANIZER_DATA_DIR`. Holds `organizer.db`, `projects/<id>/{sources,assets}`, and `trash/`. The folder is git-ignored. Deleting a project moves its folder to `trash/` instead of erasing it | A visible, portable location next to the app; deletion stays recoverable |
| IDEC-006 | **Migrations**: numbered `.sql` files; each runs in one transaction with its version record. Before migrating an existing database a consistent backup copy is written beside it; a database newer than the app is refused | IMP-002 "a failed write does not leave partial records"; brief "failed import or migration leaves the prior project recoverable" |
| IDEC-007 | **IDs** are random UUID4 hex strings; ticket numbers come from a per-project counter that only moves forward, in the same transaction as the insert | Data model "stable IDs"; "never recycle a deleted ticket number" |

### Interpretations of the data model and board spec (Assumptions)

| ID | Assumption | Where the spec was silent or open |
| --- | --- | --- |
| IDEC-010 | **Ticket prefixes are unique across projects** (2–10 characters, letters and digits, starting with a letter) and cannot change | Spec makes the prefix immutable but does not say two projects may share one. Unique prefixes keep keys like `LOG-12` unambiguous and avoid collisions when bundles are restored |
| IDEC-011 | **Hierarchy**: a child's level must rank strictly below its parent's (epic, then task, then subtask), and a subtask must have a parent. Epics are always top level. Tasks may be top level or under an epic | Spec forbids an epic below a task and a subtask containing a task or epic. A parentless subtask has no meaning, so it is refused. The strict ordering also makes hierarchy cycles impossible; the database repeats the rule in triggers |
| IDEC-012 | **Manual tasks are created already approved** (`review_state = approved`, `provenance = manual`) and start in Backlog. The schema default for other creators is `needs_review` | "An approved candidate starts in Backlog"; manual work has no import review step |
| IDEC-013 | **Priority policy is already active for manual tasks**: on every write the suggestion and its rationale are recomputed from impact and urgency using the brief's default mapping (Critical/High to P1, Medium to P2, Low to P3, High urgency promotes one tier up to P1, Unassessed gives no suggestion, P0 never suggested). A manual override and its reason are stored separately and win for display | Brings forward part of IMP-010 so that impact and urgency are meaningful in M1. The stored fields are exactly those of the data model |
| IDEC-014 | **Field origin**: every change is recorded in `task_field_change` with an origin. The details panel shows "Manually set" only for fields the user actually set; untouched defaults read "Not set" | Data model "store field changes with their origin"; avoids claiming a default was a decision |
| IDEC-015 | **Filter semantics**: a discipline selection is a set of predicates joined by OR (Dev matches Dev-only and combined; Art likewise; Both requires both; Unassigned means no discipline). Different dimensions are ANDed. Priority filters use the effective priority (override, else suggestion); *Not assessed* matches tasks with neither | Spec: Dev includes combined, Both requires both, dimensions AND, values OR |
| IDEC-016 | **Parent shown through children**: a top-level card appears if it or any descendant matches. It appears once and states how many subtasks matched, and its progress notes children hidden by the filter. Progress counts direct children | Spec: "show its parent once with a matching-subtask indicator" |
| IDEC-017 | **Card ordering**: ranks are integers with gaps of 1024 per project and status, rebalanced when a gap runs out. Dragging or Alt+Arrow places a card before a named neighbour. A status change from the menu or details places the card at the **top** of the target column; new tasks go to the **bottom** of their column | Spec requires persistent ordering but not where menu moves land |
| IDEC-018 | **Done with unfinished children**: the service refuses with the list of unfinished descendants until the caller confirms. Children are never changed | Spec: "show the remaining work and allow an explicit decision" |
| IDEC-019 | **Sidebar "Backlog"** is a flat list of every approved task of every level with a status quick-filter (default All), not only tasks in the Backlog column | Spec lists Backlog in the sidebar without defining it. Easy to narrow if the user wants only Backlog-status work |
| IDEC-020 | **Deleting a task** is allowed only when it has no children. Its dependency links, criteria, tags links and history go with it; its number is not reused | Data model invariants; avoids orphaning work |
| IDEC-021 | **Concurrency**: last write wins per field (the UI sends only changed fields). No optimistic locking in M1; writes are serialized by SQLite | One local user. Revisit if two browser tabs editing the same task becomes a problem |
| IDEC-022 | **Dependencies were pulled forward**: add/remove with duplicate, self, cross-project, and cycle rejection, shown in details. Requirement, scenario, and reference links remain in later milestones | Data model invariants and the details panel spec; small cost once hierarchy validation existed. Dependency state never sets or clears Blocked |
| IDEC-023 | **Due date** accepts date-only (`YYYY-MM-DD`) or an ISO date-time and stores it unchanged; the UI only writes date-only values | Data model "preserve date-only values as date-only" |

## Open implementation choices

| ID | Choice | When to resolve |
| --- | --- | --- |
| ~~OPEN-001~~ | Resolved by IDEC-001 to IDEC-003 | Done for the first local build; the advisor may still request changes |
| ~~OPEN-002~~ | Resolved by IDEC-004 and IDEC-005 for development use. Distribution beyond running from this folder (an installer or single-file bundle) is still open | Before distributing outside the repository |
| OPEN-003 | Final product name and default project prefix. Interim working title: "Offline Task Organizer". The prefix is chosen per project at creation, with a suggestion derived from the project name | Before the first product handoff |
| OPEN-004 | Rule vocabulary, multilingual phrase lists, and comparison thresholds | During candidate review with the actual sample |
| OPEN-005 | Measured import and board performance targets | After establishing a representative larger fixture |

Creative ambiguities in the sample remain source questions in [RawData analysis](../Findings/raw-data-analysis.md). Resolve them with the project owner; do not turn a parser assumption into a product decision.
