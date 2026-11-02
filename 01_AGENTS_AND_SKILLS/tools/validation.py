"""The engine behind ``learningos.py validate``.

Twelve groups of checks, each producing :class:`common.Finding` records rather than exceptions, so
one broken file never hides the rest of the report:

(a) structure — the three principal directories, the root entrypoints, no fourth top-level
    directory;
(b) every JSON file under the workspace parses;
(c) every file LearningOS knows about conforms to its schema;
(d) ``concept_id`` prefix == ``subject_id`` == owning directory name, and no duplicate ids;
(e) ``prerequisite_ids`` resolve (cross-subject references are allowed when the target exists);
(f) ``concept_graph.json`` edge endpoints resolve — a dangling endpoint is an **error**;
(g) ``misconception_ids`` resolve in both directions;
(h) every line of the event log parses and validates, reported with its line number;
(i) graph artefacts reference concepts that still exist;
(j) the adapter files exist and every workspace-relative path they mention resolves;
(k) ``stale_state`` drift between the event log and the snapshots;
(l) no absolute filesystem path appears in any stored value.

Severity follows the layer a file belongs to: evidence and current state produce errors, derived
artefacts produce warnings, because derived files are regenerable and a warning is the honest
signal for "re-run the generator".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from common import (
    CONCEPT_ID_RE,
    MISCONCEPTION_ID_RE,
    PRINCIPAL_DIRS,
    SUBJECT_ID_RE,
    ROOT_FILES,
    Finding,
    LearningOSDataError,
    LearningOSUsageError,
    MARKER_FILE,
    Workspace,
    escape_pointer_token,
    iter_json_strings,
    looks_absolute,
    read_json,
    read_text,
    sha256_file,
    sort_findings,
    truncate,
)
from memory import events as events_mod
from memory import store
from schema import SchemaRegistry, registry_for

#: Workspace-relative paths that must exist for the install to be usable at all.
REQUIRED_PATH_KEYS = (
    "agents_and_skills",
    "core",
    "skills",
    "adapters",
    "schemas",
    "templates",
    "subject_template",
    "memory_templates",
    "tools",
    "knowledge",
    "knowledge_subjects",
    "memory",
    "memory_global",
    "memory_subjects",
    "events",
)

#: Paths that are merely convenient: a missing one is a warning and is recreated on demand.
OPTIONAL_PATH_KEYS = (
    "docs",
    "graph_templates",
    "inbox",
    "shared_reference",
    "session_logs",
    "graphs",
    "cross_subject_graphs",
    "checkpoints",
    "archives",
    "backups",
)

#: Files whose referenced paths are checked by check (j).
ADAPTER_FILES = (
    "AGENTS.md",
    "01_AGENTS_AND_SKILLS/adapters/kiro/steering/learningos.md",
    "01_AGENTS_AND_SKILLS/adapters/kiro/README.md",
    "01_AGENTS_AND_SKILLS/adapters/codex/README.md",
)

#: A workspace-relative path as written in the documentation, e.g. ``03_MEMORY/global``.
DOC_PATH_RE = re.compile(r"\b0[123]_[A-Za-z_]+(?:/[A-Za-z0-9_.-]+)*")

#: Event types whose arrival means a subject's snapshots should have moved.
STATE_CHANGING_EVENTS = (
    "answer_evaluated",
    "mastery_updated",
    "misconception_detected",
    "misconception_resolved",
    "revision_scheduled",
    "session_ended",
)


@dataclass
class ConceptIndex:
    """Which concept and misconception ids exist anywhere in the workspace.

    Reference checks resolve against this: a concept record is the strongest evidence that a
    concept exists, a catalogue entry is enough to say "planned but not yet seeded", and anything
    in neither place is a dangling reference.
    """

    records: dict[str, str] = field(default_factory=dict)
    catalogued: dict[str, str] = field(default_factory=dict)
    misconceptions: dict[str, str] = field(default_factory=dict)

    def knows(self, concept_id: str) -> bool:
        return concept_id in self.records or concept_id in self.catalogued

    def has_record(self, concept_id: str) -> bool:
        return concept_id in self.records

    def owner(self, concept_id: str) -> str | None:
        return self.records.get(concept_id) or self.catalogued.get(concept_id)


class WorkspaceValidator:
    """Runs the checks and accumulates findings. One instance per `validate` invocation."""

    def __init__(self, ws: Workspace, *, subject: str | None = None, strict: bool = False) -> None:
        self.ws = ws
        self.subject = subject
        self.strict = strict
        self.findings: list[Finding] = []
        self.index = ConceptIndex()
        self._json_cache: dict[Path, Any] = {}
        self._visited: set[Path] = set()
        self._registry: SchemaRegistry | None = None

    # -- plumbing ----------------------------------------------------------------------

    @property
    def registry(self) -> SchemaRegistry:
        if self._registry is None:
            self._registry = registry_for(self.ws.schemas)
        return self._registry

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        path: str | None = None,
        pointer: str | None = None,
        remedy: str | None = None,
    ) -> None:
        self.findings.append(
            Finding(
                severity=severity,
                code=code,
                message=message,
                path=path,
                pointer=pointer,
                remedy=remedy,
            )
        )

    def rel(self, path: Path) -> str:
        return self.ws.rel(path)

    def read_json(self, path: Path, *, severity: str = "error") -> Any | None:
        """Parse a JSON file once, reporting a parse failure at the given severity (check (b))."""
        self._visited.add(path)
        if path in self._json_cache:
            return self._json_cache[path]
        try:
            data = read_json(path, workspace=self.ws)
        except LearningOSDataError as exc:
            self._json_cache[path] = None
            self.add(
                severity,
                exc.code,
                exc.message,
                path=exc.path or self.rel(path),
                pointer=exc.pointer,
                remedy=exc.remedy,
            )
            return None
        self._json_cache[path] = data
        return data

    def validate_against(
        self,
        path: Path,
        schema_name: str,
        data: Any,
        *,
        severity: str = "error",
    ) -> bool:
        """Schema-check ``data`` (check (c)). Returns True when it conformed."""
        findings = self.registry.validate(
            schema_name, data, path=self.rel(path), strict=self.strict
        )
        if not findings:
            return True
        for finding in findings:
            self.findings.append(
                finding
                if severity == "error"
                else Finding(
                    severity=severity,
                    code=finding.code,
                    message=finding.message,
                    path=finding.path,
                    pointer=finding.pointer,
                    remedy=finding.remedy,
                )
            )
        return False

    def load_and_validate(
        self,
        path: Path,
        schema_name: str,
        *,
        severity: str = "error",
    ) -> Any | None:
        data = self.read_json(path, severity=severity)
        if data is None:
            return None
        return data if self.validate_against(path, schema_name, data, severity=severity) else None

    # -- (a) structure -----------------------------------------------------------------

    def check_structure(self) -> None:
        root = self.ws.root
        if not self.ws.marker.is_file():
            self.add(
                "error",
                "not_a_workspace",
                f"{MARKER_FILE} is missing, so this directory is not a LearningOS workspace",
                path=MARKER_FILE,
                remedy="Run `init-workspace` from 01_AGENTS_AND_SKILLS/tools.",
            )
        for name in PRINCIPAL_DIRS:
            if not (root / name).is_dir():
                self.add(
                    "error",
                    "missing_principal_directory",
                    f"principal directory {name}/ is missing",
                    path=name,
                    remedy="Run `init-workspace`; it recreates the scaffold without touching "
                    "existing files.",
                )
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name not in PRINCIPAL_DIRS:
                self.add(
                    "error",
                    "unexpected_top_level_directory",
                    f"{child.name}/ is a fourth top-level directory; LearningOS allows exactly "
                    "three",
                    path=child.name,
                    remedy="Move its contents into 01_AGENTS_AND_SKILLS, 02_KNOWLEDGE or "
                    "03_MEMORY, then remove the directory yourself — LearningOS never deletes "
                    "your files.",
                )
        for name in ROOT_FILES:
            if not (root / name).is_file():
                self.add(
                    "error",
                    "missing_root_file",
                    f"root entrypoint {name} is missing",
                    path=name,
                    remedy="Run `init-workspace` to restore the shipped copy.",
                )
        for key in REQUIRED_PATH_KEYS:
            path = self.ws.path(key)
            if not path.is_dir():
                self.add(
                    "error",
                    "missing_directory",
                    f"required directory is missing ({key})",
                    path=self.rel(path),
                    remedy="Run `init-workspace`.",
                )
        for key in OPTIONAL_PATH_KEYS:
            path = self.ws.path(key)
            if not path.is_dir():
                self.add(
                    "warning",
                    "missing_directory",
                    f"optional directory is missing ({key}); it will be created when first used",
                    path=self.rel(path),
                    remedy="Run `init-workspace` if you want it now.",
                )
        if not self.ws.event_log.is_file():
            self.add(
                "error",
                "missing_event_log",
                "the append-only event log is missing",
                path=self.rel(self.ws.event_log),
                remedy="Run `init-workspace` to create it empty, or `restore` if you had history "
                "in it — the event log is the one layer that cannot be rebuilt.",
            )
        config = self.read_json(self.ws.marker) if self.ws.marker.is_file() else None
        if isinstance(config, dict):
            for key in ("schema_version", "version", "paths"):
                if key not in config:
                    self.add(
                        "warning",
                        "config_incomplete",
                        f"{MARKER_FILE} has no {key!r} key",
                        path=MARKER_FILE,
                        pointer=f"/{key}",
                        remedy="Compare with the shipped learningos.config.json.",
                    )

    # -- global state ------------------------------------------------------------------

    def check_global(self) -> None:
        state = self.load_and_validate(self.ws.workspace_state_file, "workspace_state")
        for name in ("learner_profile.md", "learner_index.md", "active_priorities.md"):
            path = self.ws.memory_global / name
            if not path.is_file():
                self.add(
                    "warning",
                    "missing_global_file",
                    f"{name} is missing from 03_MEMORY/global/",
                    path=self.rel(path),
                    remedy="Run `init-workspace` to restore the template.",
                )
        preferences_path = self.ws.learning_preferences_file
        if preferences_path.is_file():
            self.load_and_validate(preferences_path, "learning_preferences")
        else:
            self.add(
                "warning",
                "missing_global_file",
                "learning_preferences.json is missing; defaults will be assumed",
                path=self.rel(preferences_path),
                remedy="Run `init-workspace` to restore the template.",
            )
        if not isinstance(state, dict):
            return
        registered = {
            str(entry.get("subject_id"))
            for entry in state.get("subjects", [])
            if isinstance(entry, dict)
        }
        on_disk = set(self.ws.list_subjects())
        for subject_id in sorted(registered - on_disk):
            self.add(
                "error",
                "registered_subject_missing",
                f"workspace_state.json registers subject {subject_id!r} but its directories do "
                "not exist",
                path=self.rel(self.ws.workspace_state_file),
                pointer="/subjects",
                remedy=f'Run `init-subject --id {subject_id} --name "..."` to recreate it, or '
                "remove the entry by hand.",
            )
        for subject_id in sorted(on_disk - registered):
            self.add(
                "warning",
                "subject_not_registered",
                f"subject {subject_id!r} exists on disk but is not registered in "
                "workspace_state.json",
                path=self.rel(self.ws.workspace_state_file),
                pointer="/subjects",
                remedy=f'Run `init-subject --id {subject_id} --name "..."` — it is idempotent and '
                "will only add the registration.",
            )
        current = state.get("current_subject_id")
        if isinstance(current, str) and current and current not in on_disk:
            self.add(
                "warning",
                "current_subject_missing",
                f"current_subject_id is {current!r}, which does not exist on disk",
                path=self.rel(self.ws.workspace_state_file),
                pointer="/current_subject_id",
                remedy="Set it to an existing subject or null.",
            )

    # -- index building ----------------------------------------------------------------

    def subject_dirs(self) -> list[str]:
        """Every subject-ish directory name on either side, template scaffolding included."""
        names: set[str] = set()
        for key in ("memory_subjects", "knowledge_subjects"):
            for directory in self.ws.list_subject_dirs(key):
                if not SUBJECT_ID_RE.fullmatch(directory.name) and directory.name != "_template":
                    # Never pretend an uninitialized source drop folder is a subject.
                    # If it holds canonical state, retain full corruption diagnostics.
                    if not any((directory / marker).exists() for marker in ("subject_config.json", "concept_catalog.json", "concept_mastery.json", "subject_state.json")):
                        continue
                names.add(directory.name)
        return sorted(names)

    def build_index(self) -> None:
        """Validate the mastery and catalogue files and record which ids exist (checks (c), (d))."""
        for subject_id in self.subject_dirs():
            mastery_path = store.concept_mastery_path(self.ws, subject_id)
            if mastery_path.is_file():
                data = self.read_json(mastery_path)
                if isinstance(data, dict):
                    findings = store.concept_mastery_findings(
                        self.ws,
                        data,
                        subject_id=subject_id,
                        path=self.rel(mastery_path),
                        strict=self.strict,
                    )
                    self.findings.extend(findings)
                    concepts = data.get("concepts")
                    if isinstance(concepts, dict):
                        for key, record in concepts.items():
                            if not isinstance(key, str):
                                continue
                            previous = self.index.records.get(key)
                            if previous is not None and previous != subject_id:
                                self.add(
                                    "error",
                                    "duplicate_concept_id",
                                    f"concept {key!r} has a record in both {previous!r} and "
                                    f"{subject_id!r}",
                                    path=self.rel(mastery_path),
                                    pointer=f"/concepts/{escape_pointer_token(key)}",
                                    remedy="A concept id belongs to exactly one subject. Delete "
                                    "the copy in the subject that does not own the prefix.",
                                )
                            self.index.records[key] = subject_id
            catalog_path = self.ws.knowledge_subject(subject_id) / "concept_catalog.json"
            if catalog_path.is_file():
                data = self.load_and_validate(catalog_path, "concept_catalog")
                if isinstance(data, dict):
                    self._index_catalog(subject_id, catalog_path, data)
            misconceptions_path = store.misconceptions_path(self.ws, subject_id)
            if misconceptions_path.is_file():
                data = self.load_and_validate(misconceptions_path, "misconceptions")
                if isinstance(data, dict):
                    for entry in data.get("misconceptions", []):
                        if isinstance(entry, dict):
                            mid = entry.get("misconception_id")
                            if isinstance(mid, str):
                                self.index.misconceptions[mid] = subject_id

    def _index_catalog(self, subject_id: str, path: Path, data: dict[str, Any]) -> None:
        seen: set[str] = set()
        for position, entry in enumerate(data.get("concepts", [])):
            if not isinstance(entry, dict):
                continue
            concept_id = entry.get("concept_id")
            if not isinstance(concept_id, str):
                continue
            pointer = f"/concepts/{position}/concept_id"
            if concept_id in seen:
                self.add(
                    "error",
                    "duplicate_catalog_entry",
                    f"concept {concept_id!r} appears more than once in the catalogue",
                    path=self.rel(path),
                    pointer=pointer,
                    remedy="Remove the duplicate entry; `init-subject` never merges into an "
                    "existing catalogue, so duplicates are hand-edits.",
                )
            seen.add(concept_id)
            prefix = concept_id.split(":", 1)[0]
            if prefix != subject_id:
                self.add(
                    "error",
                    "cross_subject_catalog_entry",
                    f"catalogued concept {concept_id!r} belongs to subject {prefix!r} but is "
                    f"listed in {subject_id!r}'s catalogue",
                    path=self.rel(path),
                    pointer=pointer,
                    remedy="Concept ids are prefixed with their owning subject; move or rename "
                    "the entry.",
                )
            self.index.catalogued.setdefault(concept_id, subject_id)

    # -- per-subject checks ------------------------------------------------------------

    def check_subjects(self) -> None:
        wanted = self.subject_dirs()
        if self.subject is not None:
            if self.subject not in wanted:
                raise LearningOSUsageError(
                    f"unknown subject {self.subject!r}",
                    remedy="Known subjects: " + (", ".join(self.ws.list_subjects()) or "(none)"),
                )
            wanted = [self.subject]
        for subject_id in wanted:
            self.check_subject(subject_id)

    def check_subject(self, subject_id: str) -> None:
        memory_dir = self.ws.memory_subject(subject_id)
        knowledge_dir = self.ws.knowledge_subject(subject_id)
        if memory_dir.is_dir() != knowledge_dir.is_dir():
            missing = knowledge_dir if memory_dir.is_dir() else memory_dir
            self.add(
                "warning",
                "half_a_subject",
                f"subject {subject_id!r} exists on only one side; {self.rel(missing)} is missing",
                path=self.rel(missing),
                remedy=f'Run `init-subject --id {subject_id} --name "..."` — it is idempotent and '
                "creates only what is absent.",
            )
        for name, schema_name in store.REQUIRED_SUBJECT_MEMORY_FILES.items():
            path = memory_dir / name
            if not path.is_file():
                if memory_dir.is_dir():
                    self.add(
                        "error",
                        "missing_file",
                        f"required file is missing for subject {subject_id!r}",
                        path=self.rel(path),
                        remedy=f'Run `init-subject --id {subject_id} --name "..."` (it never '
                        "overwrites surviving files) or `restore --from <backup>`.",
                    )
                continue
            if schema_name == "concept_mastery":
                continue  # already validated while building the index
            self.load_and_validate(path, schema_name)
        for name, schema_name in store.DERIVED_SUBJECT_MEMORY_FILES.items():
            path = memory_dir / name
            if not path.is_file():
                if memory_dir.is_dir():
                    self.add(
                        "warning",
                        "missing_derived_file",
                        f"{name} is missing for subject {subject_id!r}; treated as empty",
                        path=self.rel(path),
                        remedy="Regenerate it with `reconcile` / `update-graphs`, or restore the "
                        "template with `init-subject`.",
                    )
                continue
            if schema_name == "misconceptions":
                continue  # already validated while building the index
            self.load_and_validate(path, schema_name)
        for name, schema_name in store.SUBJECT_KNOWLEDGE_FILES.items():
            path = knowledge_dir / name
            if not path.is_file():
                if knowledge_dir.is_dir():
                    severity = "error" if name == "subject_config.json" else "warning"
                    self.add(
                        severity,
                        "missing_file",
                        f"{name} is missing for subject {subject_id!r}",
                        path=self.rel(path),
                        remedy=f'Run `init-subject --id {subject_id} --name "..."`.',
                    )
                continue
            if schema_name == "concept_catalog":
                continue  # already validated while building the index
            self.load_and_validate(path, schema_name)

        self.check_subject_identity(subject_id)
        self.check_prerequisites(subject_id)
        self.check_concept_graph(subject_id)
        self.check_misconception_links(subject_id)
        self.check_revision_queue(subject_id)
        self.check_checkpoints(subject_id)

    def check_subject_identity(self, subject_id: str) -> None:
        """Every file that carries a ``subject_id`` must agree with its directory (check (d))."""
        candidates = [
            (self.ws.memory_subject(subject_id) / name, name)
            for name in store.SUBJECT_MEMORY_FILES
        ] + [
            (self.ws.knowledge_subject(subject_id) / name, name)
            for name in store.SUBJECT_KNOWLEDGE_FILES
        ]
        for path, _name in candidates:
            if not path.is_file():
                continue
            data = self.read_json(path)
            if not isinstance(data, dict):
                continue
            found = data.get("subject_id")
            if found != subject_id:
                self.add(
                    "error",
                    "subject_mismatch",
                    f"subject_id is {found!r} but the owning directory is {subject_id!r}",
                    path=self.rel(path),
                    pointer="/subject_id",
                    remedy="Correct subject_id or move the file. The directory name is "
                    "authoritative, and it is also the prefix of every concept id in the subject.",
                )

    def check_prerequisites(self, subject_id: str) -> None:
        """Check (e): prerequisites resolve, in this subject or another one that really has them."""
        mastery_path = store.concept_mastery_path(self.ws, subject_id)
        data = self._json_cache.get(mastery_path)
        if isinstance(data, dict):
            concepts = data.get("concepts")
            if isinstance(concepts, dict):
                for key, record in concepts.items():
                    if not isinstance(record, dict):
                        continue
                    for position, prerequisite in enumerate(record.get("prerequisite_ids") or []):
                        self._check_reference(
                            prerequisite,
                            path=mastery_path,
                            pointer=f"/concepts/{escape_pointer_token(str(key))}"
                            f"/prerequisite_ids/{position}",
                            owner=key,
                        )
        catalog_path = self.ws.knowledge_subject(subject_id) / "concept_catalog.json"
        catalog = self._json_cache.get(catalog_path)
        if isinstance(catalog, dict):
            for position, entry in enumerate(catalog.get("concepts", [])):
                if not isinstance(entry, dict):
                    continue
                for index, prerequisite in enumerate(entry.get("prerequisite_ids") or []):
                    self._check_reference(
                        prerequisite,
                        path=catalog_path,
                        pointer=f"/concepts/{position}/prerequisite_ids/{index}",
                        owner=str(entry.get("concept_id")),
                    )

    def _check_reference(self, concept_id: Any, *, path: Path, pointer: str, owner: str) -> None:
        if not isinstance(concept_id, str):
            return
        if self.index.has_record(concept_id):
            return
        if concept_id in self.index.catalogued:
            self.add(
                "warning",
                "prerequisite_not_seeded",
                f"{owner} requires {concept_id}, which is catalogued but has no concept record yet",
                path=self.rel(path),
                pointer=pointer,
                remedy="Seed the record at status `unassessed` so progress can be tracked, or "
                "drop the prerequisite.",
            )
            return
        self.add(
            "error",
            "dangling_prerequisite",
            f"{owner} requires {concept_id}, which does not exist in any subject",
            path=self.rel(path),
            pointer=pointer,
            remedy=f"Create {concept_id} in subject "
            f"{concept_id.split(':', 1)[0]!r}, or remove the prerequisite. Cross-subject "
            "prerequisites are allowed, but only to concepts that really exist.",
        )

    def check_concept_graph(self, subject_id: str) -> None:
        """Check (f): a dangling edge endpoint is an error, quoting both ends."""
        path = store.concept_graph_path(self.ws, subject_id)
        data = self._json_cache.get(path) if path.is_file() else None
        if data is None and path.is_file():
            data = self.read_json(path)
        if not isinstance(data, dict):
            return
        for position, node in enumerate(data.get("nodes", [])):
            if not isinstance(node, dict):
                continue
            concept_id = node.get("concept_id")
            if isinstance(concept_id, str) and not self.index.knows(concept_id):
                self.add(
                    "error",
                    "dangling_graph_node",
                    f"concept_graph node {concept_id!r} does not exist in any subject",
                    path=self.rel(path),
                    pointer=f"/nodes/{position}/concept_id",
                    remedy=f"Create the concept, or remove the node. Graph endpoints are checked "
                    "before any graph artefact is written.",
                )
        for position, edge in enumerate(data.get("edges", [])):
            if not isinstance(edge, dict):
                continue
            source = edge.get("from")
            target = edge.get("to")
            for role, value in (("from", source), ("to", target)):
                if isinstance(value, str) and not self.index.knows(value):
                    self.add(
                        "error",
                        "dangling_edge",
                        f"edge {source!r} -> {target!r} has a dangling {role} endpoint: "
                        f"{value!r} does not exist in any subject",
                        path=self.rel(path),
                        pointer=f"/edges/{position}/{role}",
                        remedy=f"Create {value!r} or delete the edge. `update-graphs` refuses to "
                        "write anything while an endpoint is dangling.",
                    )

    def check_misconception_links(self, subject_id: str) -> None:
        """Check (g): concept -> misconception and misconception -> concept both resolve."""
        misconceptions_path = store.misconceptions_path(self.ws, subject_id)
        data = self._json_cache.get(misconceptions_path)
        if isinstance(data, dict):
            for position, entry in enumerate(data.get("misconceptions", [])):
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("misconception_id")
                if isinstance(mid, str):
                    prefix = mid.split(":", 1)[0]
                    if prefix != subject_id:
                        self.add(
                            "error",
                            "cross_subject_misconception",
                            f"misconception {mid!r} is filed under subject {subject_id!r}",
                            path=self.rel(misconceptions_path),
                            pointer=f"/misconceptions/{position}/misconception_id",
                            remedy=f"Use the {subject_id}:mc-NNN form, or move the record to "
                            f"subject {prefix!r}.",
                        )
                for index, concept_id in enumerate(entry.get("concept_ids") or []):
                    if isinstance(concept_id, str) and not self.index.knows(concept_id):
                        self.add(
                            "error",
                            "dangling_misconception_concept",
                            f"misconception {mid!r} points at concept {concept_id!r}, which does "
                            "not exist",
                            path=self.rel(misconceptions_path),
                            pointer=f"/misconceptions/{position}/concept_ids/{index}",
                            remedy="Create the concept or remove the link.",
                        )
        mastery_path = store.concept_mastery_path(self.ws, subject_id)
        mastery = self._json_cache.get(mastery_path)
        if isinstance(mastery, dict):
            concepts = mastery.get("concepts")
            if isinstance(concepts, dict):
                for key, record in concepts.items():
                    if not isinstance(record, dict):
                        continue
                    for index, mid in enumerate(record.get("misconception_ids") or []):
                        if not isinstance(mid, str):
                            continue
                        if mid not in self.index.misconceptions:
                            self.add(
                                "error",
                                "dangling_misconception",
                                f"concept {key} links misconception {mid!r}, which has no record "
                                f"in {self.rel(misconceptions_path)}",
                                path=self.rel(mastery_path),
                                pointer=f"/concepts/{escape_pointer_token(str(key))}"
                                f"/misconception_ids/{index}",
                                remedy="Add the misconception record, or remove the link. A named "
                                "misconception with no record cannot be recognised next session.",
                            )

    def check_revision_queue(self, subject_id: str) -> None:
        path = store.revision_queue_path(self.ws, subject_id)
        data = self._json_cache.get(path)
        if not isinstance(data, dict):
            return
        for position, item in enumerate(data.get("items", [])):
            if not isinstance(item, dict):
                continue
            concept_id = item.get("concept_id")
            if isinstance(concept_id, str) and not self.index.knows(concept_id):
                self.add(
                    "warning",
                    "stale_revision_item",
                    f"revision queue item {concept_id!r} has no concept record",
                    path=self.rel(path),
                    pointer=f"/items/{position}/concept_id",
                    remedy="The queue is derived state: `reconcile` rebuilds it from the concept "
                    "records.",
                )

    def check_checkpoints(self, subject_id: str) -> None:
        directory = self.ws.checkpoint_dir(subject_id)
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            data = self.load_and_validate(path, "checkpoint")
            if not isinstance(data, dict):
                continue
            if data.get("subject_id") != subject_id:
                self.add(
                    "error",
                    "subject_mismatch",
                    f"checkpoint declares subject {data.get('subject_id')!r} but lives in "
                    f"{subject_id!r}'s checkpoint directory",
                    path=self.rel(path),
                    pointer="/subject_id",
                    remedy="Move the file to the right subject directory.",
                )

    # -- (h) event log -----------------------------------------------------------------

    def check_event_log(self) -> None:
        if not self.ws.event_log.is_file():
            return
        log_relative = self.rel(self.ws.event_log)
        parse_findings: list[Finding] = []
        known_subjects = set(self.subject_dirs())
        for line_no, event in events_mod.iter_events(self.ws, findings=parse_findings):
            where = f"{log_relative}:{line_no}"
            findings = self.registry.validate(
                "session_event", event, path=where, strict=self.strict
            )
            for finding in findings:
                self.findings.append(
                    Finding(
                        severity="error",
                        code="event_schema",
                        message=finding.message,
                        path=where,
                        pointer=finding.pointer,
                        remedy="Events are append-only: the line was left exactly as it is. "
                        "Append a correcting event rather than editing history.",
                    )
                )
            subject_id = event.get("subject_id")
            if isinstance(subject_id, str) and subject_id and subject_id not in known_subjects:
                self.add(
                    "warning",
                    "event_unknown_subject",
                    f"line {line_no} references subject {subject_id!r}, which no longer exists",
                    path=where,
                    remedy="Expected if a subject was archived. The history is kept on purpose.",
                )
            for concept_id in event.get("concept_ids") or []:
                if isinstance(concept_id, str) and not self.index.knows(concept_id):
                    self.add(
                        "warning",
                        "event_unknown_concept",
                        f"line {line_no} references concept {concept_id!r}, which no longer exists",
                        path=where,
                        remedy="Expected after a rename. Historical events are never rewritten.",
                    )
        self.findings.extend(parse_findings)

    # -- (i) graph artefacts -----------------------------------------------------------

    def check_graph_artifacts(self) -> None:
        if not self.ws.graphs.is_dir():
            return
        for path in sorted(self.ws.graphs.rglob("*.json")):
            data = self.load_and_validate(path, "graph_artifact", severity="warning")
            if not isinstance(data, dict):
                continue
            for position, node in enumerate(data.get("nodes", [])):
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if not isinstance(node_id, str):
                    continue
                if MISCONCEPTION_ID_RE.match(node_id):
                    if node_id not in self.index.misconceptions:
                        self.add(
                            "warning",
                            "graph_stale_reference",
                            f"graph node {node_id!r} has no misconception record any more",
                            path=self.rel(path),
                            pointer=f"/nodes/{position}/id",
                            remedy="Regenerate with `update-graphs`; graph artefacts are derived.",
                        )
                elif CONCEPT_ID_RE.match(node_id) and not self.index.knows(node_id):
                    self.add(
                        "warning",
                        "graph_stale_reference",
                        f"graph node {node_id!r} has no concept record any more",
                        path=self.rel(path),
                        pointer=f"/nodes/{position}/id",
                        remedy="Regenerate with `update-graphs`; graph artefacts are derived.",
                    )

    # -- (j) adapters ------------------------------------------------------------------

    def check_adapters(self) -> None:
        for relative in ADAPTER_FILES:
            path = self.ws.root / relative
            if not path.is_file():
                self.add(
                    "error",
                    "missing_adapter_file",
                    f"{relative} is missing, so an agent cannot discover the instructions",
                    path=relative,
                    remedy="Restore it from your copy of LearningOS, or run `init-workspace`.",
                )
                continue
            text = read_text(path)
            for mentioned in sorted(set(DOC_PATH_RE.findall(text))):
                if "*" in mentioned:
                    continue
                if not (self.ws.root / mentioned).exists():
                    self.add(
                        "error",
                        "documented_path_missing",
                        f"{relative} points at {mentioned}, which does not exist",
                        path=relative,
                        remedy="Create the file or fix the reference. An instruction file that "
                        "names a missing path sends the agent somewhere that is not there.",
                    )
        steering = self.ws.root / "01_AGENTS_AND_SKILLS/adapters/kiro/steering/learningos.md"
        if steering.is_file():
            head = read_text(steering).splitlines()[:5]
            if not any("inclusion: always" in line for line in head):
                self.add(
                    "warning",
                    "steering_front_matter",
                    "the Kiro steering file has no `inclusion: always` front matter, so Kiro will "
                    "not load it automatically",
                    path=self.rel(steering),
                    remedy="Put the front-matter block at the very top of the file.",
                )
            installed = self.ws.root / ".kiro" / "steering" / "learningos.md"
            if installed.is_file() and sha256_file(installed) != sha256_file(steering):
                self.add(
                    "warning",
                    "steering_copy_stale",
                    ".kiro/steering/learningos.md differs from the adapter source",
                    path=".kiro/steering/learningos.md",
                    remedy="Re-run the copy command in "
                    "01_AGENTS_AND_SKILLS/adapters/kiro/README.md.",
                )

    # -- (k) drift ---------------------------------------------------------------------

    def check_drift(self) -> None:
        """Warn when the event log has evidence newer than the snapshot that should reflect it."""
        subjects = (
            [self.subject] if self.subject is not None else self.ws.list_subjects()
        )
        for subject_id in subjects:
            newest = events_mod.newest_timestamp(
                self.ws, subject_id=subject_id, types=STATE_CHANGING_EVENTS
            )
            if newest is None:
                continue
            for name in ("subject_state.json", "concept_mastery.json"):
                path = self.ws.memory_subject(subject_id) / name
                data = self._json_cache.get(path)
                if not isinstance(data, dict):
                    continue
                updated_at = data.get("updated_at")
                if isinstance(updated_at, str) and newest > updated_at:
                    self.add(
                        "warning",
                        "stale_state",
                        f"the newest evidence for {subject_id!r} is {newest} but {name} was last "
                        f"updated at {updated_at}",
                        path=self.rel(path),
                        pointer="/updated_at",
                        remedy="Run `reconcile --subject "
                        f"{subject_id} --dry-run` to see the drift, then `reconcile --apply` "
                        "(it takes a backup first).",
                    )

    # -- (l) stored paths --------------------------------------------------------------

    def check_stored_paths(self) -> None:
        """No absolute or drive-letter path may appear in a stored value.

        Stored paths are workspace-relative with forward slashes, which is what lets the whole
        folder be copied to another machine — and what stops a memory file from leaking the
        learner's directory layout.
        """
        for path in self._iter_state_json_files():
            data = self._json_cache.get(path)
            if data is None:
                data = self.read_json(path)
            if data is None:
                continue
            for pointer, value in iter_json_strings(data):
                if looks_absolute(value):
                    self.add(
                        "error",
                        "absolute_path_stored",
                        f"value looks like an absolute filesystem path: {truncate(value, 80)}",
                        path=self.rel(path),
                        pointer=pointer,
                        remedy="Store workspace-relative paths with forward slashes, e.g. "
                        "02_KNOWLEDGE/subjects/<id>/source_materials/notes.pdf.",
                    )

    def _iter_state_json_files(self) -> Iterator[Path]:
        if self.ws.memory.is_dir():
            for path in sorted(self.ws.memory.rglob("*.json")):
                if self.ws.backups in path.parents:
                    continue
                yield path
        if self.ws.knowledge_subjects.is_dir():
            for path in sorted(self.ws.knowledge_subjects.rglob("*.json")):
                yield path

    # -- (b) sweep + schema coverage ---------------------------------------------------

    def check_remaining_json(self) -> None:
        """Parse every JSON file not already visited, so check (b) really covers everything."""
        for path in self._iter_state_json_files():
            if path not in self._visited:
                self.read_json(path)
        if self.ws.backups.is_dir():
            for path in sorted(self.ws.backups.glob("*.manifest.json")):
                self.load_and_validate(path, "backup_manifest", severity="warning")

    def check_schema_support(self) -> None:
        """Guard against the schemas outgrowing the bundled validator."""
        try:
            names = self.registry.names
        except LearningOSDataError as exc:
            self.add(
                "error",
                exc.code,
                exc.message,
                path=exc.path,
                remedy=exc.remedy,
            )
            return
        for name in names:
            unsupported = self.registry.unsupported_keywords(name)
            if unsupported:
                self.add(
                    "warning",
                    "schema_keyword_unsupported",
                    f"{name}.schema.json uses {', '.join(sorted(unsupported))}, which the bundled "
                    "validator ignores",
                    path=self.rel(self.ws.schemas / f"{name}.schema.json"),
                    remedy="Either keep the schema inside the supported subset or extend "
                    "tools/schema.py; an ignored keyword is an unenforced rule.",
                )

    # -- orchestration -----------------------------------------------------------------

    def run(self) -> list[Finding]:
        self.check_schema_support()
        self.check_structure()
        for directory in self.ws.list_subject_dirs("knowledge_subjects"):
            if not SUBJECT_ID_RE.fullmatch(directory.name) and directory.name != "_template":
                self.add("warning", "unassigned_source_folder",
                         "This folder name is not a valid subject id. Its contents are preserved as unassigned material, not treated as learner state.",
                         path=self.rel(directory),
                         remedy="Use the LearningOS menu to add its documents to a valid subject. Preserve the originals; do not initialize a subject id containing spaces.")
        self.build_index()
        self.check_global()
        self.check_subjects()
        self.check_event_log()
        from learning_method import audit
        self.findings.extend(audit(self.ws))
        self.check_graph_artifacts()
        self.check_adapters()
        self.check_drift()
        self.check_remaining_json()
        self.check_stored_paths()
        return sort_findings(self.findings)


def validate_workspace(
    ws: Workspace,
    *,
    subject: str | None = None,
    strict: bool = False,
) -> list[Finding]:
    """Run every check and return the findings, errors first."""
    return WorkspaceValidator(ws, subject=subject, strict=strict).run()


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(finding.is_error for finding in findings)
