# Task Graph Data Model

Use SQLite records and explicit relationship tables to represent a typed graph. Stable IDs must not depend on titles, filenames, scenario numbers, or document numbering.

## Entities

| Entity | Principal fields |
| --- | --- |
| Project | id, name, ticket_prefix, description, next_ticket_number, created_at, updated_at |
| SourceDocument | id, project_id, original_name, local_relative_path, content_hash |
| ImportRun | id, project_id, document_id, parser_version, ruleset_version, state, created_at |
| SourceBlock | id, document_id, import_run_id, source_element_id, locator, heading_path, sequence, block_type, original_text, section_role |
| Scenario | id, project_id, source_identifier, title, description |
| Requirement | id, project_id, original_text, interpretation, review_state, provenance |
| Task | id, project_id, ticket_number, parent_id, level, title, description, classifications, workflow, provenance, timestamps |
| Asset | id, project_id, relative_path, original_name, media_type, content_hash, availability |
| Reference | id, project_id, kind, source_block_id, asset_id, external_url, caption, start_ms, end_ms |
| ReviewIssue | id, project_id, type, explanation, state, resolution_note |
| Rule | id, project_id nullable, version, enabled, match_scope, operator, pattern, output, precedence |
| RuleSuggestion | id, rule_id, import_run_id, task_id, field, proposed_value, source_block_id, matched_text, state |

Here, classifications and workflow describe field groups rather than a requirement to store opaque JSON. Prefer concrete columns and join tables for values used in filtering, ordering, or referential integrity.

## Task fields and defaults

| Field | Allowed values or behavior |
| --- | --- |
| level | epic, task, subtask |
| disciplines | Join table with art and dev; no rows means unassigned |
| category | core_gameplay, content, ui_ux, polish, bug_fix, technical, production, unassigned |
| tags | Many-to-many free-form labels scoped to a project |
| impact | critical, high, medium, low, unassessed |
| urgency | high, medium, low, unspecified |
| suggested_priority | p0, p1, p2, p3, or null; the default policy never produces p0 |
| priority_override | p0, p1, p2, p3, or null |
| priority_reason | Explanation of the suggestion; retain an optional override reason |
| status | backlog, to_do, in_progress, in_review, done |
| is_blocked and blocked_reason | Independent of status |
| review_state | needs_review, approved, dismissed |
| assignee_id | Nullable reference to a local project person |
| due_at | Nullable; preserve date-only values as date-only rather than inventing a time |
| board_rank | Persistent ordering within a project and status column |
| provenance | manual, source_extraction, rule, or template; retain origin records |

Use separate AcceptanceCriterion rows with text, checked state, and ordering. Keep source identity when a criterion was explicitly extracted. Store field changes with their origin so reprocessing can protect manual edits.

UUIDs or another globally unique ID scheme allow a project bundle to retain graph identity. Human-readable ticket keys combine an immutable project prefix and a monotonically allocated ticket number. Never recycle a deleted ticket number.

## Relationship tables

| Table | Meaning |
| --- | --- |
| Task.parent_id | Parent contains child; at most one parent per child |
| TaskDependency | task_id depends_on_task_id |
| TaskRequirement | task_id implements requirement_id |
| ScenarioRequirement | scenario_id requires requirement_id |
| RequirementSource | requirement_id is supported by source_block_id |
| ScenarioSource | scenario_id comes from source_block_id |
| TaskReference | task_id references reference_id |
| RequirementReference | requirement_id references reference_id |
| TaskRelation | task_id related_to related_task_id |
| ReviewIssueTask and equivalent typed joins | Connect an issue to tasks, requirements, scenarios, or source blocks |

Use typed joins rather than unvalidated arbitrary node identifiers. Keep proposed relationships separate from confirmed relationships, using a review state and origin where needed.

Scenario membership of a task follows its requirements. Do not copy shared tasks for each scenario. The board counts and displays each task once; a scenario filter can match through descendant tasks and requirements while explaining why the parent appears.

## Invariants

- Every project record and relationship belongs to one project; validate endpoint ownership.
- A task cannot parent itself. Hierarchy and dependency graphs must be acyclic.
- Reject parent levels that put an epic below a task or make a subtask contain an epic or task.
- Prevent duplicate dependency, requirement, and reference links with unique constraints.
- Require approval before a candidate appears on the normal production board.
- Preserve source blocks and import history while tasks still reference them.
- A reference must have a valid target appropriate to its kind; a video time range must be nonnegative and ordered.
- Asset paths are relative to project storage and cannot resolve outside it.
- Parent completion is explicit. Display child progress and warn about unfinished children rather than silently marking them done.
- Marking a dependency Done does not silently clear a manually entered blocked reason.

## Evidence and revision handling

Keep original source text immutable within an import version. Simplified descriptions and approved interpretations are editable records. Use a stable source element ID where the export provides one, with a structural locator and text hash as fallback evidence.

A changed document creates a new import version. Match candidate records conservatively and present changed text or relationships as proposals. Uncertain matches remain review issues. Keep dismissed candidates identifiable so a repeated import does not continually recreate them.

Merging tasks must be transactional: preserve both sets of references and requirements, resolve duplicate relationships, validate cycles, and retain the old key as an alias or merged-into record. Never silently discard a task's history.

## Portability

A project export contains a schema-versioned manifest, structured records and edges, original sources, and local assets. Preserve IDs when restoring a new project. If the same project already exists, offer an explicit update or separate-copy operation; remap all IDs consistently for a separate copy.

CSV contains flattened task records with parent keys and reference summaries. It cannot restore the complete graph.
