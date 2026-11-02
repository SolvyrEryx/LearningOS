"""Reading and writing LearningOS state snapshots.

Every write in this module follows the same five steps, in this order:

1. if the target already exists, read it and validate it — a corrupt file on disk means the write
   is **refused**, because overwriting corruption destroys the evidence of what went wrong;
2. bump ``updated_at``;
3. validate the new object against its schema;
4. atomic write (temp file in the same directory, flush + fsync, ``os.replace``);
5. return the written object.

Step 1 is the part people skip. It is what makes "LearningOS never silently repairs a file" true
in code rather than only in the documentation: a snapshot that will not parse is reported with its
path, JSON pointer, reason and remedy, and left byte-identical.

Cross-subject isolation is enforced here too: a concept record whose id prefix is not the owning
directory name cannot be written, so one subject's mastery can never leak into another's file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from common import (
    CONCEPT_ID_RE,
    MISCONCEPTION_ID_RE,
    SCHEMA_VERSION,
    TOOL_ID,
    Finding,
    LearningOSDataError,
    LearningOSNotFoundError,
    Workspace,
    atomic_write_json,
    escape_pointer_token,
    read_json,
    read_json_optional,
    sha256_file,
    utc_now_iso,
)
from schema import SchemaRegistry, registry_for

# ------------------------------------------------------------------------------- file definitions

#: Memory-side files that must exist for a subject to be usable.
REQUIRED_SUBJECT_MEMORY_FILES: dict[str, str] = {
    "subject_state.json": "subject_state",
    "concept_mastery.json": "concept_mastery",
}

#: Memory-side files that are rebuildable, so a missing one is a warning rather than an error.
DERIVED_SUBJECT_MEMORY_FILES: dict[str, str] = {
    "concept_graph.json": "concept_graph",
    "misconceptions.json": "misconceptions",
    "revision_queue.json": "revision_queue",
}

SUBJECT_MEMORY_FILES: dict[str, str] = {
    **REQUIRED_SUBJECT_MEMORY_FILES,
    **DERIVED_SUBJECT_MEMORY_FILES,
}

#: Knowledge-side JSON files and their schemas.
SUBJECT_KNOWLEDGE_FILES: dict[str, str] = {
    "subject_config.json": "subject_config",
    "concept_catalog.json": "concept_catalog",
    "source_index.json": "source_index",
}

#: Global files and their schemas.
GLOBAL_FILES: dict[str, str] = {
    "workspace_state.json": "workspace_state",
    "learning_preferences.json": "learning_preferences",
}

CONCEPT_STATUSES = (
    "unassessed",
    "developing",
    "practising",
    "provisionally_mastered",
    "needs_review",
)

DEFAULT_LADDER_DAYS = [1, 3, 7, 16, 35]


def registry(ws: Workspace) -> SchemaRegistry:
    """The schema registry for this workspace (cached per schema directory)."""
    return registry_for(ws.schemas)


# ----------------------------------------------------------------------------- generic read/write


def load_json_file(
    ws: Workspace,
    path: Path,
    schema_name: str,
    *,
    required: bool = True,
    strict: bool = False,
    what: str | None = None,
    remedy: str | None = None,
) -> dict[str, Any] | None:
    """Read and validate one state file.

    Returns ``None`` when the file is absent and ``required`` is false. Raises
    :class:`LearningOSDataError` (exit 3) when the file is missing-but-required, unparseable or
    schema-invalid.
    """
    if not path.exists():
        if not required:
            return None
        raise LearningOSDataError(
            f"required file is missing: {ws.rel(path)}",
            path=ws.rel(path),
            remedy=remedy
            or "Run `init-subject --id <id> --name \"<name>\"` (idempotent, it will not overwrite "
            "surviving files) or `restore --from <backup>`.",
            code="missing_file",
        )
    data = read_json(path, what=what or schema_name, workspace=ws)
    registry(ws).validate_or_raise(
        schema_name,
        data,
        path=ws.rel(path),
        strict=strict,
        remedy=remedy,
    )
    return data


def save_json_file(
    ws: Workspace,
    path: Path,
    data: dict[str, Any],
    schema_name: str,
    *,
    bump: bool = True,
    strict: bool = False,
    refuse_if_corrupt: bool = True,
) -> dict[str, Any]:
    """Validate then atomically write one state file.

    ``refuse_if_corrupt`` re-reads the file already on disk and aborts when it will not parse or
    will not validate. That is the point of this function: the only way a bad file gets replaced
    is through an explicit ``restore``, never through a routine write that happens to run next.
    """
    if refuse_if_corrupt and path.exists():
        existing = read_json(path, what=schema_name, workspace=ws)
        registry(ws).validate_or_raise(
            schema_name,
            existing,
            path=ws.rel(path),
            remedy="The file already on disk is invalid, so this write was refused rather than "
            "overwriting the evidence. Fix it by hand or use `restore --from <backup>`.",
        )
    if bump:
        data["updated_at"] = utc_now_iso()
    registry(ws).validate_or_raise(schema_name, data, path=ws.rel(path), strict=strict)
    atomic_write_json(path, data)
    return data


# ---------------------------------------------------------------------------------- global state


def load_workspace_state(ws: Workspace, *, required: bool = True) -> dict[str, Any] | None:
    return load_json_file(
        ws,
        ws.workspace_state_file,
        "workspace_state",
        required=required,
        remedy="Run `init-workspace` to recreate 03_MEMORY/global/workspace_state.json.",
    )


def save_workspace_state(ws: Workspace, data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("tool_version", TOOL_ID)
    data["tool_version"] = TOOL_ID
    return save_json_file(ws, ws.workspace_state_file, data, "workspace_state")


def new_workspace_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": utc_now_iso(),
        "current_subject_id": None,
        "last_session_id": None,
        "last_session_at": None,
        "last_backup_id": None,
        "tool_version": TOOL_ID,
        "subjects": [],
    }


def register_subject(
    ws: Workspace,
    subject_id: str,
    name: str,
    *,
    exam_date: str | None = None,
    created_at: str | None = None,
    make_current: bool = True,
) -> dict[str, Any]:
    """Add a subject to ``workspace_state.json`` if it is not already registered.

    Idempotent: a second call with the same id updates the name/exam date in place rather than
    appending a duplicate entry, and never touches any other subject.
    """
    state = load_workspace_state(ws, required=False) or new_workspace_state()
    subjects = state.setdefault("subjects", [])
    entry = next((item for item in subjects if item.get("subject_id") == subject_id), None)
    if entry is None:
        entry = {
            "subject_id": subject_id,
            "name": name,
            "created_at": created_at or utc_now_iso(),
            "exam_date": exam_date,
            "last_session_at": None,
            "knowledge_path": ws.rel(ws.knowledge_subject(subject_id)),
            "memory_path": ws.rel(ws.memory_subject(subject_id)),
        }
        subjects.append(entry)
    else:
        entry["name"] = name
        entry["exam_date"] = exam_date
        entry.setdefault("knowledge_path", ws.rel(ws.knowledge_subject(subject_id)))
        entry.setdefault("memory_path", ws.rel(ws.memory_subject(subject_id)))
    subjects.sort(key=lambda item: str(item.get("subject_id")))
    if make_current and not state.get("current_subject_id"):
        state["current_subject_id"] = subject_id
    return save_workspace_state(ws, state)


def load_learning_preferences(ws: Workspace) -> dict[str, Any] | None:
    return load_json_file(
        ws,
        ws.learning_preferences_file,
        "learning_preferences",
        required=False,
    )


# --------------------------------------------------------------------------------- subject state


def subject_exists(ws: Workspace, subject_id: str) -> bool:
    return ws.memory_subject(subject_id).is_dir() or ws.knowledge_subject(subject_id).is_dir()


def require_subject(ws: Workspace, subject_id: str) -> None:
    """Raise exit-4 when a subject was asked for but does not exist."""
    if not subject_exists(ws, subject_id):
        known = ws.list_subjects()
        raise LearningOSNotFoundError(
            f"unknown subject {subject_id!r}",
            remedy=(
                "Known subjects: " + ", ".join(known)
                if known
                else 'Create it with `init-subject --id <id> --name "<name>"`.'
            ),
        )


def subject_state_path(ws: Workspace, subject_id: str) -> Path:
    return ws.memory_subject(subject_id) / "subject_state.json"


def concept_mastery_path(ws: Workspace, subject_id: str) -> Path:
    return ws.memory_subject(subject_id) / "concept_mastery.json"


def concept_graph_path(ws: Workspace, subject_id: str) -> Path:
    return ws.memory_subject(subject_id) / "concept_graph.json"


def misconceptions_path(ws: Workspace, subject_id: str) -> Path:
    return ws.memory_subject(subject_id) / "misconceptions.json"


def revision_queue_path(ws: Workspace, subject_id: str) -> Path:
    return ws.memory_subject(subject_id) / "revision_queue.json"


def load_subject_state(ws: Workspace, subject_id: str, *, required: bool = True):
    return load_json_file(ws, subject_state_path(ws, subject_id), "subject_state", required=required)


def save_subject_state(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    _assert_subject_match(data, subject_id, ws.rel(subject_state_path(ws, subject_id)))
    return save_json_file(ws, subject_state_path(ws, subject_id), data, "subject_state")


def load_concept_graph(ws: Workspace, subject_id: str, *, required: bool = False):
    return load_json_file(ws, concept_graph_path(ws, subject_id), "concept_graph", required=required)


def save_concept_graph(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    _assert_subject_match(data, subject_id, ws.rel(concept_graph_path(ws, subject_id)))
    return save_json_file(ws, concept_graph_path(ws, subject_id), data, "concept_graph")


def load_misconceptions(ws: Workspace, subject_id: str, *, required: bool = False):
    return load_json_file(ws, misconceptions_path(ws, subject_id), "misconceptions", required=required)


def save_misconceptions(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    _assert_subject_match(data, subject_id, ws.rel(misconceptions_path(ws, subject_id)))
    return save_json_file(ws, misconceptions_path(ws, subject_id), data, "misconceptions")


def load_revision_queue(ws: Workspace, subject_id: str, *, required: bool = False):
    return load_json_file(ws, revision_queue_path(ws, subject_id), "revision_queue", required=required)


def save_revision_queue(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    _assert_subject_match(data, subject_id, ws.rel(revision_queue_path(ws, subject_id)))
    return save_json_file(ws, revision_queue_path(ws, subject_id), data, "revision_queue")


def _assert_subject_match(data: dict[str, Any], subject_id: str, path: str) -> None:
    found = data.get("subject_id")
    if found != subject_id:
        raise LearningOSDataError(
            f"refusing to write {path}: its subject_id is {found!r} but the owning directory is "
            f"{subject_id!r}",
            path=path,
            pointer="/subject_id",
            remedy="Subjects are isolated. Write this object to its own subject's directory, or "
            "correct subject_id — never mix two subjects' state in one file.",
            code="subject_mismatch",
        )


# -------------------------------------------------------------------------------- concept mastery


def new_concept_mastery(subject_id: str, *, at: str | None = None) -> dict[str, Any]:
    """An empty mastery file. A new subject never inherits concepts from anywhere."""
    return {
        "schema_version": SCHEMA_VERSION,
        "subject_id": subject_id,
        "updated_at": at or utc_now_iso(),
        "concepts": {},
    }


def new_concept_record(
    concept_id: str,
    name: str,
    *,
    subject_id: str | None = None,
    unit: str | None = None,
    prerequisite_ids: Sequence[str] | None = None,
    source_refs: Sequence[dict[str, Any]] | None = None,
    status: str = "unassessed",
    at: str | None = None,
) -> dict[str, Any]:
    """A fully populated concept record at the bottom of the ladder.

    Every optional field is written explicitly (``[]``, ``{}``, ``0``, ``null``) so the file is
    self-describing when a human opens it, and so a reader never has to know the defaults.
    Starting mastery is ``null``, not a guess: that is what the first diagnostic is for.
    """
    now = at or utc_now_iso()
    derived_subject = concept_id.split(":", 1)[0] if ":" in concept_id else ""
    return {
        "schema_version": SCHEMA_VERSION,
        "concept_id": concept_id,
        "name": name,
        "subject_id": subject_id or derived_subject,
        "unit": unit,
        "prerequisite_ids": list(prerequisite_ids or []),
        "source_refs": [dict(ref) for ref in (source_refs or [])],
        "status": status,
        "mastery_estimate": None,
        "confidence_estimate": None,
        "last_attempted_at": None,
        "last_successful_retrieval_at": None,
        "independent_successes": 0,
        "hint_usage": {"total_hints": 0, "hinted_successes": 0, "last_hint_level": None},
        "recurring_mistakes": [],
        "misconception_ids": [],
        "helpful_interventions": [],
        "failed_interventions": [],
        "retention_evidence": [],
        "next_action": None,
        "review_due_at": None,
        "updated_at": now,
    }


def concept_mastery_findings(
    ws: Workspace,
    data: Any,
    *,
    subject_id: str,
    path: str,
    strict: bool = False,
) -> list[Finding]:
    """Validate a mastery file and every record inside it.

    ``concept_mastery.schema.json`` deliberately declares ``concepts`` as a plain object: the
    record shape lives in exactly one place (``concept_record.schema.json``) so the two cannot
    drift apart. The consequence is that the per-record checks — schema conformance, key/id
    agreement, and the ``prefix == subject_id == directory name`` rule that makes cross-subject
    leakage mechanically detectable — happen here.
    """
    findings = registry(ws).validate("concept_mastery", data, path=path, strict=strict)
    if findings:
        return findings
    file_subject = data.get("subject_id")
    if file_subject != subject_id:
        findings.append(
            Finding(
                severity="error",
                code="subject_mismatch",
                message=f"subject_id is {file_subject!r} but the owning directory is {subject_id!r}",
                path=path,
                pointer="/subject_id",
                remedy="Correct subject_id, or move the file to the matching subject directory. "
                "Subjects are isolated by design.",
            )
        )
    concepts = data.get("concepts")
    if not isinstance(concepts, dict):
        findings.append(
            Finding(
                severity="error",
                code="schema",
                message="`concepts` must be an object keyed by concept id",
                path=path,
                pointer="/concepts",
                remedy="Use {} for a subject with no concepts yet.",
            )
        )
        return findings
    for key, record in concepts.items():
        pointer = f"/concepts/{escape_pointer_token(str(key))}"
        if not CONCEPT_ID_RE.match(str(key)):
            findings.append(
                Finding(
                    severity="error",
                    code="concept_id_format",
                    message=f"concept key {key!r} is not a valid concept id (<subject_id>:<slug>)",
                    path=path,
                    pointer=pointer,
                    remedy="Rename the key to <subject_id>:<slug>, lowercase with hyphens.",
                )
            )
            continue
        record_findings = registry(ws).validate(
            "concept_record",
            record,
            path=path,
            pointer_prefix=pointer,
            strict=strict,
        )
        findings.extend(record_findings)
        if record_findings or not isinstance(record, dict):
            continue
        if record.get("concept_id") != key:
            findings.append(
                Finding(
                    severity="error",
                    code="concept_key_mismatch",
                    message=f"record is filed under {key!r} but its concept_id is "
                    f"{record.get('concept_id')!r}",
                    path=path,
                    pointer=f"{pointer}/concept_id",
                    remedy="The map key and concept_id must be the same string.",
                )
            )
        prefix = str(key).split(":", 1)[0]
        if prefix != subject_id:
            findings.append(
                Finding(
                    severity="error",
                    code="cross_subject_record",
                    message=f"concept {key!r} belongs to subject {prefix!r} but is stored in "
                    f"{subject_id!r}'s mastery file",
                    path=path,
                    pointer=pointer,
                    remedy=f"Move this record to 03_MEMORY/subjects/{prefix}/concept_mastery.json, "
                    "or correct the id. One subject's mastery is never stored in another's file.",
                )
            )
        if record.get("subject_id") != subject_id:
            findings.append(
                Finding(
                    severity="error",
                    code="cross_subject_record",
                    message=f"record {key!r} declares subject_id "
                    f"{record.get('subject_id')!r} inside {subject_id!r}'s mastery file",
                    path=path,
                    pointer=f"{pointer}/subject_id",
                    remedy=f"Set subject_id to {subject_id!r} or move the record to its own subject.",
                )
            )
    return findings


def load_concept_mastery(
    ws: Workspace,
    subject_id: str,
    *,
    required: bool = True,
    strict: bool = False,
) -> dict[str, Any] | None:
    """Load a subject's mastery file, validating the file and every record in it."""
    path = concept_mastery_path(ws, subject_id)
    if not path.exists():
        if not required:
            return None
        raise LearningOSDataError(
            f"required file is missing: {ws.rel(path)}",
            path=ws.rel(path),
            remedy='Run `init-subject --id ' + subject_id + ' --name "<name>"` (idempotent) or '
            "`restore --from <backup>`.",
            code="missing_file",
        )
    data = read_json(path, what="concept_mastery", workspace=ws)
    findings = concept_mastery_findings(
        ws, data, subject_id=subject_id, path=ws.rel(path), strict=strict
    )
    _raise_first(findings, ws.rel(path))
    return data


def save_concept_mastery(
    ws: Workspace,
    subject_id: str,
    data: dict[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Validate (file, records and isolation rules) then atomically write a mastery file."""
    path = concept_mastery_path(ws, subject_id)
    if path.exists():
        existing = read_json(path, what="concept_mastery", workspace=ws)
        _raise_first(
            concept_mastery_findings(ws, existing, subject_id=subject_id, path=ws.rel(path)),
            ws.rel(path),
            prefix="refusing to overwrite an invalid file",
        )
    data["updated_at"] = utc_now_iso()
    _raise_first(
        concept_mastery_findings(ws, data, subject_id=subject_id, path=ws.rel(path), strict=strict),
        ws.rel(path),
    )
    atomic_write_json(path, data)
    return data


def put_concept_record(
    ws: Workspace,
    subject_id: str,
    record: dict[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Insert or replace one concept record and write the mastery file atomically.

    Reads the file fresh from disk first, so two writers in the same session cannot clobber each
    other's records through a stale in-memory copy.
    """
    data = load_concept_mastery(ws, subject_id, required=False) or new_concept_mastery(subject_id)
    concept_id = record.get("concept_id")
    if not isinstance(concept_id, str):
        raise LearningOSDataError(
            "concept record has no concept_id",
            path=ws.rel(concept_mastery_path(ws, subject_id)),
            pointer="/concept_id",
            remedy="Build records with memory.store.new_concept_record().",
        )
    record["updated_at"] = utc_now_iso()
    data["concepts"][concept_id] = record
    data["concepts"] = {key: data["concepts"][key] for key in sorted(data["concepts"])}
    save_concept_mastery(ws, subject_id, data, strict=strict)
    return record


def get_concept_record(ws: Workspace, subject_id: str, concept_id: str) -> dict[str, Any] | None:
    data = load_concept_mastery(ws, subject_id, required=False)
    if not data:
        return None
    record = data.get("concepts", {}).get(concept_id)
    return record if isinstance(record, dict) else None


def status_counts(mastery: dict[str, Any] | None) -> dict[str, int]:
    """Concept counts per status, with every status present even when zero."""
    counts = {status: 0 for status in CONCEPT_STATUSES}
    for record in (mastery or {}).get("concepts", {}).values():
        if isinstance(record, dict):
            status = record.get("status")
            if status in counts:
                counts[status] += 1
    return counts


def _raise_first(findings: Sequence[Finding], path: str, *, prefix: str | None = None) -> None:
    errors = [finding for finding in findings if finding.is_error]
    if not errors:
        return
    first = errors[0]
    extra = "" if len(errors) == 1 else f" (and {len(errors) - 1} more)"
    head = f"{prefix}: " if prefix else ""
    raise LearningOSDataError(
        f"{head}{first.message}{extra}",
        path=first.path or path,
        pointer=first.pointer,
        remedy=first.remedy,
        code=first.code,
    )


# ------------------------------------------------------------------------------ knowledge side


def load_subject_config(ws: Workspace, subject_id: str, *, required: bool = True):
    return load_json_file(
        ws,
        ws.knowledge_subject(subject_id) / "subject_config.json",
        "subject_config",
        required=required,
    )


def save_subject_config(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    path = ws.knowledge_subject(subject_id) / "subject_config.json"
    _assert_subject_match(data, subject_id, ws.rel(path))
    return save_json_file(ws, path, data, "subject_config")


def load_concept_catalog(ws: Workspace, subject_id: str, *, required: bool = False):
    return load_json_file(
        ws,
        ws.knowledge_subject(subject_id) / "concept_catalog.json",
        "concept_catalog",
        required=required,
    )


def save_concept_catalog(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    path = ws.knowledge_subject(subject_id) / "concept_catalog.json"
    _assert_subject_match(data, subject_id, ws.rel(path))
    return save_json_file(ws, path, data, "concept_catalog")


def load_source_index(ws: Workspace, subject_id: str, *, required: bool = False):
    return load_json_file(
        ws,
        ws.knowledge_subject(subject_id) / "source_index.json",
        "source_index",
        required=required,
    )


def save_source_index(ws: Workspace, subject_id: str, data: dict[str, Any]) -> dict[str, Any]:
    path = ws.knowledge_subject(subject_id) / "source_index.json"
    _assert_subject_match(data, subject_id, ws.rel(path))
    return save_json_file(ws, path, data, "source_index")


# ---------------------------------------------------------------------------------- checkpoints


def checkpoint_path(ws: Workspace, subject_id: str, session_id: str) -> Path:
    return ws.checkpoint_dir(subject_id) / f"{session_id}.json"


#: Memory files fingerprinted in a checkpoint, so `resume` can tell whether the state it is
#: briefing from is still the state the checkpoint was written against.
FINGERPRINTED_FILES = (
    "subject_state.json",
    "concept_mastery.json",
    "concept_graph.json",
    "misconceptions.json",
    "revision_queue.json",
)


def compute_state_fingerprints(ws: Workspace, subject_id: str) -> dict[str, str]:
    """``{workspace-relative path: "sha256:<hex>"}`` for the subject's memory files."""
    fingerprints: dict[str, str] = {}
    for name in FINGERPRINTED_FILES:
        path = ws.memory_subject(subject_id) / name
        if path.is_file():
            fingerprints[ws.rel(path)] = f"sha256:{sha256_file(path)}"
    return fingerprints


def build_checkpoint(
    ws: Workspace,
    *,
    subject_id: str,
    session_id: str,
    where_we_stopped: str,
    next_action: str,
    concepts_in_flight: Iterable[dict[str, Any]] | None = None,
    open_questions: Iterable[str] | None = None,
    trigger: str | None = None,
    agent: str | None = None,
    created_at: str | None = None,
    checkpoint_id: str | None = None,
    fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble a checkpoint object without writing it."""
    if not str(where_we_stopped).strip():
        raise LearningOSDataError(
            "a checkpoint needs a concrete where_we_stopped",
            pointer="/where_we_stopped",
            remedy='Say what was actually happening, e.g. "mid-derivation of a0 for a square '
            'wave; learner had the integral set up but dropped the 1/2".',
        )
    if not str(next_action).strip():
        raise LearningOSDataError(
            "a checkpoint needs a concrete next_action",
            pointer="/next_action",
            remedy='Name the exact first activity of the next session, not "continue".',
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id or f"{session_id}-cp",
        "session_id": session_id,
        "subject_id": subject_id,
        "created_at": created_at or utc_now_iso(),
        "where_we_stopped": where_we_stopped,
        "concepts_in_flight": [dict(item) for item in (concepts_in_flight or [])],
        "next_action": next_action,
        "open_questions": list(open_questions or []),
        "trigger": trigger,
        "agent": agent,
        "tool_version": TOOL_ID,
        "state_fingerprints": (
            fingerprints if fingerprints is not None else compute_state_fingerprints(ws, subject_id)
        ),
    }


def write_checkpoint(ws: Workspace, checkpoint: dict[str, Any]) -> Path:
    """Validate and atomically write a checkpoint.

    A checkpoint for the same session id is overwritten on purpose: it is the *current* resume
    point, and the history of the session lives in the event log.
    """
    subject_id = str(checkpoint.get("subject_id"))
    session_id = str(checkpoint.get("session_id"))
    path = checkpoint_path(ws, subject_id, session_id)
    registry(ws).validate_or_raise("checkpoint", checkpoint, path=ws.rel(path))
    atomic_write_json(path, checkpoint)
    return path


def load_checkpoint(ws: Workspace, subject_id: str, session_id: str) -> dict[str, Any] | None:
    return load_json_file(
        ws,
        checkpoint_path(ws, subject_id, session_id),
        "checkpoint",
        required=False,
    )


def list_checkpoints(ws: Workspace, subject_id: str) -> list[Path]:
    directory = ws.checkpoint_dir(subject_id)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def latest_checkpoint(ws: Workspace, subject_id: str) -> tuple[Path, dict[str, Any]] | None:
    """The newest checkpoint for a subject by ``created_at``, falling back to filename order."""
    best: tuple[str, Path, dict[str, Any]] | None = None
    for path in list_checkpoints(ws, subject_id):
        data = read_json_optional(path, what="checkpoint", workspace=ws)
        if not isinstance(data, dict):
            continue
        key = str(data.get("created_at") or "") + "|" + path.name
        if best is None or key > best[0]:
            best = (key, path, data)
    if best is None:
        return None
    registry(ws).validate_or_raise("checkpoint", best[2], path=ws.rel(best[1]))
    return best[1], best[2]


def check_fingerprints(ws: Workspace, checkpoint: dict[str, Any]) -> list[Finding]:
    """Compare a checkpoint's fingerprints with the files on disk.

    A mismatch is a *warning*: memory legitimately moves on after a checkpoint was taken. It is
    reported so the briefing can say "this resume point may be out of date" instead of pretending
    to be authoritative.
    """
    findings: list[Finding] = []
    fingerprints = checkpoint.get("state_fingerprints")
    if not isinstance(fingerprints, dict):
        return findings
    for relative, expected in fingerprints.items():
        path = ws.root / str(relative)
        if not path.is_file():
            findings.append(
                Finding(
                    severity="warning",
                    code="fingerprint_missing",
                    message=f"{relative} was fingerprinted at checkpoint time but is missing now",
                    path=str(relative),
                    remedy="Run `validate`; restore the file if it was lost.",
                )
            )
            continue
        actual = f"sha256:{sha256_file(path)}"
        if actual != expected:
            findings.append(
                Finding(
                    severity="warning",
                    code="fingerprint_mismatch",
                    message=f"{relative} has changed since this checkpoint was written",
                    path=str(relative),
                    remedy="Expected if you studied or hand-edited since then. Treat the "
                    "checkpoint as a hint and trust the current state files.",
                )
            )
    return findings
