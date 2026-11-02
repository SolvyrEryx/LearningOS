"""The append-only evidence log: ``03_MEMORY/events/session_events.jsonl``.

One complete JSON object per line, UTF-8, LF, never pretty-printed, every line independently
parseable and schema-valid. The log is the only layer of LearningOS memory that cannot be
rebuilt from anything else, which is why:

* writes are appends, flushed and ``os.fsync``ed, and are validated *before* the append;
* the file is never rewritten — not to compact it, not to reorder it, and not to fix a bad line;
* a corrupt line is reported with its line number by `validate`, skipped by readers, and left
  exactly where it is.

Correcting history is done by appending a correcting event, never by editing a line.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from common import (
    SCHEMA_VERSION,
    TOOL_ID,
    Finding,
    LearningOSDataError,
    LearningOSUsageError,
    Workspace,
    append_jsonl_line,
    parse_timestamp_safe,
    truncate,
    utc_now_iso,
)
from memory.store import registry

#: Every event type the system may append. Mirrors `session_event.schema.json`.
EVENT_TYPES: tuple[str, ...] = (
    "learning_goal_set", "learning_cycle_saved", "learning_cycle_applied", "learning_note_saved",
    "session_close_requested", "session_close_completed",
    "exam_configured", "question_registered", "practice_started", "practice_attempted",
    "practice_paused", "practice_resumed", "practice_ended", "practice_error_classified", "study_plan_created",
    "session_started",
    "session_ended",
    "checkpoint_written",
    "subject_initialized",
    "diagnostic_probe",
    "explanation_given",
    "question_asked",
    "answer_evaluated",
    "hint_given",
    "misconception_detected",
    "misconception_resolved",
    "mastery_updated",
    "retention_check",
    "revision_scheduled",
    "source_indexed",
    "graph_generated",
    "note_saved",
    "open_question_logged",
    "memory_backup",
    "memory_restore",
)

ACTORS = ("learner", "agent", "tool")


def new_event_id() -> str:
    """A uuid4 hex string — 32 lowercase hex characters, no dashes."""
    return uuid.uuid4().hex


def build_event(
    *,
    type: str,
    session_id: str,
    subject_id: str | None = None,
    concept_ids: Sequence[str] | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "agent",
    at: str | None = None,
    event_id: str | None = None,
    tool_version: str | None = TOOL_ID,
) -> dict[str, Any]:
    """Assemble an event object without writing it.

    Key order is fixed so the log reads consistently when a human opens it.
    """
    if type not in EVENT_TYPES:
        raise LearningOSUsageError(
            f"unknown event type {type!r}",
            remedy="Valid types: " + ", ".join(EVENT_TYPES),
        )
    if actor not in ACTORS:
        raise LearningOSUsageError(
            f"unknown actor {actor!r}",
            remedy="Valid actors: " + ", ".join(ACTORS),
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or new_event_id(),
        "at": at or utc_now_iso(),
        "session_id": session_id,
        "subject_id": subject_id,
        "type": type,
        "actor": actor,
        "tool_version": tool_version,
        "concept_ids": list(concept_ids or []),
        "payload": dict(payload or {}),
    }


def validate_event(ws: Workspace, event: dict[str, Any], *, strict: bool = False) -> list[Finding]:
    """Schema-validate one event, reporting which ``oneOf`` branch was closest when none match."""
    return registry(ws).validate(
        "session_event",
        event,
        path=ws.rel(ws.event_log),
        strict=strict,
    )


def append_event(
    ws: Workspace,
    *,
    type: str,
    session_id: str,
    subject_id: str | None = None,
    concept_ids: Sequence[str] | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "agent",
    at: str | None = None,
    event_id: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Validate an event and append it to the log. Returns the event as written.

    Evidence is written before the snapshot it justifies: if the process dies between the two,
    the event log still holds the truth and `reconcile` can rebuild the snapshot. The reverse
    order loses evidence permanently.
    """
    event = build_event(
        type=type,
        session_id=session_id,
        subject_id=subject_id,
        concept_ids=concept_ids,
        payload=payload,
        actor=actor,
        at=at,
        event_id=event_id,
    )
    findings = validate_event(ws, event, strict=strict)
    if findings:
        first = findings[0]
        extra = "" if len(findings) == 1 else f" (and {len(findings) - 1} more)"
        raise LearningOSDataError(
            f"refusing to append an invalid {type} event: {first.message}{extra}",
            path=ws.rel(ws.event_log),
            pointer=first.pointer,
            remedy="Fix the payload so it satisfies the matching oneOf branch in "
            "01_AGENTS_AND_SKILLS/schemas/session_event.schema.json. Nothing was written.",
            code="schema",
        )
    append_jsonl_line(ws.event_log, event)
    return event


def iter_events(
    ws: Workspace,
    *,
    path: Path | None = None,
    findings: list[Finding] | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Stream ``(line_number, event)`` pairs from the log, 1-based.

    Unparseable lines are collected into ``findings`` (when a list is supplied) and skipped — a
    reader never raises because of one bad line, and never rewrites the file to "fix" it.
    """
    log_path = path or ws.event_log
    if not log_path.exists():
        return
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                if findings is not None:
                    findings.append(
                        Finding(
                            severity="error",
                            code="event_parse",
                            message=f"line {line_no} is not valid JSON: {exc.msg} "
                            f"(column {exc.colno}); content starts: {truncate(line, 60)}",
                            path=f"{ws.rel(log_path)}:{line_no}",
                            remedy="The log is append-only and was left untouched. Append a "
                            "correcting event instead of editing history; if the line is "
                            "truncated debris from a crash, remove it by hand only after "
                            "taking a backup.",
                        )
                    )
                continue
            if not isinstance(event, dict):
                if findings is not None:
                    findings.append(
                        Finding(
                            severity="error",
                            code="event_shape",
                            message=f"line {line_no} is a JSON {type(event).__name__}, "
                            "not an event object",
                            path=f"{ws.rel(log_path)}:{line_no}",
                            remedy="Each line must be one complete JSON object.",
                        )
                    )
                continue
            yield line_no, event


def read_events(
    ws: Workspace,
    *,
    subject_id: str | None = None,
    types: Iterable[str] | None = None,
    since: str | None = None,
    concept_id: str | None = None,
    session_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """Read the log into memory with optional filters. Returns ``(events, findings)``."""
    wanted = set(types) if types else None
    since_dt = parse_timestamp_safe(since) if since else None
    findings: list[Finding] = []
    events: list[dict[str, Any]] = []
    for _line_no, event in iter_events(ws, findings=findings):
        if wanted is not None and event.get("type") not in wanted:
            continue
        if subject_id is not None and event.get("subject_id") != subject_id:
            continue
        if session_id is not None and event.get("session_id") != session_id:
            continue
        if concept_id is not None and concept_id not in (event.get("concept_ids") or []):
            continue
        if since_dt is not None:
            moment = parse_timestamp_safe(event.get("at"))
            if moment is None or moment < since_dt:
                continue
        events.append(event)
    return events, findings


def last_event(
    ws: Workspace,
    *,
    subject_id: str | None = None,
    types: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """The most recent matching event by position in the log (which is append order)."""
    events, _findings = read_events(ws, subject_id=subject_id, types=types)
    return events[-1] if events else None


def newest_timestamp(
    ws: Workspace,
    *,
    subject_id: str | None = None,
    types: Iterable[str] | None = None,
) -> str | None:
    """The largest ``at`` among matching events, which drift detection compares against."""
    events, _findings = read_events(ws, subject_id=subject_id, types=types)
    stamps = [str(event.get("at")) for event in events if parse_timestamp_safe(event.get("at"))]
    return max(stamps) if stamps else None


def count_events(ws: Workspace) -> tuple[int, int]:
    """``(valid_lines, corrupt_lines)`` — a cheap health probe for `status`."""
    findings: list[Finding] = []
    valid = sum(1 for _ in iter_events(ws, findings=findings))
    return valid, len(findings)


def session_ids(ws: Workspace, *, subject_id: str | None = None) -> list[str]:
    """Session ids seen in the log, in first-appearance order."""
    seen: list[str] = []
    events, _findings = read_events(ws, subject_id=subject_id)
    for event in events:
        session = event.get("session_id")
        if isinstance(session, str) and session not in seen:
            seen.append(session)
    return seen
