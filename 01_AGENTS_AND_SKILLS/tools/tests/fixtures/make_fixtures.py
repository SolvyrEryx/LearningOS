"""Generate the test fixtures, once, from literal bytes.

Run it from this directory when a fixture needs to change::

    python make_fixtures.py

The outputs are committed beside this file and read by ``tests/test_i_provenance.py``. They are
written here rather than downloaded, and assembled from literal PDF syntax rather than produced by
a PDF library, for three reasons: the test suite must not need a network, it must not need a
writer dependency on top of the optional reader one, and the *scanned* fixture has to be a page
with genuinely no text operators — which is exactly what a real library makes it hard to produce.

Two PDFs:

* ``sample_text.pdf`` — one page, a real ``BT ... Tj ... ET`` text object with enough characters
  that extraction looks healthy.
* ``sample_scanned.pdf`` — one page, no font, no text operator at all: just a filled rectangle
  standing in for a photograph of paper. ``pypdf`` extracts nothing from it, which is precisely
  the case LearningOS must report as "needs OCR" rather than as an empty document.

Plus two plain-text sources (``syllabus.txt``, ``notes.md``) so the index covers every readable
kind. All content is synthetic: no real learning material is in the repository.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

TEXT_LINES = [
    "LearningOS test fixture - synthetic page one.",
    "Merge sort splits the array in half, sorts each half, then merges.",
    "The recurrence is T(n) = 2T(n/2) + n, which solves to Theta(n log n).",
    "Case 2 of the master theorem applies because f(n) = n matches n^log_b(a).",
    "A stable merge preserves the relative order of equal keys.",
    "This paragraph exists only so the extraction looks healthy to the indexer.",
]

#: Drawn instead of text: a grey rectangle, as a stand-in for a scanned photograph.
SCANNED_CONTENT = b"0.5 g\n72 72 468 648 re\nf\n"


def _escape(text: str) -> bytes:
    """PDF string escaping for the few characters that matter inside ``( ... )``."""
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("latin-1", "replace")


def _text_content() -> bytes:
    parts = [b"BT\n/F1 12 Tf\n14 TL\n72 720 Td\n"]
    for line in TEXT_LINES:
        parts.append(b"(" + _escape(line) + b") Tj\nT*\n")
    parts.append(b"ET\n")
    return b"".join(parts)


def _build_pdf(objects: list[bytes]) -> bytes:
    """Assemble a one-page PDF with a correct cross-reference table.

    ``objects`` are the bodies of objects 1..n, already serialised without the
    ``N 0 obj``/``endobj`` wrapper.
    """
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    chunks = [header]
    offsets: list[int] = []
    position = len(header)
    for number, body in enumerate(objects, start=1):
        serialised = b"%d 0 obj\n" % number + body + b"\nendobj\n"
        offsets.append(position)
        chunks.append(serialised)
        position += len(serialised)

    xref_offset = position
    xref = [b"xref\n", b"0 %d\n" % (len(objects) + 1), b"0000000000 65535 f \n"]
    for offset in offsets:
        xref.append(b"%010d 00000 n \n" % offset)
    chunks.extend(xref)
    chunks.append(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_offset)
    )
    return b"".join(chunks)


def text_pdf_bytes() -> bytes:
    content = _text_content()
    return _build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
        ]
    )


def scanned_pdf_bytes() -> bytes:
    return _build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> "
            b"/Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(SCANNED_CONTENT)
            + SCANNED_CONTENT
            + b"endstream",
        ]
    )


SYLLABUS_TEXT = """Algorithms - synthetic syllabus (test fixture)

Unit 1: Sorting
  1.1 Insertion sort and its invariant
  1.2 Merge sort and the divide-and-conquer pattern
  1.3 Stability, and when it matters

Unit 2: Recursion and recurrences
  2.1 Writing a recurrence from code
  2.2 The master theorem, cases 1 to 3
  2.3 Recursion trees as a sanity check

Unit 3: Asymptotic analysis
  3.1 Big-O, Theta and Omega, and the difference between them
  3.2 Common growth rates in one table
  3.3 Reading a bound in an exam question
"""

NOTES_MARKDOWN = """# Merge sort - synthetic lecture notes (test fixture)

## The recurrence

Splitting costs nothing, merging costs `n`, and there are two subproblems of size `n/2`:

    T(n) = 2T(n/2) + n

By case 2 of the master theorem, `a = 2`, `b = 2`, `f(n) = n`, and `n^log_b(a) = n`, so
`T(n) = Theta(n log n)`.

## What the exam actually asks

1. Derive the recurrence from the pseudocode, not from memory.
2. Say which case of the master theorem applies, and why.
3. State whether the sort is stable, and justify it from the merge step.

## A mistake worth remembering

Dropping the `n` in the merge step gives `T(n) = 2T(n/2)`, which solves to `Theta(n)` and is
wrong. The merge is linear; that is the whole point of the algorithm.
"""


def main() -> None:
    written = []
    for name, data in (
        ("sample_text.pdf", text_pdf_bytes()),
        ("sample_scanned.pdf", scanned_pdf_bytes()),
        ("syllabus.txt", SYLLABUS_TEXT.encode("utf-8")),
        ("notes.md", NOTES_MARKDOWN.encode("utf-8")),
    ):
        target = HERE / name
        target.write_bytes(data)
        written.append(f"{name} ({len(data)} bytes)")
    print("wrote " + ", ".join(written))


if __name__ == "__main__":
    main()
