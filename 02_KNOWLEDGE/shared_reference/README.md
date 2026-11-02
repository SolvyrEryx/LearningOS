# shared_reference — material used by more than one subject

Put material here when it genuinely serves several subjects: a mathematical tables booklet, a style
guide, a unit-conversion sheet, a standards document, a formula reference.

## Why it is separate

Subject directories are isolated on purpose — one subject's mastery data must never leak into
another's. Sources are different: the same integral table can legitimately back a concept in
`math2` and a concept in `thermo`. Rather than duplicating the file, keep one copy here and cite it
from both subjects.

## How to reference it

A concept record's `source_refs` entry may point at a shared source. The `source_id` is computed
from the workspace-relative path, so a shared file has one id everywhere it is cited:

```json
{"source_id": "integral-tables-7f3a9c1d", "locator": {"page": 4}}
```

## Rules

- Originals here are immutable, exactly as in a subject's `source_materials/`.
- Extracted text for a shared source is written next to it in `extracted_text/` inside this
  directory when a subject indexes it; it is derived and disposable.
- Do not put learner state, mastery data, session notes or answers here. Those belong in
  `03_MEMORY/`.
- Do not put subject-specific material here "just in case". If only one subject uses it, it belongs
  in that subject.
