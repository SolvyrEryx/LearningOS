"""Test D — evidence, not accuracy.

The rule under test is the one the whole system turns on: **mastery rises only on unaided
performance**. A learner who answers three questions perfectly with a hint each time has 100 %
raw accuracy and zero evidence of independence, and the ladder must say so.

Everything here goes through `memory.mastery`, so the ladder, the heuristic and the spacing ladder
are tested as the CLI and the agent actually use them, including the events they append.
"""

from __future__ import annotations

import pytest

from common import EXIT_OK, Workspace
from memory import events as events_mod
from memory import mastery
from memory import store

SESSION = "2026-02-11-algo-01"
CONCEPT = "algo:sorting"

#: Explicit timestamps rather than wall-clock, so the delay and decay assertions are exact.
T = [
    "2026-02-01T09:00:00Z",
    "2026-02-02T09:00:00Z",
    "2026-02-03T09:00:00Z",
    "2026-02-06T09:00:00Z",
    "2026-02-10T09:00:00Z",
    "2026-02-14T09:00:00Z",
]


@pytest.fixture
def algo(ws, seed_subject) -> Workspace:
    seed_subject("algo", slugs=("sorting", "recursion"))
    return Workspace.at(ws)


def attempt(workspace: Workspace, concept_id: str, outcome: str, **kwargs):
    return mastery.record_attempt_and_save(
        workspace,
        "algo",
        concept_id,
        session_id=SESSION,
        outcome=outcome,
        **kwargs,
    )


def record(workspace: Workspace, concept_id: str = CONCEPT) -> dict:
    found = store.get_concept_record(workspace, "algo", concept_id)
    assert found is not None
    return found


# -------------------------------------------------------- hinted success is never independence


def test_three_hinted_successes_are_not_independence(algo):
    """100 % raw accuracy with a hint every time is explicitly not enough."""
    for index in range(3):
        attempt(
            algo,
            CONCEPT,
            "hinted_success",
            hints_used=1,
            novelty="variant",
            difficulty=3,
            at=T[index],
        )
    current = record(algo)
    assert current["independent_successes"] == 0, "a hinted success never counts as independent"
    assert current["status"] != "provisionally_mastered"
    assert current["status"] == "practising", "two hinted successes do move it off `developing`"
    assert current["hint_usage"]["hinted_successes"] == 3
    assert current["hint_usage"]["total_hints"] == 3
    assert len(current["retention_evidence"]) == 3
    assert all(entry["outcome"] == "hinted_success" for entry in current["retention_evidence"])


def test_an_independent_success_reported_with_hints_is_stored_as_hinted(algo):
    """The counter cannot be reached by relabelling a hinted attempt."""
    attempt(algo, CONCEPT, "independent_success", hints_used=2, novelty="novel", at=T[0])
    current = record(algo)
    assert current["independent_successes"] == 0
    assert current["retention_evidence"][-1]["outcome"] == "hinted_success"
    assert current["retention_evidence"][-1]["hints_used"] == 2
    events, _findings = events_mod.read_events(algo, types=["answer_evaluated"])
    assert events[-1]["payload"]["independent"] is False
    assert events[-1]["payload"]["outcome"] == "hinted_success"


# ------------------------------------------------------------------------------ the full ladder


def test_two_spaced_unaided_successes_promote_then_a_fail_demotes(algo):
    for index in range(3):
        attempt(algo, CONCEPT, "hinted_success", hints_used=1, novelty="variant", at=T[index])
    assert record(algo)["status"] == "practising"

    masteries = []
    for index in (3, 4):
        attempt(
            algo,
            CONCEPT,
            "independent_success",
            hints_used=0,
            novelty="variant",
            difficulty=3,
            delay_days=3,
            at=T[index],
        )
        masteries.append(record(algo)["mastery_estimate"])

    promoted = record(algo)
    assert promoted["independent_successes"] == 2
    assert promoted["status"] == "provisionally_mastered"
    assert masteries[0] < masteries[1], "mastery rises across the unaided successes"
    assert all(value is not None and value <= 1.0 for value in masteries)

    queue = store.load_revision_queue(algo, "algo")
    item = mastery.queue_item_for(queue, CONCEPT)
    assert item["step_index"] == 2, "each unaided success advances one ladder step"
    assert item["interval_days"] == 7

    attempt(algo, CONCEPT, "fail", hints_used=0, novelty="variant", delay_days=4, at=T[5])
    failed = record(algo)
    assert failed["status"] == "needs_review"
    assert failed["independent_successes"] == 2, "a fail does not erase past unaided evidence"
    assert failed["mastery_estimate"] < masteries[1]

    queue = store.load_revision_queue(algo, "algo")
    item = mastery.queue_item_for(queue, CONCEPT)
    assert item["step_index"] == 0, "a fail resets the revision ladder to step 0"
    assert item["interval_days"] == 1
    assert item["last_outcome"] == "fail"


def test_familiar_or_unspaced_unaided_successes_do_not_promote(algo):
    """Two easy repetitions of the same familiar item are not evidence of mastery."""
    for index in (0, 1):
        attempt(
            algo,
            CONCEPT,
            "independent_success",
            novelty="familiar",
            delay_days=0.1,
            at=T[index],
        )
    current = record(algo)
    assert current["independent_successes"] == 2
    assert current["status"] == "practising", "familiar and unspaced is not promotion evidence"


def test_a_repeated_mistake_pattern_forces_needs_review(algo):
    concept_id = "algo:recursion"
    attempt(
        algo,
        concept_id,
        "partial",
        novelty="variant",
        mistake_pattern="forgets the base case",
        at=T[0],
    )
    assert record(algo, concept_id)["status"] == "developing"
    attempt(
        algo,
        concept_id,
        "partial",
        novelty="variant",
        mistake_pattern="forgets the base case",
        at=T[1],
    )
    current = record(algo, concept_id)
    assert current["recurring_mistakes"][0]["count"] == 2
    assert current["status"] == "needs_review"


def test_an_overdue_review_moves_a_concept_to_needs_review(algo):
    current = record(algo)
    current["review_due_at"] = "2020-01-01T00:00:00Z"
    current["last_attempted_at"] = "2019-12-01T00:00:00Z"
    assert mastery.next_status(current, now="2026-02-01T09:00:00Z") == "needs_review"


# ---------------------------------------------------------------------- the heuristic in detail


def test_mastery_estimate_is_none_without_evidence_and_bounded_with_it(algo):
    fresh = store.new_concept_record("algo:fresh", "Fresh", subject_id="algo")
    assert mastery.mastery_estimate(fresh) is None
    for index in range(3):
        attempt(algo, CONCEPT, "independent_success", novelty="novel", delay_days=3, at=T[index])
    value = record(algo)["mastery_estimate"]
    assert 0.0 <= value <= 1.0, "novel unaided work is weighted above 1.0 and then clamped"
    assert value == 1.0


def test_a_recent_open_misconception_lowers_the_estimate_and_holds_promotion(algo):
    """A wrong model seen inside the last two attempts holds the promotion back.

    The ladder's condition is "no unresolved linked misconception **seen in the last 2
    attempts**", so an old occurrence that has not resurfaced does not block for ever — but a
    current one does, and it costs 0.05 of the estimate while it is open.
    """
    attempt(algo, CONCEPT, "independent_success", novelty="variant", delay_days=3, at=T[1])

    misconceptions, entry = mastery.apply_misconception(
        store.load_misconceptions(algo, "algo"),
        subject_id="algo",
        description="thinks a stable sort means an in-place sort",
        concept_ids=[CONCEPT],
        status="open",
        at=T[2],
    )
    store.save_misconceptions(algo, "algo", misconceptions)
    assert entry["misconception_id"] == "algo:mc-001"
    current = record(algo)
    current["misconception_ids"] = [entry["misconception_id"]]
    store.put_concept_record(algo, "algo", current)

    attempt(algo, CONCEPT, "independent_success", novelty="variant", delay_days=3, at=T[2])
    held = record(algo)
    assert held["independent_successes"] == 2
    assert held["status"] == "practising", "an unresolved misconception holds the promotion"
    unpenalised = mastery.mastery_estimate(held, now=T[2], open_misconceptions=0)
    assert held["mastery_estimate"] == pytest.approx(unpenalised - 0.05, abs=0.011)

    misconceptions, _resolved = mastery.apply_misconception(
        store.load_misconceptions(algo, "algo"),
        subject_id="algo",
        misconception_id=entry["misconception_id"],
        description=entry["description"],
        concept_ids=[CONCEPT],
        status="resolved",
        at=T[3],
    )
    store.save_misconceptions(algo, "algo", misconceptions)
    attempt(algo, CONCEPT, "independent_success", novelty="variant", delay_days=3, at=T[4])
    promoted = record(algo)
    assert promoted["status"] == "provisionally_mastered"
    assert promoted["mastery_estimate"] > held["mastery_estimate"]


def test_the_spacing_ladder_advances_holds_and_resets():
    ladder = mastery.LADDER_DAYS
    assert mastery.next_step_index(None, "independent_success", ladder=ladder) == 0
    assert mastery.next_step_index(0, "independent_success", ladder=ladder) == 1
    assert mastery.next_step_index(1, "hinted_success", ladder=ladder) == 1
    assert mastery.next_step_index(3, "partial", ladder=ladder) == 0
    assert mastery.next_step_index(3, "fail", ladder=ladder) == 0
    assert mastery.next_step_index(4, "independent_success", ladder=ladder) == 4, "the top holds"


# ------------------------------------------------------------------------------- the whole loop


def test_recording_attempts_leaves_the_workspace_valid_and_the_events_ordered(algo, cli):
    attempt(algo, CONCEPT, "hinted_success", hints_used=1, novelty="variant", at=T[0])
    attempt(algo, CONCEPT, "independent_success", novelty="variant", delay_days=3, at=T[1])

    recorded, _findings = events_mod.read_events(algo)
    types = [event["type"] for event in recorded]
    assert types.count("answer_evaluated") == 2
    assert types.count("mastery_updated") == 2
    assert types.count("revision_scheduled") == 2
    # Evidence before state: every event for an attempt is appended before any snapshot is written.
    assert types.index("answer_evaluated") < types.index("mastery_updated")

    result = cli("validate")
    assert result.code == EXIT_OK, result.text
    assert "stale_state" not in result.out, "the snapshots were written after the events"
