# Offline Task Organizer Implementation Brief

Build a local application that turns creative production briefs into structured, reviewable tickets. Its primary interface is a Jira-inspired Kanban board. The first release must run without an AI model, account, cloud service, or internet connection after installation.

## User outcome

The user can import a creative brief, review suggested work, separate Art and Dev responsibilities, identify shared systems across scenarios, create subtasks, and manage progress while keeping the original evidence accessible.

The workflow is **import → generate candidates → review and organize → approve → manage on the board**.

## First release scope

| Capability | Required behavior |
| --- | --- |
| Multiple projects | Create, switch, and isolate projects and their assets |
| HTML import | Accept a file and its companion media folder, matching the sample in RawData |
| Structured extraction | Preserve headings, paragraphs, lists, tables, scenarios, and historical sections |
| Candidate generation | Suggest ticket groups and subtasks using structure and configurable rules |
| Classification | Suggest Art, Dev, both, category, and impact with visible supporting evidence |
| Human review | Approve, dismiss, split, merge, reparent, and bulk edit candidates |
| Task graph | Store hierarchy, dependencies, shared requirements, scenarios, and references |
| Kanban board | Manage status and ordering with persistent edits and filters |
| Media access | Preview local images, GIFs, and videos; preserve external links |
| Portability | Export and restore a complete project; export a flattened task CSV |

## Task classification and priority

Disciplines are independent labels: a ticket can be Dev, Art, both, or unassigned. Categories are Core gameplay, Content, UI/UX, Polish, Bug fix, Technical, Production, and Unassigned. Use one primary category and optional tags.

Store impact separately from urgency. Impact is Critical, High, Medium, Low, or Unassessed. Urgency is High, Medium, Low, or Unspecified. A central mechanic such as wood shaving can have high impact without a known deadline.

Priority is P0 through P3, or Not assessed. P0 is an explicit immediate blocker; P1 is high-priority work; P2 is normal work; P3 is lower-priority work. Suggestions must display a rationale. Unassessed work must remain distinguishable from low-priority work.

For the initial default policy, High or Critical impact suggests P1, Medium suggests P2, and Low suggests P3. High urgency can promote a suggestion by one tier up to P1. P0 requires explicit user selection. An unassessed impact remains Not assessed. Show dependency and scenario reuse information to reviewers without silently changing the priority. Support manual overrides and explain whether the displayed value is suggested or overridden.

Do not infer individual ticket assignees, deadlines, or completion from project-level metadata. An approved candidate starts in Backlog unless the user chooses otherwise.

## Trust and review

Every generated task must link to its source. Every suggested classification must record the matching rule and evidence. Preserve original wording separately from simplified descriptions.

Do not invent missing requirements, acceptance criteria, media timestamps, or creative decisions. Label manually added tasks and explicitly applied templates by their origin.

Automatically flag what deterministic checks can establish: missing assets, unresolved scenario identifiers, duplicate candidates, rule conflicts, and invalid relationships. Let users manually record semantic contradictions. Never merge duplicate suggestions or resolve conflicting creative instructions automatically.

## Interface requirements

Provide a project sidebar, search and filters, an import review workspace, the Kanban board, and a task details panel. Use compact cards, neutral backgrounds, white surfaces, restrained blue accents, and clear typography. Preserve enough space for task titles and practical editing.

Board columns are Backlog, To Do, In Progress, In Review, and Done. Blocked is a separate flag. Import approval is a separate state from production review in the In Review column.

Task cards show their key, title, discipline badges, priority, category, assignee when assigned, blocked state, and subtask progress. Details expose the original references, requirements, dependencies, and classification rationale.

## Offline operation and persistence

Use a local application service, SQLite, and project-owned source and asset files. Bundle fonts and interface dependencies locally. Bind the service to the local computer by default.

Parse imported HTML as data. Display sanitized content without executing scripts or automatically fetching external resources. Local media previews should use controlled application routes.

Persist task edits, board ordering, source relationships, and review decisions. Show save failures clearly. Reimporting identical input must not duplicate content. Reprocessing must preserve manual edits and present proposed changes for review.

A complete project bundle must restore structured records, sources, assets, and relationships on another installation. CSV is a reporting format and does not replace a complete backup.

## Sample result

The user can approve a shared ticket titled **Implement wood shaving system**, classify it as Core gameplay with both disciplines, and connect it to the scenarios that use it.

Source-supported candidate subtasks include hand shaving behavior, automated saw machine shaving, configurable output values, the bad-log variation, and specified motion or visual feedback. Separate tasks further when Art and Dev work need distinct ownership.

Attach the relevant section 3.4 references and section 3.5 resource values. A wood chip effect or additional internal system can be proposed manually if the source does not establish it.

## Deferred scope

AI processing, cloud synchronization, real-time collaboration, Jira integration, automatic video understanding, automated semantic contradiction resolution, and a graphical dependency canvas are outside the first release. Store the graph now and expose its relationships in task details.

## Delivery and acceptance

Deliver incremental working versions following the [implementation plan](../Tasks/implementation-plan.md). The advisor reviews the running product against the [acceptance checklist](../Reviews/acceptance-checklist.md), including the actual sample import and a disconnected session.

Acceptance requires an offline end-to-end demonstration: import the supplied brief, approve and organize tasks, inspect references, manage the board, restart without losing data, then export and restore the project.
