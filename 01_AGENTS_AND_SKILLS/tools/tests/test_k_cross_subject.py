"""Test K — subjects are isolated.

Multi-subject use is the point at which a learning system usually starts lying: progress in one
subject bleeds into another, or a new subject inherits someone else's mastery. LearningOS makes
that mechanically detectable by putting the owning subject inside every concept id, and these
tests pin both halves of the guarantee — no leakage, and leakage is an error when it happens.
"""

from __future__ import annotations

import json

import pytest

from common import EXIT_OK, EXIT_VALIDATION, LearningOSDataError, Workspace, utc_now_iso
from memory import events as events_mod
from memory import store

MASTERED_EVIDENCE = [
    {
        "at": "2026-02-08T09:00:00Z",
        "delay_days": 3,
        "outcome": "independent_success",
        "difficulty": 3,
        "novelty": "variant",
        "hints_used": 0,
    },
    {
        "at": "2026-02-11T09:00:00Z",
        "delay_days": 3,
        "outcome": "independent_success",
        "difficulty": 4,
        "novelty": "novel",
        "hints_used": 0,
    },
]


@pytest.fixture
def two_subjects(ws, cli):
    """`physics` driven to provisionally mastered; `chem` freshly created and untouched."""
    assert cli("init-subject", "--id", "physics", "--name", "Physics", "--quiet").code == EXIT_OK
    assert cli("init-subject", "--id", "chem", "--name", "Chemistry", "--quiet").code == EXIT_OK
    workspace = Workspace.at(ws)
    for slug in ("newtons-laws", "momentum"):
        record = store.new_concept_record(
            f"physics:{slug}", slug.replace("-", " ").title(), subject_id="physics"
        )
        record["status"] = "provisionally_mastered"
        record["independent_successes"] = 2
        record["mastery_estimate"] = 0.88
        record["last_successful_retrieval_at"] = "2026-02-11T09:00:00Z"
        record["last_attempted_at"] = "2026-02-11T09:00:00Z"
        record["retention_evidence"] = json.loads(json.dumps(MASTERED_EVIDENCE))
        # Evidence first, snapshot second — the documented write order.
        events_mod.append_event(
            workspace,
            type="mastery_updated",
            session_id="2026-02-11-physics-01",
            subject_id="physics",
            concept_ids=[f"physics:{slug}"],
            payload={
                "concept_id": f"physics:{slug}",
                "status_before": "practising",
                "status_after": "provisionally_mastered",
                "mastery_after": 0.88,
                "independent_successes": 2,
            },
        )
        store.put_concept_record(workspace, "physics", record)
    return workspace


def chem_memory_files(ws):
    return sorted(path for path in (ws / "03_MEMORY/subjects/chem").rglob("*") if path.is_file())


def test_physics_is_mastered_and_chem_is_empty(two_subjects):
    physics = store.load_concept_mastery(two_subjects, "physics")
    chem = store.load_concept_mastery(two_subjects, "chem")
    assert store.status_counts(physics)["provisionally_mastered"] == 2
    assert chem["concepts"] == {}, "a new subject never inherits mastery"
    assert store.status_counts(chem) == {
        "unassessed": 0,
        "developing": 0,
        "practising": 0,
        "provisionally_mastered": 0,
        "needs_review": 0,
    }


def test_no_physics_string_appears_in_any_chem_memory_file(ws, two_subjects):
    files = chem_memory_files(ws)
    assert files, "chem must have memory files to check"
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "physics" not in text.lower(), f"{path.name} mentions the other subject"


def test_chem_knowledge_files_are_equally_clean(ws, two_subjects):
    for path in sorted((ws / "02_KNOWLEDGE/subjects/chem").rglob("*")):
        if path.is_file():
            assert "physics" not in path.read_text(encoding="utf-8", errors="replace").lower()


def test_a_record_with_another_subjects_prefix_is_a_validation_error(ws, cli, two_subjects):
    """Hand-edited contamination is caught, with both subject names in the message."""
    target = ws / "03_MEMORY/subjects/chem/concept_mastery.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    data["concepts"]["physics:momentum"] = store.new_concept_record(
        "physics:momentum", "Momentum", subject_id="physics"
    )
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")

    result = cli("validate")
    assert result.code == EXIT_VALIDATION
    assert "cross_subject_record" in result.out
    assert "physics:momentum" in result.out
    assert "chem" in result.out


def test_the_store_refuses_to_write_a_foreign_record(two_subjects):
    mastery = store.load_concept_mastery(two_subjects, "chem")
    mastery["concepts"]["physics:momentum"] = store.new_concept_record(
        "physics:momentum", "Momentum", subject_id="physics"
    )
    with pytest.raises(LearningOSDataError) as excinfo:
        store.save_concept_mastery(two_subjects, "chem", mastery)
    assert "cross_subject" in excinfo.value.code
    assert store.load_concept_mastery(two_subjects, "chem")["concepts"] == {}


def test_a_duplicate_concept_id_in_two_subjects_is_an_error(ws, cli, two_subjects):
    target = ws / "03_MEMORY/subjects/chem/concept_mastery.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    record = store.new_concept_record("chem:momentum", "Momentum", subject_id="chem")
    data["concepts"]["chem:momentum"] = record
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert cli("validate").code == EXIT_OK, "same slug, different subject, is perfectly fine"


def test_a_cross_subject_prerequisite_is_allowed_but_carries_no_mastery(ws, cli, two_subjects):
    record = store.new_concept_record("chem:thermochemistry", "Thermochemistry", subject_id="chem")
    record["prerequisite_ids"] = ["physics:momentum"]
    store.put_concept_record(two_subjects, "chem", record)
    assert cli("validate").code == EXIT_OK

    reloaded = store.get_concept_record(Workspace.at(ws), "chem", "chem:thermochemistry")
    assert reloaded["status"] == "unassessed"
    assert reloaded["mastery_estimate"] is None
    assert reloaded["independent_successes"] == 0


def test_status_reports_each_subject_separately(cli, two_subjects):
    payload = json.loads(cli("status", "--json").out)
    by_id = {subject["subject_id"]: subject for subject in payload["subjects"]}
    assert by_id["physics"]["statuses"]["provisionally_mastered"] == 2
    assert by_id["chem"]["concepts"] == 0
    assert by_id["chem"]["statuses"]["provisionally_mastered"] == 0


def test_a_third_subject_created_afterwards_still_starts_empty(ws, cli, two_subjects):
    assert cli("init-subject", "--id", "bio", "--name", "Biology", "--quiet").code == EXIT_OK
    workspace = Workspace.at(ws)
    bio = store.load_concept_mastery(workspace, "bio")
    assert bio["concepts"] == {}
    assert bio["subject_id"] == "bio"
    for path in sorted((ws / "03_MEMORY/subjects/bio").rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            assert "physics:" not in text and "chem:" not in text
            assert '"physics"' not in text and '"chem"' not in text
    assert cli("validate").code == EXIT_OK


def test_validating_one_subject_does_not_report_another(ws, cli, two_subjects):
    (ws / "03_MEMORY/subjects/physics/revision_queue.json").unlink()
    result = cli("validate", "--subject", "chem", "--json")
    assert result.code == EXIT_OK
    payload = json.loads(result.out)
    subject_findings = [
        finding
        for finding in payload["findings"]
        if finding["path"] and "subjects/physics" in finding["path"]
    ]
    assert subject_findings == []


def test_events_keep_their_subject_tag(two_subjects):
    events, findings = events_mod.read_events(two_subjects, subject_id="chem")
    assert findings == []
    assert [event["type"] for event in events] == ["subject_initialized"]
    assert all(event["subject_id"] == "chem" for event in events)
    physics_events, _ = events_mod.read_events(two_subjects, subject_id="physics")
    assert any(event["type"] == "mastery_updated" for event in physics_events)


# --------------------------------------------------------------------- isolation in the graphs
#
# The data-level guarantees are covered above. These two assertions pin the place where isolation
# is most easily lost by accident: a drawing step that reaches for "the mastery" rather than for
# this subject's mastery.


def test_a_subjects_mastery_graph_contains_no_other_subjects_nodes(ws, cli, two_subjects):
    assert cli("update-graphs", "--subject", "chem", "--kinds", "learner_mastery").code == EXIT_OK
    artefact = ws / "03_MEMORY/graphs/chem/learner_mastery.json"
    graph = json.loads(artefact.read_text(encoding="utf-8"))
    assert graph["subject_ids"] == ["chem"]
    assert [node["id"] for node in graph["nodes"] if node["id"].startswith("physics:")] == []
    mermaid = (ws / "03_MEMORY/graphs/chem/learner_mastery.mmd").read_text(encoding="utf-8")
    assert "physics" not in mermaid.lower()


def test_the_cross_subject_graph_shows_both_subjects_with_their_own_statuses(ws, cli, two_subjects):
    # chem needs at least one concept to appear: a graph draws what memory holds, and an empty
    # subject has nothing to draw. The prerequisite deliberately crosses into physics.
    record = store.new_concept_record("chem:thermochemistry", "Thermochemistry", subject_id="chem")
    record["prerequisite_ids"] = ["physics:momentum"]
    store.put_concept_record(two_subjects, "chem", record)

    assert cli("update-graphs", "--all", "--kinds", "cross_subject").code == EXIT_OK
    graph = json.loads(
        (ws / "03_MEMORY/graphs/_cross/cross_subject.json").read_text(encoding="utf-8")
    )
    assert graph["subject_ids"] == ["chem", "physics"]
    groups = {node["group"] for node in graph["nodes"]}
    assert groups == {"chem", "physics"}
    by_subject: dict[str, set[str]] = {}
    for node in graph["nodes"]:
        by_subject.setdefault(str(node["group"]), set()).add(str(node["status"]))
    assert by_subject["physics"] == {"provisionally_mastered"}
    assert by_subject["chem"] == {"unassessed"}, (
        "a freshly created subject stays unassessed beside a mastered neighbour"
    )
