"""Local exam operations. Events are truth; all projections are replayed, never double-written.

Exam scores deliberately do not promote learning mastery. A separate judged explanation and
application belongs in the tutor's assessment workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from common import LearningOSDataError, LearningOSUsageError, read_json, utc_now_iso
from memory.write_lock import serialized
from memory import events, store

EVENT_TYPES = ("exam_configured", "question_registered", "practice_started", "practice_attempted",
               "practice_paused", "practice_resumed", "practice_ended", "practice_error_classified", "study_plan_created")
ERRORS = ("conceptual", "recall", "procedure", "calculation", "reading", "strategy", "time", "unknown")


def require(condition, message):
    if not condition:
        raise LearningOSUsageError(message)


def slug(value):
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value or "")), "Use a short id with lowercase letters, numbers and hyphens.")
    return value


def load(ws):
    """Pure replay. Refuse broken evidence instead of returning a misleading partial state."""
    state = {"exams": {}, "questions": {}, "runs": {}, "plans": []}
    rows, findings = events.read_events(ws)
    if any(f.is_error for f in findings):
        raise LearningOSDataError("Unreadable event evidence; no exam state was changed.", path=ws.rel(ws.event_log), remedy="Run validate; preserve the log and inspect the reported line.")
    for event in rows:
        kind = event["type"]
        # Validate on replay too: malformed new records must never become accepted state.
        problems = events.validate_event(ws, event)
        if problems:
            raise LearningOSDataError("Invalid exam event: " + problems[0].message, path=ws.rel(ws.event_log))
        if kind not in EVENT_TYPES:
            continue
        p = dict(event["payload"])
        if kind == "exam_configured":
            state["exams"][p["exam_id"]] = p
        elif kind == "question_registered":
            p["version"] = event["event_id"]
            state["questions"][p["question_id"]] = p
        elif kind == "practice_started":
            state["runs"][p["run_id"]] = {**p, "started_at": event["at"], "status": "running", "attempts": [], "pauses": [], "ended_at": None}
        elif kind == "study_plan_created":
            state["plans"].append({**p, "at": event["at"]})
        else:
            run = state["runs"].get(p["run_id"])
            if run is None:
                raise LearningOSDataError("Exam event references a missing practice run.", path=ws.rel(ws.event_log))
            if kind == "practice_attempted":
                run["attempts"].append({**p, "at": event["at"], "event_id": event["event_id"]})
            elif kind == "practice_error_classified":
                for attempt in reversed(run["attempts"]):
                    if attempt["question_id"] == p["question_id"]:
                        attempt.update(error=p["error"], error_note=p["note"], error_basis=p["basis"])
                        break
            elif kind == "practice_paused":
                run["status"] = "paused"
                run["pauses"].append({"start": event["at"], "end": None})
            elif kind == "practice_resumed":
                run["status"] = "running"
                run["pauses"][-1]["end"] = event["at"]
            else:
                run.update(status="completed", ended_at=event["at"])
    return state


def emit(ws, kind, payload, *, session=None, concepts=None):
    return events.append_event(ws, type=kind, session_id=session or utc_now_iso()[:10] + "-exam-tools",
                               subject_id=None, concept_ids=concepts or [], payload=payload, actor="tool")


@serialized
def configure(ws, *, exam_id, name, subjects, exam_date=None, daily_minutes=60, duration_minutes=180, target=None):
    slug(exam_id)
    require(bool(name.strip()), "An exam needs a name.")
    require(bool(subjects), "Choose at least one initialized subject.")
    for sid in subjects:
        store.require_subject(ws, sid)
    if exam_date:
        try:
            datetime.strptime(exam_date, "%Y-%m-%d")
        except ValueError:
            raise LearningOSUsageError("Exam date must be a real YYYY-MM-DD date.")
    require(1 <= daily_minutes <= 1440 and 1 <= duration_minutes <= 1440, "Minutes must be between 1 and 1440.")
    load(ws)
    p = dict(exam_id=exam_id, name=name, subject_ids=sorted(set(subjects)), exam_date=exam_date,
             daily_minutes=daily_minutes, duration_minutes=duration_minutes, target=target, rules_note="Marking is stored per question; verify it against the official exam rules.")
    emit(ws, "exam_configured", p)
    return p


@serialized
def register_questions(ws, data, *, replace=False):
    """Preflight the entire import before appending. Keys are immutable in started runs."""
    state = load(ws)
    require(isinstance(data, list) and len(data) > 0, "Import a JSON list containing at least one question.")
    seen = set()
    fingerprints = {q["fingerprint"]: q["question_id"] for q in state["questions"].values()}
    prepared = []
    for raw in data:
        require(isinstance(raw, dict), "Each question must be an object.")
        q = dict(raw)
        q.pop("version", None)
        q["fingerprint"] = hashlib.sha256(" ".join(str(q.get("prompt", "")).lower().split()).encode()).hexdigest()
        schema = {"schema_version": 1, "event_id": uuid.uuid4().hex, "at": utc_now_iso(), "session_id": "question-import",
                  "type": "question_registered", "actor": "tool", "subject_id": None, "concept_ids": q.get("concept_ids", []), "payload": q}
        errors = events.validate_event(ws, schema)
        if errors:
            raise LearningOSDataError(errors[0].message, pointer=errors[0].pointer, remedy="Use the question-import example and the documented question schema.")
        slug(q["question_id"])
        require(all(math.isfinite(q[k]) for k in ("marks", "negative_marks", "tolerance")), "Marking values must be finite.")
        require(bool(q["locator"]), "A question needs a specific source page or line locator.")
        require(q["question_id"] not in seen, "Duplicate question id inside this import.")
        require(q["question_id"] not in state["questions"] or replace, "Question already exists. Use --replace to append a corrected version.")
        require(q["fingerprint"] not in fingerprints or fingerprints[q["fingerprint"]] == q["question_id"], "Duplicate question text; keep one identity for exposure tracking.")
        exam = state["exams"].get(q["exam_id"])
        require(exam is not None, "Set up the question's exam first.")
        require(q["subject_id"] in exam["subject_ids"], "Question subject is not part of this exam.")
        catalog = store.load_concept_catalog(ws, q["subject_id"]) or {}
        mastery = store.load_concept_mastery(ws, q["subject_id"])
        known = set(mastery.get("concepts", {})) | {c["concept_id"] for c in catalog.get("concepts", [])}
        require(all(cid.startswith(q["subject_id"] + ":") and cid in known for cid in q["concept_ids"]), "Question concepts must exist in their owning subject. Use one owning subject per question.")
        source = store.load_source_index(ws, q["subject_id"]) or {}
        entries = source.get("sources", [])
        if isinstance(entries, dict):
            entries = list(entries.values())
        require(any(s["source_id"] == q["source_id"] for s in entries), "Question source_id must be in the subject's source index.")
        if q["format"] == "numeric":
            try:
                require(Decimal(q["answer"]).is_finite(), "Numeric answer must be finite.")
            except InvalidOperation:
                raise LearningOSUsageError("Numeric answer must be a number.")
        if q["format"] in ("single", "multiple"):
            valid = {c["id"] for c in q["choices"]}
            require(len(valid) == len(q["choices"]) and len(valid) >= 2, "Choice ids must be unique; provide at least two choices.")
            keys = q["answer"].split(",") if q["format"] == "multiple" else [q["answer"]]
            require(all(k.strip() in valid for k in keys), "Answer key must refer to listed choices.")
        seen.add(q["question_id"])
        fingerprints[q["fingerprint"]] = q["question_id"]
        prepared.append(q)
    for q in prepared:
        emit(ws, "question_registered", q, concepts=q["concept_ids"])
    return {"imported": len(prepared)}


@serialized
def start(ws, exam_id, question_ids, *, mode="practice", duration_minutes=None):
    state = load(ws)
    require(exam_id in state["exams"], "Set up this exam first.")
    require(mode in ("practice", "sectional", "mock"), "Unknown practice mode.")
    require(bool(question_ids) and len(question_ids) == len(set(question_ids)), "Choose distinct question ids.")
    require(not any(r["status"] != "completed" for r in state["runs"].values()), "A test is already open. Resume or finish it before starting another.")
    frozen = []
    for qid in question_ids:
        q = state["questions"].get(qid)
        require(q is not None and q["exam_id"] == exam_id, "Question is missing or belongs to another exam.")
        require(q["verification"] == "verified", "Resolve unverified/disputed answer keys before scored practice.")
        require(mode != "practice" or q.get("usage", "practice") != "holdout", "This question is reserved for unseen assessment. Use a mock or sectional test, not training.")
        exposures = sum(qid in r["question_ids"] for r in state["runs"].values())
        frozen.append({**q, "previous_exposures": exposures})
    duration = duration_minutes or state["exams"][exam_id]["duration_minutes"]
    require(1 <= duration <= 1440, "Test duration must be 1–1440 minutes.")
    run_id = "test-" + uuid.uuid4().hex[:12]
    p = dict(run_id=run_id, exam_id=exam_id, mode=mode, duration_minutes=duration,
             question_ids=question_ids, questions=frozen)
    emit(ws, "practice_started", p, session=run_id)
    return {"run_id": run_id, "mode": mode, "questions": [public_question(q) for q in frozen]}


def public_question(q):
    return {k: q[k] for k in ("question_id", "prompt", "choices", "format", "marks", "negative_marks", "source_id", "locator", "previous_exposures") if k in q}


def score(q, response, *, skipped=False, awarded=None):
    if skipped:
        return 0.0
    if q["format"] == "manual":
        require(awarded is not None and math.isfinite(awarded) and -q["negative_marks"] <= awarded <= q["marks"], "Manual marking requires a finite awarded score within the question's marking limits.")
        return float(awarded)
    if q["format"] == "numeric":
        try:
            value = Decimal(str(response))
            correct = value.is_finite() and abs(value - Decimal(q["answer"])) <= Decimal(str(q["tolerance"]))
        except InvalidOperation:
            correct = False
    elif q["format"] == "multiple":
        correct = {x.strip().casefold() for x in response.split(",")} == {x.strip().casefold() for x in q["answer"].split(",")}
    else:
        correct = response.strip().casefold() == q["answer"].strip().casefold()
    return float(q["marks"] if correct else -q["negative_marks"])


def get_run(ws, run_id):
    run = load(ws)["runs"].get(run_id)
    require(run is not None, "Unknown test id. Use practice list.")
    return run


@serialized
def submit(ws, run_id, question_id, response, *, seconds=None, timing="reported", skipped=False,
           hints=0, error="unknown", confidence=None, replace=False, awarded=None):
    run = get_run(ws, run_id)
    require(run["status"] == "running", "This test is paused or finished.")
    require(question_id in run["question_ids"], "Question is not in this test.")
    require(replace or not any(a["question_id"] == question_id for a in run["attempts"]), "Already submitted; use --replace to record a revisit.")
    require(hints >= 0 and (run["mode"] == "practice" or hints == 0), "Mocks and sectional tests cannot include hints.")
    require(seconds is None or math.isfinite(seconds) and seconds >= 0, "Time must be finite and nonnegative.")
    require(error in ERRORS, "Unknown error category.")
    require(confidence is None or math.isfinite(confidence) and 0 <= confidence <= 1, "Confidence must be 0–1.")
    q = next(q for q in run["questions"] if q["question_id"] == question_id)
    marks = score(q, response, skipped=skipped, awarded=awarded)
    p = dict(run_id=run_id, question_id=question_id, response=response, seconds=seconds,
             timing=timing if seconds is not None else "unknown", skipped=skipped, hints=hints,
             error=error, confidence=confidence, awarded=marks, question_version=q["version"],
             marking="manual_reported" if q["format"] == "manual" else "verified_key")
    emit(ws, "practice_attempted", p, session=run_id, concepts=q["concept_ids"])
    # Solutions and even per-question correctness remain hidden until the test is ended.
    return {"saved": question_id, "message": "Answer saved. Finish the test to review results."}


@serialized
def transition(ws, run_id, action):
    run = get_run(ws, run_id)
    require(action in ("pause", "resume", "end"), "Unknown test action.")
    if action == "pause":
        require(run["status"] == "running", "Only a running test can be paused.")
    elif action == "resume":
        require(run["status"] == "paused", "Only a paused test can be resumed.")
    else:
        require(run["status"] != "completed", "Test already finished.")
    emit(ws, {"pause": "practice_paused", "resume": "practice_resumed", "end": "practice_ended"}[action], {"run_id": run_id}, session=run_id)
    return review(ws, run_id) if action == "end" else {"run_id": run_id, "status": action}


@serialized
def classify(ws, run_id, question_id, category, *, note, basis="reported"):
    run = get_run(ws, run_id)
    require(run["status"] == "completed", "Classify errors after completing the test.")
    require(any(a["question_id"] == question_id for a in run["attempts"]), "No submitted attempt exists for this question.")
    require(category in ERRORS and basis in ("reported", "inferred", "confirmed"), "Use a supported error category and evidence basis.")
    require(bool(note.strip()), "Explain the observed cause rather than assigning an unexplained label.")
    emit(ws, "practice_error_classified", {"run_id": run_id, "question_id": question_id, "error": category, "note": note, "basis": basis}, session=run_id)
    return {"classified": question_id, "category": category, "basis": basis}


def repair_action(category, name):
    return {
        "conceptual": f"Check prerequisites for {name}, then explain and apply unaided.",
        "recall": f"Retrieve {name} closed-book, then repeat after a delay.",
        "procedure": f"Rebuild the method for {name}, then solve a variant unaided.",
        "calculation": f"Solve a short calculation drill for {name} and check each step.",
        "reading": f"Restate the givens and requested quantity before retrying {name}.",
        "strategy": f"Practise selecting or skipping questions like {name} under a time budget.",
        "time": f"Compare methods for {name}, then attempt a timed variant.",
        "unknown": f"Diagnose why marks were lost on {name} before choosing a repair."
    }[category]


def summary(run):
    final = {a["question_id"]: a for a in run["attempts"]}
    maximum = sum(q["marks"] for q in run["questions"])
    score_value = sum(a["awarded"] for a in final.values())
    rows = []
    for q in run["questions"]:
        a = final.get(q["question_id"])
        lost = q["marks"] - (a["awarded"] if a else 0)
        rows.append({"question_id": q["question_id"], "prompt": q["prompt"], "concept_ids": q["concept_ids"],
                     "awarded": a["awarded"] if a else 0, "maximum": q["marks"], "seconds": a["seconds"] if a else None,
                     "response": a["response"] if a else None, "answer": q["answer"], "solution": q["solution"],
                     "source_id": q["source_id"], "locator": q["locator"], "error": a["error"] if a else "unknown",
                     "error_basis": a.get("error_basis", "reported") if a else "unknown",
                     "error_note": a.get("error_note", "") if a else "",
                     "lost": lost, "previous_exposures": q["previous_exposures"],
                     "next_action": repair_action(a["error"] if a else "unknown", q["question_id"]) if lost > 0 else "Check delayed transfer on an unseen variant."})
    seconds = sum(a["seconds"] or 0 for a in run["attempts"])
    known = sum(a["seconds"] is not None for a in run["attempts"])
    end = run.get("ended_at")
    elapsed = None
    if end:
        dt = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
        elapsed = (dt(end) - dt(run["started_at"])).total_seconds()
    confidence_rows = [(a["confidence"], a["awarded"] == q["marks"]) for q in run["questions"] for a in [final.get(q["question_id"])] if a and a.get("confidence") is not None and not a["skipped"]]
    return {"run_id": run["run_id"], "exam_id": run["exam_id"], "mode": run["mode"], "ended_at": end,
            "score": score_value, "maximum": maximum, "submitted": len(final), "questions": len(rows),
            "correct": sum(r["awarded"] == r["maximum"] for r in rows), "seconds": seconds,
            "timed_attempts": known, "attempt_count": len(run["attempts"]), "elapsed_seconds": elapsed,
            "score_per_recorded_minute": round(score_value / (seconds / 60), 3) if seconds > 0 and known == len(run["attempts"]) else None,
            "confidence_sample_count": len(confidence_rows),
            "confidence_calibration_error": round(sum(abs(confidence - int(correct)) for confidence, correct in confidence_rows) / len(confidence_rows), 3) if confidence_rows else None,
            "paused": bool(run["pauses"]), "over_time": elapsed is not None and elapsed > run["duration_minutes"] * 60,
            "repeated_questions": sum(q["previous_exposures"] > 0 for q in run["questions"]),
            "negative_marks_lost": sum(-a["awarded"] for a in final.values() if a["awarded"] < 0), "rows": rows}


def review(ws, run_id):
    run = get_run(ws, run_id)
    require(run["status"] == "completed", "Finish the test before revealing scores, keys and solutions.")
    return summary(run)


def plan(ws, *, minutes=45, exam_id=None, save=False, now=None):
    require(1 <= minutes <= 1440, "Study budget must be 1–1440 minutes.")
    state = load(ws)
    if exam_id:
        require(exam_id in state["exams"], "Unknown exam; set it up first.")
        subjects = state["exams"][exam_id]["subject_ids"]
    else:
        subjects = [s["subject_id"] for s in (store.load_workspace_state(ws).get("subjects") or [])]
    moment = now or utc_now_iso()
    tasks = []
    for sid in subjects:
        data = store.load_concept_mastery(ws, sid)
        records = data.get("concepts", {})
        if not records:
            tasks.append(dict(subject_id=sid, concept_id=None, action="Map a few starting concepts and run a short baseline assessment.", reason="No concepts have been assessed or mapped yet.", priority=80))
        for cid, c in records.items():
            due = c.get("review_due_at") and c["review_due_at"] <= moment
            repeated = sum(m.get("count", 0) for m in c.get("recurring_mistakes", []))
            leverage = sum(cid in r.get("prerequisite_ids", []) for r in records.values())
            priority = (100 if due else 60 if c["status"] == "needs_review" else 35 if c["status"] == "unassessed" else 15) + min(repeated, 5) * 3 + min(leverage, 10)
            if c["status"] == "provisionally_mastered" and not due:
                continue
            tasks.append(dict(subject_id=sid, concept_id=cid, action=c.get("next_action") or "Retrieve and apply " + c["name"] + " without notes.",
                              reason=("Retrieval is due. " if due else "Current evidence: " + c["status"].replace("_", " ") + ". ") + f"{leverage} dependent concepts; {repeated} recorded recurring-error occurrences.", priority=priority))
    completed = [r for r in state["runs"].values() if r["status"] == "completed" and (not exam_id or r["exam_id"] == exam_id)]
    if completed:
        latest = max(completed, key=lambda r: r["ended_at"])
        for row in summary(latest)["rows"]:
            if row["lost"] > 0:
                tasks.append(dict(subject_id=next(q["subject_id"] for q in latest["questions"] if q["question_id"] == row["question_id"]),
                                  concept_id=row["concept_ids"][0] if row["concept_ids"] else None, action=row["next_action"], reason=f"Lost {row['lost']:g} marks in the latest completed test; category {row['error']} ({row['error_basis']}).", priority=75))
    tasks.sort(key=lambda t: (-t["priority"], t["subject_id"], t["concept_id"] or ""))
    chosen = tasks[:min(3, max(1, minutes // 10))]
    reserve = min(5, minutes // 5)
    available = minutes - reserve
    for i, t in enumerate(chosen):
        t["minutes"] = available // len(chosen) + (1 if i < available % len(chosen) else 0)
    p = dict(exam_id=exam_id, budget_minutes=minutes, reflection_minutes=reserve if chosen else 0,
             tasks=chosen, unscheduled_tasks=max(0, len(tasks) - len(chosen)), policy="transparent-v1")
    if save:
        emit(ws, "study_plan_created", p)
    return p


def add_arguments(parser):
    sub = parser.add_subparsers(dest="exam_action", required=True)
    p = sub.add_parser("setup", help="Set up an exam profile")
    p.add_argument("--id", required=True); p.add_argument("--name", required=True)
    p.add_argument("--subjects", nargs="+", required=True); p.add_argument("--date")
    p.add_argument("--daily-minutes", type=int, default=60); p.add_argument("--duration", type=int, default=180)
    p.add_argument("--target")
    p = sub.add_parser("import", help="Import checked question JSON")
    p.add_argument("file"); p.add_argument("--replace", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("start"); p.add_argument("--exam", required=True)
    p.add_argument("--questions", nargs="+", required=True); p.add_argument("--mode", choices=("practice", "sectional", "mock"), default="practice")
    p.add_argument("--duration", type=int)
    p = sub.add_parser("submit")
    p.add_argument("run"); p.add_argument("question"); p.add_argument("response", nargs="?", default="")
    p.add_argument("--seconds", type=float); p.add_argument("--skip", action="store_true")
    p.add_argument("--hints", type=int, default=0); p.add_argument("--error", choices=ERRORS, default="unknown")
    p.add_argument("--confidence", type=float); p.add_argument("--awarded", type=float); p.add_argument("--replace", action="store_true")
    for action in ("pause", "resume", "end", "review", "show"):
        p = sub.add_parser(action); p.add_argument("run")
    p = sub.add_parser("diagnose", help="Record or correct an error diagnosis after a test")
    p.add_argument("run"); p.add_argument("question"); p.add_argument("--category", choices=ERRORS, required=True)
    p.add_argument("--note", required=True); p.add_argument("--basis", choices=("reported", "inferred", "confirmed"), default="reported")
    p = sub.add_parser("retest", help="Start unseen variants of the questions that lost marks")
    p.add_argument("run"); p.add_argument("--duration", type=int)


def handler(args, ws):
    a = args.exam_action
    if a == "setup":
        result = configure(ws, exam_id=args.id, name=args.name, subjects=args.subjects, exam_date=args.date, daily_minutes=args.daily_minutes, duration_minutes=args.duration, target=args.target)
    elif a == "import":
        result = register_questions(ws, read_json(args.file), replace=args.replace)
    elif a == "list":
        state = load(ws)
        result = {"exams": list(state["exams"].values()), "questions": len(state["questions"]), "tests": [{"run_id": r["run_id"], "status": r["status"], "mode": r["mode"]} for r in state["runs"].values()]}
    elif a == "start":
        result = start(ws, args.exam, args.questions, mode=args.mode, duration_minutes=args.duration)
    elif a == "submit":
        result = submit(ws, args.run, args.question, args.response, seconds=args.seconds, skipped=args.skip, hints=args.hints, error=args.error, confidence=args.confidence, replace=args.replace, awarded=args.awarded)
    elif a == "review":
        result = review(ws, args.run)
    elif a == "diagnose":
        result = classify(ws, args.run, args.question, args.category, note=args.note, basis=args.basis)
    elif a == "retest":
        state = load(ws); r = get_run(ws, args.run); report = review(ws, args.run)
        families = {q["family"] for q in r["questions"] if any(row["question_id"] == q["question_id"] and row["lost"] > 0 for row in report["rows"])}
        seen = {qid for test in state["runs"].values() for qid in test["question_ids"]}
        variants = [q["question_id"] for q in state["questions"].values() if q["exam_id"] == r["exam_id"] and q["verification"] == "verified" and q["family"] in families and q["question_id"] not in seen]
        require(bool(variants), "No unseen verified variants are available. Ask the tutor to prepare and check variants for the failed question families.")
        result = start(ws, r["exam_id"], variants, duration_minutes=args.duration)
    elif a == "show":
        r = get_run(ws, args.run)
        result = {"run_id": r["run_id"], "status": r["status"], "questions": [public_question(q) for q in r["questions"]], "submitted": [a["question_id"] for a in r["attempts"]]}
    else:
        result = transition(ws, args.run, a)
    if a not in ("list", "show", "review"):
        from atlas_sync import refresh
        result.update(memory_saved=True, **refresh(ws))
    print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
    return 0
