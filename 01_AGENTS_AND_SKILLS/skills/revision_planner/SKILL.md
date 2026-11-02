# Skill: revision_planner

## Purpose

Decide what the learner should practise, when, and in what order — so that retention is scheduled
rather than hoped for. This skill maintains the per-subject revision queue, chooses the next most
useful activity for the time available, and builds exam-oriented plans backwards from the exam date.

## When to invoke

- At the end of every session, to set the next due dates.
- At the start of every session, to report what is due and overdue.
- "What should I revise?", "I have 20 minutes", "my exam is in three weeks".
- After any status change, especially a `fail` (which resets the ladder).

## Inputs it reads

| Path | Why |
|---|---|
| `01_AGENTS_AND_SKILLS/core/memory_protocol.md` | the status ladder and evidence semantics |
| `01_AGENTS_AND_SKILLS/schemas/revision_queue.schema.json` | queue item shape |
| `03_MEMORY/subjects/<subject_id>/revision_queue.json` | current due dates, intervals, step indices |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | status, last attempt, mastery estimate, recurring mistakes |
| `03_MEMORY/subjects/<subject_id>/concept_graph.json` | prerequisite order |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | open misconceptions needing a trigger item |
| `03_MEMORY/subjects/<subject_id>/subject_state.json` | exam date, coverage, goals |
| `03_MEMORY/global/active_priorities.md` | competing subjects and deadlines |
| `03_MEMORY/global/learning_preferences.json` | realistic session length |

## Procedure

### The interval ladder

Steps in days: **1, 3, 7, 16, 35**. Each queue item carries `concept_id`, `due_at`,
`interval_days`, `step_index`, `reason`, `last_outcome` and `attempts`.

| Last outcome | Ladder move |
|---|---|
| `independent_success` | advance one step |
| `hinted_success` | hold at the current step |
| `partial` | reset to step 0 |
| `fail` | reset to step 0 |

Past step 4 (35 days), hold at 35 days. The ladder is deliberately coarse: the evidence that matters
is independence, and a finer schedule would imply a precision the data does not support.

### Choosing the next activity

Rank in this order, and stop when the available time is filled:

1. **Overdue items** whose prerequisites are satisfied — longest overdue first.
2. **Open misconceptions** with no trigger item attempted in the last two sessions.
3. **`needs_review` concepts**, nearest-prerequisite-first so a fix is not blocked by a gap.
4. **Due items** (due today).
5. **`practising` concepts one criterion away from promotion** — name the missing criterion
   (delay, novelty, or a hint-free run) and test exactly that.
6. **New concepts** from the catalogue, in prerequisite order, but only if nothing above is pending.

Interleave two or three concepts rather than drilling one. Mass practice feels productive and
retains badly.

### Time budgeting

| Time available | Plan |
|---|---|
| ≤ 10 min | overdue retrieval only; no new material |
| 20–30 min | 10 min retrieval, then one concept at the independence gate |
| 45–60 min | 10 min retrieval, 30 min teaching/practice, 10 min consolidation + handover |
| ≥ 90 min | as above plus a timed past-paper block; checkpoint every 20–30 min |

### Exam plans

Work backwards from `exam_date` in `subject_state.json`:

- **Final week** — timed past papers and retrieval only. No new concepts.
- **Weeks 2–3 out** — close `needs_review` and open misconceptions; one pass over every
  high-weighting unit.
- **Earlier** — coverage: move `unassessed` concepts to at least `practising`, prerequisite order
  first.

State the plan as dated blocks with concept ids, and write it into the subject's `next_session.md`
so it survives the conversation. Report honestly when coverage is not achievable in the time left,
and propose what to drop — by syllabus weighting, not by what feels easiest.

## Outputs it writes

Through `memory_manager`:

| Path | What changes |
|---|---|
| `03_MEMORY/subjects/<subject_id>/revision_queue.json` | `due_at`, `interval_days`, `step_index`, `reason`, `last_outcome`, `attempts` |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | `review_due_at`, `status` → `needs_review` when a due date passes unattempted |
| `03_MEMORY/subjects/<subject_id>/next_session.md` | the dated plan |
| `03_MEMORY/global/active_priorities.md` | cross-subject deadline picture |
| `03_MEMORY/events/session_events.jsonl` | `revision_scheduled`, `retention_check` |

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py status
python learningos.py validate --subject thermo
python learningos.py search-memory "entropy" --subject thermo --type retention_check
python learningos.py update-graphs --subject thermo --kinds progress
python learningos.py session-report --last
```

## Failure handling

| Failure | Response |
|---|---|
| Queue file missing | treat as empty (it is derived), rebuild from concept records, warn once |
| Queue references a concept that no longer exists | `validate` exits 3; drop the stale item explicitly, with the learner informed |
| Everything is overdue | do not dump 40 items; take the top N by weighting and overdue age, and say what is being deferred |
| Exam date in the past | ask whether to clear it or set the next one; do not silently plan against it |
| Learner refuses retrieval, wants new material | state the trade-off once, record the preference, then do it their way |
| No time before the exam for full coverage | present the triage by syllabus weighting and let the learner choose |

## Worked example

> **Learner:** 25 minutes, exam in 18 days.

1. Queue: `thermo:entropy` overdue 3 days (step 2, 7-day interval);
   `thermo:first-law` due today (step 1); `thermo:carnot-efficiency` due in 4 days.
   Open misconception `thermo:mc-002` ("entropy always increases, full stop") untested for two
   sessions. `thermo:second-law` is at `practising`, one criterion short of promotion (needs a
   hint-free run at ≥ 2 days' delay — the delay is satisfied).
2. Plan for 25 minutes: 6 min overdue retrieval on `thermo:entropy`; 6 min trigger item for
   `thermo:mc-002`; 8 min the promotion item for `thermo:second-law`; 5 min consolidation and
   handover. `thermo:carnot-efficiency` deferred, said out loud.
3. Outcomes: entropy `independent_success` → ladder step 2 → 3, next due in 16 days. Misconception
   trigger answered correctly unaided → `thermo:mc-002` moves `open → monitoring` (resolution needs
   a second clean trigger). `thermo:second-law` `hinted_success` → ladder holds at the current step,
   promotion still blocked, and the reason is stated to the learner.
4. Events appended: two `retention_check`, one `answer_evaluated`, three `revision_scheduled`.
5. `next_session.md`: "Day 17 before exam. Start with the `thermo:second-law` promotion item,
   unaided. Then `thermo:carnot-efficiency` (due). Entropy is now on a 16-day interval."
