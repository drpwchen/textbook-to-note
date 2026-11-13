# split-acceptance-gate

Tier 1. Maintainer standing instruction (2026-11-09): drive to the output contract + validator,
no per-spec approval round. Requested by the maintainer 2026-11-14 ("新書切法驗收循環": hold an
invalid book out of the index).

## Why

`post_convert.py` counts a resplit as done when `resplit.run()` exits 0 and the book has a
`_manifest.json`. The validator verdict (`valid` / `errors` in `<corpus>/_resplit_report.json`)
is never read, so a book whose split breaks the contract is handed to ask-your-library's
`corpus.py add` and indexed like any other. The only invalid book in the maintainer's corpus
(Weiss, Schroth Therapy, `chapter_gap`: a street number and a garbled heading read as chapters
3 and 50) has been searchable that way since it was split.

A wrong chapter number that the validator cannot see (DeLee, every chapter off by +7, found
2026-11-05 only by a by-hand sample) needs a second, softer signal.

## What changes

1. After the resplit chain, every book that `_resplit_report.json` records as `valid: false`
   (and that still exists in the corpus) is a failure: `[FAIL]` lists it with its failing
   checks, a one-line hint per check naming the split-config keys that fix it, and the
   commands that re-split and re-validate the book (also on `--resplit-only`). While any such
   book exists, the ask-your-library hand-off does not run (on every route that would run it:
   default, `--index`, `--backfill`), and the exit code is 1. `corpus.py add` takes a whole corpus
   root, so holding the book back means holding the hand-off back.
2. Every book resplit in this run also gets a chapter-number check against its own pages
   (`converter/chapter_check.py`, the 2026-11-05 sample script made a module): counts of
   confirmed / unknown / contradicted are printed, and a book with more contradicted than
   confirmed files gets a `[WARN]`. It never fails the run.

## Not doing

- Per-book exclusion inside the hand-off (would need an ask-your-library change).
- Making the chapter check a gate: measured on the corpus, 22 of 202 numbered books have
  contradicted files, nearly all from in-text cross-references ("see Chapter 17"); no book has
  contradicted > confirmed, but there is no real true positive to prove a threshold on.
- Changing any validator check, threshold, or the split config format.
- The legacy `INDEXER_SCRIPT` route (deprecated; unchanged).

## Risk

Default-ON: a corpus that holds an invalid book now stops indexing until the book is fixed.
Measured on the maintainer's corpus: 285 resplit books, 1 invalid (Weiss) → held; after its
config entry (`chapter_numbers: false`, reason given) 0 held. A user with an older invalid book
sees `[FAIL]` with the fix on the next run instead of a silent pass — intended.

## Affected specs

- `openspec/specs/conversion/spec.md` — added: "An invalid split holds back the hand-off";
  "Books resplit in a run get a chapter-number check"; modified: "`post_convert.py` keeps the
  corpus in step with the converted output" (failure list, hand-off condition).
