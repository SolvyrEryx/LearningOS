"""Comparing the evidence log against the snapshots, and repairing derived fields.

Three layers of memory can disagree: the append-only event log (which cannot be rebuilt), the
per-subject snapshots (which can), and the derived queue. They drift when a session is interrupted
between an event and the snapshot it justifies, or when a file is hand-edited.

`reconcile` is the honest answer to that. It is **report-only by default**: it replays the log
against the snapshots and prints what disagrees. ``--apply`` is the single writing path, it takes
a backup before it touches anything, and it only ever re-derives fields that the stored evidence
already implies — status, the mastery estimate, the attempt counters and the revision queue. It
never invents a ``retention_evidence`` entry from an event, because an event does not carry
everything an evidence entry needs, and a fabricated attempt is indistinguishable from a real one
once it is written.

What this means in practice: `reconcile --apply` can repair a wrong status or a stale counter, and
it cannot repair lost evidence. For lost evidence the answer is `restore`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common import (
    Finding,
    Workspace,
    escape_pointer_token,
    utc_now_iso,
)
from memory import backup as backup_mod
from memory import events as events_mod
from memory import mastery as mastery_mod
from memory import store

#: Events that imply a concept's snapshot should have moved.
ATTEMPT_EVENTS = ("answer_evaluated", "retention_check")

#: Imported rather than redeclared so `validate`'s drift warning and `reconcile`'s drift report
#: can never disagree about what counts as state-changing evidence.
from validation import STATE_CHANGING_EVENTS  # noqa: E402  (deliberate: one source of truth)


@dataclass
class SubjectDrift:
    """What disagrees for one subject, and what ``--apply`` would do about it."""

    subject_id: str
    findings: list[Finding] = field(default_factory=list)
    repairable_concepts: list[str] = field(default_factory=list)
    queue_rebuild: bool = False

    @property
    def has_drift(self) -> bool:
        return bool(self.findings)


@dataclass
class ReconcileReport:
    subjects: list[SubjectDrift] = field(default_factory=list)
    log_findings: list[Finding] = field(default_factory=list)

    @property
    def findings(self) -> list[Finding]:
        collected = list(self.log_findings)
        for subject in self.subjects:
            collected.extend(subject.findings)
        return collected

    @property
    def has_drift(self) -> bool:
        return any(subject.has_drift for subject in self.subjects)

    @property
    def repairable(self) -> int:
        return sum(len(subject.repairable_concepts) for subject in self.subjects)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subjects": [
                {
                    "subject_id": subject.subject_id,
                    "findings": [finding.to_dict() for finding in subject.findings],
                    "repairable_concepts": subject.repairable_concepts,
                    "queue_rebuild": subject.queue_rebuild,
                }
                for subject in self.subjects
            ],
            "log_findings": [finding.to_dict() for finding in self.log_findings],
            "drift": self.has_drift,
            "repairable": self.repairable,
        }


# ------------------------------------------------------------------------------------- analysis


def _attempt_counts_from_events(ws: Workspace, subject_id: str) -> dict[str, dict[str, int]]:
    """Per concept, how many attempts and unaided successes the log records."""
    counts: dict[str, dict[str, int]] = {}
    events, _findings = events_mod.read_events(ws, subject_id=subject_id, types=ATTEMPT_EVENTS)
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        outcome = str(payload.get("outcome") or "")
        hints = int(payload.get("hints_used") or 0)
        for concept_id in event.get("concept_ids") or []:
            if not isinstance(concept_id, str):
                continue
            bucket = counts.setdefault(concept_id, {"attempts": 0, "independent": 0})
            bucket["attempts"] += 1
            if outcome == "independent_success" and hints == 0:
                bucket["independent"] += 1
    return counts


def analyse_subject(ws: Workspace, subject_id: str, *, now: str | None = None) -> SubjectDrift:
    """Replay the log for one subject and report every disagreement found."""
    drift = SubjectDrift(subject_id=subject_id)
    moment = now or utc_now_iso()
    mastery = store.load_concept_mastery(ws, subject_id, required=False)
    if mastery is None:
        drift.findings.append(
            Finding(
                severity="error",
                code="missing_file",
                message=f"subject {subject_id!r} has no concept_mastery.json, so there is nothing "
                "to reconcile against",
                path=ws.rel(store.concept_mastery_path(ws, subject_id)),
                remedy=f'Run `init-subject --id {subject_id} --name "..."` (idempotent) or '
                "`restore --from <backup>`.",
            )
        )
        return drift

    mastery_relative = ws.rel(store.concept_mastery_path(ws, subject_id))
    records: dict[str, Any] = mastery.get("concepts") or {}
    misconceptions = store.load_misconceptions(ws, subject_id, required=False)
    queue = store.load_revision_queue(ws, subject_id, required=False)

    newest_event = events_mod.newest_timestamp(
        ws, subject_id=subject_id, types=STATE_CHANGING_EVENTS
    )
    if newest_event:
        snapshots = (
            ("subject_state.json", store.load_subject_state(ws, subject_id, required=False)),
            ("concept_mastery.json", mastery),
        )
        for name, data in snapshots:
            if not isinstance(data, dict):
                continue
            path = ws.memory_subject(subject_id) / name
            updated_at = str(data.get("updated_at") or "")
            if updated_at and newest_event > updated_at:
                drift.findings.append(
                    Finding(
                        severity="warning",
                        code="stale_state",
                        message=f"the newest evidence for {subject_id!r} is {newest_event} but "
                        f"{name} was last updated at {updated_at}",
                        path=ws.rel(path),
                        pointer="/updated_at",
                        remedy="`reconcile --apply` re-derives status, the mastery estimate and "
                        "the counters from the stored evidence after taking a backup. Lost "
                        "evidence itself needs `restore`.",
                    )
                )

    event_counts = _attempt_counts_from_events(ws, subject_id)
    for concept_id in sorted(set(event_counts) - set(records)):
        drift.findings.append(
            Finding(
                severity="warning",
                code="drift_missing_record",
                message=f"the log has {event_counts[concept_id]['attempts']} attempt(s) for "
                f"{concept_id} but there is no concept record",
                path=mastery_relative,
                pointer="/concepts",
                remedy="Seed the record (its history is in the log and will be shown by "
                f"`search-memory {concept_id}`). `reconcile --apply` will not invent evidence.",
            )
        )

    for concept_id, record in sorted(records.items()):
        if not isinstance(record, dict):
            continue
        pointer = f"/concepts/{escape_pointer_token(concept_id)}"
        expected = mastery_mod.recompute_concept(
            record,
            now=moment,
            open_misconceptions=mastery_mod.open_misconception_count(record, misconceptions),
            blocks_promotion=mastery_mod.blocking_misconception(record, misconceptions),
        )
        differences: list[str] = []
        if expected["status"] != record.get("status"):
            differences.append(f"status {record.get('status')!r} -> {expected['status']!r}")
        if expected["mastery_estimate"] != record.get("mastery_estimate"):
            differences.append(
                f"mastery_estimate {record.get('mastery_estimate')} -> "
                f"{expected['mastery_estimate']}"
            )
        if expected["independent_successes"] != int(record.get("independent_successes") or 0):
            differences.append(
                f"independent_successes {record.get('independent_successes')} -> "
                f"{expected['independent_successes']}"
            )
        if expected.get("hint_usage") != record.get("hint_usage"):
            differences.append("hint_usage no longer matches the stored evidence")
        if differences:
            drift.repairable_concepts.append(concept_id)
            drift.findings.append(
                Finding(
                    severity="warning",
                    code="drift_derived_field",
                    message=f"{concept_id}: " + "; ".join(differences),
                    path=mastery_relative,
                    pointer=pointer,
                    remedy="`reconcile --apply` recomputes these from the stored evidence (after "
                    "a backup). The evidence itself is not changed.",
                )
            )

        counts = event_counts.get(concept_id)
        stored_evidence = len(record.get("retention_evidence") or [])
        if counts and counts["attempts"] > stored_evidence:
            drift.findings.append(
                Finding(
                    severity="warning",
                    code="drift_missing_evidence",
                    message=f"{concept_id}: the log records {counts['attempts']} attempt(s) but "
                    f"the record holds {stored_evidence} evidence entr(y/ies)",
                    path=mastery_relative,
                    pointer=f"{pointer}/retention_evidence",
                    remedy="An interrupted write. `reconcile --apply` will not fabricate the "
                    "missing attempts: re-probe the concept, or `restore` the snapshot from the "
                    "backup taken before the interruption.",
                )
            )

        due = record.get("review_due_at")
        if isinstance(due, str) and mastery_mod.queue_item_for(queue, concept_id) is None:
            drift.queue_rebuild = True
            drift.findings.append(
                Finding(
                    severity="warning",
                    code="drift_queue_missing_item",
                    message=f"{concept_id} is due at {due} but has no revision queue item",
                    path=ws.rel(store.revision_queue_path(ws, subject_id)),
                    pointer="/items",
                    remedy="`reconcile --apply` rebuilds the queue from the concept records; the "
                    "queue is derived state.",
                )
            )
    for item in (queue or {}).get("items") or []:
        if isinstance(item, dict) and item.get("concept_id") not in records:
            drift.queue_rebuild = True
            drift.findings.append(
                Finding(
                    severity="warning",
                    code="drift_queue_orphan_item",
                    message=f"revision queue holds {item.get('concept_id')!r}, which has no "
                    "concept record",
                    path=ws.rel(store.revision_queue_path(ws, subject_id)),
                    pointer="/items",
                    remedy="`reconcile --apply` drops orphan queue items.",
                )
            )
    return drift


def analyse(ws: Workspace, *, subject: str | None = None, now: str | None = None) -> ReconcileReport:
    """Replay the log against every subject's snapshots (or just one)."""
    report = ReconcileReport()
    log_findings: list[Finding] = []
    for _line_no, _event in events_mod.iter_events(ws, findings=log_findings):
        pass
    report.log_findings = log_findings
    subjects = [subject] if subject else ws.list_subjects()
    for subject_id in subjects:
        store.require_subject(ws, subject_id)
        report.subjects.append(analyse_subject(ws, subject_id, now=now))
    return report


# ---------------------------------------------------------------------------------- repair path


def apply(
    ws: Workspace,
    report: ReconcileReport,
    *,
    now: str | None = None,
    session_id: str | None = None,
    take_backup: bool = True,
) -> dict[str, Any]:
    """Re-derive the drifted fields. The only writing path, and it backs up first."""
    moment = now or utc_now_iso()
    changes: dict[str, Any] = {"backup_id": None, "concepts": [], "queues": []}
    if take_backup:
        result = backup_mod.create_backup(ws, note="pre-reconcile safety backup")
        changes["backup_id"] = result.backup_id

    for subject in report.subjects:
        if not subject.repairable_concepts and not subject.queue_rebuild:
            continue
        mastery = store.load_concept_mastery(ws, subject.subject_id, required=False)
        if mastery is None:
            continue
        misconceptions = store.load_misconceptions(ws, subject.subject_id, required=False)
        records = mastery.get("concepts") or {}
        for concept_id in subject.repairable_concepts:
            record = records.get(concept_id)
            if not isinstance(record, dict):
                continue
            before_status = record.get("status")
            updated = mastery_mod.recompute_concept(
                record,
                now=moment,
                open_misconceptions=mastery_mod.open_misconception_count(record, misconceptions),
                blocks_promotion=mastery_mod.blocking_misconception(record, misconceptions),
            )
            records[concept_id] = updated
            changes["concepts"].append(
                {
                    "subject_id": subject.subject_id,
                    "concept_id": concept_id,
                    "status_before": before_status,
                    "status_after": updated["status"],
                    "mastery_after": updated["mastery_estimate"],
                }
            )
            events_mod.append_event(
                ws,
                type="mastery_updated",
                session_id=session_id or f"{moment[:10]}-reconcile",
                subject_id=subject.subject_id,
                concept_ids=[concept_id],
                actor="tool",
                payload={
                    "concept_id": concept_id,
                    "status_before": before_status,
                    "status_after": updated["status"],
                    "mastery_after": updated["mastery_estimate"],
                    "independent_successes": updated["independent_successes"],
                    "reason": "reconcile re-derived this from the stored evidence",
                },
            )
        if subject.repairable_concepts:
            mastery["concepts"] = records
            store.save_concept_mastery(ws, subject.subject_id, mastery)
            # Re-derive the subject's coverage counters too, so the repair does not itself leave a
            # snapshot lagging behind the events it just appended.
            mastery_mod.refresh_subject_counters(ws, subject.subject_id)
        if subject.queue_rebuild:
            rebuilt = _rebuild_queue(ws, subject.subject_id, records, now=moment)
            changes["queues"].append({"subject_id": subject.subject_id, "items": len(rebuilt["items"])})
    return changes


def _rebuild_queue(
    ws: Workspace,
    subject_id: str,
    records: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    """Rebuild the queue from the concept records, keeping each concept's ladder step."""
    existing = store.load_revision_queue(ws, subject_id, required=False) or mastery_mod.new_revision_queue(
        subject_id, at=now
    )
    ladder = mastery_mod.ladder_for(existing)
    items: list[dict[str, Any]] = []
    for concept_id, record in sorted(records.items()):
        if not isinstance(record, dict):
            continue
        due = record.get("review_due_at")
        if not isinstance(due, str):
            continue
        previous = mastery_mod.queue_item_for(existing, concept_id) or {}
        step_index = max(0, min(int(previous.get("step_index") or 0), len(ladder) - 1))
        items.append(
            {
                "concept_id": concept_id,
                "due_at": due,
                "interval_days": int(previous.get("interval_days") or ladder[step_index]),
                "step_index": step_index,
                "reason": previous.get("reason") or "rebuilt by reconcile from the concept record",
                "last_outcome": previous.get("last_outcome"),
                "attempts": int(previous.get("attempts") or 0),
            }
        )
    items.sort(key=lambda item: (str(item["due_at"]), str(item["concept_id"])))
    existing["items"] = items
    existing["ladder_days"] = ladder
    return store.save_revision_queue(ws, subject_id, existing)
