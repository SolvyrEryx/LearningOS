"""Searching the learner's own history.

`search-memory` answers questions like "have we met this before?" over the three places the answer
can live: the event log, the concept records, and the misconception records. It is deliberately
boring technology — a case-insensitive scan with an optional SQLite cache:

* **no embeddings, no vector store, no network.** Nothing in this module leaves the machine, and
  the search works identically on a laptop with no internet and no third-party packages.
* **one matching predicate.** :func:`matches` is the only place that decides whether a document
  hits, so the cached path and the scanning path cannot drift apart and return different answers.
* **the cache is a cache.** `--reindex` writes an FTS5 table at
  ``03_MEMORY/global/search_index.sqlite3``; it is used only when it is present and newer than the
  data it indexes, and it is silently bypassed when SQLite was built without FTS5. Deleting it
  loses nothing.

The log is read as a stream, so a long history does not have to fit in memory, and a corrupt line
is skipped rather than ending the search.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from common import (
    Finding,
    Workspace,
    parse_date,
    truncate,
)
from memory import events as events_mod
from memory import store

#: Where the optional cache lives. Listed in .gitignore: it is derived, never authoritative.
INDEX_FILENAME = "search_index.sqlite3"

#: Payload keys worth showing as a snippet, in preference order.
SNIPPET_KEYS = (
    "notes",
    "question",
    "description",
    "learner_statement",
    "reason",
    "mistake_pattern",
    "plan",
    "note",
    "outcome",
)

KINDS = ("event", "concept", "misconception")


def index_path(ws: Workspace) -> Path:
    return ws.memory_global / INDEX_FILENAME


# ------------------------------------------------------------------------------------ documents


@dataclass(frozen=True)
class SearchHit:
    """One match, in the order the human report prints it."""

    at: str | None
    subject_id: str | None
    kind: str
    type: str
    concept_ids: tuple[str, ...]
    snippet: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "subject_id": self.subject_id,
            "kind": self.kind,
            "type": self.type,
            "concept_ids": list(self.concept_ids),
            "snippet": self.snippet,
            "path": self.path,
        }

    def format_human(self) -> str:
        concepts = ",".join(self.concept_ids) or "—"
        return (
            f"{self.at or '—':<21} {self.subject_id or '—':<10} {self.type:<22} "
            f"{concepts:<34} {self.snippet}"
        )


@dataclass(frozen=True)
class _Document:
    """A searchable unit: the text to match against plus the metadata to print."""

    at: str | None
    subject_id: str | None
    kind: str
    type: str
    concept_ids: tuple[str, ...]
    snippet: str
    path: str
    text: str

    def to_hit(self) -> SearchHit:
        return SearchHit(
            at=self.at,
            subject_id=self.subject_id,
            kind=self.kind,
            type=self.type,
            concept_ids=self.concept_ids,
            snippet=self.snippet,
            path=self.path,
        )


def _strings(node: Any) -> Iterator[str]:
    """Every string *value* inside a payload.

    Field names are deliberately left out: if keys were searchable, every event carrying a
    ``notes`` field would match the query ``notes``, which is noise rather than recall.
    """
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _snippet_for(payload: dict[str, Any], fallback: str) -> str:
    for key in SNIPPET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return truncate(value, 110)
    return truncate(fallback, 110)


def event_documents(ws: Workspace, *, findings: list[Finding] | None = None) -> Iterator[_Document]:
    """Stream one document per readable event line."""
    log_relative = ws.rel(ws.event_log)
    for line_no, event in events_mod.iter_events(ws, findings=findings):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        concept_ids = tuple(
            str(cid) for cid in event.get("concept_ids") or [] if isinstance(cid, str)
        )
        parts = [
            str(event.get("type") or ""),
            str(event.get("session_id") or ""),
            " ".join(concept_ids),
            " ".join(_strings(payload)),
        ]
        text = " ".join(part for part in parts if part)
        yield _Document(
            at=str(event.get("at")) if event.get("at") else None,
            subject_id=event.get("subject_id") if isinstance(event.get("subject_id"), str) else None,
            kind="event",
            type=str(event.get("type") or "event"),
            concept_ids=concept_ids,
            snippet=_snippet_for(payload, text),
            path=f"{log_relative}:{line_no}",
            text=text,
        )


def concept_documents(ws: Workspace, subject_ids: Iterable[str] | None = None) -> Iterator[_Document]:
    """One document per concept record: name, mistake patterns, next action, interventions."""
    for subject_id in subject_ids if subject_ids is not None else ws.list_subjects():
        mastery = store.load_concept_mastery(ws, subject_id, required=False)
        if not mastery:
            continue
        path = ws.rel(store.concept_mastery_path(ws, subject_id))
        for concept_id, record in sorted((mastery.get("concepts") or {}).items()):
            if not isinstance(record, dict):
                continue
            mistakes = [
                str(mistake.get("pattern"))
                for mistake in record.get("recurring_mistakes") or []
                if isinstance(mistake, dict) and mistake.get("pattern")
            ]
            parts = [
                str(concept_id),
                str(record.get("name") or ""),
                str(record.get("unit") or ""),
                str(record.get("status") or ""),
                str(record.get("next_action") or ""),
                " ".join(mistakes),
                " ".join(str(item) for item in record.get("helpful_interventions") or []),
                " ".join(str(item) for item in record.get("failed_interventions") or []),
                " ".join(str(item) for item in record.get("misconception_ids") or []),
            ]
            text = " ".join(part for part in parts if part)
            snippet = (
                str(record.get("next_action"))
                if record.get("next_action")
                else (mistakes[0] if mistakes else str(record.get("name") or concept_id))
            )
            yield _Document(
                at=str(record.get("updated_at")) if record.get("updated_at") else None,
                subject_id=subject_id,
                kind="concept",
                type="concept_record",
                concept_ids=(str(concept_id),),
                snippet=truncate(snippet, 110),
                path=path,
                text=text,
            )


def misconception_documents(
    ws: Workspace, subject_ids: Iterable[str] | None = None
) -> Iterator[_Document]:
    """One document per misconception record, including its detection signals and remediation."""
    for subject_id in subject_ids if subject_ids is not None else ws.list_subjects():
        data = store.load_misconceptions(ws, subject_id, required=False)
        if not data:
            continue
        path = ws.rel(store.misconceptions_path(ws, subject_id))
        for entry in data.get("misconceptions") or []:
            if not isinstance(entry, dict):
                continue
            parts = [
                str(entry.get("misconception_id") or ""),
                str(entry.get("description") or ""),
                str(entry.get("learner_statement_example") or ""),
                str(entry.get("correct_statement") or ""),
                " ".join(str(item) for item in entry.get("detection_signals") or []),
                " ".join(str(item) for item in entry.get("remediation") or []),
                " ".join(str(item) for item in entry.get("concept_ids") or []),
            ]
            text = " ".join(part for part in parts if part)
            yield _Document(
                at=str(entry.get("last_seen_at") or entry.get("first_seen_at") or "") or None,
                subject_id=subject_id,
                kind="misconception",
                type=str(entry.get("misconception_id") or "misconception"),
                concept_ids=tuple(
                    str(cid) for cid in entry.get("concept_ids") or [] if isinstance(cid, str)
                ),
                snippet=truncate(str(entry.get("description") or ""), 110),
                path=path,
                text=text,
            )


def all_documents(ws: Workspace, *, findings: list[Finding] | None = None) -> list[_Document]:
    """Every searchable document in the workspace, evidence first."""
    documents = list(event_documents(ws, findings=findings))
    documents.extend(concept_documents(ws))
    documents.extend(misconception_documents(ws))
    return documents


# ------------------------------------------------------------------------------------- matching


def matches(text: str, query: str) -> bool:
    """The one matching rule: case-insensitive substring, or every token present.

    ``"a0 term"`` matches a line containing that exact phrase, and also one containing ``a0``
    somewhere and ``term`` somewhere else. Both paths (scan and cache) call this, which is what
    guarantees they return the same results.
    """
    haystack = text.lower()
    needle = query.strip().lower()
    if not needle:
        return False
    if needle in haystack:
        return True
    tokens = [token for token in needle.split() if token]
    return len(tokens) > 1 and all(token in haystack for token in tokens)


def _since_timestamp(since: str | None) -> str | None:
    if not since:
        return None
    parse_date(since, field_name="--since")  # exit 2 on anything that is not YYYY-MM-DD
    return f"{since}T00:00:00Z"


def _sort_key(document: _Document) -> tuple[int, str, str]:
    # Used with reverse=True: timestamped documents come first, newest first, then by path.
    return (1 if document.at else 0, document.at or "", document.path)


# ---------------------------------------------------------------------------- the FTS5 cache


def fts5_available() -> bool:
    """Whether this Python's SQLite can create an FTS5 table. Absence is never an error."""
    connection = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def _indexed_files(ws: Workspace) -> list[Path]:
    """Every file whose content the cache mirrors."""
    paths = [ws.event_log]
    for subject_id in ws.list_subjects():
        paths.append(store.concept_mastery_path(ws, subject_id))
        paths.append(store.misconceptions_path(ws, subject_id))
    return [path for path in paths if path.is_file()]


def index_is_fresh(ws: Workspace) -> bool:
    """True when the cache exists and is newer than everything it mirrors."""
    cache = index_path(ws)
    if not cache.is_file():
        return False
    cache_mtime = cache.stat().st_mtime
    return all(path.stat().st_mtime <= cache_mtime for path in _indexed_files(ws))


def build_index(ws: Workspace) -> tuple[bool, str]:
    """Rebuild the FTS5 cache. Returns ``(built, message)`` and never raises on absence."""
    if not fts5_available():
        return False, (
            "search index skipped: this Python's SQLite has no FTS5 module. "
            "Searching falls back to the streaming scan, which returns identical results."
        )
    cache = index_path(ws)
    documents = all_documents(ws)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".sqlite3.tmp")
    temporary.unlink(missing_ok=True)
    try:
        # The connection is closed explicitly, not with `with`: sqlite3's context manager commits
        # the transaction but leaves the handle open, and on Windows an open handle makes the
        # os.replace below fail.
        connection = sqlite3.connect(temporary)
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE documents USING fts5("
                "at, subject_id, kind, type, concept_ids, snippet, path, text)"
            )
            connection.executemany(
                "INSERT INTO documents (at, subject_id, kind, type, concept_ids, snippet, path, "
                "text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        document.at or "",
                        document.subject_id or "",
                        document.kind,
                        document.type,
                        " ".join(document.concept_ids),
                        document.snippet,
                        document.path,
                        document.text,
                    )
                    for document in documents
                ],
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, cache)
    finally:
        temporary.unlink(missing_ok=True)
    return True, f"search index rebuilt: {len(documents)} document(s) in {ws.rel(cache)}"


def _documents_from_index(ws: Workspace) -> list[_Document] | None:
    """Read the cached documents, or ``None`` when the cache cannot be used."""
    cache = index_path(ws)
    if not cache.is_file():
        return None
    connection = None
    try:
        connection = sqlite3.connect(f"file:{cache.as_posix()}?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT at, subject_id, kind, type, concept_ids, snippet, path, text FROM documents"
        ).fetchall()
    except Exception:
        # A cache that cannot be read is not an error: it is a cache. Fall back to the scan.
        return None
    finally:
        if connection is not None:
            connection.close()
    return [
        _Document(
            at=row[0] or None,
            subject_id=row[1] or None,
            kind=row[2],
            type=row[3],
            concept_ids=tuple(part for part in (row[4] or "").split() if part),
            snippet=row[5],
            path=row[6],
            text=row[7],
        )
        for row in rows
    ]


# --------------------------------------------------------------------------------------- search


@dataclass
class SearchResult:
    """Hits plus how they were found, so the report can be honest about the engine used."""

    hits: list[SearchHit]
    engine: str
    scanned: int
    findings: list[Finding]


def search(
    ws: Workspace,
    query: str,
    *,
    subject: str | None = None,
    event_type: str | None = None,
    since: str | None = None,
    limit: int = 50,
    use_index: bool = True,
) -> SearchResult:
    """Search the evidence log, the concept records and the misconceptions.

    ``event_type`` filters the event log only — concept and misconception records have no event
    type, so asking for one excludes them rather than silently ignoring the filter. ``since`` is a
    calendar date and is compared against an event's ``at``, a record's ``updated_at`` and a
    misconception's ``last_seen_at``.
    """
    since_at = _since_timestamp(since)
    findings: list[Finding] = []
    documents: list[_Document] | None = None
    engine = "scan"
    if use_index and index_is_fresh(ws):
        documents = _documents_from_index(ws)
        if documents is not None:
            engine = "index"
    if documents is None:
        documents = all_documents(ws, findings=findings)

    hits: list[SearchHit] = []
    scanned = 0
    for document in sorted(documents, key=_sort_key, reverse=True):
        scanned += 1
        if subject is not None and document.subject_id != subject:
            continue
        if event_type is not None and (document.kind != "event" or document.type != event_type):
            continue
        if since_at is not None and (document.at or "") < since_at:
            continue
        if not matches(document.text, query):
            continue
        hits.append(document.to_hit())
        if limit and len(hits) >= limit:
            break
    return SearchResult(hits=hits, engine=engine, scanned=scanned, findings=findings)
