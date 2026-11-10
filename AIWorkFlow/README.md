# Offline Task Organizer Documentation

This directory contains the source findings, implementation brief, data model, interface requirements, delivery tasks, and review criteria for a local creative brief task organizer. The first release works without an AI model or internet connection.

The workflow is **import brief → extract candidates → review and organize → approve tasks → manage work on a Kanban board**.

## Reading order

| Document | Purpose |
| --- | --- |
| [Implementation brief](Product/implementation-brief.md) | Start here for product scope and expected behavior |
| [RawData analysis](Findings/raw-data-analysis.md) | Understand the sample brief and its ambiguities |
| [Data model](Architecture/data-model.md) | Implement the typed task graph and persistence |
| [Offline processing](Architecture/offline-processing.md) | Implement import, extraction, and classification rules |
| [Kanban interface](Design/kanban-ui.md) | Build the Jira-inspired board and review workspace |
| [Task presentation refinement](Design/task-first-review-flow.md) | Show a main description and one primary clip with supporting evidence on demand |
| [Optional AI assistance](Product/optional-ai-assistance.md) | Plan source-linked AI task drafts while preserving offline operation |
| [AI integration brief](Architecture/ai-integration.md) | Connect through the existing Codex CLI sign-in, validate responses, and review draft tasks |
| [Implementation plan](Tasks/implementation-plan.md) | Deliver the product through reviewable milestones |
| [Acceptance checklist](Reviews/acceptance-checklist.md) | Verify the implementation and record product reviews |
| [Decision log](Decisions/decision-log.md) | Understand accepted choices and open decisions |
| [Milestone 1 handoff](Tasks/milestone-1-handoff.md) | Launch the delivered local foundation, see verification results and known gaps |
| [Milestone 2 handoff](Tasks/milestone-2-handoff.md) | Import the sample brief, browse sources and media, see verification results and known gaps |
| [Milestone 2 review](Reviews/2026-11-01-milestone-2-review.md) | The advisor's observed results and the defect fixed in Milestone 3 |
| [Milestone 3 handoff](Tasks/milestone-3-handoff.md) | Generate, classify, review, and approve the wood-shaving system onto the board; verification results and known gaps |
| [Milestone 2 advisor review](Reviews/2026-11-01-milestone-2-review.md) | Observed source and media behavior, a media-folder fix, and the next extraction review gate |
| [Milestone 3 revision and AI pilot handoff](Tasks/milestone-3-revision-handoff.md) | Corrections M3 REV 001 to 003, the task-first view, optional Codex CLI drafting (AI-001 to AI-006), launch, verification and known gaps |
| [Milestone 3 advisor review](Reviews/2026-11-03-milestone-3-review.md) | Passing normal flows, three reproduced approval and hierarchy gaps, and presentation changes |

## Directory conventions

- Use the exact root name `AIWorkFlow/` and PascalCase category directories, such as `Tasks/` and `Reviews/`.
- Use lowercase kebab-case filenames, such as `implementation-brief.md`. Use `README.md` for directory indexes.
- Keep source observations in `Findings/`, product scope in `Product/`, technical design in `Architecture/`, and interface behavior in `Design/`.
- Keep implementation work in `Tasks/` and actual review results in `Reviews/`. Give future review reports names such as `2026-11-01-board-review.md`.
- Record accepted changes and unresolved choices in `Decisions/decision-log.md`.
- Link to source files in `../RawData/`; preserve the original input files.

## Responsibilities

The implementing developer builds the application and supplies a runnable local version and relevant verification results. The advisor writes briefs, reviews the delivered product, identifies gaps, and suggests improvements. The user resolves creative ambiguities and decides product priorities.

Implementation task IDs such as `IMP-001` describe work on this application. Future application ticket keys such as `LOG-12` describe work extracted from a creative project. Keep these two sets separate.

## Current state

Milestones 1 to 3 are implemented in `../app/`; see the [application README](../app/README.md) for launch instructions. The [revision handoff](Tasks/milestone-3-revision-handoff.md) (2026-11-04) delivers the fixes for M3 REV 001 to 003, the task-first task view with one chosen main clip, and the optional AI drafting pilot (AI-001 to AI-006, Codex CLI adapter, off by default). These await advisor review; nothing is accepted yet. A conventional API adapter and sending actual images or video remain follow-up scope. Milestone 1 has no separate advisor acceptance report; Milestones 4 and 5 are not started. General release checks remain separate from dated review observations.
