# Tasks — split-acceptance-gate

## Implementation

- [x] `post_convert.held_books()` + hints + gate before every non-legacy hand-off
      → Requirement: An invalid split holds back the hand-off
- [x] `converter/chapter_check.py` (module + CLI); `post_convert` runs it on this run's books
      → Requirement: Books resplit in a run get a chapter-number check
- [x] Docs: `docs/corpus-maintenance.md` "When a book fails validation" (check → config keys →
      re-split → validate)

## Verification

- [x] `test_corpus_handoff.py`: held / fixed / stale scenarios, CLI exit code
- [x] `test_chapter_check.py`: both scenarios
- [x] Corpus measurement: held books on the live corpus before / after the Weiss entry;
      chapter check over every numbered file
- [x] Full suite green

## Ship

- [x] `CHANGELOG.md` entry (folded into the unpublished version), tag, archive

### Measurement (2026-11-14)

- Held books on the maintainer's corpus (`textbook-md-v2`, 285 resplit books): before, 1
  (Weiss, `chapter_gap`); after its config entry got `chapter_numbers: false` (reason: two-page
  spreads OCR'd line-interleaved, the "chapters" 3 and 50 were a street address and a garbled
  line) and a `--force` resplit, 0. A trial resplit into scratch with that entry validated first.
- `chapter_check.py` over every numbered body file: 9,273 files in 285 books — confirmed 7,814,
  unknown 1,338, contradicted 121 (identical to the 2026-11-05 scratch script); 22 books have
  some contradicted files, inspected ones are cross-references ("see Chapter 17"); 0 books have
  contradicted > confirmed, so the run prints no `[WARN]` on this corpus.
- Full suite green (converter, figures contract, citations).
