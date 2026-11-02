"""A small public workflow for any learning goal; the tutor handles the machinery."""
import json
import re

from common import LearningOSUsageError
from memory import store
import learning_method as method
import study_workflow as study


def setup(ws, subject, *, purpose=None, mode=None, minutes=45, topic=None):
    import learningos
    from atlas_sync import refresh
    method.require(bool(subject.strip()), "Name a subject, for example Biology, Mathematics or System Design.")
    registered = store.load_workspace_state(ws).get("subjects", [])
    match = next((s for s in registered if subject.casefold() in (s["subject_id"].casefold(), s["name"].casefold())), None)
    if match:
        sid, name = match["subject_id"], match["name"]
    else:
        sid = re.sub(r"[^a-z0-9]+", "-", subject.casefold()).strip("-")
        method.require(bool(sid), "Use an English short name to create a portable subject id.")
        code = learningos.main(["init-subject", "--id", sid, "--name", subject, "--quiet", "--workspace", str(ws.root)])
        method.require(code == 0, "Subject initialization did not finish. Inspect the reported issue; no existing files were replaced.")
        name = subject
    prior_goal = method.goals(ws).get(sid)
    if purpose or mode is not None or prior_goal is None:
        method.goal(ws, sid, purpose=purpose or (prior_goal or {}).get("purpose") or "Learn and independently apply " + name,
                    mode=mode or (prior_goal or {}).get("mode") or "learn", minutes=minutes)
    if topic:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-")
        method.require(bool(slug), "Use an English short topic name for its portable id.")
        cid = sid + ":" + slug
        catalog = store.load_concept_catalog(ws, sid)
        if cid not in store.load_concept_mastery(ws, sid)["concepts"] and not any(c["concept_id"] == cid for c in catalog.get("concepts", [])):
            study.import_concepts(ws, sid, [{"concept_id": cid, "name": topic, "unit": "Starting topics", "description": "Learner-selected topic. Assess prerequisites before teaching; scope is not yet verified against sources.",
                "prerequisite_ids": [], "source_refs": [], "exam_weight": None, "tags": ["learner-selected"]}], general=True)
    return {"subject_id": sid, "subject_name": name, "goal": method.goals(ws).get(sid), "materials_required": False, "memory_saved": True,
            "next_action": "Continue in your tutor chat: /learn " + (topic or name), **refresh(ws)}


def add_arguments(parser):
    parser.add_argument("--subject", help="Subject display name or existing id")
    parser.add_argument("--goal"); parser.add_argument("--mode", choices=("learn", "exam", "interview", "professional"))
    parser.add_argument("--minutes", type=int, default=45); parser.add_argument("--topic")


def handler(args, ws):
    subject = args.subject or store.load_workspace_state(ws).get("current_subject_id")
    method.require(subject is not None, "New here? Open LearningOS Study Menu and choose Get started, or use start --subject Biology.")
    result = setup(ws, subject, purpose=args.goal, mode=args.mode, minutes=args.minutes, topic=args.topic)
    # Setup itself does not pretend a lesson started or an assessment happened.
    print(json.dumps(result, indent=2))
    return 0


def continue_args(parser):
    parser.add_argument("--subject"); parser.add_argument("--minutes", type=int)


def continue_handler(args, ws):
    sid = args.subject or store.load_workspace_state(ws).get("current_subject_id")
    method.require(sid is not None, "Use /start and name a subject first.")
    registered = store.load_workspace_state(ws).get("subjects", [])
    sid = next((s["subject_id"] for s in registered if sid.casefold() in (s["subject_id"].casefold(), s["name"].casefold())), sid)
    recovery = method.recover(ws, sid)
    summary = method.summary(ws)
    if any(s["subject_id"] == sid for s in summary["closing_sessions"]):
        request = next(e for e in reversed(method.rows(ws)) if e["type"] == "session_close_requested" and e["subject_id"] == sid)
        study.finish(ws, sid, **{k: request["payload"][k] for k in ("outcome", "next_action", "duration_minutes")})
    result = study.begin(ws, sid, minutes=args.minutes or method.goals(ws).get(sid, {}).get("minutes", 45))
    path = ws.memory_subject(sid) / "next_session.md"
    result.update(subject_id=sid, recovered_cycles=recovery["recovered_cycles"], handover=path.read_text(encoding="utf-8") if path.exists() else "Begin with a short diagnostic.",
                  open_uncertainties=[n for n in method.summary(ws)["notes"] if n["subject_id"] == sid and n["kind"] == "uncertainty" and not n["resolved"]])
    from atlas_sync import refresh
    result.update(refresh(ws))
    print(json.dumps(result, indent=2))
    return 0


def guide_handler(args, ws):
    print("""LEARNING OS — START HERE

1. Open this LearningOS folder in your tutor chat.
2. Say /start Biology (or any subject). Tell the tutor what you want to be able to do.
3. Study normally. Ask questions, solve problems, explain your thinking.
4. Say /save when stopping. Look for a memory-saved receipt and an Atlas-refresh result.
5. Next time, say /continue. Your saved evidence and exact next activity are loaded.

Optional: /today 30, /review, /test, /atlas, /help.
An exam, interview date or uploaded document is optional.

LearningOS saves committed questions, responses, explanations, mistakes, rubric judgements and
handover notes. It does not secretly record every chat message, observe other chats or save a
response the tutor has not recorded. The tutor should save each meaningful learning cycle.
Closing the chat is not /save. Already committed evidence survives; unrecorded conversation may not.

The desktop Atlas is a read-only offline snapshot. Saved operations rebuild it; reload an old tab.
Use LearningOS Study Menu > Get started if you prefer numbered setup choices.
""")
    return 0
