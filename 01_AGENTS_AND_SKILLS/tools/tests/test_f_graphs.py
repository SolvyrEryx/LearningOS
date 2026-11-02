"""Test F — the graph pipeline produces honest, parseable artefacts.

A diagram is the one output a learner will trust without checking, so the failure modes worth
testing are the quiet ones: a label that breaks the Mermaid parse and silently truncates the
picture, a legend that carries meaning in colour alone, and an edge pointing at a concept that
does not exist. The last one is treated as fatal *before* any file is written, because a map that
disagrees with memory is worse than no map.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from common import (
    EXIT_MISSING_OPTIONAL_DEP,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VALIDATION,
    Workspace,
    hash_tree,
)
from graphs import build as graph_build
from graphs import mermaid as mermaid_mod
from graphs import render as render_mod
from memory import events as events_mod
from memory import mastery
from memory import store

#: The nasty label from plan item 22: a double quote, a colon and an arrow in one string.
NASTY_NAME = 'Big-O: he said "n -> n^2" out loud'

PER_SUBJECT = ("concept_prerequisite", "learner_mastery", "misconception", "progress")

#: Files that are the source of truth. A graph run must leave every one of them untouched.
STATE_FILES = (
    "03_MEMORY/subjects/algo/subject_state.json",
    "03_MEMORY/subjects/algo/concept_mastery.json",
    "03_MEMORY/subjects/algo/concept_graph.json",
    "03_MEMORY/subjects/algo/misconceptions.json",
    "03_MEMORY/subjects/algo/revision_queue.json",
    "03_MEMORY/global/workspace_state.json",
)


def graphs_dir(ws: Path, subject_id: str = "algo") -> Path:
    return ws / "03_MEMORY/graphs" / subject_id


def read_mmd(ws: Path, kind: str, subject_id: str = "algo") -> str:
    return (graphs_dir(ws, subject_id) / f"{kind}.mmd").read_text(encoding="utf-8")


def read_graph(ws: Path, kind: str, subject_id: str = "algo") -> dict:
    return json.loads(
        (graphs_dir(ws, subject_id) / f"{kind}.json").read_text(encoding="utf-8")
    )


def node_definition_lines(text: str) -> list[str]:
    """Lines that define a node, excluding the legend's own nodes and the subgraph headers."""
    return [
        line
        for line in text.splitlines()
        if re.match(r"^\s+(?!legend_)[A-Za-z0-9_]+(\[|\(|\{)", line)
        and not line.lstrip().startswith("subgraph")
    ]


def edge_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "-->|" in line or "-.->|" in line]


def state_hashes(ws: Path) -> dict[str, str]:
    from common import sha256_file

    return {
        relative: sha256_file(ws / relative)
        for relative in STATE_FILES
        if (ws / relative).is_file()
    }


@pytest.fixture
def rich(ws, cli, seed_subject):
    """A subject with real evidence, a misconception, a prerequisite chain and a nasty label."""
    seeded = seed_subject("algo", slugs=("sorting", "recursion", "big-o"))
    workspace = Workspace.at(ws)

    record = store.get_concept_record(workspace, "algo", "algo:big-o")
    record["name"] = NASTY_NAME
    store.put_concept_record(workspace, "algo", record)

    mastery.record_attempt_and_save(
        workspace,
        "algo",
        "algo:sorting",
        session_id="2026-02-11-algo-01",
        outcome="independent_success",
        novelty="variant",
        delay_days=3,
    )
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        "algo:recursion",
        session_id="2026-02-11-algo-01",
        outcome="hinted_success",
        hints_used=2,
        novelty="familiar",
    )

    misconceptions = store.load_misconceptions(workspace, "algo") or {
        "schema_version": 1,
        "subject_id": "algo",
        "updated_at": "2026-02-11T09:00:00Z",
        "misconceptions": [],
    }
    updated, _entry = mastery.apply_misconception(
        misconceptions,
        subject_id="algo",
        description='Thinks the merge step is free, so T(n) = 2T(n/2)',
        concept_ids=["algo:recursion", "algo:sorting"],
        learner_statement="merging is just pointer moves, it does not cost n",
        correct_statement="the merge touches every element once, so it costs n",
        session_id="2026-02-11-algo-01",
    )
    store.save_misconceptions(workspace, "algo", updated)
    return seeded


# ------------------------------------------------------------------------------ what gets written


def test_update_graphs_writes_json_mmd_and_html_per_kind(ws, cli, rich):
    result = cli("update-graphs", "--subject", "algo", "--kinds", ",".join(PER_SUBJECT))
    assert result.code == EXIT_OK, result.text
    for kind in PER_SUBJECT:
        for suffix in ("json", "mmd", "html"):
            target = graphs_dir(ws) / f"{kind}.{suffix}"
            assert target.is_file(), f"{target.name} was not written"
            assert target.stat().st_size > 0
    assert not (graphs_dir(ws) / "cross_subject.json").exists(), (
        "the cross-subject graph belongs in _cross, not in a subject directory"
    )


def test_cross_subject_goes_to_its_own_directory(ws, cli, rich):
    result = cli("update-graphs", "--all")
    assert result.code == EXIT_OK
    cross = ws / "03_MEMORY/graphs/_cross/cross_subject.json"
    assert cross.is_file()
    assert json.loads(cross.read_text(encoding="utf-8"))["subject_ids"] == ["algo"]
    assert "nothing to cross yet" in result.out, (
        "one subject is a legitimate request, but the graph should say what it is showing"
    )


def test_every_artefact_validates_against_the_schema(ws, cli, rich):
    assert cli("update-graphs", "--all").code == EXIT_OK
    workspace = Workspace.at(ws)
    registry = store.registry(workspace)
    for path in sorted((ws / "03_MEMORY/graphs").rglob("*.json")):
        findings = registry.validate("graph_artifact", json.loads(path.read_text("utf-8")))
        assert findings == [], f"{path.name}: {[f.message for f in findings]}"
    assert cli("validate").code == EXIT_OK


def test_graph_runs_leave_every_state_file_untouched(ws, cli, rich):
    before = state_hashes(ws)
    assert len(before) == len(STATE_FILES)
    assert cli("update-graphs", "--all").code == EXIT_OK
    assert cli("update-graphs", "--subject", "algo").code == EXIT_OK
    assert state_hashes(ws) == before, (
        "graphs are derived; generating one must never rewrite the memory it was drawn from"
    )


def test_one_graph_generated_event_per_artefact(ws, cli, rich):
    assert cli("update-graphs", "--all").code == EXIT_OK
    workspace = Workspace.at(ws)
    events, findings = events_mod.read_events(workspace, types=["graph_generated"])
    assert findings == []
    assert [event["payload"]["kind"] for event in events] == [*PER_SUBJECT, "cross_subject"]
    for event in events:
        assert event["actor"] == "tool"
        assert event["payload"]["svg_skipped"] in (True, False)
        for relative in event["payload"]["relative_paths"]:
            assert (ws / relative).is_file()
            assert relative.startswith("03_MEMORY/graphs/")


# --------------------------------------------------------------------------------- mermaid output


@pytest.mark.parametrize(
    ("kind", "header"),
    [
        ("concept_prerequisite", "flowchart LR"),
        ("learner_mastery", "flowchart TD"),
        ("misconception", "flowchart TD"),
        ("progress", "pie title Concept status distribution"),
    ],
)
def test_each_mmd_starts_with_the_expected_header(ws, cli, rich, kind, header):
    assert cli("update-graphs", "--subject", "algo", "--kinds", ",".join(PER_SUBJECT)).code == 0
    text = read_mmd(ws, kind)
    lines = text.splitlines()
    assert lines[0] == f"%% LearningOS graph - kind: {kind}"
    assert header in lines, f"the diagram header {header!r} must be present"
    assert lines.index(header) == len([line for line in lines if line.startswith("%%")])


@pytest.mark.parametrize("kind", ["concept_prerequisite", "learner_mastery", "misconception"])
def test_one_line_per_node_and_per_edge(ws, cli, rich, kind):
    assert cli("update-graphs", "--subject", "algo", "--kinds", ",".join(PER_SUBJECT)).code == 0
    graph = read_graph(ws, kind)
    text = read_mmd(ws, kind)
    assert len(node_definition_lines(text)) == len(graph["nodes"])
    assert len(edge_lines(text)) == len(graph["edges"])
    assert graph["nodes"], "the fixture has concepts, so the graph must have nodes"


def test_a_label_with_a_quote_a_colon_and_an_arrow_is_escaped(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "concept_prerequisite").code == 0
    text = read_mmd(ws, "concept_prerequisite")
    assert NASTY_NAME in read_graph(ws, "concept_prerequisite")["nodes"][0]["label"] or any(
        NASTY_NAME == node["label"] for node in read_graph(ws, "concept_prerequisite")["nodes"]
    )
    assert "#quot;n -> n^2#quot;" in text, "a double quote must become Mermaid's #quot; entity"
    for line in node_definition_lines(text):
        label = line[line.index('"') + 1 : line.rindex('"')]
        assert '"' not in label, f"an unescaped quote would truncate the diagram: {line}"


def test_node_keys_are_injective_even_for_colliding_ids():
    """``a:b`` and ``a-b`` both sanitise to ``a_b``; the collision must not merge two nodes."""
    keys = mermaid_mod.node_keys(["algo:big-o", "algo-big:o", "algo:big-o-2"])
    assert len(set(keys.values())) == 3
    assert all(re.fullmatch(r"[A-Za-z0-9_]+", key) for key in keys.values())
    assert mermaid_mod.node_keys(["algo:big-o"]) == {"algo:big-o": "algo_big_o"}


def test_the_mastery_graph_carries_a_legend_and_a_class_per_status(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "learner_mastery").code == 0
    text = read_mmd(ws, "learner_mastery")
    assert 'subgraph Legend["Legend (text labels, not colour)"]' in text
    for status in graph_build.STATUS_ORDER:
        assert f"classDef {status} " in text, f"no classDef for {status}"
        assert f"legend_{status}[" in text, f"{status} is missing from the legend"
    graph = read_graph(ws, "learner_mastery")
    assert [entry["key"] for entry in graph["legend"]] == list(graph_build.STATUS_ORDER)
    for entry in graph["legend"]:
        assert entry["label"].strip(), "meaning is carried by text, never by colour alone"


def test_mastery_nodes_name_their_status_in_the_label(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "learner_mastery").code == 0
    graph = read_graph(ws, "learner_mastery")
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["algo:sorting"]["status"] == "practising"
    assert "practising" in by_id["algo:sorting"]["label"]
    assert by_id["algo:recursion"]["status"] == "developing"
    assert by_id["algo:big-o"]["status"] == "unassessed"
    assert 0.0 <= by_id["algo:sorting"]["mastery"] <= 1.0


def test_the_prerequisite_graph_groups_nodes_by_unit(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "concept_prerequisite").code == 0
    text = read_mmd(ws, "concept_prerequisite")
    assert 'subgraph g_unit_1["unit-1"]' in text
    graph = read_graph(ws, "concept_prerequisite")
    assert {node["group"] for node in graph["nodes"]} == {"unit-1"}
    assert {(edge["from"], edge["to"]) for edge in graph["edges"]} == {
        ("algo:sorting", "algo:recursion"),
        ("algo:recursion", "algo:big-o"),
    }


def test_the_misconception_graph_uses_hexagons_and_exhibits_edges(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "misconception").code == 0
    graph = read_graph(ws, "misconception")
    text = read_mmd(ws, "misconception")
    hexagons = [node for node in graph["nodes"] if node["shape"] == "hexagon"]
    assert len(hexagons) == 1
    assert hexagons[0]["id"] == "algo:mc-001"
    assert "{{" in text and "}}" in text, "misconceptions are distinguishable without colour"
    assert {edge["type"] for edge in graph["edges"]} == {"exhibits"}
    assert {edge["from"] for edge in graph["edges"]} == {"algo:recursion", "algo:sorting"}
    assert "-.->" in text


def test_the_progress_graph_is_a_pie_of_status_counts(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "progress").code == 0
    graph = read_graph(ws, "progress")
    text = read_mmd(ws, "progress")
    counts = {node["status"]: node["value"] for node in graph["nodes"]}
    assert counts["practising"] == 1
    assert counts["developing"] == 1
    assert counts["unassessed"] == 1
    assert sum(counts.values()) == 3
    assert text.splitlines()[-5:] == [
        '  "developing (1)" : 1',
        '  "needs review (0)" : 0',
        '  "practising (1)" : 1',
        '  "provisionally mastered (0)" : 0',
        '  "unassessed (1)" : 1',
    ]
    assert graph["series"], "the per-session advance series comes from the event log"


# -------------------------------------------------------------------------- dangling references


def test_a_dangling_edge_stops_the_run_before_anything_is_written(ws, cli, rich):
    assert cli("update-graphs", "--all").code == EXIT_OK
    before = hash_tree(ws / "03_MEMORY/graphs")
    assert before

    path = ws / "03_MEMORY/subjects/algo/concept_graph.json"
    graph = json.loads(path.read_text(encoding="utf-8"))
    graph["edges"].append(
        {
            "from": "algo:big-o",
            "to": "algo:does-not-exist",
            "type": "prerequisite",
            "note": None,
        }
    )
    path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    result = cli("update-graphs", "--all")
    assert result.code == EXIT_VALIDATION
    assert "algo:does-not-exist" in result.err
    assert "algo:big-o" in result.err, "the message names both ends of the edge"
    assert hash_tree(ws / "03_MEMORY/graphs") == before, (
        "exit 3 happens before the first write, so no artefact is created or modified"
    )
    assert cli("validate").code == EXIT_VALIDATION


def test_a_dangling_prerequisite_in_a_record_is_also_fatal(ws, cli, rich):
    workspace = Workspace.at(ws)
    record = store.get_concept_record(workspace, "algo", "algo:sorting")
    record["prerequisite_ids"] = ["algo:ghost"]
    store.put_concept_record(workspace, "algo", record)
    result = cli("update-graphs", "--subject", "algo")
    assert result.code == EXIT_VALIDATION
    assert "algo:ghost" in result.err
    assert not (graphs_dir(ws) / "learner_mastery.mmd").exists()


def test_a_cross_subject_prerequisite_to_a_real_concept_is_fine(ws, cli, rich, seed_subject):
    seed_subject("maths", slugs=("induction",), chain=False)
    workspace = Workspace.at(ws)
    record = store.get_concept_record(workspace, "algo", "algo:recursion")
    record["prerequisite_ids"] = [*record["prerequisite_ids"], "maths:induction"]
    store.put_concept_record(workspace, "algo", record)

    assert cli("update-graphs", "--all").code == EXIT_OK
    cross = json.loads(
        (ws / "03_MEMORY/graphs/_cross/cross_subject.json").read_text(encoding="utf-8")
    )
    assert cross["subject_ids"] == ["algo", "maths"]
    assert {(edge["from"], edge["to"]) for edge in cross["edges"]} == {
        ("maths:induction", "algo:recursion")
    }
    groups = {node["id"]: node["group"] for node in cross["nodes"]}
    assert groups["maths:induction"] == "maths"
    assert groups["algo:recursion"] == "algo"

    own = read_graph(ws, "learner_mastery")
    assert all(node["id"].startswith("algo:") for node in own["nodes"]), (
        "a subject's own graph never shows another subject's nodes"
    )


# ------------------------------------------------------------------------------- flags and errors


def test_dry_run_builds_everything_and_writes_nothing(ws, cli, rich):
    before = hash_tree(ws)
    result = cli("update-graphs", "--all", "--dry-run")
    assert result.code == EXIT_OK, result.text
    assert "would write" in result.out
    assert "nothing was written" in result.out
    assert hash_tree(ws) == before


def test_out_dir_keeps_memory_out_of_it(ws, cli, rich, tmp_path):
    elsewhere = tmp_path / "scratch-graphs"
    result = cli("update-graphs", "--subject", "algo", "--out-dir", str(elsewhere))
    assert result.code == EXIT_OK, result.text
    assert (elsewhere / "algo/learner_mastery.mmd").is_file()
    assert not graphs_dir(ws).exists() or not any(graphs_dir(ws).glob("*.mmd"))


def test_format_selection_is_honoured_and_json_is_always_written(ws, cli, rich):
    result = cli("update-graphs", "--subject", "algo", "--kinds", "progress", "--format", "mmd")
    assert result.code == EXIT_OK, result.text
    assert (graphs_dir(ws) / "progress.mmd").is_file()
    assert (graphs_dir(ws) / "progress.json").is_file(), "the artefact itself is never optional"
    assert not (graphs_dir(ws) / "progress.html").exists()
    assert render_mod.SVG_SKIP_NOTICE not in result.out, "no SVG was asked for"


def test_an_unknown_kind_or_format_is_a_usage_error(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--kinds", "nonsense").code == EXIT_USAGE
    assert cli("update-graphs", "--subject", "algo", "--format", "pdf").code == EXIT_USAGE


def test_an_unknown_subject_exits_four(ws, cli, rich):
    assert cli("update-graphs", "--subject", "nope").code == 4


def test_subject_and_all_together_is_a_usage_error(ws, cli, rich):
    assert cli("update-graphs", "--subject", "algo", "--all").code == EXIT_USAGE


def test_an_empty_workspace_has_nothing_to_draw_and_says_so(ws, cli):
    result = cli("update-graphs", "--all")
    assert result.code == EXIT_OK, result.text
    assert "nothing to draw" in result.out
    assert not any((ws / "03_MEMORY/graphs").rglob("*.mmd"))


def test_require_renderer_exits_six_when_mmdc_is_absent(ws, cli, rich, monkeypatch):
    monkeypatch.setattr(render_mod, "renderer_path", lambda: None)
    result = cli("update-graphs", "--subject", "algo", "--require-renderer")
    assert result.code == EXIT_MISSING_OPTIONAL_DEP
    assert "mmdc" in result.err
    assert not graphs_dir(ws).exists(), "the check happens before the first write"


def test_a_failing_renderer_is_a_notice_not_a_failure(ws, cli, rich, monkeypatch):
    """A broken mmdc must cost the .svg and nothing else."""

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "mmdc: something went wrong\n"

    monkeypatch.setattr(render_mod, "renderer_path", lambda: "C:/fake/mmdc")
    monkeypatch.setattr(render_mod.subprocess, "run", lambda *a, **k: Completed())
    result = cli("update-graphs", "--subject", "algo", "--kinds", "progress")
    assert result.code == EXIT_OK, result.text
    assert "mmdc failed" in result.out
    assert (graphs_dir(ws) / "progress.mmd").is_file()
    assert not (graphs_dir(ws) / "progress.svg").exists()
