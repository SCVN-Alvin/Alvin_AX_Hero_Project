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
| [Implementation plan](Tasks/implementation-plan.md) | Deliver the product through reviewable milestones |
| [Acceptance checklist](Reviews/acceptance-checklist.md) | Verify the implementation and record product reviews |
| [Decision log](Decisions/decision-log.md) | Understand accepted choices and open decisions |
| [Milestone 1 handoff](Tasks/milestone-1-handoff.md) | Launch the delivered local foundation, see verification results and known gaps |

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

Planning documents are available. Milestone 1 (local foundation: SQLite persistence, multiple projects, manual tasks and subtasks, Kanban board) is implemented in `../app/` and awaits advisor review; see the [Milestone 1 handoff](Tasks/milestone-1-handoff.md) and the [application README](../app/README.md). Milestones 2 to 5 are not started. No rendered product review has been completed, and the unchecked review items are future acceptance criteria.
