# Delta — conversion

## ADDED Requirements

### Requirement: Bare-numbered chapters at another level fill a numbering gap

When `--resplit` has a chapter level for a PDF outline and the book may be promoted (no
`chapter_number_overrides`, `promote_chapters` not `false`), then after `Chapter N` promotion,
for every two consecutive numbered chapter-level entries with numbers `a < b` and `b − a > 1`:
if the entries between them at one other level whose titles carry a chapter number are exactly
`a+1, …, b−1` in outline order, and their pages are non-decreasing, not lower than `a`'s page
and not higher than `b`'s, each of them SHALL move to the chapter level with its subtree (until
the next entry at its original level or shallower, and never past `b`). The files they start
carry those numbers. They are counted in the run report's `promoted_chapters`; `sections` keep
the PDF's original levels.

#### Scenario: Chapters one Section deeper in the middle of the book (Ropper shape)
- **GIVEN** an outline `1 Gait` (L2, p3), `Section A` (L2, p5), `2 Balance` (L3, p6) ›
  `Overview` (L4, p6), `3 Spasticity` (L2, p9), config `chapter_level: 2`
- **WHEN** the book is resplit
- **THEN** a file with `chapter_number` 2 starts at page 6, the chapter-1 file ends at page 4,
  the book validates, and the report has `promoted_chapters`

#### Scenario: Subsection numbers inside a gap
- **GIVEN** chapter-level `1 Gait` (p3) and `3 Spasticity` (p9) with level-3 entries
  `1 Intro` (p4), `2 Methods` (p5) between them
- **THEN** nothing is promoted (the run 1, 2 is not exactly 2)

#### Scenario: Run overshoots the gap
- **GIVEN** chapter-level `1` and `3` with level-3 entries `2` and `4` between them
- **THEN** nothing is promoted

#### Scenario: Page before the previous chapter
- **GIVEN** chapter-level `1` (p5) and `3` (p9) with a level-3 `2` at page 3
- **THEN** nothing is promoted

#### Scenario: Hand-numbered book
- **GIVEN** the first scenario's book with a non-empty `chapter_number_overrides`
- **THEN** nothing is promoted

**Negative constraints**
- MUST NOT promote outside an interior gap (before the first or after the last chapter-level number).
- MUST NOT promote in a book whose config has `chapter_number_overrides` or `promote_chapters: false`.
- MUST NOT promote when the book has no chapter level.

## MODIFIED Requirements

### Requirement: A `Chapter N` outline entry is a chapter at whatever level it sits

Only this negative constraint changes:

- ~~MUST NOT promote a title that only starts with a number (`26 Female Athlete`).~~
- MUST NOT promote a title that only starts with a number (`26 Female Athlete`), except as
  "Bare-numbered chapters at another level fill a numbering gap" allows.
