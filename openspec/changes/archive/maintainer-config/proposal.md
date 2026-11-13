# maintainer-config

Tier 1. Lets the maintainer's private scripts become thin wrappers over this repo (WP-D), and
fixes one provenance gap found on the way.

## Why

- A Surya-OCR'd book's `full_text.md` had no `Source:` line, so `--resplit` / `post_convert.py`
  could only find its PDF by searching PDF roots by folder name. On the maintainer's corpus,
  13 of 269 books have no `Source:` line.
- `PdfIndex` listed every PDF root recursively before splitting, even when every `Source:` PDF
  existed. `post_convert.py` uses each book's `Source:` folder as a root, so on a library kept
  in a synced drive every resplit listed folders of that drive (expensive: placeholder files
  get fetched). On the maintainer's corpus 252 of 269 books have all `Source:` PDFs present and
  need no listing at all.
- `books.json` & co. could only live in `BOOKS_DIR`, which for the maintainer is a synced
  drive that should not be written to; `books.json` had no way to name two books with one key
  (the private copy's `lien` alias), and no `~`.
- A book whose PDF was gone was silently resplit by carrying its old split. Fine as a default;
  the maintainer wants it loud.

## What changes

- OCR path writes `Source: `<pdf>``. Routing and OCR text unchanged.
- `PdfIndex` walks a root on first need only. Output unchanged.
- `T2N_CONFIG_DIR` (default `BOOKS_DIR`); `books.json` list values = aliases; `~` in `src`/`out`.
- `T2N_REQUIRE_SOURCE_PDF=1` (opt-in) fails such a book with the fix.

## Not doing

- No change to OCR routing, table gating, figure QC, or what a resplit writes.
- No default-ON behaviour change other than the provenance line (corrects missing output,
  touches no text) and lazy walking (byte-identical output).
- The private wrappers themselves live in the maintainer's toolkit, not here.

## Risk

A consumer that expected line 1 of an OCR'd `full_text.md` to be `<!-- page 1 -->` sees the
`Source:` line first. The resplit / split code already skips header lines before the first
page marker (it does so for the text-layer path).

## Affected specs

- `openspec/specs/conversion/spec.md` — 4 added requirements, 1 sentence modified.
