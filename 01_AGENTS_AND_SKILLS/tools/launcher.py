"""Beginner entry point. Opens only the locally generated Atlas, never arbitrary URLs."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from common import LearningOSError, LearningOSUsageError, Workspace
import atlas
import exam_engine as exams
from memory import store


def open_atlas(ws):
    path = atlas.build(ws).resolve()
    if ws.graphs.resolve() not in path.parents or path.suffix != ".html":
        raise LearningOSUsageError("The launcher only opens the generated local Atlas.")
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        print("Open this local file in your browser: " + str(path))
    return path


def ask(prompt, default=None):
    value = input(prompt + (f" [{default}]" if default is not None else "") + ": ").strip()
    return value or default or ""


def integer(prompt, default, minimum=1, maximum=1440):
    value = ask(prompt, str(default))
    if not value.isdigit() or not minimum <= int(value) <= maximum:
        raise LearningOSUsageError(f"Enter a whole number between {minimum} and {maximum}.")
    return int(value)


def choose(items, title, *, name=lambda x: str(x)):
    if not items:
        raise LearningOSUsageError("Nothing to choose yet. Add a subject or set up an exam first.")
    print("\n" + title)
    for i, item in enumerate(items, 1):
        print(f"  {i}. {name(item)}")
    return items[integer("Choose a number", 1, maximum=len(items)) - 1]


def subjects(ws):
    return store.load_workspace_state(ws).get("subjects", [])


def file_picker(title, patterns):
    """Tk is part of the Windows Python distribution; fall back to a pasted local path."""
    try:
        from tkinter import Tk, filedialog
        root = Tk(); root.withdraw(); root.attributes("-topmost", True)
        try:
            path = filedialog.askopenfilename(title=title, filetypes=patterns)
        finally:
            root.destroy()
        return Path(path) if path else None
    except (ImportError, RuntimeError):
        value = ask("Paste the local file path (blank cancels)").strip('"')
        return Path(value) if value else None


def setup_exam(ws):
    name = ask("Which exam are you preparing for?")
    eid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not eid:
        eid = ask("Choose a short exam id using English letters")
    available = subjects(ws)
    if not available:
        raise LearningOSUsageError("Add a subject first using option 4.")
    print("\nAvailable subjects:")
    for i, s in enumerate(available, 1):
        print(f"  {i}. {s['name']}")
    answer = ask("Subject numbers separated by commas, or 'all'", "all")
    if answer == "all":
        selected = available
    else:
        try:
            indices = [int(x.strip()) - 1 for x in answer.split(",")]
            if any(i < 0 or i >= len(available) for i in indices):
                raise ValueError
            selected = [available[i] for i in indices]
        except (ValueError, IndexError):
            raise LearningOSUsageError("Use the displayed subject numbers, separated by commas.")
    date = ask("Exam date as YYYY-MM-DD (blank if not known)") or None
    daily = integer("Study minutes available per day", 60)
    duration = integer("Duration of an exam paper in minutes", 180)
    target = ask("Target score or rank (optional)") or None
    exams.configure(ws, exam_id=eid, name=name, subjects=[s["subject_id"] for s in selected], exam_date=date, daily_minutes=daily, duration_minutes=duration, target=target)
    print("\nExam saved. Ask the tutor to map the official syllabus and prepare verified questions.")


def practice(ws):
    state = exams.load(ws)
    open_runs = [r for r in state["runs"].values() if r["status"] != "completed"]
    if open_runs:
        run = open_runs[0]
        if run["status"] == "paused":
            exams.transition(ws, run["run_id"], "resume")
        print("\nContinuing your saved test: " + run["run_id"])
    else:
        exam = choose(list(state["exams"].values()), "Choose an exam", name=lambda e: e["name"])
        mode = choose(["practice", "sectional", "mock"], "Choose a test type")
        questions = [q for q in state["questions"].values() if q["exam_id"] == exam["exam_id"] and q["verification"] == "verified" and (mode != "practice" or q.get("usage", "practice") != "holdout")]
        if not questions:
            raise LearningOSUsageError("No verified questions yet. Ask your tutor to prepare a source-backed question bank, then import it with option 7.")
        count = integer("How many questions?", min(5, len(questions)), maximum=len(questions))
        prior = {qid for r in state["runs"].values() for qid in r["question_ids"]}
        questions.sort(key=lambda q: (q["question_id"] in prior, q["question_id"]))
        started = exams.start(ws, exam["exam_id"], [q["question_id"] for q in questions[:count]], mode=mode,
                              duration_minutes=integer("Time limit in minutes", exam["duration_minutes"]))
        run = exams.get_run(ws, started["run_id"])
    rid = run["run_id"]
    print("\nAnswers are saved as you go. Type /pause to stop safely, /skip to skip, /end to finish.")
    for q in run["questions"]:
        if any(a["question_id"] == q["question_id"] for a in run["attempts"]):
            continue
        print(f"\n{q['question_id']}  (+{q['marks']:g} / -{q['negative_marks']:g})\n{q['prompt']}")
        for choice in q["choices"]:
            print(f"  {choice['id']}. {choice['text']}")
        if q["format"] == "manual":
            print("This written question needs tutor marking. The test is paused before collecting an answer; continue it with your tutor using the saved test id.")
            exams.transition(ws, rid, "pause")
            return
        begin = time.perf_counter()
        response = ask("Your answer")
        seconds = round(time.perf_counter() - begin, 2)
        if response == "/pause":
            exams.transition(ws, rid, "pause"); print("Paused. Choose practice next time to resume."); return
        if response == "/end":
            break
        awarded = None
        if q["format"] == "manual" and response != "/skip":
            print("This response requires human marking. Finish and review with your tutor instead of guessing a score.")
            exams.transition(ws, rid, "pause"); return
        exams.submit(ws, rid, q["question_id"], response if response != "/skip" else "", seconds=seconds, timing="timer", skipped=response == "/skip", awarded=awarded)
    result = exams.transition(ws, rid, "end")
    print(f"\nTest finished: {result['score']:g} / {result['maximum']:g} marks.\nOpen Atlas > Exam performance to review answers and repair actions.")
    atlas.build(ws)


def menu(args, ws):
    import learningos
    while True:
        print("\n  LEARNING OS\n  A little structure. A stronger next session.\n")
        print("  1  Open my Learning Atlas\n  2  What should I study today?\n  3  Set up my exam (optional)\n  4  Add a subject\n  5  Add a source document (optional)\n  6  Start or resume timed practice\n  7  Import tutor-prepared questions\n  8  Review test results\n  9  Help: start, save and continue\n 10  Get started with any learning goal\n  0  Exit\n")
        try:
            choice = ask("Choose a number", "1")
            if choice == "0":
                return 0
            if choice == "1":
                open_atlas(ws)
            elif choice == "2":
                minutes = integer("How many minutes do you have?", 45)
                data = exams.plan(ws, minutes=minutes, save=True)
                for i, t in enumerate(data["tasks"], 1):
                    print(f"\n{i}. {t['minutes']} min — {t['action']}\n   Why: {t['reason']}")
                if not data["tasks"]:
                    print("Add a subject first, then ask your tutor to map your syllabus.")
                print(f"\nKeep {data['reflection_minutes']} minutes to retrieve and save. Paste /today into your tutor chat to begin.")
            elif choice == "3":
                setup_exam(ws)
            elif choice == "4":
                name = ask("Subject name")
                sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                if not sid:
                    sid = ask("Choose a short subject id using English letters")
                learningos.main(["init-subject", "--id", sid, "--name", name, "--workspace", str(ws.root)])
            elif choice == "5":
                s = choose(subjects(ws), "Choose the subject", name=lambda s: s["name"])
                path = file_picker("Choose a syllabus, notes or past paper", [("Study documents", "*.pdf *.txt *.md"), ("All files", "*.*")])
                if path:
                    import shutil
                    from common import sha256_file
                    dest = ws.knowledge_subject(s["subject_id"]) / "source_materials" / path.name
                    if dest.exists():
                        if sha256_file(dest) != sha256_file(path):
                            raise LearningOSUsageError("A different file with that name already exists. Rename your new copy before adding it.")
                    else:
                        shutil.copy2(path, dest)
                    learningos.main(["index", "--subject", s["subject_id"], "--workspace", str(ws.root)])
            elif choice == "6":
                practice(ws)
            elif choice == "7":
                path = file_picker("Choose the question file prepared by your tutor", [("Question JSON", "*.json")])
                if path:
                    from common import read_json
                    result = exams.register_questions(ws, read_json(path))
                    print(f"Imported {result['imported']} questions.")
            elif choice == "8":
                runs = [r for r in exams.load(ws)["runs"].values() if r["status"] == "completed"]
                run = choose(runs, "Choose a completed test", name=lambda r: r["run_id"] + " · " + r["mode"])
                result = exams.review(ws, run["run_id"])
                print(f"\n{result['score']:g} / {result['maximum']:g} marks")
                for row in result["rows"]:
                    print(f"\n{row['question_id']}: {row['awarded']:g}/{row['maximum']:g}\nAnswer: {row['answer']}\n{row['solution']}\nNext: {row['next_action']}")
            elif choice == "9":
                import onboarding
                onboarding.guide_handler(args, ws)
            elif choice == "10":
                import onboarding
                name = ask("What subject or skill do you want to learn?")
                topic = ask("First topic (optional)") or None
                purpose = ask("What would you like to be able to do?") or None
                mode = choose(["learn", "exam", "interview", "professional"], "Choose your purpose (no exam required)")
                result = onboarding.setup(ws, name, purpose=purpose, mode=mode, minutes=integer("Minutes in a usual session", 45), topic=topic)
                print("\nReady. Open this LearningOS folder in your tutor chat and say /learn " + (topic or name) + ".\nEnd with /save; next time use /continue.\nYour existing records and newly mapped topics are in Atlas.")
            else:
                print("Choose one of the displayed numbers.")
            from atlas_sync import refresh
            sync = refresh(ws)
            if not sync["atlas_refreshed"]:
                print(sync["message"])
        except (LearningOSError, ValueError) as exc:
            print("\n" + str(exc) + "\nYour existing learning records were preserved.")
        except (EOFError, KeyboardInterrupt):
            print("\nMenu closed. Submitted answers remain saved; unfinished tests can be resumed.")
            return 0


def main():
    import argparse
    args = argparse.ArgumentParser()
    args.add_argument("--menu", action="store_true")
    options = args.parse_args()
    ws = Workspace.at(Path(__file__).resolve().parents[2])
    try:
        if options.menu:
            return menu(options, ws)
        open_atlas(ws)
        return 0
    except (LearningOSError, OSError) as exc:
        print("Could not refresh Learning Atlas: " + str(exc))
        print("The previous snapshot and your learning evidence were preserved.")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
