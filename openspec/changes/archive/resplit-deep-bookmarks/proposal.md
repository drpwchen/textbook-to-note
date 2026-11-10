# resplit-deep-bookmarks

Tier 1. Rewritten 2026-11-09 under the outcome-based approval mode (see
`split-manifest-contract`); the draft's open questions are decided below.

## Why

`split_by_bookmarks()` keeps only level ≤2 bookmarks and writes no markdown headings, so the
indexer's heading-aware chunking falls back to page chunks for 99.7% of the corpus (RAG review
2026-11-05, #4). The section structure exists in the PDF's level-3+ bookmarks (75.8% of all
bookmarks in the baseline sample) and is discarded.

## What changes

With `T2N_SPLIT_DEEP_HEADINGS=1`, after a bookmark split each level-3 bookmark becomes a
`## <title>` line and each level ≥4 bookmark a `### <title>` line, inserted into the chapter
file holding the bookmark's page, directly above the first line of that page whose normalised
text equals the normalised title (casefold, NFKC, punctuation → space, leading section numbering
stripped), or equals it once joined with the next line. No match → nothing inserted, only
counted; the split prints `deep headings: <inserted>/<total> inserted`. Unset / `0`: output
byte-identical to 0.7.6. Applies to every bookmark split (conversion and `--resplit`; with a
`split_levels` config the first level below the split levels is `##`).

Decisions (draft open questions): mapping 3→`##`, ≥4→`###`; no `#` line for the file's own
title; match = equality (or two-line join), never a guess; no book-level warning threshold (the
count is in the run log and `_resplit_report.json`); `full_text.md` is never touched.

## Not doing

- Not changing split points or file names.
- Not detecting headings from font size when a PDF has no deep bookmarks.
- Not applying to `detect_chapters` / `_force_split` / EPUB splits, nor to `ch00_Front_Matter.md`.

## Risk

Opt-in, default OFF; byte-identical when unset is the guard (tested). A heading at a wrong
position would mislabel `section_path` downstream — hence insertion only on an exact match.
Measured on the pilot books: Lanken 2e 1,894 / 1,895 placed deep bookmarks inserted, Zasler 2e
918 / 936.

## Affected specs

- `openspec/specs/conversion/spec.md` — added (one requirement)
