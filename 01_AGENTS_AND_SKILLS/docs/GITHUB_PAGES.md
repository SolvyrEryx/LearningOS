# LearningOS landing page

Live site: https://solvyreryx.github.io/LearningOS/

The static source is `01_AGENTS_AND_SKILLS/docs/site/`. The page explains the system and links
to the repository, a clean ZIP download and the getting-started PDF. Its sample learning record
is illustrative; it contains no real learner data. Fonts, styles and scripts are served locally
with the page. There are no analytics, forms, accounts or remote JavaScript dependencies.

This public landing page was explicitly requested by the maintainer. It is separate from the
local study toolkit and the offline Atlas. Ordinary tutoring continues to follow the local-only
workspace rules.

## Preview

From the repository root:

```sh
python -m http.server 8765 --bind 127.0.0.1 --directory 01_AGENTS_AND_SKILLS/docs/site
```

Open `http://127.0.0.1:8765`. Check mobile and desktop layouts, both illustrative session buttons,
the copy-prompt button, question disclosures, repository links and PDF. Stop the preview server
when done. No preview server is required for publication.

## Publish an update

Commit the reviewed source changes to `main`, then run in the publication checkout:

```sh
git push origin main
git subtree push --prefix=01_AGENTS_AND_SKILLS/docs/site origin gh-pages
```

GitHub Pages is configured to serve `/` from the `gh-pages` branch. The subtree push includes only
the static site folder, so the study toolkit and learner directories are outside the deployed
site. A `.nojekyll` marker keeps the site buildless. Use a normal push and preserve branch history.

The site's `guide.pdf` is a publication copy of the getting-started PDF. Refresh it when that
guide changes. Bundled fonts retain their OFL notices in `assets/`. Update `assets/social.png`
if the headline or visual identity changes; the page's social metadata uses the canonical URL.

Copy was edited using the project-local `no-ai-slop` skill installed with the maintainer's
requested `npx skills add` command. That local skill and its lockfile are excluded from Git.
