# subjects — one directory per subject

Each subdirectory here is a subject. Its name **is** the subject id: a lowercase slug matching
`^[a-z0-9][a-z0-9_-]*$`, for example `math2`, `algo`, `thermo`.

## Creating a subject

Do not copy `_template/` by hand. Run, from `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py init-subject --id algo --name "Algorithms" --exam-date 2026-05-18
```

That creates both halves of a subject in one idempotent step:

- `02_KNOWLEDGE/subjects/algo/` — the material side, from
  `01_AGENTS_AND_SKILLS/templates/subject_template/`
- `03_MEMORY/subjects/algo/` — the state side, from
  `01_AGENTS_AND_SKILLS/templates/memory_templates/`

Running it a second time changes nothing: existing files are reported as skipped, mastery is not
reset, and catalogue entries are not duplicated.

## The isolation rule

Every concept id inside a subject must be `<subject_id>:<slug>`, and the prefix must equal the
directory name. This is checked by `python learningos.py validate`, which fails with exit code 3 if
a record filed under `chem` carries an id like `physics:entropy`. The rule exists so that
cross-subject contamination is a detectable data error rather than a matter of discipline.

Sources may be shared via `02_KNOWLEDGE/shared_reference/`. Mastery is never shared.

## `_template/`

`_template/` is the scaffolding a new subject is built from, kept in the tree so you can see the
exact shape of a subject without creating one. It is not a real subject: its `subject_id` is
`_template`, its collections are empty, and the tooling skips directories whose name starts with
`_`. Do not store real material in it.
