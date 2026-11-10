# Milestone 3 Candidates and Approval Review

Milestone 3 supports the normal source-to-ticket workflow, but needs changes before acceptance. All 162 existing automated tests pass. Additional advisor probes reproduced three gaps involving requirement ownership and hierarchy edits. The user's feedback also establishes a product need: a task should lead with its main description and one chosen demonstration clip, while the full evidence remains available on demand.

## Scope and verification

Reviewed November 3, 2026 against the current working tree and the [Milestone 3 handoff](../Tasks/milestone-3-handoff.md). This is not a review of a final release commit; Milestone 3 files include uncommitted changes.

Tests used temporary storage and a separate project, Milestone 3 Advisor Test, prefix ADV. The user's live project records were not edited. The complete sample was imported, and additional probes used independent database snapshots.

| Check | Advisor result |
| --- | --- |
| Complete backend suite | 162 tests passed in 92.068 seconds |
| Sample candidate generation | 98 candidates: 15 task records including 2 subtasks, 63 requirements, 1 exclusion, 5 notes, 14 asset labels |
| Log hammering normal approval | Accepted category suggestion, manually set description and classification, attached one reference, and created ADV-1 with eight requirements |
| Wood shaving normal approval | Created ADV-2 parent and ADV-3/ADV-4 clean/bad wood subtasks; attached values preserve 10 and 40 wood logs |
| Regeneration | Added zero duplicate candidates after approval and retained decisions |
| Persistence | Reopening the database retained approved evidence and the single selected hammering reference |
| Earlier media-folder regression | Its new regression checks pass in the complete suite |
| Visual evidence | Reviewed implementer screenshots of the review workspace and approved task panel; these support the information-density concern |

Run the complete suite from `app/tests/`:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
```

The atomicity test intentionally injects a disk-full exception and logs it; the test passes. A fixture emitted an unclosed-file ResourceWarning. The previously intermittent concurrency test passed in this run.

The advisor did not complete an independent interactive browser walkthrough: native browser actions were interrupted by simultaneous browser activity. The implementer's CDP UI suite was not rerun. No new claim of a fully disconnected session, accessibility coverage, or other-browser support is made. Backend results and provided screenshots are identified separately above.

Machine-readable observations are retained in [backend results](evidence/2026-11-03-milestone-3/backend-results.json). The tests below extend the existing suite rather than assuming that passing it establishes every workflow.

## M3 REV 001 Requirement approval can lose its task association

Severity: Major. A source requirement can be marked approved but omitted from the ticket it belongs to.

Locations: [approval service](../../app/organizer/services/candidates.py) in `approve`, particularly parent validation and requirement ownership; [review interface](../../app/static/js/review.js) in `actionsBar` and `approveFlow`.

Reproduction:

1. Generate candidates from the real sample.
2. Select the requirement under Build fence that starts with The player moves to the designated fence position.
3. Approve that requirement alone, before approving Build fence. The interface offers Approve as shared requirement for requirement details, including child lines.
4. Approve Build fence.

Observed: the first approval creates zero board tickets, marks the requirement approved, and gives it no task owner. The later parent approval processes only needs-review children. The existing approved requirement still has zero task links and is absent from the parent's evidence.

Expected: a requirement already assigned to a candidate parent must remain attached to that parent when it becomes a ticket. A deliberately shared standalone requirement needs an explicit, discoverable workflow rather than silently losing its intended owner.

Fix: either require approval with the parent, or retain pending ownership and attach the existing requirement when the parent is approved. Do not create a duplicate requirement. The dialog must distinguish a standalone shared requirement from a child requirement.

Regression checks: child before parent, child and parent together, child after an approved parent, and a truly standalone shared requirement. Confirm source links, scenario links, and task ownership survive each route.

## M3 REV 002 Separate autosaved controls prevent a normal reparent operation

Severity: Major for hierarchy editing.

Location: [review interface](../../app/static/js/review.js), `structureFields`, lines 347 onward in the reviewed tree.

Reproduction: take a top-level task such as Net catch and make it a subtask of Build fence. The Level selector sends only `level: subtask`, which fails because no parent is present. The Parent selector sends only the parent ID, which fails while the candidate remains a task under another task. Sending both changes together through the service succeeds.

Expected: the interface can perform the valid operation without requiring an invalid intermediate state.

Fix: use one Move or Change hierarchy dialog that submits parent and level together. Filter eligible parents, explain restrictions, and keep the existing candidate unchanged on cancellation or rejection. Provide the reverse operation for moving a subtask to the top level.

Regression checks: top-level task to subtask, subtask to top-level task, moving between parents, and keyboard operation of the combined control.

## M3 REV 003 Parent demotion can save an invalid child hierarchy

Severity: Major. An accepted candidate edit can make subsequent approval fail.

Location: [candidate update service](../../app/organizer/services/candidates.py), `update_candidate` and `_check_parent`.

Reproduction: send one otherwise valid update that makes Wood shave a subtask under Build fence. Its two existing subtask children are retained. The update succeeds, leaving subtasks below a subtask. Approving Build fence then fails with A subtask cannot be placed under a subtask.

The approval transaction rolls back correctly and creates no partial board tasks. The defect is allowing the invalid draft hierarchy to be saved in the first place.

Fix: validate the candidate's children as well as its proposed parent before changing level or kind. Reject the change with a useful explanation, or require an explicit restructuring action. Apply equivalent validation to merge and split operations.

Regression checks: changing a parent level, changing a parent into a note, merging across incompatible levels, and maintaining valid hierarchy after a successful edit.

## Product finding Task drafts expose evidence before explaining the work

The real Log hammering candidate has an empty description, eight child requirements, nineteen family source associations, and seven suggested media references. Wood shave also starts with an empty description and six suggested references. These are separate from attachments: the suggestions are not automatically approved, but the user still has to navigate the surrounding information to formulate a ticket.

The screenshots show three simultaneously dense panes, long asset filenames, rule explanations, scenario labels, source text, and media. This supports traceability but does not give a clear first answer to what needs to be built.

Follow the [task presentation brief](../Design/task-first-review-flow.md): make the default work item a short description plus one selected primary demonstration; keep requirements, variations, rule explanations, and supporting media in secondary views. Preserve unresolved contradictions visibly.

## AI recommendation

Improve the presentation and reference selection before adding AI. AI can later draft an editable summary from selected source lines, but should not be required for classification, approval, storage, or offline use. A text-only request cannot verify the contents of a clip it has not inspected.

The [optional AI proposal](../Product/optional-ai-assistance.md) describes a bounded Draft description action, its review behavior, and the online/offline distinction. It is a proposal, not authorization to connect a provider or send project content.

## Next review gate

Resolve M3 REV 001 through 003 and add focused regression checks. Then deliver the task-first view using Log hammering and Wood shave as examples. Demonstrate a short editable description, exactly one chosen primary reference, optional supporting evidence, and correct source-linked approval. Hold final Milestone 3 acceptance until these checks pass; defer broader Milestone 4 expansion until this workflow is dependable.
