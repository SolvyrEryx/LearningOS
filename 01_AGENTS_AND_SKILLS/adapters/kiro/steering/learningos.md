---
inclusion: always
---

# LearningOS steering

This workspace is a LearningOS install. Read the canonical instructions before teaching; they are
not duplicated here. This file only says where they live.

**Start with the root `AGENTS.md`.** Then load, in order:

1. `01_AGENTS_AND_SKILLS/core/system_contract.md` — obligations, prohibitions, precedence
2. `01_AGENTS_AND_SKILLS/core/teaching_principles.md` — diagnose, explain, fade, confront
3. `01_AGENTS_AND_SKILLS/core/memory_protocol.md` — what to record and in what shape
4. `01_AGENTS_AND_SKILLS/core/source_handling.md` — using and citing the learner's documents
5. `01_AGENTS_AND_SKILLS/core/session_protocol.md` — start, checkpoint, end, recover

Then load the one skill that matches the task from `01_AGENTS_AND_SKILLS/skills/` — for example
`01_AGENTS_AND_SKILLS/skills/session_manager/SKILL.md` to open or close a session, or
`01_AGENTS_AND_SKILLS/skills/adaptive_tutor/SKILL.md` to teach a concept. The full list with
one-line descriptions is in the root `AGENTS.md`.

Learner state to read at session start: `03_MEMORY/global/learner_profile.md`,
`03_MEMORY/global/learning_preferences.json`, `03_MEMORY/global/active_priorities.md`, then the
active subject's files under `03_MEMORY/subjects/` and `02_KNOWLEDGE/subjects/`.

Tooling: run `python learningos.py --help` from `01_AGENTS_AND_SKILLS/tools`.

Five reminders, each defined in full in the files above — follow the file, not this summary:

- Files are truth; nothing counts until it is written to `03_MEMORY/`.
- Non-destructive: never delete, move or rewrite the learner's material.
- Corrupt memory is reported, never silently repaired.
- A hinted success is never an independent success.
- Subjects are isolated; a concept id is `<subject_id>:<slug>`.

Installation and update instructions for this file: `01_AGENTS_AND_SKILLS/adapters/kiro/README.md`.
