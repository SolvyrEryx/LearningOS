# Design decisions

Why LearningOS is built the way it is. Each entry states the decision, the reasoning, and what it
costs — because a decision with no cost is usually a decision that was never made.

Record of the 0.1.0 build. Nine load-bearing decisions, six deliberate improvements on the original
sketch, and the limitations that are real rather than theoretical.

---

## 1. Three-layer memory

**Decision.** The append-only event log at `03_MEMORY/events/session_events.jsonl` is the
historical record. Per-subject JSON snapshots under `03_MEMORY/subjects/` are the current state.
Graphs, session logs, the search cache and the global index are derived. Checkpoints are a fourth,
small layer: a handover, not a state.

**Why.** These four have different truth conditions. Evidence is immutable and only ever grows;
state is mutable and must stay small enough to read at the start of every session; derived
artefacts are disposable. Conflating them produces either a history you cannot read quickly or a
state file you dare not regenerate. Separating them means `reconcile` can replay layer 1 against
layer 2, and layer 3 can be deleted without a thought.

**Cost.** The same fact is written twice — once as an event, once into a snapshot — and the two can
drift. That is accepted and made visible: `validate` emits a `stale_state` warning when the newest
relevant event is newer than the snapshot it should have moved, and `reconcile` reports and
optionally repairs the difference. The write order is fixed (events first, snapshot second) so
drift means "a write was interrupted", not "normal operation".

## 2. `concept_id = "<subject_id>:<slug>"`

**Decision.** One globally unique, human-readable, stable id per concept, whose prefix encodes the
owning subject. The prefix must equal the subject directory the record lives in.

**Why.** Subject isolation was a requirement, and a requirement enforced by convention is not
enforced at all. With the prefix in the id, leakage becomes mechanically detectable: a record filed
under `chem` whose id is `physics:x` is a validation error, not a judgement call. It also makes the
cross-subject graph possible without a separate mapping table, and makes an id in a conversation
self-describing.

**Cost.** Renaming a subject means rewriting every id in it. Accepted: subject ids are short,
chosen once, and documented as "keep it short and stable".

## 3. A bundled JSON-Schema subset validator

**Decision.** `tools/schema.py` implements the draft-07 subset the fifteen shipped schemas actually
use, in the standard library. The `jsonschema` package is used only as an extra cross-check under
`validate --strict`.

**Why.** `validate` is the install's health check. A health check that fails for want of a
third-party package is worse than no health check, and "pip install this first" is exactly the
friction that makes a system not get used three months later. The subset is small because the
schemas were written to stay inside it, and `tests/test_schema_lib.py` asserts that no shipped
schema uses a keyword the bundled validator does not support — so the two cannot drift apart.

**Cost.** A hand-written validator is a hand-written validator. Mitigated by keeping the supported
keyword set explicit and testable, and by cross-checking against the real implementation when it
happens to be installed.

## 4. Mastery is evidence-derived, never accuracy-derived

**Decision.** A hinted success can never increment `independent_successes` and can never reach
`provisionally_mastered`. An attempt reported as independent while carrying hints is normalised back
to hinted — in the stored evidence *and* in the event payload.

**Why.** The stated goal is independent application. Raw accuracy rewards hint-following, and a
tutor that optimises for the learner feeling correct will hint its way to a perfect record. Test D
asserts this directly: three hinted successes in a row leave `independent_successes == 0` and the
concept below the top rung, so 100 % raw accuracy is explicitly insufficient.

**Cost.** Progress looks slower than it feels, and the learner has to be honest about hints. The
normalisation closes the relabelling loophole; an unreported hint cannot be detected by any
mechanism, and the documentation says so rather than pretending otherwise.

## 5. Graphs are data-first

**Decision.** Build canonical graph JSON, then derive Mermaid text as a pure function of that JSON,
then optionally render `.svg` (mermaid-cli) and a self-contained `.html` page. Every node and edge
id is resolved before anything is written.

**Why.** Reproducibility and graceful degradation. The artefact survives a missing renderer; the
Mermaid text is byte-stable because node ordering, id sanitisation and label escaping are
deterministic; and a dangling reference exits 3 before a single file lands, so a diagram never
quietly draws a concept that does not exist. Test G deletes the whole graphs directory, re-runs, and
asserts byte-identical `.mmd`.

**Cost.** Three artefacts per graph instead of one, and one subtlety: the `.mmd` carries no
timestamp (the JSON does), because a timestamp in the text would destroy the byte-identity claim.
The only clock-dependent content in a graph is the "review due" marker, which is a claim about
today rather than about stored state.

## 6. Atomic writes, and `--dry-run` on everything risky

**Decision.** Every JSON write goes to a temp file in the **same** directory, is `flush`ed and
`fsync`ed, is schema-validated, and only then `os.replace`s the target. Every destructive or
expensive command has `--dry-run`, and the genuinely destructive ones refuse without `--force`.

**Why.** A half-written memory file is worse than no change at all: it loses the old state and
gains nothing. Same-directory temp files keep the replace atomic on the same volume.
Validate-before-replace means a bug cannot write a file that `validate` will later reject. And a
write path refuses to overwrite a file that is already corrupt on disk, so a parse error never gets
laundered into a clean overwrite.

**Cost.** Writes are slower and the code is longer. For a few kilobytes of learning history per
session, that is not a trade worth having an opinion about.

## 7. Two subcommands beyond the brief: `init-workspace` and `reconcile`

**Decision.** Add `init-workspace`, which scaffolds an empty folder into a complete install, and
`reconcile`, which replays the event log against the snapshots.

**Why.** Both make a documented story real rather than aspirational. The quick start promises
"empty folder to first session", which needs a scaffolder. The recovery story promises that the
append-only log is authoritative and that drift is detectable, which needs something that actually
compares them. Without `reconcile`, "the event log is the source of truth" would have been a claim
with no code behind it.

**Cost.** Two more commands to document and test. `reconcile` carries a deliberate limitation —
see decision 9's cost and the limitations section.

## 8. Shared helpers stay flat beside `learningos.py`

**Decision.** `common.py`, `schema.py` and `validation.py` sit directly in
`01_AGENTS_AND_SKILLS/tools/` rather than in a fifth package directory. The brief fixes `memory/`,
`ingestion/`, `graphs/` and `tests/`; shared helpers do not justify another.

**Why.** `learningos.py` is run as a script, so its own directory is `sys.path[0]` and
`import common` resolves with no packaging, no `setup.py`, no virtualenv and no `PYTHONPATH`
fiddling. A `core/` or `lib/` package would buy nothing but a longer import line.

**Cost.** Four modules at the top of the directory listing. The layout table in
`01_AGENTS_AND_SKILLS/tools/README.md` says what each one is for.

## 9. Nothing is ever moved out of the inbox

**Decision.** `index --inbox --assign-to <subject>` **copies**. Without `--copy` it only prints the
plan. An existing file at the destination is left alone rather than overwritten. More broadly: the
tooling never deletes, truncates, moves or reorganises the learner's material, and originals in
`source_materials/` stay byte-identical.

**Why.** The non-destructive constraint beats convenience. A learning system that loses a document
once is a learning system you stop trusting with documents, and tidying up is not a job worth that
risk. The same reasoning is why `restore` replaces archived files but never deletes files that are
not in the archive: non-destructive beats exact.

**Cost.** Duplicate bytes on disk, and an inbox you have to clear by hand. Both are visible and
both are yours to decide about.

---

## Improvements on the original sketch

Six things the first design did not have, each added because the system was not honest or usable
without it.

**A machine-readable `workspace_state.json`.** The original design tracked the workspace in
Markdown only. `03_MEMORY/global/workspace_state.json` holds the registered subjects, the current
subject, the schema version and the last backup id in a schema-validated form, so `status`,
`resume` and `validate` read state instead of parsing prose. `learner_index.md` survives beside it
as the human view.

**`reconcile`.** See decision 7. Without it, the claim that the event log is authoritative would
have had no implementation.

**`init-workspace`.** The quick start starts from an empty folder, so something has to create one.
It also turned out to matter for portability: when the target folder differs from the install, the
instruction files, schemas, templates and CLI are copied in, which is what makes a LearningOS
workspace relocatable by one command.

**Subject-prefixed concept ids.** See decision 2. The original sketch used bare slugs, which made
cross-subject isolation unverifiable.

**The bundled schema validator.** See decision 3. The original sketch assumed `jsonschema`.

**Copy-not-move inbox handling.** See decision 9. The original sketch moved files out of the inbox
into the subject, which is a small convenience bought with a real risk.

---

## Honest limitations

Things this build does not do. Listed because a capability matrix that only lists capabilities is
marketing.

**No OCR.** A scanned PDF is detected (very little extractable text per page), recorded with
`likely_scanned`, `needs_ocr` and warnings, and left **un-extracted** — no text file is written at
all, so nothing can be mistaken for the document's content. Running OCR over the original and
re-indexing is a manual step. Missing text is never presented as no content.

**No `.docx` or other office-format extraction.** Recorded as `kind: other` with a
`needs_manual_extraction` warning. Export to PDF, Markdown or plain text yourself.

**No renderer is installed in this environment.** `mmdc` is not on `PATH` here, so the `.svg` path
was only ever verified as "skipped cleanly with a notice, exit 0" — and `--require-renderer` as
"exit 6, nothing written". A present-but-failing `mmdc` is also only a notice. Installing a renderer
is deliberately left to you; the tooling installs nothing.

**PDF text needs an optional package.** Without `pypdf`, PDFs are still indexed with their id, hash,
size and mtime, but no text is extracted and `needs_ocr` is recorded as `null` rather than `true`:
"we could not look" is a different claim from "there is nothing to read".

**`search-memory` does not index `02_KNOWLEDGE/`.** It searches your learning history — events,
concept records, misconceptions — not your source documents. Search those with your editor. Adding
them would mean a document provider beside the existing three, not a special case in the CLI.

**An unreported hint is undetectable.** The ladder is only as good as the evidence recorded. The
relabelling loophole is closed; a hint the learner or the agent simply does not mention is not
recoverable by any mechanism in here.

**`reconcile --apply` never fabricates evidence.** It re-derives status, the mastery estimate, the
counters and the revision queue from stored evidence. It will not manufacture a retention-evidence
entry from an event, because a fabricated attempt is indistinguishable from a real one once
written. Genuinely lost evidence is a `restore` job.

**Mastery is a heuristic, not a measurement.** The number is a recency-weighted mean over recent
evidence with a novelty weight and a misconception penalty. It is useful for ordering what to do
next. It is not a score, and the documentation and the function's own docstring both say so.

**`provisionally_mastered` is the top rung, and the word is load-bearing.** It means the recorded
evidence currently supports independent application. It does not mean retention was proved over
months, because nothing in a file can prove that faster than time passes.

**Local files are not local inference.** The tooling makes no network calls — statically asserted by
`tests/test_l_privacy_nondestructive.py`. But if the model driving the conversation is hosted, that
provider sees whatever enters the context window, including excerpts of your sources and memory.
Local storage is not local inference, and only running a local model makes it so.

**Windows-first verification.** The code is pure `pathlib`/`os` with no platform-specific calls, but
it was built and tested on Windows with PowerShell. The POSIX path is expected to work and has not
been exercised.

---

## Where the rules actually live

One canonical copy of the teaching contract, in `01_AGENTS_AND_SKILLS/core/` and
`01_AGENTS_AND_SKILLS/skills/`. The root `AGENTS.md` is a pointer with a load order. Both adapters
are pointers too — the Kiro steering file is capped at 60 lines by a test, which is how the
thin-pointer decision stays true rather than aspirational. Nothing in
`01_AGENTS_AND_SKILLS/adapters/` holds a second copy of a rule, so no copy can go stale.

The same principle applies between docs and code: the mastery formula is stated in
`01_AGENTS_AND_SKILLS/core/memory_protocol.md` and implemented in one function whose docstring
repeats that paragraph; the exit-code table in `01_AGENTS_AND_SKILLS/tools/README.md` is checked
against `common.EXIT_CODE_MEANINGS` by a test; the documented subcommand list is checked against the
real argparse definitions; and every workspace-relative path written in `AGENTS.md`, both READMEs,
this file and the adapters has to resolve on disk. Documentation that can rot, does.
