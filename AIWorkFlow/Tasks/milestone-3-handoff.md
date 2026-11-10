# Milestone 3 Handoff: Candidates, Rules, Review, and Approval

Delivered for advisor review on 2026-11-01. Milestone 4 has **not** been started.

This is an implementation handoff, not a product review. Nothing in [`Reviews/acceptance-checklist.md`](../Reviews/acceptance-checklist.md) has been ticked; the results below describe what the implementer ran. The advisor's own report belongs in `Reviews/`.

The advisor's [Milestone 2 review](../Reviews/2026-11-01-milestone-2-review.md) asked for the media-folder fix first and then the wood-shaving flow. Both are done, and the user confirmed the open Milestone 2 questions (folder picker is fine, changed HTML stays a new version, the historical vocabulary is fine, re-import is enough for missing files, Chrome only for now).

## Completed

| ID | Task | Status | Where to look |
| --- | --- | --- | --- |
| M2 REV 001 | Explicit media folder works after a rename or move | Fixed | `app/organizer/importer.py` (`locate_asset`, `_common_prefix`), `tests/test_sources.py` |
| IMP-008 | Section-based task and requirement candidates that keep source text, scenario restrictions, and resource values | Delivered | `organizer/candidates.py`, `services/candidates.py`, `migrations/0003_candidates.sql` |
| IMP-009 | Editable global and project rules with evidence and conflicts | Delivered | `organizer/rules.py`, `services/rules.py`, `static/js/rules.js` |
| IMP-010 | Priority suggestions with impact and urgency kept separate | Delivered | candidate detail in `static/js/review.js`; policy unchanged in `priority.py` |
| IMP-011 | Split review, bulk classification, approval, and dismissal | Delivered | `static/js/review.js`, `services/candidates.py` |

Review gate: *approved, classified tickets appear on the real persistent board with source references; source display alone does not satisfy it.* The wood-shaving system was approved in a running browser against the real brief and is on the SQLite-backed board with its original wording, requirements, scenarios, and attached media (screenshot [`m3-10`](milestone-3-evidence/m3-10-ticket-with-source.png)). It survives a restart.

Not built, by plan: duplicate suggestions and review issues, scenario filtering and link management after approval, merging approved tickets, changed-source proposals, export and restore (Milestones 4 and 5).

## Launch and your existing data

```sh
cd /Volumes/Supercent/AIXJam/app
./run.sh                 # then open http://localhost:8765/
```

The first start of this build upgrades your database to schema 3 (the new tables are additive; a complete copy is written first as `organizer.db.bak-pre-v0003-<time>`). Your projects, tasks, imported sources and media are kept; a test upgrades a Milestone 1 database through every migration and compares each table. The upgrade also adds five global rules (see Rules below). To try everything without touching your data: `./run.sh --data-dir /tmp/organizer-try --port 8766`.

## Try the wood-shaving example (about eight minutes)

Use the project that holds the imported brief (or import it as in the [Milestone 2 handoff](milestone-2-handoff.md)).

1. Open **Sources**, the brief, then **Review candidates** (or **Import Review** in the sidebar). Press **Generate candidates…**. The two historical sections are unticked; leave them. Press **Generate**: 98 candidates are listed as 13 **Tasks**, loose requirements, notes, and asset labels. The board is still empty.
2. Select **Wood shave**. The middle pane shows section 3.4 with the label, the two *Scenario …* headings, the lines, and the pictures **highlighted by role** (label, text it came from, scenario scope, media). The right pane shows the proposal: two **subtasks**, *Shave clean wood* (scenarios 001, 002, 005, 006) and *Shave bad wood* (003, 004, 007, 008), each with its original lines.
3. **Classification.** Under Category, the rule *Gameplay section (global rule)* suggests Core gameplay and shows the matched heading. Nothing is set yet. Press **Accept**. Tick **Art** and **Dev**. Set **Impact** to High: Priority shows **P1** with “Impact High suggests P1 under the default policy”, and P0 is only offered as an explicit override.
4. **Values.** Under *Possible values and requirements from elsewhere*, section 3.5's **Wood logs per shave** (`10 wood logs`, `40 wood logs`) is offered with the reason (shared words). Press **Attach here**. Other 3.5 items are offered to *Unlock items* (saw machine 50 coins, expansion 200 coins, tower costs); attach or ignore them.
5. **Media.** Pictures labelled *Handheld wood shaver* and *Saw machine* in section 3.1 are suggested with their reasons, as are the pictures right under the label. Open one; then **Attach all** (or attach individually). Only attached media will go onto the ticket.
6. **Rename** it to *Implement wood shaving system*; the source wording (*Wood shave*) stays visible under the title.
7. **Rules.** Open **Rules**, **Add project rule**: name “Central mechanic”, look in the title, phrase `wood shav*`, suggest Impact **Critical**. The preview lists the candidates it would affect, with the matched wording, before you save. Save it: on this candidate the value you already set is kept and the rule's suggestion is shown as *not used*.
8. **Exclusion.** Open **Build fence**: *No build fence animation* is an **Exclusion** line for scenarios 002, 004, 006, 008. It is not a task, and it did not trigger the animation rule.
9. **Approve.** Back on *Implement wood shaving system*, **Approve onto the board…**. The dialog says what will be created (the task, 2 subtasks, N requirement lines), and that unaccepted suggestions and unattached media are not applied. Approve.
10. Open the **Board**: the card is in Backlog with Art, Dev, P1, Core gameplay and 0/2 subtasks. Open it: **Source, requirements and media** shows the original *Wood shave* text with a link to the exact block, the attached values, and the attached pictures. Open a subtask to see its lines with scenario chips. Category reads *Accepted rule suggestion*; Disciplines and Impact read *Manually set*.
11. Optional: back in Import Review, **Generate more…** adds nothing and keeps everything; **Merge…** (select two tasks) previews before doing anything; **Split this task…** moves lines into a new candidate; **Dismiss** keeps a candidate out of future generations.

## Evidence against the advisor's recommendations

| Recommendation | Evidence | Result |
| --- | --- | --- |
| 1. Generate from an imported source, current sections only by default | Generate dialog ticks current sections and unticks historical ones; no candidate cites a historical block (`test_historical_sections_are_not_read_by_default`); choosing a historical section adds its candidates | Observed |
| 2. Wood shaving parent and source-supported children beside source blocks | Parent *Wood shave* with subtasks *Shave clean wood* and *Shave bad wood* from the two scenario scopes; source pane highlights label, lines, scope and media (`m3-04`) | Observed |
| 3. Original wording, media suggestions, configuration values, no invented requirements | Lines keep exact text; `Wood logs per shave:\nBy hand: 10 wood logs\nBy saw machine: 40 wood logs`, `50 coins`, `200 coins`, `5 and 15 wood logs`, `25 wood logs` are offered with exact quantities; media comes with reasons; no wood-chip or other unsourced task is generated | Observed |
| 4. Art, Dev, category, impact suggestions with editable evidence; urgency unspecified when unestablished | Suggestions show rule, scope, version and the matched text; urgency is never suggested (no rule produces it by default); unclear fields stay Unassigned/Unassessed | Observed |
| 5. Split, merge, reparent, dismiss, approve | Backend tests cover each. The browser run exercised dismiss and the Dismissed tab, bulk classification, a merge preview and cancelling it (`m3-11`), a split into a subtask, attaching a value, and approval. Confirming a merge and the parent dropdown were tested through the API only | Observed, with that limit |
| 6. Board tickets only after approval, linked to exact source blocks | Candidates create nothing until approval; `task_source`, `requirement_source` and `task_reference` rows link to block ids; the ticket's *Open in the source browser* link lands on the block | Observed |
| 7. Preserve decisions when generation repeats | `test_regeneration_creates_no_duplicates_and_keeps_decisions`, `test_regenerating_after_approval_…`, and the browser check | Observed |
| Heading-only splitting would build an oversized ticket | Labels (emphasised paragraphs) and column layout split section 3.4 into Log hammering, Build fence, Net catch, Wood shave, Unlock items, Massive coin burst, Wood log collapses, Expand new area | Observed |
| Cross-section values are offered or attached by the user, not assumed | Values are never attached automatically (`test_cross_section_values_are_offered_not_attached`) | Observed |
| Source removal must not destroy evidence | A document that approved tasks cite cannot be removed (409 with the count); the Remove message was updated | Observed |
| M2 REV 001 renamed media folder | `test_explicit_media_folder_works_after_a_rename_or_move` covers the original layout, a moved folder with the same name, a renamed folder, nested paths, a missing image, traversal, and a symlink; a second test confirms a sibling file outside the chosen folder is still blocked | Fixed, observed |

### Other checks worth knowing about

- **Negation.** The exclusion is stored as an exclusion requirement on its task with scenarios 002, 004, 006, 008; a rule preview for `build fence animation` matches nothing, because the only text containing it is the exclusion.
- **Creative failure is not a bug.** A candidate titled *Fail to defend* gets no Bug fix suggestion; only explicit defect wording (bug report, defect, crash, regression) does.
- **Conflicts.** Two equal project rules that disagree show as *Conflicting*, nothing is chosen, and accepting one marks the other *Not used*. A project rule outranks a global one.
- **Manual edits survive.** Rerunning rules and generating again never change a value set by hand, an accepted or rejected suggestion, a dismissal, or a rejected picture.
- **Atomic approval.** A failure injected on the second task leaves no tasks, no requirements, no spent ticket number, and the candidates still awaiting review.
- **Unresolved scenarios.** In a synthetic brief that names `Scenario 003 + 004 + 099`, the line is flagged because the document defines no scenario 099, and no link is made to it. The real brief has no such case.
- **Nothing external.** The browser ran with a dead proxy and no DNS; every request went to the local service and none failed.

### Automated results

| Suite | Command | Result |
| --- | --- | --- |
| Backend, including Milestones 1 and 2 and the real sample | `cd app/tests && python3 -m unittest discover -s . -p "test_*.py"` | 162 tests, all passing (109 from Milestones 1 and 2, 53 new for candidates, rules, review, approval, atomicity, regeneration and evidence, 6 of them on the real brief) |
| Milestone 3 in headless Chrome, real brief, internet unreachable | `python3 app/tests/ui_check_m3.py [screenshot_dir]` | 64 of 64 checks passing |
| Milestone 2 in headless Chrome | `python3 app/tests/ui_check_m2.py` | 88 of 88 still passing |
| Milestone 1 in headless Chrome | `python3 app/tests/ui_check.py` | 64 of 64 still passing |

Screenshots from the final run are in [`milestone-3-evidence/`](milestone-3-evidence/): [before generating](milestone-3-evidence/m3-01-before-generate.png), [generate dialog](milestone-3-evidence/m3-02-generate-dialog.png), [candidates](milestone-3-evidence/m3-03-candidates.png), [Wood shave beside its source](milestone-3-evidence/m3-04-wood-shave-beside-source.png), [classified with rule evidence](milestone-3-evidence/m3-05-classified-with-evidence.png), [the exclusion](milestone-3-evidence/m3-06-exclusion.png), [rule preview](milestone-3-evidence/m3-07-rule-preview.png), [approval dialog](milestone-3-evidence/m3-08-approve-dialog.png), [after approval](milestone-3-evidence/m3-09-after-approval.png), [ticket with source](milestone-3-evidence/m3-10-ticket-with-source.png), [subtask with source](milestone-3-evidence/m3-10b-subtask-with-source.png), [merge preview](milestone-3-evidence/m3-11-merge-preview.png), [narrow layout](milestone-3-evidence/m3-12-narrow.png).

Defects found and fixed along the way, so the advisor knows they existed:

- *Shave bad wood:* was read as a context line, so the second scenario scope never became a subtask. Sub-labels that stand alone are now recognised.
- The 3.5 price list under a bold *Unlock prices:* label became one oversized task; labelled lists that are mostly quantities are now configuration lists.
- Quantities swallowed a trailing preposition (`50 degrees to`); the unit is now at most two real words.
- Bulk media actions acted on attached pictures instead of the undecided ones.
- Tab counts counted every requirement line as something to review; they now count top-level items.
- Ticket field history recorded accepted rule suggestions as manual; it now distinguishes rule from manual.
- Deleting a task left its requirements and its candidate behind; it now removes the requirements and returns the candidates to dismissed.

### On the intermittent test failure the advisor saw

`test_parallel_creates_get_unique_gapless_ticket_numbers` failed once with `OSError(22)` in the full run and passed alone. I could not reproduce it. The most likely cause is the standard library's listening queue of 5 refusing a burst of connections, so the queue is now 128 (IDEC-054). That is a hypothesis; if the failure returns, treat it as open.

## Known gaps

- **The grouping rules are heuristics tuned on one real brief.** They find the sample's labelled mechanics and subtasks, but also propose a few things a person would drop: *1st floor (Core)* becomes a task whose lines are the map's layout and timing notes; short caption lines decide asset labels (50 characters); the note vocabulary (Important, Goal, Reference, Scenario …) is small. Another brief will need its own tuning (`candidates.py` constants) and every wrong guess is one click to dismiss, retype, move, or split. I have not tested a second brief.
- **Loose values are matched to tasks by shared words**, which can point at the wrong task (the offer for *Coin reward per enemy defeated* goes to *Massive coin burst*). They are offers, never automatic.
- **Rules are exact phrases.** There is no understanding of meaning, the Vietnamese phrase lists are minimal, and rules run only on task candidates. A rule preview covers one document at a time.
- **Scenario links are created at approval and shown read-only.** Adding, removing, filtering on scenarios, dependency suggestions, duplicate detection, and review issues are Milestone 4 (IMP-012, IMP-013). The board has no Scenario filter yet.
- **Approved requirements cannot be edited** in the interface (their original text is evidence). An approved candidate cannot be un-approved except by deleting its task, which returns its candidates to *dismissed*.
- **Merging approved tickets** is not possible (IMP-013); merge and split work on unapproved candidates only.
- **A changed source** (version 2) is not matched to version 1's candidates (IMP-014): generating on it proposes everything again.
- **Epics** are never proposed; a person can set a candidate's level to Epic.
- **Priority** is shown and explained on the candidate and the ticket, but urgency is never suggested because no default rule establishes it.
- **Limits of testing:** Google Chrome (headless) only, macOS, Python 3.14.6. No screen-reader session or contrast measurement; the review workspace uses native controls and dialogs, and keyboard operation of the approval flow was not separately tested. The offline claim has the same limit as Milestone 2: the machine itself stayed online.

## Product assumptions I made (please confirm or change)

Full text and reasons: [decision log](../Decisions/decision-log.md#implementation-decisions-milestone-3), IDEC-042 to IDEC-054.

1. **Candidates are a separate table**, and a ticket number is only spent at approval (IDEC-042).
2. **A task is a labelled group; bullets are requirement lines, not tasks** (IDEC-043). Promote a line with *Make it a subtask…*.
3. **Scenario scopes become subtasks only when every scope opens with its own sub-label**; otherwise one task keeps lines with different scenarios (IDEC-044).
4. **Approval confirms the scenario links** named in the source lines (IDEC-050).
5. **Rule suggestions and unattached media are not applied at approval**; the dialog says so (IDEC-050).
6. **The seeded *Gameplay section* rule suggests Core gameplay for any heading containing "gameplay"** (IDEC-048).
7. **Deleting a task returns its candidates to dismissed** rather than to review (IDEC-051).
8. **The configuration-list and caption thresholds** (four lines, 40 percent quantities, 50 characters) are starting values (IDEC-043).

## Questions for the user or advisor

- Should *configurable output values* (the `Wood logs per shave` block plus *prices should be adjustable*) be offered as a ready-made subtask of the wood-shaving task, or stay a requirement you attach and can promote with one click?
- Should approval also attach the picture suggestions that sit directly under a label (they are the strongest guesses), or keep every picture an explicit choice?
- Which other briefs can I test the grouping against before Milestone 4? One real brief is not enough to know how often the label and caption rules misfire.
- Should the board get its Scenario filter now (it needs the links this milestone created) or with IMP-012?

## Next step

Awaiting review of Milestone 3. Milestone 4 (IMP-012 to IMP-014: task requirements and scenario links with validation, duplicate suggestions and merge previews, changed-source proposals) will start only after the advisor responds.
