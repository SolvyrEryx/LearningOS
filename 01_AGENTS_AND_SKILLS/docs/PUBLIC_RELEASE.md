# Public distribution and guide build

The public repository distributes the reusable system, neutral templates and directory guides.
Start a release from a clean clone, then run `init-workspace --workspace <clean clone>` from the
installation being released. This copies shipped system files and creates fresh neutral learner
state without copying real subjects, profiles, sources or events. Preserve Git history and licenses.

The root `.gitignore` excludes live content under `02_KNOWLEDGE` and `03_MEMORY` except five
directory README files. Review staged paths before every push. Ignore rules cannot protect
already-tracked files or forced additions. Do not publish a used workspace as an archive.

## A fresh installation

After downloading or cloning, run from the repository root:

```sh
python 01_AGENTS_AND_SKILLS/tools/learningos.py init-workspace
python 01_AGENTS_AND_SKILLS/tools/learningos.py validate
```

The tutor can do this with the first-session prompt in the root README. Initialisation is
idempotent and does not reset existing learner files. Local learner files are absent from Git.

## Build the getting-started PDF

The source is `build_getting_started_pdf.py` beside this file. This documentation-only helper
uses optional `reportlab`. ReportLab is not required to study, initialise a workspace, use the CLI
or read the finished PDF. The helper installs nothing. If needed, install it explicitly in a
documentation build environment:

```sh
python -m pip install reportlab
python 01_AGENTS_AND_SKILLS/docs/build_getting_started_pdf.py
```

The output is `LearningOS_Getting_Started.pdf` beside the script. The builder reads no learner
files. It uses ReportLab's built-in fonts and draws the layout locally. Render and visually
inspect every page after edits; text extraction alone does not prove that layout is correct.

## Release checks

1. Review staged paths: only the system, root public files and five directory guides belong.
2. Scan staged text for credentials, private content and machine-specific paths.
3. In a fresh clone, run initialisation, validation and tests. Use synthetic smoke-test data.
4. Verify learner files are ignored and fresh initialisation has no subjects or events. Check
   PDF pages and README links.
5. Push a normal commit to the intended branch. Preserve existing history.

The MIT license covers LearningOS code and documentation. Third-party font notices stay beside
their assets. Uploaded sources and learner-created material are not relicensed by this repo.
