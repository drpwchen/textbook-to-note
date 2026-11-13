# Tasks — chapter-numbers-few-or-lost

## Implementation

- [x] `choose_chapter_level()` few-`Chapter N` fallback  → Requirement: One or two `Chapter N` outline entries still make a chapter level
- [x] resplit writes `chapter_numbers_expected`; validator `chapter_numbers_missing`  → Requirement: The validator fails a book that was supposed to be numbered but is not
- [x] `examples/sample/_split_config.json`: drop `chapter_level`, keep the retitle; quickstart "Why" paragraph (EN + zh)

## Verification

- [x] `converter/test_chapter_numbers.py` 19/19 (synthetic one- and two-chapter PDFs, bare
      number, backwards, ≥ 3 unchanged, the Hopkins letter-regex case, an unnumbered guideline)
- [x] Mutation: fallback off → 5 fail; keyword requirement removed → 2 fail; manifest flag
      off → 2 fail; validator check off → 2 fail
- [x] Full suite green; CI runs the new test
- [x] Corpus (2026-11-14): full `--resplit` of the maintainer's 285-book corpus (read-only
      source, temp out, no OneDrive listing), code before vs after: **0 of 19,435 output files
      differ, 0 verdicts differ** (268/285 pass both; the 17 failures are books whose PDF this
      run could not reach without listing the synced drive, identical in both). Because 29 of
      those books were carried without their outline, the level choice was also re-run on all
      185 outlines recorded in the production corpus's manifests: 0 changes. Validator over the
      production corpus: 284/285 before and after (Weiss_Schroth, `chapter_gap`, unchanged).
- [x] Hopkins with the letter-suffix regex config, real data, temp dirs: before PASS (no
      numbers), after FAIL `chapter_numbers_missing`
- [x] Quickstart steps 2–3 replayed: same lines (`config valid 10 files`, `1/1 books pass`);
      corpus identical to the old config's except the manifest's config-reason line

## Ship

- [x] `CHANGELOG.md` 0.8.2, version bump, tag `v0.8.2`
- [x] Archived on landing
