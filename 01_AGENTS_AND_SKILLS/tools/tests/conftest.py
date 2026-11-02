"""Shared fixtures.

Every test runs against a throwaway workspace built in ``tmp_path`` from the shipped instruction
files, schemas and templates. No test ever reads or writes the real ``03_MEMORY/`` tree, and all
learning data in the suite is synthetic.
"""

from __future__ import annotations

import contextlib
import io
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence

import pytest

import common
import learningos
from common import SCHEMA_VERSION, Workspace, utc_now_iso
from memory import events as events_mod
from memory import store

#: The real LearningOS folder this test suite lives inside.
SOURCE_ROOT = Path(__file__).resolve().parents[3]

#: Parts of 01_AGENTS_AND_SKILLS a workspace needs before `init-workspace` can run.
COPIED_INSTALL_DIRS = ("core", "skills", "adapters", "templates", "schemas", "docs")


class CliResult(NamedTuple):
    """``(exit_code, stdout, stderr)`` — unpacks as a plain tuple."""

    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A fully initialised throwaway workspace. Returns its root path."""
    root = tmp_path / "LearningOS"
    root.mkdir()
    for name in COPIED_INSTALL_DIRS:
        source = SOURCE_ROOT / "01_AGENTS_AND_SKILLS" / name
        if source.is_dir():
            shutil.copytree(source, root / "01_AGENTS_AND_SKILLS" / name)
    code = learningos.main(["init-workspace", "--quiet", "--workspace", str(root)])
    assert code == 0, "init-workspace must succeed on a fresh directory"
    return root


@pytest.fixture
def workspace(ws: Path) -> Workspace:
    """The :class:`common.Workspace` API object for the throwaway workspace."""
    return Workspace.at(ws)


@pytest.fixture
def cli(ws: Path) -> Callable[..., CliResult]:
    """Call the CLI in-process and capture its output.

    In-process rather than via ``subprocess`` so a failure shows a real traceback and coverage
    sees the code. ``--workspace`` is appended automatically unless the call supplies one.
    """

    def run(*args: Any, workspace: Path | str | None = None) -> CliResult:
        argv = [str(arg) for arg in args]
        if "--workspace" not in argv:
            argv += ["--workspace", str(workspace if workspace is not None else ws)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = learningos.main(argv)
        return CliResult(code, out.getvalue(), err.getvalue())

    return run


@dataclass
class SeededSubject:
    """What :func:`seed_subject` created, so a test does not have to re-derive the ids."""

    subject_id: str
    name: str
    concept_ids: list[str] = field(default_factory=list)
    workspace: Workspace | None = None

    @property
    def first(self) -> str:
        return self.concept_ids[0]

    @property
    def last(self) -> str:
        return self.concept_ids[-1]


@pytest.fixture
def seed_subject(ws: Path, cli: Callable[..., CliResult]) -> Callable[..., SeededSubject]:
    """Create a subject with a small synthetic concept set, catalogue and prerequisite chain."""

    def seed(
        subject_id: str = "algo",
        *,
        name: str | None = None,
        slugs: Sequence[str] = ("sorting", "recursion", "big-o"),
        status: str = "unassessed",
        chain: bool = True,
        exam_date: str | None = None,
    ) -> SeededSubject:
        display_name = name or subject_id.replace("-", " ").title()
        args = ["init-subject", "--id", subject_id, "--name", display_name, "--quiet"]
        if exam_date:
            args += ["--exam-date", exam_date]
        result = cli(*args)
        assert result.code == 0, f"init-subject failed: {result.text}"

        workspace = Workspace.at(ws)
        now = utc_now_iso()
        concept_ids = [f"{subject_id}:{slug}" for slug in slugs]

        mastery = store.load_concept_mastery(workspace, subject_id)
        for position, (slug, concept_id) in enumerate(zip(slugs, concept_ids)):
            prerequisites = [concept_ids[position - 1]] if chain and position else []
            mastery["concepts"][concept_id] = store.new_concept_record(
                concept_id,
                slug.replace("-", " ").title(),
                subject_id=subject_id,
                unit="unit-1",
                prerequisite_ids=prerequisites,
                status=status,
                at=now,
            )
        store.save_concept_mastery(workspace, subject_id, mastery)

        catalog = store.load_concept_catalog(workspace, subject_id) or {
            "schema_version": SCHEMA_VERSION,
            "subject_id": subject_id,
            "updated_at": now,
            "concepts": [],
        }
        catalog["concepts"] = [
            {
                "concept_id": concept_id,
                "name": slug.replace("-", " ").title(),
                "unit": "unit-1",
                "description": f"Be able to use {slug} in a single exam question.",
                "prerequisite_ids": [concept_ids[position - 1]] if chain and position else [],
                "source_refs": [],
                "exam_weight": None,
                "tags": [],
            }
            for position, (slug, concept_id) in enumerate(zip(slugs, concept_ids))
        ]
        store.save_concept_catalog(workspace, subject_id, catalog)

        graph = store.load_concept_graph(workspace, subject_id) or {
            "schema_version": SCHEMA_VERSION,
            "subject_id": subject_id,
            "updated_at": now,
            "nodes": [],
            "edges": [],
        }
        graph["nodes"] = [{"concept_id": concept_id, "unit": "unit-1"} for concept_id in concept_ids]
        graph["edges"] = (
            [
                {
                    "from": concept_ids[position - 1],
                    "to": concept_ids[position],
                    "type": "prerequisite",
                    "note": None,
                }
                for position in range(1, len(concept_ids))
            ]
            if chain
            else []
        )
        store.save_concept_graph(workspace, subject_id, graph)

        return SeededSubject(
            subject_id=subject_id,
            name=display_name,
            concept_ids=concept_ids,
            workspace=workspace,
        )

    return seed


class FakeClock:
    """Monotonic UTC timestamps, so ordering assertions do not depend on wall-clock resolution."""

    def __init__(self, start: str = "2026-02-10T09:00:00Z", step_seconds: int = 60) -> None:
        self._moment = common.parse_timestamp(start)
        self._step = step_seconds

    def now(self) -> str:
        return common.format_timestamp(self._moment)

    def tick(self, seconds: int | None = None) -> str:
        import datetime as dt

        self._moment = self._moment + dt.timedelta(seconds=seconds or self._step)
        return self.now()

    def advance_days(self, days: float) -> str:
        import datetime as dt

        self._moment = self._moment + dt.timedelta(days=days)
        return self.now()


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """A monotonic clock installed over ``utc_now_iso`` in every module that imported it.

    The modules use ``from common import utc_now_iso``, so each one holds its own reference and
    each one has to be patched.
    """
    clock = FakeClock()

    def stamped() -> str:
        return clock.tick()

    monkeypatch.setattr(common, "utc_now_iso", stamped)
    for module in (store, events_mod, learningos):
        if hasattr(module, "utc_now_iso"):
            monkeypatch.setattr(module, "utc_now_iso", stamped)
    return clock


@pytest.fixture
def hash_tree() -> Callable[..., dict[str, str]]:
    """``path -> {relative path: sha256}`` for before/after comparisons."""
    return common.hash_tree
