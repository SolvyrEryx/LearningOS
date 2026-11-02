# Concept catalogue: _template

The readable companion to `concept_catalog.json`. Both describe the same concepts; the JSON is what
the tooling reads, this file is what you read. Keep them in step — if they disagree, the JSON wins.

## What makes a good concept entry

- **Testable in one question.** If checking it needs three questions, it is three concepts.
- **Stated as a capability**, not a topic. "Derive a0 for a periodic signal" beats "Fourier basics".
- **Stable id.** `<subject_id>:<slug>`, lowercase, hyphenated, and never renamed casually — the id
  appears in every event, graph and citation.
- **Honest prerequisites.** Including ones in other subjects, written with that subject's prefix.

## Columns

| Column | Meaning |
|---|---|
| id | `<subject_id>:<slug>` |
| concept | the capability, in one line |
| unit | syllabus unit |
| prerequisites | concept ids that must come first |
| sources | `[source_id p.N]` |

## Catalogue

### Unit 1 — (not yet mapped)

| id | concept | unit | prerequisites | sources |
|---|---|---|---|---|
| `_template:example-concept` | Replace this row: state what the learner must be able to do. | 1 | — | — |

## Not in the catalogue

List anything you have deliberately decided not to learn, with the reason (not examinable, covered
elsewhere, out of time). Being explicit about what you dropped is more useful than a silent gap.

## Maintenance

Concepts are added as the syllabus is mapped, not all at once. After editing this file or the JSON,
run `python learningos.py validate --subject <subject_id>` from `01_AGENTS_AND_SKILLS/tools`:
dangling prerequisites and malformed ids are reported with exit code 3.
