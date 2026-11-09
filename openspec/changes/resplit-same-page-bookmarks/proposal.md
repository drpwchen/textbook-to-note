# resplit-same-page-bookmarks

Tier 1 — approved by the maintainer 2026-11-09.

## Why

`split_by_bookmarks()` (`converter/convert.py:1731`) maps every level-1/2 bookmark to the line
of its page's `<!-- page N -->` marker. Two bookmarks on the same page get the same line, so
`lines[line_start:line_end]` is empty and an empty `chNN_*.md` is written anyway
(`convert.py:1758-1765`). No text is lost — it lands in the neighbouring file — but every empty
file is a wrong chapter boundary.

Measurement (`_scratch/rag-review-2026-11-05/phase2-baseline.md`, 2026-11-09, read-only):

- 5,046 of 22,461 chapter files (22.5%) are 0 bytes, across 140 of 284 books; all 5,046 carry
  the `ch<N>` naming.
- On the 28 openable whole-book PDFs that have 0-byte files, the same-page rule (depth ≤2
  bookmarks resolving to the same page) predicts **1,508** empty files; the corpus has exactly
  **1,508**. 28/28 books match.

## What changes

- Bookmarks that resolve to the same split line are collapsed into one split point. No chapter
  file is written for the collapsed ones.
- The surviving file keeps exactly the name and content today's non-empty file has. Sequence
  numbers are **not renumbered**: gaps appear (`ch03`, `ch05`) and no existing file is renamed
  (maintainer decision 2026-11-09).
- Kill switch `T2N_SPLIT_SAME_PAGE_MERGE=0` restores today's output byte for byte. (name approved 2026-11-09).

## Not doing

- Not changing level filtering (level ≥3 is `resplit-deep-bookmarks`) or chapter numbering
  (`resplit-real-chapter-numbers-and-manifest`).
- Not touching the `detect_chapters()` fallback, `_force_split()`, or the EPUB splitter.
- Not resolving bookmarks to a line *inside* a page (title search). Split stays at page-marker
  granularity.
- **Not fixing broken-bookmark books.** Lanken 2e and Zasler 2e resolve *every* bookmark to page 1
  (Lanken `LINK_GOTO page 0`; Zasler unresolvable named destinations), so the whole book lands
  in the last chapter. Page numbers cannot repair that; it needs title-to-text anchoring — a
  separate future change. Here they only lose their empty files.
- Not fixing out-of-order bookmarks (page lower than the previous one; 2 occurrences in 47 PDFs).

## Dependencies

- `stop-pdf-bookmark-writeback` lands first, so the bookmarks read here are never the
  converter's own earlier guesses written into the source PDF.

## Risk

Default-ON change to split output, backed by the measurement above. Because surviving files keep
their names and bytes, the `seq` strategy in `citations/textbook_chapter_index.py` and any
`_chapter_index.chapters.json` stay valid; the only visible difference is fewer files. Existing
on-disk books only change when re-split (`--resplit`, or a re-conversion together with
`force-clears-stale-chapters`).

## Affected specs

- `openspec/specs/conversion/spec.md` — added (one requirement)

## Decisions (2026-11-09)

1. **Yes.** Also suppress files that are non-empty but contain only page markers and
   whitespace (first page of a pair blank)? Proposed: yes, threshold = 0 non-whitespace
   characters outside `<!-- page N -->` lines. Baseline: 382 files are 1–99 bytes — not yet
   broken down by this rule.
2. **Separate change.** Out-of-order bookmarks also produce empty files and duplicate a text span into the
   next file. Proposed: separate change, because it alters the content of non-empty files.
3. **Required pre-ship check (task).** Hayes (predicted 11 empty, actual 0) and Buckup (predicted 1, actual 0) were not explained by
   the baseline. Worth one look before ship, to confirm the rule cannot *create* a difference
   on books that are clean today.
