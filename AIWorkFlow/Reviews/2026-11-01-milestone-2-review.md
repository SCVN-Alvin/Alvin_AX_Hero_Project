# Milestone 2 Source Import Review

Milestone 2 meets the source-reading goal for the supplied RawData export: the running application displays the imported content, scenarios, and local media. The review is conditionally accepted, with one reproducible issue in explicit media-folder selection to fix before calling that feature complete.

The application is currently a source library plus a manual task board. Generating, classifying, and approving tickets from imported content remains Milestone 3 work.

## Review scope and evidence

Reviewed on November 1, 2026 using the current working tree, the implementer's [handoff](../Tasks/milestone-2-handoff.md), the live Chrome application at localhost:8765, and temporary test storage. The working tree contains uncommitted Milestone 2 changes, so this review is not tied to a final release commit.

The live project was Human face log, prefix HFL. Inspection navigated existing content without editing or deleting its records. Automated tests used temporary projects and data folders.

| Area | Advisor observation |
| --- | --- |
| Source inventory | Live interface reports 223 blocks, 12 sections, 12 scenarios, and 61 of 61 media references |
| Original content | Property values, scenario descriptions, source section paths, and the negative fence-animation instruction remain visible |
| Resource values | Section 3.5 displays tower costs of 5 and 15 wood, ballista cost of 25 wood, machine cost of 50 coins, expansion cost of 200 coins, and hand/machine outputs of 10 and 40 logs |
| Scenarios | The dedicated view preserves source identifiers and step order, highlights the repeated 10 label, and offers Original wording |
| Historical content | The outline distinguishes the two earlier revisions and explains their exclusion from future candidate generation |
| Media library | Live filters show 16 GIFs, 3 videos, and zero unavailable files |
| Image preview | Opened the core map Frame_1272628725.png; the river, net, defenses, and selling area render correctly |
| Video preview | Opened a local MP4, started playback, and confirmed elapsed time advanced to 0:23 before pausing and closing it |
| Next-stage affordances | Import Review and Rules are visibly marked Soon rather than pretending to generate tasks |

The source reader, scenario view, and enlarged map are readable in the observed desktop layout. Large GIFs are loaded on demand, which is appropriate for this sample's substantial media sizes.

## Verification results

Command from `app/tests/`:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
```

The full authorized run executed 107 tests in 58.024 seconds: 106 passed and one concurrency test failed with `URLError(OSError(22, 'Invalid argument'))`. Source-import and real-sample tests passed, including reimport, storage, source wording, and media checks.

The isolated rerun below passed in 0.585 seconds:

```sh
python3 -m unittest test_tasks.ConcurrencyTests.test_parallel_creates_get_unique_gapless_ticket_numbers
```

This is evidence of an intermittent transport or test-environment problem, not a clean all-pass full-suite run. No duplicate-ticket or data-corruption result was observed. Track the transient failure if it repeats. Several test fixtures also emitted unclosed-file ResourceWarnings; these are test cleanup issues rather than observed product failures.

An initial sandboxed run could not bind localhost test sockets and was superseded by the authorized run. The advisor did not rerun the implementer's CDP-based UI suite; interface observations above came from the running Chrome application.

The machine stayed online. Physical disconnection, other browsers, large-scale performance, and a complete keyboard/screen-reader walkthrough remain unverified by this review.

## M2 REV 001 Explicit media folder fails after a rename

Severity: Major for the explicit media-folder override; not a blocker for the supplied sample with its original directory names.

Code location: [locate_asset in importer.py](../../app/organizer/importer.py). The relevant branch compares the first path segment against the selected folder's current basename, then falls back to the HTML directory.

Reproduction using temporary files and the actual resolver:

1. Create an HTML file containing an image reference to `Brief/image.png`.
2. Place the image inside a folder named `RenamedMedia`.
3. Explicitly choose `RenamedMedia` as the media folder through `resolve_selection`.
4. Resolve `Brief/image.png` through `locate_asset`.
5. The result is `blocked` with the message that the path points outside the selected companion folder.
6. Rename the same folder to `Brief` and repeat. The same image becomes `available`.

Expected behavior: an explicitly selected companion folder can map the export's original media-root prefix to its actual location, including a renamed folder, without requiring the user to rewrite the HTML.

Actual behavior: the selected override works only when path resolution still agrees with its current name or the HTML's original layout. This makes an apparently valid selection reject available files and prevents straightforward relocation of media.

Fix guidance: distinguish the original export media-root prefix from the selected physical folder. Use an explicit or safely inferred root mapping and validate the mapped path inside the chosen folder. Preserve traversal and symlink protections; do not fix this by accepting arbitrary paths outside the selected folder.

Verification needed: original folder, moved folder with the same name, renamed folder, nested relative paths, missing image, and attempts to escape the selected root. Add a regression check for the confirmed renamed-folder case.

## Product recommendations for the next increment

The next useful increment is the complete wood-shaving workflow, followed by broader extraction. Avoid spending the next milestone on another read-only source view.

1. Add Generate candidates from an imported source, with current sections selected and historical sections excluded by default.
2. Present the proposed wood shaving parent and source-supported children beside their source blocks.
3. Attach original wording, related media suggestions, and relevant configuration values without inventing requirements.
4. Suggest Art, Dev, category, and impact with editable rule evidence. Leave urgency unspecified when the source does not establish it.
5. Let the user split, merge, reparent, dismiss, and approve candidates.
6. Create board tickets only after approval and retain links back to the exact source blocks.
7. Preserve approvals, dismissals, and manual edits when generation is repeated.

Important extraction detail: labels such as Wood shave, Build fence, and Net catch are paragraphs within section 3.4, not independent HTML heading sections. Scenario labels and following bullets establish local scope. A heading-only implementation would combine unrelated mechanics into an oversized ticket. Detect supported subgroups conservatively and let users adjust boundaries.

Likewise, the tuning values live in section 3.5. Offer them as cross-section evidence or let the user attach them; do not assume that nearby text alone connects every value to every task.

## Handoff decisions

- Keep local path selection as the initial import method. Fix M2 REV 001 rather than adding browser uploads now.
- Keep changed HTML as a separate source version for this milestone. Preserve manual role decisions when future revision matching is introduced; that remains IMP-014.
- Keep missing-file recovery by restoring the file and reimporting until the planned Locate file work in IMP-016.
- Retain editable historical labels and use additional real briefs to extend the vocabulary. Do not hardcode every future export to the sample.
- Use Chrome as the current demonstrated browser; do not claim coverage of other browsers.
- When Milestone 3 adds source-backed candidates or tasks, prevent source removal from destroying their evidence. The current Remove message saying tasks are unaffected must change with that relationship.

## Next review gate

Fix M2 REV 001 and supply focused verification. Then implement IMP-008 through IMP-011, starting with the wood-shaving flow above. Milestone 3 is accepted only when approved, classified tickets appear on the real persistent board with source references; source display alone does not satisfy it.

The general acceptance checklist remains a release checklist. This report records the checks actually performed without marking deferred product behaviors as passed.
