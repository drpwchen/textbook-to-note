# resplit-force-keeps-figures

Tier 1. Maintainer standing instruction (2026-11-09); requested 2026-11-14.

## Why

`post_convert.py` links each corpus book's `figures*` / `images` folder to the converted book's
folder (a junction on Windows). `--resplit --force` rebuilds a book by deleting the old folder,
so the link is gone and every image link in that book is broken until `ensure_figure_links`
runs again — which `convert.py --resplit --force` never does. On 2026-11-13 the maintainer's
re-split books had their junctions removed and recreated by hand. If a corpus `figures` folder
is a real folder (copied, not linked) it is deleted outright, and resplit cannot recreate it,
because it never copies figure folders.

(Python ≥ 3.8 `shutil.rmtree` removes a junction without entering it, so the converted book's
images themselves were never at risk; checked 2026-11-14 on Python 3.12.)

## What changes

Before the old book folder is deleted, every folder in it whose name resplit never writes
(`figures*`, `images`, `_epub_src`, `__pycache__`, `.drafts_bak`, `*.bak` — the folders resplit
skips when reading) moves to the same place in the new folder, unless the new folder already
has that path. A link moves as a link. The run report lists them as `kept_dirs`.

## Not doing

- Creating links that did not exist (still `post_convert.ensure_figure_links`).
- Keeping any other folder or file someone put in a corpus book folder (the folder is still
  owned by resplit and rebuilt).

## Risk

Default-ON, but it only keeps what was deleted before; the files resplit writes are unchanged.
Without `--force`, or for a book whose corpus folder does not exist yet, nothing changes.

## Affected specs

- `openspec/specs/conversion/spec.md` — added requirement.
