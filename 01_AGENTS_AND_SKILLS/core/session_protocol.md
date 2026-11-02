# Session protocol

The lifecycle of one learning session: how to start it, what to do while it runs, when to
checkpoint, what must be written before it ends, and how to recover when it ends badly.

A session is a single continuous stretch of work on one subject. Its id is
`<YYYY-MM-DD>-<subject_id>-<NN>`, for example `2026-02-11-math2-01`, where `NN` is the session
number for that subject on that date.

## 1. Start

1. **Locate the workspace.** `learningos.config.json` at the root is the marker file.
2. **Read the contract.** `01_AGENTS_AND_SKILLS/core/system_contract.md`, then this file and
   `01_AGENTS_AND_SKILLS/core/memory_protocol.md`.
3. **Read the learner.** `03_MEMORY/global/learner_profile.md`,
   `03_MEMORY/global/learning_preferences.json`, `03_MEMORY/global/active_priorities.md`.
4. **Resolve the subject** — from the learner's request, else `current_subject_id` in
   `03_MEMORY/global/workspace_state.json`. If neither is available, ask; do not guess.
5. **Read the subject state.** `subject_state.json`, `concept_mastery.json`, `next_session.md`,
   `open_questions.md`, `revision_queue.json`, `misconceptions.json` from
   `03_MEMORY/subjects/<subject_id>/`, plus `concept_catalog.json` and `syllabus_map.md` from
   `02_KNOWLEDGE/subjects/<subject_id>/`.
6. **Check health.** Run `python learningos.py validate` from `01_AGENTS_AND_SKILLS/tools` when the
   last session ended abnormally or when anything was hand-edited. Stop and report on exit 3.
7. **Append `session_started`** with the new `session_id`, and set `current_subject_id` and
   `last_session_id` in `workspace_state.json`.
8. **Give the resume briefing** — at most eight lines:
   - where the last session stopped and why
   - concepts in flight and their status
   - reviews due today, and how many are overdue
   - open questions still unanswered
   - the proposed plan for this session, stated as an outcome ("you will derive X unaided"), with
     the learner's confirmation requested before you start teaching.

`python learningos.py resume` produces the raw material for this briefing from the newest
checkpoint, `next_session.md`, the due queue, `open_questions.md` and the last five events.

## 2. Resume versus fresh start

| Situation | Action |
|---|---|
| Checkpoint exists, no `session_ended` after it | resume that session's thread; reuse the in-flight concepts and next action |
| Checkpoint exists, `session_ended` present | fresh session; read `next_session.md` for the plan |
| No checkpoint, snapshots present | fresh session; brief from `concept_mastery.json` and the due queue |
| No subject state at all | run the subject initializer skill first |
| Checkpoint `state_fingerprints` no longer match the files | resume anyway, but warn that memory changed since the checkpoint and verify the in-flight concepts with a probe |

## 3. Mid-session cadence

Work in short cycles rather than long monologues. One cycle ≈ 5–15 minutes:

probe or retrieve → teach the smallest useful piece → have the learner do something → judge it →
record the evidence → decide the next piece.

Per cycle: append the relevant events (`diagnostic_probe`, `explanation_given`, `question_asked`,
`hint_given`, `answer_evaluated`), and update the concept record when an answer has been judged.
Do not batch a whole session's evidence to the end — that is exactly the data an interruption
destroys.

Every 20–30 minutes, or at any natural boundary, state a one-line progress summary: what is now
independent, what still needs hints, what is next.

## 4. Checkpoint triggers

Write a checkpoint when **any** of these happen:

- a concept's status changes
- 20 minutes or ~15 exchanges have passed since the last checkpoint
- the learner says they are stopping, pausing, or "nearly out of time"
- before any long or risky operation (bulk indexing, restore, large graph regeneration)
- a misconception is detected or resolved
- the learner's attention is visibly dropping, before you switch activity
- immediately before you end the session

A checkpoint is `03_MEMORY/checkpoints/<subject_id>/<session_id>.json` containing
`checkpoint_id`, `session_id`, `subject_id`, `created_at`, `where_we_stopped`,
`concepts_in_flight[]`, `next_action`, `open_questions[]`, `agent`, `tool_version` and
`state_fingerprints{}` (a sha256 per memory file, so a later resume can tell whether the state it is
briefing from still matches). Writing one appends a `checkpoint_written` event.

`where_we_stopped` and `next_action` are written for a reader with no memory of the conversation.
"Continue Fourier" is useless. "Stopped after deriving a0 for a square wave with hints; next,
re-derive it unaided, then a1" is a usable handover.

## 5. End of session

All five of these, in order, before you say goodbye:

1. **Events** — append any outstanding evidence, then `session_ended` with duration, concepts
   touched and a one-line outcome.
2. **Snapshots** — final concept records, `subject_state.json` (`session_count`, `total_minutes`,
   `coverage`, `last_session_id`, `last_session_at`), `misconceptions.json`, and
   `revision_queue.json` with the next due dates from the `1, 3, 7, 16, 35` ladder.
3. **Handover** — rewrite `03_MEMORY/subjects/<subject_id>/next_session.md`: what was achieved, what
   is still shaky, the exact first activity for next time, and anything the learner should bring.
   Update `open_questions.md`.
4. **Checkpoint** — a final one, so an abrupt end is never worse than a planned one.
5. **Derived artefacts** — `python learningos.py session-report --last` for the markdown log in
   `03_MEMORY/session_logs/`, and `python learningos.py update-graphs --subject <subject_id>` when
   statuses or edges changed. Both are regenerable, so they go last.

   For the exam/Atlas extension, also run `python learningos.py atlas` after the final memory writes.
   `study end` performs the validated closure, report and Atlas steps together. Never record an exam
   score as concept mastery without the separate independent assessment evidence required by the
   teaching and memory protocols.

Then tell the learner, in three or four lines: what is now independent, what still needs work, when
the next reviews are due, and what the first activity next time will be.

## 6. Abort and crash recovery

If the session ends without step 5 — closed window, lost connection, flat battery — the event log
still holds everything appended up to that moment, because each append is flushed and fsynced.
Recovery at the start of the next session:

1. `python learningos.py validate` — confirms the log and snapshots are readable; a bad final line
   is reported with its line number and left in place.
2. `python learningos.py reconcile --dry-run` — shows where snapshots lag the event log.
3. `python learningos.py reconcile --apply` — replays the affected events after taking a backup.
4. `python learningos.py resume` — brief from the newest checkpoint, warning on any fingerprint
   mismatch.
5. If a snapshot is corrupt rather than stale, do not repair it: restore from
   `03_MEMORY/backups/` with `python learningos.py restore --from <zip> --dry-run` first.

The guarantee this protocol buys: the worst case for an interrupted session is losing the few
exchanges since the last event append, never the session, and never the subject.

## 7. Session hygiene

- The simple student workflow is `/start`, study normally, `/save`, `/continue`. Exams and uploaded
  documents are optional. Capture actual evidence every meaningful cycle with `study cycle`; use
  `study note` for explanations/questions without inventing an assessment.
- On `/continue`, complete matching pending cycle/closure journals before new learning. Refuse
  divergent or corrupt files. Each save receipt distinguishes memory from Atlas refresh status.
- `/save` uses the durable `study end` close intent. Unknown study duration remains unknown;
  only a supplied `--minutes` is recorded as learner-reported duration. Closing the chat alone
  cannot export unrecorded conversation. Reload an already-open Atlas tab after regeneration.

- One subject per session. Switching mid-session muddies both the evidence and the learner.
- Open with retrieval of the previous session's material before new teaching. It costs five minutes
  and is the highest-value five minutes available.
- Close with a plan, not a summary. The learner should know the first thing they will do next time.
- If the learner only has ten minutes, run due reviews from `revision_queue.json` and nothing else.
  A short retrieval session is worth more than a rushed new concept.
