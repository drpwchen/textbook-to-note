# Delta — conversion

## ADDED Requirements

### Requirement: An invalid split holds back the hand-off

`post_convert.py` SHALL read `<corpus>/_resplit_report.json` after the resplit chain (default
run, `--index`, `--resplit-only`) and, for `--backfill`, before the hand-off (not on the
deprecated `INDEXER_SCRIPT` route). A book is *held* when its entry has `status == "ok"` and
`valid == false` and `<corpus>/<book>/` exists. For each held book it SHALL print the book, its
`errors`, one hint line per failing check naming the split-config keys that address it, and the
commands that re-split and re-validate that book (`convert.py --resplit … --book <book> --force`,
`split_manifest.py --validate … --book <book>`). While any book is held the hand-off SHALL NOT
run, the `[FAIL]` list SHALL contain `invalid split: <book> [<checks>]` per held book, and the
exit code SHALL be 1.

#### Scenario: held book
- **GIVEN** a corpus whose report marks `BookA` `valid: false, errors: ["chapter_gap"]` and
  `BookB` valid, and an ask-your-library checkout configured
- **WHEN** `post_convert.py --index` runs
- **THEN** stdout contains `invalid split: BookA [chapter_gap]`, a hint containing
  `missing_chapters_ok`, and `--book "BookA" --force`; `corpus.py` is not run; exit code 1

#### Scenario: fixed book
- **GIVEN** the same corpus after BookA's entry becomes `valid: true`
- **THEN** `corpus.py add <root>` runs once and the exit code is its own

#### Scenario: stale entries
- **GIVEN** a `valid: false` entry for a book folder that no longer exists, an entry with
  `status: "error"`, and an entry without a `valid` key
- **THEN** nothing is held

**Negative constraints**
- MUST NOT re-run the validator or resplit anything to decide (the report is the record).
- MUST NOT hold a book on a warning.
- MUST NOT change the `INDEXER_SCRIPT` route.

### Requirement: Books resplit in a run get a chapter-number check

`converter/chapter_check.py` SHALL, for each `body` file with a `chapter_number` n, give a
verdict from the file's first two pages: `confirmed` (`Chapter n`, a line that is just `n`,
a line `n` + one of the title's first three ≥3-letter words, or `n.x` figure/table/section
numbering), `contradicted` (no such evidence and `Chapter m`, m ≠ n, present) or `unknown`.
`post_convert.py` SHALL run it on every book resplit successfully in this run and print
`chapter check <book>: confirmed X, unknown Y, contradicted Z`; when Z > X it SHALL print
`[WARN]` with up to 5 contradicted files. It SHALL NOT add a failure or change the exit code.
CLI: `python converter/chapter_check.py <corpus> [--book B]… [--sample N] [--seed S] [--json FILE]`
(read-only; exit 0).

#### Scenario: numbers off by a constant
- **GIVEN** a book whose files open with `Chapter 1`, `Chapter 2`, `Chapter 3` but carry
  `chapter_number` 8, 9, 10
- **THEN** all three are `contradicted` and the run prints `[WARN]`; the exit code is unchanged

#### Scenario: correct numbers
- **GIVEN** files opening `CHAPTER 4` / a bare line `5` / `6 Gait Training` with numbers 4, 5, 6
  (title of the third: `Gait Training`)
- **THEN** all three are `confirmed`

## MODIFIED Requirements

### Requirement: `post_convert.py` keeps the corpus in step with the converted output

Only this changes: the `[FAIL]` list also carries held books (see "An invalid split holds back
the hand-off"), and the hand-off on `--index` / default / `--backfill` runs only when no book is
held.
