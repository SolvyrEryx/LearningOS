"""Deriving a session log from the evidence.

`session-report` writes the markdown record of a session to ``03_MEMORY/session_logs/``. Every
number in it is **derived from the event log**, never from the conversation: the report cannot
claim an independent success that no ``answer_evaluated`` event supports. That is what makes it
worth reading six weeks later.

The report is a derived artefact. Deleting one loses nothing — re-run the command. Nothing in here
writes to a source-of-truth memory file, and an existing report is never overwritten without
``--force``: the second run of the day lands beside the first as ``…-02.md``, because silently
replacing a session log would destroy the only human-readable account of what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from common import (
    TOOL_ID,
    Finding,
    LearningOSNotFoundError,
    LearningOSRefusedError,
    Workspace,
    atomic_write_text,
    days_between,
    parse_timestamp_safe,
    utc_now_iso,
)
from memory import events as events_mod
from memory import store

ANSWER_OUTCOMES = ("independent_success", "hinted_success", "partial", "fail")


# ------------------------------------------------------------------------------- session lookup


@dataclass
class SessionSummary:
    """Enough about a session to list it or pick the newest one."""

    session_id: str
    subject_id: str | None
    first_at: str
    last_at: str
    event_count: int


def session_summaries(ws: Workspace, *, subject: str | None = None) -> list[SessionSummary]:
    """Every session the log knows about, oldest first by its first event."""
    seen: dict[str, SessionSummary] = {}
    for _line_no, event in events_mod.iter_events(ws):
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            continue
        subject_id = event.get("subject_id") if isinstance(event.get("subject_id"), str) else None
        if subject is not None and subject_id != subject:
            continue
        at = str(event.get("at") or "")
        existing = seen.get(session_id)
        if existing is None:
            seen[session_id] = SessionSummary(session_id, subject_id, at, at, 1)
        else:
            seen[session_id] = SessionSummary(
                session_id,
                existing.subject_id or subject_id,
                min(existing.first_at, at) if at else existing.first_at,
                max(existing.last_at, at) if at else existing.last_at,
                existing.event_count + 1,
            )
    return sorted(seen.values(), key=lambda summary: (summary.first_at, summary.session_id))


def latest_session(ws: Workspace, *, subject: str | None = None) -> SessionSummary | None:
    """The session with the newest event, which is what ``--last`` means."""
    summaries = session_summaries(ws, subject=subject)
    if not summaries:
        return None
    return max(summaries, key=lambda summary: (summary.last_at, summary.session_id))


# ---------------------------------------------------------------------------------- derivation


@dataclass
class ConceptLine:
    """One concept's story within the session."""

    concept_id: str
    name: str | None = None
    attempts: int = 0
    independent: int = 0
    hinted: int = 0
    partial: int = 0
    failed: int = 0
    hints: int = 0
    status_before: str | None = None
    status_after: str | None = None
    mastery_before: float | None = None
    mastery_after: float | None = None
    next_action: str | None = None
    mistakes: list[str] = field(default_factory=list)

    @property
    def mastery_delta(self) -> float | None:
        if self.mastery_before is None or self.mastery_after is None:
            return None
        return round(self.mastery_after - self.mastery_before, 2)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "concept_id": self.concept_id,
            "name": self.name,
            "attempts": self.attempts,
            "independent": self.independent,
            "hinted": self.hinted,
            "partial": self.partial,
            "failed": self.failed,
            "hints": self.hints,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "mastery_before": self.mastery_before,
            "mastery_after": self.mastery_after,
            "mastery_delta": self.mastery_delta,
            "next_action": self.next_action,
            "mistakes": list(self.mistakes),
        }
        return data


def build_report(
    ws: Workspace,
    session_id: str,
    *,
    subject: str | None = None,
) -> dict[str, Any]:
    """Derive the whole report for one session id. Exit 4 when the log has never seen it."""
    findings: list[Finding] = []
    events = [
        event
        for _line_no, event in events_mod.iter_events(ws, findings=findings)
        if event.get("session_id") == session_id
        and (subject is None or event.get("subject_id") == subject)
    ]
    if not events:
        known = [summary.session_id for summary in session_summaries(ws, subject=subject)]
        raise LearningOSNotFoundError(
            f"no events recorded for session {session_id!r}"
            + (f" in subject {subject!r}" if subject else ""),
            remedy=(
                "Known sessions: " + ", ".join(known[-8:])
                if known
                else "The event log has no sessions yet, so there is nothing to report on."
            ),
        )
    events.sort(key=lambda event: str(event.get("at") or ""))
    subject_id = next(
        (
            str(event.get("subject_id"))
            for event in events
            if isinstance(event.get("subject_id"), str) and event.get("subject_id")
        ),
        subject,
    )

    first_at = str(events[0].get("at") or "")
    last_at = str(events[-1].get("at") or "")
    duration = _duration_minutes(events, first_at, last_at)

    lines: dict[str, ConceptLine] = {}

    def line_for(concept_id: str) -> ConceptLine:
        if concept_id not in lines:
            lines[concept_id] = ConceptLine(concept_id=concept_id)
        return lines[concept_id]

    totals = {outcome: 0 for outcome in ANSWER_OUTCOMES}
    hints_total = 0
    explanations = 0
    probes = 0
    raised: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    open_questions: list[str] = []
    scheduled: list[dict[str, Any]] = []
    next_actions: list[str] = []
    checkpoints: list[str] = []

    for event in events:
        kind = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        concept_ids = [cid for cid in event.get("concept_ids") or [] if isinstance(cid, str)]
        if kind in ("answer_evaluated", "retention_check", "diagnostic_probe"):
            outcome = str(payload.get("outcome") or "")
            hints = int(payload.get("hints_used") or 0)
            if kind == "diagnostic_probe":
                probes += 1
            if outcome in totals and kind != "diagnostic_probe":
                totals[outcome] += 1
            for concept_id in concept_ids:
                line = line_for(concept_id)
                line.attempts += 1
                if outcome == "independent_success" and hints == 0:
                    line.independent += 1
                elif outcome == "hinted_success":
                    line.hinted += 1
                elif outcome == "partial":
                    line.partial += 1
                elif outcome == "fail":
                    line.failed += 1
                pattern = payload.get("mistake_pattern")
                if isinstance(pattern, str) and pattern and pattern not in line.mistakes:
                    line.mistakes.append(pattern)
        elif kind == "hint_given":
            hints_total += 1
            for concept_id in concept_ids:
                line_for(concept_id).hints += 1
        elif kind == "explanation_given":
            explanations += 1
        elif kind == "mastery_updated":
            concept_id = str(payload.get("concept_id") or (concept_ids[0] if concept_ids else ""))
            if concept_id:
                line = line_for(concept_id)
                if line.status_before is None:
                    line.status_before = payload.get("status_before")
                    line.mastery_before = payload.get("mastery_before")
                line.status_after = payload.get("status_after")
                line.mastery_after = payload.get("mastery_after")
        elif kind == "misconception_detected":
            raised.append(
                {
                    "misconception_id": payload.get("misconception_id"),
                    "description": payload.get("description"),
                    "concept_ids": concept_ids,
                }
            )
        elif kind == "misconception_resolved":
            resolved.append(
                {
                    "misconception_id": payload.get("misconception_id"),
                    "description": payload.get("description"),
                    "concept_ids": concept_ids,
                }
            )
        elif kind == "open_question_logged":
            question = payload.get("question")
            if isinstance(question, str):
                open_questions.append(question)
        elif kind == "revision_scheduled":
            scheduled.append(
                {
                    "concept_id": payload.get("concept_id"),
                    "due_at": payload.get("due_at"),
                    "interval_days": payload.get("interval_days"),
                    "step_index": payload.get("step_index"),
                }
            )
        elif kind == "checkpoint_written":
            checkpoint_next = payload.get("next_action")
            if isinstance(checkpoint_next, str) and checkpoint_next:
                checkpoints.append(checkpoint_next)
        elif kind == "session_ended":
            note = payload.get("notes")
            if isinstance(note, str) and note:
                next_actions.append(note)

    # Names and next actions come from the current records: the report is a view over state, so a
    # concept renamed since the session is shown under the name the learner now uses.
    if subject_id:
        mastery = store.load_concept_mastery(ws, subject_id, required=False) or {}
        records = mastery.get("concepts") or {}
        for concept_id, line in lines.items():
            record = records.get(concept_id)
            if isinstance(record, dict):
                line.name = record.get("name")
                line.next_action = record.get("next_action")
                if line.status_after is None:
                    line.status_after = record.get("status")

    for line in lines.values():
        if line.next_action and line.next_action not in next_actions:
            next_actions.append(line.next_action)
    for action in checkpoints:
        if action not in next_actions:
            next_actions.insert(0, action)

    independent_total = sum(line.independent for line in lines.values())
    hinted_total = sum(line.hinted for line in lines.values())
    return {
        "tool": TOOL_ID,
        "generated_at": utc_now_iso(),
        "session_id": session_id,
        "subject_id": subject_id,
        "date": first_at[:10] if first_at else utc_now_iso()[:10],
        "started_at": first_at or None,
        "ended_at": last_at or None,
        "duration_minutes": duration,
        "event_count": len(events),
        "completed": any(event.get("type") == "session_ended" for event in events),
        "outcomes": totals,
        "independent_successes": independent_total or totals["independent_success"],
        "hinted_successes": hinted_total or totals["hinted_success"],
        "hints_given": hints_total,
        "explanations": explanations,
        "probes": probes,
        "concepts": [line.to_dict() for line in sorted(lines.values(), key=lambda l: l.concept_id)],
        "misconceptions_raised": raised,
        "misconceptions_resolved": resolved,
        "open_questions": open_questions,
        "reviews_scheduled": scheduled,
        "next_actions": next_actions,
        "unreadable_log_lines": len(findings),
    }


def _duration_minutes(
    events: Sequence[dict[str, Any]], first_at: str, last_at: str
) -> float | None:
    """Prefer the session's own reported duration; otherwise span the events."""
    for event in reversed(events):
        if event.get("type") == "session_ended":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            reported = payload.get("duration_minutes")
            if isinstance(reported, (int, float)):
                return round(float(reported), 1)
    if parse_timestamp_safe(first_at) and parse_timestamp_safe(last_at):
        return round(days_between(first_at, last_at) * 1440.0, 1)
    return None


# ------------------------------------------------------------------------------------ rendering


def _format_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _delta(value: Any) -> str:
    if value is None:
        return "—"
    return f"+{value:.2f}" if value > 0 else f"{value:.2f}"


def render_markdown(report: dict[str, Any]) -> str:
    """The session log as markdown. Pure function of :func:`build_report`'s output."""
    lines: list[str] = []
    subject = report.get("subject_id") or "(no subject)"
    lines.append(f"# Session {report['session_id']} — {subject}")
    lines.append("")
    lines.append(
        f"Derived from `03_MEMORY/events/session_events.jsonl` by `{report['tool']}` at "
        f"{report['generated_at']}. Every count below is evidence from the log, not a summary of "
        "the conversation."
    )
    lines.append("")
    lines.append("## At a glance")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Date | {report['date']} |")
    lines.append(f"| Started | {report['started_at'] or '—'} |")
    lines.append(f"| Ended | {report['ended_at'] or '—'} |")
    duration = report.get("duration_minutes")
    lines.append(
        f"| Duration | {_format_number(duration)} minutes |"
        if duration is not None
        else "| Duration | — |"
    )
    lines.append(f"| Session closed cleanly | {'yes' if report['completed'] else 'no'} |")
    lines.append(f"| Concepts touched | {len(report['concepts'])} |")
    lines.append(f"| Independent successes | {report['independent_successes']} |")
    lines.append(f"| Hinted successes | {report['hinted_successes']} |")
    lines.append(f"| Partial / failed | {report['outcomes']['partial']} / {report['outcomes']['fail']} |")
    lines.append(f"| Hints given | {report['hints_given']} |")
    lines.append(f"| Explanations | {report['explanations']} |")
    lines.append(f"| Diagnostic probes | {report['probes']} |")
    lines.append(f"| Events recorded | {report['event_count']} |")
    lines.append("")
    lines.append(
        "An independent success is an unaided one: a hinted success is counted separately and "
        "never as evidence of independence."
    )
    lines.append("")

    lines.append("## Concepts")
    lines.append("")
    if report["concepts"]:
        lines.append("| Concept | Attempts | Indep. | Hinted | Hints | Status | Mastery | Δ |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for concept in report["concepts"]:
            status = concept["status_before"] or "—"
            if concept["status_after"] and concept["status_after"] != concept["status_before"]:
                status = f"{status} → {concept['status_after']}"
            elif concept["status_after"]:
                status = str(concept["status_after"])
            mastery = (
                f"{_format_number(concept['mastery_before'])} → "
                f"{_format_number(concept['mastery_after'])}"
            )
            label = concept["name"] or concept["concept_id"]
            lines.append(
                f"| `{concept['concept_id']}` {label} | {concept['attempts']} | "
                f"{concept['independent']} | {concept['hinted']} | {concept['hints']} | "
                f"{status} | {mastery} | {_delta(concept['mastery_delta'])} |"
            )
    else:
        lines.append("_No concept-level evidence was recorded in this session._")
    lines.append("")

    mistakes = [
        (concept["concept_id"], pattern)
        for concept in report["concepts"]
        for pattern in concept["mistakes"]
    ]
    if mistakes:
        lines.append("## Mistake patterns seen")
        lines.append("")
        for concept_id, pattern in mistakes:
            lines.append(f"- `{concept_id}` — {pattern}")
        lines.append("")

    if report["misconceptions_raised"] or report["misconceptions_resolved"]:
        lines.append("## Misconceptions")
        lines.append("")
        for entry in report["misconceptions_raised"]:
            lines.append(
                f"- **raised** `{entry['misconception_id']}` — "
                f"{entry.get('description') or 'no description recorded'}"
            )
        for entry in report["misconceptions_resolved"]:
            lines.append(
                f"- **resolved** `{entry['misconception_id']}` — "
                f"{entry.get('description') or 'no description recorded'}"
            )
        lines.append("")

    if report["reviews_scheduled"]:
        lines.append("## Reviews scheduled")
        lines.append("")
        lines.append("| Concept | Due | Interval (days) | Ladder step |")
        lines.append("|---|---|---|---|")
        for entry in report["reviews_scheduled"]:
            lines.append(
                f"| `{entry['concept_id']}` | {entry['due_at']} | "
                f"{_format_number(entry['interval_days'])} | {_format_number(entry['step_index'])} |"
            )
        lines.append("")

    if report["open_questions"]:
        lines.append("## Open questions raised")
        lines.append("")
        for question in report["open_questions"]:
            lines.append(f"- {question}")
        lines.append("")

    lines.append("## Next actions")
    lines.append("")
    if report["next_actions"]:
        for action in report["next_actions"]:
            lines.append(f"- {action}")
    else:
        lines.append("_None recorded. Write one into `next_session.md` before you stop._")
    lines.append("")

    if report["unreadable_log_lines"]:
        lines.append(
            f"> {report['unreadable_log_lines']} line(s) of the event log could not be parsed and "
            "were skipped. Run `python learningos.py validate` for their line numbers; the log is "
            "never rewritten."
        )
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------- writing


def report_filename(report: dict[str, Any]) -> str:
    subject = report.get("subject_id") or "workspace"
    return f"{report['date']}-{subject}-{report['session_id']}.md"


def default_report_path(ws: Workspace, report: dict[str, Any]) -> Path:
    return ws.session_logs / report_filename(report)


def _next_free_path(path: Path) -> Path:
    """``…-02.md``, ``…-03.md``, … so a second report never replaces the first."""
    for suffix in range(2, 100):
        candidate = path.with_name(f"{path.stem}-{suffix:02d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise LearningOSRefusedError(  # pragma: no cover - 98 reports for one session
        f"too many existing reports for {path.name}",
        path=str(path),
        remedy="Move the old reports out of 03_MEMORY/session_logs/ yourself, or pass --force.",
    )


def write_report(
    ws: Workspace,
    report: dict[str, Any],
    *,
    out: Path | None = None,
    force: bool = False,
) -> tuple[Path, bool]:
    """Write the markdown atomically. Returns ``(path, replaced_existing)``.

    Without ``--force`` an existing report is left alone and the new one is written beside it with
    a numeric suffix, so nothing a previous session recorded is ever lost.
    """
    target = out or default_report_path(ws, report)
    replaced = False
    if target.exists():
        if force:
            replaced = True
        else:
            target = _next_free_path(target)
    atomic_write_text(target, render_markdown(report) + "\n")
    return target, replaced


def summarise(report: dict[str, Any]) -> str:
    """The one-line echo the CLI prints after writing."""
    duration = report.get("duration_minutes")
    return (
        f"session {report['session_id']} ({report.get('subject_id') or 'no subject'}): "
        f"{_format_number(duration) if duration is not None else '?'} min, "
        f"{len(report['concepts'])} concept(s), "
        f"{report['independent_successes']} independent / {report['hinted_successes']} hinted, "
        f"{report['hints_given']} hint(s)"
    )
