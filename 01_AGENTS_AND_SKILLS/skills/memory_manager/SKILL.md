# Skill: memory_manager

## Purpose

The preferred learner-facing path is the command-router skill and validated `study cycle`,
`study note`, `study end`, `study recover`, `start`, and `continue` commands. They implement the
event-first and corruption rules below with explicit save/Atlas receipts. Use them instead of
manually coordinating multiple JSON writes. The learner need not run these commands themselves.
See `01_AGENTS_AND_SKILLS/docs/METHOD_AND_EVIDENCE.md` for the journal and assessment contract.

Turn what happened in the conversation into durable, schema-valid records. This skill is the only
one that writes to `03_MEMORY/`. Every other skill hands it the facts and lets it decide the shape,
the order of writes, and what to do when the state on disk is not what was expected.

## When to invoke

- After every judged answer, hint, probe or explanation that constitutes evidence.
- At every checkpoint trigger and at the end of every session.
- When the learner states a durable preference or fact about themselves.
- When a misconception is detected or resolved.
- When the learner asks "what do you know about my progress?" (read path).
- After any hand-edit of memory, to validate and reconcile.

## Inputs it reads

| Path | Why |
|---|---|
| `01_AGENTS_AND_SKILLS/core/memory_protocol.md` | the normative spec this skill implements |
| `01_AGENTS_AND_SKILLS/schemas/concept_record.schema.json` | concept record shape |
| `01_AGENTS_AND_SKILLS/schemas/session_event.schema.json` | event shape and per-type payloads |
| `01_AGENTS_AND_SKILLS/schemas/` | the remaining state and artefact schemas |
| `03_MEMORY/subjects/` | the current snapshots for the active subject |
| `03_MEMORY/global/` | profile, preferences, index, priorities, workspace state |
| `03_MEMORY/events/session_events.jsonl` | existing evidence, for drift checks and search |

## Procedure

### Write order (never reversed)

1. **Append the event** to `03_MEMORY/events/session_events.jsonl` — one line, UTF-8, `\n`, no
   pretty-printing, flushed and fsynced. `event_id` is a uuid4 hex string; `at` is ISO-8601 UTC with
   `Z`.
2. **Read the snapshot** fresh from disk. If it will not parse or will not validate, stop: report
   the file, the JSON pointer, the reason and the remedy, and write nothing (exit 3 behaviour).
3. **Apply the change** in memory, using the status ladder and the mastery heuristic from
   `memory_protocol.md`. Do not invent alternative rules.
4. **Validate** the new object against its schema.
5. **Write atomically** — temp file in the same directory, `flush()`, `os.fsync()`, `os.replace()`.
6. **Bump `updated_at`** on every snapshot you touch.

Event first, snapshot second, always. If the process dies in between, the evidence survives and
`reconcile` can rebuild the snapshot; the other order loses the evidence permanently.

### Per-event-type actions

| Evidence | Event | Snapshot effects |
|---|---|---|
| probe answered | `diagnostic_probe` | status off `unassessed` |
| explanation given | `explanation_given` | `helpful_interventions` / `failed_interventions` |
| hint given | `hint_given` | `hint_usage.total_hints`, `last_hint_level` |
| answer judged | `answer_evaluated` + `mastery_updated` | `retention_evidence` append, `independent_successes` (only if `independent_success` with zero hints), `status`, `mastery_estimate`, `last_attempted_at`, `last_successful_retrieval_at` |
| misconception | `misconception_detected` / `misconception_resolved` | `misconceptions.json`, concept `misconception_ids` |
| review set | `revision_scheduled` | `revision_queue.json`, `review_due_at` |
| session boundary | `session_started` / `session_ended` | `subject_state.json`, `workspace_state.json` |
| checkpoint | `checkpoint_written` | checkpoint file under `03_MEMORY/checkpoints/` |

### Durable learner facts

Preferences and traits that outlive a session go to `03_MEMORY/global/learner_profile.md` (prose)
and `03_MEMORY/global/learning_preferences.json` (machine-readable). One-off session details do not
go there. Never record personal information the learner did not volunteer for this purpose.

### Invariants to uphold on every write

- `concept_id` is `<subject_id>:<slug>` and the prefix equals the owning directory name.
- No absolute filesystem paths in any stored value — workspace-relative, forward slashes.
- `mastery_estimate` and `confidence_estimate` are `null` or `0.0–1.0` at 2 decimal places.
- A hinted success never increments `independent_successes`.
- No cross-subject reads or writes: a write for `chem` touches only that subject's own directory
  under `03_MEMORY/subjects/`.
- Timestamps are UTC with `Z`, second precision.

## Outputs it writes

`03_MEMORY/events/session_events.jsonl`; everything under `03_MEMORY/subjects/` for the active
subject; `03_MEMORY/global/learner_profile.md`, `learning_preferences.json`, `learner_index.md`,
`active_priorities.md`, `workspace_state.json`; checkpoints under `03_MEMORY/checkpoints/`; reports
under `03_MEMORY/session_logs/`.

It never writes to `02_KNOWLEDGE/subjects/` except the derived `source_index.json` and
`extracted_text/` produced by indexing, and it never writes anything under
`01_AGENTS_AND_SKILLS/`.

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py validate --subject math2
python learningos.py status
python learningos.py search-memory "normalising factor" --subject math2
python learningos.py checkpoint --subject math2 --session-id 2026-02-13-math2-01 --note "..." --next-action "..."
python learningos.py reconcile --subject math2 --dry-run
python learningos.py backup --note "before hand edit"
python learningos.py session-report --last
```

`restore` and `reconcile --apply` require the learner's explicit instruction. `restore --force` is
destructive; always show `--dry-run` output first.

## Failure handling

| Failure | Response |
|---|---|
| Snapshot will not parse | report path + pointer + remedy; write nothing; offer `restore --dry-run` |
| Snapshot fails schema validation | same — never "fix" it silently |
| Bad JSONL line found | report `events/session_events.jsonl:<line>`; skip it when reading; never rewrite the log |
| `stale_state` warning | `reconcile --dry-run`, show the drift, ask before `--apply` |
| Required file missing | exit 3 naming it; suggest `init-subject` (idempotent) or `restore` |
| Wrong-subject concept id | refuse the write and report it; this is a contamination bug, not a typo to paper over |
| A correction to past history is needed | append a correcting event; never edit or delete an existing line |

## Worked example

> `assessment` reports: `math2:fourier-series`, `partial`, 0 hints, difficulty 4, novelty novel,
> 240 s, "dropped 1/π in a1".

1. Append `answer_evaluated` to the event log; fsync.
2. Read `concept_mastery.json` from the `math2` directory under `03_MEMORY/subjects/` — it parses
   and validates.
3. Append to `retention_evidence`: `{"at": "...Z", "delay_days": 2, "outcome": "partial",
   "difficulty": 4, "novelty": "novel", "hints_used": 0}`. `independent_successes` unchanged at 1.
4. The mistake matches the existing pattern "drops the normalising factor" → `count: 2`,
   `last_seen_at` updated. `count >= 2` forces `status: "needs_review"`.
5. Recompute `mastery_estimate` with the documented heuristic → `0.48`, rounded to 2 dp.
6. Set `next_action`, `last_attempted_at`, `updated_at`. Validate. Atomic write.
7. Append `mastery_updated` with the before/after status and estimate.
8. Hand off to `revision_planner`, which resets the ladder to step 0 and appends
   `revision_scheduled`.
