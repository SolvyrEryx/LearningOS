"""Test C — persistence.

The claim being tested is the one the whole system rests on: what the store writes is exactly what
a later session reads back, from a cold start, with nothing carried over in memory. Floats,
timestamps, nested structures and the event log all have to survive the round trip, and a crash
must never leave a half-written file behind.
"""

from __future__ import annotations

import json

import pytest

from common import LearningOSDataError, Workspace, utc_now_iso
from memory import events as events_mod
from memory import store

RICH_RECORD_FIELDS = {
    "mastery_estimate": 0.42,
    "confidence_estimate": 0.6,
    "independent_successes": 2,
    "next_action": "Re-derive a0 for a square wave with no notes.",
}


def build_record(subject_id: str = "algo") -> dict:
    record = store.new_concept_record(
        f"{subject_id}:fourier",
        "Fourier series",
        subject_id=subject_id,
        unit="unit-3",
        prerequisite_ids=[],
        source_refs=[{"source_id": "textbook-1a2b3c4d", "locator": {"page": 212}, "note": None}],
    )
    record.update(RICH_RECORD_FIELDS)
    record["status"] = "practising"
    record["last_attempted_at"] = "2026-02-11T09:14:00Z"
    record["last_successful_retrieval_at"] = None
    record["hint_usage"] = {"total_hints": 3, "hinted_successes": 2, "last_hint_level": 2}
    record["recurring_mistakes"] = [
        {
            "pattern": "drops the 1/2 a0 term",
            "count": 2,
            "first_seen_at": "2026-02-09T09:00:00Z",
            "last_seen_at": "2026-02-11T09:14:00Z",
        }
    ]
    record["helpful_interventions"] = ["worked-example-fading", "diagram:spectrum"]
    record["failed_interventions"] = ["formal-definition-first"]
    record["retention_evidence"] = [
        {
            "at": "2026-02-11T09:14:00Z",
            "delay_days": 2,
            "outcome": "hinted_success",
            "difficulty": 3,
            "novelty": "variant",
            "hints_used": 1,
            "session_id": "2026-02-11-algo-01",
            "event_id": "9f1c2d3e4f5061728394a5b6c7d8e9f0",
        }
    ]
    record["review_due_at"] = "2026-02-14T00:00:00Z"
    return record


def test_a_concept_record_round_trips_field_for_field(ws, cli):
    assert cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet").code == 0
    written = build_record()
    store.put_concept_record(Workspace.at(ws), "algo", json.loads(json.dumps(written)))

    # Cold start: a brand new Workspace object, nothing reused from the write path.
    reloaded = store.get_concept_record(Workspace.at(ws), "algo", "algo:fourier")
    assert reloaded is not None
    for key, value in written.items():
        if key == "updated_at":
            continue  # bumped by the write, by design
        assert reloaded[key] == value, key
    assert isinstance(reloaded["mastery_estimate"], float)
    assert reloaded["mastery_estimate"] == 0.42
    assert reloaded["updated_at"].endswith("Z")


def test_subject_state_and_queue_round_trip(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    state = store.load_subject_state(workspace, "algo")
    state["session_count"] = 3
    state["total_minutes"] = 92.5
    state["active_concept_ids"] = ["algo:fourier"]
    state["goals"] = ["pass the May exam"]
    state["coverage"] = {"planned": 34, "touched": 5, "practising": 2, "provisionally_mastered": 1}
    store.save_subject_state(workspace, "algo", state)

    queue = store.load_revision_queue(workspace, "algo")
    queue["items"] = [
        {
            "concept_id": "algo:fourier",
            "due_at": "2026-02-14T00:00:00Z",
            "interval_days": 3,
            "step_index": 1,
            "reason": "spacing",
            "last_outcome": "hinted_success",
            "attempts": 2,
        }
    ]
    store.save_revision_queue(workspace, "algo", queue)

    fresh = Workspace.at(ws)
    reloaded_state = store.load_subject_state(fresh, "algo")
    assert reloaded_state["total_minutes"] == 92.5
    assert reloaded_state["coverage"]["planned"] == 34
    assert reloaded_state["active_concept_ids"] == ["algo:fourier"]
    reloaded_queue = store.load_revision_queue(fresh, "algo")
    assert reloaded_queue["items"][0]["step_index"] == 1
    assert reloaded_queue["items"][0]["due_at"] == "2026-02-14T00:00:00Z"


def test_events_round_trip_and_the_line_count_matches(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    appended = [
        events_mod.append_event(
            workspace,
            type="session_started",
            session_id="2026-02-11-algo-01",
            subject_id="algo",
            payload={"planned_minutes": 45, "goals": ["unit 3"]},
        ),
        events_mod.append_event(
            workspace,
            type="answer_evaluated",
            session_id="2026-02-11-algo-01",
            subject_id="algo",
            concept_ids=["algo:fourier"],
            payload={
                "outcome": "hinted_success",
                "independent": False,
                "hints_used": 1,
                "difficulty": 3,
                "novelty": "variant",
                "response_seconds": 180,
                "notes": "needed the orthogonality reminder",
            },
        ),
        events_mod.append_event(
            workspace,
            type="session_ended",
            session_id="2026-02-11-algo-01",
            subject_id="algo",
            payload={"duration_minutes": 45, "concepts_touched": ["algo:fourier"]},
        ),
    ]

    fresh = Workspace.at(ws)
    read_back, findings = events_mod.read_events(fresh, session_id="2026-02-11-algo-01")
    assert findings == []
    assert read_back == appended, "events must round-trip byte-for-byte in content"

    raw_lines = [
        line
        for line in fresh.event_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # one subject_initialized from init-subject plus the three appended here
    assert len(raw_lines) == 4
    valid, corrupt = events_mod.count_events(fresh)
    assert (valid, corrupt) == (4, 0)
    for line in raw_lines:
        assert json.loads(line)["schema_version"] == 1


def test_each_event_is_one_line_with_no_pretty_printing(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    events_mod.append_event(
        workspace,
        type="open_question_logged",
        session_id="2026-02-11-algo-01",
        subject_id="algo",
        payload={"question": "Why does merge sort need extra space?\nAnd when does that matter?"},
    )
    text = workspace.event_log.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\r" not in text
    for line in text.splitlines():
        assert line.startswith("{") and line.endswith("}")
        assert ": " not in line.split('"payload"')[0], "compact separators, not pretty-printed"


def test_interleaved_appends_from_two_writer_calls_all_survive(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    first = Workspace.at(ws)
    second = Workspace.at(ws)
    for index in range(5):
        events_mod.append_event(
            first,
            type="hint_given",
            session_id="2026-02-11-algo-01",
            subject_id="algo",
            payload={"level": 1, "notes": f"writer-a-{index}"},
        )
        events_mod.append_event(
            second,
            type="hint_given",
            session_id="2026-02-11-algo-01",
            subject_id="algo",
            payload={"level": 2, "notes": f"writer-b-{index}"},
        )
    events, findings = events_mod.read_events(Workspace.at(ws), types=["hint_given"])
    assert findings == []
    assert len(events) == 10
    notes = {event["payload"]["notes"] for event in events}
    assert len(notes) == 10
    assert len({event["event_id"] for event in events}) == 10


def test_a_checkpoint_round_trips_including_fingerprints(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    store.put_concept_record(workspace, "algo", build_record())
    checkpoint = store.build_checkpoint(
        workspace,
        subject_id="algo",
        session_id="2026-02-11-algo-01",
        where_we_stopped="Mid-derivation of a0 for a square wave; integral set up, 1/2 dropped.",
        next_action="Re-derive a0 with no notes, then check against the textbook.",
        concepts_in_flight=[
            {"concept_id": "algo:fourier", "status": "practising", "fading_rung": "partial"}
        ],
        open_questions=["Why the 1/2 on a0?"],
        trigger="learner_stopping",
        agent="pytest",
    )
    path = store.write_checkpoint(workspace, checkpoint)
    assert path.is_file()

    fresh = Workspace.at(ws)
    found = store.latest_checkpoint(fresh, "algo")
    assert found is not None
    _found_path, reloaded = found
    assert reloaded == checkpoint
    assert reloaded["state_fingerprints"]["03_MEMORY/subjects/algo/concept_mastery.json"].startswith(
        "sha256:"
    )
    assert store.check_fingerprints(fresh, reloaded) == []

    # Memory moves on after a checkpoint; that is a warning, never a failure.
    moved_on = build_record()
    moved_on["independent_successes"] = 3
    store.put_concept_record(fresh, "algo", moved_on)
    warnings = store.check_fingerprints(fresh, reloaded)
    assert [finding.severity for finding in warnings] == ["warning"]
    assert warnings[0].code == "fingerprint_mismatch"


def test_a_checkpoint_needs_a_concrete_handover(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    with pytest.raises(LearningOSDataError):
        store.build_checkpoint(
            workspace,
            subject_id="algo",
            session_id="s1",
            where_we_stopped="   ",
            next_action="something",
        )
    with pytest.raises(LearningOSDataError):
        store.build_checkpoint(
            workspace,
            subject_id="algo",
            session_id="s1",
            where_we_stopped="something",
            next_action="",
        )


def test_no_temporary_or_partial_files_remain_anywhere_under_memory(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    store.put_concept_record(workspace, "algo", build_record())
    events_mod.append_event(
        workspace,
        type="mastery_updated",
        session_id="2026-02-11-algo-01",
        subject_id="algo",
        payload={"concept_id": "algo:fourier", "status_after": "practising"},
    )
    store.write_checkpoint(
        workspace,
        store.build_checkpoint(
            workspace,
            subject_id="algo",
            session_id="2026-02-11-algo-01",
            where_we_stopped="stopped here",
            next_action="start there",
        ),
    )
    leftovers = [
        str(path.relative_to(ws))
        for path in (ws / "03_MEMORY").rglob("*")
        if path.is_file() and (path.suffix == ".tmp" or path.name.startswith("."))
        and path.name != ".gitkeep"
    ]
    assert leftovers == []


def test_timestamps_are_second_precision_utc(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    record = store.put_concept_record(workspace, "algo", build_record())
    for value in (record["updated_at"], utc_now_iso()):
        assert value.endswith("Z")
        assert len(value) == 20
        assert "." not in value


def test_stored_paths_are_workspace_relative_with_forward_slashes(ws, cli):
    cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet")
    workspace = Workspace.at(ws)
    state = store.load_workspace_state(workspace)
    entry = next(item for item in state["subjects"] if item["subject_id"] == "algo")
    for key in ("knowledge_path", "memory_path"):
        assert "\\" not in entry[key]
        assert ":" not in entry[key]
        assert not entry[key].startswith("/")
