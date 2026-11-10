# Milestone 3 Revision and AI Pilot Handoff

Delivered 2026-11-04 for advisor review. Nothing here is accepted, committed or ticked in the acceptance checklist. The build is in `../../app/`.

## Completed

| ID | What was delivered |
| --- | --- |
| M3 REV 001 | A requirement approved before its parent is created once, keeps its place in the candidate tree, and is attached to the parent's ticket when the parent is approved. The dialog tells a child line from a shared requirement and the result names lines still waiting for their task |
| M3 REV 002 | One **Move or change level** dialog sends level and parent together and lists only valid places, including top level and epic. The separate Level and Parent selectors are gone. A refused move changes nothing |
| M3 REV 003 | Kind, level and parent changes check the candidate's children too. Merge requires the same level and re-checks what moves. Split cannot place a subtask under a new subtask |
| Task-first view | Title and badges, a short editable description, one explicitly chosen main clip, open questions in view; Subtasks, Details, Supporting references and Original source collapsed; Compare source on demand. Roles, captions and optional playback ranges persist through approval and restart |
| AI-001 | Selection and a payload preview that is the real request, with removable blocks, limits and "what is not sent" |
| AI-002 | Settings and **Check connection** (executable, version, sign-in, required options, restrictions) without reading credentials |
| AI-003 | Background runs through the Codex CLI adapter (argument array, `shell=False`, stdin, timeout, cancellation, visible failures) |
| AI-004 | Independent validation, import of selected drafts with provenance, idempotency, no tickets before approval |
| AI-005 | Draft preview with per-draft choices, proposals that stay proposals, explicit main-clip choice |
| AI-006 | Scripted-adapter failure tests, a deliberate synthetic live request, sample drafts for Log hammering and Wood shave, and the full workflow with AI off |

## Launch and try it

```sh
cd /Volumes/Supercent/AIXJam/app && ./run.sh        # http://localhost:8765/
```

The first start upgrades the database to schema 5 and writes `organizer.db.bak-pre-v0004-<time>` first. The upgrade was rehearsed on a copy of the live database (98 candidates, 88 references, no integrity or foreign-key problems). The live `app/data/` was not opened for writing by any test or experiment (its modification time is unchanged).

**Try the task-first view (3 minutes):** Import Review → select *Log hammering (~5 seconds)*. The first view is short. Press **Start from source lines…**, tick two lines, edit them into a sentence. Press **Flag a question…** and link a line (the warning stays above everything). **Use as main clip** on one of the suggested clips (nothing is chosen for you); **More references…** shows the rest; **Clear** keeps the file. Open **Subtasks**/**Details** on *Wood shave* when you want them; **Compare source** shows the original beside the task.

**Try the corrections:** *Net catch* → ⋯ → **Move or change level…** → "A subtask of Build fence", then back to a top-level task. Open *Wood shave* → Move: no "subtask of" option is offered because it has subtasks. Approve one line under *Build fence* with **Approve this line now…**, then approve *Build fence*: the line is on the ticket.

**Try AI:** Settings → AI drafting → tick **Turn on**, **Check connection**. Select *Log hammering* → **Draft with AI…** → read the preview (untick anything), **Draft description**. Or tick *Log hammering* and *Wood shave* and use **Draft with AI…** in the selection bar for descriptions, subtasks, proposals and questions. Choose what to import in the draft window. Without a Codex sign-in the run fails with a plain message and changes nothing.

## Verification (isolated storage; the live data folder untouched)

| Check | Result |
| --- | --- |
| Backend suite (`unittest discover`) | **234 tests pass** (162 earlier + 24 revision + 48 AI) |
| Browser, Milestone 1 / 2 | 64 / 88 checks pass |
| Browser, Milestone 3 (updated to the task-first layout) | 68 checks pass |
| Browser, corrections and task-first view (`ui_check_rev.py`) | 45 checks pass |
| Browser, AI flow with the scripted adapter (`ui_check_ai.py`) | 42 checks pass |
| Deliberate live Codex request, synthetic input only | passed; details in [live-codex-smoke.md](milestone-3-revision-evidence/live-codex-smoke.md) |

All browser runs used Chrome with a dead proxy and no DNS: every request went to the local service and there were no console errors. Screenshots are in [milestone-3-revision-evidence](milestone-3-revision-evidence/).

Regression tests added: `test_revision.py` (ownership: child before parent, together, after approved parent, standalone, line under a subtask; hierarchy: top-level to subtask and back, between parents, demoting a parent with subtasks, parent to note, merge across levels and with subtasks, split; reference roles, one primary enforced by the database, range validation, persistence, board-side edits, merging; questions; description history) and `test_ai.py` (preview limits and leakage; settings; every rejection: unknown source, reference and scenario ids, malformed JSON, empty output, foreign request id, bad enum, extra field, no evidence, duplicate draft ids; invented number; auth, usage limit, network, error; timeout; cancellation; restart; late answers; import preview, proposals, subtask reuse, idempotency, retry, manual edit preserved, wrong project, failed import rolled back; and the Codex adapter against a stand-in executable: argument array, hostile source text kept off the command line, stdin, working folder, environment without API keys, classification of failures, process-group kill on timeout and cancel).

**Demonstrations.** *Log hammering*: filtered text with a removed block, a short drafted description with valid evidence, the clip proposed from its caption only, then chosen by a click (screenshots a-02 to a-04, r-03, r-09). *Wood shave*: clean and bad variations kept as subtasks (two matched to the existing subtasks, one inferred and labelled), a configurable-values suggestion, 10 and 40 wood logs kept as written (the quantity chips come from the section 3.5 lines the person attaches; the Milestone 3 browser check confirms them on the ticket, and an AI description that cites them passes the number check). Screenshots a-05, a-06, r-05. The AI answers in the browser demonstrations are **scripted**; the real brief was never sent to a model.

## Behaviour to know

- **Description-only vs batch.** *Draft with AI…* on one task imports only a description. The selection-bar action (up to three tasks) also brings subtasks, classification and clip proposals, and questions.
- **Existing descriptions are protected.** An AI description replaces yours only if you tick it; the old text is kept as an earlier version and can be restored.
- **Classification and the clip are proposals** with Accept/Reject; the primary clip is only ever set by a person's click.
- **Open questions** come from a person or from the AI, cite source blocks, and stay until resolved (review, approval dialog, ticket).
- **Board panel.** Editable properties stay visible there (status, assignee, dates are managed there); the main clip and any open question are added above them and the evidence is collapsed.

## Known gaps and changed assumptions

- **Isolation is limited (trusted personal pilot).** With every restriction this CLI version offers, a probe showed the model still lists some built-in tools (`functions.exec` failing closed, apply_patch, collaboration, MCP resource reads). They are bounded by the read-only sandbox, never-ask approvals, an empty temporary folder and no MCP servers, but a prompt is not a boundary. A direct API adapter would isolate input; it is not built. Two deprecated Codex feature flags were removed and web search (on by default) is explicitly disabled.
- **Model and allowance unknown.** Sign-in is confirmed by `login status`; the model used is not reported in the event stream (`reported_model` stays empty) and remaining allowance is only learned when a request is made. One request was tested live.
- **One request at a time**, text and captions only; no images or video are sent. Quantity checking is digits only: units and wording are not verified, and a draft can still be wrong in ways the checks cannot see.
- **No automatic contradiction detection.** The clean/dirty-log conflict appears only when a person flags it or the AI asks; the interface never decides it.
- **Ranges** apply to video only and are enforced by the player (seek, pause at end); a stand-in video that cannot decode was used in tests, so playback of a real clip with a range is verified by code reading and the persisted values, not by a recorded playback.
- **Caption wording** for suggestions that are only file hashes is generic ("GIF animation from 3.4. Gameplay Sequences Sample (1)"); rename to something better.
- **Grouping heuristics** are unchanged from Milestone 3 and still tuned on one brief; only Chrome on macOS and Python 3.14.6 were tested; keyboard operation was checked for the Move dialog only, screen readers not at all.
- **Milestone 4 is not started** (scenario filter, duplicate detection, changed-source matching).
- **Decisions:** IDEC-055 to IDEC-068. The `candidate` table was rebuilt to add the `ai` origin (migration 0004 with a foreign-key-checked rebuild); migration 0005 adds the AI tables, all empty unless AI is used.

## Questions

1. Should the board panel's property controls also collapse (badges only) in a later pass, or stay visible as management controls?
2. Is one active AI request at a time enough, or should batches queue?
3. Which model should the settings suggest, if any? It currently uses the Codex default.
