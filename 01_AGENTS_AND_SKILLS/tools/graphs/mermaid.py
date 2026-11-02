"""Graph artefact -> Mermaid text. A pure function, deliberately.

:func:`to_mermaid` touches no file, no clock and no environment: given the same graph JSON it
returns the same string, byte for byte. That is what makes a regenerated graph show up as "no
change" instead of as noise, and it is why the ``.mmd`` — not the picture — is the artefact.

Two details that are easy to get wrong and expensive to debug:

* **Node keys are sanitised injectively.** Mermaid identifiers cannot contain a colon, so
  ``algo:big-o`` becomes ``algo_big_o``. Mapping ``[^A-Za-z0-9] -> _`` is not injective on its own
  (``a:b`` and ``a-b`` both become ``a_b``), so a collision appends a 6-character sha1 of the
  original id. Keys are assigned in sorted order of the ids, so the mapping is deterministic.
* **Labels are quoted and escaped.** Everything goes inside ``"..."``, ``#`` becomes ``#35;`` and
  ``"`` becomes ``#quot;``, which is Mermaid's own entity escape. A concept called
  ``He said "x -> y": done`` therefore renders as text instead of breaking the parse.

The frames below (the ``flowchart`` header and the ``classDef`` block) mirror
``01_AGENTS_AND_SKILLS/templates/graph_templates/*.mmd.tmpl``; the templates document the frame,
this module is what emits it, and ``tests/test_f_graphs.py`` asserts the two still agree.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

#: Characters Mermaid will not accept in an identifier.
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9]")

#: ``classDef`` blocks per kind, in template order. Colour is decoration: every meaning is also
#: written as text in the node label and in the legend.
STATUS_CLASS_DEFS: tuple[str, ...] = (
    "classDef unassessed fill:#ffffff,stroke:#777777,stroke-width:1px,color:#000000;",
    "classDef developing fill:#fdf3d0,stroke:#8a6d1a,stroke-width:1px,color:#000000;",
    "classDef practising fill:#dceaf7,stroke:#1f4e79,stroke-width:1px,color:#000000;",
    "classDef provisionally_mastered fill:#dff0d8,stroke:#2d6a2d,stroke-width:2px,color:#000000;",
    "classDef needs_review fill:#f8d7da,stroke:#8b1c24,stroke-width:2px,color:#000000;",
)
LEGEND_CLASS_DEF = "classDef legend fill:#ffffff,stroke:#333333,stroke-width:1px,color:#000000;"
CONCEPT_CLASS_DEF = (
    "classDef concept fill:#f4f4f4,stroke:#333333,stroke-width:1px,color:#000000;"
)
UNIT_CLASS_DEF = "classDef unit fill:#ffffff,stroke:#555555,stroke-width:1px;"
SUBJECT_CLASS_DEF = "classDef subject fill:#ffffff,stroke:#555555,stroke-width:1px;"
MISCONCEPTION_CLASS_DEFS: tuple[str, ...] = (
    "classDef mc_open fill:#f8d7da,stroke:#8b1c24,stroke-width:2px,color:#000000;",
    "classDef mc_monitoring fill:#fdf3d0,stroke:#8a6d1a,stroke-width:1px,color:#000000;",
    "classDef mc_resolved fill:#dff0d8,stroke:#2d6a2d,stroke-width:1px,color:#000000;",
)

#: ``(diagram header, classDef lines)`` per graph kind.
FRAMES: dict[str, tuple[str, tuple[str, ...]]] = {
    "concept_prerequisite": ("flowchart LR", (UNIT_CLASS_DEF, CONCEPT_CLASS_DEF)),
    "learner_mastery": ("flowchart TD", (*STATUS_CLASS_DEFS, LEGEND_CLASS_DEF)),
    "misconception": (
        "flowchart TD",
        (CONCEPT_CLASS_DEF, *MISCONCEPTION_CLASS_DEFS, LEGEND_CLASS_DEF),
    ),
    "cross_subject": (
        "flowchart LR",
        (SUBJECT_CLASS_DEF, *STATUS_CLASS_DEFS, LEGEND_CLASS_DEF),
    ),
    "progress": ("pie title Concept status distribution", ()),
}

#: Mermaid node shapes. ``rect`` is the default.
SHAPES: dict[str, tuple[str, str]] = {
    "rect": ("[", "]"),
    "round": ("(", ")"),
    "stadium": ("([", "])"),
    "hexagon": ("{{", "}}"),
    "diamond": ("{", "}"),
    "circle": ("((", "))"),
}

LEGEND_TITLE = "Legend (text labels, not colour)"

#: Kinds whose Mermaid output carries a ``Legend`` subgraph — the same three whose shipped
#: templates have a ``{{LEGEND}}`` placeholder. The other two still carry a ``legend`` array in
#: the graph JSON, which the HTML viewer renders as a table; a prerequisite map needs no key.
LEGEND_KINDS: tuple[str, ...] = ("learner_mastery", "misconception", "cross_subject")


def escape_label(text: Any) -> str:
    """Make ``text`` safe inside a Mermaid ``"..."`` label.

    ``#`` first (it starts an entity), then ``"`` -> ``#quot;``; newlines become ``<br/>``. The
    order matters: escaping ``"`` first would leave the inserted ``#`` to be mangled.
    """
    out = str(text)
    out = out.replace("#", "#35;")
    out = out.replace('"', "#quot;")
    out = out.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    return out


def _sanitise(identifier: str) -> str:
    base = _UNSAFE_KEY_CHARS.sub("_", str(identifier))
    if not base:
        base = "n"
    if base[0].isdigit():
        base = f"n{base}"
    return base


class KeyMap:
    """Injective id -> Mermaid-key mapping, shared by nodes and subgraphs.

    Mermaid puts subgraph ids in the same namespace as node ids, so both are allocated here;
    group keys are prefixed with ``g_`` and still checked for collisions.
    """

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self._taken: dict[str, str] = {}

    def add(self, identifier: str, *, prefix: str = "") -> str:
        token = f"{prefix}{identifier}"
        if token in self._keys:
            return self._keys[token]
        base = _sanitise(f"{prefix}{identifier}") if prefix else _sanitise(identifier)
        key = base
        if key in self._taken:
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:6]
            key = f"{base}_{digest}"
            while key in self._taken:  # pragma: no cover - needs a sha1 prefix collision
                key = f"{key}x"
        self._taken[key] = token
        self._keys[token] = key
        return key

    def get(self, identifier: str, *, prefix: str = "") -> str:
        return self._keys.get(f"{prefix}{identifier}", "")

    def __contains__(self, identifier: object) -> bool:
        return str(identifier) in self._keys

    @property
    def keys_by_id(self) -> dict[str, str]:
        return dict(self._keys)


def node_keys(ids: Iterable[str]) -> dict[str, str]:
    """``{concept id: mermaid key}``, injective and independent of input order."""
    keymap = KeyMap()
    for identifier in sorted({str(item) for item in ids}):
        keymap.add(identifier)
    return keymap.keys_by_id


def _node_text(key: str, node: Mapping[str, Any]) -> str:
    shape = node.get("shape") or "rect"
    open_token, close_token = SHAPES.get(str(shape), SHAPES["rect"])
    label = escape_label(node.get("label") or node.get("id"))
    line = f'{key}{open_token}"{label}"{close_token}'
    style = node.get("style_class")
    if isinstance(style, str) and style.strip():
        line = f"{line}:::{_sanitise(style)}"
    return line


def _header(graph: Mapping[str, Any]) -> list[str]:
    """Comment header. Deliberately free of timestamps, so the output stays byte-stable."""
    subjects = ", ".join(str(item) for item in graph.get("subject_ids") or [])
    nodes = len(graph.get("nodes") or [])
    edges = len(graph.get("edges") or [])
    return [
        f"%% LearningOS graph - kind: {graph.get('kind')}",
        f"%% title: {graph.get('title') or graph.get('graph_id')}",
        f"%% subjects: {subjects}",
        f"%% nodes: {nodes}  edges: {edges}",
        "%% Generated from the memory files by learningos.py update-graphs. Derived and "
        "reproducible: regenerating it from unchanged memory produces this file byte for byte.",
    ]


def _legend_lines(graph: Mapping[str, Any], keymap: KeyMap) -> list[str]:
    if str(graph.get("kind")) not in LEGEND_KINDS:
        return []
    entries = [entry for entry in graph.get("legend") or [] if isinstance(entry, dict)]
    if not entries:
        return []
    lines = [f'  subgraph Legend["{escape_label(LEGEND_TITLE)}"]', "    direction TB"]
    for entry in entries:
        key = keymap.add(str(entry.get("key")), prefix="legend:")
        style = entry.get("style") or "legend"
        label = entry.get("label") or entry.get("key")
        lines.append(f'    {key}["{escape_label(label)}"]:::{_sanitise(str(style))}')
    lines.append("  end")
    return lines


def _flowchart(graph: Mapping[str, Any], *, grouped: bool) -> str:
    kind = str(graph.get("kind"))
    header_line, class_defs = FRAMES.get(kind, ("flowchart TD", ()))
    nodes = [node for node in graph.get("nodes") or [] if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges") or [] if isinstance(edge, dict)]

    keymap = KeyMap()
    for node in sorted(nodes, key=lambda item: str(item.get("id"))):
        keymap.add(str(node.get("id")))

    lines = [*_header(graph), header_line]
    lines.extend(f"  {definition}" for definition in class_defs)

    by_id = {str(node.get("id")): node for node in nodes}
    ungrouped = sorted(
        (node for node in nodes if not (grouped and node.get("group"))),
        key=lambda item: str(item.get("id")),
    )
    for node in ungrouped:
        lines.append(f"  {_node_text(keymap.get(str(node.get('id'))), node)}")

    if grouped:
        groups = sorted(
            {str(node.get("group")) for node in nodes if node.get("group")}
        )
        for group in groups:
            group_key = keymap.add(group, prefix="g_")
            lines.append(f'  subgraph {group_key}["{escape_label(group)}"]')
            lines.append("    direction TB" if header_line.endswith("LR") else "    direction LR")
            for node in sorted(
                (item for item in nodes if str(item.get("group")) == group),
                key=lambda item: str(item.get("id")),
            ):
                lines.append(f"    {_node_text(keymap.get(str(node.get('id'))), node)}")
            lines.append("  end")

    for edge in sorted(
        edges,
        key=lambda item: (str(item.get("from")), str(item.get("to")), str(item.get("type"))),
    ):
        source = str(edge.get("from"))
        target = str(edge.get("to"))
        if source not in by_id or target not in by_id:
            # build.py resolves every endpoint before this point; skipping here means a
            # hand-written artefact cannot produce a node out of thin air.
            continue
        label = edge.get("label") or edge.get("type")
        arrow = "-.->" if edge.get("type") == "exhibits" else "-->"
        lines.append(
            f"  {keymap.get(source)} {arrow}|\"{escape_label(label)}\"| {keymap.get(target)}"
        )

    lines.extend(_legend_lines(graph, keymap))
    return "\n".join(lines) + "\n"


def _pie(graph: Mapping[str, Any]) -> str:
    """The ``progress`` pie.

    A pie carries exactly one quantity, and here that quantity is the status distribution: every
    concept has exactly one status, so the slices really do partition the set. The per-session
    advance series is genuinely a second quantity, so it goes in the comment header rather than
    being forced into the chart.
    """
    header_line, _class_defs = FRAMES["progress"]
    lines = list(_header(graph))
    series = [entry for entry in graph.get("series") or [] if isinstance(entry, dict)]
    if series:
        lines.append("%% advances per session (a second quantity, so not a slice):")
        for entry in sorted(series, key=lambda item: str(item.get("key"))):
            lines.append(f"%%   {entry.get('key')}: {entry.get('label') or entry.get('value')}")
    lines.append(header_line)
    slices = [
        (escape_label(node.get("label") or node.get("id")), node.get("value"))
        for node in graph.get("nodes") or []
        if isinstance(node, dict)
    ]
    for label, value in sorted(slices, key=lambda item: item[0]):
        number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
        lines.append(f'  "{label}" : {int(number) if float(number).is_integer() else number}')
    return "\n".join(lines) + "\n"


def to_mermaid(graph: Mapping[str, Any]) -> str:
    """Render a graph artefact as Mermaid text. Pure and deterministic.

    Grouping is on for the kinds whose contract says so (``concept_prerequisite`` groups by
    syllabus unit, ``cross_subject`` by subject); the others draw a flat graph even if a node
    happens to carry a ``group``.
    """
    kind = str(graph.get("kind"))
    if kind == "progress":
        return _pie(graph)
    return _flowchart(graph, grouped=kind in ("concept_prerequisite", "cross_subject"))


def mermaid_filename(graph: Mapping[str, Any]) -> str:
    return f"{graph.get('kind')}.mmd"


def class_definitions_for(kind: str) -> Sequence[str]:
    """The ``classDef`` lines a kind emits — used by the tests that pin frame and template."""
    return FRAMES.get(kind, ("", ()))[1]
