# Skill: subject_initializer

## Purpose

Bring a new subject into existence: both halves of it, from templates, idempotently, with no
contamination from any existing subject. Then turn the learner's raw material (syllabus, textbook,
notes, past papers) into a concept catalogue and a prerequisite graph that the rest of the system
can teach from.

## When to invoke

- "I'm starting a new subject", "set up Thermodynamics", "I have a new syllabus".
- A session opens for a subject with no directory under `03_MEMORY/subjects/`.
- An existing subject needs its catalogue extended for a new unit or module.

## Inputs it reads

| Path | Why |
|---|---|
| `01_AGENTS_AND_SKILLS/templates/subject_template/` | the knowledge-side scaffolding |
| `01_AGENTS_AND_SKILLS/templates/memory_templates/` | the memory-side scaffolding |
| `01_AGENTS_AND_SKILLS/schemas/subject_config.schema.json` | subject config shape |
| `01_AGENTS_AND_SKILLS/schemas/concept_catalog.schema.json` | catalogue shape |
| `01_AGENTS_AND_SKILLS/schemas/concept_graph.schema.json` | edge shape |
| `01_AGENTS_AND_SKILLS/core/source_handling.md` | how to treat the learner's documents |
| `02_KNOWLEDGE/inbox/` | documents awaiting assignment |
| `03_MEMORY/global/workspace_state.json` | the existing subject list |

Reference copies of both template sets are visible at `02_KNOWLEDGE/subjects/_template/` and
`03_MEMORY/subjects/_template/`.

## Procedure

1. **Agree the id and name.** The id is a lowercase slug matching `^[a-z0-9][a-z0-9_-]*$` and
   becomes the directory name *and* the prefix of every concept id in the subject. Short and stable
   beats descriptive: `thermo`, not `thermodynamics-sem4-2026`. Renaming later rewrites every
   concept id, so get it right now.
2. **Create both halves** with one idempotent command:

   ```powershell
   python learningos.py init-subject --id thermo --name "Thermodynamics" --exam-date 2026-05-18
   ```

   This creates the knowledge side under `02_KNOWLEDGE/subjects/` and the memory side under
   `03_MEMORY/subjects/`, substitutes the id, name, exam date and timestamps, registers the subject
   in `workspace_state.json` and `03_MEMORY/global/learner_index.md`, and appends
   `subject_initialized`. It never overwrites an existing file, never resets mastery, never copies
   another subject's data, and a second run is a no-op that exits 0.
3. **Bring in the material.** Copy files into the subject's `source_materials/` directory (or assign
   from the inbox with `index --inbox --assign-to thermo --copy`, which copies and never moves), then
   index:

   ```powershell
   python learningos.py index --subject thermo
   ```

   Read the warnings. A scanned PDF is an un-extracted source needing OCR, never an empty one.
4. **Map the syllabus.** Write the subject's `syllabus_map.md` unit by unit from the learner's
   syllabus document, citing it as `[source_id p.N]`. The syllabus, not the textbook contents page,
   defines coverage.
5. **Build the concept catalogue.** For each unit, list the concepts a learner must be able to *do*
   something with. Assign `concept_id` = `<subject_id>:<slug>`. Write both the readable
   `concept_catalog.md` and the machine-readable `concept_catalog.json` (id, name, unit, short
   description, prerequisite ids, source refs). Aim for concepts that are testable in one question;
   split anything that needs three.
6. **Draw the prerequisite graph.** Populate the subject's `concept_graph.json` with `prerequisite`,
   `related` and `applies-to` edges. Cross-subject prerequisites are allowed and use the other
   subject's prefix (for example `math1:definite-integral`); the target concept must exist in that
   subject. Every edge endpoint must resolve or `validate` fails with exit 3.
7. **Seed the concept records.** Create one record per catalogued concept at `status: "unassessed"`,
   `mastery_estimate: null`, empty evidence. Do not guess starting mastery — that is what the first
   diagnostic session is for.
8. **Set priorities.** Note the exam date, unit weightings and any known weak areas in
   `subject_state.json` `goals`/`constraints` and in `03_MEMORY/global/active_priorities.md`.
9. **Validate and visualise.**

   ```powershell
   python learningos.py validate --subject thermo
   python learningos.py update-graphs --subject thermo --kinds concept_prerequisite
   ```

10. **Write the first handover.** The subject's `next_session.md` should name the first diagnostic
    activity, not "start learning".

## Outputs it writes

| Path | Content |
|---|---|
| `02_KNOWLEDGE/subjects/` | the new subject directory: `subject_config.json`, `README.md`, `syllabus_map.md`, `concept_catalog.md`, `concept_catalog.json`, `source_index.json`, `source_materials/`, `extracted_text/` |
| `03_MEMORY/subjects/` | the new state directory: `subject_state.json`, `concept_mastery.json`, `concept_graph.json`, `misconceptions.json`, `revision_queue.json`, `open_questions.md`, `next_session.md` |
| `03_MEMORY/global/workspace_state.json` | subject registered |
| `03_MEMORY/global/learner_index.md` | one line added for the subject |
| `03_MEMORY/events/session_events.jsonl` | `subject_initialized`, `source_indexed`, `graph_generated` |
| `03_MEMORY/graphs/` | the initial prerequisite graph |

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py init-workspace
python learningos.py init-subject --id <id> --name "<name>" [--exam-date YYYY-MM-DD] [--dry-run]
python learningos.py index --subject <id> [--force]
python learningos.py index --inbox --assign-to <id> --copy
python learningos.py validate --subject <id>
python learningos.py update-graphs --subject <id>
python learningos.py status
```

## Failure handling

| Failure | Response |
|---|---|
| Bad id (`Algo!`, `My Subject`) | `init-subject` exits 2; propose a valid slug |
| Subject already exists | the command is a no-op, exits 0; extend the catalogue instead of recreating |
| Existing subject is corrupt | exit 3 naming the file; offer `restore`, never overwrite the survivor |
| Syllabus is a scanned PDF | report which pages are unreadable; ask for the text; do not invent units |
| Cross-subject prerequisite target missing | `validate` exits 3 naming both ids; create the concept in the other subject or drop the edge |
| Learner wants to rename a subject | explain the cost (every concept id changes) and do it as an explicit, backed-up migration, not in passing |

## Worked example

> **Learner:** new subject, Thermodynamics, exam 18 May. Syllabus and two chapters are in the inbox.

1. Agree `thermo`. `init-subject --id thermo --name "Thermodynamics" --exam-date 2026-05-18` → both
   directories created, `subject_initialized` appended, `concepts == {}`.
2. `index --inbox` lists three files; `index --inbox --assign-to thermo --copy` copies them;
   `index --subject thermo` extracts all three. `thermo-ch4.pdf` reports
   `likely_scanned: true, needs_ocr: true` → flagged to the learner, not treated as empty.
3. `syllabus_map.md`: five units from the syllabus PDF, cited `[thermo-syllabus-9c2e1f0a p.2]`.
4. Catalogue: 34 concepts, for example `thermo:first-law`, `thermo:entropy`,
   `thermo:carnot-efficiency`. Each with a unit, a one-line description and source refs.
5. Graph: `thermo:entropy` requires `thermo:second-law`; `thermo:carnot-efficiency` requires both;
   a cross-subject edge to `math1:definite-integral` because that subject already exists.
6. 34 records seeded at `unassessed`. `validate --subject thermo` exits 0.
7. `update-graphs --subject thermo --kinds concept_prerequisite` → `.json`, `.mmd` and `.html` under
   `03_MEMORY/graphs/`; SVG is skipped with a notice because no renderer is installed.
8. `next_session.md`: "Diagnostic pass over Unit 1: three doing-level questions on the first law,
   then decide whether Unit 1 needs teaching or only retrieval. Bring the OCR'd text of ch4."
