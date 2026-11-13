# Tasks — held-book-fix-request

## Implementation

- [x] `post_convert`: move held books to `_held/`, write `_fix_request.md`, release fixed ones,
      hand-off always runs → Requirement: An invalid split is held alone, with a fix request
- [x] `resplit_plan()` rule for held books → same Requirement
- [x] Docs: `docs/corpus-maintenance.md` section rewritten; README sentence; CHANGELOG 0.8.3 entry

## Verification

- [x] `test_corpus_handoff.py`: held / fixed (link target survives) / plan / stale, CLI
- [x] Corpus measurement: held books on the live corpus (expect 0 → nothing moves)
- [x] Weiss re-OCR'd (two-page spreads) and split without `chapter_numbers: false`
- [x] Full suite green

## Ship

- [x] Fold into unpublished 0.8.3, move tag, archive

### Measurement (2026-11-14)

- Live corpus held books after this change: 0 (nothing moves on upgrade).
- Weiss, the one book that had failed: re-OCR'd with `T2N_OCR_GUTTER=1` (137 two-up PDF pages;
  the old OCR interleaved both book pages line by line). The printed contents gave 8 chapters;
  anchoring found 3 of 8 headings (bold tags / headings split over lines), so the config gives
  each chapter's PDF page. Result: 8 chapter files, validator PASS without
  `chapter_numbers: false`; chapter check 7 confirmed, 1 contradicted (ch5 starts on the right
  half of a spread whose left half is ch4's references). Whole corpus 285 / 285 PASS.
- Full suite green; held/plan/release tests include a real junction whose target survives.
