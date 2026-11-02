"""Generate a self-contained, read-only offline learning report from canonical evidence."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

from common import LearningOSDataError, atomic_write_text, sha256_file, utc_now_iso
from memory import events, store
import exam_engine
from validation import validate_workspace

ASSETS = Path(__file__).resolve().parent.parent / "templates" / "atlas"


def redact(value):
    if isinstance(value, dict):
        return {k: ("Hidden while a test is open" if k in ("answer", "solution", "awarded") else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def collect(ws, *, now=None):
    now = now or utc_now_iso()
    findings = validate_workspace(ws)
    data = dict(built_at=now, subjects=[], concepts=[], events=[], documents=[], sources=[], checkpoints=[],
                exams=[], tests=[], runs=[], plans=[], tasks=None, problems=[f.to_dict() for f in findings],
                manifest={"schema_version": 1, "inputs": {}, "included": [], "excluded": ["Backup contents", "Duplicate generated graphs", "Original document contents", "Extracted textbook contents", "Search caches"], "unavailable": []})
    manifest = data["manifest"]
    data["learning"] = {"goals": {}, "cycles": [], "notes": [], "open_sessions": [], "closing_sessions": [], "pending_cycles": 0}

    def track(path):
        if path.is_file():
            manifest["inputs"][ws.rel(path)] = sha256_file(path)

    def error(exc, path):
        manifest["unavailable"].append(ws.rel(path))
        data["problems"].append(dict(severity="error", path=ws.rel(path), code="atlas_input", message=str(exc), remedy="Inspect this file; preserve the original and use validate before repair."))

    rows, read_findings = events.read_events(ws)
    try:
        import learning_method
        data["learning"] = learning_method.summary(ws)
    except (LearningOSDataError, ValueError) as exc:
        error(exc, ws.event_log)
    # Quarantine schema-invalid rows in the display, without touching the log.
    for row in rows:
        issues = events.validate_event(ws, row)
        if not issues:
            data["events"].append(row)
    track(ws.event_log)
    track(ws.marker)
    data["last_evidence_at"] = max((e["at"] for e in data["events"]), default=None)
    active_ids = set()
    try:
        state = exam_engine.load(ws)
        data["exams"] = list(state["exams"].values())
        data["plans"] = state["plans"]
        data["question_count"] = len(state["questions"])
        for run in state["runs"].values():
            data["runs"].append({k: run[k] for k in ("run_id", "exam_id", "status", "mode", "started_at", "question_ids")})
            if run["status"] == "completed":
                data["tests"].append(exam_engine.summary(run))
            else:
                active_ids.update(run["question_ids"])
        try:
            current_sid = store.load_workspace_state(ws).get("current_subject_id")
            budget = data["learning"]["goals"].get(current_sid, {}).get("minutes", 45)
            if data["plans"]:
                budget = data["plans"][-1]["budget_minutes"]
            data["tasks"] = exam_engine.plan(ws, minutes=budget, now=now)
        except (LearningOSDataError, ValueError) as exc:
            error(exc, ws.memory_global / "workspace_state.json")
    except (LearningOSDataError, ValueError, KeyError) as exc:
        data["question_count"] = None
        error(exc, ws.event_log)
    registry = store.load_workspace_state(ws)
    for subject in registry.get("subjects", []):
        sid = subject["subject_id"]
        try:
            state = store.load_subject_state(ws, sid)
            mastery = store.load_concept_mastery(ws, sid)
            catalog = store.load_concept_catalog(ws, sid) or {}
            queue = store.load_revision_queue(ws, sid) or {}
            misconceptions = store.load_misconceptions(ws, sid) or {}
            source_index = store.load_source_index(ws, sid) or {}
            mapped = {c["concept_id"]: c for c in catalog.get("concepts", [])}
            records = mastery.get("concepts", {})
            for cid in sorted(set(mapped) | set(records)):
                c = {**mapped.get(cid, {}), **records.get(cid, {}), "subject_id": sid}
                c.setdefault("status", "unassessed")
                data["concepts"].append(c)
            data["subjects"].append({**subject, "state": state, "queue": queue, "misconceptions": misconceptions})
            for source in source_index.get("sources", []):
                data["sources"].append({**source, "subject_id": sid})
        except (LearningOSDataError, ValueError) as exc:
            error(exc, ws.memory_subject(sid))
    # Human notes, preferences, handovers and checkpoints remain browsable, not just chart totals.
    for path in sorted(ws.memory.rglob("*")):
        if not path.is_file():
            continue
        rel = ws.rel(path)
        parts = path.relative_to(ws.memory).parts
        if any(part in ("backups", "graphs", "_template") for part in parts) or path.name in (".gitkeep", "search_index.sqlite3"):
            continue
        if path == ws.event_log or path.name == ".learning-writer.lock":
            continue
        if path.suffix not in (".json", ".md", ".txt"):
            manifest["unavailable"].append(rel)
            continue
        track(path)
        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                value = json.loads(content)
                if "checkpoints" in parts:
                    data["checkpoints"].append({"path": rel, "data": value})
            data["documents"].append({"path": rel, "title": path.stem.replace("_", " "), "content": content})
        except (UnicodeError, ValueError) as exc:
            error(exc, path)
    for sid in [s["subject_id"] for s in registry.get("subjects", [])]:
        for filename in ("source_index.json", "concept_catalog.json", "syllabus_map.md", "subject_config.json"):
            track(ws.knowledge_subject(sid) / filename)
    manifest["included"] = ["Schema-valid events", "Registered subjects and concept states", "Revision queues", "Misconceptions", "Checkpoints", "Global preferences and profile", "Session notes and handovers", "Indexed source metadata", "Exam profiles, scored tests and study plans", "Learning goals, dimension rubrics, actual responses and notes", "Pending evidence saves and unfinished session closures"]
    # Atlas offers no answer-key assistance during an open test. Canonical files remain local.
    data["test_open"] = bool(active_ids)
    if active_ids:
        data["events"] = redact(data["events"])
        data["tests"] = redact(data["tests"])
    # Reserved assessment material must not be exposed in the general memory explorer.
    for e in data["events"]:
        if e["type"] == "question_registered" and e["payload"].get("usage") == "holdout":
            e["payload"] = redact(e["payload"])
            e["payload"]["prompt"] = "Reserved for unseen assessment"
            e["payload"]["choices"] = []
        if e["type"] == "practice_started":
            e["payload"]["questions"] = [redact(q) if q.get("usage") == "holdout" else q for q in e["payload"].get("questions", [])]
    return data


def build(ws, *, now=None):
    data = collect(ws, now=now)
    template = (ASSETS / "index.html").read_text(encoding="utf-8")
    css = (ASSETS / "atlas.css").read_text(encoding="utf-8")
    js = (ASSETS / "atlas.js").read_text(encoding="utf-8")
    fonts = ""
    for family, filename in (("Newsreader", "Newsreader.ttf"), ("Instrument Sans", "InstrumentSans.ttf")):
        payload = base64.b64encode((ASSETS / "fonts" / filename).read_bytes()).decode("ascii")
        fonts += "@font-face{font-family:'" + family + "';src:url(data:font/ttf;base64," + payload + ") format('truetype');font-weight:100 900;font-style:normal;font-display:swap;}"
    # JSON in script tags must escape HTML delimiters, including malicious </script> in notes.
    payload = json.dumps(data, ensure_ascii=True, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    licenses = json.dumps({p.name: p.read_text(encoding="utf-8") for p in (ASSETS / "fonts").glob("*-OFL.txt")}, ensure_ascii=True).replace("<", "\\u003c")
    rendered = template.replace("/*FONTS*/", fonts).replace("/*CSS*/", css).replace("/*SCRIPT*/", js).replace("__FONT_LICENSES__", licenses).replace("__ATLAS_DATA__", payload)
    target = ws.graphs / "atlas" / "index.html"
    atomic_write_text(target, rendered)
    return target


def add_arguments(parser):
    parser.add_argument("--json", action="store_true", default=False, help="Print the generated path as JSON")


def handler(args, ws):
    target = build(ws)
    print(json.dumps({"path": ws.rel(target), "message": "Offline snapshot built. Open this file or use the LearningOS launcher."}) if args.json else "Atlas ready: " + str(target))
    return 0
