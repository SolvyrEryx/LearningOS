# Subject: _template

Replace this heading with the subject's name. This file is for your own notes about the subject —
what the course is, who teaches it, which book edition you are using, what the exam looks like, and
anything else you would want to remember in six months. The agent reads it for context; nothing
here is parsed.

## What lives in this directory

| Path | Purpose |
|---|---|
| `subject_config.json` | identity, exam date and format, units, goals, constraints |
| `syllabus_map.md` | the syllabus unit by unit, with coverage notes |
| `concept_catalog.md` | the readable concept list |
| `concept_catalog.json` | the machine-readable concept list |
| `source_index.json` | one record per source: hash, size, pages, extraction status |
| `source_materials/` | your original files — never modified by anything here |
| `extracted_text/` | generated plain text, one `.txt` per source, safe to delete |

Learner state — mastery, misconceptions, revision queue, session history — is **not** here. It
lives in this subject's directory under `03_MEMORY/subjects/`. The split means you can swap a
textbook without touching your progress, and reset progress without losing your material.

## Getting started

1. Copy your syllabus, notes, textbook chapters and past papers into `source_materials/`.
2. From `01_AGENTS_AND_SKILLS/tools`, run `python learningos.py index --subject <subject_id>`.
3. Read the warnings — a scanned PDF needs OCR and will say so.
4. Ask the agent to map the syllabus and build the concept catalogue; it follows
   `01_AGENTS_AND_SKILLS/skills/subject_initializer/SKILL.md`.
5. Start a session and let the first one be diagnostic.

## Notes

- Exam date and format:
- Book / edition:
- Known weak areas:
- Notation this course uses:
- Anything the agent keeps getting wrong about this subject:
