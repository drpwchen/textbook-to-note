# Tasks — resplit-same-page-bookmarks

## Pre-implementation measurements

- [ ] Of the 382 corpus chapter files of 1–99 bytes, count how many have 0 non-whitespace
      characters outside `<!-- page N -->` lines (the rule this change also suppresses)
- [ ] Explain Hayes (predicted 11 empty, actual 0) and Buckup (predicted 1, actual 0); confirm the
      rule produces no difference on books that are clean today

## Implementation

- [ ] In `split_by_bookmarks()`, drop split points whose line equals the next kept point's line,
      keeping the original `idx + 1` sequence number for survivors; also skip writing a file
      with 0 non-whitespace characters outside page markers
      → Requirement: Bookmarks that share a split line produce one chapter file
- [ ] `T2N_SPLIT_SAME_PAGE_MERGE` read with default `"1"`
      → Requirement: Bookmarks that share a split line produce one chapter file

## Verification

- [ ] Test for each Scenario (synthetic PDF with a hand-set TOC + synthetic `full_text.md`)
- [ ] Byte-identical with `T2N_SPLIT_SAME_PAGE_MERGE=0` vs. v0.7.3 on ≥3 real books
- [ ] Corpus measurement AFTER, against phase2-baseline.md BEFORE (5,046 empty / 22,461; 1,508
      predicted = actual on 28 PDFs): same-page empty files → 0; non-empty files whose name or
      bytes changed = 0

## Ship

- [ ] `CHANGELOG.md` entry
- [ ] Version bump + tag
- [ ] Archive
