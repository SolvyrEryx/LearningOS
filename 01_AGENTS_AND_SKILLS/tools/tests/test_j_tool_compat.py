"""Test J — tool compatibility and documentation that cannot rot.

Two different agents discover instructions two different ways: Codex reads the root ``AGENTS.md``,
Kiro reads a steering file copied into ``.kiro/steering/``. Both routes are pointers into one
canonical instruction set, and a pointer is only worth anything if it points somewhere real.

So the assertion here is **path resolution, not string presence**. Every workspace-relative path
written in the entrypoint files, the two adapter READMEs and the two user-facing READMEs is
extracted with the same regex ``validate`` check (j) uses, and each one has to exist on disk. A
renamed core file or a moved directory therefore fails this test rather than silently sending the
agent somewhere that is not there.

The thin-pointer decision is enforced mechanically too: the steering file must declare
``inclusion: always`` and must stay short enough that it cannot have grown a second copy of the
teaching rules.

Unlike the rest of the suite these tests read the **real** install this file lives inside, because
the shipped documentation is the thing under test. Nothing here writes anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import learningos
from common import EXIT_CODE_MEANINGS
from validation import ADAPTER_FILES, DOC_PATH_RE

#: The real LearningOS folder this test suite lives inside.
SOURCE_ROOT = Path(__file__).resolve().parents[3]

#: ``AGENTS.md`` plus the adapters (checked by ``validate``), plus the two user-facing READMEs.
#: Extending the same resolution check to the READMEs is what stops the documentation drifting
#: away from the layout it describes.
DOCUMENTED_FILES = ADAPTER_FILES + (
    "README.md",
    "01_AGENTS_AND_SKILLS/tools/README.md",
    "01_AGENTS_AND_SKILLS/docs/design_decisions.md",
)

KIRO_STEERING = "01_AGENTS_AND_SKILLS/adapters/kiro/steering/learningos.md"

#: The canonical instruction files ``AGENTS.md`` has to route the agent to.
CORE_FILES = (
    "system_contract.md",
    "teaching_principles.md",
    "memory_protocol.md",
    "source_handling.md",
    "session_protocol.md",
)

#: A pointer file that is longer than this is no longer a pointer.
STEERING_MAX_LINES = 60

#: Headings in tools/README.md that introduce a subcommand, e.g. "### `init-subject`".
SUBCOMMAND_HEADING_RE = re.compile(r"^#{2,4}\s+`([a-z][a-z-]*)`\s*$", re.MULTILINE)


def read(relative: str) -> str:
    return (SOURCE_ROOT / relative).read_text(encoding="utf-8")


def documented_paths(text: str) -> list[str]:
    """Every workspace-relative path mentioned in ``text``, globs excluded.

    Uses ``validation.DOC_PATH_RE`` rather than a second regex so this test and ``validate``
    check (j) can never disagree about what counts as a documented path.
    """
    return sorted({match for match in DOC_PATH_RE.findall(text) if "*" not in match})


# ------------------------------------------------------------------- the files an agent reads


@pytest.mark.parametrize("relative", DOCUMENTED_FILES)
def test_the_entrypoint_and_adapter_files_exist(relative: str) -> None:
    path = SOURCE_ROOT / relative
    assert path.is_file(), f"{relative} is missing: an agent cannot discover the instructions"
    assert path.read_text(encoding="utf-8").strip(), f"{relative} is empty"


@pytest.mark.parametrize("relative", DOCUMENTED_FILES)
def test_every_documented_path_resolves_on_disk(relative: str) -> None:
    text = read(relative)
    mentioned = documented_paths(text)
    assert mentioned, f"{relative} mentions no workspace paths, which cannot be right"
    missing = [candidate for candidate in mentioned if not (SOURCE_ROOT / candidate).exists()]
    assert not missing, f"{relative} points at paths that do not exist: {missing}"


def test_the_adapters_point_at_each_other_and_at_the_canonical_files() -> None:
    kiro = read("01_AGENTS_AND_SKILLS/adapters/kiro/README.md")
    codex = read("01_AGENTS_AND_SKILLS/adapters/codex/README.md")
    assert ".kiro/steering" in kiro or ".kiro\\steering" in kiro
    assert "Copy-Item" in kiro, "the Kiro adapter must give the exact copy command"
    assert "AGENTS.md" in codex, "the Codex adapter must name root AGENTS.md as canonical"
    assert "learningos.config.json" in codex, "workspace-root discovery must be documented"


# ----------------------------------------------------------------- the thin-pointer decision


def test_the_steering_file_declares_inclusion_always() -> None:
    lines = read(KIRO_STEERING).splitlines()
    assert lines[0].strip() == "---", "front matter must be the very first thing in the file"
    closing = lines.index("---", 1)
    front_matter = [line.strip() for line in lines[1:closing]]
    assert "inclusion: always" in front_matter, f"front matter was {front_matter}"


def test_the_steering_file_stays_a_thin_pointer() -> None:
    lines = read(KIRO_STEERING).splitlines()
    assert len(lines) < STEERING_MAX_LINES, (
        f"the steering file is {len(lines)} lines. It holds pointers only; teaching rules belong "
        "in 01_AGENTS_AND_SKILLS/core/ where there is exactly one copy of them."
    )


def test_the_steering_file_routes_to_every_core_file() -> None:
    text = read(KIRO_STEERING)
    for name in CORE_FILES:
        assert f"01_AGENTS_AND_SKILLS/core/{name}" in text, f"steering never names {name}"


# ----------------------------------------------------------------------- the root entrypoint


def test_agents_md_references_every_core_file_and_the_skills_directory() -> None:
    text = read("AGENTS.md")
    for name in CORE_FILES:
        assert f"01_AGENTS_AND_SKILLS/core/{name}" in text, f"AGENTS.md never names {name}"
    assert "01_AGENTS_AND_SKILLS/skills/" in text, "AGENTS.md must route to the skills directory"
    for relative in ("02_KNOWLEDGE", "03_MEMORY"):
        assert relative in text, f"AGENTS.md must describe {relative}"


def test_agents_md_names_both_adapters() -> None:
    text = read("AGENTS.md")
    assert "01_AGENTS_AND_SKILLS/adapters/kiro" in text
    assert "01_AGENTS_AND_SKILLS/adapters/codex" in text


# ------------------------------------------------------------- documentation vs the real CLI


def test_tools_readme_documents_exactly_the_subcommands_that_exist() -> None:
    documented = set(SUBCOMMAND_HEADING_RE.findall(read("01_AGENTS_AND_SKILLS/tools/README.md")))
    implemented = {command.name for command in learningos.COMMANDS}
    assert documented == implemented, (
        f"documented but not implemented: {sorted(documented - implemented)}; "
        f"implemented but undocumented: {sorted(implemented - documented)}"
    )


def test_tools_readme_documents_every_exit_code() -> None:
    text = read("01_AGENTS_AND_SKILLS/tools/README.md")
    for code, meaning in sorted(EXIT_CODE_MEANINGS.items()):
        assert re.search(rf"^\|\s*`?{code}`?\s*\|", text, re.MULTILINE), (
            f"exit code {code} ({meaning}) is missing from the exit-code table"
        )


def test_tools_readme_splits_capabilities_three_ways() -> None:
    text = read("01_AGENTS_AND_SKILLS/tools/README.md").lower()
    for phrase in ("implemented and tested", "depends on the", "manual step"):
        assert phrase in text, f"the capability matrix never says {phrase!r}"


def test_the_readmes_state_that_local_files_are_not_local_inference() -> None:
    for relative in ("README.md", "01_AGENTS_AND_SKILLS/tools/README.md"):
        text = read(relative).lower()
        assert "local inference" in text, (
            f"{relative} must be explicit that storing files locally does not mean the model "
            "runs locally"
        )
