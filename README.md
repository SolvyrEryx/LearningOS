# LearningOS

**An AI tutor with learning memory you own.**

LearningOS turns a folder and a file-capable AI agent into a persistent study workspace. It records what you attempted, where you needed help, and what to practise next, so your next session can build on the last.

Learn a subject, prepare for an interview, work towards an exam, or explore a new skill. An exam date, syllabus and uploaded documents are optional.

**The everyday loop:** `/start` → study normally → `/save` → `/continue`.

**Share with a friend:** [Visit the LearningOS landing page](https://solvyreryx.github.io/LearningOS/).

[Getting-started PDF](01_AGENTS_AND_SKILLS/docs/LearningOS_Getting_Started.pdf) · [Student guide](01_AGENTS_AND_SKILLS/docs/STUDENT_GUIDE.md) · [Command reference](01_AGENTS_AND_SKILLS/tools/README.md) · [MIT license](LICENSE)

## What it does

- Checks what you can do before explaining the next piece.
- Uses worked examples, reduces help gradually, and asks you to apply ideas yourself.
- Separately records independent success, hinted success, mistakes and misconceptions.
- Schedules retrieval practice and leaves an exact handover for the next session.
- Generates a local **Learning Atlas** with concepts, evidence, notes, sources and review tasks.
- Keeps each subject's learning history separate, in readable Markdown and JSON files.

This is a folder, a conversation and a small Python toolkit. Teaching happens in your AI agent. The Atlas is a generated offline report you open in a browser.

## Start in a few minutes

### 1. Get your own copy

Use GitHub's **Code → Download ZIP**, extract it, and keep the folder somewhere permanent. Alternatively:

```sh
git clone https://github.com/SolvyrEryx/LearningOS.git
cd LearningOS
```

Each learner needs a separate copy. This repository contains the system and neutral templates, with no personal subjects, source documents or learning history.

### 2. Check the requirements

- **Python 3.11 or newer**, available as `python` in your terminal. Check with `python --version`. On systems that use `python3`, substitute it in the commands below.
- **An AI agent that can read and write local files and run shell commands.** Open the whole LearningOS folder as its workspace. A chat that only accepts pasted text cannot maintain the folder automatically.

The core toolkit uses the Python standard library. PDF text extraction optionally needs `pypdf`; no package is installed automatically. Your chosen AI agent may need its own account or subscription.

### 3. Ask your tutor to initialise and begin

Paste this into the agent with the LearningOS folder open:

> Read AGENTS.md and the five core files it points to. First run `python 01_AGENTS_AND_SKILLS/tools/learningos.py init-workspace`, then `python 01_AGENTS_AND_SKILLS/tools/learningos.py validate`. Preserve existing files and report any errors. I want to learn [your subject or skill]. Help me choose a small first goal, check what I already know, and start teaching. Save real evidence as we go.

Replace the bracketed phrase with your subject. Initialisation creates your private learner files from neutral templates. It is safe to repeat: existing learner files are preserved. The agent can handle the commands for you.

If you prefer to initialise yourself, run from the repository root:

```sh
python 01_AGENTS_AND_SKILLS/tools/learningos.py init-workspace
python 01_AGENTS_AND_SKILLS/tools/learningos.py validate
```

Agents that discover root `AGENTS.md` can use it directly. For Kiro's optional always-on setup, follow the [Kiro adapter](01_AGENTS_AND_SKILLS/adapters/kiro/README.md). Otherwise explicitly ask your agent to read `AGENTS.md` at the start of each chat.

## Study, save, return

These are messages to your tutor, not terminal commands or buttons in the Atlas. Plain sentences work too.

| You want to… | Say… |
|---|---|
| Begin or select a subject | `/start Biology` |
| Focus on a topic | `/learn Genetics` |
| Get a plan for available time | `/today 30` |
| Retrieve older material | `/review` |
| Try an unaided assessment | `/test` |
| Work on recurring errors | `/mistakes` |
| Stop and leave a handover | `/save` |
| Pick up next time | `/continue` |
| Rebuild your local report | `/atlas` |
| Get help | `/help` |

Show your working and explain your reasoning. Ask for a hint when needed; assistance stays recorded as assistance. Reading an explanation or saying "I understand" does not establish mastery.

Before stopping, ask `/save`. Look for a receipt stating **memory saved**, **Atlas refreshed** (or a specific refresh problem), and **the first activity for next time**. Closing the chat alone cannot save unrecorded work. The tutor should also persist meaningful cycles during the session.

## Add your own material, if you want

Start without documents, or copy notes, a syllabus or past papers into `02_KNOWLEDGE/inbox/`. Ask your tutor to assign and index them for the right subject. Assignment copies files; originals are preserved.

- **Markdown and plain text:** read locally without extra packages.
- **Text PDFs:** extraction requires optional `pypdf`. To enable it, run `python -m pip install pypdf` in the same Python environment as LearningOS.
- **Scanned PDFs:** need a separate OCR step; failed extraction is reported.
- **Word, slides and other formats:** export a copy to PDF, Markdown or plain text.

The tutor cites indexed material using `[source_id p.N]` (or line numbers for text). Teaching from general knowledge must be labelled as such. AI explanations and grading can still be wrong; check disputed claims against reliable material.

## View your Learning Atlas

Ask `/atlas`, or run this from the repository root:

```sh
python 01_AGENTS_AND_SKILLS/tools/learningos.py atlas
```

Open the local HTML path printed by the command. The report has embedded assets and needs no server. Reload an already-open browser tab after rebuilding it; the Atlas is a snapshot of recorded evidence.

On Windows, optional shortcuts are available: double-click `Install LearningOS shortcuts.vbs` inside the tools folder. **LearningOS** opens the Atlas; **LearningOS Study Menu** opens a numbered menu. You can also run `LearningOS.cmd` directly from that folder. Return to your tutor chat for lessons.

## What lives where

```text
LearningOS/
├── AGENTS.md                 Agent entrypoint
├── README.md                 Start here
├── LICENSE                   MIT license
├── CHANGELOG.md
├── learningos.config.json    Workspace marker
├── 01_AGENTS_AND_SKILLS/     Rules, skills, templates, docs and toolkit
├── 02_KNOWLEDGE/             Your material and derived source indexes
└── 03_MEMORY/                Your evidence, learning state and handovers
```

Initialisation creates the private workspace structure. Ordinary tutoring reads system files, preserves original source documents, and writes learner state through the toolkit.

## Privacy and sharing

The study toolkit makes no network calls. **Local files do not mean local inference:** if you choose a hosted AI agent, content it reads into the conversation is processed by that provider. Choose your agent and material accordingly.

The repository's `.gitignore` excludes learner content in `02_KNOWLEDGE/` and `03_MEMORY/`, keeping only public directory guides. This includes subjects, inbox documents, profiles, event logs, backups, checkpoints and generated reports. Neutral templates remain under the system directory.

Ignore rules do not remove files already tracked by Git and do not prevent forced additions. Review `git status` and your staged diff before publishing. Share a link to this repository for a new learner, rather than a ZIP of your used workspace.

## If something goes wrong

| Problem | Next action |
|---|---|
| Python is missing or too old | Install Python 3.11+ and reopen the agent or terminal. |
| Fresh download has no learner files | Ask the tutor to run `init-workspace`, then `validate`. |
| Tutor seems to ignore saved progress | Ask it to read `AGENTS.md`, then use `/continue`. |
| Session was interrupted | Use `/continue`; matching pending saves can be completed. |
| No save receipt | Ask `/save`; do not assume every chat message was recorded. |
| Atlas looks out of date | Ask `/atlas` and reload the report's browser tab. |
| A file is corrupt or inconsistent | Keep it unchanged. Run `validate` and follow the reported remedy with your tutor. |

For a local backup, run `python 01_AGENTS_AND_SKILLS/tools/learningos.py backup`. Recovery details are in the [command reference](01_AGENTS_AND_SKILLS/tools/README.md).

## Contribute

Report reproducible problems or suggest improvements through [GitHub issues](https://github.com/SolvyrEryx/LearningOS/issues). Include the Python version, the command and a minimal **synthetic** example. Keep personal learner files and study documents out of issues and pull requests.

To run the test suite, install `pytest` in your development environment if needed, then:

```sh
cd 01_AGENTS_AND_SKILLS/tools
python -m pytest -q
```

Tests use temporary workspaces and synthetic fixtures. To rebuild the PDF, see the [guide build notes](01_AGENTS_AND_SKILLS/docs/PUBLIC_RELEASE.md).

LearningOS is released under the [MIT License](LICENSE). Bundled Atlas fonts retain their own [Instrument Sans](01_AGENTS_AND_SKILLS/templates/atlas/fonts/InstrumentSans-OFL.txt) and [Newsreader](01_AGENTS_AND_SKILLS/templates/atlas/fonts/Newsreader-OFL.txt) license notices. Your study material remains subject to its original rights.
