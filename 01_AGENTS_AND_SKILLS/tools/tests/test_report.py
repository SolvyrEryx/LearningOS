"""The session log written by `session-report`.

A session log is only worth keeping if it cannot flatter. Every number in it is derived from the
event log, so these tests check the derivation (duration, independent versus hinted counts, hints
per concept, mastery deltas, misconceptions, scheduled reviews) and the non-destructive rule: an
existing report for a session is never silently replaced, because it is the only human-readable
account of what happened.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from common import EXIT_NOT_FOUND, EXIT_OK, Workspace, format_timestamp, sha256_file, utc_now
from memory import events as events_mod
from memory import mastery
from memory import report as report_mod
from memory import store

SESSION = "2026-02-11-algo-01"
SORTING = "algo:sorting"
RECURSION = "algo:recursion"


def at(offset_minutes: int) -> str:
    """Timestamps a little after 'now', so this session is unambiguously the newest one."""
    return format_timestamp(utc_now() + dt.timedelta(minutes=offset_minutes))


@pytest.fixture
def session(ws, cli, seed_subject) -> Workspace:
    """One complete session's worth of evidence: probes, hints, answers, a misconception, an end."""
    seed_subject("algo", slugs=("sorting", "recursion"))
    workspace = Workspace.at(ws)

    events_mod.append_event(
        workspace,
        type="session_started",
        session_id=SESSION,
        subject_id="algo",
        at=at(1),
        payload={"agent": "kiro", "planned_minutes": 45, "plan": "merge sort, then recursion"},
    )
    events_mod.append_event(
        workspace,
        type="explanation_given",
        session_id=SESSION,
        subject_id="algo",
        concept_ids=[SORTING],
        at=at(2),
        payload={"approach": "worked-example-fading", "rung": "concrete"},
    )
    events_mod.append_event(
        workspace,
        type="hint_given",
        session_id=SESSION,
        subject_id="algo",
        concept_ids=[SORTING],
        at=at(3),
        payload={"level": 2, "requested_by_learner": True},
    )
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        SORTING,
        session_id=SESSION,
        outcome="hinted_success",
        hints_used=1,
        novelty="variant",
        difficulty=3,
        at=at(4),
        notes="needed the recurrence written out",
    )
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        SORTING,
        session_id=SESSION,
        outcome="independent_success",
        novelty="variant",
        difficulty=3,
        delay_days=3,
        at=at(5),
        next_action="State the master theorem cases unaided.",
    )
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        RECURSION,
        session_id=SESSION,
        outcome="partial",
        novelty="novel",
        difficulty=4,
        at=at(6),
        mistake_pattern="forgets the base case",
    )
    events_mod.append_event(
        workspace,
        type="misconception_detected",
        session_id=SESSION,
        subject_id="algo",
        concept_ids=[RECURSION],
        at=at(7),
        payload={
            "misconception_id": "algo:mc-001",
            "description": "thinks recursion always needs a loop as well",
            "status_after": "open",
        },
    )
    events_mod.append_event(
        workspace,
        type="open_question_logged",
        session_id=SESSION,
        subject_id="algo",
        at=at(8),
        payload={"question": "Is tail recursion optimised here?", "raised_by": "learner"},
    )
    assert (
        cli(
            "checkpoint",
            "--subject",
            "algo",
            "--session-id",
            SESSION,
            "--note",
            "stopped after the first unaided merge-sort derivation",
            "--next-action",
            "State the master theorem cases unaided.",
            "--concept",
            SORTING,
        ).code
        == EXIT_OK
    )
    events_mod.append_event(
        workspace,
        type="session_ended",
        session_id=SESSION,
        subject_id="algo",
        at=at(10),
        payload={
            "duration_minutes": 42,
            "concepts_touched": [SORTING, RECURSION],
            "independent_successes": 1,
            "hinted_successes": 1,
            "outcome": "merge sort is now unaided; recursion still needs work",
        },
    )
    return workspace


def report_json(cli, *args) -> dict:
    result = cli("session-report", *args, "--json")
    assert result.code == EXIT_OK, result.text
    return json.loads(result.out)


# ------------------------------------------------------------------------------- the derivation


def test_the_report_counts_what_the_events_say(session, cli):
    payload = report_json(cli, "--last")
    assert payload["session_id"] == SESSION
    assert payload["subject_id"] == "algo"
    assert payload["duration_minutes"] == 42.0
    assert payload["completed"] is True
    assert payload["independent_successes"] == 1
    assert payload["hinted_successes"] == 1
    assert payload["outcomes"]["partial"] == 1
    assert payload["hints_given"] == 1
    assert payload["explanations"] == 1
    assert [concept["concept_id"] for concept in payload["concepts"]] == [RECURSION, SORTING]
    assert len(payload["misconceptions_raised"]) == 1
    assert payload["open_questions"] == ["Is tail recursion optimised here?"]
    assert len(payload["reviews_scheduled"]) == 3


def test_mastery_deltas_come_from_the_mastery_updated_events(session, cli):
    payload = report_json(cli, "--last")
    sorting = next(c for c in payload["concepts"] if c["concept_id"] == SORTING)
    assert sorting["attempts"] == 2
    assert sorting["independent"] == 1
    assert sorting["hinted"] == 1
    assert sorting["hints"] == 1
    assert sorting["status_before"] == "unassessed"
    assert sorting["status_after"] == "practising"
    assert sorting["mastery_before"] is None
    assert sorting["mastery_after"] > 0
    assert sorting["next_action"] == "State the master theorem cases unaided."


def test_mistake_patterns_are_carried_into_the_report(session, cli):
    payload = report_json(cli, "--last")
    recursion = next(c for c in payload["concepts"] if c["concept_id"] == RECURSION)
    assert recursion["mistakes"] == ["forgets the base case"]
    assert recursion["partial"] == 1


def test_the_duration_falls_back_to_the_event_span_when_none_was_reported(session, cli):
    """A session that was interrupted has no reported duration, so the span is used instead."""
    other = "2026-02-12-algo-01"
    events_mod.append_event(
        session,
        type="session_started",
        session_id=other,
        subject_id="algo",
        at=at(20),
        payload={},
    )
    events_mod.append_event(
        session,
        type="question_asked",
        session_id=other,
        subject_id="algo",
        concept_ids=[SORTING],
        at=at(50),
        payload={"question": "state case 2"},
    )
    payload = report_json(cli, "--session-id", other)
    assert payload["completed"] is False
    assert payload["duration_minutes"] == pytest.approx(30.0, abs=0.2)


# ------------------------------------------------------------------------------------ the file


def test_the_markdown_lands_in_session_logs_with_the_documented_name(session, cli, ws):
    payload = report_json(cli, "--last")
    expected = f"{payload['date']}-algo-{SESSION}.md"
    path = ws / "03_MEMORY/session_logs" / expected
    assert path.is_file(), sorted(p.name for p in (ws / "03_MEMORY/session_logs").iterdir())
    assert payload["path"] == f"03_MEMORY/session_logs/{expected}"
    text = path.read_text(encoding="utf-8")
    assert f"# Session {SESSION} — algo" in text
    assert "| Duration | 42 minutes |" in text
    assert "| Independent successes | 1 |" in text
    assert "| Hinted successes | 1 |" in text
    assert "algo:sorting" in text and "algo:recursion" in text
    assert "forgets the base case" in text
    assert "thinks recursion always needs a loop as well" in text
    assert "Is tail recursion optimised here?" in text
    assert "State the master theorem cases unaided." in text
    assert "Reviews scheduled" in text
    assert "evidence from the log" in text


def test_the_echoed_summary_names_the_counts(session, cli):
    result = cli("session-report", "--last")
    assert result.code == EXIT_OK, result.text
    assert "1 independent / 1 hinted" in result.out
    assert "03_MEMORY/session_logs/" in result.out


def test_an_existing_report_is_not_overwritten_without_force(session, cli, ws):
    first = report_json(cli, "--last")
    path = ws / first["path"]
    before = sha256_file(path)

    result = cli("session-report", "--last")
    assert result.code == EXIT_OK, result.text
    assert sha256_file(path) == before, "the earlier report is left exactly as it was"
    assert "left alone" in result.out
    sibling = path.with_name(f"{path.stem}-02{path.suffix}")
    assert sibling.is_file(), "the new report is written beside it"


def test_force_replaces_the_report_in_place(session, cli, ws):
    first = report_json(cli, "--last")
    path = ws / first["path"]
    logs = path.parent
    result = cli("session-report", "--last", "--force")
    assert result.code == EXIT_OK, result.text
    assert "--force" in result.out
    assert sorted(p.name for p in logs.iterdir() if p.suffix == ".md") == [path.name], (
        "--force replaces rather than adding a sibling"
    )


def test_stdout_only_writes_nothing(session, cli, ws, hash_tree):
    before = hash_tree(ws / "03_MEMORY")
    result = cli("session-report", "--last", "--stdout-only")
    assert result.code == EXIT_OK, result.text
    assert f"# Session {SESSION}" in result.out
    assert hash_tree(ws / "03_MEMORY") == before, "--stdout-only touches no file"


def test_out_writes_where_it_is_told(session, cli, tmp_path):
    target = tmp_path / "elsewhere" / "report.md"
    result = cli("session-report", "--last", "--out", str(target))
    assert result.code == EXIT_OK, result.text
    assert target.is_file()
    assert f"# Session {SESSION}" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------- the errors


def test_an_unknown_session_id_exits_four(session, cli):
    result = cli("session-report", "--session-id", "2026-01-01-algo-99")
    assert result.code == EXIT_NOT_FOUND
    assert "2026-01-01-algo-99" in result.err
    assert "Known sessions" in result.err


def test_a_workspace_with_no_sessions_exits_four(ws, cli):
    result = cli("session-report", "--last")
    assert result.code == EXIT_NOT_FOUND
    assert "no sessions yet" in result.err


def test_an_unknown_subject_exits_four(session, cli):
    assert cli("session-report", "--subject", "nope", "--last").code == EXIT_NOT_FOUND


def test_reporting_leaves_the_memory_state_untouched(session, cli, ws, hash_tree):
    tracked = ["03_MEMORY/subjects", "03_MEMORY/global", "03_MEMORY/events"]
    before = {part: hash_tree(ws / part) for part in tracked}
    assert cli("session-report", "--last").code == EXIT_OK
    assert {part: hash_tree(ws / part) for part in tracked} == before, (
        "a report is a derived artefact: it never writes to state or appends an event"
    )


def test_the_report_names_unreadable_log_lines(session, cli, ws):
    log = ws / "03_MEMORY/events/session_events.jsonl"
    with open(log, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("half a line\n")
    payload = report_json(cli, "--last")
    assert payload["unreadable_log_lines"] == 1
    text = (ws / payload["path"]).read_text(encoding="utf-8")
    assert "could not be parsed" in text


def test_session_summaries_list_every_session(session, cli):
    summaries = report_mod.session_summaries(session, subject="algo")
    ids = [summary.session_id for summary in summaries]
    assert SESSION in ids
    assert report_mod.latest_session(session, subject="algo").session_id == SESSION


def test_the_rendered_markdown_is_a_pure_function_of_the_report(session, cli):
    built = report_mod.build_report(session, SESSION, subject="algo")
    assert report_mod.render_markdown(built) == report_mod.render_markdown(built)
    store.load_concept_mastery(session, "algo")  # reading state must not change the rendering
    assert report_mod.render_markdown(built).startswith(f"# Session {SESSION}")
