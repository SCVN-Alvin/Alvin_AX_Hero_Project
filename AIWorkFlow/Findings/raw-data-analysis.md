# RawData Creative Brief Findings

The sample is a Notion-style HTML export of an XP Hero creative production brief, accompanied by local reference media. It contains enough document structure to support deterministic extraction, but several creative decisions need human review.

## Source and inspection scope

Source: [Creative Human Face Log Defense Ruelle](../../RawData/Creative_Human%20Face%20Log%20Defense_Ruelle%203e307693313580bdbd14dda52d06dd01.html).

The inspection covered HTML text and structure, local asset paths, and the two map diagrams. The page layout was not verified in a rendered browser, and the three videos were not watched end to end. External links were inventoried rather than fetched.

The source metadata reports the following values. Treat them as a snapshot of the exported document, not live project status.

| Field | Source value |
| --- | --- |
| Title | Creative_Human Face Log Defense_Ruelle |
| Game | XP Hero |
| Creators | Ruelle and Zoe |
| DEV/ART | Alvin and Kevin; individual disciplines are not assigned by this combined property |
| Assignee | Zoe |
| Start date | September 21, 2026 |
| Due date | November 3, 2026 |
| Last edited | October 28, 2026 at 2:31 PM |
| Development status | In Development |
| Document status | Document Confirm DONE |
| Reported progress | Art 60 percent and Dev 80 percent |

## Content structure

The export contains two earlier revision sections, dated `01/07` and `20/09`, followed by three principal sections:

1. Goal and reference, including competitor and existing XP Hero videos.
2. A scenario table with video identifiers `001` through `012`.
3. Development and art details covering map, camera, characters, gameplay sequences, tuning values, sound, and cheat keys.

Shared mechanics include log selection and hammering, fence construction, net catching, wood shaving, resource stacking, tower and machine unlocking, combat rewards, a coin burst, log collapse, and territory expansion.

Scenarios vary the selected log, fence or net hook, failure and recovery sequence, ending, and AI intro. An AI intro is part of the creative brief; it does not require this organizer to contain an AI model.

## Local assets

| Type | Files |
| --- | ---: |
| PNG | 38 |
| GIF | 16 |
| JPG | 4 |
| MP4 | 3 |
| Total media | 61 |

All 61 unique relative asset paths referenced by the HTML existed at inspection time. The HTML contains 77 image elements; repeated elements do not represent 77 distinct image files. MP4 files appear as attachment links rather than embedded video elements.

External references include Google Drive, AppMagic, and advertising asset URLs. These require a separately downloaded copy for offline access.

The first map diagram shows a river with floating logs and trees on either side. The second shows the core area: a central giant log, a downstream net, right-bank harvesting and defenses, and a left-bank truck-selling area reached through a passage. The text limits the left bank to the ending to save production effort.

## Concrete requirements available for extraction

| Requirement | Source section |
| --- | --- |
| Intro approximately 10 to 12 seconds and core approximately 45 seconds | 3.1 Map |
| Continuous enemy spawning | 3.1 Map |
| High camera angle with a stated rotation of 50 degrees | 3.2 Camera |
| Different fence and net behavior by scenario | 3.4 Gameplay Sequences Sample |
| Existing saw machine in wrong-log scenarios | 3.4 Gameplay Sequences Sample |
| Archery tower costs of 5 and 15 wood logs | 3.5 Implementation information |
| Rotating ballista cost of 25 wood logs | 3.5 Implementation information |
| Saw machine cost of 50 coins | 3.5 Implementation information |
| Area expansion cost of 200 coins | 3.5 Implementation information |
| Hand shaving yields 10 wood logs and machine shaving yields 40 | 3.5 Implementation information |
| Prices must be adjustable | 3.5 Implementation information |
| Sound requirements and cheat keys Z, X, C, V, B | 3.6 Sound FX and Cheat keys |

## Ambiguities for review

| ID | Observation | Review action |
| --- | --- | --- |
| SRC-001 | Scenarios 003 and 007, and 004 and 008, appear duplicated in their described flow | Ask whether a variation is missing before merging |
| SRC-002 | AI scenario 010 describes a right-log net hook but references Video 003, which describes a wrong-log failed-fence flow; scenario 011 also conflicts with its Video 005 reference | Preserve both descriptions and verify the intended base scenario |
| SRC-003 | Wrong-log scenarios say to hammer the wrong log, while the later hammering instructions say to hit the clean log | Resolve which log is selected in those scenarios |
| SRC-004 | Continuous spawning is specified, while reward and sound sections refer to waves | Clarify whether wave wording implies different spawning behavior |
| SRC-005 | Default skeleton and winter polar-bear references are mixed; the scenario table labels the default theme | Confirm the initial theme and which animal references are illustrative |
| SRC-006 | Some numbered scenario steps repeat a number | Preserve order and original text rather than treating numbers as stable IDs |

These are findings from human inspection. They are not a promise that a rule engine can discover every semantic inconsistency.

## Implications for task organization

A single wood shaving section combines gameplay logic, animation, machine behavior, resource tuning, references, and scenario variations. Splitting only by document heading will produce incomplete discipline separation.

Store shared systems once, preserve scenario-specific requirements, and allow a person to split or merge generated candidates. A requirement can need both Art and Dev work. Media proximity provides a reference suggestion, not proof that a file belongs to a task.

Avoid inventing implementation work. Wood chip spawning and log depletion can be useful proposed subtasks, but require explicit source evidence or manual/template origin before being treated as confirmed requirements.
