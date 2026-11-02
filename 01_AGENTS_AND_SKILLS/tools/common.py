"""Shared primitives for the LearningOS command-line tooling.

Everything in this module is standard library only, and deliberately so: `validate` is the
install's health check, so it must keep working on a bare Python 3.11 with no third-party
packages and no network access. There are no network imports anywhere in `tools/`.

What lives here:

* exit codes (`EXIT_*`) and the exception hierarchy that maps onto them,
* `Finding` — one structured validation/diagnostic record, plus its printers,
* `Workspace` — the resolved workspace root and every canonical path inside it,
* atomic write helpers (temp file in the *same* directory, flush + fsync, `os.replace`),
* small utilities: hashing, UTC timestamps, slugs, workspace-relative paths.

The atomic-write rule is normative and is documented in
`01_AGENTS_AND_SKILLS/core/memory_protocol.md` section 7: a crash leaves either the complete old
file or the complete new file, never half of either, and no `*.tmp` file survives a successful
write.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# --------------------------------------------------------------------------------------- identity

TOOL_NAME = "learningos"
TOOL_VERSION = "0.3.0"
TOOL_ID = f"{TOOL_NAME} {TOOL_VERSION}"
SCHEMA_VERSION = 1

#: The file that marks a directory as a LearningOS workspace root.
MARKER_FILE = "learningos.config.json"

#: Environment variable consulted when ``--workspace`` is not given.
HOME_ENV_VAR = "LEARNINGOS_HOME"

# ------------------------------------------------------------------------------------- exit codes

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_NOT_FOUND = 4
EXIT_REFUSED = 5
EXIT_MISSING_OPTIONAL_DEP = 6

EXIT_CODE_MEANINGS: dict[int, str] = {
    EXIT_OK: "success",
    EXIT_ERROR: "unexpected error",
    EXIT_USAGE: "usage error (bad arguments)",
    EXIT_VALIDATION: "validation or data failure",
    EXIT_NOT_FOUND: "requested thing does not exist",
    EXIT_REFUSED: "refused a destructive operation (use --force)",
    EXIT_MISSING_OPTIONAL_DEP: "a required optional dependency is missing",
}

# ---------------------------------------------------------------------------------------- regexes

SUBJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
STORED_SUBJECT_ID_RE = re.compile(r"^(_template|[a-z0-9][a-z0-9_-]*)$")
CONCEPT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*:[a-z0-9][a-z0-9_-]*$")
MISCONCEPTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*:mc-[0-9]{3}$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{8}$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Values that must never appear in a stored memory value (see validation check (l)).
ABSOLUTE_PATH_RES = (
    re.compile(r"^[A-Za-z]:[\\/]"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"^\\\\"),
    re.compile(r"^/(?:home|Users|usr|var|etc|mnt|opt|tmp|root)(?:/|$)"),
)

# ------------------------------------------------------------------------------------- exceptions


class LearningOSError(Exception):
    """Base class for every error the CLI knows how to report.

    Carries the information a user needs to act: what went wrong, which file, where inside the
    file, and what to do about it. ``exit_code`` is what ``main()`` returns.
    """

    exit_code = EXIT_ERROR
    code = "error"

    def __init__(
        self,
        message: str,
        *,
        remedy: str | None = None,
        path: str | None = None,
        pointer: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.path = path
        self.pointer = pointer
        if code:
            self.code = code

    def as_finding(self) -> "Finding":
        return Finding(
            severity="error",
            code=self.code,
            message=self.message,
            path=self.path,
            pointer=self.pointer,
            remedy=self.remedy,
        )

    def format_human(self) -> str:
        where = self.path or ""
        if where and self.pointer:
            where = f"{where}{self.pointer}"
        head = f"error: {self.message}" if not where else f"error: {where}: {self.message}"
        if self.remedy:
            return f"{head}\n  remedy: {self.remedy}"
        return head


class LearningOSUsageError(LearningOSError):
    """Bad arguments or an impossible request. Exit 2."""

    exit_code = EXIT_USAGE
    code = "usage"


class LearningOSDataError(LearningOSError):
    """A file will not parse, will not validate, or is missing when it is required. Exit 3.

    Raising this is how the corruption policy in `core/memory_protocol.md` is enforced: the
    offending file is never written, never truncated and never "fixed".
    """

    exit_code = EXIT_VALIDATION
    code = "data"


class LearningOSNotFoundError(LearningOSError):
    """The subject, session or artefact asked for does not exist. Exit 4."""

    exit_code = EXIT_NOT_FOUND
    code = "not_found"


class LearningOSRefusedError(LearningOSError):
    """A destructive operation was refused because ``--force`` was not given. Exit 5."""

    exit_code = EXIT_REFUSED
    code = "refused"


class LearningOSMissingDependencyError(LearningOSError):
    """An optional dependency was required by an explicit flag and is absent. Exit 6."""

    exit_code = EXIT_MISSING_OPTIONAL_DEP
    code = "missing_optional_dependency"


# ---------------------------------------------------------------------------------------- finding

SEVERITIES = ("error", "warning", "info")


@dataclass(frozen=True)
class Finding:
    """One structured diagnostic.

    Fields are the documented set (severity, code, path, pointer, message, remedy); `message` is
    declared before the optional ones only because Python requires defaults to come last.

    * ``severity`` — ``error`` (exit 3 from `validate`), ``warning`` or ``info``.
    * ``code`` — stable machine-readable label, e.g. ``schema``, ``dangling_edge``.
    * ``path`` — workspace-relative file path, with ``:<line>`` appended for JSONL findings.
    * ``pointer`` — RFC 6901 JSON pointer inside that file, e.g. ``/concepts/algo:big-o/status``.
    * ``message`` — what is wrong, in one line.
    * ``remedy`` — the concrete next action.
    """

    severity: str
    code: str
    message: str
    path: str | None = None
    pointer: str | None = None
    remedy: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "pointer": self.pointer,
            "message": self.message,
            "remedy": self.remedy,
        }

    def format_human(self) -> str:
        location = self.path or "(workspace)"
        if self.pointer:
            location = f"{location} {self.pointer}"
        lines = [f"{self.severity.upper():7} {self.code:24} {location}", f"        {self.message}"]
        if self.remedy:
            lines.append(f"        remedy: {self.remedy}")
        return "\n".join(lines)


def count_findings(findings: Sequence[Finding]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    return sorted(
        findings,
        key=lambda f: (order[f.severity], f.path or "", f.pointer or "", f.code, f.message),
    )


def print_findings(
    findings: Sequence[Finding],
    *,
    as_json: bool = False,
    stream: Any = None,
    quiet: bool = False,
) -> dict[str, int]:
    """Print findings grouped by severity and return the counts.

    With ``as_json`` the whole report is a single JSON object so another tool can consume it.
    """
    stream = stream or sys.stdout
    findings = sort_findings(findings)
    counts = count_findings(findings)
    if as_json:
        payload = {
            "tool": TOOL_ID,
            "counts": counts,
            "findings": [finding.to_dict() for finding in findings],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=stream)
        return counts
    if quiet:
        findings = [finding for finding in findings if finding.is_error]
    for severity in SEVERITIES:
        group = [finding for finding in findings if finding.severity == severity]
        if not group:
            continue
        print(f"\n{severity.upper()}S ({len(group)})", file=stream)
        for finding in group:
            print(finding.format_human(), file=stream)
    return counts


# -------------------------------------------------------------------------------- canonical paths

#: Name -> workspace-relative path. Mirrors the ``paths`` table in ``learningos.config.json``;
#: used verbatim when a workspace is being created and the config does not exist yet.
DEFAULT_PATHS: dict[str, str] = {
    "agents_and_skills": "01_AGENTS_AND_SKILLS",
    "core": "01_AGENTS_AND_SKILLS/core",
    "skills": "01_AGENTS_AND_SKILLS/skills",
    "adapters": "01_AGENTS_AND_SKILLS/adapters",
    "schemas": "01_AGENTS_AND_SKILLS/schemas",
    "templates": "01_AGENTS_AND_SKILLS/templates",
    "subject_template": "01_AGENTS_AND_SKILLS/templates/subject_template",
    "memory_templates": "01_AGENTS_AND_SKILLS/templates/memory_templates",
    "graph_templates": "01_AGENTS_AND_SKILLS/templates/graph_templates",
    "tools": "01_AGENTS_AND_SKILLS/tools",
    "docs": "01_AGENTS_AND_SKILLS/docs",
    "knowledge": "02_KNOWLEDGE",
    "inbox": "02_KNOWLEDGE/inbox",
    "shared_reference": "02_KNOWLEDGE/shared_reference",
    "knowledge_subjects": "02_KNOWLEDGE/subjects",
    "memory": "03_MEMORY",
    "memory_global": "03_MEMORY/global",
    "memory_subjects": "03_MEMORY/subjects",
    "events": "03_MEMORY/events",
    "event_log": "03_MEMORY/events/session_events.jsonl",
    "session_logs": "03_MEMORY/session_logs",
    "graphs": "03_MEMORY/graphs",
    "cross_subject_graphs": "03_MEMORY/graphs/_cross",
    "checkpoints": "03_MEMORY/checkpoints",
    "archives": "03_MEMORY/archives",
    "backups": "03_MEMORY/backups",
}

#: The only three directories permitted at the top level of a workspace.
PRINCIPAL_DIRS = ("01_AGENTS_AND_SKILLS", "02_KNOWLEDGE", "03_MEMORY")

#: Root-level files that belong to a complete install.
ROOT_FILES = ("AGENTS.md", "README.md", "CHANGELOG.md", MARKER_FILE)

#: Subject-directory names that are scaffolding rather than real subjects.
TEMPLATE_DIR_PREFIX = "_"


@dataclass(frozen=True)
class Workspace:
    """A resolved workspace root plus the canonical path table.

    Construct with :meth:`at` (or :func:`resolve_workspace`); never hard-code a path, because the
    folder is designed to be copied anywhere.
    """

    root: Path
    paths: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PATHS))
    config: dict[str, Any] = field(default_factory=dict)

    # -- construction ------------------------------------------------------------------

    @classmethod
    def at(cls, root: str | os.PathLike[str]) -> "Workspace":
        """Build a Workspace for ``root``, reading its config file when one is present."""
        root_path = Path(root).expanduser().resolve()
        config: dict[str, Any] = {}
        paths = dict(DEFAULT_PATHS)
        marker = root_path / MARKER_FILE
        if marker.is_file():
            config = read_json(marker)
            if not isinstance(config, dict):
                raise LearningOSDataError(
                    f"{MARKER_FILE} must contain a JSON object",
                    path=MARKER_FILE,
                    remedy="Restore the file from a backup or re-create it with `init-workspace`.",
                )
            configured = config.get("paths")
            if isinstance(configured, dict):
                for key, value in configured.items():
                    if isinstance(key, str) and isinstance(value, str):
                        paths[key] = value
        return cls(root=root_path, paths=paths, config=config)

    # -- generic access ----------------------------------------------------------------

    def path(self, key: str) -> Path:
        """Absolute path for a canonical path key (see :data:`DEFAULT_PATHS`)."""
        try:
            relative = self.paths[key]
        except KeyError as exc:  # pragma: no cover - programming error
            raise KeyError(f"unknown workspace path key: {key!r}") from exc
        return self.root / relative

    def rel(self, target: str | os.PathLike[str]) -> str:
        """Workspace-relative, forward-slash form of ``target`` — the only form ever stored."""
        return relpath_posix(target, self.root)

    @property
    def marker(self) -> Path:
        return self.root / MARKER_FILE

    @property
    def exists(self) -> bool:
        return self.marker.is_file()

    @property
    def tool_version(self) -> str:
        return TOOL_ID

    # -- named paths -------------------------------------------------------------------

    @property
    def agents_and_skills(self) -> Path:
        return self.path("agents_and_skills")

    @property
    def core(self) -> Path:
        return self.path("core")

    @property
    def skills(self) -> Path:
        return self.path("skills")

    @property
    def adapters(self) -> Path:
        return self.path("adapters")

    @property
    def schemas(self) -> Path:
        return self.path("schemas")

    @property
    def templates(self) -> Path:
        return self.path("templates")

    @property
    def subject_template(self) -> Path:
        return self.path("subject_template")

    @property
    def memory_templates(self) -> Path:
        return self.path("memory_templates")

    @property
    def graph_templates(self) -> Path:
        return self.path("graph_templates")

    @property
    def tools(self) -> Path:
        return self.path("tools")

    @property
    def docs(self) -> Path:
        return self.path("docs")

    @property
    def knowledge(self) -> Path:
        return self.path("knowledge")

    @property
    def inbox(self) -> Path:
        return self.path("inbox")

    @property
    def shared_reference(self) -> Path:
        return self.path("shared_reference")

    @property
    def knowledge_subjects(self) -> Path:
        return self.path("knowledge_subjects")

    @property
    def memory(self) -> Path:
        return self.path("memory")

    @property
    def memory_global(self) -> Path:
        return self.path("memory_global")

    @property
    def memory_subjects(self) -> Path:
        return self.path("memory_subjects")

    @property
    def events(self) -> Path:
        return self.path("events")

    @property
    def event_log(self) -> Path:
        return self.path("event_log")

    @property
    def session_logs(self) -> Path:
        return self.path("session_logs")

    @property
    def graphs(self) -> Path:
        return self.path("graphs")

    @property
    def cross_subject_graphs(self) -> Path:
        return self.path("cross_subject_graphs")

    @property
    def checkpoints(self) -> Path:
        return self.path("checkpoints")

    @property
    def archives(self) -> Path:
        return self.path("archives")

    @property
    def backups(self) -> Path:
        return self.path("backups")

    @property
    def workspace_state_file(self) -> Path:
        return self.memory_global / "workspace_state.json"

    @property
    def learning_preferences_file(self) -> Path:
        return self.memory_global / "learning_preferences.json"

    @property
    def learner_index_file(self) -> Path:
        return self.memory_global / "learner_index.md"

    @property
    def learner_profile_file(self) -> Path:
        return self.memory_global / "learner_profile.md"

    @property
    def active_priorities_file(self) -> Path:
        return self.memory_global / "active_priorities.md"

    # -- per-subject paths -------------------------------------------------------------

    def knowledge_subject(self, subject_id: str) -> Path:
        return self.knowledge_subjects / subject_id

    def memory_subject(self, subject_id: str) -> Path:
        return self.memory_subjects / subject_id

    def checkpoint_dir(self, subject_id: str) -> Path:
        return self.checkpoints / subject_id

    def graph_dir(self, subject_id: str) -> Path:
        return self.graphs / subject_id

    def list_subjects(self) -> list[str]:
        """Real subject ids found on disk, template scaffolding excluded."""
        found: set[str] = set()
        for parent in (self.memory_subjects, self.knowledge_subjects):
            if not parent.is_dir():
                continue
            for child in parent.iterdir():
                if not child.is_dir():
                    continue
                if child.name.startswith((TEMPLATE_DIR_PREFIX, ".")):
                    continue
                if not SUBJECT_ID_RE.fullmatch(child.name):
                    # A display-name drop folder is not a valid initialized subject.
                    # Validation reports it separately without touching its contents.
                    continue
                found.add(child.name)
        return sorted(found)

    def list_subject_dirs(self, parent_key: str) -> list[Path]:
        """Every subject-ish directory under a parent, including ``_template``.

        Used by `validate`, which checks the shipped templates for schema conformance even though
        they are not real subjects.
        """
        parent = self.path(parent_key)
        if not parent.is_dir():
            return []
        return sorted(
            (child for child in parent.iterdir() if child.is_dir() and not child.name.startswith(".")),
            key=lambda p: p.name,
        )


def resolve_workspace(explicit: str | None = None, *, require_marker: bool = True) -> Workspace:
    """Find the workspace root and return a :class:`Workspace`.

    Resolution order, highest priority first:

    1. ``--workspace PATH`` (the ``explicit`` argument),
    2. the ``LEARNINGOS_HOME`` environment variable,
    3. the nearest ancestor of this file that contains ``learningos.config.json``,
    4. the nearest such ancestor of the current working directory.

    With ``require_marker=False`` the directory does not have to be a workspace yet, which is what
    ``init-workspace`` needs when it is creating one.
    """
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.exists() and require_marker:
            raise LearningOSUsageError(
                f"--workspace path does not exist: {candidate}",
                remedy="Point --workspace at an existing LearningOS folder, "
                "or run `init-workspace --workspace <path>` to create one.",
            )
        candidate = candidate.resolve() if candidate.exists() else candidate.absolute()
        if require_marker and not (candidate / MARKER_FILE).is_file():
            raise LearningOSUsageError(
                f"{candidate} is not a LearningOS workspace ({MARKER_FILE} not found)",
                remedy=f"Run `init-workspace --workspace {candidate}` to scaffold one.",
            )
        return Workspace.at(candidate)

    env_home = os.environ.get(HOME_ENV_VAR)
    if env_home:
        candidate = Path(env_home).expanduser()
        if require_marker and not (candidate / MARKER_FILE).is_file():
            raise LearningOSUsageError(
                f"{HOME_ENV_VAR}={env_home} is not a LearningOS workspace "
                f"({MARKER_FILE} not found)",
                remedy=f"Unset {HOME_ENV_VAR} or point it at the LearningOS folder.",
            )
        return Workspace.at(candidate)

    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        found = _ascend_for_marker(start)
        if found is not None:
            return Workspace.at(found)

    if not require_marker:
        return Workspace.at(Path.cwd())
    raise LearningOSUsageError(
        f"could not find a LearningOS workspace: no {MARKER_FILE} in this directory or any parent",
        remedy=f"Pass --workspace <path>, set {HOME_ENV_VAR}, or run `init-workspace`.",
    )


def _ascend_for_marker(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / MARKER_FILE).is_file():
            return directory
    return None


# ---------------------------------------------------------------------------------- atomic writes


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    """Create a directory (and parents) if needed and return it."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def dumps_json(obj: Any) -> str:
    """The one canonical JSON serialisation for state files: 2-space indent, UTF-8, trailing LF."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> Path:
    """Write ``data`` to ``path`` atomically.

    The temp file is created in the *same* directory as the target so that ``os.replace`` stays
    within one filesystem and is therefore atomic. The handle is flushed and ``os.fsync``ed before
    the replace, and the temp file is removed if anything goes wrong — no ``*.tmp`` debris is left
    behind either way.
    """
    target = Path(path)
    ensure_dir(target.parent)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return target


def atomic_write_text(path: str | os.PathLike[str], text: str) -> Path:
    """Atomically write UTF-8 text with LF line endings."""
    normalised = text.replace("\r\n", "\n")
    return atomic_write_bytes(path, normalised.encode("utf-8"))


def atomic_write_json(path: str | os.PathLike[str], obj: Any) -> Path:
    """Atomically write ``obj`` as pretty-printed UTF-8 JSON.

    Callers that write memory state must schema-validate *before* calling this (see
    ``memory/store.py``), so an invalid snapshot never reaches the canonical path.
    """
    return atomic_write_bytes(path, dumps_json(obj).encode("utf-8"))


def append_jsonl_line(path: str | os.PathLike[str], obj: Any) -> str:
    """Append one compact JSON object as a single LF-terminated line, flushed and fsynced.

    The event log is the only append-only file in the system and the only one that is never
    rewritten — not even to fix a bad line.
    """
    target = Path(path)
    ensure_dir(target.parent)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if "\n" in line or "\r" in line:  # pragma: no cover - json.dumps escapes these
        raise LearningOSDataError(
            "refusing to append an event containing a raw newline",
            path=str(target),
            remedy="Report this as a bug; event payloads must be JSON-escaped.",
        )
    with open(target, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return line


# ------------------------------------------------------------------------------------- read paths


def read_json(
    path: str | os.PathLike[str],
    *,
    what: str | None = None,
    remedy: str | None = None,
    workspace: Workspace | None = None,
) -> Any:
    """Read JSON, raising :class:`LearningOSDataError` with an actionable message on failure.

    A missing required file and an unparseable file are both exit-3 conditions. Neither case
    writes, truncates or repairs anything.
    """
    target = Path(path)
    shown = workspace.rel(target) if workspace else str(target)
    label = what or "file"
    if not target.exists():
        raise LearningOSDataError(
            f"required {label} is missing",
            path=shown,
            remedy=remedy
            or "Run `init-subject` (idempotent, never overwrites survivors) or `restore` "
            "from a backup.",
            code="missing_file",
        )
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LearningOSDataError(
            f"{label} is not valid UTF-8 (byte {exc.start})",
            path=shown,
            remedy=remedy or "Re-save the file as UTF-8, or restore it from a backup.",
            code="encoding",
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LearningOSDataError(
            f"{label} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
            path=shown,
            remedy=remedy
            or "Fix the syntax by hand, or restore the file from 03_MEMORY/backups/. "
            "LearningOS never repairs a corrupt file for you.",
            code="parse",
        ) from exc


def read_json_optional(
    path: str | os.PathLike[str],
    *,
    what: str | None = None,
    workspace: Workspace | None = None,
) -> Any | None:
    """Like :func:`read_json` but returns ``None`` when the file does not exist."""
    if not Path(path).exists():
        return None
    return read_json(path, what=what, workspace=workspace)


def read_text(path: str | os.PathLike[str]) -> str:
    """Read UTF-8 text, tolerating undecodable bytes so a damaged file can still be reported."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------------------- utilities


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1 << 20) -> str:
    """Streaming sha256 of a file, so a large PDF does not have to fit in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def hash_tree(root: str | os.PathLike[str], *, skip_names: Sequence[str] = ()) -> dict[str, str]:
    """Map every file under ``root`` to its sha256, keyed by relative POSIX path.

    Used for before/after comparisons: idempotency checks, backup round-trips, and the
    "this command touched nothing" assertions.
    """
    base = Path(root)
    if not base.exists():
        return {}
    skip = set(skip_names) | {"__pycache__", ".pytest_cache"}
    result: dict[str, str] = {}
    for item in sorted(base.rglob("*")):
        if any(part in skip for part in item.parts):
            continue
        if item.is_file():
            result[relpath_posix(item, base)] = sha256_file(item)
    return result


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` — the only timestamp format ever stored."""
    return format_timestamp(utc_now())


def format_timestamp(moment: _dt.datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: str, *, field_name: str = "timestamp") -> _dt.datetime:
    """Parse a stored ``...Z`` timestamp, raising a usage error on anything else."""
    if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
        raise LearningOSUsageError(
            f"{field_name} must look like 2026-02-11T09:14:00Z, got {value!r}",
            remedy="Use ISO-8601 UTC with a trailing Z and second precision.",
        )
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def parse_timestamp_safe(value: Any) -> _dt.datetime | None:
    """Parse a timestamp, returning ``None`` instead of raising. For read-only comparisons."""
    if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
        return None
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def parse_date(value: str, *, field_name: str = "date") -> _dt.date:
    """Parse a ``YYYY-MM-DD`` calendar date, raising a usage error on anything else."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise LearningOSUsageError(
            f"{field_name} must look like 2026-05-18, got {value!r}",
            remedy="Use a calendar date in YYYY-MM-DD form.",
        )
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise LearningOSUsageError(
            f"{field_name} is not a real date: {value!r}",
            remedy="Check the month and day.",
        ) from exc


def days_between(earlier: str | _dt.datetime, later: str | _dt.datetime) -> float:
    """Whole-and-fractional days from ``earlier`` to ``later``; negative if the order is reversed."""
    start = parse_timestamp(earlier) if isinstance(earlier, str) else earlier
    end = parse_timestamp(later) if isinstance(later, str) else later
    return (end - start).total_seconds() / 86400.0


def slugify(text: str, *, max_length: int = 60) -> str:
    """Lowercase hyphen slug suitable for an id segment.

    Accents are folded, runs of non-alphanumerics collapse to a single hyphen, and the result is
    trimmed to ``max_length``. An input with nothing usable returns ``"untitled"`` rather than an
    empty string, because ids must never be empty.
    """
    normalised = unicodedata.normalize("NFKD", str(text))
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:max_length].strip("-")
    if not slug or not slug[0].isalnum():
        slug = ("x-" + slug).strip("-") if slug else "untitled"
    return slug


def relpath_posix(target: str | os.PathLike[str], base: str | os.PathLike[str]) -> str:
    """``target`` relative to ``base`` with forward slashes.

    Falls back to ``os.path.relpath`` (so ``..`` segments can appear) only when the two paths do
    not share a prefix; stored values are always inside the workspace and so never hit that case.
    """
    target_path = Path(target)
    base_path = Path(base)
    try:
        return target_path.relative_to(base_path).as_posix()
    except ValueError:
        return Path(os.path.relpath(str(target_path), str(base_path))).as_posix()


def looks_absolute(value: str) -> bool:
    """True when a stored string looks like an absolute or drive-letter filesystem path."""
    if not isinstance(value, str) or not value:
        return False
    return any(pattern.search(value) for pattern in ABSOLUTE_PATH_RES)


def iter_json_strings(node: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(json_pointer, value)`` for every string anywhere inside a parsed JSON document."""
    if isinstance(node, str):
        yield pointer or "/", node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from iter_json_strings(value, f"{pointer}/{escape_pointer_token(str(key))}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_json_strings(value, f"{pointer}/{index}")


def escape_pointer_token(token: str) -> str:
    """RFC 6901 escaping for one JSON pointer token."""
    return token.replace("~", "~0").replace("/", "~1")


def truncate(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def which(command: str) -> str | None:
    """``shutil.which`` wrapped so callers do not have to import shutil for a capability probe."""
    import shutil

    return shutil.which(command)


def optional_dependency(module_name: str) -> tuple[bool, str | None]:
    """Report whether an optional dependency imports, and its version if it can be determined.

    Optional dependencies are never installed automatically and their absence is never an
    exception — only a documented, visible degradation. The version is read from installed
    metadata rather than a ``__version__`` attribute, which some packages have deprecated.
    """
    try:
        __import__(module_name)
    except Exception:
        return False, None
    try:
        from importlib.metadata import version

        return True, version(module_name)
    except Exception:
        return True, None
