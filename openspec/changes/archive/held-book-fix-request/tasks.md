# Tasks — held-book-fix-request

## Implementation

- [ ] `post_convert`: move held books to `_held/`, write `_fix_request.md`, release fixed ones,
      hand-off always runs → Requirement: An invalid split is held alone, with a fix request
- [ ] `resplit_plan()` rule for held books → same Requirement
- [ ] Docs: `docs/corpus-maintenance.md` section rewritten; README sentence; CHANGELOG 0.8.3 entry

## Verification

- [ ] `test_corpus_handoff.py`: held / fixed (link target survives) / plan / stale, CLI
- [ ] Corpus measurement: held books on the live corpus (expect 0 → nothing moves)
- [ ] Weiss re-OCR'd (two-page spreads) and split without `chapter_numbers: false`
- [ ] Full suite green

## Ship

- [ ] Fold into unpublished 0.8.3, move tag, archive
