#!/usr/bin/env python3
"""LearningOS command line interface.

Run from ``01_AGENTS_AND_SKILLS/tools``::

    python learningos.py --help
    python learningos.py validate
    python learningos.py init-subject --id algo --name "Algorithms"

The CLI exists to do the things an agent must never improvise: creating the scaffold, validating
the install, and writing state through schema-checked atomic writes. Teaching, diagnosing and
explaining stay in the conversation, guided by `01_AGENTS_AND_SKILLS/core/` and the skills.

Exit codes (``common.EXIT_*``):

===  ==========================================================================
0    success
1    unexpected error (a bug — the message includes what to do next)
2    usage error: bad or missing arguments
3    validation or data failure; nothing was written
4    the subject, session or artefact asked for does not exist
5    refused a destructive operation (re-run with ``--force``)
6    a required optional dependency is missing (only with ``--require-*`` flags)
===  ==========================================================================

Nothing here makes a network call, and nothing uploads anything anywhere.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import atlas as atlas_mod
import exam_engine as exam_mod
import study_workflow as study_mod
import launcher as launcher_mod
import onboarding as onboarding_mod

if __name__ == "__main__" and str(Path(__file__).resolve().parent) not in sys.path:
    # Allow `python path/to/learningos.py` from anywhere, not just from the tools directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402 - sys.path is prepared above
    DEFAULT_PATHS,
    EXIT_OK,
    EXIT_ERROR,
    EXIT_USAGE,
    EXIT_VALIDATION,
    MARKER_FILE,
    PRINCIPAL_DIRS,
    SCHEMA_VERSION,
    SUBJECT_ID_RE,
    TOOL_ID,
    TOOL_NAME,
    TOOL_VERSION,
    LearningOSDataError,
    LearningOSError,
    LearningOSMissingDependencyError,
    LearningOSNotFoundError,
    LearningOSUsageError,
    Workspace,
    atomic_write_json,
    atomic_write_text,
    count_findings,
    dumps_json,
    ensure_dir,
    optional_dependency,
    parse_date,
    print_findings,
    read_json,
    read_text,
    resolve_workspace,
    utc_now_iso,
    which,
)
from memory import backup as backup_mod  # noqa: E402
from memory import events as events_mod  # noqa: E402
from memory import mastery as mastery_mod  # noqa: E402
from memory import reconcile as reconcile_mod  # noqa: E402
from memory import report as report_mod  # noqa: E402
from memory import search as search_mod  # noqa: E402
from memory import store  # noqa: E402
from graphs import build as graph_build  # noqa: E402
from graphs import render as graph_render  # noqa: E402
from ingestion import indexer as indexer_mod  # noqa: E402
from validation import has_errors, validate_workspace  # noqa: E402

#: The install this script belongs to. Used by `init-workspace` to scaffold another folder from
#: the files that shipped beside it.
SOURCE_ROOT = Path(__file__).resolve().parents[2]

#: Subject ids that are reserved for scaffolding and may never be created.
RESERVED_SUBJECT_IDS = ("_template", "_cross")

#: Copied verbatim into a new workspace (the shipped instruction set, schemas, templates, CLI).
INSTALL_ROOT_FILES = ("AGENTS.md", "README.md", "CHANGELOG.md", "LICENSE", ".gitignore", ".gitattributes")
INSTALL_TREES = ("01_AGENTS_AND_SKILLS",)
INSTALL_DOC_FILES = (
    "02_KNOWLEDGE/README.md",
    "02_KNOWLEDGE/inbox/README.md",
    "02_KNOWLEDGE/shared_reference/README.md",
    "02_KNOWLEDGE/subjects/README.md",
    "03_MEMORY/README.md",
)
COPY_EXCLUDED_PARTS = ("__pycache__", ".pytest_cache")

#: Directories that exist so the layout is self-documenting even before they hold anything.
GITKEEP_DIRS = (
    "session_logs",
    "graphs",
    "cross_subject_graphs",
    "checkpoints",
    "archives",
    "backups",
)

#: Memory templates that belong somewhere other than 03_MEMORY/subjects/<id>/.
MEMORY_TEMPLATE_EXCLUDES = ("global", "checkpoint.json")


# ===================================================================== scaffolding (shared plan)


@dataclass
class ScaffoldOp:
    """One thing `init-workspace` / `init-subject` would create.

    Collected before anything is written so ``--dry-run`` can print the exact plan, and so the
    same code path reports ``created`` and ``skipped`` honestly.
    """

    kind: str  # dir | copy | json | text | touch
    target: Path
    source: Path | None = None
    data: Any = None
    installer: bool = False
    schema: str | None = None


def _excluded(path: Path) -> bool:
    return any(part in COPY_EXCLUDED_PARTS or part.endswith(".pyc") for part in path.parts)


def _copy_tree_ops(source: Path, target: Path, *, installer: bool) -> list[ScaffoldOp]:
    """Copy ops for every file under ``source``, preserving relative structure."""
    ops: list[ScaffoldOp] = []
    if not source.is_dir():
        return ops
    for item in sorted(source.rglob("*")):
        if _excluded(item.relative_to(source)):
            continue
        destination = target / item.relative_to(source)
        if item.is_dir():
            ops.append(ScaffoldOp("dir", destination, installer=installer))
        elif item.is_file():
            ops.append(ScaffoldOp("copy", destination, source=item, installer=installer))
    return ops


def _apply_ops(
    ws: Workspace,
    ops: Sequence[ScaffoldOp],
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Execute (or simulate) scaffold ops.

    Returns ``(created, skipped, overwritten)`` as workspace-relative paths. An existing file is
    never rewritten unless ``force`` is set *and* the op is an installer op — learner material is
    out of reach of ``--force`` by construction.
    """
    created: list[str] = []
    skipped: list[str] = []
    overwritten: list[str] = []
    for op in ops:
        relative = ws.rel(op.target)
        if op.kind == "dir":
            if op.target.is_dir():
                continue
            created.append(relative + "/")
            if not dry_run:
                ensure_dir(op.target)
            continue
        exists = op.target.exists()
        if exists and not (force and op.installer):
            skipped.append(relative)
            continue
        if exists:
            overwritten.append(relative)
        else:
            created.append(relative)
        if dry_run:
            continue
        ensure_dir(op.target.parent)
        if op.kind == "copy":
            assert op.source is not None
            shutil.copyfile(op.source, op.target)
        elif op.kind == "json":
            if op.schema:
                store.registry(ws).validate_or_raise(
                    op.schema, op.data, path=relative
                )
            atomic_write_json(op.target, op.data)
        elif op.kind == "text":
            atomic_write_text(op.target, str(op.data))
        elif op.kind == "touch":
            op.target.touch()
        else:  # pragma: no cover - programming error
            raise LearningOSError(f"unknown scaffold op {op.kind!r}")
    return created, skipped, overwritten


# ============================================================================== init-workspace


def _workspace_scaffold_ops(ws: Workspace) -> list[ScaffoldOp]:
    ops: list[ScaffoldOp] = []
    same_install = SOURCE_ROOT.resolve() == ws.root.resolve()

    for name in PRINCIPAL_DIRS:
        ops.append(ScaffoldOp("dir", ws.root / name))
    for key in DEFAULT_PATHS:
        if key == "event_log":
            continue
        ops.append(ScaffoldOp("dir", ws.path(key)))

    if not same_install:
        for name in INSTALL_ROOT_FILES:
            source = SOURCE_ROOT / name
            if source.is_file():
                ops.append(ScaffoldOp("copy", ws.root / name, source=source, installer=True))
        for tree in INSTALL_TREES:
            ops.extend(_copy_tree_ops(SOURCE_ROOT / tree, ws.root / tree, installer=True))
        for relative in INSTALL_DOC_FILES:
            source = SOURCE_ROOT / relative
            if source.is_file():
                ops.append(
                    ScaffoldOp("copy", ws.root / relative, source=source, installer=True)
                )

    ops.append(
        ScaffoldOp(
            "json",
            ws.marker,
            data={
                "schema_version": SCHEMA_VERSION,
                "version": TOOL_VERSION,
                "created_at": utc_now_iso(),
                "workspace_name": ws.root.name,
                "paths": dict(DEFAULT_PATHS),
            },
            installer=False,
        )
    )

    # Prefer the target workspace's own templates (they are copied in above); fall back to the
    # install this script shipped with, which matters only for --dry-run on an empty folder.
    templates = (
        ws.memory_templates
        if ws.memory_templates.is_dir()
        else SOURCE_ROOT / DEFAULT_PATHS["memory_templates"]
    )
    subject_templates = (
        ws.subject_template
        if ws.subject_template.is_dir()
        else SOURCE_ROOT / DEFAULT_PATHS["subject_template"]
    )

    global_templates = templates / "global"
    if global_templates.is_dir():
        for item in sorted(global_templates.iterdir()):
            if not item.is_file():
                continue
            target = ws.memory_global / item.name
            if item.name == "workspace_state.json":
                ops.append(
                    ScaffoldOp(
                        "json", target, data=store.new_workspace_state(), schema="workspace_state"
                    )
                )
            elif item.suffix == ".json":
                data = read_json(item)
                if isinstance(data, dict) and "updated_at" in data:
                    data["updated_at"] = utc_now_iso()
                ops.append(ScaffoldOp("json", target, data=data))
            else:
                ops.append(ScaffoldOp("copy", target, source=item))

    ops.extend(
        _copy_tree_ops(subject_templates, ws.knowledge_subjects / "_template", installer=False)
    )
    if templates.is_dir():
        for item in sorted(templates.rglob("*")):
            relative = item.relative_to(templates)
            if relative.parts and relative.parts[0] in MEMORY_TEMPLATE_EXCLUDES:
                continue
            target = ws.memory_subjects / "_template" / relative
            if item.is_dir():
                ops.append(ScaffoldOp("dir", target))
            elif item.is_file():
                ops.append(ScaffoldOp("copy", target, source=item))

    ops.append(ScaffoldOp("touch", ws.event_log))
    for key in GITKEEP_DIRS:
        ops.append(ScaffoldOp("text", ws.path(key) / ".gitkeep", data=""))
    return ops


def cmd_init_workspace(args: argparse.Namespace, ws: Workspace) -> int:
    """Scaffold a LearningOS workspace. Idempotent; never rewrites an existing file."""
    ops = _workspace_scaffold_ops(ws)
    created, skipped, overwritten = _apply_ops(
        ws, ops, dry_run=args.dry_run, force=args.force
    )
    if args.json:
        print(
            dumps_json(
                {
                    "workspace": str(ws.root),
                    "dry_run": bool(args.dry_run),
                    "created": created,
                    "skipped": skipped,
                    "overwritten": overwritten,
                }
            ),
            end="",
        )
        return EXIT_OK
    if args.quiet:
        return EXIT_OK
    verb = "would create" if args.dry_run else "created"
    print(f"workspace: {ws.root}")
    print(f"{verb}: {len(created)}   skipped (already present): {len(skipped)}", end="")
    print(f"   overwritten: {len(overwritten)}" if overwritten else "")
    for relative in created[:40]:
        print(f"  + {relative}")
    if len(created) > 40:
        print(f"  … and {len(created) - 40} more")
    for relative in overwritten[:40]:
        print(f"  ~ {relative}")
    if not created and not overwritten:
        print("  nothing to do — this workspace is already complete")
    if args.dry_run:
        print("\n--dry-run: nothing was written.")
    else:
        print(
            "\nNext: `python learningos.py validate`, then "
            '`python learningos.py init-subject --id <id> --name "<name>"`.'
        )
    return EXIT_OK


# ================================================================================ init-subject


def _substitute_markdown(text: str, subject_id: str, name: str) -> str:
    """Point a markdown template at a real subject.

    The templates use the literal ``_template`` everywhere an id appears, so a single replacement
    is enough; the README's H1 additionally gets the human-readable name.
    """
    text = text.replace("# Subject: _template", f"# Subject: {name}")
    return text.replace("_template", subject_id)


def _substitute_json(
    data: dict[str, Any],
    *,
    subject_id: str,
    name: str,
    exam_date: str | None,
    description: str | None,
    now: str,
) -> dict[str, Any]:
    """Point a JSON template at a real subject, touching only identity and timestamp fields."""
    data["subject_id"] = subject_id
    if "name" in data:
        data["name"] = name
    if "exam_date" in data:
        data["exam_date"] = exam_date
    if "description" in data:
        data["description"] = description
    if "created_at" in data:
        data["created_at"] = now
    if "updated_at" in data:
        data["updated_at"] = now
    if "concepts" in data and isinstance(data["concepts"], dict):
        # A new subject never inherits mastery from anywhere, including the template.
        data["concepts"] = {}
    return data


def _subject_scaffold_ops(
    ws: Workspace,
    *,
    subject_id: str,
    name: str,
    exam_date: str | None,
    description: str | None,
) -> list[ScaffoldOp]:
    now = utc_now_iso()
    ops: list[ScaffoldOp] = []
    knowledge_target = ws.knowledge_subject(subject_id)
    memory_target = ws.memory_subject(subject_id)
    ops.append(ScaffoldOp("dir", knowledge_target))
    ops.append(ScaffoldOp("dir", memory_target))

    def template_ops(source_dir: Path, target_dir: Path, excludes: Sequence[str] = ()) -> None:
        if not source_dir.is_dir():
            raise LearningOSError(
                f"template directory is missing: {ws.rel(source_dir)}",
                remedy="Restore 01_AGENTS_AND_SKILLS/templates/ from your copy of LearningOS, "
                "then re-run `init-subject`.",
            )
        for item in sorted(source_dir.rglob("*")):
            relative = item.relative_to(source_dir)
            if relative.parts and relative.parts[0] in excludes:
                continue
            target = target_dir / relative
            if item.is_dir():
                ops.append(ScaffoldOp("dir", target))
            elif item.suffix == ".json":
                data = read_json(item)
                if not isinstance(data, dict):  # pragma: no cover - shipped templates are objects
                    ops.append(ScaffoldOp("copy", target, source=item))
                    continue
                schema_name = store.SUBJECT_KNOWLEDGE_FILES.get(
                    item.name
                ) or store.SUBJECT_MEMORY_FILES.get(item.name)
                ops.append(
                    ScaffoldOp(
                        "json",
                        target,
                        data=_substitute_json(
                            data,
                            subject_id=subject_id,
                            name=name,
                            exam_date=exam_date,
                            description=description,
                            now=now,
                        ),
                        schema=schema_name,
                    )
                )
            elif item.suffix == ".md":
                ops.append(
                    ScaffoldOp(
                        "text",
                        target,
                        data=_substitute_markdown(read_text(item), subject_id, name),
                    )
                )
            else:
                ops.append(ScaffoldOp("copy", target, source=item))

    template_ops(ws.subject_template, knowledge_target)
    template_ops(ws.memory_templates, memory_target, excludes=MEMORY_TEMPLATE_EXCLUDES)
    return ops


def _assert_existing_subject_healthy(ws: Workspace, subject_id: str) -> None:
    """Exit 3 when a surviving file of this subject is corrupt.

    The alternative — quietly writing a fresh template over it — would destroy both the learner's
    progress and the evidence of what broke.
    """
    for name, schema_name in store.SUBJECT_MEMORY_FILES.items():
        path = ws.memory_subject(subject_id) / name
        if path.is_file():
            if schema_name == "concept_mastery":
                store.load_concept_mastery(ws, subject_id)
            else:
                store.load_json_file(ws, path, schema_name)
    for name, schema_name in store.SUBJECT_KNOWLEDGE_FILES.items():
        path = ws.knowledge_subject(subject_id) / name
        if path.is_file():
            store.load_json_file(ws, path, schema_name)


def _learner_index_row(subject_id: str, name: str, exam_date: str | None) -> str:
    return f"| `{subject_id}` | {name} | {exam_date or '—'} | 0 | 0 | 0 | — |"


def _ensure_learner_index_row(
    ws: Workspace,
    subject_id: str,
    name: str,
    exam_date: str | None,
    *,
    dry_run: bool,
) -> bool:
    """Add one row to ``learner_index.md`` for a new subject. Never refreshes existing rows.

    Refreshing is deliberately out of scope: it would make a second `init-subject` rewrite the
    file, and `status` already reads the live state and is authoritative over this summary.
    """
    path = ws.learner_index_file
    if not path.is_file():
        return False
    text = read_text(path)
    if f"| `{subject_id}` |" in text:
        return False
    row = _learner_index_row(subject_id, name, exam_date)
    lines = text.splitlines()
    placeholder = next(
        (index for index, line in enumerate(lines) if "no subjects yet" in line), None
    )
    if placeholder is not None:
        lines[placeholder] = row
    else:
        last_row = None
        for index, line in enumerate(lines):
            if line.startswith("|"):
                last_row = index
            elif last_row is not None and line.strip() == "":
                break
        if last_row is None:
            return False
        lines.insert(last_row + 1, row)
    if not dry_run:
        atomic_write_text(path, "\n".join(lines) + "\n")
    return True


def _register_subject_if_needed(
    ws: Workspace,
    subject_id: str,
    name: str,
    exam_date: str | None,
    *,
    created_at: str,
    dry_run: bool,
) -> bool:
    """Register the subject in ``workspace_state.json`` only when that changes something.

    `init-subject` has to be hash-stable on a second run, so a no-op must not bump
    ``updated_at``.
    """
    state = store.load_workspace_state(ws, required=False)
    if state is None:
        if dry_run:
            return True
        store.register_subject(ws, subject_id, name, exam_date=exam_date, created_at=created_at)
        return True
    before = dumps_json({key: value for key, value in state.items() if key != "updated_at"})
    candidate = json.loads(dumps_json(state))
    subjects = candidate.setdefault("subjects", [])
    entry = next(
        (item for item in subjects if item.get("subject_id") == subject_id),
        None,
    )
    if entry is None:
        subjects.append(
            {
                "subject_id": subject_id,
                "name": name,
                "created_at": created_at,
                "exam_date": exam_date,
                "last_session_at": None,
                "knowledge_path": ws.rel(ws.knowledge_subject(subject_id)),
                "memory_path": ws.rel(ws.memory_subject(subject_id)),
            }
        )
        subjects.sort(key=lambda item: str(item.get("subject_id")))
    if not candidate.get("current_subject_id"):
        candidate["current_subject_id"] = subject_id
    candidate["tool_version"] = TOOL_ID
    after = dumps_json({key: value for key, value in candidate.items() if key != "updated_at"})
    if after == before:
        return False
    if not dry_run:
        store.save_workspace_state(ws, candidate)
    return True


def cmd_init_subject(args: argparse.Namespace, ws: Workspace) -> int:
    """Create both halves of a subject from the templates, idempotently and in isolation."""
    subject_id = args.id
    if not SUBJECT_ID_RE.match(subject_id):
        raise LearningOSUsageError(
            f"invalid subject id {subject_id!r}",
            remedy="Use a lowercase slug matching ^[a-z0-9][a-z0-9_-]*$ — short and stable, "
            "e.g. `thermo`, not `Thermodynamics sem-4 2026!`. The id becomes the directory name "
            "and the prefix of every concept id in the subject.",
        )
    if subject_id in RESERVED_SUBJECT_IDS:
        raise LearningOSUsageError(
            f"subject id {subject_id!r} is reserved for scaffolding",
            remedy="Pick another id.",
        )
    exam_date = args.exam_date
    if exam_date:
        parse_date(exam_date, field_name="--exam-date")

    existed_before = store.subject_exists(ws, subject_id)
    if existed_before:
        _assert_existing_subject_healthy(ws, subject_id)

    ops = _subject_scaffold_ops(
        ws,
        subject_id=subject_id,
        name=args.name,
        exam_date=exam_date,
        description=args.description,
    )
    created, skipped, _overwritten = _apply_ops(ws, ops, dry_run=args.dry_run, force=False)
    first_creation = not existed_before and bool(created)
    created_at = utc_now_iso()

    event_id = None
    if first_creation and not args.dry_run:
        event = events_mod.append_event(
            ws,
            type="subject_initialized",
            session_id=f"{created_at[:10]}-{subject_id}-init",
            subject_id=subject_id,
            actor="tool",
            payload={
                "name": args.name,
                "exam_date": exam_date,
                "created_paths": [
                    relative.rstrip("/") for relative in created if not relative.endswith("/")
                ][:50],
                "template_version": SCHEMA_VERSION,
            },
        )
        event_id = event["event_id"]
    registered = _register_subject_if_needed(
        ws, subject_id, args.name, exam_date, created_at=created_at, dry_run=args.dry_run
    )
    indexed = _ensure_learner_index_row(
        ws, subject_id, args.name, exam_date, dry_run=args.dry_run
    )

    if args.json:
        print(
            dumps_json(
                {
                    "subject_id": subject_id,
                    "name": args.name,
                    "exam_date": exam_date,
                    "dry_run": bool(args.dry_run),
                    "existed_before": existed_before,
                    "created": created,
                    "skipped": skipped,
                    "registered": registered,
                    "learner_index_updated": indexed,
                    "event_id": event_id,
                    "knowledge_path": ws.rel(ws.knowledge_subject(subject_id)),
                    "memory_path": ws.rel(ws.memory_subject(subject_id)),
                }
            ),
            end="",
        )
        return EXIT_OK
    if args.quiet:
        return EXIT_OK
    verb = "would create" if args.dry_run else "created"
    if not created:
        print(f"subject {subject_id!r} already exists; nothing to do.")
    else:
        print(f"subject {subject_id!r} ({args.name}) — {verb} {len(created)} path(s):")
        for relative in created:
            print(f"  + {relative}")
    if skipped:
        print(f"  {len(skipped)} existing file(s) left untouched")
    print(f"  knowledge: {ws.rel(ws.knowledge_subject(subject_id))}")
    print(f"  memory:    {ws.rel(ws.memory_subject(subject_id))}")
    if created or args.dry_run:
        print(
            "  mastery:   concepts = {} (a new subject never inherits another subject's progress)"
        )
    else:
        # A no-op run must describe the mastery that is actually on disk, not the mastery a new
        # subject would have started with.
        existing = store.load_concept_mastery(ws, subject_id).get("concepts") or {}
        print(f"  mastery:   {len(existing)} concept record(s) already on disk, left untouched")
    if args.dry_run:
        print("\n--dry-run: nothing was written.")
    elif created:
        print(
            "\nNext: copy your material into "
            f"{ws.rel(ws.knowledge_subject(subject_id))}/source_materials/, then "
            f"`python learningos.py index --subject {subject_id}`."
        )
    return EXIT_OK


# ===================================================================================== validate


def cmd_validate(args: argparse.Namespace, ws: Workspace) -> int:
    """Check the whole install: structure, schemas, references, event log, stored paths."""
    findings = validate_workspace(ws, subject=args.subject, strict=args.strict)
    if args.json:
        print_findings(findings, as_json=True)
        return EXIT_VALIDATION if has_errors(findings) else EXIT_OK
    counts = count_findings(findings)
    if not args.quiet:
        scope = f"subject {args.subject}" if args.subject else "whole workspace"
        print(f"validate — {scope} at {ws.root}")
    print_findings(findings, quiet=args.quiet)
    if not args.quiet:
        print(
            f"\n{counts['error']} error(s), {counts['warning']} warning(s), "
            f"{counts['info']} note(s)"
        )
        if counts["error"]:
            print(
                "Errors must be fixed by hand or with `restore`; LearningOS never repairs a file "
                "for you."
            )
        elif counts["warning"]:
            print("No errors. Warnings are safe to leave, and each one names its own remedy.")
        else:
            print("No problems found.")
    return EXIT_VALIDATION if has_errors(findings) else EXIT_OK


# ======================================================================================= status


def _renderer_status() -> tuple[bool, str]:
    path = which("mmdc")
    if path:
        return True, f"mermaid-cli (mmdc) found at {path}; SVG rendering available"
    return (
        False,
        "mermaid-cli (mmdc) not found on PATH — .mmd and .html artefacts are still written, "
        "only .svg is skipped",
    )


def _collect_status(ws: Workspace) -> dict[str, Any]:
    state = store.load_workspace_state(ws, required=False) or {}
    subjects: list[dict[str, Any]] = []
    now = utc_now_iso()
    for subject_id in ws.list_subjects():
        subject_state = store.load_subject_state(ws, subject_id, required=False) or {}
        mastery = store.load_concept_mastery(ws, subject_id, required=False)
        queue = store.load_revision_queue(ws, subject_id, required=False) or {}
        due = [
            item
            for item in queue.get("items", [])
            if isinstance(item, dict) and str(item.get("due_at", "")) <= now
        ]
        subjects.append(
            {
                "subject_id": subject_id,
                "name": subject_state.get("name") or subject_id,
                "exam_date": subject_state.get("exam_date"),
                "concepts": len((mastery or {}).get("concepts", {})),
                "statuses": store.status_counts(mastery),
                "due_reviews": len(due),
                "sessions": subject_state.get("session_count", 0),
                "last_session_at": subject_state.get("last_session_at"),
            }
        )
    valid_events, corrupt_events = events_mod.count_events(ws)
    findings = validate_workspace(ws)
    counts = count_findings(findings)
    backups = (
        sorted(path.name for path in ws.backups.glob("*.zip")) if ws.backups.is_dir() else []
    )
    renderer_ok, renderer_message = _renderer_status()
    vendored = graph_render.vendored_renderer()
    pypdf_ok, pypdf_version = optional_dependency("pypdf")
    jsonschema_ok, jsonschema_version = optional_dependency("jsonschema")
    return {
        "tool": TOOL_ID,
        "workspace": str(ws.root),
        "current_subject_id": state.get("current_subject_id"),
        "last_session_id": state.get("last_session_id"),
        "last_session_at": state.get("last_session_at"),
        "last_backup_id": state.get("last_backup_id") or (backups[-1][:-4] if backups else None),
        "subjects": subjects,
        "events": {"valid": valid_events, "unreadable": corrupt_events},
        "validate": counts,
        "backups_on_disk": len(backups),
        "renderer": {"available": renderer_ok, "message": renderer_message},
        "vendored_renderer": {
            "available": vendored is not None,
            "message": (
                f"graphs/vendor/{graph_render.VENDOR_SCRIPT_NAME} is present; HTML graph pages "
                "inline it and render offline"
                if vendored is not None
                else f"no graphs/vendor/{graph_render.VENDOR_SCRIPT_NAME}; HTML graph pages show "
                "the Mermaid source plus instructions (see graphs/vendor/README.md)"
            ),
        },
        "optional_dependencies": {
            "pypdf": {"available": pypdf_ok, "version": pypdf_version},
            "jsonschema": {"available": jsonschema_ok, "version": jsonschema_version},
        },
    }


def cmd_status(args: argparse.Namespace, ws: Workspace) -> int:
    """One screen of truth about the install: subjects, evidence, health, capabilities."""
    data = _collect_status(ws)
    if args.json:
        print(dumps_json(data), end="")
        return EXIT_OK
    print(f"{data['tool']} — {data['workspace']}")
    current = data["current_subject_id"] or "none"
    print(f"subjects: {len(data['subjects'])}   current: {current}")
    if data["subjects"]:
        print(
            f"  {'id':<14}{'concepts':>9}{'unassessed':>12}{'developing':>12}"
            f"{'practising':>12}{'mastered':>10}{'review':>8}{'due':>6}"
        )
        for subject in data["subjects"]:
            statuses = subject["statuses"]
            print(
                f"  {subject['subject_id']:<14}{subject['concepts']:>9}"
                f"{statuses['unassessed']:>12}{statuses['developing']:>12}"
                f"{statuses['practising']:>12}{statuses['provisionally_mastered']:>10}"
                f"{statuses['needs_review']:>8}{subject['due_reviews']:>6}"
            )
        print("  'mastered' means provisionally mastered: enough unaided evidence to stop "
              "teaching and start spacing.")
    else:
        print('  none yet — run `init-subject --id <id> --name "<name>"`')
    events = data["events"]
    suffix = (
        f", {events['unreadable']} unreadable line(s)"
        if events["unreadable"]
        else ", no unreadable lines"
    )
    print(f"evidence: {events['valid']} event(s) in {ws.rel(ws.event_log)}{suffix}")
    print(
        f"last session: {data['last_session_id'] or '—'}"
        f" ({data['last_session_at'] or 'no date recorded'})"
    )
    print(
        f"backups: {data['backups_on_disk']} on disk; last id {data['last_backup_id'] or '—'}"
    )
    counts = data["validate"]
    print(
        f"validate: {counts['error']} error(s), {counts['warning']} warning(s)"
        + ("" if counts["error"] == 0 else " — run `validate` for details")
    )
    print(f"renderer: {data['renderer']['message']}")
    print(f"viewer:   {data['vendored_renderer']['message']}")
    for name, info in data["optional_dependencies"].items():
        version = info["version"] or "version unknown"
        print(f"optional: {name} {'available (' + version + ')' if info['available'] else 'not installed'}")
    return EXIT_OK


# ================================================================== resume and checkpointing


def _resolve_subject(ws: Workspace, explicit: str | None) -> str:
    """``--subject`` wins, then ``workspace_state.current_subject_id``. Exit 4 when neither works."""
    if explicit:
        store.require_subject(ws, explicit)
        return explicit
    state = store.load_workspace_state(ws, required=False) or {}
    current = state.get("current_subject_id")
    if isinstance(current, str) and current and store.subject_exists(ws, current):
        return current
    known = ws.list_subjects()
    raise LearningOSNotFoundError(
        "no subject to work with: --subject was not given and workspace_state.json has no usable "
        "current_subject_id",
        path=ws.rel(ws.workspace_state_file),
        pointer="/current_subject_id",
        remedy=(
            "Pass --subject with one of: " + ", ".join(known)
            if known
            else 'Create a subject first: `python learningos.py init-subject --id <id> '
            '--name "<name>"`.'
        ),
    )


def _open_questions(ws: Workspace, subject_id: str) -> list[str]:
    """The unanswered questions from ``open_questions.md``, ignoring the format examples.

    The template documents its own format inside HTML comments, so the comment blocks are skipped —
    otherwise every fresh subject would "resume" with the example question.
    """
    path = ws.memory_subject(subject_id) / "open_questions.md"
    if not path.is_file():
        return []
    questions: list[str] = []
    in_comment = False
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if "<!--" in stripped:
            in_comment = "-->" not in stripped
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith(("- [open]", "- [blocked]")):
            questions.append(stripped[2:])
    return questions


def _next_session_plan(ws: Workspace, subject_id: str) -> str | None:
    """The "First activity" line from ``next_session.md``, when one has been written."""
    path = ws.memory_subject(subject_id) / "next_session.md"
    if not path.is_file():
        return None
    for line in read_text(path).splitlines():
        if "First activity:" in line:
            text = line.split("First activity:", 1)[1].strip()
            text = text.strip("*").strip()
            if text and not text.startswith("(be exact"):
                return text
    return None


def _collect_resume(ws: Workspace, subject_id: str) -> dict[str, Any]:
    found = store.latest_checkpoint(ws, subject_id)
    if found is None:
        raise LearningOSNotFoundError(
            f"no checkpoint has been written for subject {subject_id!r}, so there is no resume "
            "point",
            path=ws.rel(ws.checkpoint_dir(subject_id)),
            remedy="Start a fresh session instead: read "
            f"03_MEMORY/subjects/{subject_id}/next_session.md and concept_mastery.json, then "
            "write one with `python learningos.py checkpoint --subject "
            f'{subject_id} --session-id <id> --note "<where we stopped>" --next-action "<next>"`.',
        )
    checkpoint_path, checkpoint = found
    fingerprint_findings = store.check_fingerprints(ws, checkpoint)
    queue = store.load_revision_queue(ws, subject_id, required=False)
    due = mastery_mod.due_items(queue)
    mastery = store.load_concept_mastery(ws, subject_id, required=False) or {}
    records = mastery.get("concepts") or {}
    in_flight = []
    for item in checkpoint.get("concepts_in_flight") or []:
        if not isinstance(item, dict):
            continue
        concept_id = str(item.get("concept_id"))
        record = records.get(concept_id) if isinstance(records, dict) else None
        in_flight.append(
            {
                "concept_id": concept_id,
                "name": (record or {}).get("name"),
                "status": (record or {}).get("status") or item.get("status"),
                "mastery_estimate": (record or {}).get("mastery_estimate"),
                "note": item.get("note"),
            }
        )
    recent, _findings = events_mod.read_events(ws, subject_id=subject_id)
    ended_after = any(
        event.get("type") == "session_ended"
        and str(event.get("at") or "") >= str(checkpoint.get("created_at") or "")
        and event.get("session_id") == checkpoint.get("session_id")
        for event in recent
    )
    return {
        "subject_id": subject_id,
        "checkpoint_path": ws.rel(checkpoint_path),
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "session_id": checkpoint.get("session_id"),
        "created_at": checkpoint.get("created_at"),
        "session_was_closed": ended_after,
        "where_we_stopped": checkpoint.get("where_we_stopped"),
        "next_action": checkpoint.get("next_action"),
        "concepts_in_flight": in_flight,
        "checkpoint_open_questions": list(checkpoint.get("open_questions") or []),
        "open_questions": _open_questions(ws, subject_id),
        "next_session_plan": _next_session_plan(ws, subject_id),
        "due_reviews": [
            {
                "concept_id": item.get("concept_id"),
                "due_at": item.get("due_at"),
                "last_outcome": item.get("last_outcome"),
                "step_index": item.get("step_index"),
            }
            for item in due
        ],
        "due_review_count": len(due),
        "recent_events": [
            {
                "at": event.get("at"),
                "type": event.get("type"),
                "concept_ids": event.get("concept_ids") or [],
            }
            for event in recent[-5:]
        ],
        "warnings": [finding.to_dict() for finding in fingerprint_findings],
    }


def cmd_resume(args: argparse.Namespace, ws: Workspace) -> int:
    """Brief the agent from the newest checkpoint, the due queue and the last five events."""
    subject_id = _resolve_subject(ws, args.subject)
    briefing = _collect_resume(ws, subject_id)
    if args.json:
        print(dumps_json(briefing), end="")
        return EXIT_OK
    print(f"resume — subject {briefing['subject_id']}")
    print(f"  checkpoint:   {briefing['checkpoint_id']} ({briefing['created_at']})")
    print(f"  session:      {briefing['session_id']}"
          + ("  [closed cleanly]" if briefing["session_was_closed"] else "  [interrupted]"))
    print(f"  stopped at:   {briefing['where_we_stopped']}")
    print(f"  next action:  {briefing['next_action']}")
    if briefing["concepts_in_flight"]:
        print("  in flight:")
        for concept in briefing["concepts_in_flight"]:
            estimate = concept["mastery_estimate"]
            shown = "—" if estimate is None else f"{estimate:.2f}"
            print(
                f"    - {concept['concept_id']} ({concept['status'] or 'unknown'}, "
                f"mastery {shown})"
                + (f" — {concept['note']}" if concept.get("note") else "")
            )
    else:
        print("  in flight:    none recorded")
    print(f"  due reviews:  {briefing['due_review_count']}")
    for item in briefing["due_reviews"][:8]:
        print(f"    - {item['concept_id']} due {item['due_at']}")
    questions = briefing["checkpoint_open_questions"] + briefing["open_questions"]
    print(f"  open questions: {len(questions)}")
    for question in questions[:5]:
        print(f"    - {question}")
    if briefing["next_session_plan"]:
        print(f"  next_session.md: {briefing['next_session_plan']}")
    if briefing["recent_events"]:
        print("  last events:")
        for event in briefing["recent_events"]:
            concepts = ",".join(event["concept_ids"]) or "—"
            print(f"    {event['at']}  {event['type']:<22} {concepts}")
    for warning in briefing["warnings"]:
        print(f"  warning: {warning['message']}")
        print(f"           remedy: {warning['remedy']}")
    if briefing["warnings"]:
        print(
            "  memory changed since this checkpoint was taken: trust the current state files and "
            "verify the in-flight concepts with a probe."
        )
    return EXIT_OK


def cmd_checkpoint(args: argparse.Namespace, ws: Workspace) -> int:
    """Write a resume point and append the matching ``checkpoint_written`` event."""
    subject_id = args.subject
    store.require_subject(ws, subject_id)
    concepts = [{"concept_id": concept_id} for concept_id in args.concept or []]
    checkpoint = store.build_checkpoint(
        ws,
        subject_id=subject_id,
        session_id=args.session_id,
        where_we_stopped=args.note,
        next_action=args.next_action,
        concepts_in_flight=concepts,
        open_questions=args.open_question or [],
        trigger=args.trigger,
        agent=args.agent,
    )
    events_mod.append_event(
        ws,
        type="checkpoint_written",
        session_id=args.session_id,
        subject_id=subject_id,
        concept_ids=[concept["concept_id"] for concept in concepts],
        actor="agent",
        payload={
            "checkpoint_id": checkpoint["checkpoint_id"],
            "relative_path": ws.rel(store.checkpoint_path(ws, subject_id, args.session_id)),
            "where_we_stopped": args.note,
            "next_action": args.next_action,
            "trigger": args.trigger,
        },
    )
    path = store.write_checkpoint(ws, checkpoint)
    if args.json:
        print(
            dumps_json(
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "path": ws.rel(path),
                    "subject_id": subject_id,
                    "session_id": args.session_id,
                    "fingerprinted_files": sorted(checkpoint["state_fingerprints"]),
                }
            ),
            end="",
        )
        return EXIT_OK
    if not args.quiet:
        print(f"checkpoint {checkpoint['checkpoint_id']} -> {ws.rel(path)}")
        print(f"  stopped at:  {args.note}")
        print(f"  next action: {args.next_action}")
        print(f"  fingerprinted {len(checkpoint['state_fingerprints'])} memory file(s)")
        print(f"  resume with: python learningos.py resume --subject {subject_id}")
    return EXIT_OK


# ================================================================================ search-memory


def cmd_search_memory(args: argparse.Namespace, ws: Workspace) -> int:
    """Search the event log, the concept records and the misconceptions. Local, no embeddings."""
    if args.type and args.type not in events_mod.EVENT_TYPES:
        raise LearningOSUsageError(
            f"unknown event type {args.type!r}",
            remedy="Valid types: " + ", ".join(events_mod.EVENT_TYPES),
        )
    if args.limit is not None and args.limit < 1:
        raise LearningOSUsageError(
            "--limit must be at least 1",
            remedy="Leave it out for the default of 20 results.",
        )
    notice: str | None = None
    if args.reindex:
        _built, notice = search_mod.build_index(ws)
    result = search_mod.search(
        ws,
        args.query,
        subject=args.subject,
        event_type=args.type,
        since=args.since,
        limit=args.limit or 20,
    )
    if args.json:
        print(
            dumps_json(
                {
                    "query": args.query,
                    "engine": result.engine,
                    "count": len(result.hits),
                    "notice": notice,
                    "unreadable_log_lines": len(result.findings),
                    "hits": [hit.to_dict() for hit in result.hits],
                }
            ),
            end="",
        )
        return EXIT_OK
    if notice and not args.quiet:
        print(notice)
    if not result.hits:
        print(f"no matches for {args.query!r}")
        return EXIT_OK
    if not args.quiet:
        print(
            f"{len(result.hits)} match(es) for {args.query!r} "
            f"({result.engine} over {result.scanned} document(s))"
        )
        print(
            f"{'at':<21} {'subject':<10} {'type':<22} {'concepts':<34} snippet"
        )
    for hit in result.hits:
        print(hit.format_human())
    if result.findings and not args.quiet:
        print(
            f"\n{len(result.findings)} unreadable event-log line(s) were skipped; run "
            "`python learningos.py validate` for their line numbers."
        )
    return EXIT_OK


# =============================================================================== session-report


def cmd_session_report(args: argparse.Namespace, ws: Workspace) -> int:
    """Derive the markdown session log from the event log."""
    subject = args.subject
    if subject:
        store.require_subject(ws, subject)
    session_id = args.session_id
    if not session_id:
        latest = report_mod.latest_session(ws, subject=subject)
        if latest is None:
            raise LearningOSNotFoundError(
                "the event log has no sessions yet"
                + (f" for subject {subject!r}" if subject else ""),
                path=ws.rel(ws.event_log),
                remedy="Teach a session first; `session-report` only ever reports evidence that "
                "was actually recorded.",
            )
        session_id = latest.session_id
    report = report_mod.build_report(ws, session_id, subject=subject)

    if args.stdout_only:
        print(report_mod.render_markdown(report))
        return EXIT_OK
    out = Path(args.out).expanduser() if args.out else None
    path, replaced = report_mod.write_report(ws, report, out=out, force=args.force)
    if args.json:
        payload = dict(report)
        payload["path"] = ws.rel(path) if ws.root in path.parents else str(path)
        payload["replaced_existing"] = replaced
        print(dumps_json(payload), end="")
        return EXIT_OK
    if not args.quiet:
        print(report_mod.summarise(report))
        print(f"  written to {ws.rel(path) if ws.root in path.parents else path}")
        if replaced:
            print("  (--force: the previous report for this session was replaced)")
        elif path.name != report_mod.report_filename(report):
            print(
                "  an earlier report for this session exists and was left alone; this one was "
                "written beside it. Use --force to replace instead."
            )
    return EXIT_OK


# ============================================================== backup / restore / reconcile


def cmd_backup(args: argparse.Namespace, ws: Workspace) -> int:
    """Zip 03_MEMORY plus the subject descriptions, with a sha256 manifest beside it."""
    result = backup_mod.create_backup(
        ws,
        note=args.note,
        include_sources=args.include_sources,
        dry_run=args.dry_run,
    )
    if args.json:
        print(dumps_json(result.to_dict(ws)), end="")
        return EXIT_OK
    if args.quiet:
        return EXIT_OK
    verb = "would archive" if args.dry_run else "archived"
    print(f"backup {result.backup_id}: {verb} {result.plan.file_count} file(s), "
          f"{result.plan.total_bytes} byte(s)")
    for relative in [path for _p, path in result.plan.files][:40]:
        print(f"  + {relative}")
    if result.plan.file_count > 40:
        print(f"  … and {result.plan.file_count - 40} more")
    if args.dry_run:
        print("\n--dry-run: nothing was written.")
    else:
        print(f"  zip:      {ws.rel(result.zip_path)}")
        print(f"  manifest: {ws.rel(result.manifest_path)} (sha256 per file)")
        if not args.include_sources:
            print(
                "  source_materials/ was not included; add --include-sources to archive your "
                "original documents too."
            )
    return EXIT_OK


def cmd_restore(args: argparse.Namespace, ws: Workspace) -> int:
    """Verify a backup, then replace files from it. Refuses to overwrite without --force."""
    result = backup_mod.restore(
        ws,
        args.source,
        dry_run=args.dry_run,
        force=args.force,
        only=args.only or None,
    )
    if args.json:
        print(dumps_json(result.to_dict()), end="")
        return EXIT_OK
    if args.quiet:
        return EXIT_OK
    if result.dry_run:
        print(
            f"restore {result.backup_id} --dry-run: {len(result.planned)} file(s) would be "
            f"written, {len(result.would_overwrite)} of them over an existing file"
        )
        for relative in result.planned[:40]:
            marker = "~" if relative in set(result.would_overwrite) else "+"
            print(f"  {marker} {relative}")
        if len(result.planned) > 40:
            print(f"  … and {len(result.planned) - 40} more")
        print("\nEvery manifest sha256 verified. --dry-run: nothing was written.")
        if result.would_overwrite and not result.forced:
            print("Re-run with --force to replace the existing files.")
        return EXIT_OK
    print(f"restore {result.backup_id}: wrote {len(result.written)} file(s)")
    if result.safety_backup_id:
        print(
            f"  a pre-restore safety backup was taken first: {result.safety_backup_id} "
            f"(in {ws.rel(ws.backups)})"
        )
    print("  run `python learningos.py validate` to confirm the restored tree is healthy")
    return EXIT_OK


def cmd_reconcile(args: argparse.Namespace, ws: Workspace) -> int:
    """Replay the event log against the snapshots. Report-only unless --apply is given."""
    report = reconcile_mod.analyse(ws, subject=args.subject)
    writing = bool(args.apply) and not args.dry_run
    repairable = bool(report.repairable) or any(
        subject.queue_rebuild for subject in report.subjects
    )
    changes: dict[str, Any] | None = None
    if writing and repairable:
        changes = reconcile_mod.apply(ws, report)
    if args.json:
        payload = report.to_dict()
        payload["applied"] = changes
        payload["dry_run"] = bool(args.dry_run) or not args.apply
        print(dumps_json(payload), end="")
        return EXIT_VALIDATION if any(f.is_error for f in report.findings) else EXIT_OK
    scope = f"subject {args.subject}" if args.subject else "all subjects"
    if not args.quiet:
        print(f"reconcile — {scope}")
    print_findings(report.findings, quiet=args.quiet)
    if args.quiet:
        return EXIT_VALIDATION if any(f.is_error for f in report.findings) else EXIT_OK
    if not report.has_drift and not report.log_findings:
        print("\nNo drift: every snapshot matches the evidence.")
    elif changes is not None:
        print(
            f"\nrepaired {len(changes['concepts'])} concept(s) and "
            f"{len(changes['queues'])} queue(s) after backup {changes['backup_id']}"
        )
        for change in changes["concepts"][:20]:
            print(
                f"  {change['concept_id']}: {change['status_before']} -> {change['status_after']}"
                f" (mastery {change['mastery_after']})"
            )
    elif writing:
        print(
            "\nNothing was written: the drift reported above is not derivable from the stored "
            "evidence. Lost evidence is a `restore` job, not a recompute."
        )
    else:
        print(
            f"\n{len(report.findings)} item(s) reported, {report.repairable} concept(s) could be "
            "re-derived from the stored evidence."
        )
        print(
            "Nothing was written. Run `python learningos.py reconcile --apply` to repair the "
            "derived fields (it takes a backup first)."
        )
    return EXIT_VALIDATION if any(f.is_error for f in report.findings) else EXIT_OK


# ================================================================== ingestion (the `index` command)


def _index_targets(args: argparse.Namespace, ws: Workspace) -> list[str]:
    """Which subjects this invocation will index. Exit 2 when the request is contradictory."""
    if args.all and args.subject:
        raise LearningOSUsageError(
            "--subject and --all cannot both be given",
            remedy="Index one subject with --subject <id>, or every subject with --all.",
        )
    if args.assign_to and not args.inbox:
        raise LearningOSUsageError(
            "--assign-to only makes sense with --inbox",
            remedy=f"Run `index --inbox --assign-to {args.assign_to} --copy`, or drop "
            "--assign-to.",
        )
    if args.subject:
        store.require_subject(ws, args.subject)
        return [args.subject]
    if args.all:
        return ws.list_subjects()
    if args.inbox:
        return [args.assign_to] if args.assign_to else []
    raise LearningOSUsageError(
        "nothing to index",
        remedy="Pass --subject <id>, --all, or --inbox to see what is waiting in "
        "02_KNOWLEDGE/inbox/.",
    )


def _print_inbox(args: argparse.Namespace, ws: Workspace) -> list[indexer_mod.Assignment]:
    """List the inbox and, with --assign-to and --copy, copy its files into a subject."""
    waiting = indexer_mod.list_inbox(ws)
    if not args.quiet:
        label = ws.rel(ws.inbox)
        if not waiting:
            print(f"{label}: empty — drop syllabi, notes, chapters or past papers in there.")
        else:
            print(f"{label}: {len(waiting)} file(s) waiting")
            for path in waiting:
                print(f"  {path.relative_to(ws.inbox).as_posix()}  ({path.stat().st_size} bytes)")
    if not args.assign_to:
        if waiting and not args.quiet:
            print(
                "\nAssign them with: python learningos.py index --inbox --assign-to <subject> "
                "--copy"
            )
        return []

    store.require_subject(ws, args.assign_to)
    planning = args.dry_run or not args.copy
    assignments = indexer_mod.assign_from_inbox(ws, args.assign_to, dry_run=planning)
    if not args.quiet:
        verb = "would copy" if planning else "copied"
        print(f"\n{verb} into {ws.rel(indexer_mod.source_materials_dir(ws, args.assign_to))}/")
        for assignment in assignments:
            marker = {"copied": "+", "would-copy": "+", "already-present": "="}[assignment.action]
            print(f"  {marker} {assignment.name} ({assignment.action})")
        print(
            "  originals stay in the inbox: LearningOS copies, never moves, so a mis-assignment "
            "cannot lose the only copy."
        )
        if not args.copy:
            print("\nNothing was written. Re-run with --copy to make the copies.")
    return assignments


def cmd_index(args: argparse.Namespace, ws: Workspace) -> int:
    """Hash the learner's documents, derive text from what changed, record provenance."""
    assignments: list[indexer_mod.Assignment] = []
    if args.inbox:
        assignments = _print_inbox(args, ws)
    targets = _index_targets(args, ws)
    if args.inbox and not args.copy and args.assign_to:
        targets = []  # nothing was copied, so there is nothing new to index

    results = [
        indexer_mod.index_subject(ws, subject_id, force=args.force, dry_run=args.dry_run)
        for subject_id in targets
    ]

    needs_ocr = [item for result in results for item in result.needs_ocr]
    without_text = [item for result in results for item in result.without_text]

    if args.json:
        print(
            dumps_json(
                {
                    "tool": TOOL_ID,
                    "dry_run": bool(args.dry_run),
                    "assignments": [item.to_dict() for item in assignments],
                    "subjects": [result.to_dict() for result in results],
                }
            ),
            end="",
        )
    elif not args.quiet:
        if not results and not args.inbox:
            print("no subjects to index yet. Create one with `init-subject`.")
        for result in results:
            directory = ws.rel(indexer_mod.source_materials_dir(ws, result.subject_id))
            print(f"\n{result.subject_id}: {len(result.sources)} source(s) in {directory}/")
            for item in result.sources:
                flag = {
                    "extracted": "extracted",
                    "would-extract": "would extract",
                    "skipped": "unchanged",
                }[item.action]
                pages = f", {item.record.get('pages')} page(s)" if item.record.get("pages") else ""
                print(f"  [{flag}] {item.relative_path}  ({item.kind}{pages})")
                print(f"           id {item.source_id}")
                for warning in item.warnings:
                    print(f"           ! {warning}")
            if result.vanished:
                print("  no longer on disk (dropped from the index):")
                for relative in result.vanished:
                    print(f"    - {relative}")
            if not args.dry_run and result.index_rewritten:
                derived = len([item for item in result.extracted if item.produced_text])
                print(
                    f"  wrote {ws.rel(ws.knowledge_subject(result.subject_id))}/source_index.json,"
                    f" {derived} extracted text file(s), {len(result.skipped)} unchanged"
                )
            elif not args.dry_run:
                print(
                    f"  nothing changed: {len(result.skipped)} source(s) already indexed, so "
                    "source_index.json was left exactly as it was"
                )
        if args.dry_run and results:
            print("\n--dry-run: nothing was written.")
        if needs_ocr:
            print(
                f"\n{len(needs_ocr)} source(s) need OCR. LearningOS does not perform OCR: run it "
                "externally and drop the text in 02_KNOWLEDGE/inbox/, or paste the passage you "
                "need. These sources are recorded with their hashes — never as empty documents."
            )
        if without_text:
            print(
                f"{len(without_text)} source(s) have no extracted text. Cite them only from a "
                "passage you have actually read."
            )

    if args.strict and (needs_ocr or without_text):
        raise LearningOSDataError(
            f"--strict: {len(needs_ocr)} source(s) need OCR and "
            f"{len(without_text)} produced no text",
            path=ws.rel(ws.knowledge_subjects),
            remedy="Supply a readable export of those sources, or drop --strict to accept them "
            "as indexed-but-unreadable. The index itself was written.",
            code="unreadable_sources",
        )
    return EXIT_OK


# ============================================================ graphs (the `update-graphs` command)


def _graph_subjects(args: argparse.Namespace, ws: Workspace) -> list[str]:
    if args.all and args.subject:
        raise LearningOSUsageError(
            "--subject and --all cannot both be given",
            remedy="Draw one subject with --subject <id>, or every subject with --all.",
        )
    if args.subject:
        store.require_subject(ws, args.subject)
        return [args.subject]
    return ws.list_subjects()


def cmd_update_graphs(args: argparse.Namespace, ws: Workspace) -> int:
    """Regenerate the derived graph artefacts. Never writes a source-of-truth memory file."""
    kinds = graph_build.kinds_for(args.kinds)
    formats = graph_render.parse_formats(args.format)
    subjects = _graph_subjects(args, ws)
    out_base = Path(args.out_dir).expanduser().resolve() if args.out_dir else ws.graphs

    if args.require_renderer and "svg" in formats and graph_render.renderer_path() is None:
        # Checked before anything is built or written, so the run either produces every artefact
        # it promised or none of them.
        raise LearningOSMissingDependencyError(
            "mermaid-cli (mmdc) is not on PATH, and --require-renderer was given",
            remedy="Install mermaid-cli yourself, or drop --require-renderer: the .mmd and .html "
            "are written either way and the .svg is only a convenience.",
        )

    # Build everything first. A dangling endpoint anywhere must stop the run with exit 3 *before*
    # a single file is written, so the graphs on disk never disagree with the memory they claim.
    index = graph_build.build_concept_index(ws)
    now = utc_now_iso()
    jobs: list[tuple[str | None, Path, dict[str, Any]]] = []
    for subject_id in subjects:
        for kind in kinds:
            if kind not in graph_build.PER_SUBJECT_KINDS:
                continue
            jobs.append(
                (
                    subject_id,
                    out_base / subject_id,
                    graph_build.build_graph(ws, subject_id, kind, index=index, now=now),
                )
            )
    extra_notices: list[str] = []
    if graph_build.CROSS_SUBJECT_KIND in kinds and ws.list_subjects():
        # The cross graph always spans every subject, whatever --subject said: its whole purpose
        # is the edges that leave a subject.
        cross = graph_build.build_graph(
            ws,
            None,
            graph_build.CROSS_SUBJECT_KIND,
            index=index,
            now=now,
        )
        jobs.append((None, out_base / "_cross", cross))
        if len(cross["subject_ids"]) < 2:
            extra_notices.append(
                "cross_subject was drawn with a single subject, so there is nothing to cross "
                "yet. It becomes useful once a second subject has concepts."
            )

    template = ws.graph_templates / "viewer.html.tmpl"
    results: list[tuple[str | None, graph_render.RenderResult]] = []
    for subject_id, directory, graph in jobs:
        result = graph_render.write_artifacts(
            graph,
            directory,
            formats,
            require_renderer=args.require_renderer,
            dry_run=args.dry_run,
            template_path=template,
        )
        results.append((subject_id, result))
        if args.dry_run:
            continue
        relative_paths = [
            ws.rel(path) for path in result.written if _inside(ws.root, path)
        ]
        events_mod.append_event(
            ws,
            type="graph_generated",
            session_id=f"{now[:10]}-graphs",
            subject_id=subject_id,
            actor="tool",
            payload={
                "kind": result.kind,
                "relative_paths": relative_paths,
                "node_count": len(graph.get("nodes") or []),
                "edge_count": len(graph.get("edges") or []),
                "formats": result.formats,
                "svg_skipped": result.svg_skipped,
                "renderer": result.renderer,
            },
        )

    notices: list[str] = []
    for _subject_id, result in results:
        for notice in result.notices:
            if notice not in notices:
                notices.append(notice)
    notices.extend(notice for notice in extra_notices if notice not in notices)

    if args.json:
        print(
            dumps_json(
                {
                    "tool": TOOL_ID,
                    "dry_run": bool(args.dry_run),
                    "out_dir": str(out_base),
                    "kinds": kinds,
                    "formats": formats,
                    "subjects": subjects,
                    "artefacts": [result.to_dict() for _subject_id, result in results],
                    "notices": notices,
                }
            ),
            end="",
        )
        return EXIT_OK

    if not args.quiet:
        if not jobs:
            print(
                "no subjects yet, so there is nothing to draw. Create one with "
                '`init-subject --id <id> --name "<name>"`.'
            )
        for subject_id, result in results:
            where = ws.rel(result.out_dir) if _inside(ws.root, result.out_dir) else result.out_dir
            paths = result.planned if args.dry_run else result.written
            verb = "would write" if args.dry_run else "wrote"
            print(f"{result.kind} ({subject_id or '_cross'}): {verb} {len(paths)} file(s)")
            for path in paths:
                print(f"  {path.name}  in {where}/")
        if args.dry_run and jobs:
            print("\n--dry-run: nothing was written.")
        for notice in notices:
            print(notice)
        if jobs and not args.dry_run:
            print(
                "\nGraphs are derived: deleting 03_MEMORY/graphs/ is safe, and regenerating from "
                "unchanged memory reproduces byte-identical .mmd files."
            )
    return EXIT_OK


def _inside(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` is the workspace root or beneath it."""
    try:
        Path(candidate).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


# ====================================================================================== parsing


def _global_options() -> argparse.ArgumentParser:
    """Options accepted both before and after the subcommand.

    ``argparse.SUPPRESS`` defaults matter: without them the subparser copy would overwrite a value
    given before the subcommand with its own default.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--workspace",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="the LearningOS folder to act on. Default: LEARNINGOS_HOME, else the nearest parent "
        f"directory containing {MARKER_FILE}.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="machine-readable output on stdout instead of the human report.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print only what matters: errors, and nothing on success.",
    )
    return parser


@dataclass
class Command:
    name: str
    handler: Callable[[argparse.Namespace, Workspace], int]
    help: str
    description: str
    epilog: str
    needs_workspace: bool = True
    add_arguments: Callable[[argparse.ArgumentParser], None] | None = None


def _init_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and write nothing at all.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-copy the shipped instruction files, schemas, templates and CLI over existing "
        "ones. Never touches anything under 02_KNOWLEDGE or 03_MEMORY.",
    )


def _init_subject_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--id",
        required=True,
        metavar="SUBJECT_ID",
        help="lowercase slug matching ^[a-z0-9][a-z0-9_-]*$. Becomes the directory name and the "
        "prefix of every concept id in the subject, so keep it short and stable.",
    )
    parser.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help='the subject\'s human-readable name, e.g. "Thermodynamics".',
    )
    parser.add_argument(
        "--exam-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="exam date, if there is one. Drives prioritisation, never deadlines you did not set.",
    )
    parser.add_argument(
        "--description",
        metavar="TEXT",
        default=None,
        help="one line about the course, stored in subject_config.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be created and write nothing.",
    )


def _index_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        metavar="SUBJECT_ID",
        default=None,
        help="index one subject's source_materials/.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="index every subject.",
    )
    parser.add_argument(
        "--inbox",
        action="store_true",
        help="list what is waiting in 02_KNOWLEDGE/inbox/.",
    )
    parser.add_argument(
        "--assign-to",
        metavar="SUBJECT_ID",
        default=None,
        dest="assign_to",
        help="with --inbox and --copy, copy the inbox files into this subject and index it.",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="actually make the copies. Without it, --assign-to only prints the plan. Inbox "
        "originals are never moved or deleted.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-extract every source even when its hash, size and mtime are unchanged.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 3 when any source needs OCR or produced no text. The index is still written; "
        "this is for scripting a 'my material is all readable' check.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be extracted and write nothing.",
    )


def _update_graphs_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        metavar="SUBJECT_ID",
        default=None,
        help="draw one subject. Default: every subject.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="draw every subject (the default when --subject is absent).",
    )
    parser.add_argument(
        "--kinds",
        metavar="A,B,C",
        default=None,
        help="comma-separated subset of: "
        + ", ".join(graph_build.KINDS)
        + ". Default: all. cross_subject always spans every subject, whatever --subject says: "
        "the edges that leave a subject are the point of it.",
    )
    parser.add_argument(
        "--format",
        metavar="A,B,C",
        default=None,
        help="comma-separated subset of: "
        + ", ".join(graph_render.FORMATS)
        + ". Default: all. json is always written — it is the artefact the rest derives from.",
    )
    parser.add_argument(
        "--out-dir",
        metavar="PATH",
        default=None,
        dest="out_dir",
        help="write somewhere other than 03_MEMORY/graphs/ (one subdirectory per subject, plus "
        "_cross). Useful for a throwaway look without touching memory.",
    )
    parser.add_argument(
        "--require-renderer",
        action="store_true",
        dest="require_renderer",
        help="treat a missing mermaid-cli (mmdc) as a failure (exit 6) instead of printing the "
        "skip notice.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build every graph, report what would be written, and write nothing.",
    )


def _validate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        metavar="SUBJECT_ID",
        default=None,
        help="restrict the per-subject checks to one subject. Workspace-wide checks still run.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also cross-check with the `jsonschema` package when it is installed. Its absence "
        "is never an error: the bundled validator is the primary path.",
    )


def _resume_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        metavar="SUBJECT_ID",
        default=None,
        help="which subject to resume. Default: current_subject_id from "
        "03_MEMORY/global/workspace_state.json.",
    )


def _checkpoint_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--subject", required=True, metavar="SUBJECT_ID", help="the subject.")
    parser.add_argument(
        "--session-id",
        required=True,
        metavar="SESSION_ID",
        help="the session this checkpoint belongs to, e.g. 2026-02-11-algo-01. Writing a second "
        "checkpoint for the same session replaces it: it is the current resume point, and the "
        "history lives in the event log.",
    )
    parser.add_argument(
        "--note",
        required=True,
        metavar="TEXT",
        help="where_we_stopped, written for a reader with no memory of the conversation. "
        '"Continue Fourier" is useless; "stopped after deriving a0 with hints" is a handover.',
    )
    parser.add_argument(
        "--next-action",
        required=True,
        metavar="TEXT",
        help="the exact first activity of the next session.",
    )
    parser.add_argument(
        "--concept",
        action="append",
        metavar="CONCEPT_ID",
        default=[],
        help="a concept that was in flight. Repeatable.",
    )
    parser.add_argument(
        "--open-question",
        action="append",
        metavar="TEXT",
        default=[],
        help="an unanswered question to carry forward. Repeatable.",
    )
    parser.add_argument(
        "--trigger",
        metavar="NAME",
        default=None,
        help="which checkpoint trigger fired: status_change, time, learner_stopping, "
        "pre_risky_op, misconception, session_end.",
    )
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help="which agent was driving, for provenance across tools.",
    )


def _search_memory_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", metavar="QUERY", help="text to look for, case-insensitive.")
    parser.add_argument(
        "--subject", metavar="SUBJECT_ID", default=None, help="restrict to one subject."
    )
    parser.add_argument(
        "--type",
        metavar="EVENT_TYPE",
        default=None,
        help="restrict to one event type (e.g. answer_evaluated). Concept and misconception "
        "records have no event type, so this excludes them.",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default=None, help="ignore anything older than this date."
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N", help="maximum hits to print. Default 20."
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="rebuild the optional SQLite FTS5 cache at 03_MEMORY/global/search_index.sqlite3 "
        "before searching. The cache only ever speeds the same scan up; results are identical "
        "with or without it, and it is skipped when SQLite has no FTS5.",
    )


def _session_report_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject", metavar="SUBJECT_ID", default=None, help="restrict to one subject."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--session-id", metavar="SESSION_ID", default=None, help="the session to report on."
    )
    group.add_argument(
        "--last",
        action="store_true",
        help="report on the session with the newest event. This is the default when neither flag "
        "is given.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="write somewhere other than 03_MEMORY/session_logs/<date>-<subject>-<session>.md.",
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="print the markdown and write no file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing report for this session. Without it, the new report is written "
        "beside the old one with a -02 suffix.",
    )


def _backup_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--note", metavar="TEXT", default=None, help="why you took it, stored in the manifest."
    )
    parser.add_argument(
        "--include-sources",
        action="store_true",
        help="also archive 02_KNOWLEDGE/subjects/*/source_materials/. Originals are copied, never "
        "moved.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the file list and byte total, and write nothing.",
    )


def _restore_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="ZIP_OR_ID",
        help="a backup id (memory-20260211T091400Z) or a path to a .zip. Its .manifest.json must "
        "sit beside it: every sha256 is verified before anything is written.",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="PREFIX",
        default=[],
        help="restore only entries under a workspace-relative prefix, e.g. --only 03_MEMORY. "
        "Repeatable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify the archive and print the plan. Writes nothing, takes no safety backup.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="required to overwrite existing files. A pre-restore safety backup is taken first.",
    )


def _reconcile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject", metavar="SUBJECT_ID", default=None, help="restrict to one subject."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report only, even with --apply. This is already the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="re-derive the drifted fields from the stored evidence, after taking a backup. The "
        "only writing path. It never fabricates evidence.",
    )


COMMANDS: tuple[Command, ...] = (
    Command(name="start", handler=onboarding_mod.handler, add_arguments=onboarding_mod.add_arguments,
            help="set up any subject or goal, with or without source documents", description="Goal-independent onboarding for learning, exams, interviews and professional study. Setup records no learning success.",
            epilog="example:\n  python learningos.py start --subject Biology --topic Genetics --goal \"Explain and apply inheritance\"\n"),
    Command(name="continue", handler=onboarding_mod.continue_handler, add_arguments=onboarding_mod.continue_args,
            help="recover saved steps and continue a subject from its handover", description="Complete interrupted, validated learning commits and load the exact next activity. Divergent or corrupt state is never overwritten.",
            epilog="example:\n  python learningos.py continue --subject computer-networks\n"),
    Command(name="guide", handler=onboarding_mod.guide_handler,
            help="show the simple student workflow and what gets saved", description="Four everyday actions: start, study normally, save, continue. Technical commands are for the tutor.",
            epilog="example:\n  python learningos.py guide\n"),
    Command(name="atlas", handler=atlas_mod.handler,
            help="build your offline Learning Atlas", description="Generate a searchable, interactive read-only snapshot of your learning memory. No hosting or network requests.",
            epilog="example:\n  python learningos.py atlas\n"),
    Command(name="menu", handler=launcher_mod.menu,
            help="open the beginner-friendly numbered study menu", description="Set up exams, add material, practise and review results without remembering commands.",
            epilog="example:\n  python learningos.py menu\n"),
    Command(name="today", handler=study_mod.today_handler, add_arguments=study_mod.today_args,
            help="plan your next study session within your time budget", description="Explain the next actions using due reviews, prerequisites and recorded errors. Saving a plan records no learning success.",
            epilog="example:\n  python learningos.py today --minutes 45\n"),
    Command(name="exam", handler=exam_mod.handler, add_arguments=exam_mod.add_arguments,
            help="set up exams, import verified questions, run and review timed tests", description="Versioned local exam preparation with independent event evidence. No rank predictions or automatic mastery promotion.",
            epilog="example:\n  python learningos.py exam list\n"),
    Command(name="study", handler=study_mod.handler, add_arguments=study_mod.add_arguments,
            help="validated tutor operations for mapping, sessions and learning evidence", description="Start/resume a subject, map source-backed concepts, record judged attempts, and save a complete session handover.",
            epilog="example:\n  python learningos.py study start --subject computer-networks\n"),
    Command(
        name="init-workspace",
        handler=cmd_init_workspace,
        help="create or complete the workspace scaffold (idempotent)",
        description=(
            "Create the three principal directories, the root entrypoints, the global memory "
            "files, the empty event log and the _template copies. Existing files are listed as "
            "skipped and never rewritten, so it is safe to re-run at any time — including into a "
            "folder that already holds learning history."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py init-workspace\n"
            "  python learningos.py init-workspace --workspace D:\\Study\\LearningOS\n"
            "  python learningos.py init-workspace --dry-run\n"
        ),
        needs_workspace=False,
        add_arguments=_init_workspace_args,
    ),
    Command(
        name="init-subject",
        handler=cmd_init_subject,
        help="create both halves of a new subject from the templates (idempotent)",
        description=(
            "Create 02_KNOWLEDGE/subjects/<id>/ and 03_MEMORY/subjects/<id>/ from the shipped "
            "templates, register the subject in workspace_state.json and learner_index.md, and "
            "append one subject_initialized event. It never overwrites an existing file, never "
            "reads or copies another subject's mastery, and a second run is a no-op that exits 0. "
            "If a surviving file of the subject is corrupt it exits 3 and writes nothing."
        ),
        epilog=(
            "examples:\n"
            '  python learningos.py init-subject --id thermo --name "Thermodynamics" '
            "--exam-date 2026-05-18\n"
            '  python learningos.py init-subject --id algo --name "Algorithms" --dry-run\n'
            "\nexit codes: 0 created or already present, 2 bad id or date, 3 existing subject is "
            "corrupt\n"
        ),
        add_arguments=_init_subject_args,
    ),
    Command(
        name="index",
        handler=cmd_index,
        help="hash and extract text from a subject's source documents",
        description=(
            "Walks 02_KNOWLEDGE/subjects/<id>/source_materials/, records one entry per document "
            "in source_index.json (stable source_id, sha256, size, mtime, pages, warnings) and "
            "writes the derived text to extracted_text/<source_id>.txt with a provenance header "
            "and `--- page N ---` markers. Originals are opened read-only and never modified. A "
            "source is re-extracted only when its hash, size or mtime changed, when its derived "
            "text is missing, or with --force. A scanned PDF is recorded as an un-extracted "
            "source needing OCR, with its hash intact — never as an empty document. PDF text "
            "needs the optional `pypdf` package; without it, PDFs are still indexed and the "
            "install hint is printed."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py index --subject thermo\n"
            "  python learningos.py index --all --force\n"
            "  python learningos.py index --inbox\n"
            "  python learningos.py index --inbox --assign-to thermo --copy\n"
            "  python learningos.py index --subject thermo --strict\n"
            "\nexit codes: 0 indexed, 2 contradictory arguments, 3 --strict and something needs "
            "OCR, 4 unknown subject\n"
        ),
        add_arguments=_index_args,
    ),
    Command(
        name="update-graphs",
        handler=cmd_update_graphs,
        help="regenerate the concept, mastery, misconception, cross-subject and progress graphs",
        description=(
            "Builds canonical graph JSON from the memory files, renders deterministic Mermaid "
            "text from it, and writes a self-contained HTML viewer — into "
            "03_MEMORY/graphs/<subject>/ and 03_MEMORY/graphs/_cross/. Every node and edge id is "
            "resolved first: a dangling endpoint exits 3 before a single file is written. An "
            "`.svg` is produced only when mermaid-cli (mmdc) is already on PATH; otherwise the "
            "skip notice is printed and the run still exits 0. Graphs are derived state — this "
            "command never writes a source-of-truth memory file, and deleting the graphs "
            "directory is safe."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py update-graphs --all\n"
            "  python learningos.py update-graphs --subject thermo "
            "--kinds concept_prerequisite,learner_mastery\n"
            "  python learningos.py update-graphs --all --format mmd,html --dry-run\n"
            "  python learningos.py update-graphs --subject thermo --require-renderer\n"
            "\nexit codes: 0 written (with or without an SVG), 2 unknown kind or format, 3 a "
            "dangling reference or a corrupt input file (nothing written), 4 unknown subject, "
            "6 --require-renderer with no mmdc on PATH\n"
        ),
        add_arguments=_update_graphs_args,
    ),
    Command(
        name="validate",
        handler=cmd_validate,
        help="check structure, schemas, references and the event log",
        description=(
            "The install's health check. Reports structure problems, schema violations, dangling "
            "concept and misconception references, unreadable event-log lines (with line "
            "numbers), stale derived state and any absolute path stored in a memory file. "
            "Findings carry a file, a JSON pointer, a reason and a remedy. Nothing is ever "
            "repaired automatically."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py validate\n"
            "  python learningos.py validate --subject algo --strict\n"
            "  python learningos.py validate --json\n"
            "\nexit codes: 0 no errors (warnings allowed), 3 at least one error\n"
        ),
        add_arguments=_validate_args,
    ),
    Command(
        name="status",
        handler=cmd_status,
        help="one-screen health and progress summary",
        description=(
            "Subjects with concept counts by status and due reviews, the size and health of the "
            "event log, the last session, the last backup, the current validate counts, and which "
            "optional capabilities (PDF extraction, SVG rendering, strict schema cross-checks) "
            "are available in this environment."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py status\n"
            "  python learningos.py status --json\n"
        ),
    ),
    Command(
        name="resume",
        handler=cmd_resume,
        help="brief from the newest checkpoint, the due queue and the last events",
        description=(
            "The raw material for the start-of-session briefing: where the last session stopped, "
            "the concepts that were in flight and their current status, the exact next action, "
            "how many reviews are due, the unanswered questions, the plan in next_session.md and "
            "the last five events. A checkpoint whose state_fingerprints no longer match the "
            "files is reported as a warning and still exits 0 — memory legitimately moves on "
            "after a checkpoint was taken."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py resume\n"
            "  python learningos.py resume --subject algo\n"
            "  python learningos.py resume --json\n"
            "\nexit codes: 0 briefing printed, 4 no subject or no checkpoint (the message names "
            "what to do instead)\n"
        ),
        add_arguments=_resume_args,
    ),
    Command(
        name="checkpoint",
        handler=cmd_checkpoint,
        help="write a resume point for the current session",
        description=(
            "Write 03_MEMORY/checkpoints/<subject>/<session>.json and append a checkpoint_written "
            "event. Both --note (where we stopped) and --next-action are required and must be "
            "concrete: a checkpoint exists so that a reader with no memory of the conversation "
            "can pick the thread up. A sha256 of each memory file is stored alongside, so a later "
            "`resume` can tell whether the state it is briefing from still matches."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py checkpoint --subject algo --session-id 2026-02-11-algo-01 \\\n"
            '      --note "stopped mid-derivation of the master theorem, 2 hints used" \\\n'
            '      --next-action "re-derive case 2 unaided, no notes" --concept algo:big-o\n'
            "\nexit codes: 0 written, 3 an empty note or next action, 4 unknown subject\n"
        ),
        add_arguments=_checkpoint_args,
    ),
    Command(
        name="search-memory",
        handler=cmd_search_memory,
        help="search the event log, concept records and misconceptions",
        description=(
            "Case-insensitive search over the learner's own history: event lines (type, concept "
            "ids, payload notes), concept records (name, recurring mistakes, next action, "
            "interventions) and misconception records (description, learner statement, detection "
            "signals, remediation). A streaming scan by default, with an optional SQLite FTS5 "
            "cache behind --reindex. No embeddings, no vector store, no network call: nothing "
            "leaves this machine."
        ),
        epilog=(
            "examples:\n"
            '  python learningos.py search-memory "a0 term" --subject math2\n'
            '  python learningos.py search-memory orthogonality --type answer_evaluated '
            "--since 2026-02-01\n"
            '  python learningos.py search-memory "drops the sign" --json --limit 5\n'
            "\nexit codes: 0 always when the query ran (including zero hits), 2 a malformed "
            "--since or --type\n"
        ),
        add_arguments=_search_memory_args,
    ),
    Command(
        name="session-report",
        handler=cmd_session_report,
        help="write the markdown log for a session, derived from the events",
        description=(
            "Derives duration, concepts touched, independent versus hinted counts, hints per "
            "concept, misconceptions raised and resolved, mastery deltas, reviews scheduled and "
            "the next actions from the event log, and writes "
            "03_MEMORY/session_logs/<date>-<subject>-<session>.md atomically. Every number comes "
            "from recorded evidence, so the report cannot claim an independent success no event "
            "supports. An existing report is never overwritten without --force: the new one is "
            "written beside it with a -02 suffix."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py session-report --last\n"
            "  python learningos.py session-report --session-id 2026-02-11-algo-01 --force\n"
            "  python learningos.py session-report --last --stdout-only\n"
            "\nexit codes: 0 written, 4 unknown session id or no sessions recorded yet\n"
        ),
        add_arguments=_session_report_args,
    ),
    Command(
        name="backup",
        handler=cmd_backup,
        help="archive 03_MEMORY and the subject descriptions to a verifiable zip",
        description=(
            "Writes 03_MEMORY/backups/<backup_id>.zip plus a <backup_id>.manifest.json holding a "
            "sha256 for every file, which is what lets `restore` prove the archive is intact "
            "before it writes anything. Previous backups and the regenerable search cache are "
            "not archived. Nothing is ever moved or deleted, and nothing is uploaded anywhere."
        ),
        epilog=(
            "examples:\n"
            '  python learningos.py backup --note "before hand-editing concept_mastery"\n'
            "  python learningos.py backup --include-sources\n"
            "  python learningos.py backup --dry-run\n"
        ),
        add_arguments=_backup_args,
    ),
    Command(
        name="restore",
        handler=cmd_restore,
        help="restore memory from a backup, after verifying every hash",
        description=(
            "Verifies every sha256 in the manifest against the archive first: one mismatch, one "
            "missing entry, or one entry with an absolute or '..' path means exit 3 and an "
            "untouched workspace. Overwriting existing files requires --force, which takes a "
            "pre-restore safety backup before replacing anything, and writes each file via a "
            "temp file in its destination directory. --dry-run writes nothing at all."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py restore --from memory-20260211T091400Z --dry-run\n"
            "  python learningos.py restore --from memory-20260211T091400Z --force\n"
            "  python learningos.py restore --from D:\\copies\\memory-20260211T091400Z.zip "
            "--only 03_MEMORY --force\n"
            "\nexit codes: 0 restored, 3 verification failed (nothing written), 4 no such "
            "backup, 5 refused because files exist and --force was not given\n"
        ),
        add_arguments=_restore_args,
    ),
    Command(
        name="reconcile",
        handler=cmd_reconcile,
        help="compare the event log with the snapshots and re-derive drifted fields",
        description=(
            "Replays the append-only evidence against the per-subject snapshots and reports every "
            "disagreement: a stale snapshot, a status or counter that the stored evidence does "
            "not support, an attempt in the log with no evidence entry, a missing or orphaned "
            "revision-queue item. Report-only by default. --apply re-derives status, the mastery "
            "estimate, the counters and the queue after taking a backup; it never fabricates "
            "evidence, so lost attempts are a `restore` job, not a recompute."
        ),
        epilog=(
            "examples:\n"
            "  python learningos.py reconcile\n"
            "  python learningos.py reconcile --subject algo --dry-run\n"
            "  python learningos.py reconcile --subject algo --apply\n"
            "\nexit codes: 0 report printed (with or without drift), 3 a file will not parse or "
            "validate\n"
        ),
        add_arguments=_reconcile_args,
    ),
)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with one sub-parser per command."""
    globals_parser = _global_options()
    parser = argparse.ArgumentParser(
        prog="learningos.py",
        parents=[globals_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "LearningOS — a local, file-based learning system. The files in the workspace are the "
            "source of truth; this CLI is how they are created, checked and written safely."
        ),
        epilog=(
            "exit codes:\n"
            "  0 success                3 validation or data failure (nothing written)\n"
            "  1 unexpected error       4 not found\n"
            "  2 usage error            5 refused a destructive operation (use --force)\n"
            "                           6 required optional dependency missing\n"
            "\nquick start:\n"
            "  python learningos.py init-workspace\n"
            '  python learningos.py init-subject --id algo --name "Algorithms"\n'
            "  python learningos.py validate\n"
            "  python learningos.py status\n"
            "\nThe teaching behaviour itself lives in 01_AGENTS_AND_SKILLS/core/ and "
            "01_AGENTS_AND_SKILLS/skills/ and is read by the agent, not by this script.\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
        help="print the tool version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for command in COMMANDS:
        sub = subparsers.add_parser(
            command.name,
            parents=[globals_parser],
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help=command.help,
            description=command.description,
            epilog=command.epilog,
        )
        if command.add_arguments:
            command.add_arguments(sub)
        # Nested beginner/tutor commands accept global options at the end too.
        for action in sub._actions:
            if isinstance(action, argparse._SubParsersAction):
                for nested in action.choices.values():
                    nested.add_argument("--workspace", default=argparse.SUPPRESS, metavar="PATH")
                    nested.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
                    nested.add_argument("--quiet", "-q", action="store_true", default=argparse.SUPPRESS)
        sub.set_defaults(_command=command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the exit code instead of calling ``sys.exit``, so tests can drive it."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # --help and --version exit 0; argparse usage errors exit 2
        code = exc.code
        if code in (0, None):
            return EXIT_OK
        return EXIT_USAGE

    args.workspace = getattr(args, "workspace", None)
    args.json = bool(getattr(args, "json", False))
    args.quiet = bool(getattr(args, "quiet", False))
    command: Command = args._command

    try:
        ws = resolve_workspace(args.workspace, require_marker=command.needs_workspace)
        before_log = ws.event_log.stat().st_size if ws.event_log.exists() else 0
        result = command.handler(args, ws)
        after_log = ws.event_log.stat().st_size if ws.event_log.exists() else 0
        if result == EXIT_OK and after_log != before_log and command.name not in ("atlas", "menu", "restore"):
            from atlas_sync import refresh
            sync = refresh(ws)
            if not args.json and not args.quiet and command.name not in ("exam", "study", "start", "continue", "today"):
                print("\n" + sync["message"])
            elif not sync["atlas_refreshed"]:
                print(sync["message"], file=sys.stderr)
        if result == EXIT_OK and command.name == "restore" and after_log != before_log and not args.json and not args.quiet:
            print("Restored file bytes were preserved. Reopen LearningOS or use /atlas to rebuild the viewer from restored memory.")
        return result
    except LearningOSError as exc:
        print(exc.format_human(), file=sys.stderr)
        if "ws" in locals() and "before_log" in locals() and ws.event_log.exists() and ws.event_log.stat().st_size != before_log:
            print("  Evidence was journalled before this failure. Use /continue or study recover; preserve the same operation id when retrying.", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("\ninterrupted; nothing further was written", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # pragma: no cover - genuine bug path
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "  remedy: this is a bug. Re-run with --json for the raw report, check "
            "`python learningos.py validate`, and note that no file is written unless a command "
            "says so.",
            file=sys.stderr,
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
