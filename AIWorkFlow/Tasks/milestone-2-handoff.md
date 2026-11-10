# Milestone 2 Handoff: Source Import

Delivered for advisor review on 2026-11-01. Milestone 3 has **not** been started.

This is an implementation handoff, not a product review. Nothing in [`Reviews/acceptance-checklist.md`](../Reviews/acceptance-checklist.md) has been ticked; the results below describe what the implementer ran. The advisor's own review report belongs in `Reviews/`.

## Completed tasks

| ID | Task | Status | Where to look |
| --- | --- | --- | --- |
| IMP-005 | Managed source and asset import with path validation and hashing | Delivered | `app/organizer/importer.py`, `fsbrowse.py`, `static/js/importer.js`, `filepicker.js` |
| IMP-006 | Ordered source blocks, heading paths, tables, historical sections, attachment links, scenarios | Delivered | `app/organizer/htmlparse.py`, `sourceparse.py`, `scenarios.py`, `migrations/0002_sources.sql` |
| IMP-007 | Sanitized source browsing and local image, GIF, and video previews | Delivered | `static/js/sources.js`, `blocks.js`, `media.js`, `server.py` (`/api/assets/<id>/file`) |

Review gate: *the source browser shows actual imported content, preserves historical context, and makes no automatic remote requests.* Every result below was produced by importing the real files in `RawData/` into a running application and browsing them in a real browser.

Not built, by plan: candidate generation, rules, review queue, requirement and scenario links on tasks, changed-source proposals, export and restore (Milestones 3 to 5).

## Launch and your existing data

```sh
cd /Volumes/Supercent/AIXJam/app
./run.sh                 # then open http://localhost:8765/
```

Nothing new to install (Python 3 only, as before).

**Your existing project data is kept.** The first start of this build upgrades the database from schema 1 to 2. The upgrade only adds tables, runs in one transaction, and first writes a complete copy next to the database (`app/data/organizer.db.bak-pre-v0002-<time>`). I did not touch your live `app/data/` folder (it still holds the project "Human face log", prefix HFL). I verified the upgrade on a copy of it: the project row is unchanged, `integrity_check` and `foreign_key_check` are clean, and the backup exists. An automated test repeats this with tasks, people, tags, criteria, and history, and another test confirms that a failing migration rolls back and keeps the data.

To try everything without touching your data at all:

```sh
./run.sh --data-dir /tmp/organizer-try --port 8766
```

## Import-testing instructions

### In the browser (about five minutes)

1. Open (or create) a project, then click **Import** in the header.
2. Click **Browse…** beside *HTML file*, go to `RawData/`, select `Creative_Human Face Log Defense_Ruelle 3e30….html`, and choose it. Leave *Media folder* empty; the folder next to the file is found automatically.
3. Click **Preview selection**. Expected: 223 blocks in 12 sections, 2 tables, **12 scenarios (001 to 012)**, historical sections *1st (01/07)* and *2nd (20/09)*, **61 files referenced, 61 found**, 956 MB, 7 web links kept but not fetched, and a note that a style sheet asked to load a CDN file and was not fetched. Nothing has been copied yet.
4. Click **Import brief**. It finishes in a few seconds and shows the counts. **Open the source browser**.
5. *Content*: the outline (historical sections badged), the page properties table, section 3.1 with the two map diagrams, section 3.5 with the unlock prices unchanged, 3.4 with *No build fence animation*. Click a picture to enlarge it. Large GIFs show a *Show here* button instead of loading.
6. Use the search box with `no build fence`, `king shot` (a hit inside a historical section is badged), and `TIẾNG` (matches `Tiếng`).
7. Use the `⋯` beside *1st (01/07)* in the outline and choose *Treat as current requirements*; the badge disappears and the choice survives a restart. Mark it historical again if you like.
8. *Scenarios*: 12 cards. Scenario 001 has 13 numbered steps; the second "10." is highlighted because the document repeats that number. *Original wording* shows the untouched cell. 009 to 012 contain only `Video 00x`, so they stay plain text.
9. *Media*: all 61 files, filters for images, GIFs, video and unavailable. Open one of the three videos with *Play*; it plays and seeks from the local service.
10. *Notes*: what the importer noticed.
11. Import the same file again: **Already imported**, nothing changes.

### A deliberately missing file

Copy the sample (about 1 GB, a few seconds) and delete one image:

```sh
cp -R /Volumes/Supercent/AIXJam/RawData /tmp/brief-test
rm "/tmp/brief-test/Creative_Human Face Log Defense_Ruelle/image 1.png"
```

(Remove the copy afterwards with `rm -rf /tmp/brief-test`.)

Import `/tmp/brief-test/…html` into a project (use a second project if you already imported the original there, so the two copies are not compared). The preview lists `Creative_Human Face Log Defense_Ruelle/image 1.png` as missing and still lets you import. In the source browser the picture shows a *Missing* card with its original path, and **Notes** and **Media → Unavailable** list it. To recover, restore the file (`cp` it back from `RawData/`) and import the same HTML again: the file is attached to the existing document, with no second copy of anything. I ran exactly these steps from the terminal as a check: the preview reported "60 found, 1 missing", the import completed with "60 of 61 files (1 unavailable)", the second import attached the file, and the third reported "Identical".

To see path safety at work, the automated tests import references such as `../secret.png`, `/etc/passwd`, `file:///…`, `a\b.png`, and symbolic links; each is reported as *blocked* and never read.

### From a terminal

The same pipeline without a browser; useful for scripting and for a scratch data folder:

```sh
cd /Volumes/Supercent/AIXJam/app
python3 -m organizer preview --project HFL --html "../RawData/Creative_Human Face Log Defense_Ruelle 3e307693313580bdbd14dda52d06dd01.html"
python3 -m organizer import  --project HFL --html "../RawData/…html" --data-dir /tmp/organizer-try   # --json for machine-readable output
```

`--project` accepts the id, the prefix, or the exact name; `--assets DIR` overrides the media folder.

## Evidence against the Milestone 2 completion criteria

| Criterion | Evidence | Result |
| --- | --- | --- |
| IMP-005 *Import the sample, resolve its 61 referenced local asset paths* | `SampleImportTests.test_all_61_referenced_local_paths_resolve` and the browser run: 61 asset rows, all `available`; 38 PNG, 16 GIF, 4 JPG, 3 MP4; names with spaces, parentheses and Unicode (`image 1.png`, `0256_(1).jpg`) decode correctly. Every file was copied and hashed (SHA-256); 3 pairs of files are byte-identical, so 58 are stored | Observed |
| IMP-005 *Report a deliberately missing file* | `ImportTests.test_import_resolves_assets_hashes_and_reports_missing_file`; browser run with a missing image: preview names it, the import still completes, the content shows a *Missing* card with the original path, Notes and Media list it; restoring the file and importing again attaches it without a second document | Observed |
| IMP-005 path validation | `TargetClassificationTests`, `PathSafetyImportTests`: absolute paths, `..` (also percent-encoded), backslashes, NUL, `file:`, Windows drive paths, symlinked files and folders, and references to siblings outside the media folder are blocked and nothing outside the folder reaches storage | Observed |
| IMP-005 failed import leaves no partial records | `test_failed_commit_leaves_no_partial_records_or_files` (failure injected mid-write: zero rows, no stray files, run marked failed, a normal import works afterwards); cancel; interrupted run marked at start-up | Observed |
| IMP-006 *Inspect the sample's 12 scenario rows and retained original wording* | `test_twelve_scenarios_with_identifiers_001_to_012` and the browser: identifiers 001 to 012, concepts, 100 numbered steps in total, scenario 001 keeps both "10." labels in order, 009 to 012 stay plain, *Original wording* shows each cell untouched | Observed |
| IMP-006 heading paths, tables, historical sections, attachment links | Heading paths for every block (for example *3.5 Implementation information*); tables stored as cells; `1st (01/07)` and `2nd (20/09)` detected as historical with a stated reason and kept viewable and searchable; the three `attachment:` MP4 links become video blocks backed by assets | Observed |
| IMP-006 resource values, negative instruction | Section 3.5 values (5 and 15 wood logs, 25 wood logs, 50 coins, 200 coins, 10 and 40 wood logs per shave) and *No build fence animation* appear exactly as written, under the right headings | Observed |
| IMP-007 *Inspect map images* | Section 3.1 shows its 24 images, including the river and core-area map diagrams; the browser scrolled through the section and every image decoded (`naturalWidth > 0`); screenshot `m2-05b-map-images.png` | Observed |
| IMP-007 *Play a local video while disconnected* | Chrome was started with a dead proxy and no DNS, and a navigation to `https://example.com/` failed as expected. In that browser the first MP4 loaded, reported its real dimensions and duration (matching `ffprobe` of the original), played, and seeked (byte ranges). The page made no request outside the local service, and none failed | Observed, with the limit below |
| IMP-007 sanitized preview, no remote requests | A hostile brief (script, handlers, `javascript:` link, remote image, iframe, SVG script, remote CSS import, entity-escaped markup) renders as inert text; `window.__pwned` stays undefined; nothing requested from the hostile host; the remote image shows as an *External image* placeholder. A test blocks all non-local sockets and name lookups during import and browsing | Observed |

**Limit of the offline claim:** the machine itself stayed online. The browser was cut off from the internet and the service made no outside connection (tested with a socket guard), but I did not unplug the network, so the full disconnected-session acceptance item still belongs in the Milestone 5 end-to-end run.

### Automated results

| Suite | Command | Result |
| --- | --- | --- |
| Backend, including Milestone 1 and the real sample | `cd app/tests && python3 -m unittest discover -s . -p "test_*.py"` | 107 tests, all passing (45 from Milestone 1, 54 new, 8 against the real sample). The sample tests skip themselves if `RawData/` is absent |
| Milestone 2 in headless Chrome, real sample, internet unreachable | `python3 app/tests/ui_check_m2.py [screenshot_dir]` | 88 of 88 checks passing on the final run (the previous 87-check version passed twice in a row after its last fix) |
| Milestone 1 in headless Chrome | `python3 app/tests/ui_check.py` | 64 of 64 checks still passing |

Screenshots from the final run are in [`milestone-2-evidence/`](milestone-2-evidence/): [preview](milestone-2-evidence/m2-01-import-preview.png), [done](milestone-2-evidence/m2-03-import-done.png), [source top](milestone-2-evidence/m2-04-source-top.png), [section 3.5](milestone-2-evidence/m2-05-section-3-5.png), [map images](milestone-2-evidence/m2-05b-map-images.png), [search](milestone-2-evidence/m2-06-search.png), [image viewer](milestone-2-evidence/m2-07-image-viewer.png), [scenarios](milestone-2-evidence/m2-08-scenarios.png), [media tab](milestone-2-evidence/m2-09-media-tab.png), [video](milestone-2-evidence/m2-10-video-playing.png), [GIF](milestone-2-evidence/m2-11-gif-viewer.png), [missing file preview](milestone-2-evidence/m2-12-missing-preview.png), [missing file in content](milestone-2-evidence/m2-13-missing-in-content.png), [sources list](milestone-2-evidence/m2-14-sources-list.png), [narrow layout](milestone-2-evidence/m2-15-narrow.png), [hostile content](milestone-2-evidence/m2-16-hostile-content.png).

Defects found and fixed along the way, so the advisor knows they existed:

- A rejected preview (wrong path) was shown as "Not saved" in the header and blocked leaving the page. Previews and job starts are no longer treated as saves.
- A reference containing an encoded NUL could be filed under the name of a legitimate file; paths with control characters now keep a visible escaped name and are blocked.
- A cell with one number-like word ("1. only one") was marked ambiguous instead of plain.
- The jump to a section hid its heading under the sticky search bar.
- A browser closing a connection mid-video printed a server stack trace.
- The header Import button did nothing while a finished import was on screen.

### Performance reading (informational, relates to OPEN-005)

On the development machine, with the files already in the operating system's cache: the 956 MB sample (61 files, 58 unique) imports in about 0.6 s end to end; importing it again (read and compare) also takes about 0.6 s. Reading the finished document: detail 1 ms, all 223 blocks 4 ms, search 1 ms, 61 assets 1 ms. The data folder holds 793 MB (58 stored files plus the HTML) and the database is 688 KB. A cold disk or a slower volume will be slower; this is one sample, not a benchmark.

## Known gaps

- **Not interpreted or built:** table cell spans and nested tables (cell content is flattened); math (KaTeX) and other Notion-only blocks beyond headings, lists, tables, images, attachments, quotes, callouts, toggles and code; web pages linked from the brief; any file type other than HTML.
- **No "Locate file" action.** A missing file is recovered by restoring it and importing the same brief again (IDEC-028, IDEC-039). The explicit action and media recovery are IMP-016.
- **No changed-source handling.** A different HTML file becomes a new version beside the old one; there is no diff, proposal, or matching yet (IMP-014).
- **Runs are not resumable**; an interrupted import is reported and retried from the start (IDEC-027).
- **Historical detection is a vocabulary of patterns** (IDEC-034). It correctly flags the sample's two revision sections and nothing else, but an unusual label would be treated as current; the one-click change exists for that reason. Scenario column names are likewise a starting vocabulary (IDEC-035).
- **Step segmentation is conservative** (IDEC-036): a cell with a stray non-numbered line between steps is kept whole.
- **Whitespace at the very start and end of a block or cell is trimmed** in the stored text (IDEC-032).
- **The folder picker lists folders on this computer.** It shows only folder and `.html` names, the service is bound to localhost, and `--import-root` confines it.
- **No thumbnails for large images or GIFs** (no image library; they load on request). Videos have no poster frame.
- **Limits:** HTML up to 64 MB; imports need free disk space for the media plus 64 MB.
- **Tested with Google Chrome (headless) only**, on macOS, with Python 3.14.6. Safari and Firefox, other Python versions, and Windows or Linux were not tried. Video playback needs H.264 support in the browser.
- **Keyboard and screen readers:** tabs, file picker rows, media tiles and the viewer are native buttons and a dialog, and opening a tile with Enter and closing with Escape were tested. There was no screen-reader session, no contrast measurement, and no full keyboard-only walkthrough.
- **Evidence files are not exported or backed up** beyond copying the data folder while the service is stopped (Milestone 5).

## Product assumptions I made (please confirm or change)

Full text and reasons are in the [decision log](../Decisions/decision-log.md#implementation-decisions-milestone-2), IDEC-024 to IDEC-041. The ones most likely to matter:

1. **Files are chosen by path with a folder picker, not uploaded through the browser** (IDEC-024), because the sample is 956 MB.
2. **Identical file contents are stored once** and every referencing path keeps its own row (IDEC-025).
3. **Re-import semantics** (IDEC-028): identical means nothing changes; the same HTML with a recovered file attaches it; different HTML is a new version.
4. **Historical sections are detected from heading text and are one click to change**, and a change applies to the section and its subsections (IDEC-034).
5. **Scenarios belong to the document that defines them**, so two briefs that both number scenarios `001` do not collide (IDEC-035).
6. **Page properties are kept as a read-only snapshot** and are never used as task data (IDEC-033).
7. **Removing an import is possible** and moves its files to `trash/` (IDEC-040); Milestone 3 must block it while tasks cite the document.
8. **Scenario tables in historical sections are not extracted** until the section is marked current (IDEC-035).

## Questions for the user or advisor

- Is path selection with a folder picker acceptable, or should a browser-upload option be added for use over a network share later?
- Should a changed brief (same name, different HTML) stay a plain new version for now, or should Milestone 3 already reuse the first version's section roles?
- Is the working vocabulary for historical sections right for your other briefs (revision labels such as `1st (01/07)`, "history", "archive")? If you use a different convention, tell me the wording.
- Do you want an explicit "Locate file" button now, or is "restore the file and import again" enough until Milestone 5?
- Is Chrome still the only browser that needs review?

## Next step

Awaiting review of Milestone 2. Milestone 3 (IMP-008 to IMP-011: candidate generation, editable rules, priority suggestions, split review and approval) will start only after the advisor responds.
