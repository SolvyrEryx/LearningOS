"""Validated tutor commands and a plain-language, budgeted daily plan."""
from __future__ import annotations

import json
from datetime import datetime

from common import LearningOSUsageError, atomic_write_text, read_json, utc_now_iso
from memory.write_lock import serialized
from memory import events, mastery, report, store
import exam_engine


def session_rows(ws, sid):
    return events.read_events(ws, subject_id=sid)[0]


def active(ws, sid):
    rows = session_rows(ws, sid)
    starts = [e for e in rows if e["type"] == "session_started"]
    ended = {e["session_id"] for e in rows if e["type"] == "session_ended"}
    return next((e for e in reversed(starts) if e["session_id"] not in ended), None)


@serialized
def begin(ws, sid, *, minutes=45, plan="Diagnose prerequisites, retrieve independently, then choose the smallest useful next step."):
    store.require_subject(ws, sid)
    import learning_method as method
    method.rows(ws)
    method.require_coaching_allowed(ws)
    exam_engine.require(1 <= minutes <= 1440, "Session budget must be 1–1440 minutes.")
    state_summary = method.summary(ws)
    exam_engine.require(not state_summary["closing_sessions"], "A session save is incomplete. Use /save or study recover before starting another.")
    exam_engine.require(not any(s["subject_id"] != sid for s in state_summary["open_sessions"]), "Another subject session is open. Save it before switching subjects.")
    existing = active(ws, sid)
    if existing:
        return {"session_id": existing["session_id"], "message": "Resuming the existing open session."}
    state = store.load_workspace_state(ws)
    store.load_subject_state(ws, sid)
    store.load_concept_mastery(ws, sid)
    now = utc_now_iso()
    prefix = now[:10] + "-" + sid + "-"
    number = 1 + sum(e["type"] == "session_started" and e["session_id"].startswith(prefix) for e in session_rows(ws, sid))
    session_id = prefix + f"{number:02}"
    events.append_event(ws, type="session_started", session_id=session_id, subject_id=sid, payload={"agent": "LearningOS", "planned_minutes": minutes, "plan": plan})
    state.update(current_subject_id=sid, last_session_id=session_id, last_session_at=now)
    store.save_workspace_state(ws, state)
    return {"session_id": session_id, "plan": plan}


@serialized
def import_concepts(ws, sid, data, *, general=False):
    """Add derived source mappings, never overwrite an existing concept's evidence."""
    store.require_subject(ws, sid)
    exam_engine.require(isinstance(data, list) and bool(data), "Supply a JSON list of source-backed concept records.")
    catalog = store.load_concept_catalog(ws, sid)
    current = store.load_concept_mastery(ws, sid)
    sources = {s["source_id"] for s in (store.load_source_index(ws, sid) or {}).get("sources", [])}
    now = utc_now_iso()
    existing = {c["concept_id"]: c for c in catalog.get("concepts", [])}
    prepared = []
    for c in data:
        cid = c.get("concept_id", "")
        exam_engine.require(cid.startswith(sid + ":"), "Concept id must belong to this subject.")
        exam_engine.slug(cid.split(":", 1)[1])
        exam_engine.require(cid not in existing and cid not in current.get("concepts", {}) and not any(x["concept_id"] == cid for x in prepared), "Concept already mapped; existing evidence will not be replaced.")
        exam_engine.require((general or bool(c.get("source_refs"))) and all(r.get("source_id") in sources for r in c.get("source_refs", [])), "Every concept needs an indexed source reference.")
        exam_engine.require(all(r.get("locator", {}).get("page") or r.get("locator", {}).get("line") for r in c["source_refs"]), "Every concept source reference needs a page or line locator.")
        if general and not c.get("source_refs"):
            c = {**c, "source_refs": [], "tags": sorted(set(c.get("tags", []) + ["general-background", "not-source-verified"]))}
        prepared.append(c)
    proposed = {**catalog, "concepts": [*catalog.get("concepts", []), *prepared], "updated_at": now}
    store.registry(ws).validate_or_raise("concept_catalog", proposed)
    known = set(existing) | {c["concept_id"] for c in prepared} | set(current.get("concepts", {}))
    for c in prepared:
        exam_engine.require(all(p in known for p in c.get("prerequisite_ids", [])), "All prerequisite ids must exist in the same subject map before import.")
        record = store.new_concept_record(c["concept_id"], c["name"], subject_id=sid, unit=c.get("unit"), prerequisite_ids=c.get("prerequisite_ids", []), source_refs=c["source_refs"], at=now)
        store.registry(ws).validate_or_raise("concept_record", record)
    # Append the complete validated additions so the source mapping operation is auditable.
    path = ws.memory_subject(sid) / ("concept-import-" + now.replace(":", "").replace("-", "") + "-" + __import__("uuid").uuid4().hex[:8] + ".json")
    events.append_event(ws, type="note_saved", session_id=now[:10] + "-source-mapping", subject_id=sid, concept_ids=[c["concept_id"] for c in prepared],
                        payload={"relative_path": ws.rel(path), "title": "Source-derived unassessed concept additions", "kind": "concept_mapping"})
    from common import atomic_write_json
    atomic_write_json(path, {"schema_version": 1, "subject_id": sid, "concepts": prepared})
    store.save_concept_catalog(ws, sid, proposed)
    for c in prepared:
        store.put_concept_record(ws, sid, store.new_concept_record(c["concept_id"], c["name"], subject_id=sid, unit=c.get("unit"), prerequisite_ids=c.get("prerequisite_ids", []), source_refs=c["source_refs"], at=now))
    st = store.load_subject_state(ws, sid)
    st["coverage"]["planned"] = len(proposed["concepts"])
    store.save_subject_state(ws, sid, st)
    return {"mapped": len(prepared), "learning_state": "unassessed", "message": "Concepts mapped as unassessed; general-background concepts are explicitly labelled. No performance inferred."}


@serialized
def finish(ws, sid, *, outcome, next_action, duration_minutes=None):
    """Durable close intent makes every later step safely retryable after interruption."""
    import learning_method as method
    from atlas_sync import refresh
    store.require_subject(ws, sid)
    method.require(bool(outcome.strip()) and bool(next_action.strip()), "Save a concrete outcome and next action.")
    method.require(duration_minutes is None or 0 <= duration_minutes <= 1440, "Reported session time must be 0–1440 minutes.")
    history = method.rows(ws)
    completed = {e["session_id"] for e in history if e["type"] == "session_close_completed"}
    requests = [e for e in history if e["type"] == "session_close_requested" and e["subject_id"] == sid]
    request = next((e for e in reversed(requests) if e["session_id"] not in completed), None)
    start = active(ws, sid)
    if request is None and start is None:
        last = requests[-1] if requests else None
        method.require(last is not None and last["payload"]["outcome"] == outcome and last["payload"]["next_action"] == next_action, "No open subject session. Nothing was counted or changed.")
        return {"session_id": last["session_id"], "memory_saved": True, "already_saved": True, "next_action": next_action, **refresh(ws)}
    # Finish only known pending evidence; divergence or corruption is refused before closure.
    for event in method.pending(ws, sid):
        method.apply_cycle(ws, event)
    state = store.load_subject_state(ws, sid)
    cm = store.load_concept_mastery(ws, sid)
    store.load_revision_queue(ws, sid)
    store.load_misconceptions(ws, sid)
    store.load_workspace_state(ws)
    if request is None:
        request = events.append_event(ws, type="session_close_requested", session_id=start["session_id"], subject_id=sid,
            payload={"outcome": outcome, "next_action": next_action, "duration_minutes": duration_minutes})
    # Retrying uses the original close intent, not newly guessed summary text.
    intent = request["payload"]
    session_id = request["session_id"]
    outcome, next_action = intent["outcome"], intent["next_action"]
    history = method.rows(ws)
    session = [e for e in history if e["session_id"] == session_id]
    answers = [e for e in session if e["type"] == "answer_evaluated"]
    touched = sorted({cid for e in session for cid in e.get("concept_ids", [])})
    now = utc_now_iso()
    if not any(e["type"] == "session_ended" for e in session):
        events.append_event(ws, type="session_ended", session_id=session_id, subject_id=sid,
            payload={"duration_minutes": intent["duration_minutes"], "concepts_touched": touched, "outcome": outcome,
                     "independent_successes": sum(e["payload"]["outcome"] == "independent_success" and e["payload"].get("hints_used", 0) == 0 for e in answers),
                     "hinted_successes": sum(e["payload"]["outcome"] == "hinted_success" for e in answers)})
    endings = [e for e in session_rows(ws, sid) if e["type"] == "session_ended"]
    state.update(session_count=len({e["session_id"] for e in endings}), total_minutes=round(sum(e["payload"].get("duration_minutes") or 0 for e in endings), 2), last_session_id=session_id, last_session_at=now)
    store.save_subject_state(ws, sid, state)
    store.save_concept_mastery(ws, sid, cm)
    handover = f"# Next session\n\nLast session: {session_id}\n\n## Where we stopped\n\n{outcome}\n\n## First activity\n\n{next_action}\n\n## Concepts touched\n\n" + "\n".join("- " + cid for cid in touched) + "\n"
    atomic_write_text(ws.memory_subject(sid) / "next_session.md", handover)
    question_path = ws.memory_subject(sid) / "open_questions.md"
    original_questions = question_path.read_text(encoding="utf-8") if question_path.exists() else "# Open questions\n"
    begin_marker, end_marker = "<!-- learningos:uncertainties -->", "<!-- /learningos:uncertainties -->"
    uncertainties = [n for n in method.summary(ws)["notes"] if n["subject_id"] == sid and n["kind"] == "uncertainty" and not n["resolved"]]
    managed = begin_marker + "\n\n## Recorded uncertainties\n\n" + ("\n".join("- " + n["title"] + ": " + n["content"] for n in uncertainties) or "No unresolved recorded uncertainties.") + "\n\n" + end_marker
    if begin_marker in original_questions and end_marker in original_questions:
        before, remaining = original_questions.split(begin_marker, 1)
        _, after = remaining.split(end_marker, 1)
        question_text = before + managed + after
    else:
        question_text = original_questions.rstrip() + "\n\n" + managed + "\n"
    atomic_write_text(question_path, question_text)
    store.write_checkpoint(ws, store.build_checkpoint(ws, subject_id=sid, session_id=session_id, where_we_stopped=outcome, next_action=next_action, trigger="session_end", agent="LearningOS"))
    state_global = store.load_workspace_state(ws)
    state_global.update(last_session_id=session_id, last_session_at=now, current_subject_id=sid)
    store.save_workspace_state(ws, state_global)
    report_data = report.build_report(ws, session_id, subject=sid)
    # Session reports are derived; replace any interim report with the actual closing evidence.
    report.write_report(ws, report_data, force=True)
    events.append_event(ws, type="session_close_completed", session_id=session_id, subject_id=sid, payload={"next_action": next_action})
    note_count = sum(e["type"] == "learning_note_saved" for e in session)
    return {"session_id": session_id, "memory_saved": True, "assessed_attempts": len(answers), "saved_notes": note_count,
            "capture_scope": "Recorded questions, responses, notes and handover; not an automatic full-chat transcript.",
            "evidence_notice": "No assessment evidence was recorded; the session handover is saved." if not answers else "Recorded assessment evidence is saved.",
            "duration_minutes": intent["duration_minutes"], "duration_basis": "learner_reported" if intent["duration_minutes"] is not None else "not_recorded", "next_action": next_action, **refresh(ws)}


def add_arguments(parser):
    sub = parser.add_subparsers(dest="study_action", required=True)
    p = sub.add_parser("start"); p.add_argument("--subject", required=True); p.add_argument("--minutes", type=int, default=45)
    p = sub.add_parser("map"); p.add_argument("--subject", required=True); p.add_argument("file"); p.add_argument("--general", action="store_true")
    p = sub.add_parser("cycle", help="Save actual response, rubric, solution steps and learning evidence"); p.add_argument("--subject", required=True); p.add_argument("file")
    p = sub.add_parser("note", help="Save an explanation, question, uncertainty or resolution"); p.add_argument("--subject", required=True); p.add_argument("--title", required=True); p.add_argument("--content", required=True); p.add_argument("--kind", choices=("note", "explanation", "question", "uncertainty", "resolution"), default="note"); p.add_argument("--id"); p.add_argument("--resolves")
    p = sub.add_parser("recover"); p.add_argument("--subject", required=True)
    p = sub.add_parser("status"); p.add_argument("--subject")
    p = sub.add_parser("record"); p.add_argument("--subject", required=True); p.add_argument("--concept", required=True)
    p.add_argument("--outcome", choices=("independent_success", "hinted_success", "partial", "fail"), required=True)
    p.add_argument("--hints", type=int, default=0); p.add_argument("--novelty", choices=("familiar", "variant", "novel"), default="variant")
    p.add_argument("--difficulty", type=int, choices=range(1, 6), default=3); p.add_argument("--seconds", type=float)
    p.add_argument("--mistake"); p.add_argument("--next-action", required=True); p.add_argument("--question")
    p = sub.add_parser("end"); p.add_argument("--subject", required=True); p.add_argument("--outcome", required=True); p.add_argument("--next-action", required=True); p.add_argument("--minutes", type=float)


def handler(args, ws):
    import learning_method as method
    action = args.study_action
    if action == "start":
        result = begin(ws, args.subject, minutes=args.minutes)
    elif action == "map":
        result = import_concepts(ws, args.subject, read_json(args.file), general=args.general)
    elif action == "cycle":
        result = method.record_cycle(ws, args.subject, read_json(args.file))
    elif action == "note":
        result = method.note(ws, args.subject, title=args.title, content=args.content, kind=args.kind, note_id=args.id, resolves=args.resolves)
    elif action == "recover":
        result = method.recover(ws, args.subject)
        state = method.summary(ws)
        if any(s["subject_id"] == args.subject for s in state["closing_sessions"]):
            request = next(e for e in reversed(method.rows(ws)) if e["type"] == "session_close_requested" and e["subject_id"] == args.subject)
            result["session_close"] = finish(ws, args.subject, outcome=request["payload"]["outcome"], next_action=request["payload"]["next_action"])
    elif action == "status":
        result = method.summary(ws)
        if args.subject:
            store.require_subject(ws, args.subject)
            result["goals"] = {sid: g for sid, g in result["goals"].items() if sid == args.subject}
            for key in ("cycles", "notes", "open_sessions", "closing_sessions"):
                result[key] = [entry for entry in result[key] if entry["subject_id"] == args.subject]
            result["pending_cycles"] = sum(not c["applied"] for c in result["cycles"])
    elif action == "record":
        start = active(ws, args.subject)
        exam_engine.require(start is not None, "Start or resume this subject session first.")
        exam_engine.require(args.hints >= 0, "Hints cannot be negative.")
        exam_engine.require(not method.pending(ws, args.subject), "Complete the pending cycle with study recover first.")
        exam_engine.require(args.outcome != "independent_success" or args.hints > 0, "Unaided success requires the full response and explanation/application rubric. Use study cycle instead of a bare outcome label.")
        r = mastery.record_attempt_and_save(ws, args.subject, args.concept, session_id=start["session_id"], outcome=args.outcome,
                                           hints_used=args.hints, novelty=args.novelty, difficulty=args.difficulty,
                                           response_seconds=args.seconds, mistake_pattern=args.mistake,
                                           next_action=args.next_action, question_ref=args.question)
        store.write_checkpoint(ws, store.build_checkpoint(ws, subject_id=args.subject, session_id=start["session_id"], where_we_stopped=f"Recorded {r.record['retention_evidence'][-1]['outcome']} for {args.concept}.", next_action=args.next_action, trigger="attempt_recorded", agent="LearningOS"))
        result = {"concept_id": args.concept, "status": r.status_after, "review_due_at": r.record.get("review_due_at")}
        from atlas_sync import refresh
        result.update(memory_saved=True, **refresh(ws))
    else:
        result = finish(ws, args.subject, outcome=args.outcome, next_action=args.next_action, duration_minutes=args.minutes)
    if action in ("start", "map"):
        from atlas_sync import refresh
        result.update(memory_saved=True, **refresh(ws))
    print(json.dumps(result, indent=2))
    return 0


def today_args(parser):
    parser.add_argument("--minutes", type=int, default=45); parser.add_argument("--exam"); parser.add_argument("--save", action="store_true")


def today_handler(args, ws):
    data = exam_engine.plan(ws, minutes=args.minutes, exam_id=args.exam, save=args.save)
    if args.save:
        from atlas_sync import refresh
        data.update(memory_saved=True, **refresh(ws))
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"YOUR NEXT {args.minutes} MINUTES\n")
        for i, t in enumerate(data["tasks"], 1):
            print(f"{i}. [{t['minutes']} min] {t['action']}\n   Why: {t['reason']}\n")
        if not data["tasks"]:
            print("Add a subject and map your syllabus to get a useful starting plan.")
        if data["reflection_minutes"]:
            print(f"Keep {data['reflection_minutes']} minutes to retrieve what you learned and save the session.")
    return 0
