# Changelog

All notable changes to LearningOS are recorded here. Versions follow semantic versioning; the
`version` field in `learningos.config.json` is the authoritative value.

## 0.3.0 - public starter distribution (2026-11-02)

- Added a public-facing README with fresh-install instructions, a first-session tutor prompt,
  everyday chat commands, source handling, privacy and recovery guidance.
- Added a six-page getting-started PDF and a local documentation build helper.
- Published the reusable system under MIT, preserving bundled font license notices.
- Excluded all live learner material and memory from Git, keeping only directory guides.
- Preserved PDF and font bytes through Git attributes; fresh workspace copies retain licensing
  and Git metadata rules.
- Replaced maintainer-specific installation paths in adapter and toolkit documentation with
  an example location.

## 0.1.0 — initial build

First complete build. Everything listed below is implemented and covered by the test suite:
**331 tests passing** (`python -m pytest -q` from `01_AGENTS_AND_SKILLS/tools`).

### Workspace contract

- Three principal directories — `01_AGENTS_AND_SKILLS/`, `02_KNOWLEDGE/`, `03_MEMORY/` — with no
  fourth, root entrypoints `AGENTS.md`, `README.md`, `CHANGELOG.md` and the workspace marker
  `learningos.config.json`.
- Canonical instruction set in `01_AGENTS_AND_SKILLS/core/`: system contract, teaching principles,
  memory protocol, source handling, session protocol. Exactly one copy of the teaching logic.
- Eight skill workflows in `01_AGENTS_AND_SKILLS/skills/`: adaptive tutor, assessment, memory
  manager, revision planner, session manager, subject initializer, visual learning, knowledge graph.
- Thin tool adapters: a Kiro steering file (one manual copy step, capped at 60 lines by a test) and
  a Codex adapter that states root `AGENTS.md` is canonical. Pointers only, no duplicated rules.
- Fifteen draft-07 JSON Schemas in `01_AGENTS_AND_SKILLS/schemas/` defining the concept record,
  per-subject state, session events, graph artefacts, source index and backup manifests.
- Knowledge, memory and graph templates in `01_AGENTS_AND_SKILLS/templates/`, instantiated into
  `02_KNOWLEDGE/subjects/_template/`, `03_MEMORY/subjects/_template/` and `03_MEMORY/global/`.

### Memory engine

- Four-layer memory: the append-only event log at `03_MEMORY/events/session_events.jsonl`, the
  per-subject snapshots under `03_MEMORY/subjects/`, derived artefacts, and checkpoints.
- Twenty event types, each line independently parseable and schema-validated against a `oneOf`
  branch keyed on the event type.
- Atomic JSON writes: temp file in the same directory, `flush` + `fsync`, schema validation, then
  `os.replace`. Write paths refuse to overwrite a file that is already corrupt on disk.
- Evidence-derived status ladder (`unassessed` → `developing` → `practising` →
  `provisionally_mastered`, plus `needs_review`). A hinted success never increments
  `independent_successes`, and an attempt reported as independent while carrying hints is
  normalised back to hinted in both the evidence and the event payload.
- Documented mastery heuristic: a recency-weighted mean over the last eight pieces of evidence,
  scaled by novelty, minus a penalty per open misconception, clamped and rounded to 2 dp. Stated in
  `01_AGENTS_AND_SKILLS/core/memory_protocol.md` and implemented in one function.
- Spaced-revision queue with a configurable interval ladder, advanced, held or reset by outcome.
- Cross-subject isolation enforced mechanically: `concept_id = "<subject_id>:<slug>"` with the
  prefix required to equal the owning subject directory.
- Corruption policy: a parse or schema failure names the file, a JSON pointer, the reason and a
  remedy, and leaves the bytes byte-identical. Nothing is ever silently repaired.

### Command line (`01_AGENTS_AND_SKILLS/tools/learningos.py`)

Thirteen subcommands, all standard library, all with real `--help`, input validation, graceful
errors and a documented exit code:

- `init-workspace`, `init-subject` — idempotent scaffolding; a second run changes no file hash.
- `index` — document hashing, text extraction with provenance headers and page markers, stable
  path-derived `source_id`s, change detection, and copy-only inbox assignment.
- `update-graphs` — canonical graph JSON, deterministic Mermaid, self-contained HTML viewer, and an
  optional `.svg`; five graph kinds including a cross-subject view.
- `validate` — twelve structure, schema, reference, event-log, drift and privacy checks.
- `status`, `resume`, `checkpoint` — health, start-of-session briefing, and session handover.
- `search-memory` — local search over events, concepts and misconceptions, with an optional SQLite
  FTS5 cache.
- `session-report` — markdown session logs derived entirely from recorded evidence.
- `backup`, `restore` — verifiable zip archives with a per-file sha256 manifest, pre-restore safety
  backup, `--dry-run`, `--force` and path-traversal rejection.
- `reconcile` — replays the event log against the snapshots, reports drift, and re-derives only the
  fields that stored evidence supports.
- Exit-code contract: 0 success, 1 unexpected error, 2 usage, 3 validation or data failure
  (nothing written), 4 not found, 5 refused destructive operation, 6 required optional dependency
  missing.
- A bundled standard-library JSON-Schema subset validator, so `validate` never depends on an
  installed package; `jsonschema` is an optional cross-check under `--strict`.

### Documentation

- Root `README.md`: what LearningOS is and is not, a QUICK START from empty folder to first session
  with executable PowerShell commands and session prompts, the memory model, day-to-day usage,
  backup/restore, hand-editing memory safely, switching agents, and an explicit
  "local files are not local inference" privacy section.
- `01_AGENTS_AND_SKILLS/tools/README.md`: every subcommand with its flags, the exit-code table, the
  optional dependencies and what degrades without each, how to run the tests, and a capability
  matrix split into implemented-and-tested / depends-on-the-active-agent-or-model /
  requires-a-manual-step.
- `01_AGENTS_AND_SKILLS/docs/design_decisions.md`: the nine load-bearing decisions with their
  rationale and cost, the six improvements on the original sketch, and the honest limitations.

### Privacy and non-destructiveness

- No network capability anywhere in the tooling: no module under `tools/` imports a network library
  or shells out to a package manager, asserted statically by the test suite. The only sanctioned
  subprocess call is the optional `mmdc` invocation.
- Originals under `source_materials/` are opened read-only and stay byte-identical; derived text
  lands beside them in `extracted_text/`.
- Nothing is moved, deleted or uploaded; destructive operations require `--force` and offer
  `--dry-run`.

### Known limitations in 0.1.0

No OCR for scanned PDFs (flagged, never recorded as empty), no `.docx` extraction, no renderer
bundled or installed, `search-memory` does not index `02_KNOWLEDGE/`, and `reconcile --apply`
re-derives but never fabricates evidence. Full list with reasoning in
`01_AGENTS_AND_SKILLS/docs/design_decisions.md`.
# Exam preparation and offline Atlas upgrade — 2026-11-01

- Added a self-contained, searchable Learning Atlas with local Newsreader and Instrument Sans fonts.
- Added numbered beginner menu, Windows launcher and tutor chat-command router.
- Added event-sourced exam profiles, versioned verified questions, timed attempts, mocks, pauses,
  revisits, scores, error diagnoses, unseen-family retests and transparent budgeted plans.
- Added validated concept mapping and tutor session writers; mastery evidence remains separate.
- Corrected subject discovery for invalid-id source drop folders, preserving their files and
  reporting them explicitly as unassigned material rather than recommending an invalid initializer.
- Added integration coverage for marking, replay, source references, state isolation and safe HTML.

# Goal-independent methodology and student workflow — 0.3.0 — 2026-11-01

- Added start/continue/guide onboarding, optional source-free topic mapping, personal learning modes,
  and a four-action student workflow. Exam configuration is optional.
- Added actual prompt/response/rubric/step journals, stored hints, strict independent-evidence gates,
  duplicate-operation protection and exact interrupted-save completion.
- Added retryable session closure, explicit unknown/reported duration, saved uncertainty resolutions,
  canonical compatibility events and separate memory/Atlas receipts.
- Added OS-backed study/exam writer locks, reserved assessment questions and a coaching guard while
  an unaided mock/sectional test remains open.
- Extended Atlas with learning dimensions, goals, actual cycles, notes, pending saves, open sessions,
  clearer timestamps and a beginner guide. Evidence writes attempt automatic regeneration.
- Restore deliberately preserves archived bytes; reopen Atlas afterward to regenerate its report.
- Documented that unrecorded conversation and other chats are not automatically captured.
