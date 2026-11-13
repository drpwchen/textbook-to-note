# conversion — delta for resplit-cross-level-chapters

## ADDED Requirements

### Requirement: A `Chapter N` outline entry is a chapter at whatever level it sits

When `--resplit` has a chapter level for a PDF outline (from the config's `chapter_level` or
chosen automatically), every entry whose title starts with `Chapter N` / `Chap. N` (case
ignored, `N` = 1–3 digits) at another level, whose `N` no chapter-level entry carries, SHALL be
split as a chapter: it and the entries below it (until the next entry at its original level or
shallower) move by the same level difference. The file it starts carries `chapter_number` N.
`sections` keep the PDF's original levels. The run report counts moved entries as
`promoted_chapters`. A config entry `promote_chapters: false` turns this off for that book; a book whose config
entry has `chapter_number_overrides` is never promoted (its split was numbered by hand).

#### Scenario: Chapters nested one Section deeper
- **GIVEN** an outline `CHAPTER 24 X` (L2, p600), `CHAPTER 25 Y` (L2, p615), `SECTION IV` (L3,
  p650), `CHAPTER 26 Z` (L4, p650) › `Introduction` (L5, p650), `CHAPTER 27 W` (L4, p670)
- **WHEN** the book is resplit
- **THEN** files with `chapter_number` 26 and 27 start at pages 650 and 670, the chapter-25 file
  ends at page 649, and `sections` still lists `CHAPTER 26 Z` with `level: 4`

#### Scenario: First chapter one level shallower
- **GIVEN** `CHAPTER 1: Family-Centered Care` (L1, p19) › 6 sections (L2), `PART I` (L1, p33),
  `CHAPTER 2: Motor Development` (L2, p35)
- **THEN** pages 19–32 are one file with `chapter_number` 1 and `kind: body`, not six unnumbered files

#### Scenario: Number already taken
- **GIVEN** chapter-level entries `Chapter 1`…`Chapter 5` and an L3 entry `Chapter 3 summary`
- **THEN** nothing is promoted

#### Scenario: Turned off
- **GIVEN** the first scenario's book with config `{"promote_chapters": false}`
- **THEN** the output is identical to before this change

**Negative constraints**
- MUST NOT promote when the book has no chapter level.
- MUST NOT promote in a book whose config entry has `chapter_number_overrides`.
- MUST NOT promote a title that only starts with a number (`26 Female Athlete`).

## MODIFIED Requirements

### Requirement: The validator fails a book whose split breaks the contract

(adds one check to the list)
`chapter_unmapped` (a `sections` entry whose title starts with `Chapter N` / `Chap. N` names a
number no file carries, unless listed in `missing_chapters_ok`; books with
`split_method == "per_chapter_pdf"` are exempt, their outline numbers belong to each chapter PDF).

#### Scenario: Chapters merged into the previous file
- **GIVEN** a manifest whose `sections` list `CHAPTER 26 Z` and no file has `chapter_number` 26
- **THEN** the book FAILs with `chapter_unmapped` naming 26
