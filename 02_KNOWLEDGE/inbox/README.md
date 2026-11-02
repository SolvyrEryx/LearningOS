# inbox — un-triaged documents

Drop anything here when you do not yet want to decide which subject it belongs to: a lecture
recording transcript, a photographed page, a past paper, a PDF chapter, a set of notes.

## How it is used

1. You copy files into this directory yourself (File Explorer, `Copy-Item`, a download).
2. The agent lists what is here and proposes an assignment per file.
3. On your confirmation the file is **copied** into
   `02_KNOWLEDGE/subjects/<subject_id>/source_materials/` and indexed there.

Nothing is ever moved or deleted from this directory by LearningOS. If a file ends up in the wrong
subject, the original is still here, and the wrong copy can be removed by hand. Clearing the inbox
is your decision, not the agent's.

## What to name things

Give files descriptive names before dropping them in — the name becomes part of the stable
`source_id` used in every citation. `thermo-unit3-lecture-notes.pdf` is a good name;
`scan_0012.pdf` is not.

## Supported kinds

| Kind | Handling |
|---|---|
| `.txt`, `.md` | read directly as UTF-8; locator is the line number |
| `.pdf` | text extracted per page when the optional `pypdf` package is installed; locator is the page |
| image-only / scanned `.pdf` | recorded as needing OCR, never recorded as empty |
| anything else | recorded with `kind: "other"` and a `needs_manual_extraction` warning |

A source that cannot be extracted is still a known source. It appears in `source_index.json` with
its hash and a warning, and the agent will ask you to paste or describe the relevant passage rather
than pretending the material does not exist.
