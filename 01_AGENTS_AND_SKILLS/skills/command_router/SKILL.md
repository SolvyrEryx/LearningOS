# Skill: command_router

Use for slash commands and ordinary requests to start, learn, save, resume or understand LearningOS.
Read the core contracts first. This system serves learning, exams, interviews and professional study.
An exam name, deadline, uploaded material or complete syllabus is never a prerequisite for starting.
These are chat conventions interpreted by the tutor, not commands the offline Atlas can execute.

## Student-facing workflow

The only essential actions are **/start**, studying normally, **/save**, and **/continue**.
`/learn TOPIC` also works directly. Do not make the learner memorize ids, write JSON, run Python,
choose a grading rubric or manage checkpoint files. Natural language is equivalent to commands.

| Command | What the tutor does |
|---|---|
| `/start [subject]` | Resolve an existing display name or initialize a new subject with `start`. Ask for a subject if absent; goal and materials are optional. Offer a short diagnostic as the first learning activity. |
| `/learn [topic]` | Start/continue the owning subject, map the topic as unassessed if needed, then use adaptive-tutor and methodology rules below. |
| `/save` or "stop for today" | Record outstanding real cycles and notes. Synthesize a concrete outcome/next action from actual evidence, then run `study end`. Return the memory/Atlas receipt. |
| `/continue` or `/resume` | Run `continue` for the resolved subject: finish known pending operations, read the handover and queue, then resume with retrieval. No fabricated reconstruction of missing chat. |
| `/help` | Show the four everyday actions and say where teaching happens, what is saved and how to reopen Atlas. Use `guide` for exact current behaviour. |
| `/today [minutes]` | Use `today` with available time; do not demand an exam. Explain the selected first task. |
| `/review` | Retrieve due concepts closed-book and log the real responses. |
| `/test` | Run an unaided concept assessment, recording question, response and dimension rubric through `study cycle`. |
| `/mock` | Optional exam module. Use checked questions and real timing; hide all help until completion. |
| `/mistakes` | Inspect step-level failures, concept mistakes and exam errors; prescribe a targeted variant retest. |
| `/atlas` | Rebuild the report and return its local path. An existing browser tab needs reloading. |
| "remember this" | Use `study note` with the actual content; no invented performance. |

When changing subjects, close the current subject with a truthful handover before starting the next.
Explain this briefly; do not ask for a second permission to perform the save already implied by the
switch. One learner per workspace; a new person requires a fresh workspace, never a subject named
for the person inside someone else's history.

## One learning cycle

1. Read the concept's previous evidence, source references and prerequisite state.
2. Choose the smallest useful learning outcome. A cycle may assess one or two dimensions; it does
   not need to assess every dimension, teach every prerequisite or finish a chapter.
3. Ask the learner to do something before giving the relevant answer. Preserve the actual prompt.
4. Explain the minimum useful piece, if needed, and record important explanations/uncertainties
   using `study note`. Record actual hints and their content rather than hiding assistance.
5. Capture the actual learner response and relevant intermediate working. Judge the first broken
   step and the tested dimensions with a reason for each judgment.
6. Prepare a validated `learning_input` record, then call `study cycle`. Reuse its cycle_id on retry.
   The command journals the evidence before applying snapshots, makes duplicate retries idempotent,
   schedules retrieval, checkpoints and attempts an Atlas refresh.
7. Briefly state the result and exact next activity. Show the save receipt at meaningful boundaries.

Do not rely on `/save` to magically export the conversation later. Persist each meaningful cycle as
it happens. A question or note can be saved before the learner answers, without inventing an attempt.
The learner can close the app unexpectedly: only committed evidence is guaranteed to survive.

## Honest assessment

Dimensions: explanation, application, recall, derivation, assumptions, transfer, method_selection,
execution. Use only dimensions relevant to the task. Record `not_tested` or omit the others.
The same method works for a mathematical derivation, a code interview, an explanatory essay or an
educational clinical case. Written case performance alone is not evidence of practical competence.

A cycle earns independent success only when explanation AND application pass, grading is checked,
there are no hints or solution exposure, and no marked failed step contradicts that judgment.
Recall alone, confidence, uncertain grading, reading an explanation and copying a solution cannot
establish this. The existing status ladder and delayed-retrieval requirements remain unchanged.
Same-task/same-prompt retries after feedback in the same session are familiar and assisted.
Novelty requires meaningful unfamiliar application, not changed numbers or a paraphrased prompt.

For error diagnosis, distinguish interpretation, method selection, missing knowledge, execution,
assumptions and speed. Give a specific repair, then a different independent task and a delayed check.
Use mixed unlabelled tasks after basic competence so the learner must select the method. Preserve
some verified questions as `usage: holdout`; training cannot use reserved assessment questions.

General-background learning is allowed with `study map --general`; label that provenance clearly.
When sources exist, use and cite them. Mark disputed, unverified or uncertain teaching/grading as
provisional; record an open question rather than making the learner memorize a guess. For material
whose correctness depends on current standards or guidelines, verify the appropriate authoritative
source before marking its factual answer checked. Never send workspace files to external services.

## Tutor operations

Run from `01_AGENTS_AND_SKILLS/tools`; use the registered ids internally:

```powershell
python learningos.py start --subject Biology --topic Genetics --goal "Explain and apply inheritance" --mode learn
python learningos.py continue --subject biology
python learningos.py study cycle --subject biology path/to/cycle.json
python learningos.py study note --subject biology --kind uncertainty --title "Boundary case" --content "Actual question and what remains uncertain."
python learningos.py study end --subject biology --outcome "Actual achieved and unfinished work." --next-action "Exact first retrieval next time."
python learningos.py study status
python learningos.py guide
```

Only supply `study end --minutes` when actual duration is known/reported. Chat wall-clock time is not
study time. Do not run example assessment commands without an actual learner attempt.
See `01_AGENTS_AND_SKILLS/docs/STUDENT_GUIDE.md` and `METHOD_AND_EVIDENCE.md` for formats and rules.

## Save/recovery contract

A receipt states separately `memory_saved` and `atlas_refreshed`. If Atlas fails, say the evidence is
saved and explain how to retry the viewer; never report memory loss or full success incorrectly.
A pending cycle contains its original evidence and exact before/after projections. `/continue`
finishes only a matching, validated operation. Diverged or corrupt files are preserved and reported.
Do not run reconcile --apply or restore to fix unrelated drift without the required authorization.
The offline report is a snapshot. It cannot detect unrecorded chat or silently watch the filesystem.
