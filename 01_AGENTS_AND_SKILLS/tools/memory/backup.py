"""Backing up and restoring the learner's memory.

A backup is a plain zip plus a sibling manifest listing every file with its sha256. Plain, because
the archive has to be readable in five years by something that is not this script, and because the
learner must be able to open it and see their own files.

The safety rules are the whole point of this module:

* **restore verifies before it writes.** Every sha256 in the manifest is checked against the zip
  first; one mismatch means exit 3 and an untouched workspace. A half-restored memory tree is
  worse than no restore at all.
* **path traversal is rejected outright.** A zip entry with an absolute path, a drive letter or a
  ``..`` segment aborts the whole operation (exit 3) before a single byte is extracted.
* **overwriting needs ``--force``.** Restoring onto a tree that still has files refuses with
  exit 5 and changes nothing; with ``--force`` a pre-restore safety backup is taken first, so the
  state you are about to replace is itself recoverable.
* **``--dry-run`` writes nothing at all**, including no event and no safety backup.

Extraction is per-file: temp file in the destination directory, then ``os.replace``, so an
interrupted restore leaves whole files rather than truncated ones.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from common import (
    SCHEMA_VERSION,
    TOOL_ID,
    Finding,
    LearningOSDataError,
    LearningOSNotFoundError,
    LearningOSRefusedError,
    LearningOSUsageError,
    MARKER_FILE,
    Workspace,
    atomic_write_json,
    ensure_dir,
    parse_timestamp,
    read_json,
    sha256_bytes,
    sha256_file,
    utc_now,
    utc_now_iso,
)
from memory import events as events_mod
from memory import store

#: ``memory-<YYYYMMDD>T<HHMMSS>Z`` — the id is also the zip and manifest filename stem.
BACKUP_ID_RE = re.compile(r"^memory-[0-9]{8}T[0-9]{6}Z$")

#: Knowledge-side files that describe the subject rather than being the subject's source material.
KNOWLEDGE_FILES = (
    "subject_config.json",
    "source_index.json",
    "concept_catalog.json",
    "syllabus_map.md",
    "concept_catalog.md",
)

#: Never archived: previous backups (recursion) and the regenerable search cache.
SKIPPED_MEMORY_NAMES = ("search_index.sqlite3",)
SKIPPED_PARTS = ("__pycache__", ".pytest_cache")


def backups_dir(ws: Workspace) -> Path:
    return ws.backups


def new_backup_id(*, now: str | None = None) -> str:
    moment = parse_timestamp(now) if now else utc_now()
    return "memory-" + moment.strftime("%Y%m%dT%H%M%SZ")


def free_backup_id(ws: Workspace, *, now: str | None = None) -> str:
    """A backup id that is not already in use on disk.

    The id has second resolution because it has to stay human-readable and match the schema
    pattern, so two backups taken in the same second would otherwise collide — and a backup that
    silently overwrites another backup is data loss, not a naming detail. This walks forward one
    second at a time until the pair of filenames is free.
    """
    moment = parse_timestamp(now) if now else utc_now()
    for _ in range(86400):
        candidate = "memory-" + moment.strftime("%Y%m%dT%H%M%SZ")
        if not zip_path_for(ws, candidate).exists() and not manifest_path_for(
            ws, candidate
        ).exists():
            return candidate
        moment += _one_second()
    raise LearningOSRefusedError(  # pragma: no cover - a day's worth of ids in one directory
        "could not find a free backup id",
        path=ws.rel(backups_dir(ws)),
        remedy="Move older archives out of 03_MEMORY/backups/ yourself; LearningOS never deletes "
        "a backup.",
    )


def _one_second():
    import datetime as dt

    return dt.timedelta(seconds=1)


def zip_path_for(ws: Workspace, backup_id: str) -> Path:
    return backups_dir(ws) / f"{backup_id}.zip"


def manifest_path_for(ws: Workspace, backup_id: str) -> Path:
    return backups_dir(ws) / f"{backup_id}.manifest.json"


# ----------------------------------------------------------------------------------- planning


@dataclass
class BackupPlan:
    """Exactly what would go into the archive, so ``--dry-run`` can print the truth."""

    files: list[tuple[Path, str]] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def file_count(self) -> int:
        return len(self.files)


def _skip(path: Path) -> bool:
    if any(part in SKIPPED_PARTS for part in path.parts):
        return True
    if path.name in SKIPPED_MEMORY_NAMES:
        return True
    return path.name.endswith(".tmp")


def plan_backup(ws: Workspace, *, include_sources: bool = False) -> BackupPlan:
    """Collect the files to archive: all of 03_MEMORY except backups, plus subject descriptions."""
    plan = BackupPlan()
    seen: set[str] = set()

    def add(path: Path) -> None:
        if not path.is_file() or _skip(path):
            return
        relative = ws.rel(path)
        if relative in seen:
            return
        seen.add(relative)
        plan.files.append((path, relative))
        plan.total_bytes += path.stat().st_size

    if ws.memory.is_dir():
        plan.includes.append(ws.rel(ws.memory))
        for path in sorted(ws.memory.rglob("*")):
            if backups_dir(ws) == path or backups_dir(ws) in path.parents:
                continue
            add(path)
    if ws.marker.is_file():
        plan.includes.append(MARKER_FILE)
        add(ws.marker)
    if ws.knowledge_subjects.is_dir():
        plan.includes.append(ws.rel(ws.knowledge_subjects))
        for subject_dir in sorted(ws.knowledge_subjects.iterdir()):
            if not subject_dir.is_dir():
                continue
            for name in KNOWLEDGE_FILES:
                add(subject_dir / name)
            if include_sources:
                sources = subject_dir / "source_materials"
                if sources.is_dir():
                    for path in sorted(sources.rglob("*")):
                        add(path)
    if not plan.includes:  # pragma: no cover - a workspace always has 03_MEMORY
        plan.includes.append(ws.rel(ws.memory))
    plan.files.sort(key=lambda item: item[1])
    return plan


# ------------------------------------------------------------------------------------- backup


@dataclass
class BackupResult:
    backup_id: str
    zip_path: Path | None
    manifest_path: Path | None
    manifest: dict[str, Any]
    plan: BackupPlan
    dry_run: bool

    def to_dict(self, ws: Workspace) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "dry_run": self.dry_run,
            "zip": ws.rel(self.zip_path) if self.zip_path else None,
            "manifest": ws.rel(self.manifest_path) if self.manifest_path else None,
            "file_count": self.plan.file_count,
            "total_bytes": self.plan.total_bytes,
            "includes": self.manifest.get("includes", []),
            "files": [relative for _path, relative in self.plan.files],
        }


def build_manifest(
    ws: Workspace,
    plan: BackupPlan,
    *,
    backup_id: str,
    note: str | None,
    include_sources: bool,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Hash every file now, so `restore` can prove the archive is intact later."""
    return {
        "schema_version": SCHEMA_VERSION,
        "backup_id": backup_id,
        "created_at": created_at or utc_now_iso(),
        "tool_version": TOOL_ID,
        "note": note,
        "includes": sorted(set(plan.includes)),
        "include_sources": bool(include_sources),
        "file_count": plan.file_count,
        "total_bytes": plan.total_bytes,
        "files": [
            {
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path, relative in plan.files
        ],
    }


def create_backup(
    ws: Workspace,
    *,
    note: str | None = None,
    include_sources: bool = False,
    dry_run: bool = False,
    session_id: str | None = None,
    record_event: bool = True,
) -> BackupResult:
    """Write ``03_MEMORY/backups/<backup_id>.zip`` and its manifest. Nothing is ever moved."""
    plan = plan_backup(ws, include_sources=include_sources)
    backup_id = free_backup_id(ws)
    manifest = build_manifest(
        ws, plan, backup_id=backup_id, note=note, include_sources=include_sources
    )
    if dry_run:
        return BackupResult(backup_id, None, None, manifest, plan, True)

    store.registry(ws).validate_or_raise(
        "backup_manifest", manifest, path=ws.rel(manifest_path_for(ws, backup_id))
    )
    ensure_dir(backups_dir(ws))
    target = zip_path_for(ws, backup_id)
    temporary = target.with_suffix(".zip.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, relative in plan.files:
                archive.write(path, arcname=relative)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    atomic_write_json(manifest_path_for(ws, backup_id), manifest)

    if record_event:
        events_mod.append_event(
            ws,
            type="memory_backup",
            session_id=session_id or f"{utc_now_iso()[:10]}-backup",
            subject_id=None,
            actor="tool",
            payload={
                "backup_id": backup_id,
                "relative_path": ws.rel(target),
                "file_count": plan.file_count,
                "total_bytes": plan.total_bytes,
                "include_sources": bool(include_sources),
                "note": note,
            },
        )
        state = store.load_workspace_state(ws, required=False)
        if state is not None:
            state["last_backup_id"] = backup_id
            store.save_workspace_state(ws, state)
    return BackupResult(backup_id, target, manifest_path_for(ws, backup_id), manifest, plan, False)


def list_backups(ws: Workspace) -> list[str]:
    """Backup ids present on disk, oldest first (the id sorts chronologically)."""
    if not backups_dir(ws).is_dir():
        return []
    return sorted(
        path.stem for path in backups_dir(ws).glob("*.zip") if BACKUP_ID_RE.match(path.stem)
    )


# ------------------------------------------------------------------------------------ restore


def resolve_source(ws: Workspace, source: str) -> tuple[Path, Path]:
    """Accept a backup id or a path to a zip. Returns ``(zip_path, manifest_path)``."""
    candidate = Path(source).expanduser()
    if BACKUP_ID_RE.match(source):
        zip_path = zip_path_for(ws, source)
        manifest = manifest_path_for(ws, source)
    elif candidate.suffix == ".zip":
        zip_path = candidate if candidate.is_absolute() else (Path.cwd() / candidate)
        manifest = zip_path.with_name(zip_path.stem + ".manifest.json")
    else:
        raise LearningOSUsageError(
            f"--from must be a backup id (memory-20260211T091400Z) or a path to a .zip, "
            f"got {source!r}",
            remedy="Available backups: " + (", ".join(list_backups(ws)) or "(none yet)"),
        )
    if not zip_path.is_file():
        raise LearningOSNotFoundError(
            f"backup archive not found: {zip_path}",
            remedy="Available backups: " + (", ".join(list_backups(ws)) or "(none yet)")
            + ". Run `backup` to create one.",
        )
    if not manifest.is_file():
        raise LearningOSDataError(
            f"backup manifest not found beside the archive: {manifest.name}",
            path=manifest.name,
            remedy="restore verifies every file's sha256 before writing, so it will not restore "
            "an archive it cannot verify. Keep the .manifest.json next to its .zip.",
            code="missing_manifest",
        )
    return zip_path, manifest


def unsafe_entry_names(names: Iterable[str]) -> list[str]:
    """Zip entries that must never be extracted: absolute, drive-lettered, or escaping with ``..``.

    Checked for the **whole archive** before anything is written, so a single poisoned entry
    cannot result in a partially extracted restore.
    """
    unsafe: list[str] = []
    for name in names:
        normalised = name.replace("\\", "/")
        if not normalised or normalised.startswith("/"):
            unsafe.append(name)
            continue
        if re.match(r"^[A-Za-z]:", normalised):
            unsafe.append(name)
            continue
        if any(part == ".." for part in normalised.split("/")):
            unsafe.append(name)
    return unsafe


def verify_archive(zip_path: Path, manifest: dict[str, Any]) -> list[Finding]:
    """Check the archive against its manifest. An empty list means it is safe to extract."""
    findings: list[Finding] = []
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        unsafe = unsafe_entry_names(names)
        if unsafe:
            findings.append(
                Finding(
                    severity="error",
                    code="unsafe_zip_entry",
                    message=f"archive contains {len(unsafe)} unsafe path(s), first: {unsafe[0]!r}",
                    path=zip_path.name,
                    remedy="Refusing to extract an archive that tries to write outside the "
                    "workspace. Nothing was written. Inspect the zip by hand.",
                )
            )
            return findings
        present = set(names)
        for entry in manifest.get("files", []):
            if not isinstance(entry, dict):
                continue
            relative = str(entry.get("relative_path"))
            if relative not in present:
                findings.append(
                    Finding(
                        severity="error",
                        code="backup_file_missing",
                        message=f"the manifest lists {relative} but the archive does not contain it",
                        path=zip_path.name,
                        remedy="The archive is incomplete. Use a different backup; nothing was "
                        "written.",
                    )
                )
                continue
            data = archive.read(relative)
            actual = sha256_bytes(data)
            if actual != str(entry.get("sha256")):
                findings.append(
                    Finding(
                        severity="error",
                        code="backup_hash_mismatch",
                        message=f"{relative} does not match its manifest sha256 "
                        f"(expected {str(entry.get('sha256'))[:12]}…, got {actual[:12]}…)",
                        path=zip_path.name,
                        remedy="The archive is corrupt or was tampered with. Nothing was written. "
                        "Use another backup.",
                    )
                )
            elif len(data) != int(entry.get("bytes") or 0):
                findings.append(
                    Finding(
                        severity="error",
                        code="backup_size_mismatch",
                        message=f"{relative} is {len(data)} bytes but the manifest says "
                        f"{entry.get('bytes')}",
                        path=zip_path.name,
                        remedy="The archive does not match its manifest. Nothing was written.",
                    )
                )
    return findings


@dataclass
class RestoreResult:
    backup_id: str
    dry_run: bool
    forced: bool
    written: list[str] = field(default_factory=list)
    would_overwrite: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    safety_backup_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "dry_run": self.dry_run,
            "forced": self.forced,
            "written": self.written,
            "would_overwrite": self.would_overwrite,
            "planned": self.planned,
            "safety_backup_id": self.safety_backup_id,
        }


def _selected_files(
    manifest: dict[str, Any], *, only: Sequence[str] | None
) -> list[dict[str, Any]]:
    entries = [entry for entry in manifest.get("files", []) if isinstance(entry, dict)]
    if not only:
        return entries
    prefixes = tuple(prefix.rstrip("/") + "/" for prefix in only)
    return [
        entry
        for entry in entries
        if str(entry.get("relative_path")).startswith(prefixes)
        or str(entry.get("relative_path")) in only
    ]


def restore(
    ws: Workspace,
    source: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    only: Sequence[str] | None = None,
    session_id: str | None = None,
) -> RestoreResult:
    """Verify, then (with ``--force``) replace files from a backup. Raises rather than guessing."""
    zip_path, manifest_path = resolve_source(ws, source)
    manifest = read_json(manifest_path, what="backup_manifest")
    store.registry(ws).validate_or_raise(
        "backup_manifest", manifest, path=manifest_path.name
    )
    findings = verify_archive(zip_path, manifest)
    if findings:
        first = findings[0]
        extra = "" if len(findings) == 1 else f" (and {len(findings) - 1} more)"
        raise LearningOSDataError(
            f"refusing to restore: {first.message}{extra}",
            path=first.path,
            remedy=first.remedy,
            code=first.code,
        )

    backup_id = str(manifest.get("backup_id"))
    entries = _selected_files(manifest, only=only)
    if not entries:
        raise LearningOSUsageError(
            "nothing in this backup matches --only",
            remedy="Drop --only, or pass a workspace-relative prefix such as 03_MEMORY.",
        )
    existing = [
        str(entry.get("relative_path"))
        for entry in entries
        if (ws.root / str(entry.get("relative_path"))).exists()
    ]

    if dry_run:
        return RestoreResult(
            backup_id=backup_id,
            dry_run=True,
            forced=force,
            written=[],
            would_overwrite=existing,
            planned=[str(entry.get("relative_path")) for entry in entries],
        )
    if existing and not force:
        raise LearningOSRefusedError(
            f"{len(existing)} file(s) already exist, starting with {existing[0]}",
            remedy="Re-run with --force to replace them (a pre-restore safety backup is taken "
            "first), or inspect the archive with --dry-run. Nothing was written.",
        )

    safety_id: str | None = None
    if existing:
        safety = create_backup(
            ws, note=f"pre-restore safety backup before {backup_id}", record_event=False
        )
        safety_id = safety.backup_id

    written: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for entry in entries:
            relative = str(entry.get("relative_path"))
            target = (ws.root / relative).resolve()
            if ws.root not in target.parents and target != ws.root:
                raise LearningOSDataError(  # pragma: no cover - unsafe names are rejected above
                    f"refusing to write outside the workspace: {relative}",
                    path=relative,
                    remedy="The archive is not trustworthy. Nothing further was written.",
                    code="unsafe_zip_entry",
                )
            ensure_dir(target.parent)
            data = archive.read(relative)
            temporary = target.with_name(f".{target.name}.restore.tmp")
            try:
                with open(temporary, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            written.append(relative)

    events_mod.append_event(
        ws,
        type="memory_restore",
        session_id=session_id or f"{utc_now_iso()[:10]}-restore",
        subject_id=None,
        actor="tool",
        payload={
            "backup_id": backup_id,
            "relative_path": ws.rel(zip_path) if ws.root in zip_path.parents else zip_path.name,
            "file_count": len(written),
            "total_bytes": sum(int(entry.get("bytes") or 0) for entry in entries),
            "forced": bool(force),
            "note": f"restored {len(written)} file(s)"
            + (f"; safety backup {safety_id}" if safety_id else ""),
        },
    )
    return RestoreResult(
        backup_id=backup_id,
        dry_run=False,
        forced=force,
        written=written,
        would_overwrite=existing,
        planned=written,
        safety_backup_id=safety_id,
    )


def describe_backup(ws: Workspace, backup_id: str) -> dict[str, Any]:
    """The manifest of one backup, for `status` and for the restore preview."""
    manifest_path = manifest_path_for(ws, backup_id)
    if not manifest_path.is_file():
        raise LearningOSNotFoundError(
            f"no manifest for backup {backup_id!r}",
            remedy="Available backups: " + (", ".join(list_backups(ws)) or "(none yet)"),
        )
    return read_json(manifest_path, what="backup_manifest", workspace=ws)
