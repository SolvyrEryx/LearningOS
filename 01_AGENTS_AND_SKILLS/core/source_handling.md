# Source handling

How LearningOS treats the learner's documents: syllabi, textbooks, lecture notes, past papers,
slide decks, photographed pages. The governing idea is that the learner's files are evidence, and
evidence is never altered.

## 1. Originals are immutable

Files under `02_KNOWLEDGE/subjects/` (in each subject's `source_materials/`) and under
`02_KNOWLEDGE/shared_reference/` are opened read-only and never modified, renamed, re-encoded,
compressed, split, merged or deleted — not by the agent, not by the tooling, not as a "cleanup".

If a file genuinely needs to change (a corrected chapter, a better scan), the learner replaces it
themselves. Re-indexing then notices the new hash and re-extracts. The `source_id` is derived from
the workspace-relative path, not the content, so provenance survives the edit: citations written
last month still resolve.

## 2. Derived text lives in `extracted_text/`

Extraction writes one plain-text file per source to the subject's `extracted_text/` directory, named
`<source_id>.txt`. Every extracted file begins with a provenance header and carries page markers:

```
# source_id: thermo-unit3-notes-7f3a9c1d
# source_path: 02_KNOWLEDGE/subjects/<subject_id>/source_materials/thermo-unit3-notes.pdf
# extracted_at: 2026-02-11T09:02:00Z
# tool: pypdf
# tool_version: 6.18.1

--- page 1 ---
...text...

--- page 2 ---
...text...
```

For `.txt` and `.md` sources the locator is the line number rather than the page. Everything in
`extracted_text/` is derived and disposable: deleting the directory and re-running indexing must
reproduce it. Never hand-edit extracted text — fix the original, or record the correction as a note
in memory.

## 3. The source index

`02_KNOWLEDGE/subjects/<subject_id>/source_index.json` holds one record per source:
`source_id`, workspace-relative `relative_path`, `title`, `kind`, `bytes`, `sha256`, `mtime_iso`,
`pages`, `extracted_text_path`, and an `extraction` block with the tool, version, timestamp,
character counts, `likely_scanned`, `needs_ocr` and any `warnings`.

`source_id = "<filename-slug>-<sha8 of relative_path>"`. Re-extraction happens only when the
sha256, byte size or mtime changed, when the extracted file is missing, or when `--force` is passed.
That makes indexing cheap to re-run and keeps extracted-file timestamps meaningful.

## 4. Citation format

Any claim taken from the learner's material is cited inline as `[source_id p.N]`:

> The entropy of mixing for ideal gases depends only on the mole fractions
> `[thermo-unit3-notes-7f3a9c1d p.14]`.

- Pages for PDFs, `l.N` for line-addressed text files, `p.N–M` for a range.
- Cite the specific page, never the whole document.
- When you are reasoning from general knowledge rather than from a source in this workspace, say so
  explicitly in the same sentence. "From general background, not from your notes: …"
- When your statement contradicts a source, surface the conflict rather than silently preferring
  one. The learner's exam is usually graded against their syllabus, not against you.
- A concept record links its sources through `source_refs`, for example
  `{"source_id": "thermo-unit3-notes-7f3a9c1d", "locator": {"page": 14}}`, so revision can jump
  straight back to the passage.

## 5. Scanned PDFs and OCR

Many real study materials are photographs of paper. Text extraction returns almost nothing for
them, and the failure is easy to mistake for an empty document.

- Median characters per page below 50 sets `likely_scanned: true`.
- Sixty per cent or more of pages below that threshold sets `needs_ocr: true`.
- A very short extraction adds a `suspiciously_short` warning.

**Missing text is never "no content".** A source that could not be extracted is recorded as an
*un-extracted source needing OCR*, with its hash, size and page count intact. The agent must then:

1. Tell the learner which file and which pages could not be read.
2. Ask them to paste the relevant passage, describe it, or run OCR externally and drop the resulting
   text file into the inbox as a companion source.
3. Never infer the contents of a page it could not read, and never claim a topic is absent from the
   material because extraction failed.

OCR is not performed by LearningOS. It is a documented manual step, deliberately, because bundling
an OCR engine would mean a heavyweight non-standard dependency or a network service.

## 6. Unsupported formats

`.docx`, `.pptx`, `.epub`, images and archives are recorded in the index with `kind: "other"` and a
`needs_manual_extraction` warning. They are known sources with hashes; they simply have no extracted
text. The remedy is to export to PDF, Markdown or plain text and add that export as a source.

## 7. The inbox

`02_KNOWLEDGE/inbox/` is the drop zone. Assignment **copies** into the target subject's
`source_materials/`; nothing is ever moved or deleted from the inbox by LearningOS. The cost is a
duplicated file; the benefit is that a mis-assignment can never lose the only copy. Clearing the
inbox is the learner's decision.

Shared material — tables, standards, formula sheets — belongs in `02_KNOWLEDGE/shared_reference/`
and is cited from several subjects by the same `source_id`. Sources may be shared. Mastery may not.

## 8. Privacy

Indexing, extraction and citation are entirely local: no upload, no lookup, no telemetry. But when
you quote a passage into the conversation, a hosted model sees it. So quote the passage you need,
not the chapter, and tell the learner plainly if they ask. The distinction between local files and
hosted inference is stated in full in `01_AGENTS_AND_SKILLS/core/system_contract.md`.

## 9. Checklist before teaching from a source

1. Is it indexed? If not, run indexing — an unindexed source has no stable id to cite.
2. Does the index show warnings for it (`needs_ocr`, `suspiciously_short`, `needs_manual_extraction`)?
   If yes, say so before relying on it.
3. Does the concept you are about to teach have `source_refs`? If not, add them as you teach, so the
   next session resumes at the right page.
4. Is the syllabus mapped in `syllabus_map.md`? Coverage claims are made against the syllabus, not
   against the textbook's table of contents.
