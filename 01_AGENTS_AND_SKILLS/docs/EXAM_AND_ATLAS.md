# LearningOS: exams and Learning Atlas

For the current beginner workflow, start with `STUDENT_GUIDE.md` in this directory. Version 0.3 is
goal-independent: `/start`, study normally, `/save`, `/continue`; exams and source uploads are optional.
The rich `study cycle` command records actual responses and dimensions; `METHOD_AND_EVIDENCE.md`
describes its recovery and integrity checks. Bare unaided-success labels are no longer accepted by
the legacy summary command. Identical session-close retries complete the original save safely.

## For everyday study

Double-click **LearningOS** on the desktop to build and open your Atlas.
Double-click **LearningOS Study Menu** for numbered choices. No terminal knowledge is required.
Python 3.11 or newer is required; no new Python package is needed for the upgrade.

On another Windows computer, open `01_AGENTS_AND_SKILLS/tools` and double-click
`Install LearningOS shortcuts.vbs` once. It creates the two desktop shortcuts without overwriting
existing ones. If Windows Script Host is unavailable, `LearningOS.cmd` still opens the Atlas;
`python learningos.py menu` opens the same study menu. Do not weaken Windows policy to enable scripts.

The Atlas is a read-only local report with embedded fonts and data. It does not upload your files,
run a tutor, or update learning state when you click a concept. It shows the build date, diagnostics
and which records are included. Reopen the launcher to build a new snapshot, then reload any old tab.
Your tutor should also rebuild it at the end of a study session.

In your tutor chat, use `/help`, `/today 45`, `/resume`, `/learn CRC`, `/test`, `/review`, `/mock`,
`/mistakes`, `/save` and `/atlas`. These are instructions understood by the tutor after reading
AGENTS.md, not globally installed commands. Normal sentences work too.

### First-time setup

1. In Study Menu, add a subject and select your local syllabus, notes or past papers.
2. Set up your exam, date, target and daily study budget.
3. Ask the tutor: "Map my official syllabus and assess my starting point."
4. Ask for source-backed practice questions with checked solutions. The tutor prepares the import.
5. Run practice from the menu, then inspect Exam performance and Error patterns in Atlas.
6. Finish a teaching session with `/save` so the next session starts from evidence.

During menu practice: `/skip` submits a skip, `/pause` saves your place, `/end` finishes with
unanswered questions unattempted. A local timer measures the time from question display to answer.
Ctrl+C preserves previously submitted answers. Resume the open test through the same menu.
The paper clock is diagnostic rather than an enforced examination lock. Pauses and interruptions
are displayed, and the viewer cannot prevent a learner opening their own source files.

## Implemented operations

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py atlas
python learningos.py menu
python learningos.py today --minutes 45 --save
python learningos.py exam setup --id my-exam --name "My exam" --subjects computer-networks --date 2027-06-01 --daily-minutes 90 --duration 180
python learningos.py exam import path/to/tutor-prepared-questions.json
python learningos.py exam start --exam my-exam --questions crc-1 --mode mock
python learningos.py exam show TEST_ID
python learningos.py exam submit TEST_ID crc-1 A --seconds 45
python learningos.py exam pause TEST_ID
python learningos.py exam resume TEST_ID
python learningos.py exam end TEST_ID
python learningos.py exam review TEST_ID
python learningos.py exam diagnose TEST_ID crc-1 --category calculation --basis confirmed --note "The XOR step was performed as ordinary subtraction."
python learningos.py exam retest TEST_ID
python learningos.py exam list
```

Use actual ids returned by commands. `exam submit --replace` records a revisit; both attempts remain
in history and the latest submitted response determines the final score. Timing totals retain every
attempt. Reported per-question times may be incomplete and are explicitly counted.

`exam import --replace` appends corrected questions. Tests freeze the question/key version at start,
so their historical scores remain reproducible. A correction never silently regrades past evidence.
Unverified/disputed questions cannot enter scored practice. Single-answer and exact-set multiple
choice and numeric-tolerance scoring are implemented. Complex partial-credit/written answers use
manual reported marking: `exam submit ... --awarded 2.5`. The beginner menu pauses manual items for
tutor assessment rather than asking learners to guess their own marks.

`exam retest` selects unseen verified variants sharing a failed question's family. It refuses when
none are available. The tutor must prepare/check variants and schedule delayed retesting; this
command does not pretend to have measured retention or resolved a misconception.

### Question import format

A JSON list. This is an illustrative template; the tutor replaces its ids, text and citations with
real indexed source material. The file is not imported automatically.

```json
[
  {
    "question_id": "crc-1",
    "exam_id": "my-exam",
    "subject_id": "computer-networks",
    "concept_ids": ["computer-networks:crc-relationships"],
    "prompt": "A generator polynomial has degree 4. How many remainder bits are appended?",
    "format": "single",
    "choices": [{"id": "A", "text": "4"}, {"id": "B", "text": "5"}],
    "answer": "A",
    "solution": "The remainder has as many bits as the generator degree. Check the cited source.",
    "marks": 1,
    "negative_marks": 0,
    "tolerance": 0,
    "source_id": "replace-with-indexed-source-id",
    "locator": {"page": 3},
    "origin": "generated",
    "verification": "unverified",
    "difficulty": 2,
    "family": "crc-remainder-size"
  }
]
```

Origins: `official`, `third_party`, `generated`. Verification: `verified`, `unverified`, `disputed`.
Formats: `single`, `multiple` (comma-separated choice ids), `numeric` (answer as a string), `manual`.
Every source id must exist in that subject's source index and every concept must be mapped there.
Provide a page or line locator. No imported file can overwrite source originals.
Whitespace/case-normalized exact duplicate prompts are rejected; this is not a semantic near-duplicate
detector. Use a shared `family` and exposure history to guard against memorized variants.

## Data architecture and compatibility

No server, hosted site or second database. All new exam records live in the existing append-only
event stream. `exam_engine.load()` replays current profiles, question versions, tests, diagnoses and
plans on demand; no migration rewrites old events. Existing mastery snapshots retain their schema
and heuristic. `backup` already includes the event log, so all exam evidence is archived and restored.
`search-memory` can search the new event payloads. Atlas regenerates its derived output.

New event types: `exam_configured`, `question_registered`, `practice_started`, `practice_attempted`,
`practice_paused`, `practice_resumed`, `practice_ended`, `practice_error_classified`, `study_plan_created`.
Their complete payload schemas are in `schemas/session_event.schema.json`; all readers must preserve
unknown future types rather than rewriting event history. Restoring older tools against newer events
requires restoring/updating the matching schemas and code; memory restore does not downgrade code.

Source-backed concept additions use `study map`, which validates the complete candidate catalogue
and references before writing, records an import note and adds only unassessed records. Existing
concept ids are refused to protect evidence. Edges are proposed study order until the tutor checks
them. Full official syllabus coverage remains unknown without an official syllabus mapping.

`study start`, `study record` and `study end` wrap the existing validated mastery/revision writers.
The end command refreshes the subject snapshot, next_session.md, checkpoint, session report,
workspace session pointer and Atlas. It does not modify unresolved open questions. Repeated closure
is refused rather than double-counting time. An interruption after an event but before its snapshot
still requires the documented validate/reconcile recovery procedure; nothing auto-repairs drift.

The Atlas covers registered subjects, concepts, events, queues, misconceptions, notes, handovers,
checkpoints, preferences, source metadata, exam results and plans. Backup contents, original/extracted
document contents, caches and duplicate generated artefacts are deliberately excluded. The manifest
lists input hashes and unavailable files. Corrupt inputs produce visible diagnostics and unavailable
views; originals are preserved. A failed build preserves the previous report.

## Design sources and assets

Editorial direction: warm paper, ink, terracotta, fine rules, restrained surfaces and generous spacing.
Newsreader is the reading/display serif; Instrument Sans handles controls and compact data.
The original font files and SIL Open Font License texts are bundled in `templates/atlas/fonts` and
embedded in every HTML report. No CDN requests or runtime font downloads.

- Newsreader source and design rationale: https://github.com/productiontype/Newsreader
- Instrument Sans source and design rationale: https://github.com/Instrument/instrument-sans
- Font distribution: https://github.com/google/fonts/tree/main/ofl/newsreader
- Font distribution: https://github.com/google/fonts/tree/main/ofl/instrumentsans
- Editorial reference inspected: https://www.anthropic.com/

The design is original LearningOS styling; it does not redistribute Anthropic's proprietary fonts.

## Practical limits

An exam's official sectional rules, adaptive weighting, complex partial marking and cohort rank data
must be configured or supplied. Current plans use transparent priorities, not a learned prediction
model. Unknown exam weightings stay unknown. Paper scores are observed marks, never pass probabilities.
The menu supports locally indexed material and tutor-prepared JSON rather than arbitrary test-provider
exports. OCR remains the existing manual step. Full browser-based exam simulation, automatic OCR,
provider-specific import adapters and statistically calibrated rank forecasts are future extensions.
