# no-full-text-sources

Tier 1 (post_convert resplit chain; no OCR routing, table gating or figure QC).

## Why

A book converted before `full_text.md` existed holds only chapter files; its PDF is named by the
`Source:` line of one of them (usually `ch00_Front_Matter.md`), and its split config says
`method: reconstruct`. `post_convert.pdf_roots_for()` only read `full_text.md`, so such a book got
the `T2N_PDF_ROOTS` fallback, its PDF was not under it, and the resplit silently carried the old
split. The old private copy hid this by listing the whole synced library. Measured on the
maintainer's corpus: 28 of the 94 chapter-files-only books affected; 16 of them failed the
validator after such a resplit.

`T2N_REQUIRE_SOURCE_PDF=1` checked `full_text.md` only, so these books were not refused either.

## What changes

- `pdf_roots_for()`: a book with no `full_text.md` takes its chapter files' `Source:` folders.
  Default-ON (corrects wrong output, measured below).
- `T2N_REQUIRE_SOURCE_PDF=1`: a book with no `full_text.md` and no findable PDF (no resolvable
  chapter `Source:`, no `pdf` entry in `<corpus>/_split_config.json`) is a `[FAIL]` with the fix.
  A resolvable config `pdf` entry also satisfies the check for any book. Unset: unchanged.

## Not doing

- No listing of any folder to find a PDF; no change to `resplit.py`.

## Affected specs

- `openspec/specs/conversion/spec.md` — scenarios added to the post_convert and
  `T2N_REQUIRE_SOURCE_PDF` requirements; the roots sentence extended.
