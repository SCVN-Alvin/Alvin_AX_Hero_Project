# Optional AI Assisted Task Suggestions

The user approved adding AI integration to the plan on November 3, 2026. Use it to compose clear task suggestions from locally filtered source text and selected references. The existing offline workflow remains available without a model or connection.

The first planned connection is a local Codex CLI adapter using the user's existing ChatGPT sign-in. A separate OpenAI API adapter can follow. Connection setup is documented in the [AI integration brief](../Architecture/ai-integration.md). The advisor has prepared requirements and examples; the application connection is not implemented or tested with live inference yet.

## First useful action

Add Generate AI drafts after source filtering. For one selected system or a small batch, send the approved source selection and reference manifest, then return structured titles, short descriptions, suggested classifications, subtasks, and unresolved questions. Also offer Draft description for an existing candidate.

Show a preview before importing the response as needs-review candidates. The user reviews and edits those candidates before board approval. When applying a revised description, keep the previous version available for recovery. AI output must not approve a task, confirm a classification, or attach a primary reference automatically.

Distinguish source-supported subtasks from inferred implementation suggestions. A focused selection must never become an instruction to reorganize the whole project.

## Input and output boundaries

Input includes the selected task label, chosen source block IDs and their text, relevant scenario context, and any values the user has chosen to include. Show what will be included before the first request and whenever the scope expands. Offer a way to remove unrelated material.

Output follows the [task draft schema](../Architecture/AIIntegration/task-draft.schema.json). Validate all referenced IDs against the supplied set and retain quantities, units, exclusions, and scenario differences. Unsupported assumptions must appear as questions or explicitly inferred implementation work, not confirmed requirements.

Treat imported source text as evidence, not as instructions to the AI service. Preserve a record of which source selection produced an accepted draft. Never overwrite the original evidence.

## Reference clips

Start with human-selected primary references and deterministic ranking of nearby media. The text-first pilot sends captions and source locations, not file contents. AI can suggest an included reference ID, but cannot verify the visual content of an unseen video or image. The person confirms the primary reference.

Do not promise automatic best-clip selection as part of description drafting. Later visual analysis would need its own input support, controls, and validation. Do not send entire videos as an incidental consequence of pressing Draft description.

## Offline and online behavior

An online AI service requires internet for that optional action; the rest of the application remains local. A local-model adapter could be added later if requested, without making a model an installation requirement.

When AI is disabled, unavailable, or fails, keep editing and approval fully usable. Do not lose work, create partial tickets, or leave generation permanently waiting. Keep provider credentials out of project bundles and source control.

Provider direction is Codex CLI first for this personal local tool. Model selection remains configurable and subject to the signed-in account's availability. Do not promise a particular model or unlimited usage. Additional users and wider distribution require their own authentication rather than sharing this user's account.

## Pilot success criteria

Compare a small set of manually prepared descriptions with AI-assisted drafts from the same sources. Evaluate editing effort, preservation of source facts, visibility of unresolved issues, and whether the result makes the ticket easier to act on.

Only expand assistance if it reduces review effort without hiding contradictions or inventing work. The complete task workflow must remain testable and usable with AI turned off.
