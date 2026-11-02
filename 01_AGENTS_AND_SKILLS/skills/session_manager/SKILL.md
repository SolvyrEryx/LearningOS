# Skill: session_manager

## Purpose

Prefer the goal-independent command-router workflow for ordinary requests: `/start`, `/learn`,
`/save`, `/continue`. The `continue` CLI completes matching pending save operations; `study end`
journals closure before writing the final handover and is safe to retry. No exam or material upload
is required. Read `01_AGENTS_AND_SKILLS/docs/STUDENT_GUIDE.md` for the student-facing experience.

Open, checkpoint and close a learning session so that no session ever loses evidence and every
session starts with an accurate picture of where the last one stopped. This skill owns the session
lifecycle; it does not teach. Teaching is `adaptive_tutor`, judging is `assessment`.

## When to invoke

- The learner says anything that opens work: "let's study", "continue where we left off", "teach me
  Module 3", "I have 20 minutes".
- The learner says anything that closes work: "that's enough", "I have to go", "save this".
- Any checkpoint trigger from `01_AGENTS_AND_SKILLS/core/session_protocol.md` fires.
- A previous session appears to have ended abnormally (no `session_ended` after the last
  checkpoint).

## Inputs it reads

| Path | Why |
|---|---|
| `learningos.config.json` | locate and verify the workspace root |
| `01_AGENTS_AND_SKILLS/core/session_protocol.md` | the lifecycle rules this skill implements |
| `03_MEMORY/global/workspace_state.json` | `current_subject_id`, `last_session_id` |
| `03_MEMORY/global/learner_profile.md` | durable facts about the learner |
| `03_MEMORY/global/learning_preferences.json` | session length, modality, notation preferences |
| `03_MEMORY/global/active_priorities.md` | what matters in the next few weeks |
| `03_MEMORY/subjects/<subject_id>/next_session.md` | the previous session's handover note |
| `03_MEMORY/subjects/<subject_id>/subject_state.json` | counters, coverage, exam date |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | current concept statuses |
| `03_MEMORY/subjects/<subject_id>/revision_queue.json` | what is due today |
| `03_MEMORY/subjects/<subject_id>/open_questions.md` | unresolved questions |
| `03_MEMORY/checkpoints/` | the newest checkpoint for this subject |
| `03_MEMORY/events/session_events.jsonl` | the last few events, for context |

## Procedure

### Opening

1. Resolve the subject: explicit request → `current_subject_id` → ask. Never guess.
2. If `03_MEMORY/subjects/<subject_id>/` does not exist, hand off to `subject_initializer`.
3. Run `validate` if the last session ended abnormally or anything was hand-edited. On exit 3,
   report the findings and stop — do not teach on top of corrupt state.
4. Run `resume` to collect the briefing material.
5. Mint `session_id` = `<YYYY-MM-DD>-<subject_id>-<NN>`; `NN` counts existing sessions for that
   subject on that date, starting at `01`.
6. Append `session_started`. Update `workspace_state.json`.
7. Deliver the briefing (≤ 8 lines): where we stopped, concepts in flight, reviews due and overdue,
   open questions, and the proposed outcome for this session. Ask for confirmation.
8. If the checkpoint's `state_fingerprints` no longer match, say so and verify the in-flight concept
   with one probe before continuing.

### Running

9. Keep the cycle short: retrieve → teach → do → judge → record. Delegate teaching and judging.
10. Checkpoint on every trigger. Do not batch evidence to the end of the session.
11. On any `validate` error appearing mid-session, stop writing and report.

### Closing

12. Append outstanding evidence, then `session_ended` with duration, concepts touched, outcome.
13. Update `subject_state.json`: `session_count`, `total_minutes`, `coverage`, `last_session_id`,
    `last_session_at`.
14. Rewrite `next_session.md` and `open_questions.md` for a reader with no memory of the
    conversation.
15. Write the final checkpoint.
16. Generate derived artefacts: `session-report --last`, and `update-graphs --subject <id>` if
    statuses or edges changed.
17. Close with four lines: now independent / still shaky / next reviews due / first activity next
    time.

## Outputs it writes

| Path | Content |
|---|---|
| `03_MEMORY/events/session_events.jsonl` | appended events |
| `03_MEMORY/global/workspace_state.json` | current subject, last session, tool version |
| `03_MEMORY/subjects/<subject_id>/subject_state.json` | counters and coverage |
| `03_MEMORY/subjects/<subject_id>/next_session.md` | handover note |
| `03_MEMORY/subjects/<subject_id>/open_questions.md` | unresolved questions |
| `03_MEMORY/checkpoints/` | `<subject_id>/<session_id>.json` |
| `03_MEMORY/session_logs/` | generated markdown report |

Event types emitted: `session_started`, `checkpoint_written`, `session_ended`, and
`open_question_logged` when a question is parked.

## Commands it may run

Run from `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py resume --subject <id>
python learningos.py checkpoint --subject <id> --session-id <sid> --note "<where we stopped>" --next-action "<next>"
python learningos.py validate
python learningos.py status
python learningos.py session-report --last
python learningos.py reconcile --dry-run
```

Never `restore` without the learner's explicit instruction.

## Failure handling

| Failure | Response |
|---|---|
| `resume` exits 4 (no checkpoint) | brief from snapshots instead; say the checkpoint is missing |
| `validate` exits 3 | report file, pointer, remedy; refuse to write; offer `reconcile --dry-run` or `restore` |
| Fingerprint mismatch | warn, probe the in-flight concept, continue |
| Subject state missing | hand off to `subject_initializer`; never fabricate state |
| Learner stops abruptly | the appended events are already durable; write a checkpoint immediately if you get the chance |

## Worked example

> **Learner:** back again, 30 minutes before dinner.

1. `resume --subject math2` → newest checkpoint `2026-02-11-math2-01`, `where_we_stopped`: "derived
   a0 for a square wave with level-2 hints", `next_action`: "re-derive a0 unaided, then a1";
   three reviews due, one overdue by two days.
2. Append `session_started` for `2026-02-13-math2-01`; set `current_subject_id = "math2"`.
3. Briefing: *"Last time you got a0 for a square wave with two hints. Today: a0 unaided, then a1.
   Three retrievals are due, one overdue — periodic functions. Thirty minutes: ten on retrieval,
   twenty on a0/a1. Sound right?"*
4. Teach and judge via `adaptive_tutor` and `assessment`, appending events per cycle.
5. At minute 20 `math2:fourier-series` moves `developing → practising` → checkpoint written.
6. Close: `session_ended`, `subject_state.json` updated, `next_session.md` rewritten to "start with
   a1 for a square wave, unaided; a0 is now independent", final checkpoint, `session-report --last`,
   `update-graphs --subject math2`.
7. Summary: *"a0 is now independent. a1 still needs the orthogonality step. Next reviews Friday.
   Next time we start with a1, no notes."*
