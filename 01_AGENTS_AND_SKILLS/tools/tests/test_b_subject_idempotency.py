"""Test B — subject creation is idempotent and never destructive.

`init-subject` is the command most likely to be re-run by accident: an agent that is unsure
whether a subject exists should be able to run it and find out, safely. Running it twice must
therefore change nothing at all — not a byte, not a counter, and certainly not mastery.
"""

from __future__ import annotations

import json

from common import EXIT_OK, EXIT_USAGE, EXIT_VALIDATION, Workspace
from memory import events as events_mod
from memory import store


def init_algo(cli, *extra):
    return cli("init-subject", "--id", "algo", "--name", "Algorithms", *extra)


def test_init_subject_creates_both_halves(ws, cli, workspace):
    result = init_algo(cli, "--exam-date", "2026-05-18")
    assert result.code == EXIT_OK, result.text
    assert (ws / "02_KNOWLEDGE/subjects/algo/subject_config.json").is_file()
    assert (ws / "02_KNOWLEDGE/subjects/algo/concept_catalog.md").is_file()
    assert (ws / "02_KNOWLEDGE/subjects/algo/source_materials").is_dir()
    assert (ws / "03_MEMORY/subjects/algo/subject_state.json").is_file()
    assert (ws / "03_MEMORY/subjects/algo/next_session.md").is_file()

    config = store.load_subject_config(workspace, "algo")
    state = store.load_subject_state(workspace, "algo")
    assert config["subject_id"] == state["subject_id"] == "algo"
    assert config["name"] == state["name"] == "Algorithms"
    assert config["exam_date"] == state["exam_date"] == "2026-05-18"
    assert state["created_at"].endswith("Z")


def test_placeholders_are_substituted_in_markdown_too(ws, cli):
    init_algo(cli)
    text = (ws / "03_MEMORY/subjects/algo/next_session.md").read_text(encoding="utf-8")
    assert "_template" not in text
    assert "# Next session: algo" in text
    readme = (ws / "02_KNOWLEDGE/subjects/algo/README.md").read_text(encoding="utf-8")
    assert "# Subject: Algorithms" in readme


def test_new_subject_starts_with_no_concepts_and_no_evidence(cli, workspace):
    init_algo(cli)
    mastery = store.load_concept_mastery(workspace, "algo")
    assert mastery["concepts"] == {}
    assert mastery["subject_id"] == "algo"
    queue = store.load_revision_queue(workspace, "algo")
    assert queue["items"] == []
    assert queue["ladder_days"] == [1, 3, 7, 16, 35]


def test_subject_is_registered_once_in_workspace_state_and_learner_index(ws, cli, workspace):
    init_algo(cli, "--exam-date", "2026-05-18")
    state = store.load_workspace_state(workspace)
    entries = [entry for entry in state["subjects"] if entry["subject_id"] == "algo"]
    assert len(entries) == 1
    assert entries[0]["knowledge_path"] == "02_KNOWLEDGE/subjects/algo"
    assert entries[0]["memory_path"] == "03_MEMORY/subjects/algo"
    assert state["current_subject_id"] == "algo"
    index = (ws / "03_MEMORY/global/learner_index.md").read_text(encoding="utf-8")
    assert index.count("| `algo` |") == 1
    assert "no subjects yet" not in index


def test_first_creation_appends_exactly_one_subject_initialized_event(cli, workspace):
    init_algo(cli)
    events, findings = events_mod.read_events(workspace, types=["subject_initialized"])
    assert findings == []
    assert len(events) == 1
    assert events[0]["subject_id"] == "algo"
    assert events[0]["actor"] == "tool"
    assert events[0]["payload"]["name"] == "Algorithms"
    assert all("\\" not in path for path in events[0]["payload"]["created_paths"])


def test_second_identical_run_changes_nothing(ws, cli, hash_tree, workspace):
    init_algo(cli)
    before = hash_tree(ws)
    result = init_algo(cli)
    assert result.code == EXIT_OK
    assert "already exists" in result.out
    assert hash_tree(ws) == before, "a second init-subject must not rewrite a single file"
    events, _ = events_mod.read_events(workspace, types=["subject_initialized"])
    assert len(events) == 1, "no second subject_initialized event"


def test_second_run_does_not_reset_mastery_written_in_between(ws, cli, workspace, hash_tree):
    init_algo(cli)
    mastery = store.load_concept_mastery(workspace, "algo")
    record = store.new_concept_record("algo:sorting", "Sorting", subject_id="algo")
    record["status"] = "practising"
    record["independent_successes"] = 1
    record["mastery_estimate"] = 0.42
    mastery["concepts"]["algo:sorting"] = record
    store.save_concept_mastery(workspace, "algo", mastery)
    before = hash_tree(ws)

    assert init_algo(cli).code == EXIT_OK

    after = store.load_concept_mastery(workspace, "algo")
    assert list(after["concepts"]) == ["algo:sorting"]
    assert after["concepts"]["algo:sorting"]["status"] == "practising"
    assert after["concepts"]["algo:sorting"]["independent_successes"] == 1
    assert after["concepts"]["algo:sorting"]["mastery_estimate"] == 0.42
    assert hash_tree(ws) == before


def test_a_no_op_run_reports_the_mastery_that_is_actually_on_disk(cli, workspace):
    """A second run must describe the file it just read, not the file a new subject starts with.

    It printed ``concepts = {}`` unconditionally, which is true on creation and a false statement
    about an existing subject that already holds evidence.
    """
    init_algo(cli)
    mastery = store.load_concept_mastery(workspace, "algo")
    mastery["concepts"]["algo:sorting"] = store.new_concept_record(
        "algo:sorting", "Sorting", subject_id="algo"
    )
    store.save_concept_mastery(workspace, "algo", mastery)

    result = init_algo(cli)

    assert result.code == EXIT_OK
    assert "1 concept record(s) already on disk" in result.out
    assert "concepts = {}" not in result.out


def test_catalogue_entries_are_never_duplicated(cli, workspace):
    init_algo(cli)
    catalog = store.load_concept_catalog(workspace, "algo")
    catalog["concepts"] = [
        {"concept_id": "algo:sorting", "name": "Sorting", "unit": "unit-1"}
    ]
    store.save_concept_catalog(workspace, "algo", catalog)
    assert init_algo(cli).code == EXIT_OK
    again = store.load_concept_catalog(workspace, "algo")
    assert [entry["concept_id"] for entry in again["concepts"]] == ["algo:sorting"]


def test_dry_run_creates_nothing(ws, cli, hash_tree):
    before = hash_tree(ws)
    result = init_algo(cli, "--dry-run")
    assert result.code == EXIT_OK
    assert "nothing was written" in result.out
    assert not (ws / "02_KNOWLEDGE/subjects/algo").exists()
    assert not (ws / "03_MEMORY/subjects/algo").exists()
    assert hash_tree(ws) == before


def test_bad_subject_id_is_a_usage_error(ws, cli, hash_tree):
    before = hash_tree(ws)
    for bad in ("Algo!", "My Subject", "_template", "ALGO", "algo/evil", "algo:x"):
        result = cli("init-subject", "--id", bad, "--name", "x")
        assert result.code == EXIT_USAGE, bad
        assert "remedy" in result.err, bad
    # A leading dash is rejected by argparse itself, still with exit 2.
    assert cli("init-subject", "--id", "-algo", "--name", "x").code == EXIT_USAGE
    assert hash_tree(ws) == before


def test_bad_exam_date_is_a_usage_error(cli, ws, hash_tree):
    before = hash_tree(ws)
    assert init_algo(cli, "--exam-date", "18-05-2026").code == EXIT_USAGE
    assert init_algo(cli, "--exam-date", "2026-02-30").code == EXIT_USAGE
    assert hash_tree(ws) == before


def test_corrupt_existing_subject_exits_three_and_writes_nothing(ws, cli, hash_tree):
    init_algo(cli)
    target = ws / "03_MEMORY/subjects/algo/concept_mastery.json"
    target.write_text('{"schema_version": 1, "subject_id": "algo",', encoding="utf-8")
    before = hash_tree(ws)
    result = init_algo(cli)
    assert result.code == EXIT_VALIDATION, result.text
    assert "concept_mastery.json" in result.err
    assert hash_tree(ws) == before, "a corrupt file must be left exactly as it is"


def test_a_partially_deleted_subject_is_completed_without_touching_survivors(ws, cli, workspace):
    init_algo(cli)
    mastery = store.load_concept_mastery(workspace, "algo")
    mastery["concepts"]["algo:sorting"] = store.new_concept_record(
        "algo:sorting", "Sorting", subject_id="algo"
    )
    store.save_concept_mastery(workspace, "algo", mastery)
    survivor = (ws / "03_MEMORY/subjects/algo/concept_mastery.json").read_bytes()
    (ws / "03_MEMORY/subjects/algo/revision_queue.json").unlink()

    assert init_algo(cli).code == EXIT_OK
    assert (ws / "03_MEMORY/subjects/algo/revision_queue.json").is_file()
    assert (ws / "03_MEMORY/subjects/algo/concept_mastery.json").read_bytes() == survivor


def test_json_output_shape(cli):
    payload = json.loads(init_algo(cli, "--json").out)
    assert payload["subject_id"] == "algo"
    assert payload["existed_before"] is False
    assert payload["memory_path"] == "03_MEMORY/subjects/algo"
    assert payload["event_id"]


def test_validate_passes_after_creating_two_subjects(cli):
    init_algo(cli)
    assert cli("init-subject", "--id", "thermo", "--name", "Thermodynamics").code == EXIT_OK
    assert cli("validate").code == EXIT_OK
    assert cli("status").code == EXIT_OK
