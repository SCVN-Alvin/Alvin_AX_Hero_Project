# Kanban Interface Specification

Use a Jira-inspired desktop layout with compact cards, strong information hierarchy, and practical editing. The initial interface should make imported suggestions easy to verify and approved work easy to track.

## Application layout

| Region | Contents |
| --- | --- |
| Sidebar | Project switcher, Board, Backlog, Import Review, Sources, Rules, Settings |
| Header | Active project, search, filters, Import, Create task |
| Workspace | Board, list, source browser, or import review |
| Details panel | Selected task, subtasks, classifications, requirements, references, history |

Use neutral page backgrounds, white cards, restrained blue accents, and locally bundled type and icons. Use text and symbols alongside color. Avoid decorative dashboards that consume space without helping the task workflow.

Optimize for desktop. At narrower widths, collapse the sidebar and let the board scroll horizontally; keep task details available as a full panel. Preserve access to all actions without requiring drag-and-drop.

## Board behavior

Columns are Backlog, To Do, In Progress, In Review, and Done. Show a count of visible cards in each column. A blocked task stays in its current column and displays a blocked badge and reason.

Show approved top-level tasks by default. Expand children within a card or its details panel. A shared task appears once, even if it serves several scenarios or carries both discipline labels.

Allow card dragging within and across columns. Supply a status menu for keyboard and non-drag use. Persist changes and card ordering. If saving fails, restore or clearly mark the unsaved state; do not silently lose edits.

## Card contents

Display the ticket key, title, Art and Dev badges, priority or Not assessed, primary category, optional assignee, and completed/total subtask count. Include a blocked indicator when applicable.

Keep long descriptions, source quotations, and large media out of the card. Show the title sufficiently to distinguish tasks and expose the complete title on selection. A task with both disciplines has two readable badges.

## Search and filtering

Search titles, descriptions, and ticket keys. Filter by discipline, category, impact, urgency, priority, scenario, and assignee. Include Unassigned and Unassessed choices.

Selecting Dev includes Dev-only and combined Art/Dev tasks. Selecting Both requires both labels. Combine different filter dimensions with AND; multiple selections within one dimension use OR, except the explicit Both discipline choice.

If a scenario matches a subtask requirement, show its parent once with a matching-subtask indicator. Make clear when progress counts include children hidden by the filter. Offer Clear filters when no cards match.

## Import review workspace

Place source text and media on the left and candidate details on the right. Show source heading paths and let the user jump to the original block. Keep proposed text and original text distinguishable.

Provide Approve, Dismiss, Split, Merge, Reparent, and bulk classification actions. Show rule evidence beside suggested fields. Let users attach or remove suggested references and connect shared requirements to scenarios.

Distinguish Needs import review from the production status In Review. The former checks extracted information; the latter checks work that has been implemented.

Merging must preview the retained title, children, requirements, and references. Preserve old ticket links and report any invalid dependency rather than discarding it.

## Task details

Lead with title, description, and editable task properties. Follow with acceptance criteria, subtasks, dependencies, scenario requirements, references, and change history. Use tabs or expandable sections if necessary to keep the main work readable.

Show whether classifications are suggested or manually set. Display priority rationale and allow an override. Support nullable assignee and deadline values.

When a parent is marked Done with unfinished children, show the remaining work and allow an explicit decision. Never complete children automatically.

## Media and sources

Preview images and GIFs at a useful size. Provide video controls and optional manual timestamp ranges. Show captions and original filenames, with a link to the source block.

External links carry an internet-required indicator and open only on user action. Missing assets show Locate file, while retaining the original reference. The source browser supports historical sections without mixing them into current extracted requirements by default.

## Required interface states

| Situation | Expected behavior |
| --- | --- |
| No project | Explain the import workflow and offer Create project |
| Empty project | Offer Import brief and Create task |
| Import running | Show stage and progress without freezing the interface |
| Import failed | Show a useful cause and retry path without corrupting the project |
| No candidates | Explain what was parsed and allow source section selection or manual creation |
| Missing media | Preserve the reference and offer file recovery |
| No filter matches | Show active filters and Clear filters |
| Save pending or failed | Make persistence state visible and keep recoverable edits |
| External resource offline | Keep the link and identify that a local copy is unavailable |

## Review emphasis

Review the running application using real imported text and media. Check title readability, keyboard access, card density, long text handling, clear classification evidence, and the difference between candidate approval and production status. A static mockup is insufficient to verify persistence or offline operation.
