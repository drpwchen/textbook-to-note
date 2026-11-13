# Delta — conversion

## MODIFIED Requirements

### Requirement: An invalid split is held alone, with a fix request

(Replaces "An invalid split holds back the hand-off".)

`post_convert.py` SHALL read `<corpus>/_resplit_report.json` after the resplit chain (default
run, `--index`, `--resplit-only`) and, for `--backfill`, before the hand-off (not on the
deprecated `INDEXER_SCRIPT` route). A book is *held* when its entry has `status == "ok"` and
`valid == false` and `<corpus>/<book>/` or `<corpus>/_held/<book>/` exists. For each held book:

- `<corpus>/<book>/`, if present, SHALL move to `<corpus>/_held/<book>/` (replacing an older
  held copy; links inside a removed copy are unlinked, never followed).
- `<corpus>/_held/<book>/_fix_request.md` SHALL be (re)written: the book, each failing check
  with the validator's detail lines, the config keys per check, the config file path, the
  re-split and validate commands, the chapter-check command, the done criterion, and the rules.
- stdout SHALL list it after `[FAIL]` as `invalid split: <book> [<checks>]`, with the path of
  its fix request; the exit code SHALL be 1.

The hand-off SHALL run whether or not books are held (they are under `_held/`, which
ask-your-library skips). A `_held/<book>/` whose report entry is valid and whose
`<corpus>/<book>/` exists SHALL be removed (the book was fixed).

`resplit_plan()` SHALL plan a held book (no `<corpus>/<book>/_manifest.json`, a
`_held/<book>/_manifest.json`) as forced when the book's newest `.md` or
`<corpus>/_split_config.json` is newer than the held manifest, and SHALL NOT plan it otherwise.

#### Scenario: held book
- **GIVEN** a corpus whose report marks `BookA` `valid: false, errors: ["chapter_gap"]` and
  `BookB` valid, both folders present, an ask-your-library checkout configured
- **WHEN** `post_convert.py --index` runs
- **THEN** `BookA` is at `_held/BookA/` (not at `BookA/`), `_held/BookA/_fix_request.md` contains
  `chapter_gap`, `missing_chapters_ok` and `--book "BookA" --force`; stdout contains
  `invalid split: BookA [chapter_gap]`; `corpus.py add <root>` runs once; exit code 1

#### Scenario: fixed book
- **GIVEN** `_held/BookA/` and a fresh `BookA/` whose report entry is valid
- **THEN** `_held/BookA/` is removed, a file the held copy linked to still exists, nothing is
  held, and the exit code is the hand-off's own

#### Scenario: plan for a held book
- **GIVEN** `_held/BookA/_manifest.json` newer than BookA's markdown and the split config
- **THEN** BookA is not planned; after the config is touched it is planned as `("BookA", True)`

#### Scenario: stale entries
- **GIVEN** a `valid: false` entry for a book with neither folder, an entry with
  `status: "error"`, and an entry without a `valid` key
- **THEN** nothing is held or moved

**Negative constraints**
- MUST NOT re-run resplit to decide whether a book is held (the report is the record).
- MUST NOT hold a book on a warning.
- MUST NOT follow a link when removing a held copy.
- MUST NOT change the `INDEXER_SCRIPT` route.
