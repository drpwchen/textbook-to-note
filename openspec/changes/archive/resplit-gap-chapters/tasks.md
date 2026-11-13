# Tasks — resplit-gap-chapters

## Implementation

- [x] `split_manifest.promote_gap_chapters()`, called in `resplit.resplit_unit()` right after
      `promote_keyword_chapters()` under the same guards
      → Requirement: Bare-numbered chapters at another level fill a numbering gap

## Verification

- [x] `test_resplit.py::test_gap_chapters` — every scenario (unit level, one end-to-end resplit,
      and a book with `chapter_number_overrides` that must not be promoted)
- [x] Corpus measurement: full resplit of the maintainer's corpus, HEAD vs this change
- [x] Ropper without its overrides / `split_levels`: same chapter number on every page as the live split
- [x] Full suite green

## Ship

- [x] `CHANGELOG.md` entry, version, tag, archive

### Measurement (2026-11-14)

- Full resplit of the maintainer's corpus (`textbook-md` → scratch, the live `_split_config.json`,
  explicit PDF map), HEAD `bbb2bf9` vs this change: 288 books, 19,128 files, **0 changed**
  (content hash + manifest), validator 283 / 283. The only book of this shape carries
  `chapter_number_overrides`, which still disables promotion.
- Ropper with a config of only `method: reconstruct` + `toc_chapters: 49` (no overrides, no
  `split_levels`, no `chapter_level`): chapter level 2 chosen automatically, 24 entries promoted,
  69 files, validator passes, and every one of the 1,616 PDF pages carries the same chapter
  number as the hand-numbered split.
