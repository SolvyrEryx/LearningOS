# Skill: visual_learning

## Purpose

Produce diagrams that do cognitive work: dependency chains, process flows, state machines,
comparisons across several dimensions, part-whole decompositions. The test for a diagram is whether
the learner can answer a question with it that they could not answer without it. Decoration fails
that test and is not produced.

This skill makes *explanatory* diagrams for a concept. Whole-subject concept and mastery graphs
derived from memory are `knowledge_graph`'s job.

## When to invoke

- The structure *is* the content: ordering, branching, feedback, hierarchy, multi-axis comparison.
- The learner is confusing two neighbouring concepts and needs their boundary made visible.
- The learner asks for a diagram, a concept map, a flowchart or a summary sheet.
- A misconception is spatial or structural ("entropy flows like heat").

Do not invoke it for a definition, a single formula, or a linear list — prose is better for those.

## Inputs it reads

| Path | Why |
|---|---|
| `01_AGENTS_AND_SKILLS/core/teaching_principles.md` §9 | when a visual is justified |
| `01_AGENTS_AND_SKILLS/templates/graph_templates/` | the static Mermaid frames and the HTML shell |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | what the learner already has, so the diagram starts from it |
| `03_MEMORY/subjects/<subject_id>/misconceptions.json` | the confusion the diagram must resolve |
| `03_MEMORY/global/learning_preferences.json` | notation and modality preferences |
| `02_KNOWLEDGE/subjects/` | the subject's `extracted_text/` for the source's own terminology |

## Procedure

1. **State the question the diagram answers**, in one sentence, before drawing anything. If you
   cannot, do not draw it.
2. **Choose the form:**

   | Structure | Form |
   |---|---|
   | ordering / dependency | `flowchart LR` |
   | process with branches | `flowchart TD` with decision diamonds |
   | states and transitions | `stateDiagram-v2` |
   | time sequence between actors | `sequenceDiagram` |
   | part-whole / taxonomy | `flowchart TD` with `subgraph` |
   | multi-axis comparison | a markdown table, not a diagram |
   | distribution of statuses | `pie` |

3. **Keep it to 5–9 nodes.** Beyond that, split it. A diagram the learner cannot hold in working
   memory has moved the problem rather than solved it.
4. **Label every node and every edge.** An unlabelled arrow is a guess waiting to be made wrong.
5. **Never encode meaning in colour alone.** Use a text legend. This is an accessibility
   requirement, not a style preference: colour-blind readers and greyscale printouts must get the
   same information.
6. **Write it as text first.** Mermaid source in the conversation, so the learner can read it even
   if nothing renders.
7. **Save it as an artefact** when it is worth keeping: `.mmd` plus a self-contained `.html` viewer
   under `03_MEMORY/graphs/` in the subject's directory, named for the concept it explains. Append a
   `graph_generated` event. Diagrams are derived artefacts — they never write to a state file.
8. **Have the learner use it.** Ask them to trace a path, predict a transition, or add the missing
   node. A diagram they only looked at taught nothing.
9. **Record the outcome.** If the diagram worked, add it to `helpful_interventions` as
   `diagram:<short-name>`; if it did not, add it to `failed_interventions` so it is not repeated.

### Escaping rules for Mermaid labels

- Wrap every label in double quotes: `A["Entropy change ΔS"]`.
- Escape an internal double quote as `#quot;`.
- Node *keys* use `[A-Za-z0-9_]` only; put the readable text in the label.
- Labels containing `:` or `->` are safe once quoted; keys containing them are not.

### Rendering

Mermaid text is the canonical artefact. HTML viewers are generated from the templates in
`01_AGENTS_AND_SKILLS/templates/graph_templates/`. SVG requires `mmdc` (mermaid-cli) on PATH; when
it is absent, the `.mmd` and `.html` are still written and the run prints
`SVG skipped: mermaid-cli (mmdc) not found on PATH` and exits 0. Tell the learner the two ways to
get pictures: install mermaid-cli, or open the generated `.html`.

## Outputs it writes

| Path | Content |
|---|---|
| `03_MEMORY/graphs/` | `<name>.mmd` and `<name>.html` in the subject's subdirectory |
| `03_MEMORY/events/session_events.jsonl` | `graph_generated`, and `explanation_given` for the teaching step |
| `03_MEMORY/subjects/<subject_id>/concept_mastery.json` | `helpful_interventions` / `failed_interventions` (via `memory_manager`) |

## Commands it may run

From `01_AGENTS_AND_SKILLS/tools`:

```powershell
python learningos.py update-graphs --subject thermo --kinds learner_mastery --format mmd,html
python learningos.py status
```

Hand-authored concept diagrams are written as text by this skill; `update-graphs` is for the
memory-derived graphs.

## Failure handling

| Failure | Response |
|---|---|
| Diagram would exceed 9 nodes | split into two, each with its own question |
| No renderer available | `.mmd` + `.html` still written; state the skip notice and the two remedies |
| Learner cannot read diagrams in their client | give the Mermaid source plus a numbered prose walk-through |
| Label breaks Mermaid syntax | quote it and escape `"` as `#quot;`; never silently drop characters |
| Diagram did not help | record it in `failed_interventions` and switch to a worked example |
| Learner asks for colour-coded output only | add the text legend anyway; explain why |

## Worked example

> **Learner:** I keep mixing up reversible and irreversible processes.

1. Question the diagram must answer: *"given a process, which branch applies and what does that
   imply for ΔS of the universe?"* Structure is branching → `flowchart TD`.
2. Source:

   ```mermaid
   flowchart TD
     A["Process on a system"] --> B{"Quasi-static and frictionless?"}
     B -- "yes" --> C["Reversible"]
     B -- "no" --> D["Irreversible"]
     C --> E["ΔS#quot;universe#quot; = 0"]
     D --> F["ΔS#quot;universe#quot; > 0"]
     E --> G["Carnot bound attained"]
     F --> H["Carnot bound not attained"]
   ```

3. Seven nodes, every edge labelled, no colour-only meaning.
4. Use it: "free expansion of a gas into vacuum — trace the path and tell me ΔS of the universe."
   Learner follows B → D → F correctly and unaided.
5. Saved as `reversibility.mmd` plus `reversibility.html` in the subject's directory under
   `03_MEMORY/graphs/`; `graph_generated` appended; SVG skipped with the documented notice.
6. `helpful_interventions` gains `diagram:reversibility`; the open misconception
   `thermo:mc-002` moves to `monitoring` after the correct unaided trace.
