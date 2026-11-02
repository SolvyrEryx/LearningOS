# Codex adapter

**Nothing to install.** Codex and other `AGENTS.md`-aware agents read the root `AGENTS.md`
automatically when the workspace is opened from the LearningOS folder. That file is the canonical
entrypoint, and it points at everything else.

## Usage

```powershell
cd C:\Study\LearningOS
codex
```

Then talk normally: *"Resume my Thermodynamics session"*, *"Teach me Module 3 from scratch"*,
*"What's due today?"*.

If the agent seems not to have read the contract — it skips the resume briefing, or asks what it
should do with no reference to your memory files — say:

> Read AGENTS.md, then `01_AGENTS_AND_SKILLS/core/system_contract.md`, then the skill that matches
> what I just asked for.

## Why there is no steering file here

Root `AGENTS.md` *is* the Codex convention. Duplicating the instructions into an adapter would
create a second copy that silently drifts out of date, which is the failure mode this design
avoids. Both adapters therefore hold pointers only: the Kiro one exists purely because Kiro reads
`.kiro/steering/` rather than `AGENTS.md`. See
`01_AGENTS_AND_SKILLS/adapters/kiro/README.md`.

## Running from a subdirectory

The tooling finds the workspace root by looking for `learningos.config.json`, walking up from its
own location, so this works from anywhere:

```powershell
python C:\Study\LearningOS\01_AGENTS_AND_SKILLS\tools\learningos.py status
```

You can also be explicit with `--workspace`, or set `LEARNINGOS_HOME` to the folder.

## Capability notes

| Capability | Status with Codex |
|---|---|
| Reads root `AGENTS.md` automatically | yes — this is the whole adapter |
| Reads and writes workspace files | yes |
| Runs the Python CLI | yes, via the shell |
| Loads every instruction file up front | no, and that is intended: `AGENTS.md` defines a load order so only the relevant skill is read |
| Renders Mermaid inline | no — open the generated `.html` artefact, or install mermaid-cli for `.svg` |

## Other agents

Any agent that can read files and run a shell can drive LearningOS. The requirements are only:

1. It reads `AGENTS.md` at session start (ask it to, if it does not do so on its own).
2. It can read and write files under `02_KNOWLEDGE/` and `03_MEMORY/`.
3. It can run `python learningos.py ...` from `01_AGENTS_AND_SKILLS/tools`.

Nothing in the workspace is specific to one model or vendor. If a new agent needs its own discovery
convention, add a thin adapter beside this one — pointers only, never a second copy of the rules.
