"""Turning the learner's documents into citable, derived text.

Two modules, one rule: the originals under ``02_KNOWLEDGE/subjects/<id>/source_materials/`` are
opened read-only and never altered.

* :mod:`ingestion.extract` — one file in, plain text plus provenance and honesty flags out.
* :mod:`ingestion.indexer` — walks a subject's sources, re-extracts only what changed, and writes
  ``source_index.json`` plus ``extracted_text/<source_id>.txt``.

Both are described for the agent in ``01_AGENTS_AND_SKILLS/core/source_handling.md``.
"""

from __future__ import annotations

__all__ = ["extract", "indexer"]
