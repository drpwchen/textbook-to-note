# Tasks — no-full-text-sources

## Implementation

- [x] `pdf_roots_for()` reads chapter files' `Source:` when there is no `full_text.md`
- [x] `configured_pdf()`; `unresolved_sources()` covers chapter-files-only books (strict mode)

## Verification

- [x] `converter/test_maintainer_config.py` 29/29 (no-full_text strict cases; roots from chapter
      Source; unset unchanged). Mutation: no-full_text branch off → 3 fail; config `pdf` ignored →
      1 fail; "all chapter Sources found" forced true → 3 fail; roots fallback removed → 1 fail
- [x] Corpus (2026-11-14): the 94 chapter-files-only books of the maintainer's corpus resplit
      before vs after (read-only source, temp out, no OneDrive listing): 28 books' output changed,
      **every one now byte-identical to the production corpus** (12 manifest-only, 16 whose chapter
      files were wrong); validator 78/94 → 94/94; no book went from pass to fail
- [x] Strict check over the real corpus lists 14 chapter-files-only books with no findable PDF
      (they need a `pdf` config entry)

## Also in 0.8.2 (same area)

- [x] `split_manifest.is_tooling_dir()`: `_`-folders other than `_Guidelines` are skipped by
      `--resplit`, `post_convert.book_folders()` and `--validate`. Real corpus: post_convert
      289 → 288 folders (drops `_figure_remap`), validator on textbook-md 291 → 289 (drops
      `_figure_remap`, `__pycache__`), validator on textbook-md-v2 285 → 285. Test
      `test_tooling_folders_skipped`; mutation (rule reverted to `.` only) → 4 fail.

## Ship

- [x] `CHANGELOG.md` 0.8.2; tag `v0.8.2` moved (unpublished)
