# 02_KNOWLEDGE — subject material

This directory holds *what is being learned*. It never holds *what the learner has demonstrated* —
that lives in `03_MEMORY/`. Keeping the two apart means you can replace a textbook without losing
your mastery history, and wipe a bad memory experiment without losing your sources.

## Layout

```
02_KNOWLEDGE/
├── README.md               this file
├── inbox/                  drop zone for new documents, un-triaged
├── shared_reference/       material used by more than one subject
└── subjects/
    ├── README.md
    ├── _template/          the shape every new subject is created from
    └── <subject_id>/       one directory per subject
```

Each subject directory contains:

| File / directory | Purpose |
|---|---|
| `subject_config.json` | identity, exam date, units, goals, constraints |
| `README.md` | human notes about the subject and its material |
| `syllabus_map.md` | the syllabus, unit by unit, with coverage notes |
| `concept_catalog.md` | the readable concept list for the subject |
| `concept_catalog.json` | the machine-readable concept list (ids, names, units, prerequisites) |
| `source_index.json` | one record per source file: hash, size, pages, extraction status |
| `source_materials/` | **your original files, never modified** |
| `extracted_text/` | generated plain text, one `.txt` per source, safe to delete and rebuild |

## Rules

1. **Originals are immutable.** Files in `source_materials/` are opened read-only. Nothing in
   LearningOS edits, renames, re-encodes or deletes them.
2. **Derived content is separate and disposable.** Everything in `extracted_text/` and every
   `source_index.json` entry can be regenerated from the originals.
3. **The inbox is copy-only.** Files are copied out of `inbox/` into a subject, never moved, so a
   mis-assignment is never a loss.
4. **Subject ids are lowercase slugs** matching `^[a-z0-9][a-z0-9_-]*$` (for example `math2`,
   `thermo`, `algo`). The directory name is the id, and every concept id in that subject must start
   with `<subject_id>:`.
5. **`_template/` is read-only scaffolding.** Do not store real material in it.

Full handling rules, including the citation format and the scanned-PDF policy, are in
`01_AGENTS_AND_SKILLS/core/source_handling.md`.
