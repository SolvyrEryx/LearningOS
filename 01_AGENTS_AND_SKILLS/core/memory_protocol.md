# Memory protocol

The canonical specification of what LearningOS remembers, where it is stored, in what shape, and
what happens when something is wrong. Schemas in `01_AGENTS_AND_SKILLS/schemas/`, the tooling in
`01_AGENTS_AND_SKILLS/tools`, and this document are required to agree; if they diverge, that is a
bug to fix, not a judgement call.

## 1. Four layers

| Layer | Path | Write mode | Rebuildable |
|---|---|---|---|
| Evidence | `03_MEMORY/events/session_events.jsonl` | append-only | no — this is the record |
| Current state | `03_MEMORY/subjects/<subject_id>/*.json`, `03_MEMORY/global/` | atomic overwrite | yes, by replay |
| Derived | `03_MEMORY/graphs/`, `03_MEMORY/session_logs/`, the optional search cache in `03_MEMORY/global/` | regenerate | always |
| Checkpoints | `03_MEMORY/checkpoints/<subject_id>/<session_id>.json` | write once per trigger | no, but cheap to lose |

Only the event log is irreplaceable, which is why it is never rewritten — not even to fix a bad
line.

## 2. The concept record

Stored as a value in `03_MEMORY/subjects/<subject_id>/concept_mastery.json` under
`concepts["<concept_id>"]`.

```json
{
  "schema_version": 1,
  "concept_id": "math2:fourier-series",
  "name": "Fourier series",
  "subject_id": "math2",
  "prerequisite_ids": ["math2:periodic-functions", "math1:definite-integral"],
  "source_refs": [{"source_id": "textbook-ch7-1a2b3c4d", "locator": {"page": 212}}],
  "status": "developing",
  "mastery_estimate": 0.42,
  "confidence_estimate": 0.6,
  "last_attempted_at": "2026-02-11T09:14:00Z",
  "last_successful_retrieval_at": null,
  "independent_successes": 0,
  "hint_usage": {"total_hints": 3, "hinted_successes": 2, "last_hint_level": 2},
  "recurring_mistakes": [
    {"pattern": "drops the 1/2 a0 term", "count": 2,
     "first_seen_at": "2026-02-09T09:00:00Z", "last_seen_at": "2026-02-11T09:14:00Z"}
  ],
  "misconception_ids": ["math2:mc-003"],
  "helpful_interventions": ["worked-example-fading", "diagram:spectrum"],
  "failed_interventions": ["formal-definition-first"],
  "retention_evidence": [
    {"at": "2026-02-11T09:14:00Z", "delay_days": 2, "outcome": "hinted_success",
     "difficulty": 3, "novelty": "variant", "hints_used": 1}
  ],
  "next_action": "Re-derive a0 for a square wave with no notes.",
  "review_due_at": "2026-02-14T00:00:00Z",
  "updated_at": "2026-02-11T09:15:00Z"
}
```

Rules (normative):

- Required: `schema_version, concept_id, name, subject_id, status, updated_at`. All other fields have
  defaults (`[]`, `{}`, `0`, `null`) and are written explicitly so files are self-describing.
- `concept_id` pattern `^[a-z0-9][a-z0-9_-]*:[a-z0-9][a-z0-9_-]*$`; the part before `:` **must** equal
  `subject_id`, which **must** equal the owning directory name.
- `status` ∈ `unassessed | developing | practising | provisionally_mastered | needs_review`.
- `mastery_estimate` / `confidence_estimate`: `number 0..1` or `null`, 2 decimal places.
  `mastery_estimate` is documented as a *heuristic* — "estimated chance of an unaided correct
  explanation **and** application right now" — not a measurement. `confidence_estimate` is the
  learner's self-report and is never used to raise `status`.
- `outcome` ∈ `independent_success | hinted_success | partial | fail`;
  `novelty` ∈ `familiar | variant | novel`; `difficulty` ∈ 1..5.
- Timestamps: ISO-8601 UTC with `Z`, second precision.
- All stored paths are workspace-relative with forward slashes (no absolute user paths).

Field semantics worth stating twice: `independent_successes` counts attempts with
`outcome == "independent_success"` and `hints_used == 0`, and nothing else ever increments it.
`retention_evidence` is an ordered list, oldest first; `delay_days` is the gap since the previous
attempt on this concept. `recurring_mistakes[].pattern` is a short reusable description, not a
transcript — the same pattern increments `count` rather than appending a new entry.
`helpful_interventions` / `failed_interventions` are the levers that make the system adaptive:
record what actually worked so the next session does not re-run a failed approach.

## 3. Status ladder

- `unassessed` → `developing` on any attempt.
- `developing` → `practising` after ≥ 1 `independent_success` **or** ≥ 2 `hinted_success`.
- `practising` → `provisionally_mastered` **only if all** hold: ≥ 2 `independent_success`; at least one
  of them with `novelty != "familiar"`; at least one of them with `delay_days >= 2`; zero hints in
  those two attempts; no linked misconception with `status != "resolved"` seen in the last 2 attempts.
- any → `needs_review` on `fail`, on a `recurring_mistakes` entry reaching `count >= 2`, or when
  `review_due_at` has passed with no attempt.

"Provisionally" is not hedging for its own sake: it means the evidence is sufficient to stop
teaching and start spacing, not that the concept is finished.

## 4. Mastery heuristic

Over the last 8 `retention_evidence` entries, each attempt contributes
`outcome value × novelty weight` with a recency weight of `0.5 ** (days_since / 30)`:

- outcome value — independent 1.0, hinted 0.45, partial 0.25, fail 0.0
- novelty weight — familiar 0.8, variant 1.0 (also the default when novelty was not recorded),
  novel 1.15
- recency weight — a 30-day half-life, so old evidence fades instead of being discarded

`mastery_estimate` is the recency-**weighted mean** of those contributions, minus
`0.05 × open misconceptions` linked to the concept, clamped to `0.0 .. 1.0` and rounded to 2 decimal
places. A weighted mean, not a running sum: it stays inside `0..1` by construction, it rises when
new evidence is better than the history, and it falls when a recent attempt fails — whereas a sum
would reward volume of practice regardless of quality.

The formula lives in exactly one function, `mastery_estimate()` in
`01_AGENTS_AND_SKILLS/tools/memory/mastery.py`, whose docstring repeats this paragraph. Never
re-derive it anywhere else.

It is an *estimate*: present it as one. Never report it as a percentage score, a grade, or a
probability of passing.

## 5. The event log

`03_MEMORY/events/session_events.jsonl`: append-only, UTF-8, `\n` line endings, one complete JSON
object per line, no pretty-printing, each line independently parseable and schema-valid.

```json
{"schema_version":1,"event_id":"9f1c...","at":"2026-02-11T09:14:00Z","session_id":"2026-02-11-math2-01","subject_id":"math2","type":"answer_evaluated","actor":"agent","tool_version":"learningos 0.1.0","concept_ids":["math2:fourier-series"],"payload":{"question_ref":"pyq-2023-q4","outcome":"hinted_success","independent":false,"hints_used":1,"difficulty":3,"novelty":"variant","response_seconds":180,"notes":"needed the orthogonality reminder"}}
```

- Required: `schema_version, event_id, at, session_id, type, actor`. `subject_id` may be `null` for
  workspace-level events. `concept_ids` defaults `[]`, `payload` defaults `{}`.
- `event_id` is a uuid4 hex string. `actor` ∈ `learner | agent | tool`.
- `type` ∈ `session_started, session_ended, checkpoint_written, subject_initialized,
  diagnostic_probe, explanation_given, question_asked, answer_evaluated, hint_given,
  misconception_detected, misconception_resolved, mastery_updated, retention_check,
  revision_scheduled, source_indexed, graph_generated, note_saved, open_question_logged,
  memory_backup, memory_restore`.

Exam-extension event types are also canonical: `exam_configured`, `question_registered`,
`practice_started`, `practice_attempted`, `practice_paused`, `practice_resumed`, `practice_ended`,
`practice_error_classified`, and `study_plan_created`. Their payloads are specified in the same
session-event schema. `exam_engine.load()` replays these records directly; no second truth store
or mastery migration is introduced. Exam scores never promote learning mastery. See
`01_AGENTS_AND_SKILLS/docs/EXAM_AND_ATLAS.md` for timing, marking and versioning semantics.
- Per-type payload shapes are specified in `01_AGENTS_AND_SKILLS/schemas/session_event.schema.json`
  via `oneOf` on `type`, and the validator reports which branch failed.
- Writer: open `"a"`, `encoding="utf-8"`, `newline="\n"`, write one line, `flush()` + `os.fsync()`.
  Appends are never rewritten; a corrupt line is reported by `validate` with its line number and
  **left in place**.
- One exception, and only one: `restore` replaces the log with the copy inside the archive, which
  rewinds it to the backup's state before appending `memory_restore`. It takes a pre-restore safety
  backup first, so the discarded tail is recoverable. Nothing else in the system ever rewinds the
  log.

## 6. When to write what

| Moment | Event(s) appended | Snapshot updated |
|---|---|---|
| session opens | `session_started` | `workspace_state.json` |
| probe answered | `diagnostic_probe` | concept record status may move off `unassessed` |
| explanation delivered | `explanation_given` | — |
| hint given | `hint_given` | `hint_usage` |
| answer judged | `answer_evaluated` | `retention_evidence`, counters, `status`, `mastery_estimate` via `mastery_updated` |
| misconception seen / cleared | `misconception_detected` / `misconception_resolved` | `misconceptions.json`, concept `misconception_ids` |
| review scheduled | `revision_scheduled` | `revision_queue.json` |
| source indexed | `source_indexed` | `source_index.json` (knowledge side) |
| graph written | `graph_generated` | none — graphs never write state |
| checkpoint trigger | `checkpoint_written` | checkpoint file |
| session closes | `session_ended` | `subject_state.json`, `next_session.md`, `open_questions.md`, `learner_index.md` |

Append the event first, then update the snapshot. If the process dies between the two, the event log
still holds the truth and `reconcile` can rebuild the snapshot. The reverse order loses evidence.

## 7. Atomic writes

Every JSON write goes to a temp file in the **same** directory as the target, is `flush()`ed and
`os.fsync()`ed, and is then moved into place with `os.replace()`. Same directory matters: it keeps
the replace on one filesystem so it is atomic. Consequences:

- A crash leaves either the complete old file or the complete new file. Never half of either.
- No `*.tmp` file survives a successful write.
- Schema validation happens **before** the temp file is replaced into position, so an invalid
  snapshot never reaches the canonical path.
- The event log is the exception: it is appended, not replaced, with `flush()` + `os.fsync()`.

## 8. Corruption, partial and inconsistent state

1. **Parse or schema failure on a required state file** → raise `LearningOSDataError` carrying file,
   JSON pointer, reason, and remedy. The file is **not** written, not truncated, not "fixed".
   Any command that would write it aborts with exit 3.
2. **Missing derived file** (graph artefact, search index, revision queue) → warning, treated as empty,
   regenerable by `update-graphs` / `reconcile`.
3. **Missing required file** (`subject_state.json`, `concept_mastery.json`) → exit 3 naming the file and
   suggesting `init-subject` (idempotent, will not overwrite survivors) or `restore`.
4. **Bad JSONL line** → `validate` lists `events/session_events.jsonl:<line>` with the parse error;
   readers skip and count it; the file is never rewritten.
5. **State vs event drift** (newest relevant event newer than `snapshot.updated_at`) → `validate`
   reports a `stale_state` warning; `reconcile --dry-run` shows the diff; `reconcile --apply` replays
   the affected events after taking a backup.

The common thread: LearningOS would rather stop and tell you exactly which byte range is wrong than
guess at a repair. A silently "fixed" mastery file is indistinguishable from fabricated evidence.

## 9. Hand-editing memory

### Goal-independent learning workflow extension

`learning_goal_set`, `learning_cycle_saved`, `learning_cycle_applied`, `learning_note_saved`,
`session_close_requested` and `session_close_completed` are also canonical event types with payloads
in session_event.schema.json. Actual question/response/dimension evidence is validated by
learning_input.schema.json. Positive independent evidence through the tutor CLI requires the richer
cycle path; recall alone or provisional grading does not establish independent concept mastery.
The status ladder and mastery heuristic above are unchanged; no historical evidence is rewritten.

The cycle journal carries exact before/after projections and canonical event ids. Known interrupted
operations can be completed with `study recover` or `continue`; divergent/corrupt targets are refused.
This is operation completion, not implicit authorization for unrelated reconcile/restore repairs.
Session closure journals its intent and is retryable without duplicate counting. Unknown duration
is null; chat wall-clock time is never silently treated as study time. Atlas refresh success is
reported separately from evidence storage. See `01_AGENTS_AND_SKILLS/docs/METHOD_AND_EVIDENCE.md`.

Permitted and expected — these are your files. Safe procedure:

1. `python learningos.py backup --note "before hand edit"` from `01_AGENTS_AND_SKILLS/tools`.
2. Edit with any editor. Keep UTF-8, keep `\n`, keep timestamps in `...Z` form.
3. `python learningos.py validate` — fix anything it names.
4. `python learningos.py reconcile --dry-run` if you changed counters or statuses.

Do not edit `03_MEMORY/events/session_events.jsonl` to change history. Appending a correcting event
is the supported way to fix a wrong record; rewriting the log destroys the only unreconstructable
layer in the system.
