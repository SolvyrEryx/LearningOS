# Kiro adapter

Kiro discovers always-on workspace instructions from steering files in `.kiro/steering/`. LearningOS
keeps its canonical instructions in `01_AGENTS_AND_SKILLS/core/` and the root `AGENTS.md`; this
adapter is a one-file pointer that tells Kiro to read them.

## Install (one manual step)

Run once, from the workspace root:

```powershell
New-Item -ItemType Directory -Force -Path 'C:\Study\LearningOS\.kiro\steering' | Out-Null
Copy-Item -Path 'C:\Study\LearningOS\01_AGENTS_AND_SKILLS\adapters\kiro\steering\learningos.md' -Destination 'C:\Study\LearningOS\.kiro\steering\learningos.md' -Force
```

Verify:

```powershell
Get-Content 'C:\Study\LearningOS\.kiro\steering\learningos.md' -TotalCount 3
```

The first three lines should be the front-matter block containing `inclusion: always`.

If you copied the LearningOS folder to a different location, substitute that path. The steering file
contains only workspace-relative references, so it needs no editing after a move.

## Why it is a copy, not a symlink or a config entry

Kiro reads `.kiro/steering/*.md` from the workspace. `.kiro/` is tool-specific state, and LearningOS
is designed to be portable across agents, so it is not part of the three principal directories. A
copy keeps the two concerns separate: delete `.kiro/` and LearningOS is untouched; delete the
adapter and your Kiro install is untouched.

Symlinks would avoid the copy but need elevated privileges or developer mode on Windows, so the
documented path is an explicit copy.

## Keeping it in sync

The steering file is deliberately thin — pointers only, under 60 lines, with no teaching rules of
its own. That is what makes it safe to copy: the canonical content it points at can change freely
without the copy going stale.

Re-run the copy command when `01_AGENTS_AND_SKILLS/adapters/kiro/steering/learningos.md` itself
changes (that is, when a core file is added, removed or renamed). Changing the *content* of a core
file needs no re-copy.

To check whether the installed copy is current:

```powershell
$a = Get-FileHash 'C:\Study\LearningOS\01_AGENTS_AND_SKILLS\adapters\kiro\steering\learningos.md'
$b = Get-FileHash 'C:\Study\LearningOS\.kiro\steering\learningos.md'
if ($a.Hash -eq $b.Hash) { 'up to date' } else { 'stale - re-copy' }
```

## What Kiro gives you, and what it does not

| Capability | Status |
|---|---|
| Always-on instructions via steering | works after the copy step above |
| Reading and writing workspace files | built in |
| Running `learningos.py` in the terminal | built in |
| Automatic loading of every file in this folder | **no** — the agent loads what the steering file and `AGENTS.md` point it to, on demand |
| Rendering Mermaid in the chat pane | depends on your Kiro version; the generated `.html` artefacts always work in a browser |

If steering is not being picked up, check that the file sits directly in `.kiro/steering/`, that the
front matter is the very first thing in the file, and that you have reopened the workspace.

## Without the copy step

LearningOS still works: ask the agent to read `AGENTS.md` at the start of a session. The steering
file only removes that manual prompt. See `01_AGENTS_AND_SKILLS/adapters/codex/README.md` for the
equivalent situation with Codex, where root `AGENTS.md` is discovered automatically and no copy is
needed.
