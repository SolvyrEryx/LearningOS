# Learner index

One line per subject: the fastest way to see the whole workspace at a glance. Maintained by
`init-subject` and refreshed at the end of each session. The machine-readable equivalent is
`03_MEMORY/global/workspace_state.json`; if the two disagree, that file wins.

## Subjects

| Subject id | Name | Exam date | Concepts | Provisionally mastered | Needs review | Last session |
|---|---|---|---|---|---|---|
| — | no subjects yet | — | 0 | 0 | 0 | — |

## How to read this

- **Concepts** — entries in the subject's catalogue.
- **Provisionally mastered** — enough unaided evidence to stop teaching and start spacing. Not
  "finished".
- **Needs review** — failed, or a mistake pattern seen twice, or a review date passed unattempted.
  This is the column that should drive your next session.
- **Last session** — if this is weeks old, expect retrieval to be slower than the mastery numbers
  suggest.

## Getting a fresh copy

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py status
```

`status` reads the current state directly and is always authoritative over this summary.

## Adding a subject

```powershell
python learningos.py init-subject --id <id> --name "<name>" --exam-date YYYY-MM-DD
```

The command is idempotent: running it twice changes nothing, and it never touches another subject's
mastery.
