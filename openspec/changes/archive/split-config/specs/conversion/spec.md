# conversion — delta for split-config

## ADDED Requirements

### Requirement: A per-book split config steers `--resplit` without code changes

`--resplit` SHALL read `--split-config FILE`, else `<out>/_split_config.json`, and apply the
entry named after the book folder (unit entries under `units` override it for that sub-folder)
before splitting: `drop_titles`, then `page_offset`, then `anchor_titles`, then removal of
split-level entries whose page is lower than an earlier one. With `anchor_titles`, a split-level
entry is placed at the line whose normalised text equals its numbering-stripped title (also
over up to 3 joined lines), preferring lines that carry the entry's own chapter number
(including such a line that is the title cut short or with a typo, similarity ≥ 0.85); among
candidates the longest increasing sequence is kept; pages where ≥ 5 distinct titles match are
treated as a contents listing and skipped. Deeper entries are placed inside their chapter's span.

#### Scenario: Broken outline repaired by anchoring
- **GIVEN** a PDF whose outline entries all point at page 1, `full_text.md` with `CHAPTER 1 /
  GAIT` on page 3, `CHAPTER 2 / BALANCE` on page 6, `CHAPTER 3 / SPASTICITY` on page 9, and the
  config `{"BookA": {"anchor_titles": true}}`
- **WHEN** BookA is resplit
- **THEN** the files with `chapter_number` 1 / 2 / 3 start at PDF pages 3 / 6 / 9,
  `split_method == "config"`, and the book validates

#### Scenario: Without the entry
- **GIVEN** the same book, no config entry
- **THEN** the book fails validation (its text lands in one file)

#### Scenario: Title not found
- **GIVEN** an anchored entry whose title occurs nowhere after the previous placed entry
- **THEN** it is not a split point, its `sections` row has `pdf_page: null`, and the run report
  counts it in `split_total` but not `split_anchored`

#### Scenario: A file whose title cannot be verified
- **GIVEN** a config entry `title_check_skip: {"ch26_SECTION_1.md": "divider page, title is an image"}`
- **THEN** the manifest carries the same `title_check_skip` map and that file is not title-checked

#### Scenario: Outline given by the config
- **GIVEN** a PDF with no usable outline (none, or one written by converter ≤ 0.7.3) and the entry
  `toc: [[1, "Chapter 1 Gait", 3], [1, "Chapter 2 Balance", 6], [1, "Chapter 3 Spasticity", 9]]`
- **THEN** the files with `chapter_number` 1 / 2 / 3 start at PDF pages 3 / 6 / 9 and
  `split_method == "config"`; with `anchor_titles: true` the pages may be `null` and the
  titles are located in the text as for a PDF outline

#### Scenario: Book folder starting with `_`
- **GIVEN** a config entry for the book folder `_Guidelines` (a dict) next to the `_doc` note
- **THEN** the `_Guidelines` entry is applied; `_doc` (a string) is ignored

#### Scenario: Override makes a chapter of a front-by-position file
- **GIVEN** an unnumbered chapter-1 file marked `front` because it precedes the first numbered
  chapter, and `chapter_number_overrides` giving it 1
- **THEN** its `kind` is `body`; a file before it whose title is front matter stays `front`

#### Scenario: Reconstruct over pages the old splitter duplicated
- **GIVEN** `method: reconstruct` and old split files in which page 252 appears in two files with
  identical text
- **THEN** page 252 appears once in the rebuilt text; when the two copies differ the book is
  refused (`error`, "page markers not strictly increasing") and nothing is written for it

#### Scenario: Reconstruct honours `exclude_files`
- **GIVEN** `method: reconstruct` and an excluded old whole-book duplicate `ch08_x.md`
- **THEN** none of its text appears in the reconstructed book

**Negative constraints**
- Every config entry MUST carry a `reason`; `title_check_skip`, `missing_chapters_ok` and
  `exclude_files` are only for a cause written in it (image-only title, chapter absent from the
  source, duplicate file), never to make a wrong split pass.
- The converter MUST NOT write or modify the config file.
- A book without an entry MUST be split exactly as without the config file.
- An entry MUST NOT be placed at a guessed position when no line matches.
