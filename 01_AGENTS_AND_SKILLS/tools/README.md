# `learningos.py` — the LearningOS command line

This directory holds the only executable part of LearningOS: a standard-library Python CLI that
creates, checks and safely writes the files the agent teaches from. The teaching behaviour itself
is not here — it lives as prose in `01_AGENTS_AND_SKILLS/core/` and `01_AGENTS_AND_SKILLS/skills/`
and is read by the agent, not by this script.

Run everything from this directory:

```powershell
cd C:\Study\LearningOS\01_AGENTS_AND_SKILLS\tools
python learningos.py --help
python learningos.py validate
```

The CLI finds the workspace root by looking for `learningos.config.json`: `--workspace PATH` first,
then the `LEARNINGOS_HOME` environment variable, then by walking up from its own location. So it
also works when invoked by absolute path from anywhere.

---

## Requirements

| | |
|---|---|
| Python | 3.11 or newer, on `PATH` as `python`. Developed and tested against 3.11.9. |
| Third-party packages | **none required.** Every subcommand works on a bare interpreter. |
| Platform | Windows (PowerShell) is the tested path; the code is pure `pathlib`/`os` and has no Windows-only calls. |
| Network | never used. No module under `tools/` imports `socket`, `urllib`, `requests`, `http.client`, `ftplib` or `smtplib`, and `tests/test_l_privacy_nondestructive.py` fails the build if one does. |

### Optional dependencies, and what you lose without each

| Dependency | Buys you | Without it |
|---|---|---|
| [`pypdf`](https://pypi.org/project/pypdf/) (`python -m pip install pypdf`) | text extraction from PDFs during `index` | PDFs are still indexed — id, sha256, size, mtime — but no text is extracted, `needs_ocr` is recorded as `null` rather than `true` ("we could not look" is not the same claim as "there is nothing to read"), and the run prints the install hint and exits 0 |
| [`@mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli) (`mmdc` on `PATH`) | a real `.svg` per graph | `update-graphs` prints `SVG skipped: mermaid-cli (mmdc) not found on PATH`, writes the `.json`, `.mmd` and `.html` anyway, and exits 0. `--require-renderer` turns the absence into exit 6 |
| a hand-placed `mermaid.min.js` in `01_AGENTS_AND_SKILLS/tools/graphs/vendor/` | the generated HTML page renders the diagram offline, with no CDN request | the page still opens and shows the Mermaid source as readable text plus a notice explaining both renderer routes. See the README in that directory |
| [`jsonschema`](https://pypi.org/project/jsonschema/) | a second opinion under `validate --strict` | nothing changes: the bundled subset validator in `schema.py` is the primary path, and `validate` never depends on an installed package. Its absence is not even a warning |

Nothing is ever installed, downloaded or auto-updated for you. `python learningos.py status`
reports which of the four are present in the current environment.

---

## Exit codes

Every subcommand returns one of these. `main(argv)` returns the int; only the `__main__` guard
calls `sys.exit`, which is what lets the test suite drive the CLI in-process.

| Code | Meaning | Typical cause |
|---|---|---|
| `0` | success | including "nothing to do", a second idempotent run, and a search with zero hits |
| `1` | unexpected error | a bug: prints a one-line message plus a remedy |
| `2` | usage error | bad or contradictory arguments, a malformed `--id`, `--exam-date` or `--since` |
| `3` | validation or data failure | a corrupt or schema-invalid file, a dangling concept reference, a failed archive verification. **Nothing is written** |
| `4` | not found | unknown subject, unknown session id, no checkpoint to resume from, no such backup |
| `5` | refused a destructive operation | `restore` onto existing files without `--force` |
| `6` | required optional dependency missing | `update-graphs --require-renderer` with no `mmdc` on `PATH` |

A failure prints `error: <path><pointer>: <message>` followed by a `remedy:` line on stderr. Data
findings also carry a JSON pointer into the offending file.

### Global flags

Accepted before or after the subcommand:

| Flag | Effect |
|---|---|
| `--workspace PATH` | the LearningOS folder to act on |
| `--json` | machine-readable output on stdout instead of the human report |
| `-q`, `--quiet` | print only what matters: errors, and nothing on success |
| `--version` | print the tool version and exit |

---

## Subcommands

### `start`

Goal-independent setup: select or create a subject, optionally record a purpose/mode and starting
topic. No exam or document is required; a source-free topic is clearly labelled general-background
and unassessed. Example: `python learningos.py start --subject Biology --topic Inheritance`.

### `continue`

Complete known interrupted learning saves, then resume the selected subject from its handover.
Divergent/corrupt records are preserved and reported. Example: `python learningos.py continue --subject biology`.

### `guide`

Show the simple student workflow and precisely what is stored. Example: `python learningos.py guide`.
The four everyday actions are start, study normally, save, continue. Students use chat or the menu;
the tutor prepares structured evidence and manages the files.

### `atlas`

Build a self-contained offline Learning Atlas from real memory. No network or source-state writes.
Example: `python learningos.py atlas`. The launcher opens the generated report in your browser;
an already-open page needs reloading after regeneration. Invalid inputs remain visible as diagnostics.

### `menu`

Open a numbered, beginner-friendly menu for exams, sources, practice and results.
Example: `python learningos.py menu`. This command is interactive; agents use its noninteractive
equivalents instead. Desktop users can launch `LearningOS.cmd menu`.

### `today`

Explain a study plan within the learner's time budget. Priority uses due retrieval, prerequisite
leverage, current evidence and recent lost marks. Example: `python learningos.py today --minutes 45`.
Use `--exam ID` to limit scope and `--save` to record the plan without claiming learning occurred.

### `exam`

Manage local exam profiles, verified question imports, timed practice, paused tests, revisits,
score reviews, error diagnoses and unseen-family retests. Example: `python learningos.py exam list`.
Use `exam --help` for actions and `exam ACTION --help` for exact arguments. Every operation records
versioned evidence; test scores never promote learning mastery. Read the exam/Atlas guide under docs
for the question-import shape, manual partial marking and practical limits.

### `study`

Validated tutor writers for source-backed concept mapping and learning-session lifecycle.
Example: `python learningos.py study start --subject computer-networks`. Actions: `start`, `map`,
`record`, `end`. Actual judged attempts use `record`; it preserves hinted versus independent evidence.
`end` requires an outcome and exact next action, then saves the handover, checkpoint, report and Atlas.

### `init-workspace`

`[--dry-run] [--force]`

Creates the three principal directories, the root entrypoints, the global memory files, the empty
event log and the `_template` copies. Existing files are listed as `skipped` and never rewritten,
so it is safe to re-run into a folder that already holds learning history. When the target is a
different folder from the install this script lives in, the shipped instruction files, schemas,
templates and the CLI are copied into it as well — that is what makes
`init-workspace --workspace D:\Anywhere\LearningOS` produce a complete, portable install.
`--force` re-copies those installer files only; it can never touch anything under `02_KNOWLEDGE/`
or `03_MEMORY/`. Exit codes: 0.

### `init-subject`

`--id SUBJECT_ID --name NAME [--exam-date YYYY-MM-DD] [--description TEXT] [--dry-run]`

Creates `02_KNOWLEDGE/subjects/<id>/` and `03_MEMORY/subjects/<id>/` from the templates,
substitutes the id, name, exam date and timestamps, registers the subject in `workspace_state.json`
and `learner_index.md`, and appends one `subject_initialized` event. `--id` must match
`^[a-z0-9][a-z0-9_-]*$`; `_template` and `_cross` are reserved. It never overwrites an existing
file, never reads or copies another subject's mastery (a new `concept_mastery.json` has
`concepts == {}`), and a second identical run is a no-op that changes no file hash. Exit codes: 0
created or already present, 2 bad id or date, 3 the existing subject is corrupt.

### `index`

`[--subject SUBJECT_ID] [--all] [--inbox] [--assign-to SUBJECT_ID] [--copy] [--force] [--strict] [--dry-run]`

Walks a subject's `source_materials/`, records one entry per document in `source_index.json`
(stable `source_id`, sha256, byte size, mtime, page count, warnings) and writes the derived text to
`extracted_text/<source_id>.txt` with a provenance header and `--- page N ---` markers. Originals
are opened read-only and never modified. A source is re-extracted only when its hash, size or mtime
changed, when its derived text is missing, or with `--force`; a genuine no-op leaves
`source_index.json` byte-identical. A scanned PDF is recorded as an un-extracted source needing OCR,
with its hash intact — never as an empty document. `--inbox` lists what is waiting in
`02_KNOWLEDGE/inbox/`; `--inbox --assign-to <id>` prints the copy plan and `--copy` performs it.
Inbox originals are copied, never moved. Exit codes: 0, 2 contradictory arguments, 3 `--strict` and
something needs OCR, 4 unknown subject.

### `update-graphs`

`[--subject SUBJECT_ID] [--all] [--kinds A,B,C] [--format A,B,C] [--out-dir PATH] [--require-renderer] [--dry-run]`

Builds canonical graph JSON from the memory files, renders deterministic Mermaid text from it, and
writes a self-contained HTML viewer — into `03_MEMORY/graphs/<subject>/` and
`03_MEMORY/graphs/_cross/`. Kinds: `concept_prerequisite`, `learner_mastery`, `misconception`,
`progress` (per subject) and `cross_subject`, which always spans every subject whatever `--subject`
said. Formats: `json`, `mmd`, `html`, `svg`; `json` is always written because the rest derives from
it. Every endpoint is resolved first, so a dangling edge exits 3 before a single file is written.
Graphs are derived state: this command never writes a source-of-truth memory file, and deleting the
graphs directory is safe — re-running recreates byte-identical `.mmd`, except for the "review due"
marker, which is a claim about today and so can change across a day boundary. Exit codes: 0 (with or
without an SVG), 2 unknown kind or format, 3 a dangling reference or corrupt input, 4 unknown
subject, 6 `--require-renderer` with no `mmdc`.

### `validate`

`[--subject SUBJECT_ID] [--strict]`

The install's health check, and the first thing to run when anything looks wrong. Checks structure
and the absence of a fourth top-level directory, JSON parsing, schema conformance, concept-id
prefixes against the owning subject directory, prerequisite and misconception references, graph
edge endpoints, every line of the event log (reported with its line number), stale derived state,
and any absolute filesystem path stored in a memory file. Warnings do not fail the run. **Nothing
is ever repaired automatically** — a finding names the file, a JSON pointer, the reason and a
remedy, and leaves the file byte-identical. Exit codes: 0 no errors, 3 at least one error.

### `status`

One screen: subjects with concept counts by status and due reviews, the size and health of the
event log, the last session, the last backup, the current `validate` counts, and which optional
capabilities are available here. Exit codes: 0.

### `resume`

`[--subject SUBJECT_ID]`

The raw material for a start-of-session briefing: where the last session stopped, the concepts that
were in flight and their current status, the exact next action, how many reviews are due, the
unanswered questions, the plan in `next_session.md` and the last five events. A checkpoint whose
stored fingerprints no longer match the files is reported as a warning and still exits 0 — memory
legitimately moves on after a checkpoint was taken. Exit codes: 0, 4 no subject or no checkpoint.

### `checkpoint`

`--subject SUBJECT_ID --session-id SESSION_ID --note TEXT --next-action TEXT [--concept CONCEPT_ID] [--open-question TEXT] [--trigger NAME] [--agent NAME]`

Writes `03_MEMORY/checkpoints/<subject>/<session>.json` and appends a `checkpoint_written` event.
Both `--note` (where we stopped) and `--next-action` are required and must be non-empty: a
checkpoint exists so that a reader with no memory of the conversation can pick the thread up.
A sha256 of each memory file is stored alongside, so a later `resume` can tell whether the state it
is briefing from still matches. `--concept` and `--open-question` are repeatable. Writing a second
checkpoint for the same session replaces it; the history stays in the event log. Exit codes: 0,
3 an empty note or next action, 4 unknown subject.

### `search-memory`

`QUERY [--subject SUBJECT_ID] [--type EVENT_TYPE] [--since YYYY-MM-DD] [--limit N] [--reindex]`

Case-insensitive search over your own history: event lines, concept records and misconception
records. `--type` filters events only, so asking for one deliberately excludes concept and
misconception hits rather than silently ignoring the filter. A streaming scan by default;
`--reindex` builds an optional SQLite FTS5 cache (`search_index.sqlite3` under
`03_MEMORY/global/`) that stores the same documents and applies the same matching predicate, so
results are identical with or without it. No embeddings, no vector store, no network call. It does
not index `02_KNOWLEDGE/` — your source documents are searched with your editor. Exit codes: 0
whenever the query ran, including zero hits; 2 a malformed `--since` or `--type`.

### `session-report`

`[--subject SUBJECT_ID] [--session-id SESSION_ID | --last] [--out PATH] [--stdout-only] [--force]`

Derives duration, concepts touched, independent versus hinted counts, hints per concept,
misconceptions raised and resolved, mastery deltas, reviews scheduled and the next actions from the
event log, and writes `03_MEMORY/session_logs/<date>-<subject>-<session>.md` atomically. Every
number comes from recorded evidence, so the report cannot claim an independent success no event
supports. An existing report is not overwritten without `--force`; the new one is written beside it
with a `-02` suffix. `--last` means the most recent session of *any* kind, including the tool-only
sessions `index` stamps as `<date>-index`, so pass `--session-id` when you want the teaching session
specifically. Exit codes: 0, 4 unknown session id or no sessions recorded yet.

### `backup`

`[--note TEXT] [--include-sources] [--dry-run]`

Writes `03_MEMORY/backups/<backup_id>.zip` plus a `<backup_id>.manifest.json` holding a sha256 for
every entry, which is what lets `restore` prove the archive is intact before it writes anything.
Archived: all of `03_MEMORY/` (except previous backups and the regenerable search cache),
`learningos.config.json`, and each subject's `subject_config.json`, `source_index.json`,
`concept_catalog.json`, `syllabus_map.md` and `concept_catalog.md`. Not archived: `extracted_text/`,
which is derived — regenerate it with `index --force`. `--include-sources` adds
`source_materials/`. Nothing is moved, deleted or uploaded. Exit codes: 0.

### `restore`

`--from ZIP_OR_ID [--only PREFIX] [--dry-run] [--force]`

Verifies every sha256 in the manifest against the archive first: one mismatch, one missing entry,
or one entry with an absolute or `..` path means exit 3 and an untouched workspace. Overwriting
existing files requires `--force`, which takes a pre-restore safety backup before replacing
anything and writes each file via a temp file in its destination directory. `--only PREFIX` is
repeatable and restricts the restore to a workspace-relative prefix. Restoring rewinds the event
log to the archived state and appends one `memory_restore` line; events recorded after the backup
survive only in that safety backup. Files not in the archive are left alone — non-destructive beats
exactness. Exit codes: 0, 3 verification failed, 4 no such backup, 5 refused because files exist
and `--force` was not given.

### `reconcile`

`[--subject SUBJECT_ID] [--dry-run] [--apply]`

Replays the append-only evidence against the per-subject snapshots and reports every disagreement:
a stale snapshot, a status or counter the stored evidence does not support, an attempt in the log
with no evidence entry, a missing or orphaned revision-queue item. Report-only by default, and a
report exits 0 whether or not it found drift. `--apply` is the only writing path: it takes a backup
first and re-derives status, the mastery estimate, the counters and the queue. It never fabricates a
retention-evidence entry from an event, because a fabricated attempt is indistinguishable from a
real one once written — lost evidence is a `restore` job, not a recompute. Exit codes: 0, 3 a file
will not parse or validate.

---

## Running the tests

```powershell
cd C:\Study\LearningOS\01_AGENTS_AND_SKILLS\tools
python -m pytest -q
```

Invoke it as `python -m pytest`, not `pytest`: the console script is frequently not on `PATH` on
Windows. `pytest.ini` sets `testpaths = tests` and `conftest.py` puts this directory on `sys.path`,
so the modules import as `common`, `schema`, `memory.store` and so on without installation.

Every test builds a throwaway workspace in `tmp_path` from the shipped templates. No test reads or
writes the real `03_MEMORY/` tree, and all learning data in the suite is synthetic. The suite is
lettered against the acceptance checks it covers — A initialisation, B subject idempotency,
C persistence, D evidence and the status ladder, E recovery, F graphs, G graph reproducibility,
H integrity and corruption, I provenance, J tool and documentation compatibility, K cross-subject
isolation, L privacy and non-destructiveness — plus `test_cli_help.py`, `test_schema_lib.py`,
`test_search.py` and `test_report.py`. Run one file with
`python -m pytest tests/test_h_integrity.py -q`.

The PDF assertions skip themselves when `pypdf` is absent, and a separate test monkeypatches the
import to absent to prove the graceful path. No test requires `mmdc`.

---

## Layout

| File | Role |
|---|---|
| `learningos.py` | argparse CLI. One `Command` entry per subcommand in the module-level `COMMANDS` tuple; `main(argv=None) -> int` |
| `common.py` | workspace resolution, the `Workspace` path API, atomic writes, hashing, timestamps, the exception hierarchy, exit codes, the `Finding` record |
| `schema.py` | the bundled stdlib JSON-Schema subset validator and the schema registry over `01_AGENTS_AND_SKILLS/schemas/` |
| `validation.py` | the twelve checks behind `validate` |
| `memory/` | `store` (snapshots and checkpoints), `events` (the append-only log), `mastery` (the status ladder and the estimate), `search`, `report`, `backup`, `reconcile` |
| `ingestion/` | `extract` (per-format text extraction and classification), `indexer` (`source_index.json`, the inbox) |
| `graphs/` | `build` (canonical graph JSON), `mermaid` (pure JSON to text), `render` (HTML, optional SVG), `vendor/` for a hand-placed renderer |
| `tests/` | the pytest suite and its fixtures |

Two invariants worth knowing before you edit anything here. Every JSON write goes through a temp
file in the **same** directory, `flush` + `fsync`, then `os.replace`, and is schema-validated
*before* the replace — a half-written memory file is worse than no change at all. And every write
path refuses to overwrite a file that is already corrupt on disk, so a parse error never gets
laundered into a clean overwrite.

---

## Capability matrix

What this tooling does, what depends on the agent or model you drive it with, and what needs you to
do something once by hand.

### Implemented and tested here

| Capability | Where |
|---|---|
| Workspace and subject scaffolding, idempotent on re-run | `init-workspace`, `init-subject` |
| Structure, schema, reference and event-log validation with actionable findings | `validate`, `validation.py` |
| Atomic, schema-validated JSON writes; refusal to overwrite corrupt files | `memory/store.py` |
| Append-only evidence log where each line parses and validates independently | `memory/events.py` |
| Evidence-derived status ladder and mastery estimate; a hinted success never counts as independent | `memory/mastery.py` |
| Spaced-revision queue and ladder | `memory/mastery.py`, `revision_queue.json` |
| Checkpoint, resume briefing and drift detection | `checkpoint`, `resume`, `reconcile` |
| Local search over events, concepts and misconceptions, with an optional FTS5 cache | `search-memory` |
| Evidence-derived session reports | `session-report` |
| Verifiable backup and restore, with path-traversal rejection | `backup`, `restore` |
| Document indexing with stable ids, provenance headers and change detection | `index` |
| Graph JSON, deterministic Mermaid text and a self-contained HTML viewer | `update-graphs` |
| Cross-subject isolation enforced by the concept-id prefix | `validate` check (d), `tests/test_k_cross_subject.py` |
| No network capability anywhere in the tooling | `tests/test_l_privacy_nondestructive.py` |

### Depends on the active agent or model

| Capability | Why it is not ours to guarantee |
|---|---|
| Discovering the instructions at session start | Kiro reads `.kiro/steering/`, Codex reads root `AGENTS.md`, other agents differ. Both adapters are thin pointers; if an agent ignores them, ask it to read `AGENTS.md` |
| Quality of explanation, diagnosis and questioning | the model does the teaching; `01_AGENTS_AND_SKILLS/core/` and the skills constrain *how*, not *how well* |
| Actually writing memory at the end of a session | the CLI makes it safe and cheap; the agent still has to call it. `validate` and `reconcile` are how you catch an agent that did not |
| Honest reporting of hints and independence | the ladder is only as good as the evidence recorded. A hinted success relabelled as independent is normalised back by `mastery.record_attempt`, but an unreported hint cannot be detected |
| Rendering Mermaid inline in the chat pane | tool-dependent. The `.html` artefact always works in a browser |
| Reading long source documents | context limits are the model's, not the workspace's. `index` extracts text so the agent can read the part it needs |

### Requires a manual step

| Capability | The step |
|---|---|
| Kiro always-on instructions | copy the steering file into `.kiro/steering/` once — see `01_AGENTS_AND_SKILLS/adapters/kiro/README.md` |
| PDF text extraction | `python -m pip install pypdf` |
| `.svg` graph rendering | install mermaid-cli yourself so `mmdc` is on `PATH` |
| Offline-rendering HTML graph pages | drop `mermaid.min.js` into `01_AGENTS_AND_SKILLS/tools/graphs/vendor/` |
| OCR for scanned PDFs | **not implemented.** `index` flags the source as `needs_ocr` and leaves it un-extracted; run an OCR tool of your choice over the original and re-index |
| `.docx` and other office formats | **not implemented.** Recorded as `kind: other` with a `needs_manual_extraction` warning; export to PDF, Markdown or text yourself |
| `jsonschema` cross-checks under `--strict` | `python -m pip install jsonschema` |
| Off-machine backup | `backup` writes a zip; copying it somewhere else is your decision, deliberately |

---

## Privacy

The tooling makes no network calls at all: there is no upload path, no telemetry, no CDN fetch and
no package installation. Your documents, memory and learning history stay in this folder.

**Local files are not local inference.** If the model driving the conversation is hosted — most are
— then whatever the agent reads and pastes into the conversation reaches that provider, including
excerpts of your sources and memory. That is a property of the model you chose, not of this
workspace. Running a local model is the only way to make inference local too. See the privacy
section of the root `README.md`.
