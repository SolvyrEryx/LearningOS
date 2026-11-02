"""The knowledge-graph pipeline: memory JSON -> graph JSON -> Mermaid -> optional SVG/HTML.

```
memory files  ->  graph artefact JSON  ->  Mermaid text  ->  { .svg via mmdc | self-contained .html }
   (truth)         (graphs.build)          (graphs.mermaid)         (graphs.render)
```

Data first, for two reasons. The artefact survives a missing renderer — a picture is a
convenience, the graph is the thing — and because the Mermaid step is a *pure* function of the
JSON, the same memory always produces byte-identical ``.mmd`` output, so a regenerated graph shows
up as "no change" rather than as noise.

Nothing in this package writes to a source-of-truth memory file, and deleting
``03_MEMORY/graphs/`` is always safe.
"""

from __future__ import annotations

__all__ = ["build", "mermaid", "render"]
