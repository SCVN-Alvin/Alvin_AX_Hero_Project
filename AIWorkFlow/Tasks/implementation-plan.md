# Implementation Delivery Plan

These tasks build the organizer itself. IDs use the `IMP` prefix so they cannot be confused with creative tickets imported into the finished application. Status is tracked per milestone below. Milestone 1 is delivered for review (see the [Milestone 1 handoff](milestone-1-handoff.md)); everything else is planned and unstarted.

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

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-005 | Implement managed source and asset import with path validation and hashing | IMP-002 | Import the sample, resolve its 61 referenced local asset paths, and report a deliberately missing file |
| IMP-006 | Parse ordered source blocks, heading paths, tables, historical sections, and attachment links | IMP-005 | Inspect the sample's 12 scenario rows and retained original wording |
| IMP-007 | Implement sanitized source browsing and local image, GIF, and video previews | IMP-006 | Inspect map images and play a local video while disconnected |

Review gate: the source browser shows actual imported content, preserves historical context, and makes no automatic remote requests.

## Milestone 3 Candidate generation and review

| ID | Task | Dependencies | Completion evidence |
| --- | --- | --- | --- |
| IMP-008 | Generate section-based task and requirement candidates | IMP-006 | Wood shaving candidates retain source text, scenario restrictions, and resource values |
| IMP-009 | Implement editable global and project rules with evidence and conflicts | IMP-008 | Preview and change a rule; unclear fields remain unassigned |
| IMP-010 | Implement priority suggestions with impact and urgency kept separate | IMP-009 | Default mapping and overrides have visible explanations; P0 is never selected automatically |
| IMP-011 | Implement split review, bulk classification, approval, and dismissal | IMP-007, IMP-009, IMP-010 | Review a source section, edit its candidates, and approve selected tasks onto the board |

Review gate: approval is a deliberate action and every generated ticket can be traced to its source. Negative instructions are not presented as confirmed positive tasks.

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
