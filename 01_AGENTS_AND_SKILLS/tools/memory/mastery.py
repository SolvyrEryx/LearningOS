"""Turning evidence into status, a mastery estimate and a spacing schedule.

This module is where the central rule of LearningOS is implemented in code rather than prose:
**mastery rises only on unaided performance**. A hinted success is recorded honestly as a hinted
success; it never increments ``independent_successes`` and it can never reach
``provisionally_mastered``. Raw accuracy is not evidence of independence, because following a hint
correctly is a measure of the hint.

Everything here except :func:`record_attempt_and_save` is a pure function over plain dicts, so the
ladder and the heuristic can be reasoned about and tested without touching the filesystem. The one
store-writing wrapper appends the ``answer_evaluated`` / ``mastery_updated`` /
``revision_scheduled`` events *before* it writes any snapshot, which is the order
`core/memory_protocol.md` section 6 requires: if the process dies between the two, the event log
still holds the truth and `reconcile` can rebuild the snapshot.

The status ladder (`core/memory_protocol.md` section 3) and the mastery heuristic (section 4) are
normative. Do not re-derive them anywhere else — call into this module.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from common import (
    SCHEMA_VERSION,
    LearningOSUsageError,
    Workspace,
    days_between,
    format_timestamp,
    parse_timestamp,
    parse_timestamp_safe,
    utc_now_iso,
)
from memory import events as events_mod
from memory import store

# ----------------------------------------------------------------------------------- vocabulary

OUTCOMES = ("independent_success", "hinted_success", "partial", "fail")
NOVELTIES = ("familiar", "variant", "novel")
SUCCESS_OUTCOMES = ("independent_success", "hinted_success")

#: The spacing ladder in days. Past the last step the interval holds.
LADDER_DAYS: tuple[int, ...] = (1, 3, 7, 16, 35)

#: Evidence value per outcome. `fail` is 0.0, not negative: the decay term already forgets.
OUTCOME_VALUES: dict[str, float] = {
    "independent_success": 1.0,
    "hinted_success": 0.45,
    "partial": 0.25,
    "fail": 0.0,
}

#: Harder material is worth more when it works. An unrecorded novelty is treated as `variant`.
NOVELTY_WEIGHTS: dict[str, float] = {"familiar": 0.8, "variant": 1.0, "novel": 1.15}
DEFAULT_NOVELTY_WEIGHT = 1.0

#: How many of the most recent attempts the estimate looks at.
EVIDENCE_WINDOW = 8

#: Recency half-life in days: evidence 30 days old counts half as much as evidence from today.
HALF_LIFE_DAYS = 30.0

#: Subtracted from the estimate for each open misconception linked to the concept.
MISCONCEPTION_PENALTY = 0.05

#: Promotion needs this many unaided successes, with the extra conditions in :func:`next_status`.
PROMOTION_INDEPENDENT_SUCCESSES = 2

#: One of the promoting attempts must have been after at least this gap.
PROMOTION_MIN_DELAY_DAYS = 2.0


# ------------------------------------------------------------------------------- small helpers


def is_independent(entry: dict[str, Any]) -> bool:
    """True only for an ``independent_success`` with zero hints. Nothing else counts."""
    return (
        isinstance(entry, dict)
        and entry.get("outcome") == "independent_success"
        and int(entry.get("hints_used") or 0) == 0
    )


def independent_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The unaided attempts in a record's evidence, oldest first."""
    return [entry for entry in record.get("retention_evidence") or [] if is_independent(entry)]


def hinted_success_count(record: dict[str, Any]) -> int:
    return sum(
        1
        for entry in record.get("retention_evidence") or []
        if isinstance(entry, dict) and entry.get("outcome") == "hinted_success"
    )


def _novelty_weight(novelty: Any) -> float:
    return NOVELTY_WEIGHTS.get(str(novelty), DEFAULT_NOVELTY_WEIGHT)


def _check_outcome(outcome: str) -> str:
    if outcome not in OUTCOMES:
        raise LearningOSUsageError(
            f"unknown outcome {outcome!r}",
            remedy="Use one of: " + ", ".join(OUTCOMES),
        )
    return outcome


def _check_novelty(novelty: Any) -> str | None:
    if novelty is None:
        return None
    if novelty not in NOVELTIES:
        raise LearningOSUsageError(
            f"unknown novelty {novelty!r}",
            remedy="Use one of: " + ", ".join(NOVELTIES) + ", or leave it unset.",
        )
    return str(novelty)


# --------------------------------------------------------------------------- mastery heuristic


def mastery_estimate(
    record: dict[str, Any],
    *,
    now: str | None = None,
    open_misconceptions: int = 0,
) -> float | None:
    """The documented mastery heuristic. Returns ``None`` when there is no evidence yet.

    Quoted in `core/memory_protocol.md` section 4 and implemented in exactly one place:

        over the last 8 ``retention_evidence`` entries, each attempt contributes
        ``outcome_value × novelty_weight`` with a recency weight of ``0.5 ** (days_since / 30)``;
        the estimate is the recency-weighted **mean** of those contributions, minus
        ``0.05 × open misconceptions``, clamped to ``0.0 .. 1.0`` and rounded to 2 decimals.

    * outcome values — independent 1.0, hinted 0.45, partial 0.25, fail 0.0
    * novelty weights — familiar 0.8, variant 1.0 (also the default when novelty is unrecorded),
      novel 1.15
    * recency — a 30-day half-life, so old evidence fades rather than being discarded

    A weighted **mean** rather than a running sum is what makes the number interpretable: it stays
    inside 0..1 by construction, it rises when newer evidence is better than the history, and it
    falls when a recent attempt fails. A sum would reward volume of practice regardless of
    quality.

    This is a *heuristic estimate of the chance of an unaided correct explanation and application
    right now*, not a measurement, not a score and not a probability of passing. Present it that
    way or not at all.
    """
    evidence = [
        entry
        for entry in (record.get("retention_evidence") or [])
        if isinstance(entry, dict) and entry.get("outcome") in OUTCOME_VALUES
    ]
    if not evidence:
        return None
    reference = now or record.get("last_attempted_at") or evidence[-1].get("at") or utc_now_iso()
    reference_dt = parse_timestamp_safe(reference)
    if reference_dt is None:
        reference_dt = parse_timestamp(utc_now_iso())

    weighted_total = 0.0
    weight_total = 0.0
    for entry in evidence[-EVIDENCE_WINDOW:]:
        moment = parse_timestamp_safe(entry.get("at"))
        days_since = 0.0 if moment is None else max(0.0, (reference_dt - moment).total_seconds() / 86400.0)
        weight = 0.5 ** (days_since / HALF_LIFE_DAYS)
        value = OUTCOME_VALUES[str(entry.get("outcome"))] * _novelty_weight(entry.get("novelty"))
        weighted_total += weight * value
        weight_total += weight
    if weight_total <= 0:  # pragma: no cover - weights are strictly positive
        return None
    raw = weighted_total / weight_total - MISCONCEPTION_PENALTY * max(0, int(open_misconceptions))
    return round(min(1.0, max(0.0, raw)), 2)


# ------------------------------------------------------------------------------- status ladder


def _repeated_mistake_is_current(record: dict[str, Any]) -> bool:
    """True when a mistake pattern has recurred and no cleaner attempt has happened since.

    A pattern reaching ``count >= 2`` forces `needs_review`. A later attempt that does not repeat
    it leaves ``last_seen_at`` behind the newest evidence, which clears the block — the ladder
    responds to current behaviour, not to a permanent record of past errors.
    """
    evidence = record.get("retention_evidence") or []
    if not evidence:
        return False
    last_at = str(evidence[-1].get("at") or "")
    for mistake in record.get("recurring_mistakes") or []:
        if not isinstance(mistake, dict):
            continue
        if int(mistake.get("count") or 0) >= 2 and str(mistake.get("last_seen_at") or "") >= last_at:
            return True
    return False


def _is_overdue(record: dict[str, Any], now: str | None) -> bool:
    """True when ``review_due_at`` has passed and no attempt was made since it fell due."""
    due = record.get("review_due_at")
    if not isinstance(due, str):
        return False
    moment = now or utc_now_iso()
    if due > moment:
        return False
    last_attempt = record.get("last_attempted_at")
    return not (isinstance(last_attempt, str) and last_attempt >= due)


def next_status(
    record: dict[str, Any],
    *,
    now: str | None = None,
    blocking_misconception: bool = False,
) -> str:
    """The status this record's evidence supports. Normative ladder, one implementation.

    * ``unassessed`` → ``developing`` on any attempt.
    * ``developing`` → ``practising`` after ≥ 1 unaided success **or** ≥ 2 hinted successes.
    * ``practising`` → ``provisionally_mastered`` only if **all** hold: ≥ 2 unaided successes; at
      least one of those two on material that was not ``familiar``; at least one of them after a
      gap of ≥ 2 days; zero hints in those two attempts (true by definition of unaided); and no
      unresolved linked misconception seen in the last two attempts.
    * anything → ``needs_review`` on a ``fail``, on a mistake pattern recurring, or when a
      scheduled review fell due with no attempt.

    "Provisionally" mastered means the evidence is sufficient to stop teaching and start spacing —
    not that the concept is finished.
    """
    evidence = [entry for entry in record.get("retention_evidence") or [] if isinstance(entry, dict)]
    if not evidence:
        return "needs_review" if _is_overdue(record, now) else str(record.get("status") or "unassessed")
    if evidence[-1].get("outcome") == "fail":
        return "needs_review"
    if _repeated_mistake_is_current(record):
        return "needs_review"
    if _is_overdue(record, now):
        return "needs_review"

    unaided = independent_evidence(record)
    if len(unaided) >= PROMOTION_INDEPENDENT_SUCCESSES and not blocking_misconception:
        deciding = unaided[-PROMOTION_INDEPENDENT_SUCCESSES:]
        novel_enough = any(
            entry.get("novelty") not in (None, "familiar") for entry in deciding
        )
        spaced_enough = any(
            float(entry.get("delay_days") or 0.0) >= PROMOTION_MIN_DELAY_DAYS for entry in deciding
        )
        unaided_enough = all(int(entry.get("hints_used") or 0) == 0 for entry in deciding)
        if novel_enough and spaced_enough and unaided_enough:
            return "provisionally_mastered"
    if unaided or hinted_success_count(record) >= 2:
        return "practising"
    return "developing"


def blocking_misconception(
    record: dict[str, Any],
    misconceptions: dict[str, Any] | None,
) -> bool:
    """Whether an unresolved linked misconception should block promotion.

    "Seen in the last two attempts" is read off the misconception's own occurrences: an unresolved
    record whose newest occurrence is at or after the second-to-last attempt still counts against
    the concept. An unresolved record with no occurrence timestamps blocks too — when the evidence
    cannot say it has gone away, the honest default is that it has not.
    """
    linked = {str(mid) for mid in record.get("misconception_ids") or []}
    if not linked:
        return False
    evidence = record.get("retention_evidence") or []
    threshold = str((evidence[-2] if len(evidence) >= 2 else evidence[0]).get("at") or "") if evidence else ""
    for entry in (misconceptions or {}).get("misconceptions", []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("misconception_id")) not in linked:
            continue
        if entry.get("status") == "resolved":
            continue
        occurrences = [
            str(item.get("at"))
            for item in entry.get("occurrences") or []
            if isinstance(item, dict) and isinstance(item.get("at"), str)
        ]
        if not occurrences:
            return True
        if max(occurrences) >= threshold:
            return True
    return False


def open_misconception_count(
    record: dict[str, Any],
    misconceptions: dict[str, Any] | None,
) -> int:
    """How many of a concept's linked misconceptions are not resolved."""
    linked = {str(mid) for mid in record.get("misconception_ids") or []}
    if not linked:
        return 0
    return sum(
        1
        for entry in (misconceptions or {}).get("misconceptions", [])
        if isinstance(entry, dict)
        and str(entry.get("misconception_id")) in linked
        and entry.get("status") != "resolved"
    )


# ------------------------------------------------------------------------------ spacing ladder


def ladder_for(queue: dict[str, Any] | None) -> list[int]:
    """The ladder a subject's queue is using, defaulting to 1, 3, 7, 16, 35 days."""
    configured = (queue or {}).get("ladder_days")
    if isinstance(configured, list) and configured and all(
        isinstance(step, int) and step >= 1 for step in configured
    ):
        return list(configured)
    return list(LADDER_DAYS)


def next_step_index(previous_step: int | None, outcome: str, *, ladder: Sequence[int]) -> int:
    """Where on the ladder the next review sits.

    Unaided success advances one step, a hinted success holds, and ``partial`` or ``fail`` resets
    to step 0 — a concept that needed help has not earned a longer gap. A concept that has never
    been scheduled starts at step 0 whatever happened, because there is no interval to advance
    from yet.
    """
    _check_outcome(outcome)
    if previous_step is None:
        return 0
    current = max(0, min(int(previous_step), len(ladder) - 1))
    if outcome == "independent_success":
        return min(current + 1, len(ladder) - 1)
    if outcome == "hinted_success":
        return current
    return 0


def schedule_review(
    concept_id: str,
    outcome: str,
    *,
    previous: dict[str, Any] | None = None,
    now: str | None = None,
    ladder: Sequence[int] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the revision-queue item for a concept after an attempt. Pure function."""
    steps = list(ladder or LADDER_DAYS)
    moment = now or utc_now_iso()
    previous_step = None if previous is None else int(previous.get("step_index") or 0)
    step_index = next_step_index(previous_step, outcome, ladder=steps)
    interval_days = int(steps[step_index])
    due_at = format_timestamp(parse_timestamp(moment) + _days(interval_days))
    attempts = int((previous or {}).get("attempts") or 0) + 1
    return {
        "concept_id": concept_id,
        "due_at": due_at,
        "interval_days": interval_days,
        "step_index": step_index,
        "reason": reason
        or ("spacing" if outcome in SUCCESS_OUTCOMES else "needs_review after a weak attempt"),
        "last_outcome": outcome,
        "attempts": attempts,
    }


def _days(count: float):
    import datetime as dt

    return dt.timedelta(days=count)


def new_revision_queue(subject_id: str, *, at: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "subject_id": subject_id,
        "updated_at": at or utc_now_iso(),
        "ladder_days": list(LADDER_DAYS),
        "items": [],
    }


def upsert_queue_item(queue: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Replace (or insert) one concept's queue item, keeping the queue sorted by due date."""
    items = [
        existing
        for existing in queue.get("items") or []
        if isinstance(existing, dict) and existing.get("concept_id") != item["concept_id"]
    ]
    items.append(item)
    items.sort(key=lambda entry: (str(entry.get("due_at")), str(entry.get("concept_id"))))
    queue["items"] = items
    return queue


def queue_item_for(queue: dict[str, Any] | None, concept_id: str) -> dict[str, Any] | None:
    for item in (queue or {}).get("items") or []:
        if isinstance(item, dict) and item.get("concept_id") == concept_id:
            return item
    return None


def due_items(queue: dict[str, Any] | None, *, now: str | None = None) -> list[dict[str, Any]]:
    """Queue items that have fallen due, soonest first."""
    moment = now or utc_now_iso()
    return sorted(
        (
            item
            for item in (queue or {}).get("items") or []
            if isinstance(item, dict) and str(item.get("due_at") or "") <= moment
        ),
        key=lambda item: str(item.get("due_at")),
    )


# ------------------------------------------------------------------------------ recording work


def _bump_recurring_mistake(record: dict[str, Any], pattern: str, at: str) -> bool:
    """Increment an existing pattern or add it. Returns True when it has now recurred."""
    mistakes = record.setdefault("recurring_mistakes", [])
    for mistake in mistakes:
        if isinstance(mistake, dict) and mistake.get("pattern") == pattern:
            mistake["count"] = int(mistake.get("count") or 0) + 1
            mistake["last_seen_at"] = at
            mistake.setdefault("first_seen_at", at)
            return int(mistake["count"]) >= 2
    mistakes.append(
        {"pattern": pattern, "count": 1, "first_seen_at": at, "last_seen_at": at}
    )
    return False


def record_attempt(
    record: dict[str, Any],
    *,
    outcome: str,
    hints_used: int = 0,
    novelty: str | None = None,
    difficulty: int | None = None,
    delay_days: float | None = None,
    at: str | None = None,
    mistake_pattern: str | None = None,
    session_id: str | None = None,
    event_id: str | None = None,
    next_action: str | None = None,
    confidence_estimate: float | None = None,
    open_misconceptions: int = 0,
    blocks_promotion: bool = False,
) -> dict[str, Any]:
    """Apply one judged attempt to a concept record and return the updated copy.

    The input record is not mutated. What this does, in order:

    1. normalises the outcome — an ``independent_success`` reported with hints is stored as a
       ``hinted_success``, because "independent" means zero hints and the counter must not be
       reachable by a hinted attempt under another name;
    2. appends a ``retention_evidence`` entry (computing ``delay_days`` from the previous attempt
       when the caller does not supply it);
    3. updates the counters — ``independent_successes`` only for an unaided success, hint totals
       always;
    4. folds in a repeated mistake pattern;
    5. recomputes ``status`` from the ladder and ``mastery_estimate`` from the heuristic.

    ``confidence_estimate`` is stored as the learner's self-report and is never used to raise
    ``status``.
    """
    outcome = _check_outcome(outcome)
    novelty = _check_novelty(novelty)
    hints = max(0, int(hints_used or 0))
    if outcome == "independent_success" and hints > 0:
        outcome = "hinted_success"
    moment = at or utc_now_iso()

    updated = copy.deepcopy(record)
    evidence = updated.setdefault("retention_evidence", [])
    if delay_days is None:
        previous_at = evidence[-1].get("at") if evidence else updated.get("last_attempted_at")
        if isinstance(previous_at, str) and parse_timestamp_safe(previous_at):
            delay_days = round(max(0.0, days_between(previous_at, moment)), 2)
    entry: dict[str, Any] = {
        "at": moment,
        "delay_days": None if delay_days is None else float(delay_days),
        "outcome": outcome,
        "difficulty": difficulty,
        "novelty": novelty,
        "hints_used": hints,
        "session_id": session_id,
        "event_id": event_id,
    }
    evidence.append(entry)

    hint_usage = updated.setdefault(
        "hint_usage", {"total_hints": 0, "hinted_successes": 0, "last_hint_level": None}
    )
    hint_usage["total_hints"] = int(hint_usage.get("total_hints") or 0) + hints
    if hints:
        hint_usage["last_hint_level"] = min(4, hints)
    if outcome == "hinted_success":
        hint_usage["hinted_successes"] = int(hint_usage.get("hinted_successes") or 0) + 1
    if is_independent(entry):
        updated["independent_successes"] = int(updated.get("independent_successes") or 0) + 1
    if outcome in SUCCESS_OUTCOMES:
        updated["last_successful_retrieval_at"] = moment
    updated["last_attempted_at"] = moment
    if confidence_estimate is not None:
        updated["confidence_estimate"] = round(float(confidence_estimate), 2)
    if next_action is not None:
        updated["next_action"] = next_action
    if mistake_pattern:
        _bump_recurring_mistake(updated, mistake_pattern, moment)

    updated["status"] = next_status(
        updated, now=moment, blocking_misconception=blocks_promotion
    )
    updated["mastery_estimate"] = mastery_estimate(
        updated, now=moment, open_misconceptions=open_misconceptions
    )
    updated["updated_at"] = moment
    return updated


def recompute_concept(
    record: dict[str, Any],
    *,
    now: str | None = None,
    open_misconceptions: int = 0,
    blocks_promotion: bool = False,
) -> dict[str, Any]:
    """Recompute the derived fields of a record from the evidence already in it.

    Used by `reconcile`: it never invents evidence, it only re-derives ``status``,
    ``mastery_estimate`` and the counters that the stored ``retention_evidence`` implies. A
    hand-edited status or counter is overwritten by what the evidence actually supports, which is
    the point.
    """
    moment = now or utc_now_iso()
    updated = copy.deepcopy(record)
    evidence = [entry for entry in updated.get("retention_evidence") or [] if isinstance(entry, dict)]
    updated["independent_successes"] = sum(1 for entry in evidence if is_independent(entry))
    hint_usage = updated.setdefault(
        "hint_usage", {"total_hints": 0, "hinted_successes": 0, "last_hint_level": None}
    )
    hint_usage["total_hints"] = sum(int(entry.get("hints_used") or 0) for entry in evidence)
    hint_usage["hinted_successes"] = sum(
        1 for entry in evidence if entry.get("outcome") == "hinted_success"
    )
    if evidence:
        updated["last_attempted_at"] = str(evidence[-1].get("at"))
        successes = [
            str(entry.get("at"))
            for entry in evidence
            if entry.get("outcome") in SUCCESS_OUTCOMES and entry.get("at")
        ]
        updated["last_successful_retrieval_at"] = max(successes) if successes else None
    updated["status"] = next_status(updated, now=moment, blocking_misconception=blocks_promotion)
    updated["mastery_estimate"] = mastery_estimate(
        updated, now=moment, open_misconceptions=open_misconceptions
    )
    return updated


# ------------------------------------------------------------------------------ misconceptions


def next_misconception_id(data: dict[str, Any] | None, subject_id: str) -> str:
    """The next free ``<subject_id>:mc-NNN`` id for a subject."""
    highest = 0
    for entry in (data or {}).get("misconceptions", []):
        if not isinstance(entry, dict):
            continue
        mid = str(entry.get("misconception_id") or "")
        suffix = mid.rsplit("mc-", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{subject_id}:mc-{highest + 1:03d}"


def apply_misconception(
    data: dict[str, Any],
    *,
    subject_id: str,
    description: str,
    misconception_id: str | None = None,
    concept_ids: Iterable[str] = (),
    status: str = "open",
    learner_statement: str | None = None,
    correct_statement: str | None = None,
    detection_signals: Iterable[str] = (),
    remediation: Iterable[str] = (),
    at: str | None = None,
    session_id: str | None = None,
    event_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add or update one misconception record. Returns ``(misconceptions, entry)``.

    Reuse beats proliferation: when ``misconception_id`` names an existing record this appends an
    occurrence and moves the status, rather than creating a near-duplicate of a wrong model the
    learner already has a history with.
    """
    if status not in ("open", "monitoring", "resolved"):
        raise LearningOSUsageError(
            f"unknown misconception status {status!r}",
            remedy="Use open, monitoring or resolved.",
        )
    moment = at or utc_now_iso()
    updated = copy.deepcopy(data)
    entries = updated.setdefault("misconceptions", [])
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and item.get("misconception_id") == misconception_id
        ),
        None,
    )
    if entry is None:
        entry = {
            "misconception_id": misconception_id or next_misconception_id(updated, subject_id),
            "concept_ids": [],
            "description": description,
            "learner_statement_example": learner_statement,
            "correct_statement": correct_statement,
            "detection_signals": list(detection_signals),
            "remediation": list(remediation),
            "occurrences": [],
            "status": status,
            "first_seen_at": moment,
            "last_seen_at": moment,
            "resolved_at": None,
        }
        entries.append(entry)
    else:
        entry["description"] = description or entry.get("description")
        if learner_statement is not None:
            entry["learner_statement_example"] = learner_statement
        if correct_statement is not None:
            entry["correct_statement"] = correct_statement
        for signal in detection_signals:
            if signal not in entry.setdefault("detection_signals", []):
                entry["detection_signals"].append(signal)
        for step in remediation:
            if step not in entry.setdefault("remediation", []):
                entry["remediation"].append(step)
        entry["status"] = status
        entry["last_seen_at"] = moment

    for concept_id in concept_ids:
        if concept_id not in entry.setdefault("concept_ids", []):
            entry["concept_ids"].append(str(concept_id))
    entry["concept_ids"].sort()
    occurrence: dict[str, Any] = {"at": moment, "session_id": session_id, "event_id": event_id}
    first_concept = next(iter(concept_ids), None)
    if first_concept:
        occurrence["concept_id"] = str(first_concept)
    entry.setdefault("occurrences", []).append(occurrence)
    entry["resolved_at"] = moment if status == "resolved" else None
    entries.sort(key=lambda item: str(item.get("misconception_id")))
    return updated, entry


# -------------------------------------------------------------------- the one writing wrapper


@dataclass
class AttemptResult:
    """What :func:`record_attempt_and_save` wrote, so a caller can report it honestly."""

    concept_id: str
    record: dict[str, Any]
    status_before: str | None
    status_after: str
    mastery_before: float | None
    mastery_after: float | None
    queue_item: dict[str, Any]
    event_ids: list[str] = field(default_factory=list)

    @property
    def promoted(self) -> bool:
        return self.status_before != self.status_after


def record_attempt_and_save(
    ws: Workspace,
    subject_id: str,
    concept_id: str,
    *,
    session_id: str,
    outcome: str,
    hints_used: int = 0,
    novelty: str | None = None,
    difficulty: int | None = None,
    delay_days: float | None = None,
    at: str | None = None,
    mistake_pattern: str | None = None,
    question_ref: str | None = None,
    response_seconds: float | None = None,
    failure_mode: str | None = None,
    confidence_estimate: float | None = None,
    next_action: str | None = None,
    notes: str | None = None,
    actor: str = "agent",
    schedule: bool = True,
) -> AttemptResult:
    """Record one judged attempt end to end: events first, then snapshots.

    Three events are appended before anything is written to a snapshot
    (``answer_evaluated`` → ``mastery_updated`` → ``revision_scheduled``), then the concept
    record, the revision queue and the subject's derived counters are saved. That ordering is not
    cosmetic: the event log is the only layer that cannot be rebuilt, so it is always written
    first, and writing every event before every snapshot keeps the newest evidence from looking
    newer than the state that already reflects it.
    """
    store.require_subject(ws, subject_id)
    record = store.get_concept_record(ws, subject_id, concept_id)
    if record is None:
        raise LearningOSUsageError(
            f"concept {concept_id!r} has no record in subject {subject_id!r}",
            remedy="Seed it first with memory.store.new_concept_record() / put_concept_record(), "
            "or check the id — the prefix must be the subject id.",
        )
    misconceptions = store.load_misconceptions(ws, subject_id, required=False)
    open_count = open_misconception_count(record, misconceptions)
    blocks = blocking_misconception(record, misconceptions)

    outcome = _check_outcome(outcome)
    hints = max(0, int(hints_used or 0))
    stored_outcome = "hinted_success" if outcome == "independent_success" and hints else outcome
    moment = at or utc_now_iso()

    answer_event = events_mod.append_event(
        ws,
        type="answer_evaluated",
        session_id=session_id,
        subject_id=subject_id,
        concept_ids=[concept_id],
        actor=actor,
        at=moment,
        payload={
            "question_ref": question_ref,
            "outcome": stored_outcome,
            "independent": stored_outcome == "independent_success" and hints == 0,
            "hints_used": hints,
            "difficulty": difficulty,
            "novelty": novelty,
            "delay_days": delay_days,
            "response_seconds": response_seconds,
            "failure_mode": failure_mode,
            "mistake_pattern": mistake_pattern,
            "confidence_estimate": confidence_estimate,
            "notes": notes,
        },
    )
    status_before = str(record.get("status") or "unassessed")
    mastery_before = record.get("mastery_estimate")
    updated = record_attempt(
        record,
        outcome=outcome,
        hints_used=hints,
        novelty=novelty,
        difficulty=difficulty,
        delay_days=delay_days,
        at=moment,
        mistake_pattern=mistake_pattern,
        session_id=session_id,
        event_id=answer_event["event_id"],
        next_action=next_action,
        confidence_estimate=confidence_estimate,
        open_misconceptions=open_count,
        blocks_promotion=blocks,
    )
    queue = store.load_revision_queue(ws, subject_id, required=False) or new_revision_queue(
        subject_id, at=moment
    )
    item = schedule_review(
        concept_id,
        stored_outcome,
        previous=queue_item_for(queue, concept_id),
        now=moment,
        ladder=ladder_for(queue),
    )
    if schedule:
        updated["review_due_at"] = item["due_at"]

    mastery_event = events_mod.append_event(
        ws,
        type="mastery_updated",
        session_id=session_id,
        subject_id=subject_id,
        concept_ids=[concept_id],
        actor="tool",
        at=moment,
        payload={
            "concept_id": concept_id,
            "status_before": status_before,
            "status_after": updated["status"],
            "mastery_before": mastery_before,
            "mastery_after": updated["mastery_estimate"],
            "independent_successes": updated["independent_successes"],
            "reason": f"{stored_outcome} with {hints} hint(s)",
        },
    )
    event_ids = [answer_event["event_id"], mastery_event["event_id"]]
    if schedule:
        revision_event = events_mod.append_event(
            ws,
            type="revision_scheduled",
            session_id=session_id,
            subject_id=subject_id,
            concept_ids=[concept_id],
            actor="tool",
            at=moment,
            payload={
                "concept_id": concept_id,
                "due_at": item["due_at"],
                "interval_days": item["interval_days"],
                "step_index": item["step_index"],
                "reason": item["reason"],
            },
        )
        event_ids.append(revision_event["event_id"])

    store.put_concept_record(ws, subject_id, updated)
    if schedule:
        upsert_queue_item(queue, item)
        store.save_revision_queue(ws, subject_id, queue)
    refresh_subject_counters(
        ws, subject_id, session_id=session_id, concept_id=concept_id, at=moment
    )

    return AttemptResult(
        concept_id=concept_id,
        record=updated,
        status_before=status_before,
        status_after=str(updated["status"]),
        mastery_before=mastery_before,
        mastery_after=updated["mastery_estimate"],
        queue_item=item,
        event_ids=event_ids,
    )


def refresh_subject_counters(
    ws: Workspace,
    subject_id: str,
    *,
    session_id: str | None = None,
    concept_id: str | None = None,
    at: str | None = None,
) -> dict[str, Any] | None:
    """Keep ``subject_state.json``'s derived counters in step with the concept records.

    Only derived counters move here. ``session_count`` and ``total_minutes`` belong to the session
    boundary, not to a single attempt. Doing this whenever evidence is recorded — rather than only
    at session end — is what stops every answer from leaving a `stale_state` warning behind, and
    `reconcile` calls it for the same reason after it re-derives a record.
    """
    state = store.load_subject_state(ws, subject_id, required=False)
    if state is None:
        return None
    mastery = store.load_concept_mastery(ws, subject_id, required=False)
    counts = store.status_counts(mastery)
    coverage = dict(state.get("coverage") or {})
    coverage["touched"] = sum(value for key, value in counts.items() if key != "unassessed")
    coverage["practising"] = counts["practising"]
    coverage["provisionally_mastered"] = counts["provisionally_mastered"]
    state["coverage"] = coverage
    if session_id:
        state["last_session_id"] = session_id
    if at:
        state["last_session_at"] = at
    if concept_id:
        active = [cid for cid in state.get("active_concept_ids") or [] if cid != concept_id]
        state["active_concept_ids"] = sorted([*active, concept_id])
    return store.save_subject_state(ws, subject_id, state)
