# Tasks — maintainer-config

## Implementation

- [x] `surya_ocr_pdf()` writes the `Source:` line  → Requirement: An OCR'd book records its source PDF
- [x] `CONFIG_DIR` in `shared/config.py`; `convert.py` / `rename_books.py` read from it  → Requirement: Library settings can live outside `BOOKS_DIR`
- [x] `load_book_aliases()`, `resolve_book_keys(..., aliases)`, `~` expansion  → same
- [x] lazy `PdfIndex` (`_build()`, `walked`, lazy `combine()`)  → Requirement: PDF roots are walked only on demand
- [x] `require_source_pdf()` / `unresolved_sources()` in `run_resplit()`  → Requirement: `T2N_REQUIRE_SOURCE_PDF=1` …
- [x] `docs/setup-details.md`, `CHANGELOG.md` (0.8.1), CI runs the new test

## Verification

- [x] `converter/test_maintainer_config.py` 23/23; `test_surya_ocr.py` 42/42 (Source-line check)
- [x] Mutation: eager `PdfIndex` → 5 fail; `strict = False` → 4 fail; `BOOKS_JSON` back on
      `BOOKS_DIR` → 2 fail; Source line removed → 1 fail
- [x] Full suite green (13 + 1 files)
- [x] End to end through the maintainer's wrappers on the B2 validator set (4 PDFs, incl. a
      30-page scanned excerpt through Surya): conversion 160/163 files byte-identical to the
      old private copy; the 3 others are the OCR'd excerpt's `full_text.md` and
      `ch00_Front_Matter.md` (+ the `Source:` line, nothing else) and `batch_progress.json`
      (same entries, different order). `post_convert --resplit-only`: 163/168 identical; the
      excerpt is now split from its PDF (`pdf_found_by: source_path`, `split: pattern`, same
      4 chapter files) instead of carried, and its manifest/report say so; `_Guidelines/
      batch_progress.json` no longer copied (0.8.1, intended). Source PDFs unchanged (sha256).
- [x] Real-data subset (temp copy of 4 books): AHA guideline + Weiss byte-identical to the
      production corpus; a book with a missing `Source:` PDF and one with no `Source:` line
      fail with the fix under `T2N_REQUIRE_SOURCE_PDF=1`; no OneDrive folder listed

## Ship

- [x] `CHANGELOG.md` 0.8.1 entry (unpublished release, folded in; tag moved)
- [x] Archived on landing (Tier 1, no separate review round)
