# 03_MEMORY — learner state

This is the part of LearningOS that makes it an operating system rather than a prompt collection.
It records what has actually been demonstrated, what went wrong, what is due, and where the last
session stopped. Everything here is plain text or JSON you can read, diff, back up and hand-edit.

## Four layers, kept deliberately apart

| Layer | Where | Written | Rebuildable |
|---|---|---|---|
| **Evidence** | `03_MEMORY/events/session_events.jsonl` | append-only, one JSON object per line | no — this is the record |
| **Current state** | `03_MEMORY/subjects/<id>/*.json`, `03_MEMORY/global/` | overwritten atomically | yes, by replaying events |
| **Derived artefacts** | `03_MEMORY/graphs/`, `03_MEMORY/session_logs/`, search cache | regenerated on demand | yes, always |
| **Checkpoints** | `03_MEMORY/checkpoints/<id>/<session_id>.json` | once per checkpoint trigger | no, but losing one only costs a resume briefing |

The separation matters. The event log is the only thing that cannot be reconstructed, so it is
append-only and never rewritten — not even to fix a bad line. Snapshots exist because reading one
small JSON file is faster than replaying a year of events. Derived files are disposable by design,
so deleting `03_MEMORY/graphs/` is always safe.

## Layout

```
03_MEMORY/
├── README.md
├── global/
│   ├── learner_profile.md        durable facts about how you learn
│   ├── learning_preferences.json machine-readable session preferences
│   ├── learner_index.md          one-line index of every subject
│   ├── active_priorities.md      what matters in the next few weeks
│   └── workspace_state.json      current subject, last session, tool version
├── subjects/
│   ├── _template/                the shape a new subject's state takes
│   └── <subject_id>/
│       ├── subject_state.json      coverage, counters, goals
│       ├── concept_mastery.json    the concept records — the core of the system
│       ├── concept_graph.json      prerequisite / related / applies-to edges
│       ├── misconceptions.json     named misconceptions and their remediation
│       ├── revision_queue.json     what is due, and when
│       ├── open_questions.md       unresolved questions to come back to
│       └── next_session.md         the handover note for the next session
├── events/session_events.jsonl   the append-only evidence log
├── session_logs/                 generated per-session markdown reports
├── graphs/                       generated concept and mastery graphs
├── checkpoints/                  mid- and end-of-session resume points
├── archives/                     material you have retired but want to keep
└── backups/                      zipped snapshots plus their manifests
```

## Rules that protect this directory

1. **Atomic writes.** Every JSON write goes to a temp file in the same directory, is flushed and
   fsynced, then `os.replace`d over the target. A crash leaves either the old file or the new one,
   never half of either.
2. **Validate before writing.** A snapshot is schema-checked against `01_AGENTS_AND_SKILLS/schemas/`
   before it reaches the disk.
3. **Never repair corruption.** A file that will not parse is reported with its path, the location
   of the problem and a remedy, and left byte-identical. Recovery is `restore`, or your editor —
   never a silent rewrite.
4. **No absolute paths inside memory.** Every stored path is workspace-relative with forward
   slashes, so the whole folder can be moved or copied to another machine.
5. **Subject isolation.** A concept record in `03_MEMORY/subjects/chem/` must have ids prefixed
   `chem:`. Nothing copies mastery between subjects.

## Checking and maintaining it

Run from `01_AGENTS_AND_SKILLS/tools`:

| Command | Purpose |
|---|---|
| `python learningos.py validate` | full health check; exit 3 on any error |
| `python learningos.py status` | one-screen summary of subjects, due reviews, last session |
| `python learningos.py reconcile --dry-run` | report drift between the event log and snapshots |
| `python learningos.py backup --note "before exam"` | zip this directory plus a manifest |
| `python learningos.py restore --from <zip> --dry-run` | preview a restore without writing |

The protocol these commands implement is specified in
`01_AGENTS_AND_SKILLS/core/memory_protocol.md`.

## Privacy

Nothing in this directory is uploaded by LearningOS or its tooling. It is, however, the material an
agent reads during a session — and if that agent is a hosted model, the content of the files it
reads is sent to that provider as part of the conversation. Local files do not mean local
inference. Keep anything you would not share with your model provider out of this directory.
