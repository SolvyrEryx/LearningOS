"""Test L — privacy and non-destructiveness.

Two promises are tested here, both of which are easy to make and easy to break silently.

**Local-first.** No module under `tools/` may import a network library or shell out to a package
manager. This is a static scan, not a runtime check, because "it did not phone home during the
test" is a much weaker claim than "it cannot". Note what this does *not* claim: if the model
driving the conversation is hosted, it already sees whatever is pasted into the chat. Local files
are not local inference.

**Non-destructive.** Risky commands refuse by default, `--dry-run` writes nothing at all, and a
restore verifies every hash before it touches a byte — including rejecting an archive that tries
to write outside the workspace.
"""

from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

import pytest

from common import (
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_VALIDATION,
    SCHEMA_VERSION,
    Workspace,
    hash_tree,
    looks_absolute,
    sha256_bytes,
    sha256_file,
    utc_now_iso,
)
from memory import backup as backup_mod
from memory import events as events_mod
from memory import mastery
from memory import store

TOOLS_ROOT = Path(__file__).resolve().parents[1]

#: Importing any of these would make the tooling capable of leaving the machine.
FORBIDDEN_MODULES = {
    "socket",
    "requests",
    "urllib",
    "http",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
    "webbrowser",
}

#: The single sanctioned ``subprocess`` user: rendering an .svg with mermaid-cli, if it is
#: installed. Nothing else in the tooling is allowed to start a process.
SUBPROCESS_ALLOWLIST = {"graphs/render.py"}

EVENT_LOG = "03_MEMORY/events/session_events.jsonl"


def python_files() -> list[Path]:
    return sorted(
        path
        for path in TOOLS_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and ".pytest_cache" not in path.parts
    )


def relative(path: Path) -> str:
    return path.relative_to(TOOLS_ROOT).as_posix()


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


# ------------------------------------------------------------------------------ local-first scan


def test_no_module_imports_anything_that_could_reach_the_network():
    offenders: dict[str, set[str]] = {}
    for path in python_files():
        bad = imported_modules(path) & FORBIDDEN_MODULES
        if bad:
            offenders[relative(path)] = bad
    assert offenders == {}, f"network-capable imports found: {offenders}"


def test_subprocess_is_only_used_by_the_optional_renderer():
    users = {
        relative(path)
        for path in python_files()
        if "subprocess" in imported_modules(path)
    }
    assert users <= SUBPROCESS_ALLOWLIST, (
        f"unexpected subprocess user(s): {sorted(users - SUBPROCESS_ALLOWLIST)}"
    )


def test_nothing_shells_out_to_a_package_manager():
    """Optional dependencies are reported, never installed. Any `pip install` must be prose."""
    for path in python_files():
        text = path.read_text(encoding="utf-8")
        if "pip install" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = ast.unparse(node.func)
                assert not target.startswith(("subprocess.", "os.system", "os.exec")), (
                    f"{relative(path)} starts a process in a file that mentions pip install"
                )


def test_no_memory_value_holds_an_absolute_path(ws, cli, seed_subject):
    seed_subject("algo", slugs=("sorting",))
    workspace = Workspace.at(ws)
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        "algo:sorting",
        session_id="2026-02-11-algo-01",
        outcome="hinted_success",
        hints_used=1,
        novelty="variant",
    )
    assert cli("backup", "--note", "state the paths").code == EXIT_OK
    offenders: list[str] = []
    for path in sorted((ws / "03_MEMORY").rglob("*.json")):
        if "backups" in path.relative_to(ws).parts and path.suffix == ".zip":
            continue
        for pointer, value in _strings(json.loads(path.read_text(encoding="utf-8"))):
            if looks_absolute(value) or "C:\\" in value or "e:\\" in value.lower():
                offenders.append(f"{path.relative_to(ws).as_posix()}{pointer} = {value!r}")
    assert offenders == [], offenders
    assert cli("validate").code == EXIT_OK


def _strings(node, pointer: str = ""):
    if isinstance(node, str):
        yield pointer or "/", node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{pointer}/{index}")


# ---------------------------------------------------------------------- read-only commands stay so


@pytest.fixture
def populated(ws, cli, seed_subject) -> Workspace:
    """A subject with evidence, a checkpoint and some source material on disk."""
    seed_subject("algo", slugs=("sorting", "recursion"))
    workspace = Workspace.at(ws)
    sources = ws / "02_KNOWLEDGE/subjects/algo/source_materials"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "notes.md").write_text(
        "# Merge sort\n\nT(n) = 2T(n/2) + n\n", encoding="utf-8"
    )
    (sources / "syllabus.txt").write_text("Unit 1: sorting\nUnit 2: recursion\n", encoding="utf-8")
    mastery.record_attempt_and_save(
        workspace,
        "algo",
        "algo:sorting",
        session_id="2026-02-11-algo-01",
        outcome="independent_success",
        novelty="variant",
        delay_days=3,
        next_action="Derive the recurrence unaided.",
    )
    assert (
        cli(
            "checkpoint",
            "--subject",
            "algo",
            "--session-id",
            "2026-02-11-algo-01",
            "--note",
            "stopped after one unaided derivation",
            "--next-action",
            "Derive the recurrence unaided.",
            "--concept",
            "algo:sorting",
        ).code
        == EXIT_OK
    )
    return workspace


def test_reading_commands_never_touch_source_materials(populated, cli):
    sources = populated.root / "02_KNOWLEDGE/subjects/algo/source_materials"
    before = hash_tree(sources)
    assert before, "the fixture put files there"
    assert cli("search-memory", "recurrence").code == EXIT_OK
    assert cli("session-report", "--last").code == EXIT_OK
    assert cli("resume").code == EXIT_OK
    assert cli("reconcile").code == EXIT_OK
    assert cli("status").code == EXIT_OK
    # `index` is the one command that reads the learner's documents on purpose, and `update-graphs`
    # is the one that writes the most files. Both must leave every original byte-identical.
    assert cli("index", "--subject", "algo").code == EXIT_OK
    assert cli("index", "--subject", "algo", "--force").code == EXIT_OK
    assert cli("index", "--all").code == EXIT_OK
    assert cli("update-graphs", "--all").code == EXIT_OK
    assert hash_tree(sources) == before, "originals are opened read-only, never rewritten"


def test_graph_generation_never_writes_a_source_of_truth_file(populated, cli, ws):
    """Derived artefacts are additive: the snapshots they are drawn from stay untouched."""
    state_files = sorted((ws / "03_MEMORY/subjects/algo").glob("*.json"))
    assert state_files
    before = {path.name: sha256_file(path) for path in state_files}
    assert cli("update-graphs", "--all").code == EXIT_OK
    assert cli("update-graphs", "--all").code == EXIT_OK
    assert {path.name: sha256_file(path) for path in state_files} == before
    assert any((ws / "03_MEMORY/graphs").rglob("*.mmd")), "it really did write the graphs"


def test_indexing_only_ever_adds_derived_files(populated, cli, ws):
    """Nothing outside source_index.json and extracted_text/ is touched by `index`."""
    knowledge = ws / "02_KNOWLEDGE/subjects/algo"
    before = hash_tree(knowledge)
    assert cli("index", "--subject", "algo", "--force").code == EXIT_OK
    after = hash_tree(knowledge)
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    assert all(
        key == "source_index.json" or key.startswith("extracted_text/") for key in changed
    ), changed


def test_reconcile_is_report_only_by_default(populated, cli):
    before = hash_tree(populated.root / "03_MEMORY")
    result = cli("reconcile", "--subject", "algo", "--dry-run")
    assert result.code == EXIT_OK, result.text
    assert hash_tree(populated.root / "03_MEMORY") == before, "no drift, nothing written"


# ----------------------------------------------------------------------------- backup / restore


def test_backup_dry_run_writes_nothing(populated, cli):
    before = hash_tree(populated.root)
    result = cli("backup", "--dry-run")
    assert result.code == EXIT_OK, result.text
    assert "nothing was written" in result.out
    assert hash_tree(populated.root) == before


def test_backup_then_restore_dry_run_writes_nothing(populated, cli):
    assert cli("backup", "--note", "round trip").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    before = hash_tree(populated.root)

    result = cli("restore", "--from", backup_id, "--dry-run")
    assert result.code == EXIT_OK, result.text
    assert "nothing was written" in result.out
    assert hash_tree(populated.root) == before, "--dry-run is a plan, not a write"


def test_restore_without_force_refuses_and_changes_nothing(populated, cli):
    assert cli("backup").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    before = hash_tree(populated.root)

    result = cli("restore", "--from", backup_id)
    assert result.code == EXIT_REFUSED
    assert "--force" in result.err
    assert "Nothing was written" in result.err
    assert hash_tree(populated.root) == before


def test_restore_force_round_trips_a_mutated_memory_tree(populated, cli, ws):
    assert cli("backup", "--note", "before the damage").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    manifest = backup_mod.describe_backup(populated, backup_id)
    log = ws / EVENT_LOG
    with zipfile.ZipFile(backup_mod.zip_path_for(populated, backup_id)) as archive:
        archived_log_lines = archive.read(EVENT_LOG).decode("utf-8").splitlines()

    record = store.get_concept_record(populated, "algo", "algo:sorting")
    record["next_action"] = "DAMAGED: hand-edited into nonsense"
    record["status"] = "provisionally_mastered"
    store.put_concept_record(populated, "algo", record)
    (ws / "03_MEMORY/subjects/algo/revision_queue.json").unlink()
    assert sha256_file(ws / "03_MEMORY/subjects/algo/concept_mastery.json") != next(
        entry["sha256"]
        for entry in manifest["files"]
        if entry["relative_path"] == "03_MEMORY/subjects/algo/concept_mastery.json"
    )

    result = cli("restore", "--from", backup_id, "--force")
    assert result.code == EXIT_OK, result.text
    assert "safety backup" in result.out

    for entry in manifest["files"]:
        target = ws / entry["relative_path"]
        assert target.is_file(), f"{entry['relative_path']} was not restored"
        if entry["relative_path"] == EVENT_LOG:
            continue
        assert sha256_file(target) == entry["sha256"], entry["relative_path"]

    restored = store.get_concept_record(populated, "algo", "algo:sorting")
    assert restored["next_action"] == "Derive the recurrence unaided."
    assert restored["status"] == "practising"

    # The event log is the one file that legitimately differs from the archive afterwards: a
    # restore rewinds the log to its archived state and then appends the restore itself, because
    # the restore is evidence too. Events that happened after the backup are not hidden — they
    # live in the pre-restore safety backup, which is why --force takes one.
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[: len(archived_log_lines)] == archived_log_lines
    assert len(lines) == len(archived_log_lines) + 1
    appended = json.loads(lines[-1])
    assert appended["type"] == "memory_restore"
    assert appended["payload"]["backup_id"] == backup_id
    assert appended["payload"]["forced"] is True
    assert cli("validate").code == EXIT_OK


def test_restore_force_takes_a_safety_backup_first(populated, cli):
    assert cli("backup").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    result = cli("restore", "--from", backup_id, "--force", "--json")
    assert result.code == EXIT_OK, result.text
    payload = json.loads(result.out)
    assert payload["safety_backup_id"] and payload["safety_backup_id"] != backup_id
    assert backup_mod.zip_path_for(populated, payload["safety_backup_id"]).is_file()


def test_restore_only_limits_what_is_written(populated, cli, ws):
    assert cli("backup").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    result = cli("restore", "--from", backup_id, "--only", "03_MEMORY", "--dry-run", "--json")
    assert result.code == EXIT_OK, result.text
    payload = json.loads(result.out)
    assert payload["planned"]
    assert all(relative.startswith("03_MEMORY/") for relative in payload["planned"])


def test_a_tampered_archive_is_refused_before_anything_is_written(populated, cli, ws):
    assert cli("backup").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    manifest_path = backup_mod.manifest_path_for(populated, backup_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = hash_tree(ws / "03_MEMORY/subjects")

    result = cli("restore", "--from", backup_id, "--force")
    assert result.code == EXIT_VALIDATION
    assert "manifest sha256" in result.err
    assert hash_tree(ws / "03_MEMORY/subjects") == before


def test_a_zip_entry_with_a_parent_traversal_is_rejected(populated, cli, ws, tmp_path):
    """A crafted archive must not be able to write one byte outside the workspace."""
    backup_id = "memory-20200101T000000Z"
    safe_payload = b"this one is harmless\n"
    evil_payload = b"this one must never be written\n"
    zip_path = ws / "03_MEMORY/backups" / f"{backup_id}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("03_MEMORY/archives/harmless.txt", safe_payload)
        archive.writestr("../evil.txt", evil_payload)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "backup_id": backup_id,
        "created_at": "2020-01-01T00:00:00Z",
        "tool_version": "handmade",
        "note": "crafted by test L",
        "includes": ["03_MEMORY"],
        "include_sources": False,
        "file_count": 2,
        "total_bytes": len(safe_payload) + len(evil_payload),
        "files": [
            {
                "relative_path": "03_MEMORY/archives/harmless.txt",
                "bytes": len(safe_payload),
                "sha256": sha256_bytes(safe_payload),
            },
            {
                "relative_path": "../evil.txt",
                "bytes": len(evil_payload),
                "sha256": sha256_bytes(evil_payload),
            },
        ],
    }
    (ws / "03_MEMORY/backups" / f"{backup_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    before = hash_tree(ws)

    result = cli("restore", "--from", backup_id, "--force")
    assert result.code == EXIT_VALIDATION
    assert "unsafe path" in result.err
    assert not (ws / "03_MEMORY/archives/harmless.txt").exists(), (
        "the whole archive is refused, so even the harmless entry stays out"
    )
    assert not (ws.parent / "evil.txt").exists()
    assert hash_tree(ws) == before


def test_an_archive_without_its_manifest_is_refused(populated, cli, ws):
    assert cli("backup").code == EXIT_OK
    backup_id = backup_mod.list_backups(populated)[-1]
    backup_mod.manifest_path_for(populated, backup_id).unlink()
    result = cli("restore", "--from", backup_id, "--force")
    assert result.code == EXIT_VALIDATION
    assert "manifest" in result.err


def test_backups_do_not_archive_previous_backups_or_the_search_cache(populated, cli):
    assert cli("search-memory", "recurrence", "--reindex").code == EXIT_OK
    assert cli("backup").code == EXIT_OK
    first = backup_mod.list_backups(populated)[-1]
    assert cli("backup").code == EXIT_OK
    second = backup_mod.list_backups(populated)[-1]
    manifest = backup_mod.describe_backup(populated, second)
    paths = {entry["relative_path"] for entry in manifest["files"]}
    assert not any(path.startswith("03_MEMORY/backups/") for path in paths)
    assert first not in " ".join(paths)
    assert "03_MEMORY/global/search_index.sqlite3" not in paths, (
        "the search cache is derived; archiving it would bloat every backup for nothing"
    )


def test_backup_without_include_sources_leaves_documents_out(populated, cli):
    assert cli("backup").code == EXIT_OK
    manifest = backup_mod.describe_backup(populated, backup_mod.list_backups(populated)[-1])
    paths = {entry["relative_path"] for entry in manifest["files"]}
    assert not any("source_materials" in path for path in paths)

    assert cli("backup", "--include-sources").code == EXIT_OK
    manifest = backup_mod.describe_backup(populated, backup_mod.list_backups(populated)[-1])
    paths = {entry["relative_path"] for entry in manifest["files"]}
    assert "02_KNOWLEDGE/subjects/algo/source_materials/notes.md" in paths
    assert manifest["include_sources"] is True


def test_a_backup_records_itself_as_evidence(populated, cli):
    assert cli("backup", "--note", "evidence please").code == EXIT_OK
    events, _findings = events_mod.read_events(populated, types=["memory_backup"])
    assert events, "a backup is an event worth keeping"
    assert events[-1]["payload"]["note"] == "evidence please"
    assert events[-1]["payload"]["relative_path"].startswith("03_MEMORY/backups/")
    state = store.load_workspace_state(populated)
    assert state["last_backup_id"] == events[-1]["payload"]["backup_id"]
    assert state["updated_at"] <= utc_now_iso()
