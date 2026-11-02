"""Writing graph artefacts to disk: ``<kind>.json``, ``.mmd``, ``.html`` and optionally ``.svg``.

This is the only module in ``tools/`` permitted to start a process, and the only thing it starts is
``mmdc`` (mermaid-cli) when that is already on PATH — with an argument list, never a shell string,
and with a timeout. Nothing is downloaded and nothing is installed: when the renderer is absent the
run prints

    SVG skipped: mermaid-cli (mmdc) not found on PATH

and still exits 0, because the Mermaid text *is* the artefact and the picture is a convenience.
Only ``--require-renderer`` turns the absence into a failure (exit 6).

The HTML page is a learning artefact, not an application: no network requests, no CDN script tag,
no fonts, no analytics. If ``graphs/vendor/mermaid.min.js`` has been dropped in by hand it is
*inlined*, so the page renders offline and stays self-contained when copied elsewhere. Otherwise
the page shows the escaped Mermaid source plus a visible notice explaining the two ways to get a
rendered diagram.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from common import (
    LearningOSMissingDependencyError,
    LearningOSUsageError,
    atomic_write_json,
    atomic_write_text,
    ensure_dir,
)
from graphs.mermaid import escape_label, to_mermaid

#: Formats ``--format`` accepts. ``json`` is always written: it is the artefact everything else is
#: derived from, so making it optional would be a trap.
FORMATS: tuple[str, ...] = ("json", "mmd", "html", "svg")
DEFAULT_FORMATS: tuple[str, ...] = ("json", "mmd", "html", "svg")

#: Printed verbatim when the renderer is missing. Tests and the docs both quote this string.
SVG_SKIP_NOTICE = "SVG skipped: mermaid-cli (mmdc) not found on PATH"

#: Where a hand-placed renderer is looked for. Nothing ever writes here.
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
VENDOR_SCRIPT_NAME = "mermaid.min.js"

#: Seconds before a stuck ``mmdc`` is abandoned. It is an optional nicety; it does not get to hang
#: the learner's session.
RENDER_TIMEOUT_SECONDS = 120

#: Instructional comments in the shipped HTML template document the placeholders for whoever
#: edits it. They are stripped from the generated page: a comment that mentions ``{{...}}`` is
#: addressed to the implementer, not to the learner reading their own graph.
_HTML_COMMENT_RE = re.compile(r"[ \t]*<!--.*?-->\n?", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


def _strip_instructional_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub(
        lambda match: "" if "{{" in match.group(0) else match.group(0), text
    )

NO_RENDERER_NOTICE = """  <section class="notice">
    <h2>Diagram not rendered</h2>
    <p>The Mermaid source above is the artefact; rendering it as a picture is optional. Two ways to
      get a rendered diagram, both local and both one-off:</p>
    <ol>
      <li>Place <code>mermaid.min.js</code> in
        <code>01_AGENTS_AND_SKILLS/tools/graphs/vendor/</code>, then regenerate. The file is then
        inlined into this page, which keeps it self-contained and offline.</li>
      <li>Install mermaid-cli so that <code>mmdc</code> is on PATH, then regenerate with
        <code>--format mmd,html,svg</code> to get an <code>.svg</code> beside this file.</li>
    </ol>
    <p>Neither is required. Nothing is downloaded automatically.</p>
  </section>"""

FALLBACK_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}} - LearningOS</title>
</head>
<body>
<main>
  <h1>{{TITLE}}</h1>
  <dl>
    <dt>Kind</dt><dd>{{KIND}}</dd>
    <dt>Subjects</dt><dd>{{SUBJECT_IDS}}</dd>
    <dt>Generated</dt><dd>{{GENERATED_AT}}</dd>
    <dt>Size</dt><dd>{{NODE_COUNT}} nodes, {{EDGE_COUNT}} edges</dd>
  </dl>
  <table>
    <caption>Legend</caption>
    <thead><tr><th scope="col">Key</th><th scope="col">Meaning</th></tr></thead>
    <tbody>
{{LEGEND_ROWS}}
    </tbody>
  </table>
  <h2>Diagram</h2>
  <pre class="mermaid">{{MERMAID_SOURCE}}</pre>
{{RENDERER_BLOCK}}
</main>
</body>
</html>
"""


@dataclass
class RenderResult:
    """What :func:`write_artifacts` wrote, and what it could not."""

    kind: str
    out_dir: Path
    written: list[Path] = field(default_factory=list)
    planned: list[Path] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    svg_skipped: bool = False
    renderer: str | None = None
    inlined_renderer: bool = False
    dry_run: bool = False

    @property
    def formats(self) -> list[str]:
        return sorted({path.suffix.lstrip(".") for path in (self.planned or self.written)})

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "out_dir": str(self.out_dir),
            "written": [str(path) for path in self.written],
            "planned": [str(path) for path in self.planned],
            "formats": self.formats,
            "svg_skipped": self.svg_skipped,
            "renderer": self.renderer,
            "inlined_renderer": self.inlined_renderer,
            "notices": list(self.notices),
            "dry_run": self.dry_run,
        }


def parse_formats(selection: str | Sequence[str] | None) -> list[str]:
    """Parse ``--format mmd,html,svg``. ``json`` is added whether or not it was asked for."""
    if selection is None:
        wanted = list(DEFAULT_FORMATS)
    elif isinstance(selection, str):
        wanted = [part.strip().lstrip(".") for part in selection.split(",") if part.strip()]
    else:
        wanted = [str(part).strip().lstrip(".") for part in selection if str(part).strip()]
    unknown = sorted({item for item in wanted if item not in FORMATS})
    if unknown:
        raise LearningOSUsageError(
            "unknown --format value(s): " + ", ".join(unknown),
            remedy="Valid formats: " + ", ".join(FORMATS) + ". json is always written.",
        )
    if not wanted:
        raise LearningOSUsageError(
            "--format was empty",
            remedy="Give a comma-separated list, e.g. --format mmd,html.",
        )
    return [item for item in FORMATS if item in set(wanted) or item == "json"]


def renderer_path() -> str | None:
    """Absolute path of ``mmdc``, or ``None``. A capability probe, not an install."""
    return shutil.which("mmdc")


def vendored_renderer(vendor_dir: Path | None = None) -> Path | None:
    """The hand-placed ``mermaid.min.js``, if the learner put one there."""
    candidate = (vendor_dir or VENDOR_DIR) / VENDOR_SCRIPT_NAME
    return candidate if candidate.is_file() else None


def _legend_rows(graph: Mapping[str, Any]) -> str:
    rows = []
    for entry in graph.get("legend") or []:
        if not isinstance(entry, dict):
            continue
        key = html.escape(str(entry.get("key", "")))
        label = html.escape(str(entry.get("label", "")))
        rows.append(f"      <tr><th scope=\"row\">{key}</th><td>{label}</td></tr>")
    if not rows:
        rows.append(
            '      <tr><th scope="row">-</th><td>this graph needs no legend</td></tr>'
        )
    return "\n".join(rows)


def _renderer_block(vendor_dir: Path | None) -> tuple[str, bool]:
    script = vendored_renderer(vendor_dir)
    if script is None:
        return NO_RENDERER_NOTICE, False
    source = script.read_text(encoding="utf-8", errors="replace")
    # Inlined, not linked: the page must work with no network and survive being copied anywhere.
    block = (
        "  <script>\n"
        f"{source}\n"
        "  </script>\n"
        "  <script>\n"
        "    if (window.mermaid) {\n"
        "      window.mermaid.initialize({ startOnLoad: true, securityLevel: 'strict' });\n"
        "    }\n"
        "  </script>"
    )
    return block, True


def render_html(
    graph: Mapping[str, Any],
    mermaid_source: str,
    *,
    template_path: Path | None = None,
    vendor_dir: Path | None = None,
) -> tuple[str, bool]:
    """Build the self-contained viewer page. Returns ``(html, renderer_was_inlined)``.

    Uses ``01_AGENTS_AND_SKILLS/templates/graph_templates/viewer.html.tmpl`` when it is available
    (that is what the template is for), and a built-in equivalent when the tooling has been copied
    somewhere without it.
    """
    template = FALLBACK_TEMPLATE
    if template_path is not None and Path(template_path).is_file():
        template = _strip_instructional_comments(
            Path(template_path).read_text(encoding="utf-8")
        )
    block, inlined = _renderer_block(vendor_dir)
    values = {
        "TITLE": html.escape(str(graph.get("title") or graph.get("graph_id") or "LearningOS")),
        "KIND": html.escape(str(graph.get("kind"))),
        "SUBJECT_IDS": html.escape(", ".join(str(s) for s in graph.get("subject_ids") or [])),
        "GENERATED_AT": html.escape(str(graph.get("generated_at") or "")),
        "NODE_COUNT": str(len(graph.get("nodes") or [])),
        "EDGE_COUNT": str(len(graph.get("edges") or [])),
        "MERMAID_SOURCE": html.escape(mermaid_source),
        "LEGEND_ROWS": _legend_rows(graph),
        "RENDERER_BLOCK": block,
    }
    # One pass, so a substituted value is never rescanned for placeholders.
    page = _PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), match.group(0)), template)
    return page, inlined


def _render_svg(mmd_path: Path, svg_path: Path) -> tuple[bool, str | None]:
    """Invoke ``mmdc``. Returns ``(wrote_svg, notice)``; never raises on a renderer failure."""
    executable = renderer_path()
    if executable is None:  # pragma: no cover - guarded by the caller
        return False, SVG_SKIP_NOTICE
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell, no user string
            [executable, "-i", str(mmd_path), "-o", str(svg_path)],
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"SVG skipped: mmdc did not finish within {RENDER_TIMEOUT_SECONDS}s. "
            "The .mmd and .html were still written."
        )
    except OSError as exc:
        return False, f"SVG skipped: mmdc could not be started ({exc}). The .mmd was still written."
    if completed.returncode != 0 or not svg_path.is_file():
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {completed.returncode}"
        return False, f"SVG skipped: mmdc failed ({tail}). The .mmd and .html were still written."
    return True, None


def write_artifacts(
    graph: Mapping[str, Any],
    out_dir: str | Path,
    formats: Sequence[str] | None = None,
    *,
    require_renderer: bool = False,
    dry_run: bool = False,
    template_path: Path | None = None,
    vendor_dir: Path | None = None,
) -> RenderResult:
    """Write one graph's artefacts into ``out_dir``.

    Writes nothing at all under ``dry_run``; raises
    :class:`common.LearningOSMissingDependencyError` (exit 6) only when ``require_renderer`` is set
    and ``mmdc`` is absent.
    """
    wanted = [item for item in (formats or DEFAULT_FORMATS)]
    kind = str(graph.get("kind"))
    directory = Path(out_dir)
    result = RenderResult(kind=kind, out_dir=directory, dry_run=dry_run)
    mermaid_source = to_mermaid(graph)

    if "svg" in wanted:
        executable = renderer_path()
        result.renderer = executable
        if executable is None:
            if require_renderer:
                raise LearningOSMissingDependencyError(
                    "mermaid-cli (mmdc) is not on PATH, and --require-renderer was given",
                    remedy="Install mermaid-cli yourself (`npm install -g @mermaid-js/mermaid-cli`) "
                    "or drop the flag: the .mmd and .html are written either way. LearningOS never "
                    "installs anything for you.",
                )
            result.svg_skipped = True
            result.notices.append(SVG_SKIP_NOTICE)

    for item in wanted:
        if item == "svg" and result.svg_skipped:
            continue
        result.planned.append(directory / f"{kind}.{item}")

    if dry_run:
        return result

    ensure_dir(directory)
    for item in wanted:
        target = directory / f"{kind}.{item}"
        if item == "json":
            atomic_write_json(target, dict(graph))
            result.written.append(target)
        elif item == "mmd":
            atomic_write_text(target, mermaid_source)
            result.written.append(target)
        elif item == "html":
            page, inlined = render_html(
                graph,
                mermaid_source,
                template_path=template_path,
                vendor_dir=vendor_dir,
            )
            result.inlined_renderer = inlined
            atomic_write_text(target, page)
            result.written.append(target)
            if not inlined:
                result.notices.append(
                    "HTML written without an inlined renderer: the page shows the Mermaid source "
                    "plus instructions. See graphs/vendor/README.md to enable rendering."
                )
        elif item == "svg":
            if result.svg_skipped:
                continue
            mmd_path = directory / f"{kind}.mmd"
            if not mmd_path.is_file():
                atomic_write_text(mmd_path, mermaid_source)
            wrote, notice = _render_svg(mmd_path, target)
            if wrote:
                result.written.append(target)
            else:
                result.svg_skipped = True
                if notice:
                    result.notices.append(notice)
    return result


def escaped_mermaid_for_html(mermaid_source: str) -> str:
    """The Mermaid text as it appears inside ``<pre class="mermaid">``."""
    return html.escape(mermaid_source)


def legend_label(entry: Mapping[str, Any]) -> str:
    """One legend row as plain text — used by the HTML table and by tests."""
    return f"{entry.get('key')}: {escape_label(entry.get('label'))}"
