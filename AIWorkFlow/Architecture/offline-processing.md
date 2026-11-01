# Offline Import and Classification

The first release uses document parsing, deterministic rules, and manual review. It must produce useful candidates without claiming to understand every creative instruction.

## Import pipeline

1. Select a project, HTML file, and companion asset folder. Preview the selection before copying files into project storage.
2. Inventory files, calculate hashes, and identify an identical prior import.
3. Parse the document into ordered source blocks, retaining headings, paragraphs, lists, tables, source element IDs, links, and media references.
4. Identify historical sections and current requirements. Let the user change this interpretation before generating candidates.
5. Extract scenarios from recognized table columns such as Video No., Concept, and Scenario. For an unfamiliar layout, let the user identify the relevant table and columns.
6. Produce candidate requirements and task groups from selected sections. Preserve their original wording and source locations.
7. Run enabled classification rules and record their evidence. Suggest local media connections within the relevant section.
8. Run structural checks and place candidates in the import review queue.
9. On approval, create or update production tasks transactionally. Retain rejected suggestions and manual changes.

An interrupted import must leave either the previous valid project state or a resumable import run. Do not expose partially committed tasks on the board.

## Source structure

Use a heading stack to assign each block a section path. Keep table cells and rows as structured content instead of flattening the entire document into one string.

Recognize attachment links as assets, even when they are not HTML video elements. Decode percent-encoded relative paths and preserve spaces and Unicode filenames. Record missing files without failing the entire import.

The supplied scenario cells contain inline numbered steps, including repeated numbers. Segment only when the pattern is clear. Retain original text, ordering, and an unsplit fallback for ambiguous cells. Document numbering is not a database ID.

Historical content remains searchable and viewable. Excluding it from extraction must not delete it or establish it as current truth.

## Candidate generation

A heading that names a system, such as Wood shave, can propose a parent task. Its actionable bullets can propose children. Preserve timing, costs, scenario restrictions, negative instructions, and references as requirements or constraints.

Do not treat every bullet as a task. Asset labels, prices, scenarios, explanatory text, and exclusions need a type or a review choice. The source text must remain accessible when the parser is unsure.

Default titles should remain close to the source. Simplification may remove markup and normalize whitespace, but must not rewrite technical meaning or invent a causal explanation.

Templates can propose extra implementation subtasks only after explicit application. Label these as template-derived and keep them in review until approved.

## Rule configuration

Support global rules plus project-specific rules. A project rule has precedence over a global rule for a single-value field. Equal-precedence conflicting suggestions create a review issue. Disciplines may accumulate, so Art and Dev matches can legitimately produce both.

Required rule fields are ID, name, version, enabled state, match scope, operator, phrase or pattern, case sensitivity, output field, proposed value, and precedence. Start with contains-phrase and equals operators; unrestricted scripting is unnecessary.

Support phrase groups for the English and Vietnamese wording present in the sample. Keep vocabulary editable. Do not require translation services.

| Scope or phrase | Suggested output | Limitation |
| --- | --- | --- |
| Spawn, collision, resource calculation | Dev | Record the matched wording |
| Model, texture, illustration | Art | Do not classify the entire document from one nearby label |
| Animation or VFX integration | Art and Dev suggestion | A person may refine responsibility |
| Core gameplay heading | Core gameplay category | Allow a more specific local rule to override |
| Explicit defect report | Bug fix category | Creative failure scenarios are not automatically bugs |
| Project rule for a central mechanic | High impact | Display that this is a configured rule |

A match explains a suggestion; it does not confirm the requirement. Do not use percentages that imply measured model confidence. Use states such as Suggested, Conflicting, and Reviewed.

Preserve negation. For example, No build fence animation must remain an exclusion. If a simple rule cannot interpret it safely, leave the candidate unresolved rather than emitting a positive animation task.

## Priority policy

Keep impact, urgency, and priority independently editable. Use the initial policy in the [implementation brief](../Product/implementation-brief.md): map assessed impact to P1, P2, or P3; allow High urgency to promote by one tier up to P1; reserve P0 for explicit selection. Unassessed impact leaves suggested priority empty.

Dependencies and scenario reuse are visible context. Defer a numeric weighting formula until the user has reviewed real results. Never convert the exported project due date into individual task deadlines automatically.

## Reference suggestions

Attach source text to its own candidate. Offer image and media suggestions from the same semantic section, with special care around columns and tables. Keep a proposed association distinguishable from a confirmed one.

External links remain links. Do not fetch remote resources in the background. Users can add a local copy and connect it to the original reference.

For videos, support manually entered timestamp ranges. The first release does not analyze frames or sound.

## Duplicate and issue detection

Use exact normalized text matches for strong duplicate suggestions. Optional text similarity may rank possible matches, but must show both sources and preserve scenario distinctions. Do not strip numbers, negative instructions, or good/bad labels when comparing meaning-sensitive content.

Check scenario references against known identifiers. This catches a missing Video 099, but cannot establish that an existing Video 003 is the correct conceptual base. The user must verify meaning.

Store semantic issues from the source analysis as manual review examples. Do not hardcode sample-specific diagnoses into a generic parser.

## Reimport behavior

An identical source hash and asset manifest should reuse the existing source version. Running a new ruleset creates fresh proposals, not a fresh set of approved tickets.

For changed sources, compare source blocks and show additions, removals, changed requirements, and uncertain mappings. Never overwrite edited titles, classifications, acceptance criteria, or relationships silently. Preserve dismissed candidate decisions where source identity still matches.

## Local file handling

Store files inside the project's managed storage directory. Validate paths before copying or serving media. Do not follow symlinks or decoded paths outside the selected import root.

Render sanitized source previews with remote loading disabled. Bundle application dependencies, icons, and fonts locally. Export from a consistent database snapshot together with the corresponding source and media files.
