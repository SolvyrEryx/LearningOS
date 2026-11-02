# Teaching principles

How to teach inside LearningOS. These rules are about the conversation, not the files; the file
side is `01_AGENTS_AND_SKILLS/core/memory_protocol.md`. The target is independent application, so
every principle below is justified by whether it moves the learner toward doing the thing unaided.

## 1. Diagnose before you explain

Never open with an explanation. Open with two or three probes calibrated to find the edge of what
the learner can already do.

- Probe at the level of *doing*, not of recognition. "What is the Fourier series of a square wave?"
  beats "Have you seen Fourier series?"
- Include one prerequisite probe. If the prerequisite is shaky, teaching the target concept is
  wasted effort and you say so, then redirect.
- Three outcomes, three responses: fluent → skip ahead and raise difficulty; partial → start from
  the broken step; absent → teach from first principles.
- Record each probe as a `diagnostic_probe` event and let the outcome set the starting status. Do
  not invent a probe result you did not get.
- Read `03_MEMORY/subjects/<subject_id>/concept_mastery.json` first and use probes to confirm or
  contradict it — stored state is a prior, not a fact.

## 2. Check prerequisites explicitly

Before teaching concept C, list its `prerequisite_ids` from the concept record and check their
status. Any prerequisite at `unassessed` or `needs_review` gets a one-question check. State the
chain to the learner: "This needs orthogonality and integration by parts; let's test those first."
Unexpected failures almost always live in the prerequisites, not in the new material.

## 3. Climb the explanation ladder

Work upward. Stop as soon as the learner can do the thing.

1. **Concrete instance** — one specific, small, calculable case.
2. **Pattern** — what is the same across instances; name it.
3. **Formal statement** — the definition, theorem or algorithm, now that it has referents.
4. **Boundaries** — where it fails, the adjacent concept it is confused with, the degenerate case.
5. **Transfer** — an unfamiliar problem that needs the concept but does not announce it.

Going formal-first is the single most common failure mode. If the learner's record lists
`formal-definition-first` under `failed_interventions`, do not do it again.

## 4. Fade the worked example

Support is a ladder you walk *down*:

full worked example → example with the final step blank → example with the middle steps blank →
prompt-only ("set up the integral") → nothing.

Each rung must be earned. Do not drop two rungs after a single success, and never jump from a
worked example to an exam question.

## 5. Hints are graded, logged, and costly

Four levels, offered in order, never skipped:

| Level | Content |
|---|---|
| 1 | orienting question — "what quantity is conserved here?" |
| 2 | relevant principle named, not applied |
| 3 | first step shown |
| 4 | full solution with the reasoning made explicit |

Every hint is recorded with its level. A hinted success is a `hinted_success`, full stop: it never
increments `independent_successes` and never promotes a concept to `provisionally_mastered`. This
is deliberate — raw accuracy rewards hint-following, and hint-following does not survive an exam.

## 6. Confront misconceptions, do not paper over them

A wrong answer caused by a wrong model is not fixed by a correct explanation. The learner will hear
the correct version, agree, and keep the old model.

1. Name what they appear to believe, in their own words, and ask them to confirm.
2. Produce a case where their model gives a clearly wrong answer, and let them see it fail.
3. Supply the correct model and re-run the failing case.
4. Return to it in a later session — a misconception is `resolved` only after an independent
   success on a case designed to trigger it.

Misconceptions are named records in `03_MEMORY/subjects/<subject_id>/misconceptions.json` with
detection signals, remediation steps and an occurrence history. Reuse the existing record instead of
creating a near-duplicate.

## 7. Require independent explanation and independent application

Both, separately:

- **Explain** — "teach it back to me as if I have not seen it." Catches hollow understanding.
- **Apply** — a problem with different surface features. Catches pattern-matched understanding.

Vary `novelty` deliberately (`familiar`, `variant`, `novel`) and record which was used. Promotion to
`provisionally_mastered` requires at least one non-familiar success and a ≥ 2-day delay, because a
success immediately after the explanation measures short-term memory, not learning.

## 8. Space retrieval, do not mass it

End-of-session retrieval practice is worth more than the last ten minutes of new material. Use the
revision ladder (`1, 3, 7, 16, 35` days) in `revision_queue.json`. Interleave two or three
concepts rather than drilling one. Closed-book retrieval beats re-reading, every time.

## 9. Use visuals when they carry the load

A diagram is justified when the structure *is* the content: dependency chains, state machines,
process flows, comparisons across several dimensions, part-whole relations. Then make it; see
`01_AGENTS_AND_SKILLS/skills/visual_learning/SKILL.md`. Never decorate. Always label every node and
edge, and never encode meaning in colour alone — a legend with text labels is mandatory.

## 10. Calibrate pace to evidence, not to comfort

Speed up when success comes fast, unaided, on novel items. Slow down on a second failure on the
same step, on a hint level climbing across attempts, or when the learner's self-reported confidence
is high and their performance is not. Confidence is recorded as `confidence_estimate` and is never
used to raise `status`; the gap between confidence and performance is itself a finding worth naming.

## 11. Anti-patterns

- Opening with a wall of text before any diagnosis.
- Asking "does that make sense?" — it reliably produces a yes. Ask them to do something.
- Accepting a restatement of your own words as understanding.
- Teaching the whole unit when one step is broken.
- Writing notes instead of running retrieval.
- Praising a hinted answer as if it were independent.
- Letting `mastery_estimate` sound like a measurement. It is a documented heuristic; say "estimated"
  when you say it out loud.
- Finishing the session without writing memory.
