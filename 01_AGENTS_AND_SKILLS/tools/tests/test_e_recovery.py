"""Test E — recovering from an interrupted session.

The question this answers is the one that decides whether the system is usable at all: a session
was cut off mid-derivation, the conversation is gone, and the only thing left is the folder. Can
the next session pick the thread up from the files?

So: a checkpoint, no ``session_ended``, then `resume`. The briefing has to name the subject, where
the work stopped, the concept that was in flight, the exact next action and how many reviews are
due. When there is no checkpoint at all, `resume` must say so and say what to do instead rather
than invent a plan.
"""

from __future__ import annotations

import pytest

from common import (
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_VALIDATION,
    SCHEMA_VERSION,
    Workspace,
    sha256_file,
    utc_now_iso,
)
from memory import events as events_mod
from memory import mastery
from memory import store

SESSION = "2026-02-11-algo-01"
CONCEPT = "algo:sorting"
STOPPED = "mid-derivation of the merge step; learner had the recurrence but dropped the n log n"
NEXT_ACTION = "re-derive the merge-sort recurrence unaided, no notes"


@pytest.fixture
def interrupted(ws, cli, seed_subject) -> Workspace:
    """A session that was cut off: evidence and a checkpoint, but no session_ended event."""
    seed_subject("algo", slugs=("sorting", "recursion"))
    workspace = Workspace.at(ws)
    events_mod.append_event(
        workspace,
        type="session_started",
        session_id=SESSION,
        subject_id="algo",
        actor="agent",
        payload={"agent": "kiro", "planned_minutes": 45},
    )
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        CONCEPT,
        session_id=SESSION,
        outcome="hinted_success",
        hints_used=2,
        novelty="variant",
        difficulty=3,
        next_action=NEXT_ACTION,
    )
    # An overdue review, written with a date in the past so "due" is not a question of today.
    queue = store.load_revision_queue(workspace, "algo")
    mastery.upsert_queue_item(
        queue,
        {
            "concept_id": "algo:recursion",
            "due_at": "2020-01-01T00:00:00Z",
            "interval_days": 1,
            "step_index": 0,
            "reason": "overdue on purpose, so the briefing has something to count",
            "last_outcome": "hinted_success",
            "attempts": 1,
        },
    )
    store.save_revision_queue(workspace, "algo", queue)
    result = cli(
        "checkpoint",
        "--subject",
        "algo",
        "--session-id",
        SESSION,
        "--note",
        STOPPED,
        "--next-action",
        NEXT_ACTION,
        "--concept",
        CONCEPT,
        "--open-question",
        "Why is the recurrence not n^2 if every level copies the whole array?",
        "--trigger",
        "learner_stopping",
    )
    assert result.code == EXIT_OK, result.text
    return workspace


# --------------------------------------------------------------------------------- the briefing


def test_resume_after_an_interrupted_session_brings_everything_back(interrupted, cli):
    result = cli("resume")
    assert result.code == EXIT_OK, result.text
    out = result.out
    assert "algo" in out
    assert STOPPED in out, "where_we_stopped is the whole point of a checkpoint"
    assert CONCEPT in out, "the in-flight concept"
    assert NEXT_ACTION in out, "the exact next action"
    assert "due reviews:  1" in out, "the due-review count"
    assert "[interrupted]" in out, "no session_ended event was recorded for this session"
    assert "recurrence not n^2" in out, "the open question carried in the checkpoint"
    assert SESSION in out


def test_resume_json_exposes_the_same_briefing(interrupted, cli):
    import json

    result = cli("resume", "--json")
    assert result.code == EXIT_OK, result.text
    payload = json.loads(result.out)
    assert payload["subject_id"] == "algo"
    assert payload["where_we_stopped"] == STOPPED
    assert payload["next_action"] == NEXT_ACTION
    assert payload["due_review_count"] == 1
    assert payload["session_was_closed"] is False
    assert [concept["concept_id"] for concept in payload["concepts_in_flight"]] == [CONCEPT]
    assert payload["concepts_in_flight"][0]["status"] == "developing"
    assert len(payload["recent_events"]) <= 5
    assert payload["recent_events"][-1]["type"] == "checkpoint_written"


def test_resume_explicit_subject_matches_the_resolved_one(interrupted, cli):
    assert cli("resume", "--subject", "algo").out == cli("resume").out


def test_a_closed_session_is_reported_as_closed(interrupted, cli):
    events_mod.append_event(
        interrupted,
        type="session_ended",
        session_id=SESSION,
        subject_id="algo",
        actor="agent",
        payload={"duration_minutes": 42, "concepts_touched": [CONCEPT]},
    )
    result = cli("resume")
    assert result.code == EXIT_OK
    assert "[closed cleanly]" in result.out


# -------------------------------------------------------------------------- when there is no path


def test_resume_without_a_checkpoint_exits_four_and_names_the_remedy(ws, cli, seed_subject):
    seed_subject("algo")
    result = cli("resume")
    assert result.code == EXIT_NOT_FOUND
    assert "no checkpoint" in result.err
    assert "next_session.md" in result.err, "it must say what to do instead"
    assert "learningos.py checkpoint" in result.err


def test_resume_without_any_subject_exits_four(ws, cli):
    result = cli("resume")
    assert result.code == EXIT_NOT_FOUND
    assert "no subject" in result.err
    assert "init-subject" in result.err


def test_resume_with_an_unknown_subject_exits_four(interrupted, cli):
    result = cli("resume", "--subject", "nope")
    assert result.code == EXIT_NOT_FOUND
    assert "unknown subject" in result.err


# ------------------------------------------------------------------------------- fingerprints


def test_a_fingerprint_mismatch_warns_but_still_resumes(interrupted, cli):
    record = store.get_concept_record(interrupted, "algo", CONCEPT)
    record["next_action"] = "hand-edited since the checkpoint was written"
    store.put_concept_record(interrupted, "algo", record)

    result = cli("resume")
    assert result.code == EXIT_OK, "a changed file is a warning, never a failure"
    assert "has changed since this checkpoint was written" in result.out
    assert "concept_mastery.json" in result.out
    assert "verify the in-flight concepts with a probe" in result.out
    assert STOPPED in result.out, "the briefing is still printed"


def test_a_fingerprinted_file_that_vanished_is_reported(interrupted, cli):
    (interrupted.root / "03_MEMORY/subjects/algo/revision_queue.json").unlink()
    result = cli("resume")
    assert result.code == EXIT_OK
    assert "fingerprinted at checkpoint time but is missing now" in result.out


# -------------------------------------------------------------------------------- checkpointing


def test_checkpoint_writes_the_file_the_event_and_the_fingerprints(ws, cli, seed_subject):
    seed_subject("algo")
    workspace = Workspace.at(ws)
    result = cli(
        "checkpoint",
        "--subject",
        "algo",
        "--session-id",
        SESSION,
        "--note",
        STOPPED,
        "--next-action",
        NEXT_ACTION,
    )
    assert result.code == EXIT_OK, result.text
    path = ws / "03_MEMORY/checkpoints/algo" / f"{SESSION}.json"
    assert path.is_file()
    checkpoint = store.load_checkpoint(workspace, "algo", SESSION)
    assert checkpoint["where_we_stopped"] == STOPPED
    assert checkpoint["next_action"] == NEXT_ACTION
    assert checkpoint["schema_version"] == SCHEMA_VERSION
    fingerprints = checkpoint["state_fingerprints"]
    assert "03_MEMORY/subjects/algo/concept_mastery.json" in fingerprints
    for relative, expected in fingerprints.items():
        assert expected == f"sha256:{sha256_file(ws / relative)}"
    events, _findings = events_mod.read_events(workspace, types=["checkpoint_written"])
    assert events[-1]["payload"]["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert events[-1]["payload"]["relative_path"] == f"03_MEMORY/checkpoints/algo/{SESSION}.json"
    assert cli("validate").code == EXIT_OK


def test_a_checkpoint_with_an_empty_handover_is_refused(ws, cli, seed_subject):
    seed_subject("algo")
    result = cli(
        "checkpoint",
        "--subject",
        "algo",
        "--session-id",
        SESSION,
        "--note",
        "   ",
        "--next-action",
        NEXT_ACTION,
    )
    assert result.code == EXIT_VALIDATION
    assert "where_we_stopped" in result.err
    assert not (ws / "03_MEMORY/checkpoints/algo").exists(), "nothing was written"


def test_a_checkpoint_for_an_unknown_subject_exits_four(ws, cli, seed_subject):
    seed_subject("algo")
    result = cli(
        "checkpoint",
        "--subject",
        "nope",
        "--session-id",
        SESSION,
        "--note",
        STOPPED,
        "--next-action",
        NEXT_ACTION,
    )
    assert result.code == EXIT_NOT_FOUND
    assert "unknown subject" in result.err


def test_a_second_checkpoint_replaces_the_resume_point_and_keeps_the_history(interrupted, cli):
    later = "stopped again, this time after the first unaided attempt"
    result = cli(
        "checkpoint",
        "--subject",
        "algo",
        "--session-id",
        SESSION,
        "--note",
        later,
        "--next-action",
        NEXT_ACTION,
    )
    assert result.code == EXIT_OK, result.text
    checkpoint = store.load_checkpoint(interrupted, "algo", SESSION)
    assert checkpoint["where_we_stopped"] == later
    events, _findings = events_mod.read_events(interrupted, types=["checkpoint_written"])
    assert len(events) == 2, "the file is the current resume point; the log keeps both writes"
    assert cli("resume").out.count(later) >= 1


def test_the_newest_checkpoint_across_sessions_wins(interrupted, cli):
    newer = "2026-02-12-algo-01"
    assert (
        cli(
            "checkpoint",
            "--subject",
            "algo",
            "--session-id",
            newer,
            "--note",
            "second session stopped at the master theorem",
            "--next-action",
            "state all three cases unaided",
        ).code
        == EXIT_OK
    )
    result = cli("resume")
    assert newer in result.out
    assert "master theorem" in result.out


def test_resume_reads_the_plan_out_of_next_session_md(interrupted, cli):
    path = interrupted.root / "03_MEMORY/subjects/algo/next_session.md"
    text = path.read_text(encoding="utf-8").replace(
        '1. **First activity:** (be exact — "re-derive a0 for a triangle wave, no notes", not '
        '"continue\n   Fourier")',
        "1. **First activity:** re-derive the merge recurrence from scratch",
    )
    path.write_text(text, encoding="utf-8")
    result = cli("resume")
    assert result.code == EXIT_OK
    assert "re-derive the merge recurrence from scratch" in result.out


def test_checkpoints_do_not_count_as_state_changing_evidence(interrupted, cli):
    """A checkpoint is a resume point, not evidence, so it never triggers a drift warning."""
    result = cli("validate")
    assert result.code == EXIT_OK, result.text
    assert "stale_state" not in result.out


def test_the_whole_recovery_path_survives_a_fresh_workspace_object(interrupted):
    reloaded = Workspace.at(interrupted.root)
    found = store.latest_checkpoint(reloaded, "algo")
    assert found is not None
    _path, checkpoint = found
    assert checkpoint["where_we_stopped"] == STOPPED
    assert checkpoint["created_at"] <= utc_now_iso()
