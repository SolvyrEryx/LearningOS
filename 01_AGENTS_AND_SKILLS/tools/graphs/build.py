"""Building a canonical graph artefact from the memory files.

Reads ``concept_mastery.json``, ``concept_graph.json``, ``misconceptions.json``,
``revision_queue.json``, ``subject_state.json`` and the event log — read-only, always — and returns
a dict conforming to ``01_AGENTS_AND_SKILLS/schemas/graph_artifact.schema.json``.

Three properties are load-bearing:

* **Every node and edge id is resolved before anything is returned.** An endpoint that exists in no
  subject raises :class:`common.LearningOSDataError` naming the edge, which is what lets
  ``update-graphs`` exit 3 *before* it has written a single file. Resolution uses the same rule as
  ``validate``: a concept exists if it has a record, or is at least catalogued.
* **Subject isolation survives the cross-subject graph.** Each node carries the status recorded in
  its *own* subject's files. One subject's mastery is never applied to another subject's nodes,
  which is why a freshly initialised subject shows as ``unassessed`` beside a mastered neighbour.
* **Edges come from two sources, deduplicated**: ``concept_graph.json`` (the declared edge source of
  truth) and the ``prerequisite_ids`` of the concept records. A subject whose agent recorded
  prerequisites only on the records still gets a usable map, and a subject with both gets each edge
  once.

``generated_at`` and ``source_fingerprints`` (a sha256 per input file) record which state a picture
was drawn from. The only clock-dependent content is the ``review due`` marker, which is a statement
about today rather than about the stored state; everything else is a function of the files alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from common import (
    CONCEPT_ID_RE,
    SCHEMA_VERSION,
    TOOL_ID,
    LearningOSDataError,
    LearningOSUsageError,
    Workspace,
    parse_timestamp_safe,
    sha256_file,
    utc_now_iso,
)
from memory import events as events_mod
from memory import store
from validation import ConceptIndex

#: Every graph kind, in the order ``--all`` builds them.
KINDS: tuple[str, ...] = (
    "concept_prerequisite",
    "learner_mastery",
    "misconception",
    "cross_subject",
    "progress",
)

#: Kinds that describe exactly one subject.
PER_SUBJECT_KINDS: tuple[str, ...] = (
    "concept_prerequisite",
    "learner_mastery",
    "misconception",
    "progress",
)

#: The one kind that spans subjects and is written to ``03_MEMORY/graphs/_cross/``.
CROSS_SUBJECT_KIND = "cross_subject"

#: Status order used for legends and pie slices, so output order never depends on dict order.
STATUS_ORDER: tuple[str, ...] = (
    "unassessed",
    "developing",
    "practising",
    "provisionally_mastered",
    "needs_review",
)

#: Text for every status class. Meaning is carried by words, never by colour alone.
STATUS_MEANING: dict[str, str] = {
    "unassessed": "not probed yet, so there is no evidence either way",
    "developing": "attempted; not yet reliable",
    "practising": "succeeding with help, or unaided once",
    "provisionally_mastered": "two unaided successes, one delayed and one non-routine",
    "needs_review": "failed, a mistake recurred, or a review fell due",
}

#: How far up the ladder a status is, for the `progress` series. ``needs_review`` ranks with
#: ``unassessed`` rather than last, because arriving there is a regression, not an advance.
PROGRESS_RANK: dict[str, int] = {
    "unassessed": 0,
    "needs_review": 0,
    "developing": 1,
    "practising": 2,
    "provisionally_mastered": 3,
}

MISCONCEPTION_STATUS_ORDER: tuple[str, ...] = ("open", "monitoring", "resolved")
MISCONCEPTION_MEANING: dict[str, str] = {
    "open": "seen and not yet corrected",
    "monitoring": "corrected once; watching for a relapse",
    "resolved": "corrected and confirmed unaided",
}

#: Edge types that may appear in a concept graph.
EDGE_TYPES: tuple[str, ...] = ("prerequisite", "related", "applies-to")

#: Node id used when a graph has nothing to draw, so the Mermaid output is still valid and the
#: page says why it is empty instead of looking broken. Deliberately colon-free, so it can never
#: be mistaken for a concept id.
EMPTY_NODE_ID = "no-data"


# ------------------------------------------------------------------------------- concept lookup


def build_concept_index(ws: Workspace) -> ConceptIndex:
    """Which concept and misconception ids exist anywhere in the workspace.

    Shares :class:`validation.ConceptIndex` with ``validate`` on purpose: the two must never
    disagree about whether an id resolves. Loading goes through ``memory.store``, so a corrupt
    input file raises exit 3 here too, before any artefact is built.
    """
    index = ConceptIndex()
    for subject_id in ws.list_subjects():
        mastery = store.load_concept_mastery(ws, subject_id, required=False)
        for concept_id in (mastery or {}).get("concepts", {}):
            if isinstance(concept_id, str):
                index.records[concept_id] = subject_id
        catalog = store.load_concept_catalog(ws, subject_id, required=False)
        for entry in (catalog or {}).get("concepts", []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("concept_id"), str):
                index.catalogued.setdefault(entry["concept_id"], subject_id)
        misconceptions = store.load_misconceptions(ws, subject_id, required=False)
        for entry in (misconceptions or {}).get("misconceptions", []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("misconception_id"), str):
                index.misconceptions[entry["misconception_id"]] = subject_id
    return index


class _Sources:
    """Per-subject memory, loaded once, plus the fingerprints of what was read."""

    def __init__(self, ws: Workspace, subject_id: str) -> None:
        self.ws = ws
        self.subject_id = subject_id
        self.state = store.load_subject_state(ws, subject_id, required=False) or {}
        self.mastery = store.load_concept_mastery(ws, subject_id, required=False) or {}
        self.graph = store.load_concept_graph(ws, subject_id, required=False) or {}
        self.misconceptions = store.load_misconceptions(ws, subject_id, required=False) or {}
        self.queue = store.load_revision_queue(ws, subject_id, required=False) or {}

    @property
    def concepts(self) -> dict[str, dict[str, Any]]:
        found = self.mastery.get("concepts")
        return found if isinstance(found, dict) else {}

    def paths(self) -> list[Path]:
        return [
            store.subject_state_path(self.ws, self.subject_id),
            store.concept_mastery_path(self.ws, self.subject_id),
            store.concept_graph_path(self.ws, self.subject_id),
            store.misconceptions_path(self.ws, self.subject_id),
            store.revision_queue_path(self.ws, self.subject_id),
        ]


def fingerprints(ws: Workspace, paths: Iterable[Path]) -> dict[str, str]:
    """``{workspace-relative path: "sha256:<hex>"}`` for the files that actually existed."""
    found: dict[str, str] = {}
    for path in paths:
        if path.is_file():
            found[ws.rel(path)] = f"sha256:{sha256_file(path)}"
    return dict(sorted(found.items()))


# ------------------------------------------------------------------------------------- helpers


def _dangling(
    ws: Workspace,
    concept_id: str,
    *,
    path: Path,
    pointer: str,
    context: str,
) -> LearningOSDataError:
    return LearningOSDataError(
        f"{context}: {concept_id!r} does not exist in any subject",
        path=ws.rel(path),
        pointer=pointer,
        remedy=f"Create {concept_id!r} in subject {concept_id.split(':', 1)[0]!r}, or remove the "
        "reference. No graph file was written: a map with an endpoint that does not exist would "
        "be worse than no map.",
        code="dangling_edge",
    )


def _label_for(
    concept_id: str,
    sources: _Sources | None,
    catalog_names: dict[str, str],
) -> str:
    record = (sources.concepts.get(concept_id) if sources else None) or {}
    name = record.get("name")
    if isinstance(name, str) and name.strip():
        return name
    catalogued = catalog_names.get(concept_id)
    if catalogued:
        return catalogued
    slug = concept_id.split(":", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip() or concept_id


def _catalog_names_and_units(
    ws: Workspace,
    subject_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """``(names, units)`` from the catalogue, so a concept with no record still gets a label.

    The catalogue is the syllabus-side view: a concept can be planned there before it has a
    mastery record, and a prerequisite pointing at it should still draw with a readable name.
    """
    catalog = store.load_concept_catalog(ws, subject_id, required=False) or {}
    names: dict[str, str] = {}
    units: dict[str, str] = {}
    for entry in catalog.get("concepts", []) or []:
        if not isinstance(entry, dict):
            continue
        concept_id = entry.get("concept_id")
        if not isinstance(concept_id, str):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            names[concept_id] = name
        unit = entry.get("unit")
        if isinstance(unit, str) and unit.strip():
            units[concept_id] = unit
    return names, units


def _unit_for(
    concept_id: str,
    sources: _Sources | None,
    graph_units: dict[str, str],
    catalog_units: dict[str, str],
) -> str | None:
    record = (sources.concepts.get(concept_id) if sources else None) or {}
    unit = record.get("unit")
    if isinstance(unit, str) and unit.strip():
        return unit
    unit = graph_units.get(concept_id)
    if isinstance(unit, str) and unit.strip():
        return unit
    unit = catalog_units.get(concept_id)
    if isinstance(unit, str) and unit.strip():
        return unit
    return None


def _graph_units(sources: _Sources) -> dict[str, str]:
    units: dict[str, str] = {}
    for node in sources.graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        concept_id = node.get("concept_id")
        unit = node.get("unit")
        if isinstance(concept_id, str) and isinstance(unit, str) and unit.strip():
            units[concept_id] = unit
    return units


def _declared_edges(
    ws: Workspace,
    sources: _Sources,
    index: ConceptIndex,
) -> list[tuple[str, str, str]]:
    """Edges from ``concept_graph.json``, every endpoint resolved. Raises on a dangling one."""
    path = store.concept_graph_path(ws, sources.subject_id)
    edges: list[tuple[str, str, str]] = []
    for position, node in enumerate(sources.graph.get("nodes", []) or []):
        if not isinstance(node, dict):
            continue
        concept_id = node.get("concept_id")
        if isinstance(concept_id, str) and not index.knows(concept_id):
            raise _dangling(
                ws,
                concept_id,
                path=path,
                pointer=f"/nodes/{position}/concept_id",
                context="concept_graph node",
            )
    for position, edge in enumerate(sources.graph.get("edges", []) or []):
        if not isinstance(edge, dict):
            continue
        source = edge.get("from")
        target = edge.get("to")
        edge_type = edge.get("type") if edge.get("type") in EDGE_TYPES else "prerequisite"
        for role, value in (("from", source), ("to", target)):
            if not isinstance(value, str):
                continue
            if not index.knows(value):
                raise _dangling(
                    ws,
                    value,
                    path=path,
                    pointer=f"/edges/{position}/{role}",
                    context=f"edge {source!r} -> {target!r} has a dangling {role} endpoint",
                )
        if isinstance(source, str) and isinstance(target, str):
            edges.append((source, target, str(edge_type)))
    return edges


def _record_prerequisite_edges(
    ws: Workspace,
    sources: _Sources,
    index: ConceptIndex,
) -> list[tuple[str, str, str]]:
    """``prerequisite_ids`` as edges, every endpoint resolved. Raises on a dangling one."""
    path = store.concept_mastery_path(ws, sources.subject_id)
    edges: list[tuple[str, str, str]] = []
    for concept_id in sorted(sources.concepts):
        record = sources.concepts[concept_id]
        if not isinstance(record, dict):
            continue
        for position, prerequisite in enumerate(record.get("prerequisite_ids") or []):
            if not isinstance(prerequisite, str):
                continue
            if not index.knows(prerequisite):
                raise _dangling(
                    ws,
                    prerequisite,
                    path=path,
                    pointer=f"/concepts/{concept_id}/prerequisite_ids/{position}",
                    context=f"{concept_id} requires a prerequisite that is missing",
                )
            edges.append((prerequisite, concept_id, "prerequisite"))
    return edges


def _due_concepts(sources: _Sources, now: str) -> set[str]:
    """Concepts whose scheduled review has fallen due as of ``now``.

    This is the one piece of a graph that depends on the clock, because "due" is a claim about
    today rather than about the stored state. It is read from ``revision_queue.json``, which is
    derived state, so it never affects what counts as evidence.
    """
    moment = parse_timestamp_safe(now)
    due: set[str] = set()
    if moment is None:
        return due
    for item in sources.queue.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        concept_id = item.get("concept_id")
        due_at = parse_timestamp_safe(item.get("due_at"))
        if isinstance(concept_id, str) and due_at is not None and due_at <= moment:
            due.add(concept_id)
    return due


def _status_of(record: dict[str, Any] | None) -> str:
    status = (record or {}).get("status")
    return status if status in STATUS_ORDER else "unassessed"


def _mastery_of(record: dict[str, Any] | None) -> float | None:
    value = (record or {}).get("mastery_estimate")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    return None


def _status_legend(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "key": status,
            "label": f"{status.replace('_', ' ')} — {STATUS_MEANING[status]} "
            f"({counts.get(status, 0)})",
            "style": status,
        }
        for status in STATUS_ORDER
    ]


def _empty_node(message: str) -> dict[str, Any]:
    return {
        "id": EMPTY_NODE_ID,
        "label": message,
        "group": None,
        "status": None,
        "mastery": None,
        "style_class": None,
        "shape": "round",
        "value": None,
    }


def _node(
    concept_id: str,
    *,
    label: str,
    group: str | None = None,
    status: str | None = None,
    mastery: float | None = None,
    style_class: str | None = None,
    shape: str | None = "rect",
    value: float | None = None,
) -> dict[str, Any]:
    return {
        "id": concept_id,
        "label": label,
        "group": group,
        "status": status,
        "mastery": mastery,
        "style_class": style_class,
        "shape": shape,
        "value": value,
    }


def _edge(source: str, target: str, edge_type: str, label: str | None = None) -> dict[str, Any]:
    return {"from": source, "to": target, "type": edge_type, "label": label}


def _dedupe_edges(edges: Sequence[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for edge in edges:
        if edge in seen:
            continue
        seen.add(edge)
        unique.append(edge)
    return sorted(unique)


def _frame(
    kind: str,
    *,
    subject_ids: Sequence[str],
    title: str,
    generated_at: str,
    source_fingerprints: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "graph_id": f"{kind}-{'+'.join(subject_ids)}",
        "kind": kind,
        "subject_ids": list(subject_ids),
        "generated_at": generated_at,
        "generator": f"{TOOL_ID} graphs.build",
        "title": title,
        "source_fingerprints": source_fingerprints,
        "legend": [],
        "nodes": [],
        "edges": [],
    }


# --------------------------------------------------------------------------------- the five kinds


def build_graph(
    ws: Workspace,
    subject_id: str | None,
    kind: str,
    *,
    index: ConceptIndex | None = None,
    now: str | None = None,
    subject_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build one graph artefact. ``subject_id`` is ignored for ``cross_subject``.

    Raises :class:`common.LearningOSUsageError` for an unknown kind and
    :class:`common.LearningOSDataError` for a dangling reference or a corrupt input file.
    """
    if kind not in KINDS:
        raise LearningOSUsageError(
            f"unknown graph kind {kind!r}",
            remedy="Valid kinds: " + ", ".join(KINDS),
        )
    generated_at = now or utc_now_iso()
    resolved_index = index if index is not None else build_concept_index(ws)

    if kind == CROSS_SUBJECT_KIND:
        graph = _build_cross_subject(
            ws,
            subject_ids if subject_ids is not None else ws.list_subjects(),
            resolved_index,
            generated_at,
        )
    else:
        if not subject_id:
            raise LearningOSUsageError(
                f"the {kind} graph needs a subject",
                remedy="Pass --subject <id>, or --all to cover every subject.",
            )
        store.require_subject(ws, subject_id)
        builder = {
            "concept_prerequisite": _build_concept_prerequisite,
            "learner_mastery": _build_learner_mastery,
            "misconception": _build_misconception,
            "progress": _build_progress,
        }[kind]
        graph = builder(ws, subject_id, resolved_index, generated_at)

    store.registry(ws).validate_or_raise(
        "graph_artifact",
        graph,
        path=f"{ws.rel(ws.graphs)}/{kind}.json",
        remedy="This is a bug in graphs/build.py: the artefact it assembled does not match "
        "graph_artifact.schema.json. Nothing was written.",
    )
    return graph


def _subject_nodes(
    ws: Workspace,
    sources: _Sources,
    index: ConceptIndex,
    *,
    extra_ids: Iterable[str] = (),
) -> tuple[list[str], dict[str, str], dict[str, str], dict[str, str]]:
    """``(sorted concept ids, labels, units, catalogue names)`` for one subject."""
    catalog_names, catalog_units = _catalog_names_and_units(ws, sources.subject_id)
    graph_units = _graph_units(sources)
    ids = set(sources.concepts)
    for node in sources.graph.get("nodes", []) or []:
        if isinstance(node, dict) and isinstance(node.get("concept_id"), str):
            ids.add(node["concept_id"])
    for concept_id in extra_ids:
        ids.add(concept_id)
    ids = {
        concept_id
        for concept_id in ids
        if CONCEPT_ID_RE.match(concept_id)
        and concept_id.split(":", 1)[0] == sources.subject_id
    }
    ordered = sorted(ids)
    labels = {
        concept_id: _label_for(concept_id, sources, catalog_names) for concept_id in ordered
    }
    units = {
        concept_id: _unit_for(concept_id, sources, graph_units, catalog_units) or ""
        for concept_id in ordered
    }
    return ordered, labels, units, catalog_names


def _build_concept_prerequisite(
    ws: Workspace,
    subject_id: str,
    index: ConceptIndex,
    generated_at: str,
) -> dict[str, Any]:
    sources = _Sources(ws, subject_id)
    declared = _declared_edges(ws, sources, index)
    derived = _record_prerequisite_edges(ws, sources, index)
    edges = _dedupe_edges([*declared, *derived])
    ids, labels, units, _names = _subject_nodes(
        ws,
        sources,
        index,
        extra_ids=[
            endpoint
            for edge in edges
            for endpoint in (edge[0], edge[1])
        ],
    )
    inside = set(ids)
    graph = _frame(
        "concept_prerequisite",
        subject_ids=[subject_id],
        title=f"{subject_id}: concept prerequisites",
        generated_at=generated_at,
        source_fingerprints=fingerprints(ws, sources.paths()),
    )
    graph["nodes"] = [
        _node(
            concept_id,
            label=labels[concept_id],
            group=units[concept_id] or None,
            shape="rect",
            style_class="concept",
        )
        for concept_id in ids
    ] or [_empty_node("no concepts recorded yet for this subject")]
    graph["edges"] = [
        _edge(source, target, edge_type, edge_type)
        for source, target, edge_type in edges
        if source in inside and target in inside
    ]
    graph["legend"] = [
        {
            "key": "concept",
            "label": "concept, grouped into its syllabus unit",
            "style": "concept",
        },
        {
            "key": "prerequisite",
            "label": "A --> B means A is a prerequisite of B",
            "style": None,
        },
    ]
    return graph


def _build_learner_mastery(
    ws: Workspace,
    subject_id: str,
    index: ConceptIndex,
    generated_at: str,
) -> dict[str, Any]:
    sources = _Sources(ws, subject_id)
    declared = _declared_edges(ws, sources, index)
    derived = _record_prerequisite_edges(ws, sources, index)
    edges = [
        edge for edge in _dedupe_edges([*declared, *derived]) if edge[2] == "prerequisite"
    ]
    ids, labels, _units, _names = _subject_nodes(
        ws,
        sources,
        index,
        extra_ids=[endpoint for edge in edges for endpoint in (edge[0], edge[1])],
    )
    inside = set(ids)
    due = _due_concepts(sources, generated_at)
    counts = {status: 0 for status in STATUS_ORDER}
    nodes: list[dict[str, Any]] = []
    for concept_id in ids:
        record = sources.concepts.get(concept_id)
        status = _status_of(record)
        counts[status] += 1
        mastery = _mastery_of(record)
        annotations = [status.replace("_", " ")]
        if mastery is not None:
            annotations.append(f"{mastery:.2f}")
        if concept_id in due:
            annotations.append("review due")
        nodes.append(
            _node(
                concept_id,
                label=f"{labels[concept_id]} [{', '.join(annotations)}]",
                group=None,
                status=status,
                mastery=mastery,
                style_class=status,
                shape="rect",
            )
        )
    graph = _frame(
        "learner_mastery",
        subject_ids=[subject_id],
        title=f"{subject_id}: mastery by concept",
        generated_at=generated_at,
        source_fingerprints=fingerprints(ws, sources.paths()),
    )
    graph["nodes"] = nodes or [_empty_node("no concepts recorded yet for this subject")]
    graph["edges"] = [
        _edge(source, target, edge_type, edge_type)
        for source, target, edge_type in edges
        if source in inside and target in inside
    ]
    graph["legend"] = _status_legend(counts)
    return graph


def _build_misconception(
    ws: Workspace,
    subject_id: str,
    index: ConceptIndex,
    generated_at: str,
) -> dict[str, Any]:
    sources = _Sources(ws, subject_id)
    path = store.misconceptions_path(ws, subject_id)
    entries = [
        entry
        for entry in sources.misconceptions.get("misconceptions", []) or []
        if isinstance(entry, dict) and isinstance(entry.get("misconception_id"), str)
    ]
    linked: set[str] = set()
    edges: list[dict[str, Any]] = []
    counts = {status: 0 for status in MISCONCEPTION_STATUS_ORDER}
    nodes: list[dict[str, Any]] = []
    for position, entry in enumerate(sorted(entries, key=lambda item: item["misconception_id"])):
        misconception_id = entry["misconception_id"]
        status = entry.get("status") if entry.get("status") in counts else "open"
        counts[str(status)] += 1
        description = entry.get("description")
        label = description if isinstance(description, str) and description.strip() else (
            misconception_id
        )
        nodes.append(
            _node(
                misconception_id,
                label=f"{misconception_id}: {label}",
                group=None,
                status=None,
                style_class=f"mc_{status}",
                shape="hexagon",
            )
        )
        for offset, concept_id in enumerate(entry.get("concept_ids") or []):
            if not isinstance(concept_id, str):
                continue
            if not index.knows(concept_id):
                raise _dangling(
                    ws,
                    concept_id,
                    path=path,
                    pointer=f"/misconceptions/{position}/concept_ids/{offset}",
                    context=f"misconception {misconception_id} points at a concept that is missing",
                )
            linked.add(concept_id)
            edges.append(_edge(concept_id, misconception_id, "exhibits", "exhibits"))

    ids, labels, _units, _names = _subject_nodes(ws, sources, index, extra_ids=linked)
    concept_nodes = [
        _node(
            concept_id,
            label=labels.get(concept_id, concept_id),
            group=None,
            status=_status_of(sources.concepts.get(concept_id)),
            mastery=_mastery_of(sources.concepts.get(concept_id)),
            style_class="concept",
            shape="rect",
        )
        for concept_id in ids
        if concept_id in linked
    ]
    # A concept in another subject can legitimately exhibit this subject's misconception; keep it
    # as a node so the edge has an endpoint, but do not pretend to know its status.
    for concept_id in sorted(linked - set(ids)):
        concept_nodes.append(
            _node(
                concept_id,
                label=f"{concept_id} (other subject)",
                group=None,
                status=None,
                style_class="concept",
                shape="rect",
            )
        )

    graph = _frame(
        "misconception",
        subject_ids=[subject_id],
        title=f"{subject_id}: misconceptions and the concepts they touch",
        generated_at=generated_at,
        source_fingerprints=fingerprints(ws, sources.paths()),
    )
    graph["nodes"] = [*concept_nodes, *nodes] or [
        _empty_node("no misconceptions recorded yet — nothing to draw, which is good news")
    ]
    graph["edges"] = sorted(edges, key=lambda edge: (edge["from"], edge["to"], edge["type"]))
    graph["legend"] = [
        {"key": "concept", "label": "concept that exhibited the misconception", "style": "concept"},
        *[
            {
                "key": f"mc_{status}",
                "label": f"misconception, {status} — {MISCONCEPTION_MEANING[status]} "
                f"({counts[status]})",
                "style": f"mc_{status}",
            }
            for status in MISCONCEPTION_STATUS_ORDER
        ],
    ]
    return graph


def _build_progress(
    ws: Workspace,
    subject_id: str,
    index: ConceptIndex,
    generated_at: str,
) -> dict[str, Any]:
    sources = _Sources(ws, subject_id)
    counts = {status: 0 for status in STATUS_ORDER}
    for record in sources.concepts.values():
        if isinstance(record, dict):
            counts[_status_of(record)] += 1

    advances: dict[str, int] = {}
    events, _findings = events_mod.read_events(
        ws, subject_id=subject_id, types=["mastery_updated"]
    )
    for event in events:
        payload = event.get("payload") or {}
        before = payload.get("status_before")
        after = payload.get("status_after")
        if not isinstance(after, str) or after not in STATUS_ORDER or before == after:
            continue
        # Only count a real advance up the ladder. A drop to needs_review is movement, but it is
        # not progress, and a graph that counted it as progress would be flattering rather than
        # useful.
        if (
            isinstance(before, str)
            and before in PROGRESS_RANK
            and PROGRESS_RANK[after] <= PROGRESS_RANK[before]
        ):
            continue
        session_id = str(event.get("session_id") or "unknown")
        advances[session_id] = advances.get(session_id, 0) + 1

    graph = _frame(
        "progress",
        subject_ids=[subject_id],
        title=f"{subject_id}: concept status distribution",
        generated_at=generated_at,
        # The advance series is read from the event log, but the log is deliberately NOT
        # fingerprinted: it is append-only, so its hash moves with every write — including the
        # `graph_generated` event this very command appends. Fingerprinting it would make a
        # perfectly reproducible artefact look different on every run. The series itself is
        # stored below, which is the part a reader can check.
        source_fingerprints=fingerprints(ws, sources.paths()),
    )
    graph["nodes"] = [
        _node(
            f"status-{status}",
            label=f"{status.replace('_', ' ')} ({counts[status]})",
            group=None,
            status=status,
            style_class=status,
            shape=None,
            value=float(counts[status]),
        )
        for status in STATUS_ORDER
    ]
    graph["edges"] = []
    graph["series"] = [
        {
            "key": session_id,
            "value": float(advances[session_id]),
            "label": f"{advances[session_id]} concept(s) advanced",
        }
        for session_id in sorted(advances)
    ]
    graph["legend"] = [
        *_status_legend(counts),
        {
            "key": "series",
            "label": f"{len(advances)} session(s) in which a concept advanced a status",
            "style": None,
        },
    ]
    return graph


def _build_cross_subject(
    ws: Workspace,
    subject_ids: Sequence[str],
    index: ConceptIndex,
    generated_at: str,
) -> dict[str, Any]:
    ordered = sorted({str(subject_id) for subject_id in subject_ids})
    if not ordered:
        raise LearningOSUsageError(
            "the cross_subject graph needs at least one subject",
            remedy='Create one with `init-subject --id <id> --name "<name>"` first.',
        )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    read_paths: list[Path] = []
    owners: dict[str, str] = {}
    counts = {status: 0 for status in STATUS_ORDER}
    per_subject: dict[str, _Sources] = {}

    for subject_id in ordered:
        store.require_subject(ws, subject_id)
        sources = _Sources(ws, subject_id)
        per_subject[subject_id] = sources
        read_paths.extend(sources.paths())
        ids, labels, _units, _names = _subject_nodes(ws, sources, index)
        for concept_id in ids:
            # Each node keeps the status from its OWN subject's files. This is the line that
            # makes cross-subject isolation true in the picture as well as in the data.
            record = sources.concepts.get(concept_id)
            status = _status_of(record)
            counts[status] += 1
            owners[concept_id] = subject_id
            nodes.append(
                _node(
                    concept_id,
                    label=f"{labels[concept_id]} [{status.replace('_', ' ')}]",
                    group=subject_id,
                    status=status,
                    mastery=_mastery_of(record),
                    style_class=status,
                    shape="rect",
                )
            )

    for subject_id in ordered:
        sources = per_subject[subject_id]
        declared = _declared_edges(ws, sources, index)
        derived = _record_prerequisite_edges(ws, sources, index)
        for source, target, edge_type in _dedupe_edges([*declared, *derived]):
            if edge_type != "prerequisite":
                continue
            left = source.split(":", 1)[0]
            right = target.split(":", 1)[0]
            if left == right:
                continue  # an intra-subject edge belongs to that subject's own graph
            edges.append(_edge(source, target, "prerequisite", "prerequisite"))
            for endpoint in (source, target):
                if endpoint not in owners:
                    # A prerequisite in a subject that is not part of this graph: show it, but
                    # never guess its status.
                    owners[endpoint] = endpoint.split(":", 1)[0]
                    nodes.append(
                        _node(
                            endpoint,
                            label=f"{endpoint.split(':', 1)[-1].replace('-', ' ')} [not in this "
                            "graph]",
                            group=endpoint.split(":", 1)[0],
                            status=None,
                            style_class="unassessed",
                            shape="rect",
                        )
                    )

    graph = _frame(
        "cross_subject",
        subject_ids=ordered,
        title="cross-subject prerequisites: " + ", ".join(ordered),
        generated_at=generated_at,
        source_fingerprints=fingerprints(ws, read_paths),
    )
    graph["nodes"] = sorted(nodes, key=lambda node: str(node["id"])) or [
        _empty_node("no concepts recorded in any subject yet")
    ]
    seen: set[tuple[str, str]] = set()
    unique_edges: list[dict[str, Any]] = []
    for edge in sorted(edges, key=lambda item: (item["from"], item["to"])):
        key = (str(edge["from"]), str(edge["to"]))
        if key in seen:
            continue
        seen.add(key)
        unique_edges.append(edge)
    graph["edges"] = unique_edges
    graph["legend"] = [
        {"key": "subject", "label": "one subgraph per subject", "style": "subject"},
        *_status_legend(counts),
        {
            "key": "isolation",
            "label": "every node keeps the status recorded in its own subject's files",
            "style": None,
        },
    ]
    return graph


def kinds_for(selection: str | Sequence[str] | None) -> list[str]:
    """Parse a ``--kinds a,b,c`` selection into validated kind names."""
    if selection is None:
        return list(KINDS)
    if isinstance(selection, str):
        wanted = [part.strip() for part in selection.split(",") if part.strip()]
    else:
        wanted = [str(part).strip() for part in selection if str(part).strip()]
    if not wanted:
        raise LearningOSUsageError(
            "--kinds was empty",
            remedy="Give a comma-separated list, e.g. --kinds concept_prerequisite,progress. "
            "Valid kinds: " + ", ".join(KINDS),
        )
    unknown = [kind for kind in wanted if kind not in KINDS]
    if unknown:
        raise LearningOSUsageError(
            "unknown graph kind(s): " + ", ".join(sorted(unknown)),
            remedy="Valid kinds: " + ", ".join(KINDS),
        )
    return [kind for kind in KINDS if kind in set(wanted)]
