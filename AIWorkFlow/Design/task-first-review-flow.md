# Task Presentation and Review Flow

The user wants a task such as Log hammering to show a main description and a reference clip. Make that the default task experience. Preserve source evidence without showing every related paragraph and image at once.

This brief refines the existing Kanban specification in response to the November 3 product feedback. It does not require an AI model or a provider connection.

## Default task view

Show the title, compact classification badges, a short editable description, and one primary demonstration clip. A clip can be a local video or animated GIF. Where a video supports it, let the user set and save a start/end range rather than creating a duplicate video file.

Below the description and clip, show compact entry points for Subtasks, Details, and Sources. Open them when needed. Keep normal task actions accessible without scrolling through the entire source document.

| Default view | Available on demand |
| --- | --- |
| Title and short main description | Original wording and heading path |
| One primary reference clip or Choose reference | Supporting media and asset references |
| Compact Art/Dev, category, priority labels | Rule matches and classification explanation |
| Subtask count and a simple expand action | Full requirement lines and scenario variations |
| Any unresolved blocking question | Detailed source comparison and change history |

Do not hide a contradiction or critical exclusion merely to make a ticket shorter. Show a compact Needs clarification warning with a link to the conflicting lines.

## Example Log hammering draft

Title: Implement log hammering intro.

Suggested manually reviewed description: Build the log-hammering intro with looping hammer motion, impact feedback, and the transition to the waterfall. Confirm log-selection behavior for each scenario.

Primary reference: one user-selected demonstration clip, with a friendly label such as Hammer motion and log impact. Retain the original filename in reference details.

Clarification: the sample contains conflicting clean/dirty-log instructions. Preserve that question; do not silently decide which log is selected while simplifying the description.

The example description is a proposed task summary, not a replacement for the original source wording.

## Review sequence

1. Import and select the current source sections.
2. Generate candidate systems and select one from a compact list.
3. Review a task draft containing the title and an editable description. Without AI, offer selected source lines as starting content and allow direct editing; do not invent a prose summary.
4. Choose the primary demonstration from a small set of local candidates. Show up to three strongest suggestions initially, with their source sections and reasons. More references opens a separate picker.
5. Expand requirements, variations, subtasks, or evidence only when needed. Open the full source beside the draft through Compare source.
6. Confirm classifications and approve. The preview states what will become tickets and which clip is primary.

Keep the full three-pane evidence workspace as an advanced comparison mode rather than the only starting view.

## Reference selection rules

Prefer references explicitly linked in the task's own source subgroup. Next consider media directly associated with that subgroup. Cross-section keyword matches belong in More references by default.

These rules rank suggestions; they do not prove relevance. The user explicitly selects the primary reference. Never auto-attach every suggestion. If there is no suitable clip, show Choose reference or an explicit None decision, rather than substituting a random asset image.

Distinguish demonstration clips, implementation asset references, and source evidence. An image of a hammer model can support art work without becoming the main demonstration of hammering behavior.

Use friendly captions in the task view. Show long filenames and technical hashes only in details. Load the primary player on demand and keep supporting media unloaded until expanded.

## Data changes

Reuse the editable task/candidate description for the main description. Keep source text immutable. Store the selected primary reference separately from its supporting attachments.

Add an explicit role on the candidate and task reference relationship, for example primary_demo, supporting_demo, asset_reference, or source_evidence. Enforce at most one primary_demo per task or candidate, and require a primary reference to be an attached reference from the same project.

Preserve reference roles through approval and reloading. Clearing a primary selection must not delete its source asset. Keep captions editable without renaming original files.

Use the reference's start/end timestamps for playback ranges; ensure they are stored, returned by the API, and respected by the player. Unknown ranges remain empty. Do not infer precise timing from a filename.

## Acceptance examples

- Opening Log hammering shows its description and one chosen clip without presenting all seven media suggestions.
- More references still exposes every retained suggestion with its relevance explanation.
- A person can edit the description without altering original source text.
- The selected primary clip and optional time range survive approval, restart, and task reopening.
- Wood shave can show a main system description while its clean/bad wood variations remain accessible as subtasks.
- A visible clarification survives summary editing when the source conflicts.
- The complete flow works offline and without AI. Optional AI buttons do not block it.

Implement the requirement-ownership and hierarchy corrections in the [Milestone 3 review](../Reviews/2026-11-03-milestone-3-review.md) before accepting the revised workflow.
