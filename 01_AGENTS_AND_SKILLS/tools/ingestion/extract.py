"""Text extraction from one source document.

The contract is in ``01_AGENTS_AND_SKILLS/core/source_handling.md`` and it has three edges that
matter more than the happy path:

1. **Originals are immutable.** Every file here is opened ``"rb"`` and nothing is ever written back
   to it. The derived text goes beside it in ``extracted_text/``, never over it.
2. **Missing text is never "no content".** A scanned PDF yields almost nothing; recording that as
   an empty document would let the agent claim a topic is absent from the learner's material when
   really it could not read the page. So an extraction that produced nothing is reported as an
   *un-extracted source needing OCR*, with warnings, and no text file is written at all.
3. **`pypdf` is optional.** It is imported inside a ``try`` at call time. When it is absent the
   function still returns an :class:`Extraction` — carrying the install hint as a warning and
   ``needs_ocr=None``, because "we do not know" is not the same as "it needs OCR". Nothing is ever
   installed, downloaded or shelled out to; the hint is prose for the learner to act on.

Locators differ by kind: PDFs get ``--- page N ---`` markers in the derived text and are cited as
``[source_id p.N]``; ``.txt``/``.md`` sources are cited by line number of the *original* file
(``[source_id l.N]``), so no markers are injected into their body.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import TOOL_ID, optional_dependency, utc_now_iso

# ------------------------------------------------------------------------------------ thresholds

#: Below this many characters on a page, the page is treated as having no usable text.
SCANNED_PAGE_CHAR_THRESHOLD = 50

#: When this fraction of pages or more is under the threshold, the document needs OCR.
NEEDS_OCR_PAGE_FRACTION = 0.6

#: An extraction shorter than this is flagged; it is usually a partial read, not a short document.
SHORT_EXTRACTION_CHARS = 200

#: Printed into the page-separator line of the derived text.
PAGE_MARKER = "--- page {page} ---"

# -------------------------------------------------------------------------------------- warnings

PYPDF_MISSING_WARNING = (
    "pypdf not installed; PDF text extraction unavailable — install with "
    "`python -m pip install pypdf`"
)
NO_TEXT_WARNING = (
    "no text could be extracted; recorded as an un-extracted source needing OCR, not as an "
    "empty document. Run OCR externally and drop the result in 02_KNOWLEDGE/inbox/, or paste "
    "the passage you need into the conversation"
)
NEEDS_OCR_WARNING = (
    "needs_ocr: most pages carry no extractable text, so this is almost certainly a scan. "
    "LearningOS does not perform OCR"
)
LIKELY_SCANNED_WARNING = (
    "likely_scanned: the median page holds fewer than "
    f"{SCANNED_PAGE_CHAR_THRESHOLD} characters"
)
SHORT_WARNING = (
    f"suspiciously_short: under {SHORT_EXTRACTION_CHARS} characters were extracted; check the "
    "original before relying on it"
)
EMPTY_SOURCE_WARNING = "empty_source: the file itself holds no text"
MANUAL_EXTRACTION_WARNING = (
    "needs_manual_extraction: this format is not read by LearningOS. Export it to PDF, Markdown "
    "or plain text and add that export as a source"
)
UNREADABLE_WARNING = "unreadable: the file could not be opened for reading"

# ----------------------------------------------------------------------------------------- kinds

#: Suffix -> ``kind`` in ``source_index.schema.json``.
TEXT_SUFFIXES: dict[str, str] = {
    ".txt": "text",
    ".text": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mmd": "text",
    ".csv": "text",
    ".json": "text",
    ".rst": "text",
}
PDF_SUFFIXES = (".pdf",)

#: Formats a human has to convert before LearningOS can read them (source_handling.md §6).
KNOWN_UNSUPPORTED_SUFFIXES = (
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".epub",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".zip",
    ".rar",
    ".7z",
)


def classify(path: str | Path) -> str:
    """The ``kind`` recorded in the source index: ``pdf``, ``text``, ``markdown`` or ``other``."""
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in TEXT_SUFFIXES:
        return TEXT_SUFFIXES[suffix]
    return "other"


@dataclass
class Extraction:
    """The result of reading one source.

    ``text`` is ``None`` when nothing usable came out — that is the signal not to write an
    ``extracted_text`` file, which is what keeps "could not read it" distinguishable from
    "it was empty" on disk as well as in the index.
    """

    kind: str
    tool: str
    text: str | None = None
    pages: int | None = None
    page_chars: list[int] = field(default_factory=list)
    likely_scanned: bool | None = None
    needs_ocr: bool | None = None
    warnings: list[str] = field(default_factory=list)
    tool_version: str | None = None

    @property
    def chars(self) -> int:
        return len(self.text or "")

    @property
    def chars_per_page_median(self) -> float | None:
        if not self.page_chars:
            return None
        return float(statistics.median(self.page_chars))

    @property
    def produced_text(self) -> bool:
        return bool(self.text)

    def index_block(self, *, at: str | None = None) -> dict[str, Any]:
        """The ``extraction`` object stored in ``source_index.json``."""
        return {
            "tool": self.tool,
            "tool_version": self.tool_version,
            "at": at or utc_now_iso(),
            "chars": self.chars,
            "chars_per_page_median": self.chars_per_page_median,
            "likely_scanned": self.likely_scanned,
            "needs_ocr": self.needs_ocr,
            "warnings": list(self.warnings),
        }


def provenance_header(
    *,
    source_id: str,
    source_path: str,
    extracted_at: str,
    tool: str,
    tool_version: str | None,
) -> str:
    """The five-line header every derived text file starts with (source_handling.md §2).

    It exists so that a text file found on its own, months later, still says where it came from
    and what read it. ``source_path`` is workspace-relative, like every stored path.
    """
    return (
        f"# source_id: {source_id}\n"
        f"# source_path: {source_path}\n"
        f"# extracted_at: {extracted_at}\n"
        f"# tool: {tool}\n"
        f"# tool_version: {tool_version or '-'}\n"
    )


def render_extracted_text(
    extraction: Extraction,
    *,
    source_id: str,
    source_path: str,
    extracted_at: str,
) -> str:
    """Header + body, exactly as written to ``extracted_text/<source_id>.txt``."""
    header = provenance_header(
        source_id=source_id,
        source_path=source_path,
        extracted_at=extracted_at,
        tool=extraction.tool,
        tool_version=extraction.tool_version,
    )
    body = extraction.text or ""
    if not body.endswith("\n"):
        body += "\n"
    return f"{header}\n{body}"


# ----------------------------------------------------------------------------------- extraction


def extract_text(path: str | Path) -> Extraction:
    """Read one source document. Never raises: a failure is reported, not thrown.

    The caller (``ingestion.indexer``) records the result either way, because a source that could
    not be read is still a source the learner owns and the agent must know about.
    """
    target = Path(path)
    kind = classify(target)
    if kind == "pdf":
        return _extract_pdf(target)
    if kind in ("text", "markdown"):
        return _extract_text_file(target, kind)
    return _extract_other(target)


def _extract_text_file(path: Path, kind: str) -> Extraction:
    try:
        raw = path.read_bytes()
    except OSError:
        return Extraction(
            kind=kind,
            tool="none",
            text=None,
            warnings=[UNREADABLE_WARNING],
            likely_scanned=None,
            needs_ocr=None,
        )
    # errors="replace" rather than "strict": a single bad byte must not cost the whole document.
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    warnings: list[str] = []
    if "\ufffd" in text:
        warnings.append(
            "replacement_characters: the file is not valid UTF-8; undecodable bytes were "
            "replaced with U+FFFD. Re-save it as UTF-8 for a clean read"
        )
    stripped = text.strip()
    if not stripped:
        return Extraction(
            kind=kind,
            tool="builtin-text",
            tool_version=TOOL_ID,
            text=None,
            pages=None,
            likely_scanned=False,
            needs_ocr=False,
            warnings=warnings + [EMPTY_SOURCE_WARNING],
        )
    if len(stripped) < SHORT_EXTRACTION_CHARS:
        warnings.append(SHORT_WARNING)
    return Extraction(
        kind=kind,
        tool="builtin-text",
        tool_version=TOOL_ID,
        text=text,
        pages=None,
        likely_scanned=False,
        needs_ocr=False,
        warnings=warnings,
    )


def _extract_other(path: Path) -> Extraction:
    """A known-but-unreadable format. Recorded with its hash; it simply has no derived text."""
    suffix = path.suffix.lower() or "(no extension)"
    note = MANUAL_EXTRACTION_WARNING
    if suffix not in KNOWN_UNSUPPORTED_SUFFIXES:
        note = f"{MANUAL_EXTRACTION_WARNING} (unrecognised extension {suffix})"
    return Extraction(
        kind="other",
        tool="none",
        text=None,
        pages=None,
        likely_scanned=None,
        needs_ocr=None,
        warnings=[note],
    )


def pypdf_available() -> tuple[bool, str | None]:
    """``(importable, version)`` for the optional PDF reader. Never raises, never installs."""
    return optional_dependency("pypdf")


def _extract_pdf(path: Path) -> Extraction:
    try:
        import pypdf  # noqa: PLC0415 - optional dependency, probed at call time on purpose
    except Exception:
        # Absence is a documented, visible degradation — not an error, and never auto-fixed.
        return Extraction(
            kind="pdf",
            tool="none",
            text=None,
            pages=None,
            likely_scanned=None,
            needs_ocr=None,
            warnings=[PYPDF_MISSING_WARNING],
        )

    _available, version = pypdf_available()
    warnings: list[str] = []
    try:
        with path.open("rb") as handle:  # read-only, always
            reader = pypdf.PdfReader(handle)
            pages = list(reader.pages)
            page_texts: list[str] = []
            for number, page in enumerate(pages, start=1):
                try:
                    page_texts.append(page.extract_text() or "")
                except Exception as exc:  # a damaged page must not lose the rest
                    page_texts.append("")
                    warnings.append(
                        f"page_unreadable: page {number} could not be parsed "
                        f"({type(exc).__name__}); it is counted as having no text"
                    )
    except Exception as exc:
        return Extraction(
            kind="pdf",
            tool="pypdf",
            tool_version=version,
            text=None,
            pages=None,
            likely_scanned=None,
            needs_ocr=None,
            warnings=warnings
            + [
                f"unreadable: pypdf could not open this PDF ({type(exc).__name__}: {exc}). "
                "It is recorded as an un-extracted source"
            ],
        )

    page_chars = [len(text.strip()) for text in page_texts]
    page_count = len(page_texts)
    thin_pages = sum(1 for count in page_chars if count < SCANNED_PAGE_CHAR_THRESHOLD)
    median = float(statistics.median(page_chars)) if page_chars else 0.0
    likely_scanned = bool(page_chars) and median < SCANNED_PAGE_CHAR_THRESHOLD
    needs_ocr = bool(page_chars) and (thin_pages / page_count) >= NEEDS_OCR_PAGE_FRACTION

    body_parts: list[str] = []
    for number, text in enumerate(page_texts, start=1):
        normalised = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        body_parts.append(PAGE_MARKER.format(page=number) + "\n" + normalised + "\n")
    body = "\n".join(body_parts)
    total_chars = sum(page_chars)

    if likely_scanned:
        warnings.append(LIKELY_SCANNED_WARNING)
    if needs_ocr:
        warnings.append(NEEDS_OCR_WARNING)

    if total_chars == 0:
        # Nothing usable came out. Keep the page count — it is real information — but write no
        # text file, so the index cannot be mistaken for "this document is empty".
        warnings.append(NO_TEXT_WARNING)
        return Extraction(
            kind="pdf",
            tool="pypdf",
            tool_version=version,
            text=None,
            pages=page_count,
            page_chars=page_chars,
            likely_scanned=True,
            needs_ocr=True,
            warnings=warnings,
        )

    if total_chars < SHORT_EXTRACTION_CHARS:
        warnings.append(SHORT_WARNING)

    return Extraction(
        kind="pdf",
        tool="pypdf",
        tool_version=version,
        text=body,
        pages=page_count,
        page_chars=page_chars,
        likely_scanned=likely_scanned,
        needs_ocr=needs_ocr,
        warnings=warnings,
    )
