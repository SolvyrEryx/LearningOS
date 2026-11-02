"""Indexing a subject's source documents.

What this module guarantees, in the order the guarantees matter:

* **Nothing under ``source_materials/`` is written, renamed or removed.** It is walked, hashed and
  read; that is all. The inbox is only ever *copied* from (``source_handling.md`` §7), so a
  mis-assignment can never lose the only copy of a file.
* **``source_id`` is derived from the path, not the content**:
  ``"<filename-slug>-<sha8 of the workspace-relative path>"``. A corrected chapter therefore keeps
  its id, and a citation written last month still resolves. The content hash lives in ``sha256``
  and is what change detection compares.
* **Re-extraction only when something actually changed** — sha256, byte size or mtime differs, the
  derived text file has gone missing, or ``--force``. So re-running the command is cheap and the
  mtimes under ``extracted_text/`` keep meaning "this is when the text was last derived".
* **A source that could not be read is still indexed**, with its hash, size, page count and
  warnings, and with ``extracted_text_path: null``. Missing text is never recorded as no content.

Everything written here is derived: deleting ``extracted_text/`` and ``source_index.json`` and
re-running ``index --force`` reproduces both.
"""

from __future__ import annotations

import datetime as _dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from common import (
    SCHEMA_VERSION,
    LearningOSUsageError,
    Workspace,
    atomic_write_text,
    format_timestamp,
    sha256_file,
    sha256_text,
    slugify,
    utc_now_iso,
)
from ingestion.extract import Extraction, classify, extract_text, render_extracted_text
from memory import events as events_mod
from memory import store

#: Never treated as a source document.
IGNORED_NAMES = (".gitkeep", ".gitignore", "Thumbs.db", "desktop.ini", ".DS_Store")

#: Ignored in the inbox as well as the above: the inbox's own documentation.
INBOX_IGNORED_NAMES = IGNORED_NAMES + ("README.md",)


def source_materials_dir(ws: Workspace, subject_id: str) -> Path:
    return ws.knowledge_subject(subject_id) / "source_materials"


def extracted_text_dir(ws: Workspace, subject_id: str) -> Path:
    return ws.knowledge_subject(subject_id) / "extracted_text"


def iter_source_files(directory: Path, *, ignored: Sequence[str] = IGNORED_NAMES) -> list[Path]:
    """Every file under ``directory``, recursively, sorted and tolerant of odd names.

    Sorted by the workspace-relative POSIX path so the index order — and therefore the file on
    disk — does not depend on the filesystem's enumeration order.
    """
    if not directory.is_dir():
        return []
    found = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name not in ignored and not path.name.startswith(".")
    ]
    return sorted(found, key=lambda path: path.relative_to(directory).as_posix())


def source_id_for(ws: Workspace, path: Path) -> str:
    """``<filename-slug>-<sha8 of the workspace-relative path>``.

    Path-derived on purpose: see the module docstring. Two different files can share a slug (two
    ``notes.pdf`` in different folders), and the hash of the full relative path separates them.
    """
    relative = ws.rel(path)
    return f"{slugify(Path(relative).stem, max_length=48)}-{sha256_text(relative)[:8]}"


def mtime_iso(path: Path) -> str:
    stamp = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc)
    return format_timestamp(stamp)


@dataclass
class ProcessedSource:
    """One source as this run saw it.

    ``action`` is ``extracted`` (text derived now), ``skipped`` (unchanged since last time) or
    ``would-extract`` (``--dry-run``).
    """

    source_id: str
    relative_path: str
    action: str
    record: dict[str, Any]
    extraction: Extraction | None = None

    @property
    def kind(self) -> str:
        return str(self.record.get("kind"))

    @property
    def needs_ocr(self) -> bool:
        return bool((self.record.get("extraction") or {}).get("needs_ocr"))

    @property
    def warnings(self) -> list[str]:
        return list((self.record.get("extraction") or {}).get("warnings") or [])

    @property
    def produced_text(self) -> bool:
        return bool(self.record.get("extracted_text_path"))


@dataclass
class IndexResult:
    """What one subject's indexing run did."""

    subject_id: str
    sources: list[ProcessedSource] = field(default_factory=list)
    vanished: list[str] = field(default_factory=list)
    index: dict[str, Any] | None = None
    dry_run: bool = False
    forced: bool = False
    index_rewritten: bool = False

    @property
    def extracted(self) -> list[ProcessedSource]:
        return [item for item in self.sources if item.action in ("extracted", "would-extract")]

    @property
    def skipped(self) -> list[ProcessedSource]:
        return [item for item in self.sources if item.action == "skipped"]

    @property
    def needs_ocr(self) -> list[ProcessedSource]:
        return [item for item in self.sources if item.needs_ocr]

    @property
    def without_text(self) -> list[ProcessedSource]:
        return [item for item in self.sources if not item.produced_text]

    @property
    def warning_count(self) -> int:
        return sum(len(item.warnings) for item in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "dry_run": self.dry_run,
            "forced": self.forced,
            "index_rewritten": self.index_rewritten,
            "source_count": len(self.sources),
            "extracted": [item.source_id for item in self.extracted],
            "skipped": [item.source_id for item in self.skipped],
            "needs_ocr": [item.source_id for item in self.needs_ocr],
            "without_text": [item.source_id for item in self.without_text],
            "vanished": list(self.vanished),
            "sources": [
                {
                    "source_id": item.source_id,
                    "relative_path": item.relative_path,
                    "action": item.action,
                    "kind": item.kind,
                    "pages": item.record.get("pages"),
                    "chars": (item.record.get("extraction") or {}).get("chars"),
                    "extracted_text_path": item.record.get("extracted_text_path"),
                    "warnings": item.warnings,
                }
                for item in self.sources
            ],
        }


def new_source_index(subject_id: str, *, at: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "subject_id": subject_id,
        "updated_at": at or utc_now_iso(),
        "sources": [],
    }


def _previous_by_id(index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    previous: dict[str, dict[str, Any]] = {}
    for entry in (index or {}).get("sources") or []:
        if isinstance(entry, dict) and isinstance(entry.get("source_id"), str):
            previous[entry["source_id"]] = entry
    return previous


def needs_reextraction(
    ws: Workspace,
    previous: dict[str, Any] | None,
    *,
    sha256: str,
    size: int,
    mtime: str,
    force: bool,
) -> tuple[bool, str]:
    """``(re-extract?, why)`` — the whole change-detection rule, in one place."""
    if force:
        return True, "forced"
    if previous is None:
        return True, "new source"
    if previous.get("sha256") != sha256:
        return True, "content changed"
    if previous.get("bytes") != size:
        return True, "size changed"
    if previous.get("mtime_iso") != mtime:
        return True, "mtime changed"
    derived = previous.get("extracted_text_path")
    if isinstance(derived, str) and derived and not (ws.root / derived).is_file():
        return True, "derived text missing"
    return False, "unchanged"


def index_subject(
    ws: Workspace,
    subject_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    session_id: str | None = None,
) -> IndexResult:
    """Walk one subject's ``source_materials/``, derive what changed, write the index.

    Raises :class:`common.LearningOSNotFoundError` (exit 4) for an unknown subject and
    :class:`common.LearningOSDataError` (exit 3) when an existing ``source_index.json`` is corrupt
    — in which case nothing is written, because overwriting it would destroy the evidence.
    """
    store.require_subject(ws, subject_id)
    directory = source_materials_dir(ws, subject_id)
    existing = store.load_source_index(ws, subject_id, required=False)
    previous = _previous_by_id(existing)
    result = IndexResult(subject_id=subject_id, dry_run=dry_run, forced=force)
    records: list[dict[str, Any]] = []
    writes: list[tuple[Path, str]] = []

    for path in iter_source_files(directory):
        relative = ws.rel(path)
        source_id = source_id_for(ws, path)
        stat = path.stat()
        digest = sha256_file(path)
        stamp = mtime_iso(path)
        earlier = previous.get(source_id)
        changed, _why = needs_reextraction(
            ws,
            earlier,
            sha256=digest,
            size=stat.st_size,
            mtime=stamp,
            force=force,
        )
        if not changed and earlier is not None:
            records.append(earlier)
            result.sources.append(
                ProcessedSource(
                    source_id=source_id,
                    relative_path=relative,
                    action="skipped",
                    record=earlier,
                )
            )
            continue

        at = utc_now_iso()
        extraction = extract_text(path)
        text_path: Path | None = None
        if extraction.produced_text:
            text_path = extracted_text_dir(ws, subject_id) / f"{source_id}.txt"
            writes.append(
                (
                    text_path,
                    render_extracted_text(
                        extraction,
                        source_id=source_id,
                        source_path=relative,
                        extracted_at=at,
                    ),
                )
            )
        record = {
            "source_id": source_id,
            "relative_path": relative,
            "title": path.stem,
            "kind": extraction.kind or classify(path),
            "bytes": stat.st_size,
            "sha256": digest,
            "mtime_iso": stamp,
            "pages": extraction.pages,
            "extracted_text_path": ws.rel(text_path) if text_path else None,
            "extraction": extraction.index_block(at=at),
        }
        records.append(record)
        result.sources.append(
            ProcessedSource(
                source_id=source_id,
                relative_path=relative,
                action="would-extract" if dry_run else "extracted",
                record=record,
                extraction=extraction,
            )
        )

    seen_ids = {record["source_id"] for record in records}
    result.vanished = sorted(
        str(entry.get("relative_path"))
        for source_id, entry in previous.items()
        if source_id not in seen_ids
    )

    index = dict(existing or new_source_index(subject_id))
    index["schema_version"] = SCHEMA_VERSION
    index["subject_id"] = subject_id
    index["sources"] = sorted(records, key=lambda entry: str(entry.get("relative_path")))
    result.index = index

    if dry_run:
        return result

    # A genuine no-op leaves the file alone rather than rewriting it with a fresh `updated_at`:
    # "nothing changed" should look like nothing changed on disk too.
    unchanged = existing is not None and {
        **index,
        "updated_at": existing.get("updated_at"),
    } == existing
    if unchanged and not writes:
        result.index = existing
        return result

    for target, text in writes:
        atomic_write_text(target, text)
    store.save_source_index(ws, subject_id, index)
    result.index_rewritten = True

    session = session_id or f"{utc_now_iso()[:10]}-index"
    for item in result.sources:
        if item.action != "extracted":
            continue
        block = item.record.get("extraction") or {}
        events_mod.append_event(
            ws,
            type="source_indexed",
            session_id=session,
            subject_id=subject_id,
            actor="tool",
            payload={
                "source_id": item.source_id,
                "relative_path": item.relative_path,
                "sha256": item.record.get("sha256"),
                "bytes": item.record.get("bytes"),
                "pages": item.record.get("pages"),
                "chars": block.get("chars"),
                "extracted_text_path": item.record.get("extracted_text_path"),
                "likely_scanned": block.get("likely_scanned"),
                "needs_ocr": block.get("needs_ocr"),
                "skipped_unchanged": False,
                "warnings": list(block.get("warnings") or []),
            },
        )
    return result


def index_all(
    ws: Workspace,
    *,
    force: bool = False,
    dry_run: bool = False,
    session_id: str | None = None,
) -> list[IndexResult]:
    """Index every real subject. Template directories are scaffolding and are skipped."""
    return [
        index_subject(
            ws,
            subject_id,
            force=force,
            dry_run=dry_run,
            session_id=session_id,
        )
        for subject_id in ws.list_subjects()
    ]


# ------------------------------------------------------------------------------------- the inbox


@dataclass
class Assignment:
    """One inbox file and what happened to it. ``action`` is never ``moved``."""

    name: str
    source: str
    target: str
    action: str  # copied | would-copy | already-present

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "target": self.target,
            "action": self.action,
        }


def list_inbox(ws: Workspace) -> list[Path]:
    """Files waiting in ``02_KNOWLEDGE/inbox/``, recursively."""
    return iter_source_files(ws.inbox, ignored=INBOX_IGNORED_NAMES)


def assign_from_inbox(
    ws: Workspace,
    subject_id: str,
    *,
    names: Iterable[str] | None = None,
    dry_run: bool = False,
) -> list[Assignment]:
    """**Copy** inbox files into a subject's ``source_materials/``.

    Nothing is moved and nothing in the inbox is deleted — clearing it is the learner's decision
    (``source_handling.md`` §7). An existing file at the target is left alone rather than
    overwritten; the cost is that the learner has to remove it deliberately, and the benefit is
    that indexing can never clobber material that is already in place.
    """
    wanted = {str(name) for name in names} if names else None
    target_dir = source_materials_dir(ws, subject_id)
    assignments: list[Assignment] = []
    for path in list_inbox(ws):
        relative_name = path.relative_to(ws.inbox).as_posix()
        if wanted is not None and relative_name not in wanted and path.name not in wanted:
            continue
        destination = target_dir / relative_name
        if destination.exists():
            action = "already-present"
        elif dry_run:
            action = "would-copy"
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)  # copy, never move
            action = "copied"
        assignments.append(
            Assignment(
                name=relative_name,
                source=ws.rel(path),
                target=ws.rel(destination),
                action=action,
            )
        )
    if wanted is not None and not assignments:
        raise LearningOSUsageError(
            "none of the named files are in 02_KNOWLEDGE/inbox/",
            remedy="Run `index --inbox` to list what is actually waiting there.",
        )
    return assignments
