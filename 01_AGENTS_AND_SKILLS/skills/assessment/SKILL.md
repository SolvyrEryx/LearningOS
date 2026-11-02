# Skill: assessment

## Purpose

Find out what the learner can actually do, unaided. This skill writes probes and test items, judges
answers against an explicit rubric, classifies the failure mode when an answer is wrong, and
produces the `outcome / hints_used / difficulty / novelty` tuple that the mastery rules consume.
It is the only skill allowed to decide whether a success was independent.

## When to invoke

- At the start of teaching a concept (diagnostic probe).
- After each fading rung (did that stick?).
- At the independence gate, before any promotion to `provisionally_mastered`.
- When a review in `revision_queue.json` is due (retention check).
- "Test me", "give me past-paper questions", "am I ready for the exam?".

## Inputs it reads

| Path | Why |
|---|---|
| `01_AGENTS_AND_SKILLS/core/teaching_principles.md` | hint levels, independence requirements |
| `01_AGENTS_AND_SKILLS/core/memory_protocol.md` | the status ladder and the exact evidence fields |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | status, past evidence, recurring mistakes |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | detection signals to watch for |
| `03_MEMORY/subjects/<subject_id>/revision_queue.json` | what is due, and at which interval |
| `02_KNOWLEDGE/subjects/` | `concept_catalog.json`, `syllabus_map.md`, past papers and notes in `extracted_text/` |

## Procedure

1. **Pick the item type for the purpose.**

   | Purpose | Item |
   |---|---|
   | diagnose | one doing-level question at the expected level, plus one prerequisite question |
   | check a rung | the same task with one more step blank |
   | independence gate | explain-back **and** a `variant`/`novel` application |
   | retention | closed-book recall of a previously independent item |
   | exam readiness | a timed past-paper question, graded to the paper's rubric |

2. **Set `novelty` deliberately.** `familiar` = the item they have seen; `variant` = same structure,
   different surface; `novel` = requires the concept but does not announce it. A promotion needs at
   least one non-familiar success.
3. **State the rubric before judging**, even to yourself: what the answer must contain, what is
   allowed to be missing, what makes it wrong rather than incomplete.
4. **Count hints honestly.** Any hint at any level makes the result `hinted_success` at best. If you
   supplied a step the learner did not ask for, that is still a hint.
5. **Classify the outcome** as exactly one of `independent_success`, `hinted_success`, `partial`,
   `fail`.
6. **Classify the failure mode** when not a success:
   - *prerequisite gap* → redirect to the prerequisite
   - *misconception* → confront it (step 7)
   - *procedural slip* → record a `recurring_mistakes` pattern
   - *recall failure* → it was never consolidated; schedule retrieval
   - *comprehension failure* → re-teach at a lower rung
7. **Misconceptions.** Match the learner's statement against `detection_signals` in
   `misconceptions.json`. Reuse an existing record rather than creating a near-duplicate. If new,
   create `<subject_id>:mc-NNN` with `description`, `learner_statement_example`,
   `correct_statement`, `detection_signals[]` and `remediation[]`. Resolution requires an
   independent success on an item designed to trigger it.
8. **Record the evidence** via `memory_manager`: one `answer_evaluated` event carrying `outcome`,
   `independent`, `hints_used`, `difficulty` (1–5), `novelty`, `response_seconds` and a short note,
   then the concept-record update and `mastery_updated`.
9. **Ask for a confidence self-report** at the independence gate and store it as
   `confidence_estimate`. It is never used to raise `status`; a large confidence/performance gap is
   itself worth telling the learner about.
10. **Schedule the follow-up** through `revision_planner`.

## Outputs it writes

Through `memory_manager`:

| Path | What changes |
|---|---|
| `03_MEMORY/events/session_events.jsonl` | `diagnostic_probe`, `question_asked`, `hint_given`, `answer_evaluated`, `retention_check`, `misconception_detected`, `misconception_resolved`, `mastery_updated` |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | `retention_evidence`, `independent_successes`, `hint_usage`, `recurring_mistakes`, `status`, `mastery_estimate`, `confidence_estimate`, `last_successful_retrieval_at` |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | occurrences, status transitions |
| `03_MEMORY/subjects/<subject_id>/revision_queue.json` | via `revision_planner` |

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py status
python learningos.py search-memory "orthogonality" --subject math2 --type answer_evaluated
python learningos.py session-report --last
```

## Failure handling

| Failure | Response |
|---|---|
| Ambiguous answer | ask one clarifying question before judging; never guess an outcome |
| Learner asks for the answer | that is hint level 4; record it and do not call the result a success |
| Past paper unreadable (`needs_ocr`) | say so, ask for the question text, do not invent a question and attribute it to the paper |
| Promotion criteria not met but learner feels ready | explain exactly which criterion is missing (delay, novelty, hints) and test for it |
| Concept record missing | create it at `unassessed` before recording evidence |
| `validate` exits 3 | stop recording, report the finding |

## Worked example

> **Learner:** test me on Fourier series, I think I've got it.

1. Record shows `status: "practising"`, `independent_successes: 1` (square wave, 2 days ago,
   `variant`). The missing promotion criterion is a *second* independent success.
2. Item, `novelty: "novel"`, `difficulty: 4`: "a sawtooth on `[-π, π)`, find a0 and a1 — no notes."
   No scaffolding offered.
3. Learner computes a0 = 0 correctly, then drops the `1/π` factor in a1.
4. Judgement: `partial`. Failure mode: procedural slip, and it matches the existing
   `recurring_mistakes` pattern "drops the normalising factor" → `count` becomes 2.
5. Consequences: `count >= 2` forces `status: "needs_review"`; the revision ladder resets to step 0,
   due tomorrow; `mastery_estimate` recomputed and reported as an estimate, not a score.
6. Event appended: `answer_evaluated` with `outcome: "partial"`, `independent: false`,
   `hints_used: 0`, `difficulty: 4`, `novelty: "novel"`, `response_seconds: 240`,
   notes "a0 correct, dropped 1/π in a1".
7. Told plainly: *"a0 is solid. The a1 normalising factor has now gone wrong twice, so I've moved
   this back to needs-review and set a retrieval for tomorrow. Not a promotion yet — the criterion
   you're missing is a second clean, unaided run."*
