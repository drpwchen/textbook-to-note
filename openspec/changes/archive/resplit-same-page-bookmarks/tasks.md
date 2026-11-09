# Tasks — resplit-same-page-bookmarks

## Pre-implementation measurements

- [x] Of the 382 corpus chapter files of 1–99 bytes, count how many have 0 non-whitespace
      characters outside `<!-- page N -->` lines (the rule this change also suppresses)
      → **48** of 382 (top: Gaber 6; the rest 1–2 per book)
- [x] Explain Hayes (predicted 11 empty, actual 0) and Buckup (predicted 1, actual 0); confirm the
      rule produces no difference on books that are clean today
      → Their corpus folders were produced in April by an older converter run (no `full_text.md`,
      chapter files start with TOC text, not page markers), so the bookmark prediction never
      applied. Re-splitting today's PDF text: Hayes v0.7.3 would write 11 empty files, this change
      writes none; Buckup loses its 1 page-marker-only file. Survivors byte-identical in both.
      Clean books (8 of the 37 measured) are byte-identical with the behaviour on.

## Implementation

- [x] In `split_by_bookmarks()`, drop split points whose line equals the next kept point's line,
      keeping the original `idx + 1` sequence number for survivors; also skip writing a file
      with 0 non-whitespace characters outside page markers
      → Requirement: Bookmarks that share a split line produce one chapter file
- [x] `T2N_SPLIT_SAME_PAGE_MERGE` read with default `"1"`
      → Requirement: Bookmarks that share a split line produce one chapter file

## Verification

- [x] Test for each Scenario (synthetic PDF with a hand-set TOC + synthetic `full_text.md`)
- [x] Byte-identical with `T2N_SPLIT_SAME_PAGE_MERGE=0` vs. v0.7.3 on ≥3 real books
- [x] Corpus measurement AFTER, against phase2-baseline.md BEFORE (5,046 empty / 22,461; 1,508
      predicted = actual on 28 PDFs): same-page empty files → 0; non-empty files whose name or
      bytes changed = 0
      → `converter/test_split_same_page.py` 19/19. Corpus (37 books with `full_text.md` + an
      openable PDF; Hayes/Buckup via fresh fitz text): v0.7.3
      writes 3,424 files, 1,220 of them 0-byte + 7 page-marker-only; this change writes 2,198,
      text-less = 1 (Levangie: the out-of-order bookmark, kept by design). Survivor name/bytes
      changed = 0; dropped files that had text = 0; kill switch == v0.7.3 on 37/37.

## Ship

- [x] `CHANGELOG.md` entry
- [x] Version bump + tag (v0.7.5)
- [x] Archive
