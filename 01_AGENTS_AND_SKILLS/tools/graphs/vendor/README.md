# `graphs/vendor/` — optional, hand-placed renderer

This directory is empty on purpose. Nothing here is required, and LearningOS never downloads
anything into it.

## What it is for

If you drop a file called **`mermaid.min.js`** here, every `.html` graph page generated afterwards
**inlines** it. The page then renders the diagram offline, with no network request and no CDN
`<script src>`, and it keeps working if you copy it to another machine or email it to yourself.

Without it, the generated page is still complete: it shows the Mermaid source as readable text plus
a short notice explaining this file and the `mmdc` alternative. You lose the picture, not the
artefact.

## How to add it

The file is published by the Mermaid project. Download it yourself — deliberately a manual step,
because the tooling does not make network calls — and save it here:

```text
01_AGENTS_AND_SKILLS/tools/graphs/vendor/mermaid.min.js
```

Then regenerate:

```powershell
cd 01_AGENTS_AND_SKILLS\tools
python learningos.py update-graphs --all
```

`python learningos.py status` reports whether a vendored renderer and `mmdc` were found.

## The other route: `mmdc`

[mermaid-cli](https://github.com/mermaid-js/mermaid-cli) gives you a real `.svg` file instead of a
page that renders in a browser. Install it yourself so that `mmdc` is on `PATH`, then:

```powershell
python learningos.py update-graphs --all --format mmd,html,svg
```

When `mmdc` is not on `PATH`, `update-graphs` prints
`SVG skipped: mermaid-cli (mmdc) not found on PATH`, writes the `.mmd` and `.html` anyway, and
exits 0. Use `--require-renderer` if you would rather that be a failure (exit 6).

## Why it is not bundled

`mermaid.min.js` is large, versioned, and not ours to redistribute inside this folder. Vendoring it
by hand is a one-off decision with a visible cost, which is the right shape for an optional
convenience. Note also that the two routes are independent: the inlined script renders in a
browser, `mmdc` renders to a file, and neither is needed to read the `.mmd`.

## What is ignored here

Everything except this README. `.gitignore` at the workspace root keeps a vendored
`mermaid.min.js` out of version control, since it is a third-party binary artefact rather than part
of your learning history.
