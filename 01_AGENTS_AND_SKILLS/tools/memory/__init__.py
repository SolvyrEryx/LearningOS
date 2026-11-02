"""The LearningOS memory engine.

Four layers, kept deliberately separate (see `01_AGENTS_AND_SKILLS/core/memory_protocol.md`):

* **evidence** — ``03_MEMORY/events/session_events.jsonl``, append-only, never rewritten
  (:mod:`memory.events`);
* **current state** — the per-subject and global JSON snapshots, atomically overwritten
  (:mod:`memory.store`);
* **derived** — graphs, reports and the optional search cache, always regenerable;
* **checkpoints** — one resume point per trigger (:func:`memory.store.write_checkpoint`).

Only the event log is irreplaceable, which is why nothing in this package ever rewrites it.
"""

from __future__ import annotations

__all__ = [
    "backup",
    "events",
    "mastery",
    "reconcile",
    "report",
    "search",
    "store",
]
