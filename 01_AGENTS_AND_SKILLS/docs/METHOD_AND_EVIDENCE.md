# Methodology and evidence contract

LearningOS 0.3 adds a goal-independent learning cycle and a simpler student workflow. Exam practice
remains an optional module. The canonical mastery heuristic, ladder and old evidence are unchanged.
This document describes implementation, not a promise that any particular learning outcome or rank
has already been achieved.

## Learning input (prepared by the tutor)

`study cycle --subject ID FILE` consumes a JSON object conforming to
`schemas/learning_input.schema.json`. Example shape, never to be run as real learner evidence:

```json
{
  "cycle_id": "unique-id-reused-on-retry",
  "concept_id": "biology:inheritance",
  "task_id": "stable-question-or-task-identity",
  "prompt": "The actual question asked.",
  "response": "The actual learner response, with relevant working.",
  "feedback": "Specific feedback about the response.",
  "dimensions": {
    "explanation": {"outcome": "pass", "rationale": "Specific evidence in the response."},
    "application": {"outcome": "partial", "rationale": "The first missing or broken step."}
  },
  "steps": [
    {"work": "Actual learner step", "judgment": "fail", "rationale": "Why this transition fails."}
  ],
  "hints": 1,
  "support": [{"level": 1, "content": "The actual hint given."}],
  "solution_seen": false,
  "novelty": "variant",
  "difficulty": 3,
  "evaluation": "checked",
  "next_action": "A specific independent repair task and later retrieval.",
  "seconds": null,
  "confidence": null
}
```

Dimensions also include recall, derivation, assumptions, transfer, method_selection and execution.
Judgments are pass, partial, fail or not_tested. `evaluation` is checked or provisional.
Omit irrelevant dimensions rather than forcing eight questions into every learning cycle.
Source provenance lives on the concept and cited teaching notes; the tutor must distinguish checked
domain judgments from uncertain ones. A rubric makes judgments auditable; it cannot guarantee the
model's factual correctness. Reliable source/answer checking remains necessary.

Independent success requires both explanation and application to pass with checked grading, zero
assistance/solution exposure, and no failed annotated step. Recall-only or provisional work remains
partial. A stored hint overrides a falsely zero hint count. Same-task or normalized-prompt repeats
are familiar; a same-session retry after feedback is assisted. Novelty labelled novel requires a
passed transfer judgment. This exact-match detection does not claim semantic plagiarism detection.

Important explanations/questions use `study note`; they do not alter mastery. Legacy `study record`
remains for summary-assisted/partial/failure evidence but refuses bare unaided-success claims. New
positive independent evidence must use the richer cycle path.

## Crash-safe operation

1. Validate input, current records, references, rubric, and all proposed snapshots.
2. Append learning_cycle_saved with actual evidence, before/after concept records, review item,
   canonical event ids and grading conditions.
3. Append the compatible answer_evaluated/mastery_updated/revision_scheduled events exactly once.
4. Apply the concept/review snapshots and checkpoint.
5. Append learning_cycle_applied; then try to refresh Atlas and return a receipt.

`study recover` or `continue` completes pending operations by the same ids. Each target must match its
recorded before or after state; a divergent or corrupt file is never overwritten. Validation audits
the proposed projection against the original mastery algorithm and flags unfinished operations.
OS-backed writer locks serialize study and exam saves and release automatically on process exit.
The lock file is internal coordination, not learner evidence. Older direct low-level writer calls
remain outside this new transaction path; agents should use the documented study commands.

Session close first journals session_close_requested. It writes session_ended once, refreshes
snapshots, handover, checkpoint, report and workspace pointer, then records session_close_completed.
A retry completes the same intent and never counts another session. Atlas failure is returned
separately from successful memory storage. Unknown duration stays unknown; an overnight chat cannot
silently become overnight study. `--minutes` is explicitly learner-reported duration.

These are exact operation-completion semantics, not permission to run unrelated reconcile/restore
repairs. Existing drift/corruption policies still apply. Backups already include the event log and
memory snapshots; old records are not rewritten or automatically migrated.

## Atlas and onboarding

`start` creates/selects a subject and optionally records a mode/goal and starting topic. It records
no lesson success. `continue` completes known pending saves and loads the handover. `guide` explains
what is and is not captured. The desktop numbered menu offers the same setup without JSON/CLI work.

Atlas includes goals, open/pending sessions, full cycles, notes, dimension counts and the existing
concept/source/revision/exam views. Its build timestamp includes the time. It remains an offline
read-only snapshot: regeneration does not automatically reload an already-open browser tab.
CLI writes that append evidence attempt an Atlas refresh, except restore: it preserves archived
file bytes and explicitly asks the learner to reopen Atlas afterward. Direct calls to supported cycle/note/
close functions also return explicit refresh results. Arbitrary editor changes or undocumented
low-level calls are not monitored. A cache avoids repeated rebuilds for unchanged inputs in one
process; a fresh launcher invocation rebuilds from the current files.

Reserved question-bank entries may use `usage: holdout`. Training rejects them, and the general
Atlas event explorer hides their prompts/keys/solutions. Mock/sectional runs may use them; exposure history
still records prior appearances. This prevents accidental UI leakage, not access to files the
learner owns. Never claim a local self-study system can enforce an invigilated exam.
Starting/coaching a lesson is refused while a mock/sectional assessment remains open, including a
paused one. Finish that assessment before resuming assistance.

Uncertainty notes can be resolved by a new `learning_note_saved` record of kind `resolution` with
`resolves_note_id`; the original question remains in history. The final handover refreshes only the
managed uncertainty section of open_questions.md and preserves the rest of that file.

## Not captured or claimed

No background surveillance, automatic full-chat export, cross-chat memory ingestion, automatic
factual verification, practical competence from written case answers, inferred study duration,
semantic duplicate detection, guaranteed complete memory of unrecorded conversation, or rank
prediction. A save receipt and visible evidence trail replace those assumptions.
