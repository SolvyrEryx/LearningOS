# Skill: knowledge_graph

## Purpose

Turn memory into structure the learner can see: which concepts depend on which, which are mastered,
which are blocked by a prerequisite that is not, where misconceptions cluster, and how subjects
connect. Unlike `visual_learning` — which hand-draws a diagram to explain one concept — every
artefact here is **derived from the memory files** and is fully regenerable.

## When to invoke

- "Show me the map", "what's left?", "what's blocking X?", "where am I weak?".
- At the end of a session in which statuses or graph edges changed.
- After `subject_initializer` has built a catalogue and a prerequisite graph.
- Before an exam, to find unmastered concepts with the most dependents.
- When choosing a learning order: topological order over the prerequisite graph is the default.

## The pipeline

Data-first, in four stages, each stage optional after the second:

```
memory JSON  →  canonical graph JSON  →  Mermaid text  →  { SVG via mmdc | self-contained HTML }
   (truth)        (graph_artifact)        (pure function)        (optional renderings)
```

1. **Build** reads only `concept_mastery.json`, `concept_graph.json`, `misconceptions.json`,
   `revision_queue.json`, `subject_state.json` and the event log, and emits a graph object
   conforming to `01_AGENTS_AND_SKILLS/schemas/graph_artifact.schema.json`: `nodes`, `edges`,
   `legend`, `generated_at`, `generator` and `source_fingerprints` (a sha256 per input file, so you
   can tell which state a picture was drawn from). Every node and edge id is checked against the
   concept records **before** anything is returned.
2. **Mermaid** is a pure, deterministic function of that JSON. Node keys are sanitised injectively
   (`[^A-Za-z0-9]` → `_`, plus a 6-character sha1 suffix if two keys would collide); labels are
   quoted and internal quotes escaped as `#quot;`; nodes and edges are emitted in sorted order, so
   the same memory produces byte-identical `.mmd` output. The one exception is the "review due"
   marker, which is a claim about *today*: unchanged memory can legitimately render differently
   across a day boundary.
3. **SVG** is produced only if `mmdc` (mermaid-cli) is on PATH.
4. **HTML** is always produced: a self-contained page showing the Mermaid source, which renders it
   if a vendored `mermaid.min.js` is present and otherwise shows the escaped source plus a visible
   notice explaining the two ways to enable rendering.

**Renderer-missing fallback:** when `mmdc` is not found, `.json`, `.mmd` and `.html` are still
written, the run prints `SVG skipped: mermaid-cli (mmdc) not found on PATH` and exits 0. Only
`--require-renderer` turns that into a failure (exit 6). The artefact is the Mermaid text; the
picture is a convenience.

## Graph kinds

| Kind | Nodes | Edges | Shape |
|---|---|---|---|
| `concept_prerequisite` | all concepts, grouped by unit | `prerequisite`, `related`, `applies-to` | `flowchart LR` with a `subgraph` per unit |
| `learner_mastery` | all concepts styled by status | prerequisites | `flowchart TD`, one `classDef` per status, `Legend` subgraph |
| `misconception` | concepts + misconception nodes | `exhibits` | `flowchart TD`, misconceptions as `{{hexagons}}` |
| `cross_subject` | one subgraph per subject | only cross-subject prerequisite edges | `flowchart LR` |
| `progress` | status counts + per-session advances | — | `pie title Concept status distribution` |

`cross_subject` is the one graph that spans subjects, and it is strictly read-only in both
directions: a subject's mastery is **never** applied to another subject's nodes. Nodes keep the
status recorded in their own subject's files, which is why a freshly initialised subject shows as
`unassessed` even when a neighbouring subject is fully mastered.

## Inputs it reads

| Path | Why |
|---|---|
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | nodes, statuses, mastery estimates |
| `03_MEMORY/subjects/<subject_id>/concept_graph.json` | the edge source of truth |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | misconception nodes and `exhibits` edges |
| `03_MEMORY/subjects/<subject_id>/revision_queue.json` | due markers |
| `03_MEMORY/subjects/<subject_id>/subject_state.json` | units, coverage |
| `03_MEMORY/events/session_events.jsonl` | the per-session advance series for `progress` |
| `01_AGENTS_AND_SKILLS/templates/graph_templates/` | static frames: headers, `classDef` blocks, legend, HTML shell |
| `01_AGENTS_AND_SKILLS/schemas/graph_artifact.schema.json` | the artefact contract |

## Outputs it writes

| Path | Content |
|---|---|
| `03_MEMORY/graphs/` | `<subject_id>/<kind>.json`, `.mmd`, `.html` (and `.svg` when a renderer exists) |
| `03_MEMORY/graphs/_cross/` | the `cross_subject` artefacts |
| `03_MEMORY/events/session_events.jsonl` | one `graph_generated` event per artefact |

Nothing else. This skill never writes a source-of-truth memory file, and deleting
`03_MEMORY/graphs/` is always safe — re-running `update-graphs` recreates byte-identical `.mmd`,
apart from the clock-dependent "review due" marker described above.

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py update-graphs --subject thermo
python learningos.py update-graphs --all
python learningos.py update-graphs --subject thermo --kinds concept_prerequisite,learner_mastery
python learningos.py update-graphs --all --format mmd,html --dry-run
python learningos.py update-graphs --subject thermo --require-renderer
python learningos.py validate --subject thermo
```

## Reading a graph with the learner

- **Blocked work** — an `unassessed` node whose prerequisites are `provisionally_mastered` is ready
  now; an `unassessed` node with an `unassessed` prerequisite is not, and teaching it will fail.
- **Leverage** — the unmastered node with the most dependents is the highest-value next concept.
- **Clusters** — several `needs_review` nodes sharing a prerequisite means the shared prerequisite is
  the real problem.
- **Misconception hubs** — one misconception touching many concepts deserves a dedicated session.
- **Cross-subject** — a prerequisite living in another subject explains stalls that look
  inexplicable inside this one.

Always pair the picture with one sentence of interpretation and one proposed action. A map with no
decision attached is decoration.

## Failure handling

| Failure | Response |
|---|---|
| Dangling edge endpoint | exit 3 naming the `from`/`to` id **before writing any file**; fix the edge or create the concept |
| Duplicate concept id | `validate` exits 3; resolve in `concept_mastery.json` first |
| `mmdc` missing | `.mmd` + `.html` still written, skip notice printed, exit 0 (exit 6 only with `--require-renderer`) |
| Corrupt input memory file | exit 3 naming file and pointer; no graph written; never repair |
| Graph directory deleted | not an error — regenerate |
| Graph is unreadably large | use `--kinds` and per-unit subgraphs; offer a filtered view rather than a wall |
| Cross-subject graph requested with one subject | produce it with a single subgraph and say so |

## Worked example

> **Learner:** exam in 18 days — what should I actually work on?

1. `update-graphs --subject thermo --kinds learner_mastery,concept_prerequisite` → six files under
   `03_MEMORY/graphs/` in the `thermo` directory. `mmdc` is absent, so the SVG skip notice prints and
   the exit code is 0.
2. The mastery graph shows 34 nodes: 9 `provisionally_mastered`, 11 `practising`, 6 `needs_review`,
   8 `unassessed`, with a `classDef` per status and a `Legend` subgraph carrying text labels.
3. Reading it: five of the six `needs_review` nodes depend on `thermo:second-law`, which is itself
   only `practising`. Three `unassessed` nodes are blocked behind it too.
4. Interpretation and action, in one breath: *"`thermo:second-law` is the bottleneck — eight
   concepts sit behind it. One hint-free run at a two-day delay promotes it and unblocks the lot.
   That is tomorrow's first activity."*
5. Written to the subject's `next_session.md`; one `graph_generated` event per artefact appended.
6. A week later, `update-graphs --all` also refreshes `03_MEMORY/graphs/_cross/`, showing that
   `thermo:entropy` depends on `math1:definite-integral` — and the `math1` nodes keep `math1`'s own
   statuses, untouched by anything learned in `thermo`.
