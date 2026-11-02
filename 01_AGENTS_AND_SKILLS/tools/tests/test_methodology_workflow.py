"""A new student's journey, evidence loopholes, interrupted saves and viewer failures."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import atlas
import atlas_sync
import learning_method as method
import onboarding
import study_workflow as study
from common import LearningOSUsageError, Workspace
from memory import events, store


@pytest.fixture
def learner(ws, seed_subject):
    seed_subject("biology", slugs=("inheritance", "meiosis"))
    workspace = Workspace.at(ws)
    study.begin(workspace, "biology")
    return workspace


def cycle(cid="cycle-1", **changes):
    raw = dict(cycle_id=cid, concept_id="biology:inheritance", task_id=cid,
               prompt="Explain the rule, then apply it to an unfamiliar case: " + cid,
               response="Actual synthetic learner explanation and a worked application.",
               feedback="The explanation and application satisfy the specified rubric.",
               dimensions={"explanation":{"outcome":"pass","rationale":"The learner states the mechanism and assumptions."},
                           "application":{"outcome":"pass","rationale":"The learner independently applies the mechanism correctly."}},
               hints=0, solution_seen=False, novelty="variant", difficulty=3, next_action="Retrieve and apply a delayed variant.", evaluation="checked")
    raw.update(changes)
    return raw


def test_new_student_can_start_without_exam_or_sources(ws, cli):
    result = cli("start", "--subject", "Biology", "--topic", "Inheritance", "--goal", "Explain inheritance and apply it", "--json")
    assert result.code == 0, result.text
    payload = json.loads(result.out)
    assert payload["subject_id"] == "biology" and payload["atlas_refreshed"]
    record = store.get_concept_record(Workspace.at(ws), "biology", "biology:inheritance")
    assert record["status"] == "unassessed" and record["source_refs"] == []
    assert cli("continue", "--subject", "Biology", "--json").code == 0
    assert cli("guide").code == 0
    assert cli("validate").code == 0, cli("validate").text


def test_complete_cycle_stores_response_and_updates_viewer(learner):
    raw = cycle()
    receipt = method.record_cycle(learner, "biology", raw)
    assert receipt["memory_saved"] and receipt["atlas_refreshed"]
    assert receipt["outcome"] == "independent_success"
    cm = store.get_concept_record(learner, "biology", raw["concept_id"])
    assert cm["independent_successes"] == 1
    view = atlas.collect(learner)
    assert view["learning"]["cycles"][0]["response"] == raw["response"]
    assert view["learning"]["cycles"][0]["applied"]


def test_same_operation_id_never_double_counts(learner):
    raw = cycle()
    method.record_cycle(learner, "biology", raw)
    receipt = method.record_cycle(learner, "biology", raw)
    assert receipt["already_saved"]
    record = store.get_concept_record(learner, "biology", raw["concept_id"])
    assert record["independent_successes"] == 1 and len(record["retention_evidence"]) == 1
    with pytest.raises(LearningOSUsageError):
        method.record_cycle(learner, "biology", {**raw, "response":"A different response"})


@pytest.mark.parametrize("changes,expected", [
    ({"dimensions":{"recall":{"outcome":"pass","rationale":"Recalls a fact."}}}, "partial"),
    ({"evaluation":"provisional"}, "partial"),
    ({"hints":1}, "hinted_success"),
    ({"solution_seen":True}, "hinted_success"),
    ({"support":[{"level":1,"content":"Consider the missing condition."}]}, "hinted_success"),
    ({"steps":[{"work":"An incorrect step","judgment":"fail","rationale":"The transition violates an assumption."}]}, "partial")
])
def test_assessment_loopholes_cannot_earn_unaided_success(learner, changes, expected):
    raw = cycle(**changes)
    assert method.record_cycle(learner, "biology", raw)["outcome"] == expected
    assert store.get_concept_record(learner, "biology", raw["concept_id"])["independent_successes"] == 0


def test_feedback_retry_is_familiar_and_assisted(learner):
    raw = cycle()
    method.record_cycle(learner, "biology", raw)
    retry = {**raw, "cycle_id":"retry", "novelty":"novel"}
    result = method.record_cycle(learner, "biology", retry)
    assert result["outcome"] == "hinted_success"
    assert method.summary(learner)["cycles"][-1]["novelty"] == "familiar"


def test_journal_recovers_after_snapshot_write_interruption(learner, monkeypatch):
    # A writer's metadata clock must differ from the original event time, deterministically.
    monkeypatch.setattr(store, "utc_now_iso", lambda: "2026-11-01T23:59:59Z")
    original = store.save_revision_queue
    monkeypatch.setattr(store, "save_revision_queue", lambda *a, **k: (_ for _ in ()).throw(OSError("Simulated write interruption")))
    with pytest.raises(OSError):
        method.record_cycle(learner, "biology", cycle())
    assert len(method.pending(learner, "biology")) == 1
    monkeypatch.setattr(store, "save_revision_queue", original)
    assert method.recover(learner, "biology")["recovered_cycles"] == 1
    assert not method.pending(learner, "biology")
    record = store.get_concept_record(learner, "biology", "biology:inheritance")
    assert len(record["retention_evidence"]) == 1
    canonical, _ = events.read_events(learner, types=["answer_evaluated"])
    assert len(canonical) == 1


def test_recovery_refuses_diverged_projection(learner, monkeypatch):
    original = method.apply_cycle
    monkeypatch.setattr(method, "apply_cycle", lambda *a: (_ for _ in ()).throw(OSError("Simulated interruption")))
    with pytest.raises(OSError):method.record_cycle(learner, "biology", cycle())
    monkeypatch.setattr(method, "apply_cycle", original)
    record = store.get_concept_record(learner, "biology", "biology:inheritance")
    record["next_action"] = "A separately edited action."
    store.put_concept_record(learner, "biology", record)
    with pytest.raises(LearningOSUsageError):method.recover(learner, "biology")
    assert store.get_concept_record(learner, "biology", "biology:inheritance")["next_action"] == "A separately edited action."


def test_session_close_can_resume_after_end_event(learner, monkeypatch):
    original = study.atomic_write_text
    monkeypatch.setattr(study, "atomic_write_text", lambda *a: (_ for _ in ()).throw(OSError("Simulated handover failure")))
    with pytest.raises(OSError):study.finish(learner, "biology", outcome="Actual outcome", next_action="Delayed retrieval")
    assert method.summary(learner)["closing_sessions"]
    monkeypatch.setattr(study, "atomic_write_text", original)
    result = study.finish(learner, "biology", outcome="Actual outcome", next_action="Delayed retrieval")
    assert result["memory_saved"] and result["duration_minutes"] is None
    repeated = study.finish(learner, "biology", outcome="Actual outcome", next_action="Delayed retrieval")
    assert repeated["already_saved"]
    ended, _ = events.read_events(learner, types=["session_ended"])
    assert len(ended) == 1
    assert store.load_subject_state(learner, "biology")["session_count"] == 1


def test_viewer_failure_is_not_reported_as_lost_memory(learner, monkeypatch):
    atlas_sync._cache.clear()
    monkeypatch.setattr(atlas, "build", lambda *a, **k: (_ for _ in ()).throw(OSError("Simulated renderer failure")))
    result = method.record_cycle(learner, "biology", cycle())
    assert result["memory_saved"] and not result["atlas_refreshed"]
    assert store.get_concept_record(learner, "biology", "biology:inheritance")["independent_successes"] == 1


def test_notes_and_explanations_are_saved_without_mastery(learner):
    before = store.load_concept_mastery(learner, "biology")
    result = method.note(learner, "biology", title="Open question", content="The learner asked about a boundary case.", kind="uncertainty", note_id="question-1")
    assert result["memory_saved"] and result["atlas_refreshed"]
    assert method.summary(learner)["notes"][0]["content"] == "The learner asked about a boundary case."
    assert store.load_concept_mastery(learner, "biology") == before


def test_threaded_duplicate_submissions_do_not_double_count(learner):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: method.record_cycle(learner, "biology", cycle()), range(2)))
    assert sum(r["already_saved"] for r in results) == 1
    assert store.get_concept_record(learner, "biology", "biology:inheritance")["independent_successes"] == 1


def test_unassessed_work_must_be_a_note_not_an_attempt(learner):
    with pytest.raises(LearningOSUsageError):
        method.record_cycle(learner, "biology", cycle(dimensions={"recall":{"outcome":"not_tested","rationale":"An explanation was read, not assessed."}}))
    assert not method.summary(learner)["cycles"]


def test_failed_work_is_recorded_as_failure(learner):
    data = cycle(dimensions={"application":{"outcome":"fail","rationale":"The learner used an incompatible rule."}},
                 steps=[{"work":"The actual wrong step", "judgment":"fail", "rationale":"A required assumption is false."}])
    assert method.record_cycle(learner,"biology",data)["outcome"] == "fail"


def test_resolved_uncertainty_is_removed_from_managed_handover(learner):
    method.note(learner,"biology",title="A boundary case",content="What happens at the boundary?",kind="uncertainty",note_id="uncertain-1")
    method.note(learner,"biology",title="Boundary checked",content="The source resolves the boundary condition.",kind="resolution",note_id="resolved-1",resolves="uncertain-1")
    assert next(n for n in method.summary(learner)["notes"] if n["note_id"]=="uncertain-1")["resolved"]
    study.finish(learner,"biology",outcome="Discussed a boundary case.",next_action="Independently apply a variant.")
    handover=(learner.memory_subject("biology")/"open_questions.md").read_text(encoding="utf-8")
    assert "No unresolved recorded uncertainties" in handover


def test_empty_session_receipt_does_not_claim_captured_assessments(learner):
    result=study.finish(learner,"biology",outcome="Only setup occurred.",next_action="Run the first actual diagnostic.")
    assert result["assessed_attempts"]==0 and result["saved_notes"]==0
    assert "No assessment evidence" in result["evidence_notice"]
    assert "not an automatic full-chat" in result["capture_scope"]
