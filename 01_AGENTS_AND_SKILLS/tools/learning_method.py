"""Goal-independent learning evidence with recoverable, idempotent commits.

The journal stores actual question/response/rubric data and the exact proposed projection before
canonical events or snapshots are written. Completing an interrupted commit never invents evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid

from common import LearningOSDataError, LearningOSUsageError, utc_now_iso
from memory.write_lock import serialized
from memory import events, mastery, store

TYPES = ("learning_goal_set", "learning_cycle_saved", "learning_cycle_applied", "learning_note_saved",
         "session_close_requested", "session_close_completed")
DIMENSIONS = ("explanation", "application", "recall", "derivation", "assumptions", "transfer", "method_selection", "execution")


def require(condition, text):
    if not condition:
        raise LearningOSUsageError(text)


def rows(ws):
    data, findings = events.read_events(ws)
    if findings:
        raise LearningOSDataError("The event log has unreadable evidence.", path=ws.rel(ws.event_log), remedy="Run validate; preserve the log and inspect the reported line.")
    for row in data:
        issues = events.validate_event(ws, row)
        if issues:
            raise LearningOSDataError(issues[0].message, path=ws.rel(ws.event_log), pointer=issues[0].pointer)
    return data


@serialized
def goal(ws, sid, *, purpose, mode="learn", minutes=45):
    store.require_subject(ws, sid)
    require(mode in ("learn", "exam", "interview", "professional"), "Choose learn, exam, interview or professional.")
    require(bool(purpose.strip()) and 1 <= minutes <= 1440, "Supply a learning goal and 1–1440 minutes.")
    rows(ws)
    p = dict(purpose=purpose, mode=mode, minutes=minutes)
    events.append_event(ws, type="learning_goal_set", session_id=utc_now_iso()[:10] + "-setup", subject_id=sid, payload=p)
    return p


def goals(ws):
    result = {}
    for event in rows(ws):
        if event["type"] == "learning_goal_set":
            result[event["subject_id"]] = {**event["payload"], "at": event["at"]}
    return result


def pending(ws, sid=None):
    data = rows(ws)
    applied = {e["payload"]["cycle_id"] for e in data if e["type"] == "learning_cycle_applied"}
    return [e for e in data if e["type"] == "learning_cycle_saved" and e["payload"]["input"]["cycle_id"] not in applied and (sid is None or e["subject_id"] == sid)]


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def validate_input(ws, sid, raw):
    # This validates learner evidence, not the correctness of the tutor's domain judgement.
    store.registry(ws).validate_or_raise("learning_input", raw)
    require(raw["concept_id"].startswith(sid + ":"), "Evidence must belong to the selected subject.")
    require(bool(raw["response"].strip()) and bool(raw["prompt"].strip()), "Store the actual question and learner response; summaries alone cannot earn mastery.")
    require(bool(raw["dimensions"]), "Assess at least one explicit learning dimension.")
    require(any(v["outcome"] != "not_tested" for v in raw["dimensions"].values()), "No dimension was assessed. Save an explanation or note instead of inventing an attempt.")
    require(all(str(raw[k]).strip() for k in ("cycle_id", "task_id", "feedback", "next_action")), "Use meaningful ids, feedback and an exact next action.")
    for value in raw["dimensions"].values():
        require(bool(value["rationale"].strip()), "Each judgement needs a reason grounded in the response.")
    for value in (raw.get("seconds"), raw.get("confidence")):
        require(value is None or math.isfinite(value), "Timing and confidence must be finite numbers.")
    return copy.deepcopy(raw)


def derive_outcome(data):
    checked = data["evaluation"] == "checked"
    dims = data["dimensions"]
    outcomes = [v["outcome"] for v in dims.values() if v["outcome"] != "not_tested"]
    if checked and outcomes and all(v == "fail" for v in outcomes):
        return "fail"
    if any(s["judgment"] == "fail" for s in data.get("steps", [])):
        return "partial"
    complete = all(dims.get(k, {}).get("outcome") == "pass" for k in ("explanation", "application"))
    if checked and complete:
        return "hinted_success" if data["hints"] or data["solution_seen"] else "independent_success"
    return "partial"


def normalized_prompt(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def require_coaching_allowed(ws):
    import exam_engine
    require(not any(r["status"] != "completed" and r["mode"] in ("mock", "sectional") for r in exam_engine.load(ws)["runs"].values()),
            "An unaided assessment is still open. Finish it before starting coached learning; pauses do not turn it into assisted practice.")


@serialized
def record_cycle(ws, sid, raw):
    from study_workflow import active
    data = validate_input(ws, sid, raw)
    require_coaching_allowed(ws)
    data["hints"] = max(data["hints"], len(data.get("support", [])))
    history = rows(ws)
    existing = next((e for e in history if e["type"] == "learning_cycle_saved" and e["payload"]["input"]["cycle_id"] == data["cycle_id"]), None)
    if existing:
        require(existing["subject_id"] == sid and existing["payload"]["submitted_hash"] == digest(raw), "This cycle id was already used for different evidence. Nothing was overwritten.")
        apply_cycle(ws, existing)
        return receipt(ws, existing, repeated=True)
    require(not pending(ws, sid), "An interrupted evidence save must be completed first: use study recover or /continue.")
    start = active(ws, sid)
    require(start is not None, "Start or continue this subject before recording learning.")
    require(not any(e["type"] == "session_close_requested" and e["session_id"] == start["session_id"] for e in history), "Finish the pending session save before adding more work.")
    record = store.get_concept_record(ws, sid, data["concept_id"])
    require(record is not None, "Map this concept first; source-free general learning is supported with study map --general.")
    matching = [e for e in history if e["type"] == "learning_cycle_saved" and e["subject_id"] == sid and
                (e["payload"]["input"]["task_id"] == data["task_id"] or normalized_prompt(e["payload"]["input"]["prompt"]) == normalized_prompt(data["prompt"]))]
    if matching:
        data["novelty"] = "familiar"
        if any(e["session_id"] == start["session_id"] for e in matching):
            # A retry after receiving feedback in this session cannot count as independent.
            data["solution_seen"] = True
    if data["novelty"] == "novel" and data["dimensions"].get("transfer", {}).get("outcome") != "pass":
        data["novelty"] = "variant"
    outcome = derive_outcome(data)
    moment = utc_now_iso()
    misconceptions = store.load_misconceptions(ws, sid)
    old_queue = store.load_revision_queue(ws, sid) or mastery.new_revision_queue(sid, at=moment)
    store.load_subject_state(ws, sid)
    eid = uuid.uuid4().hex
    effective_hints = max(data["hints"], 1 if data["solution_seen"] else 0)
    after = mastery.record_attempt(record, outcome=outcome, hints_used=effective_hints, novelty=data["novelty"],
        difficulty=data["difficulty"], at=moment, session_id=start["session_id"], event_id=eid,
        next_action=data["next_action"], mistake_pattern=data.get("mistake"), confidence_estimate=data.get("confidence"),
        open_misconceptions=mastery.open_misconception_count(record, misconceptions),
        blocks_promotion=mastery.blocking_misconception(record, misconceptions))
    review_before = mastery.queue_item_for(old_queue, data["concept_id"])
    review_after = mastery.schedule_review(data["concept_id"], outcome, previous=review_before, now=moment, ladder=mastery.ladder_for(old_queue))
    after["review_due_at"] = review_after["due_at"]
    store.registry(ws).validate_or_raise("concept_record", after)
    proposed_queue = mastery.upsert_queue_item(copy.deepcopy(old_queue), review_after)
    store.registry(ws).validate_or_raise("revision_queue", proposed_queue)
    p = dict(input=data, submitted_hash=digest(raw), outcome=outcome, evidence_event_id=eid,
             mastery_event_id=uuid.uuid4().hex, revision_event_id=uuid.uuid4().hex,
             record_before=record, record_after=after, review_before=review_before, review_after=review_after,
             open_misconceptions=mastery.open_misconception_count(record, misconceptions),
             blocks_promotion=mastery.blocking_misconception(record, misconceptions))
    event = events.append_event(ws, type="learning_cycle_saved", session_id=start["session_id"], subject_id=sid,
                                concept_ids=[data["concept_id"]], payload=p, at=moment)
    apply_cycle(ws, event)
    return receipt(ws, event)


@serialized
def apply_cycle(ws, event):
    sid = event["subject_id"]
    p = event["payload"]
    data = p["input"]
    history = rows(ws)
    if any(e["type"] == "learning_cycle_applied" and e["payload"]["cycle_id"] == data["cycle_id"] for e in history):
        return
    validate_input(ws, sid, data)
    for field in ("record_before", "record_after"):
        store.registry(ws).validate_or_raise("concept_record", p[field])
        require(p[field]["concept_id"] == data["concept_id"] and p[field]["subject_id"] == sid, "Journal projection belongs to another concept or subject.")
    require(p["outcome"] == derive_outcome(data), "Journal outcome does not match its stored assessment.")
    validate_projection(ws, event)
    current = store.get_concept_record(ws, sid, data["concept_id"])
    queue = store.load_revision_queue(ws, sid) or mastery.new_revision_queue(sid)
    store.load_subject_state(ws, sid)
    # Preflight every target before any writes. A divergent or corrupt record is never replaced.
    require(current in (p["record_before"], p["record_after"]), "The concept changed after this pending save. Preserve it and inspect the journal; automatic completion was refused.")
    item = mastery.queue_item_for(queue, data["concept_id"])
    require(item in (p["review_before"], p["review_after"]), "The revision item changed after this pending save; inspect it before completing recovery.")
    queue = mastery.upsert_queue_item(queue, p["review_after"])
    store.registry(ws).validate_or_raise("revision_queue", queue)
    event_ids = {e["event_id"] for e in history}
    if p["evidence_event_id"] not in event_ids:
        events.append_event(ws, type="answer_evaluated", event_id=p["evidence_event_id"], at=event["at"], session_id=event["session_id"], subject_id=sid, concept_ids=[data["concept_id"]], actor="tool",
            payload={"question_ref": "cycle:" + data["cycle_id"], "outcome": p["outcome"], "independent": p["outcome"] == "independent_success", "hints_used": max(data["hints"], int(data["solution_seen"])),
                     "difficulty": data["difficulty"], "novelty": data["novelty"], "response_seconds": data.get("seconds"), "confidence_estimate": data.get("confidence"), "notes": "Full question, response and rubric are in learning_cycle_saved."})
    if p["mastery_event_id"] not in event_ids:
        events.append_event(ws, type="mastery_updated", event_id=p["mastery_event_id"], at=event["at"], session_id=event["session_id"], subject_id=sid, concept_ids=[data["concept_id"]], actor="tool",
            payload={"concept_id": data["concept_id"], "status_before": p["record_before"]["status"], "status_after": p["record_after"]["status"], "mastery_before": p["record_before"].get("mastery_estimate"), "mastery_after": p["record_after"].get("mastery_estimate"), "independent_successes": p["record_after"]["independent_successes"], "reason": "Judged cycle: " + data["cycle_id"]})
    if p["revision_event_id"] not in event_ids:
        item = p["review_after"]
        events.append_event(ws, type="revision_scheduled", event_id=p["revision_event_id"], at=event["at"], session_id=event["session_id"], subject_id=sid, concept_ids=[data["concept_id"]], actor="tool",
            payload={"concept_id": data["concept_id"], "due_at": item["due_at"], "interval_days": item["interval_days"], "step_index": item["step_index"], "reason": item["reason"]})
    # The journal already holds exact status/revision evidence; the canonical answer event keeps
    # existing reports and reconciliation compatible, with no duplicate answer on retry.
    # put_concept_record deliberately retimestamps its argument. A durable journal instead
    # requires this exact record, including its original timestamp, to be replayable.
    cm = store.load_concept_mastery(ws, sid)
    cm["concepts"][data["concept_id"]] = copy.deepcopy(p["record_after"])
    store.save_concept_mastery(ws, sid, cm)
    store.save_revision_queue(ws, sid, queue)
    mastery.refresh_subject_counters(ws, sid, session_id=event["session_id"], concept_id=data["concept_id"], at=event["at"])
    store.write_checkpoint(ws, store.build_checkpoint(ws, subject_id=sid, session_id=event["session_id"],
        where_we_stopped=data["feedback"], next_action=data["next_action"], trigger="learning_cycle", agent="LearningOS"))
    events.append_event(ws, type="learning_cycle_applied", subject_id=sid, session_id=event["session_id"], concept_ids=[data["concept_id"]], payload={"cycle_id": data["cycle_id"]})


@serialized
def recover(ws, sid):
    unfinished = pending(ws, sid)
    for event in unfinished:
        apply_cycle(ws, event)
    from atlas_sync import refresh
    return {"recovered_cycles": len(unfinished), **refresh(ws)}


def receipt(ws, event, *, repeated=False):
    from atlas_sync import refresh
    data = event["payload"]["input"]
    first_broken = next((i + 1 for i, s in enumerate(data.get("steps", [])) if s["judgment"] == "fail"), None)
    return {"cycle_id": data["cycle_id"], "session_id": event["session_id"], "memory_saved": True, "already_saved": repeated,
            "outcome": event["payload"]["outcome"], "status": event["payload"]["record_after"]["status"],
            "first_broken_step": first_broken, "next_action": data["next_action"], **refresh(ws)}


@serialized
def note(ws, sid, *, title, content, kind="note", note_id=None, resolves=None):
    from study_workflow import active
    require(bool(title.strip()) and bool(content.strip()), "Store a title and the actual note or explanation content.")
    require(kind in ("note", "explanation", "question", "uncertainty", "resolution"), "Choose a supported note type.")
    if kind == "explanation":
        require_coaching_allowed(ws)
    start = active(ws, sid)
    require(start is not None, "Start or continue this subject first.")
    nid = note_id or uuid.uuid4().hex
    history = rows(ws)
    if kind == "resolution":
        require(resolves is not None and any(e["type"] == "learning_note_saved" and e["subject_id"] == sid and e["payload"]["note_id"] == resolves for e in history), "A resolution must reference an existing note in this subject.")
    duplicate = next((e for e in history if e["type"] == "learning_note_saved" and e["payload"]["note_id"] == nid), None)
    p = dict(note_id=nid, title=title, content=content, kind=kind)
    if resolves is not None:
        require(kind == "resolution", "Only a resolution can reference another note.")
        p["resolves_note_id"] = resolves
    if duplicate:
        require(duplicate["subject_id"] == sid and duplicate["payload"] == p, "Note id already used for different content.")
    else:
        events.append_event(ws, type="learning_note_saved", subject_id=sid, session_id=start["session_id"], payload=p)
    from atlas_sync import refresh
    return {"memory_saved": True, "note_id": nid, **refresh(ws)}


def summary(ws):
    data = rows(ws)
    applied = {e["payload"]["cycle_id"] for e in data if e["type"] == "learning_cycle_applied"}
    cycles = [{"subject_id": e["subject_id"], "session_id": e["session_id"], "at": e["at"], **e["payload"]["input"],
               "outcome": e["payload"]["outcome"], "applied": e["payload"]["input"]["cycle_id"] in applied} for e in data if e["type"] == "learning_cycle_saved"]
    starts = [e for e in data if e["type"] == "session_started"]
    closed = {e["session_id"] for e in data if e["type"] == "session_ended"}
    completed = {e["session_id"] for e in data if e["type"] == "session_close_completed"}
    closing = [e for e in data if e["type"] == "session_close_requested" and e["session_id"] not in completed]
    resolved = {(e["subject_id"], e["payload"].get("resolves_note_id")) for e in data if e["type"] == "learning_note_saved" and e["payload"]["kind"] == "resolution"}
    return {"goals": goals(ws), "cycles": cycles, "pending_cycles": len([c for c in cycles if not c["applied"]]),
            "open_sessions": [{"subject_id": e["subject_id"], "session_id": e["session_id"], "started_at": e["at"], "plan": e["payload"].get("plan")} for e in starts if e["session_id"] not in closed],
            "closing_sessions": [{"subject_id": e["subject_id"], "session_id": e["session_id"]} for e in closing],
            "notes": [{"subject_id": e["subject_id"], "session_id": e["session_id"], "at": e["at"], **e["payload"], "resolved": (e["subject_id"], e["payload"]["note_id"]) in resolved} for e in data if e["type"] == "learning_note_saved"]}


def validate_projection(ws, event):
    p, sid = event["payload"], event["subject_id"]
    data = validate_input(ws, sid, p["input"])
    for k in ("record_before", "record_after"):
        store.registry(ws).validate_or_raise("concept_record", p[k])
        require(p[k]["concept_id"] == data["concept_id"] and p[k]["subject_id"] == sid, "Journal subject/concept mismatch.")
    require(p["outcome"] == derive_outcome(data), "Journal outcome contradicts the assessment rubric.")
    expected = mastery.record_attempt(p["record_before"], outcome=p["outcome"], hints_used=max(data["hints"], int(data["solution_seen"])),
        novelty=data["novelty"], difficulty=data["difficulty"], at=event["at"], session_id=event["session_id"], event_id=p["evidence_event_id"],
        next_action=data["next_action"], mistake_pattern=data.get("mistake"), confidence_estimate=data.get("confidence"),
        open_misconceptions=p["open_misconceptions"], blocks_promotion=p["blocks_promotion"])
    expected["review_due_at"] = p["review_after"]["due_at"]
    require(expected == p["record_after"], "Journal projection does not follow the canonical mastery algorithm; preserve and inspect the event.")
    require(p["review_after"]["concept_id"] == data["concept_id"], "Journal review belongs to a different concept.")
    candidate = mastery.new_revision_queue(sid, at=event["at"])
    candidate["items"] = [p["review_after"]]
    store.registry(ws).validate_or_raise("revision_queue", candidate)


def audit(ws):
    from common import Finding
    findings = []
    data, parse_findings = events.read_events(ws)
    for event in data:
        if event.get("type") != "learning_cycle_saved" or events.validate_event(ws, event):
            continue
        try:
            validate_projection(ws, event)
        except (LearningOSUsageError, LearningOSDataError, KeyError, TypeError) as exc:
            findings.append(Finding("error", "learning_journal", str(exc), path=ws.rel(ws.event_log), remedy="Preserve this event; inspect the recorded cycle. Do not silently rewrite history or promote mastery."))
    applied = {e.get("payload", {}).get("cycle_id") for e in data if e.get("type") == "learning_cycle_applied"}
    saved = {e.get("payload", {}).get("input", {}).get("cycle_id") for e in data if e.get("type") == "learning_cycle_saved"}
    for cid in saved - applied:
        findings.append(Finding("warning", "pending_learning_save", f"Learning cycle {cid} is journalled but its snapshots still need completion.", path=ws.rel(ws.event_log), remedy="Use /continue or study recover. Exact replay is refused if files diverged or are corrupt."))
    for cid in applied - saved:
        findings.append(Finding("error", "orphan_learning_commit", f"Cycle completion {cid} has no saved evidence.", path=ws.rel(ws.event_log), remedy="Preserve the log and inspect the unmatched event."))
    completed = {e.get("session_id") for e in data if e.get("type") == "session_close_completed"}
    for e in data:
        if e.get("type") == "session_close_requested" and e["session_id"] not in completed:
            findings.append(Finding("warning", "pending_session_close", "The final handover for " + e["session_id"] + " has not finished.", path=ws.rel(ws.event_log), remedy="Use /continue or /save to complete the recorded close intent without counting the session twice."))
    return findings
