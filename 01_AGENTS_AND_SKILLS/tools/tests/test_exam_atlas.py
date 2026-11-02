"""Exam evidence, nontechnical entry points, and offline report integrity."""
import json
from pathlib import Path

import pytest

import atlas
import exam_engine as ex
import study_workflow as study
from common import LearningOSError, LearningOSUsageError, Workspace, atomic_write_text, hash_tree
from memory import store


@pytest.fixture
def bank(ws, seed_subject, cli):
    seed_subject("algo", slugs=("sorting", "recursion"))
    workspace = Workspace.at(ws)
    # Create and index a synthetic source; no real learner data is used by tests.
    source = workspace.knowledge_subject("algo") / "source_materials" / "questions.md"
    source.write_text("# Synthetic questions\nCompare sorting methods.\n", encoding="utf-8")
    assert cli("index", "--subject", "algo").code == 0
    sid = store.load_source_index(workspace, "algo")["sources"][0]["source_id"]
    ex.configure(workspace, exam_id="entry", name="Test exam", subjects=["algo"], exam_date="2027-01-01")
    q = dict(question_id="sort-1", exam_id="entry", subject_id="algo", concept_ids=["algo:sorting"],
             prompt="Which choice is correct?", format="single", choices=[{"id":"A","text":"First"},{"id":"B","text":"Second"}],
             answer="A", solution="PRIVATE_SOLUTION_ONLY_AFTER_END", marks=4, negative_marks=1, tolerance=0,
             source_id=sid, locator={"line":2}, origin="generated", verification="verified", difficulty=2, family="sorting-basic")
    ex.register_questions(workspace, [q])
    return workspace, q


def test_scoring_is_canonical_and_test_does_not_promote_mastery(bank):
    ws, q = bank
    before = hash_tree(ws.memory_subject("algo"))
    run = ex.start(ws, "entry", [q["question_id"]], mode="mock")
    assert "answer" not in run["questions"][0]
    rid = run["run_id"]
    ex.submit(ws, rid, q["question_id"], "B", seconds=12, error="reading")
    with pytest.raises(LearningOSUsageError):
        ex.review(ws, rid)
    result = ex.transition(ws, rid, "end")
    assert result["score"] == -1
    assert result["maximum"] == 4
    assert result["negative_marks_lost"] == 1
    assert "Restate" in result["rows"][0]["next_action"]
    assert hash_tree(ws.memory_subject("algo")) == before
    assert ex.load(ws) == ex.load(ws)


@pytest.mark.parametrize("fmt,key,response,expected", [("single","A","a",4),("multiple","A,B","B,A",4),("multiple","A,B","A",-1),("numeric","1.5","1.50001",4),("numeric","1.5","nan",-1),("numeric","1.5","2",-1)])
def test_marking_formats(fmt,key,response,expected):
    q = {"format":fmt,"answer":key,"marks":4,"negative_marks":1,"tolerance":0.001}
    assert ex.score(q,response) == expected


def test_manual_marking_cannot_invent_an_out_of_range_score():
    q = {"format":"manual","marks":4,"negative_marks":1}
    assert ex.score(q,"essay",awarded=2.5) == 2.5
    with pytest.raises(LearningOSUsageError):
        ex.score(q,"essay",awarded=10)
    with pytest.raises(LearningOSUsageError):
        ex.score(q,"essay")


def test_paused_resume_duplicate_and_hint_protection(bank):
    ws,q=bank; rid=ex.start(ws,"entry",[q["question_id"]],mode="mock")["run_id"]
    with pytest.raises(LearningOSUsageError):
        ex.submit(ws,rid,q["question_id"],"A",hints=1)
    ex.transition(ws,rid,"pause")
    with pytest.raises(LearningOSUsageError):
        ex.submit(ws,rid,q["question_id"],"A")
    ex.transition(ws,rid,"resume")
    ex.submit(ws,rid,q["question_id"],"B")
    with pytest.raises(LearningOSUsageError):
        ex.submit(ws,rid,q["question_id"],"A")
    ex.submit(ws,rid,q["question_id"],"A",replace=True)
    result=ex.transition(ws,rid,"end")
    assert result["score"]==4 and result["attempt_count"]==2 and result["paused"]


def test_answer_key_corrections_do_not_rewrite_started_tests(bank):
    ws,q=bank; rid=ex.start(ws,"entry",[q["question_id"]])["run_id"]
    changed={**q,"answer":"B"}
    ex.register_questions(ws,[changed],replace=True)
    ex.submit(ws,rid,q["question_id"],"A")
    assert ex.transition(ws,rid,"end")["score"]==4
    rid2=ex.start(ws,"entry",[q["question_id"]])["run_id"]
    ex.submit(ws,rid2,q["question_id"],"A")
    r=ex.transition(ws,rid2,"end")
    assert r["score"]==-1 and r["repeated_questions"]==1


def test_import_is_preflighted_and_rejects_duplicate_text_and_foreign_concepts(bank):
    ws,q=bank
    before=ws.event_log.read_bytes()
    with pytest.raises(LearningOSUsageError):
        ex.register_questions(ws,[{**q,"question_id":"new"}])
    assert ws.event_log.read_bytes()==before
    with pytest.raises(LearningOSUsageError):
        ex.register_questions(ws,[{**q,"question_id":"new","prompt":"New question","concept_ids":["other:sorting"]}])
    assert ws.event_log.read_bytes()==before


def test_plans_fit_budget_and_are_explained(bank):
    ws,q=bank
    for budget in (1,7,10,45,120):
        p=ex.plan(ws,minutes=budget,exam_id="entry")
        assert sum(t["minutes"] for t in p["tasks"])+p["reflection_minutes"]<=budget
        assert all(t["minutes"]>0 and t["reason"] for t in p["tasks"])
    ex.plan(ws,minutes=45,exam_id="entry",save=True)
    assert len(ex.load(ws)["plans"])==1


def test_atlas_escapes_injection_in_notes_and_has_no_external_dependencies(bank):
    ws,q=bank
    note=ws.memory_global/"injection-test.md"
    note.write_text('</script><img src=x onerror="alert(1)">',encoding="utf-8")
    before=hash_tree(ws.memory_subject("algo"))
    output=atlas.build(ws,now="2026-11-01T06:00:00Z")
    content=output.read_text(encoding="utf-8")
    assert '</script><img src=x' not in content
    assert '\\u003c/script\\u003e' in content
    assert 'connect-src \'none\'' in content
    assert 'src="https://' not in content and 'href="https://' not in content
    assert 'data:font/ttf;base64,' in content
    assert hash_tree(ws.memory_subject("algo"))==before
    first=output.read_bytes();atlas.build(ws,now="2026-11-01T06:00:00Z")
    assert output.read_bytes()==first


def test_open_test_solutions_do_not_leak_into_atlas(bank):
    ws,q=bank
    rid=ex.start(ws,"entry",[q["question_id"]],mode="mock")["run_id"]
    ex.submit(ws,rid,q["question_id"],"A")
    text=atlas.build(ws).read_text(encoding="utf-8")
    assert q["solution"] not in text
    ex.transition(ws,rid,"end")
    assert q["solution"] in atlas.build(ws).read_text(encoding="utf-8")


def test_corrupt_subject_is_shown_not_repaired(bank):
    ws,q=bank
    path=ws.memory_subject("algo")/"concept_mastery.json"
    path.write_text('{"broken":',encoding="utf-8")
    data=atlas.collect(ws)
    assert data["problems"] and data["manifest"]["unavailable"]
    assert path.read_text(encoding="utf-8")=='{"broken":'


def test_beginner_and_tutor_cli_help_and_lifecycle(bank,cli):
    ws,q=bank
    for cmd in ("atlas","menu","today","study","exam"):
        assert cli(cmd,"--help").code==0
    assert cli("exam","show","missing").code==2
    started=study.begin(ws,"algo")
    assert study.begin(ws,"algo")["session_id"]==started["session_id"]
    result=cli("study","record","--subject","algo","--concept","algo:sorting","--outcome","independent_success","--hints","1","--next-action","Try an unaided variant.")
    assert result.code==0,result.text
    record=store.get_concept_record(ws,"algo","algo:sorting")
    assert record["independent_successes"]==0
    assert record["retention_evidence"][-1]["outcome"]=="hinted_success"
    study.finish(ws,"algo",outcome="Practised with one hint.",next_action="Retrieve an unaided variant.")
    with pytest.raises(LearningOSUsageError):
        study.finish(ws,"algo",outcome="Duplicate",next_action="No")
    assert (ws.memory_subject("algo")/"next_session.md").exists()
    assert list((ws.root/"03_MEMORY/checkpoints/algo").glob("*.json"))
    assert list((ws.root/"03_MEMORY/session_logs").glob("*algo*.md"))
    assert cli("validate").code==0,cli("validate").text


def test_mapping_adds_only_unassessed_source_backed_concepts(bank):
    ws,q=bank
    c={"concept_id":"algo:merging","name":"Merging","unit":"unit-1","description":"Merge sorted runs.","prerequisite_ids":["algo:sorting"],"source_refs":[{"source_id":q["source_id"],"locator":{"line":2}}],"exam_weight":None,"tags":[]}
    study.import_concepts(ws,"algo",[c])
    r=store.get_concept_record(ws,"algo",c["concept_id"])
    assert r["status"]=="unassessed" and r["independent_successes"]==0 and r["mastery_estimate"] is None


def test_invalid_key_is_rejected_before_writing(bank):
    ws,q=bank; before=ws.event_log.read_bytes()
    with pytest.raises(LearningOSUsageError):
        ex.register_questions(ws,[{**q,"answer":"Z"}],replace=True)
    assert ws.event_log.read_bytes()==before


def test_error_diagnosis_is_audited_without_changing_marks(bank):
    ws,q=bank;rid=ex.start(ws,"entry",[q["question_id"]])["run_id"]
    ex.submit(ws,rid,q["question_id"],"B",confidence=.9)
    ex.transition(ws,rid,"end")
    ex.classify(ws,rid,q["question_id"],"reading",note="Missed the final condition.",basis="confirmed")
    result=ex.review(ws,rid)
    assert result["score"]==-1
    assert result["rows"][0]["error_basis"]=="confirmed"
    assert result["confidence_sample_count"]==1
    assert result["confidence_calibration_error"]==.9


def test_unverified_questions_cannot_enter_a_scored_test(bank):
    ws,q=bank
    ex.register_questions(ws,[{**q,"verification":"disputed"}],replace=True)
    with pytest.raises(LearningOSUsageError):
        ex.start(ws,"entry",[q["question_id"]])


def test_bad_log_is_never_silently_repaired(bank):
    ws,q=bank
    with ws.event_log.open("a",encoding="utf-8") as f:f.write('{invalid-json\n')
    before=ws.event_log.read_bytes()
    with pytest.raises(LearningOSError):ex.load(ws)
    assert ws.event_log.read_bytes()==before
    data=atlas.collect(ws)
    assert data["problems"]
    assert ws.event_log.read_bytes()==before


def test_source_drop_directory_is_visible_as_warning_not_fake_subject(ws,cli):
    directory=ws/"02_KNOWLEDGE/subjects/Computer networks"
    directory.mkdir();original=directory/"notes.txt";original.write_text("Original material",encoding="utf-8")
    result=cli("validate")
    assert result.code==0,result.text
    assert "unassigned_source_folder" in result.out
    assert "Computer networks" not in Workspace.at(ws).list_subjects()
    assert original.read_text(encoding="utf-8")=="Original material"


def test_menu_accepts_a_simple_number_to_exit(ws,cli,monkeypatch):
    monkeypatch.setattr("builtins.input",lambda prompt:"0")
    assert cli("menu").code==0


def test_mapping_never_replaces_mastery_missing_from_catalog(bank):
    ws,q=bank
    cm=store.load_concept_mastery(ws,"algo")
    before=json.dumps(cm,sort_keys=True)
    catalog=store.load_concept_catalog(ws,"algo")
    catalog["concepts"]=[];store.save_concept_catalog(ws,"algo",catalog)
    c={"concept_id":"algo:sorting","name":"Sorting","unit":"unit-1","description":"Sort.","prerequisite_ids":[],"source_refs":[{"source_id":q["source_id"],"locator":{"line":2}}],"exam_weight":None,"tags":[]}
    with pytest.raises(LearningOSUsageError):study.import_concepts(ws,"algo",[c])
    assert json.dumps(store.load_concept_mastery(ws,"algo"),sort_keys=True)==before


def test_retest_selects_unseen_verified_family_variants(bank,cli):
    ws,q=bank
    ex.register_questions(ws,[{**q,"question_id":"sort-variant","prompt":"A different surface form in the same family."}])
    rid=ex.start(ws,"entry",[q["question_id"]])["run_id"]
    ex.submit(ws,rid,q["question_id"],"B");ex.transition(ws,rid,"end")
    result=cli("exam","retest",rid)
    assert result.code==0,result.text
    data=json.loads(result.out)
    assert data["questions"][0]["question_id"]=="sort-variant"
    assert "answer" not in data["questions"][0]


def test_exam_evidence_survives_backup_restore_roundtrip(bank,cli):
    ws,q=bank
    rid=ex.start(ws,"entry",[q["question_id"]])["run_id"]
    ex.submit(ws,rid,q["question_id"],"A");ex.transition(ws,rid,"end")
    expected=ex.load(ws)
    result=cli("backup","--json")
    assert result.code==0,result.text
    payload=json.loads(result.out)
    backup_id=payload["backup_id"]
    ex.plan(ws,minutes=30,save=True)
    assert cli("restore","--from",backup_id,"--dry-run").code==0
    assert cli("restore","--from",backup_id,"--force").code==0
    assert ex.load(ws)==expected


def test_holdout_prompts_and_keys_are_not_exposed_by_training_or_atlas(bank):
    ws,q=bank
    held={**q,"question_id":"holdout-1","prompt":"RESERVED_UNSEEN_PROMPT","solution":"RESERVED_UNSEEN_KEY","usage":"holdout"}
    ex.register_questions(ws,[held])
    with pytest.raises(LearningOSUsageError):ex.start(ws,"entry",["holdout-1"],mode="practice")
    serialized=json.dumps(atlas.collect(ws)["events"])
    assert held["prompt"] not in serialized and held["solution"] not in serialized
    assert ex.start(ws,"entry",["holdout-1"],mode="mock")["run_id"]


def test_open_mock_cannot_be_used_as_a_coached_lesson(bank):
    ws,q=bank
    rid=ex.start(ws,"entry",[q["question_id"]],mode="mock")["run_id"]
    with pytest.raises(LearningOSUsageError):study.begin(ws,"algo")
    ex.transition(ws,rid,"pause")
    with pytest.raises(LearningOSUsageError):study.begin(ws,"algo")
    ex.transition(ws,rid,"end")
    assert study.begin(ws,"algo")["session_id"]
