"""Test A — initialisation.

From an empty folder to a workspace that validates clean, with no fourth top-level directory and
no file rewritten on a second run. This is the test that keeps the "open the folder and start"
promise honest.
"""

from __future__ import annotations

import json

import pytest

import learningos
from common import EXIT_OK, MARKER_FILE, PRINCIPAL_DIRS, ROOT_FILES, Workspace
from memory import store

GLOBAL_FILES = (
    "learner_profile.md",
    "learning_preferences.json",
    "learner_index.md",
    "active_priorities.md",
    "workspace_state.json",
)

#: Every file the scaffold writes that has a schema, and the schema it must satisfy.
SCHEMA_MAP = {
    "03_MEMORY/global/workspace_state.json": "workspace_state",
    "03_MEMORY/global/learning_preferences.json": "learning_preferences",
    "03_MEMORY/subjects/_template/subject_state.json": "subject_state",
    "03_MEMORY/subjects/_template/concept_mastery.json": "concept_mastery",
    "03_MEMORY/subjects/_template/concept_graph.json": "concept_graph",
    "03_MEMORY/subjects/_template/misconceptions.json": "misconceptions",
    "03_MEMORY/subjects/_template/revision_queue.json": "revision_queue",
    "02_KNOWLEDGE/subjects/_template/subject_config.json": "subject_config",
    "02_KNOWLEDGE/subjects/_template/concept_catalog.json": "concept_catalog",
    "02_KNOWLEDGE/subjects/_template/source_index.json": "source_index",
}


def test_init_workspace_on_an_empty_directory_exits_zero(tmp_path):
    target = tmp_path / "fresh"
    target.mkdir()
    assert learningos.main(["init-workspace", "--quiet", "--workspace", str(target)]) == EXIT_OK
    assert (target / MARKER_FILE).is_file()


def test_exactly_three_principal_directories(ws):
    directories = sorted(
        item.name for item in ws.iterdir() if item.is_dir() and not item.name.startswith(".")
    )
    assert directories == list(PRINCIPAL_DIRS), "there must be no fourth top-level directory"


def test_root_entrypoints_exist(ws):
    for name in ROOT_FILES:
        assert (ws / name).is_file(), name
    config = json.loads((ws / MARKER_FILE).read_text(encoding="utf-8"))
    assert config["workspace_name"] == ws.name
    assert config["paths"]["event_log"] == "03_MEMORY/events/session_events.jsonl"


def test_global_memory_files_exist_and_the_event_log_starts_empty(ws):
    for name in GLOBAL_FILES:
        assert (ws / "03_MEMORY" / "global" / name).is_file(), name
    log = ws / "03_MEMORY" / "events" / "session_events.jsonl"
    assert log.is_file()
    assert log.read_text(encoding="utf-8") == ""


def test_template_subject_directories_are_populated(ws):
    assert (ws / "02_KNOWLEDGE/subjects/_template/source_materials").is_dir()
    assert (ws / "02_KNOWLEDGE/subjects/_template/extracted_text").is_dir()
    assert (ws / "03_MEMORY/subjects/_template/next_session.md").is_file()
    # checkpoint.json belongs under 03_MEMORY/checkpoints/, not in a subject directory
    assert not (ws / "03_MEMORY/subjects/_template/checkpoint.json").exists()


@pytest.mark.parametrize("relative,schema_name", sorted(SCHEMA_MAP.items()))
def test_every_scaffolded_json_file_validates(workspace, relative, schema_name):
    data = json.loads((workspace.root / relative).read_text(encoding="utf-8"))
    assert store.registry(workspace).validate(schema_name, data, path=relative) == []


def test_every_json_file_in_the_workspace_parses(ws):
    for path in ws.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_validate_exits_zero_on_a_fresh_workspace(cli):
    result = cli("validate")
    assert result.code == EXIT_OK, result.text
    assert "No problems found" in result.out


def test_validate_strict_also_exits_zero(cli):
    assert cli("validate", "--strict").code == EXIT_OK


def test_status_exits_zero_and_reports_no_subjects(cli):
    result = cli("status")
    assert result.code == EXIT_OK
    assert "none yet" in result.out


def test_second_init_workspace_is_a_no_op(ws, cli, hash_tree):
    before = hash_tree(ws)
    result = cli("init-workspace")
    assert result.code == EXIT_OK
    assert hash_tree(ws) == before, "a second init-workspace must not rewrite a single file"
    assert "nothing to do" in result.out


def test_dry_run_on_an_empty_directory_writes_nothing(tmp_path):
    target = tmp_path / "planned"
    target.mkdir()
    code = learningos.main(["init-workspace", "--dry-run", "--workspace", str(target)])
    assert code == EXIT_OK
    assert list(target.iterdir()) == [], "--dry-run must not create anything"


def test_dry_run_reports_what_it_would_do(tmp_path, capsys):
    target = tmp_path / "planned"
    target.mkdir()
    learningos.main(["init-workspace", "--dry-run", "--workspace", str(target)])
    captured = capsys.readouterr().out
    assert "would create" in captured
    assert "nothing was written" in captured


def test_json_output_is_machine_readable(cli):
    result = cli("init-workspace", "--json")
    payload = json.loads(result.out)
    assert payload["created"] == []
    assert payload["dry_run"] is False
    assert payload["skipped"]


def test_workspace_state_starts_with_no_subjects(workspace):
    state = store.load_workspace_state(workspace)
    assert state["subjects"] == []
    assert state["current_subject_id"] is None


def test_init_workspace_into_a_nested_path_creates_it(tmp_path):
    target = tmp_path / "deeper" / "LearningOS"
    assert learningos.main(["init-workspace", "--quiet", "--workspace", str(target)]) == EXIT_OK
    assert (target / MARKER_FILE).is_file()
    assert learningos.main(["validate", "--quiet", "--workspace", str(target)]) == EXIT_OK
