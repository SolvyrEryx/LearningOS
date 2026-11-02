"""Test H — integrity and the corruption policy.

The rule under test is from `core/memory_protocol.md` section 8: LearningOS would rather stop and
tell you exactly what is wrong than guess at a repair, because a silently "fixed" mastery file is
indistinguishable from fabricated evidence. So for every flavour of damage: exit 3, a finding that
names the file (and the line, for the event log), an actionable remedy, and the offending bytes
left exactly as they were.
"""

from __future__ import annotations

import json

import pytest

from common import (
    EXIT_OK,
    EXIT_VALIDATION,
    LearningOSDataError,
    Workspace,
    hash_tree,
    sha256_file,
)
from memory import events as events_mod
from memory import store
from validation import validate_workspace

TRUNCATED = '{"schema_version": 1, "subject_id": "algo", "updated_at":'
WRONG_SHAPE = '{"schema_version": 1, "subject_id": "algo", "updated_at": "nope", "concepts": []}'


@pytest.fixture
def algo(ws, cli):
    assert cli("init-subject", "--id", "algo", "--name", "Algorithms", "--quiet").code == EXIT_OK
    workspace = Workspace.at(ws)
    store.put_concept_record(
        workspace,
        "algo",
        store.new_concept_record("algo:sorting", "Sorting", subject_id="algo"),
    )
    return workspace


def findings_for(result_text: str) -> str:
    return result_text


# ----------------------------------------------------------------------- unparseable state file


def test_truncated_json_is_reported_and_left_alone(ws, cli, algo):
    target = ws / "03_MEMORY/subjects/algo/concept_mastery.json"
    target.write_text(TRUNCATED, encoding="utf-8")
    before = sha256_file(target)

    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "03_MEMORY/subjects/algo/concept_mastery.json" in result.out
    assert "not valid JSON" in result.out
    assert "remedy" in result.out
    assert sha256_file(target) == before, "a corrupt file is never rewritten"


def test_valid_json_of_the_wrong_shape_is_reported_with_a_pointer(ws, cli, algo):
    target = ws / "03_MEMORY/subjects/algo/concept_mastery.json"
    target.write_text(WRONG_SHAPE, encoding="utf-8")
    before = sha256_file(target)

    result = cli("validate", "--json")
    assert result.code == EXIT_VALIDATION
    payload = json.loads(result.out)
    paths = {finding["path"] for finding in payload["findings"]}
    pointers = {finding["pointer"] for finding in payload["findings"]}
    assert "03_MEMORY/subjects/algo/concept_mastery.json" in paths
    assert {"/updated_at", "/concepts"} & pointers
    assert sha256_file(target) == before


def test_missing_required_file_names_it_and_suggests_a_remedy(ws, cli, algo):
    (ws / "03_MEMORY/subjects/algo/subject_state.json").unlink()
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "03_MEMORY/subjects/algo/subject_state.json" in result.out
    assert "init-subject" in result.out or "restore" in result.out


def test_missing_derived_file_is_only_a_warning(ws, cli, algo):
    (ws / "03_MEMORY/subjects/algo/revision_queue.json").unlink()
    result = cli("validate")
    assert result.code == EXIT_OK, result.text
    assert "missing_derived_file" in result.out


# ---------------------------------------------------------------------------- the event log


def test_a_non_json_line_is_reported_with_its_line_number(ws, cli, algo):
    log = ws / "03_MEMORY/events/session_events.jsonl"
    with open(log, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("this line is not json\n")
    before = sha256_file(log)

    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "03_MEMORY/events/session_events.jsonl:2" in result.out
    assert "append-only" in result.out
    assert sha256_file(log) == before, "the event log is never rewritten"


def test_a_schema_invalid_event_line_is_reported_with_its_line_number(ws, cli, algo):
    log = ws / "03_MEMORY/events/session_events.jsonl"
    bad = {
        "schema_version": 1,
        "event_id": "9f1c2d3e4f5061728394a5b6c7d8e9f0",
        "at": "2026-02-11T09:14:00Z",
        "session_id": "2026-02-11-algo-01",
        "subject_id": "algo",
        "type": "answer_evaluated",
        "actor": "agent",
        "payload": {"outcome": "hinted_success"},
    }
    with open(log, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(bad) + "\n")
    before = sha256_file(log)

    result = cli("validate", "--json")
    assert result.code == EXIT_VALIDATION
    payload = json.loads(result.out)
    offenders = [
        finding
        for finding in payload["findings"]
        if finding["path"] == "03_MEMORY/events/session_events.jsonl:2"
    ]
    assert offenders, payload["findings"]
    assert "answer evaluated" in offenders[0]["message"]
    assert sha256_file(log) == before


def test_readers_skip_a_bad_line_and_count_it(ws, algo):
    log = ws / "03_MEMORY/events/session_events.jsonl"
    with open(log, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json}\n")
        handle.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "0" * 32,
                    "at": "2026-02-11T09:15:00Z",
                    "session_id": "s",
                    "subject_id": "algo",
                    "type": "session_ended",
                    "actor": "agent",
                    "payload": {},
                }
            )
            + "\n"
        )
    events, findings = events_mod.read_events(algo)
    assert len(events) == 2, "the good lines on either side still load"
    assert len(findings) == 1
    assert findings[0].path.endswith(":2")
    valid, corrupt = events_mod.count_events(algo)
    assert (valid, corrupt) == (2, 1)


def test_appending_an_invalid_event_writes_nothing(ws, algo):
    log = ws / "03_MEMORY/events/session_events.jsonl"
    before = sha256_file(log)
    with pytest.raises(LearningOSDataError) as excinfo:
        events_mod.append_event(
            algo,
            type="answer_evaluated",
            session_id="2026-02-11-algo-01",
            subject_id="algo",
            payload={"outcome": "hinted_success"},  # missing independent + hints_used
        )
    assert excinfo.value.exit_code == EXIT_VALIDATION
    assert "Nothing was written" in (excinfo.value.remedy or "")
    assert sha256_file(log) == before


# ------------------------------------------------------------------ writes through the store


def test_a_write_refuses_to_overwrite_a_corrupt_file(ws, algo):
    target = ws / "03_MEMORY/subjects/algo/concept_mastery.json"
    target.write_text(TRUNCATED, encoding="utf-8")
    before = sha256_file(target)
    with pytest.raises(LearningOSDataError) as excinfo:
        store.put_concept_record(
            algo,
            "algo",
            store.new_concept_record("algo:recursion", "Recursion", subject_id="algo"),
        )
    assert excinfo.value.exit_code == EXIT_VALIDATION
    assert "concept_mastery.json" in (excinfo.value.path or "")
    assert sha256_file(target) == before


def test_a_write_refuses_an_invalid_object_and_leaves_the_file_intact(ws, algo):
    target = ws / "03_MEMORY/subjects/algo/subject_state.json"
    before = sha256_file(target)
    state = store.load_subject_state(algo, "algo")
    state["session_count"] = "three"
    with pytest.raises(LearningOSDataError):
        store.save_subject_state(algo, "algo", state)
    assert sha256_file(target) == before


def test_a_write_refuses_to_mix_two_subjects(ws, cli, algo):
    assert cli("init-subject", "--id", "chem", "--name", "Chemistry", "--quiet").code == EXIT_OK
    target = ws / "03_MEMORY/subjects/chem/concept_mastery.json"
    before = sha256_file(target)
    mastery = store.load_concept_mastery(algo, "chem")
    mastery["concepts"]["algo:sorting"] = store.new_concept_record(
        "algo:sorting", "Sorting", subject_id="algo"
    )
    with pytest.raises(LearningOSDataError) as excinfo:
        store.save_concept_mastery(algo, "chem", mastery)
    assert "cross_subject" in excinfo.value.code
    assert sha256_file(target) == before


def test_loading_a_corrupt_file_raises_with_path_and_remedy(ws, algo):
    target = ws / "03_MEMORY/subjects/algo/subject_state.json"
    target.write_text("{", encoding="utf-8")
    with pytest.raises(LearningOSDataError) as excinfo:
        store.load_subject_state(algo, "algo")
    assert excinfo.value.path == "03_MEMORY/subjects/algo/subject_state.json"
    assert excinfo.value.remedy


# ------------------------------------------------------------------------ structural damage


def test_a_fourth_top_level_directory_is_an_error(ws, cli):
    (ws / "04_SCRATCH").mkdir()
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "fourth top-level directory" in result.out


def test_a_dangling_prerequisite_is_an_error(ws, cli, algo):
    record = store.get_concept_record(algo, "algo", "algo:sorting")
    record["prerequisite_ids"] = ["algo:not-a-thing"]
    store.put_concept_record(algo, "algo", record)
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "dangling_prerequisite" in result.out
    assert "algo:not-a-thing" in result.out


def test_a_cross_subject_prerequisite_to_a_real_concept_is_accepted(ws, cli, algo):
    assert cli("init-subject", "--id", "math", "--name", "Mathematics", "--quiet").code == EXIT_OK
    store.put_concept_record(
        algo, "math", store.new_concept_record("math:integral", "Integral", subject_id="math")
    )
    record = store.get_concept_record(algo, "algo", "algo:sorting")
    record["prerequisite_ids"] = ["math:integral"]
    store.put_concept_record(algo, "algo", record)
    assert cli("validate").code == EXIT_OK


def test_a_dangling_graph_edge_is_an_error_quoting_both_ends(ws, cli, algo):
    graph = store.load_concept_graph(algo, "algo")
    graph["nodes"] = [{"concept_id": "algo:sorting", "unit": None}]
    graph["edges"] = [
        {"from": "algo:sorting", "to": "algo:does-not-exist", "type": "prerequisite", "note": None}
    ]
    store.save_concept_graph(algo, "algo", graph)
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "dangling_edge" in result.out
    assert "algo:does-not-exist" in result.out
    assert "algo:sorting" in result.out


def test_a_dangling_misconception_link_is_an_error(ws, cli, algo):
    record = store.get_concept_record(algo, "algo", "algo:sorting")
    record["misconception_ids"] = ["algo:mc-007"]
    store.put_concept_record(algo, "algo", record)
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "dangling_misconception" in result.out


def test_an_absolute_path_in_a_memory_file_is_an_error(ws, cli, algo):
    record = store.get_concept_record(algo, "algo", "algo:sorting")
    record["next_action"] = "Open e:\\AI-KIRO-CODEX\\LearningOS\\notes.pdf and redo question 4"
    store.put_concept_record(algo, "algo", record)
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "absolute_path_stored" in result.out


def test_documented_paths_in_the_adapters_must_resolve(ws, cli):
    steering = ws / "01_AGENTS_AND_SKILLS/adapters/kiro/steering/learningos.md"
    steering.write_text(
        steering.read_text(encoding="utf-8") + "\nSee 03_MEMORY/does_not_exist/file.md\n",
        encoding="utf-8",
    )
    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "documented_path_missing" in result.out


# ------------------------------------------------------------------------------ state drift


def test_state_newer_evidence_produces_a_stale_state_warning_and_exit_zero(ws, cli, algo):
    """Evidence newer than the snapshot is drift: a warning, not a failure."""
    events_mod.append_event(
        algo,
        type="mastery_updated",
        session_id="2026-02-11-algo-01",
        subject_id="algo",
        at="2099-01-01T00:00:00Z",
        payload={"concept_id": "algo:sorting", "status_after": "practising"},
    )
    findings = validate_workspace(algo)
    drift = [finding for finding in findings if finding.code == "stale_state"]
    assert drift, [finding.code for finding in findings]
    assert all(finding.severity == "warning" for finding in drift)
    assert "reconcile" in drift[0].remedy
    result = cli("validate")
    assert result.code == EXIT_OK, result.text
    assert "stale_state" in result.out

    before = hash_tree(ws / "03_MEMORY")
    dry = cli("reconcile", "--subject", "algo", "--dry-run")
    assert dry.code == EXIT_OK, dry.text
    assert "stale_state" in dry.out
    assert "concept_mastery.json" in dry.out or "subject_state.json" in dry.out
    assert "Nothing was written" in dry.out
    assert hash_tree(ws / "03_MEMORY") == before, "a report-only run writes nothing"


def test_reconcile_reports_a_hand_edited_status_and_apply_re_derives_it(ws, cli, algo):
    """A status the evidence does not support is drift, and `--apply` is the only way it moves."""
    from memory import mastery as mastery_mod

    mastery_mod.record_attempt_and_save(
        algo,
        "algo",
        "algo:sorting",
        session_id="2026-02-11-algo-01",
        outcome="hinted_success",
        hints_used=1,
        novelty="variant",
    )
    record = store.get_concept_record(algo, "algo", "algo:sorting")
    record["status"] = "provisionally_mastered"
    record["independent_successes"] = 5
    record["mastery_estimate"] = 0.99
    store.put_concept_record(algo, "algo", record)

    dry = cli("reconcile", "--subject", "algo", "--dry-run")
    assert dry.code == EXIT_OK, dry.text
    assert "drift_derived_field" in dry.out
    assert "independent_successes 5 -> 0" in dry.out
    assert store.get_concept_record(algo, "algo", "algo:sorting")["status"] == (
        "provisionally_mastered"
    ), "a report-only run changes nothing"

    applied = cli("reconcile", "--subject", "algo", "--apply")
    assert applied.code == EXIT_OK, applied.text
    assert "repaired 1 concept(s)" in applied.out
    assert "after backup memory-" in applied.out, "--apply backs up before it writes"
    assert list((ws / "03_MEMORY/backups").glob("*.zip")), "the safety backup is on disk"

    repaired = store.get_concept_record(algo, "algo", "algo:sorting")
    assert repaired["status"] == "developing", "one hinted success supports no more than that"
    assert repaired["independent_successes"] == 0
    assert repaired["mastery_estimate"] < 0.99
    assert cli("validate").code == EXIT_OK
    assert "No drift" in cli("reconcile", "--subject", "algo").out


def test_reconcile_will_not_invent_evidence_the_log_implies(ws, cli, algo):
    """An attempt in the log with no evidence entry is reported, never fabricated."""
    events_mod.append_event(
        algo,
        type="answer_evaluated",
        session_id="2026-02-11-algo-01",
        subject_id="algo",
        concept_ids=["algo:sorting"],
        payload={"outcome": "independent_success", "independent": True, "hints_used": 0},
    )
    result = cli("reconcile", "--subject", "algo", "--apply")
    assert result.code == EXIT_OK, result.text
    assert "drift_missing_evidence" in result.out
    record = store.get_concept_record(algo, "algo", "algo:sorting")
    assert record["retention_evidence"] == [], "no attempt was fabricated from the event"
    assert record["independent_successes"] == 0


def test_quiet_mode_prints_only_errors(ws, cli, algo):
    (ws / "03_MEMORY/subjects/algo/revision_queue.json").unlink()
    result = cli("validate", "--quiet")
    assert result.code == EXIT_OK
    assert result.out.strip() == "", result.out


def test_validate_subject_filter_rejects_an_unknown_subject(cli, algo):
    result = cli("validate", "--subject", "nope")
    assert result.code != EXIT_OK
    assert "unknown subject" in result.err
