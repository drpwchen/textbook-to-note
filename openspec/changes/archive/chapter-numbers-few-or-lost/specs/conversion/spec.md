# Delta — conversion

## ADDED Requirements

### Requirement: One or two `Chapter N` outline entries still make a chapter level

When no outline level has at least 3 numbered titles and no level's numbers go backwards,
`choose_chapter_level()` SHALL return the level whose numbered titles (with the book's
`chapter_regex`, if any) are all written `Chapter N` / `Chap. N` with that same N, number 1 or 2,
and strictly increase (most such titles wins; ties → the shallower level), with reason
`few chapters, titled `Chapter N``. Bare numbers (`5 Stroke`) never qualify on their own.
Any book for which `choose_chapter_level()` returned a level before still gets the same level.

#### Scenario: a one-chapter excerpt
- **GIVEN** an outline `[1 "Chapter 1. An Introduction", 2 "1.1. Overview", 2 "1.2. Homeostasis"]`
  and no split config
- **WHEN** `--resplit` runs
- **THEN** the manifest's `chapter_level` is 1, every body file carries chapter number 1, and
  the validator passes (before: `chapter_unmapped`)

#### Scenario: a bare number
- **GIVEN** an outline `[1 "5 Stroke", 2 "Overview"]`
- **THEN** no chapter level is chosen (as before)

#### Scenario: three or more numbers
- **GIVEN** any outline with a level of ≥ 3 increasing numbers, or a level of ≥ 3 numbers that go
  backwards
- **THEN** the result is the previous release's

### Requirement: The validator fails a book that was supposed to be numbered but is not

`--validate` SHALL fail a book with `chapter_numbers_missing` when no file carries a chapter
number and either the manifest's `chapter_level` is not null, or the manifest carries
`chapter_numbers_expected`. `--resplit` SHALL write `chapter_numbers_expected` (text naming the
config keys) only when the book's split config sets `chapter_regex` or `chapter_level`, does not
set `chapter_numbers: false`, and no file ended up with a chapter number (after
`chapter_number_overrides`). A book whose outline and config say nothing about chapter numbers
keeps passing with no numbers.

#### Scenario: numbers lost to a regex (failure case)
- **GIVEN** outline titles `7.`, `8a.`, `8b.`, `9.` and config `chapter_regex: "^(\d+)[a-z]?\b"`
  (8, 8 is not increasing, so every number is dropped)
- **THEN** the manifest has `chapter_numbers_expected` and the book FAILs with
  `chapter_numbers_missing` (before: PASS with no numbers)

#### Scenario: a guideline with no numbered chapters
- **GIVEN** outline `Introduction`, `Methods`, `Recommendations` and no config
- **THEN** the book passes and its manifest has no `chapter_numbers_expected`

