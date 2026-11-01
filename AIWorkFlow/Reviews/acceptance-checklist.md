# Product Acceptance Checklist

Use this checklist against a running local application. All items are initially unchecked. Record observed results, reproduction steps, and evidence in a dated report under this directory.

## Local operation

- [ ] Install and launch using documented local instructions.
- [ ] Complete the main workflow while disconnected from the internet.
- [ ] Confirm the interface, fonts, icons, and media do not require external services.
- [ ] Confirm no AI model, API credential, account, or telemetry connection is required.
- [ ] Confirm the service binds to the local computer by default.
- [ ] Restart and recover saved projects, edits, ordering, and review decisions.

## Sample import and evidence

- [ ] Import the actual HTML and companion folder in RawData.
- [ ] Preserve 12 scenario records with identifiers 001 through 012.
- [ ] Resolve the 61 unique local asset paths referenced by the supplied export.
- [ ] Recognize MP4 attachment links and preview at least one local video.
- [ ] Preserve heading paths, original text, inline numbered steps, and historical sections.
- [ ] Exclude historical sections from candidate generation by default and allow changing that choice.
- [ ] Read section 3.5 resource values without changing their quantities or units.
- [ ] Preserve negative requirements such as No build fence animation.
- [ ] Handle a missing media file without failing the whole import.
- [ ] Preview sanitized HTML without executing scripts or automatically loading remote resources.

## Extraction and classification

- [ ] Review source-supported shaving candidates without invented implementation details.
- [ ] Classify a task as Art, Dev, both, or unassigned.
- [ ] Display matching rule evidence and distinguish suggestions from reviewed values.
- [ ] Leave uncertain categories and impact unassigned or unassessed.
- [ ] Keep impact, urgency, and priority independent and explain priority overrides.
- [ ] Keep creative failure scenarios from being classified automatically as bug reports.
- [ ] Apply a project rule, preview its results, and resolve a conflicting single-value suggestion.
- [ ] Label explicitly applied template tasks separately from source-derived tasks.
- [ ] Avoid copying the project's assignee, due date, or completion percentages onto every ticket.
- [ ] Keep semantic source contradictions available as manual issues without claiming automatic resolution.

## Review workflow and graph

- [ ] Compare source material and a candidate side by side.
- [ ] Split, merge, reparent, approve, dismiss, and bulk classify candidates.
- [ ] Preserve references, requirements, and history when merging.
- [ ] Connect one shared shaving task to multiple scenarios without duplicating it.
- [ ] Attach a source paragraph and local media to the same task.
- [ ] Reject self-parenting, hierarchy cycles, dependency cycles, and cross-project edges.
- [ ] Show duplicate and similarity suggestions without merging automatically.
- [ ] Keep import approval separate from the production In Review status.
- [ ] Keep original wording accessible after a simplified description is edited.

## Board and usability

- [ ] Display the five required columns and a separate blocked state.
- [ ] Show readable titles, ticket keys, discipline labels, priority, category, and child progress.
- [ ] Move and reorder cards by drag and by an accessible alternative.
- [ ] Persist status and rank changes and expose save failures.
- [ ] Make Dev filtering include combined tasks and Both filtering require both disciplines.
- [ ] Display one shared task once when several scenario links match.
- [ ] Show the parent and explain a match through a filtered subtask.
- [ ] Handle long text, horizontal board scrolling, empty projects, and empty filter results.
- [ ] Inspect images and GIFs and use local video controls from task details.
- [ ] Keep unfinished children visible when their parent is marked Done.

## Reimport and portability

- [ ] Reimport identical input without duplicating approved tasks, scenarios, assets, or requirements.
- [ ] Rerun rules without overwriting manual edits or recreating dismissed candidates unnecessarily.
- [ ] Review changed-source proposals and retain links to the earlier source version.
- [ ] Export a complete project and restore it into a clean workspace.
- [ ] Preserve graph identity, local media, ticket keys, and ordering during restore.
- [ ] Handle an existing-project collision with an explicit update or separate-copy choice.
- [ ] Export a correctly quoted task CSV, including text with commas and newlines.
- [ ] Verify a failed import or migration leaves the prior project recoverable.

## Review report format

Create `YYYY-MM-DD-<feature>-review.md` with the tested version, launch environment, completed IMP task IDs, and demonstrated workflow. Separate observed defects from design suggestions.

For each defect, record severity, reproduction steps, expected behavior, observed behavior, related requirement, and supporting evidence. Use Blocker for inability to use or trust the core workflow, Major for incorrect required behavior, and Minor for a smaller usability defect.

Finish the report with accepted behaviors, outstanding fixes, and the next review target. Screenshots can support interface findings, but persistence and offline claims require runtime verification. Do not report unexecuted checks as passed.
