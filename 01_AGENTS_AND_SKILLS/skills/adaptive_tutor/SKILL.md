# Skill: adaptive_tutor

## Purpose

Teach one concept at a time, starting from what the learner demonstrably already knows, adapting the
explanation to what has worked for them before, and stopping only when they can do the thing
unaided. This skill owns the explanation; judging answers belongs to `assessment`, and writing
records belongs to `memory_manager`.

## When to invoke

- "Teach me X", "explain X", "I don't get X", "start Module 3 from scratch".
- A concept is at `developing` or `needs_review` and a review is due.
- `assessment` reports a failure whose cause is a missing or broken explanation rather than a
  misconception.

## Inputs it reads

| Path | Why |
|---|---|
| `01_AGENTS_AND_SKILLS/core/teaching_principles.md` | the explanation ladder, fading, hint levels |
| `01_AGENTS_AND_SKILLS/core/source_handling.md` | citation rules |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | status, prerequisites, `helpful_interventions`, `failed_interventions`, `recurring_mistakes`, `next_action` |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | known wrong models to confront |
| `03_MEMORY/subjects/<subject_id>/concept_graph.json` | prerequisite chain |
| `03_MEMORY/global/learning_preferences.json` | notation, modality, worked-example appetite |
| `02_KNOWLEDGE/subjects/` | the subject's `concept_catalog.json`, `syllabus_map.md`, `source_index.json` and `extracted_text/` |

## Procedure

1. **Identify the target concept id.** Resolve the learner's words against `concept_catalog.json`.
   If the concept is not catalogued, add it (via `memory_manager`) before teaching, so the evidence
   has somewhere to go.
2. **Read the record.** Status, prerequisites, what worked, what failed, recurring mistakes,
   `next_action` from last time. Honour `failed_interventions` — do not re-run a losing approach.
3. **Check prerequisites.** Any prerequisite at `unassessed` or `needs_review` gets a one-question
   check. If a prerequisite is broken, say so and teach it first; record the redirect.
4. **Probe the target.** One or two doing-level questions. Record as `diagnostic_probe`. Set the
   starting rung from the result: fluent → go to transfer; partial → start at the broken step;
   absent → start at the concrete instance.
5. **Climb the ladder** (`teaching_principles.md` §3): concrete instance → pattern → formal
   statement → boundaries → transfer. One rung at a time, each followed by the learner doing
   something. Cite sources as `[source_id p.N]`; mark general-knowledge reasoning as such.
6. **Fade support.** Full worked example → final step blank → middle steps blank → prompt only →
   nothing. One rung per success.
7. **Hint in levels.** 1 orienting question → 2 principle named → 3 first step shown → 4 full
   solution. Append `hint_given` with the level every time. Never skip to level 3.
8. **Use a diagram when the structure is the content.** Hand off to `visual_learning`.
9. **Confront misconceptions** rather than restating the correct version — hand off to the
   misconception procedure in `assessment` and `memory_manager`.
10. **Require independence before stopping.** The learner explains it back *and* applies it to a
    `variant` or `novel` item with zero hints. Until then the concept is not done, however good the
    explanation felt.
11. **Record as you go.** After each judged answer, `memory_manager` appends the event and updates
    the concept record. Write `next_action` before you move to another concept.

## Outputs it writes

Through `memory_manager` (this skill does not write files directly):

| Path | What changes |
|---|---|
| `03_MEMORY/events/session_events.jsonl` | `diagnostic_probe`, `explanation_given`, `question_asked`, `hint_given`, `answer_evaluated`, `mastery_updated`, `misconception_detected` |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | `status`, `retention_evidence`, `hint_usage`, `helpful_interventions`, `failed_interventions`, `recurring_mistakes`, `next_action`, `mastery_estimate` |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | new or updated misconception records |
| `03_MEMORY/graphs/` | only when `visual_learning` / `knowledge_graph` is invoked |

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py search-memory "a0 term" --subject math2
python learningos.py status
python learningos.py update-graphs --subject math2 --kinds learner_mastery
```

It may not run `restore`, `backup --include-sources`, or any command that writes a snapshot outside
the memory protocol.

## Failure handling

| Failure | Response |
|---|---|
| Concept not in the catalogue | add it first; do not teach into a void |
| Prerequisite missing from memory entirely | create the record at `unassessed`, probe it, then decide |
| Source needed but `needs_ocr` | say which file and pages are unreadable; ask the learner to paste the passage; never infer page content |
| Two failures on the same step | stop climbing, drop a fading rung, and suspect a prerequisite or a misconception |
| `validate` exits 3 mid-session | stop writing, report the finding, do not teach on corrupt state |
| Learner's confidence high, performance low | name the gap, record `confidence_estimate`, keep `status` where the evidence puts it |

## Worked example

> **Learner:** teach me Fourier series, I've seen them but they never stick.

1. Resolve to `math2:fourier-series`. Record: `status: "unassessed"`,
   prerequisites `math2:periodic-functions`, `math1:definite-integral`;
   `failed_interventions: ["formal-definition-first"]`.
2. Prerequisite check: "sketch the period of `sin(3x)`" → fluent. "Integrate `x·cos(x)` over
   `0..2π`" → partial, integration by parts shaky. Noted, not fatal.
3. Probe: "what does the a0 term represent physically?" → "the first harmonic". Wrong, and it is a
   recognisable wrong model → candidate misconception.
4. Rung 1, concrete: a square wave, compute a0 by hand together, show it is the mean value.
   Skip the formal definition first — the record says that failed before.
   Cite `[math2-textbook-ch7-1a2b3c4d p.212]`.
5. Confront: "if a0 were the first harmonic, what would a constant signal look like?" They see the
   contradiction. Record misconception `math2:mc-003`, "a0 read as the first harmonic".
6. Rung 2–3: pattern across two waveforms, then the formal series, now with referents.
7. Fade: worked example → final step blank → prompt only. One level-2 hint on the integral bounds,
   appended as `hint_given`.
8. Independence test: a triangle wave, `novelty: "variant"`, no hints → correct. One
   `independent_success`. Status `developing → practising` — not `provisionally_mastered`, because
   that needs a second independent success at ≥ 2 days' delay.
9. `next_action`: "re-derive a0 for a triangle wave unaided after two days, then a1".
   `helpful_interventions`: `["concrete-first", "mean-value-framing"]`.
