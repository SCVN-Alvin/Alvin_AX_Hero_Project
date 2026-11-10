# Implementation Delivery Plan

These tasks build the organizer itself. IDs use the `IMP` prefix for the core application and `AI` for the optional AI pilot, so they cannot be confused with creative tickets imported into the finished application. Milestone 1 is delivered; the Milestone 2 media-folder regression now passes. Milestone 3 requires approval, hierarchy, and presentation corrections. The user approved adding AI integration to the plan on 2026-11-03. The AI pilot and Milestones 4 and 5 are unstarted.

Each milestone should produce a runnable local increment, verification results, and known limitations. The advisor reviews behavior against the linked requirements before suggesting the next scope change.

## Milestone 1 Local foundation

**Status: delivered for advisor review on 2026-10-31.** IMP-001 to IMP-004 are implemented in `../../app/`. Launch instructions, verification results, known gaps, and changed assumptions are in the [Milestone 1 handoff](milestone-1-handoff.md). Not yet reviewed or accepted.

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-001 | Establish a locally runnable application with bundled interface assets | None | Starts locally and the shell loads without runtime network access |
| IMP-002 | Implement SQLite schema, migrations, project storage, and stable ticket keys | IMP-001 | Two isolated projects survive restart; a failed write does not leave partial records |
| IMP-003 | Implement manual task and subtask editing with nullable classifications | IMP-002 | Create, edit, reparent, and reopen a real persisted task |
| IMP-004 | Implement Kanban columns, status actions, card ordering, and filters | IMP-003 | Drag or menu changes persist; a combined Art/Dev task matches Dev and Both correctly |

Review gate: the board works with manually created data stored in SQLite. An in-memory or browser-storage demonstration does not satisfy this milestone.

## Milestone 2 Source import

**Status: conditionally accepted on 2026-11-01; follow-up regression verified on 2026-11-03.** IMP-005 to IMP-007 are implemented in `../../app/`. The supplied sample works in the source and media views. M2 REV 001 from the [earlier review](../Reviews/2026-11-01-milestone-2-review.md) is fixed and its regression checks pass in the new 162-test run; the previously intermittent concurrency test also passes in that run. The [Milestone 2 handoff](milestone-2-handoff.md) retains launch instructions and known limits. See the [Milestone 3 review](../Reviews/2026-11-03-milestone-3-review.md) for current verification scope.

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-005 | Implement managed source and asset import with path validation and hashing | IMP-002 | Import the sample, resolve its 61 referenced local asset paths, and report a deliberately missing file |
| IMP-006 | Parse ordered source blocks, heading paths, tables, historical sections, and attachment links | IMP-005 | Inspect the sample's 12 scenario rows and retained original wording |
| IMP-007 | Implement sanitized source browsing and local image, GIF, and video previews | IMP-006 | Inspect map images and play a local video while disconnected |

Review gate: the source browser shows actual imported content, preserves historical context, and makes no automatic remote requests.

## Milestone 3 Candidate generation and review

**Status: reviewed on 2026-11-03; corrections and the task-first view delivered 2026-11-04 for re-review (see the [revision handoff](milestone-3-revision-handoff.md)); not yet accepted.** The [advisor review](../Reviews/2026-11-03-milestone-3-review.md) confirms 162 passing existing tests and successful normal flows, but reproduces M3 REV 001 through 003 involving requirement ownership and hierarchy editing. Fix those and implement the [task presentation refinement](../Design/task-first-review-flow.md) before broader Milestone 4 expansion. IMP-008 to IMP-011 and M2 REV 001 are delivered; the [handoff](milestone-3-handoff.md) retains implementer instructions and reported results. Optional AI drafting is proposed separately and is not required for acceptance.

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-008 | Generate section-based task and requirement candidates | IMP-006 | Wood shaving candidates retain source text, scenario restrictions, and resource values |
| IMP-009 | Implement editable global and project rules with evidence and conflicts | IMP-008 | Preview and change a rule; unclear fields remain unassigned |
| IMP-010 | Implement priority suggestions with impact and urgency kept separate | IMP-009 | Default mapping and overrides have visible explanations; P0 is never selected automatically |
| IMP-011 | Implement split review, bulk classification, approval, and dismissal | IMP-007, IMP-009, IMP-010 | Review a source section, edit its candidates, and approve selected tasks onto the board |

Review gate: approval is a deliberate action and every generated ticket can be traced to its source. Negative instructions are not presented as confirmed positive tasks.

## Optional AI pilot after the Milestone 3 corrections

**Status: implemented 2026-11-04 and delivered for advisor review; see the [revision handoff](milestone-3-revision-handoff.md).** AI-001 to AI-006 are complete as a trusted personal pilot with the Codex CLI. Approved for planning on 2026-11-03. Follow the [AI integration brief](../Architecture/ai-integration.md) and [product requirements](../Product/optional-ai-assistance.md). This is a separate opt-in capability, not a new dependency for offline task management. Resolve M3 REV 001 through 003 and provide the concise description-plus-primary-clip view before accepting AI-generated candidates.

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| AI-001 | Build filtered AI request selection and preview | M3 corrections and task presentation refinement | Show exact selected source text, scenario context, and reference manifest; exclude unrelated sections |
| AI-002 | Add Codex CLI connection checks and adapter settings | AI-001 | Detect executable/version and ChatGPT sign-in without reading or displaying credential contents |
| AI-003 | Implement background CLI draft jobs and structured response capture | AI-002 | Use argument-array subprocess execution, restricted runtime, timeout/cancellation, and visible failures |
| AI-004 | Validate AI output and import selected drafts with provenance | AI-003 | Reject invalid/unknown IDs; preserve source links, manual edits, and idempotency; create no tickets before approval |
| AI-005 | Integrate concise AI draft preview and primary-reference selection | AI-004 | Review a short description, optional subtasks and classifications, then explicitly choose the main clip |
| AI-006 | Verify optional AI behavior and fallback | AI-005 | Fake-adapter failure tests plus a deliberate synthetic live request, sample draft review, and full non-AI operation offline |

Review gate: AI output becomes editable needs-review candidates, not automatically approved tasks. The person can compare evidence, reject the response, recover the previous description, and continue without AI. A conventional OpenAI API adapter and actual image-content requests are follow-up scope, using the same contract.

## Milestone 4 Relationships and revisions

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-012 | Implement task requirements, scenario links, dependencies, and cycle validation | IMP-003, IMP-011 | One shaving ticket serves several scenarios; invalid graph changes are rejected |
| IMP-013 | Add duplicate suggestions, review issues, and merge previews | IMP-012 | Show candidate pairs without automatic merging; merging preserves evidence |
| IMP-014 | Implement identical reimport handling and changed-source proposals | IMP-005, IMP-011, IMP-012 | Reimport creates no duplicates and preserves an edited title and a dismissed candidate |

Review gate: shared work is stored once, scenario filtering behaves correctly, and rerunning extraction does not erase decisions.

## Milestone 5 Portability and product verification

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-015 | Implement versioned project export, restore, and task CSV | IMP-007, IMP-012, IMP-014 | Restore into a separate installation or clean workspace with media and graph links intact |
| IMP-016 | Complete save recovery, keyboard actions, empty states, and media recovery | IMP-004, IMP-011, IMP-015 | Demonstrate the relevant failure and recovery states without data loss |
| IMP-017 | Run end-to-end offline verification and supply a local launch guide | IMP-013, IMP-014, IMP-015, IMP-016 | Complete the acceptance checklist and disclose remaining gaps |

Review gate: the user can import, review, organize, restart, export, and restore without an internet connection.

## Scope boundaries

Do not hardcode sample-specific tickets or diagnoses as generic parser behavior. Do not introduce a graph canvas, cloud backend, AI requirement, or collaboration system during these milestones.

Choose the concrete language, framework, packaging method, and local data directory before IMP-001 implementation. Record these choices in the [decision log](../Decisions/decision-log.md). Keep the architecture portable; existing stack choices may be reused if a developer establishes an application repository.

## Handoff information

For each review, provide the completed IMP IDs, launch instructions, tested behaviors, known gaps, and any changed product assumptions. Record observed review results under `Reviews/`; do not mark acceptance items passed based only on implementation intent.
