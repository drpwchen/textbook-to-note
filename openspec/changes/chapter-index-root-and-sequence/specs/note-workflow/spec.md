# note-workflow — delta for chapter-index-root-and-sequence

## ADDED Requirements

### Requirement: The chapter index never scans an unnamed directory

The system SHALL resolve its corpus root from `--textbook-dir`, then `TEXTBOOK_DIR`, then
`{repo}/output` if that directory exists, and SHALL fail rather than scan the current working
directory.

#### Scenario: No corpus named, repo output exists
- **GIVEN** neither `--textbook-dir` nor `TEXTBOOK_DIR` is set, and `{repo}/output` is a directory
- **WHEN** `textbook_chapter_index.py --rebuild` runs from any working directory
- **THEN** the printed summary line names `{repo}/output` as the scanned root

#### Scenario: No corpus named and no repo output
- **GIVEN** neither `--textbook-dir` nor `TEXTBOOK_DIR` is set and `{repo}/output` does not exist
- **WHEN** `textbook_chapter_index.py --rebuild` or `textbook_ref_lint.py` runs
- **THEN** it exits 2 with a message naming both `--textbook-dir` and `TEXTBOOK_DIR`, and writes
  no index file

**Negative constraints**
- The resolved root MUST NOT be the process working directory unless the user named it.
- A failed resolution MUST NOT write, truncate, or delete `_chapter_index.json`.

### Requirement: A rebuild that recognises no book is refused

The system SHALL refuse to write an index in which no directory was recognised as a converted
book (every entry `strategy: "none"`), because that is what scanning the wrong directory looks
like.

#### Scenario: Wrong directory scanned
- **GIVEN** a root whose subdirectories are not converted books
- **WHEN** `--rebuild` runs
- **THEN** it exits 2, prints `REFUSED` with the scanned root and the count of directories
  examined, and no index file is written

#### Scenario: A genuinely unmappable corpus can still be indexed
- **GIVEN** the same root and `--allow-empty`
- **WHEN** `--rebuild` runs
- **THEN** the index is written and the exit code is 0

#### Scenario: An unreadable subdirectory does not abort the scan
- **GIVEN** a root containing a subdirectory that raises `PermissionError` on listing (a Windows
  user-profile junction is the common case)
- **WHEN** `--rebuild` runs
- **THEN** that directory is skipped, the remaining books are indexed, the index is written, and
  the summary line reports the skipped count

**Negative constraints**
- A single unreadable directory MUST NOT raise out of `build()`.
- The refusal MUST NOT be silenced by tuning what counts as "recognised". `--allow-empty` is the
  only override.
- A book recognised as `seq` counts as recognised: a corpus converted entirely by this repo's own
  converter must still index.

### Requirement: A sequence-split book is named as such, never guessed at

The system SHALL record a book whose markdown files are named `chNN_Title.md` (the converter's
own split naming, where `NN` is a file sequence and not the printed chapter number) as
`strategy: "seq"` with its sequence→title table, and SHALL report a citation into it as
UNVERIFIABLE naming the file that sequence number points at.

The trigger is at least 3 files matching `^ch(\d+)[_ ](.+)\.md$` with a non-zero number, tried
only after every chapter-number strategy has failed.

#### Scenario: Converter-split book is distinguished from a structureless one
- **GIVEN** a book directory holding `ch01_Introduction.md`, `ch02_Methods.md`, `ch03_Results.md`
- **WHEN** the index is rebuilt
- **THEN** that book's `strategy` is `"seq"`, its `chapters` is empty, and its `sequence` maps
  `"2"` to `"Methods"`

#### Scenario: Citing into a sequence-split book says what to do about it
- **GIVEN** an index in which `Alpha_Handbook_7e_2021` has `strategy: "seq"`
- **WHEN** `Alpha Ch.2` is linted
- **THEN** the verdict is UNVERIFIABLE and the detail names the book, states it was split by the
  converter's file sequence, and names `_chapter_index.chapters.json`

#### Scenario: A pinned chapter table makes the book checkable
- **GIVEN** `_chapter_index.chapters.json` containing `{"Alpha_Handbook_7e_2021": {"2": "Middles"}}`
- **WHEN** the index is rebuilt and `Alpha Ch.2` is linted
- **THEN** that book's `strategy` is `"pinned"` and the verdict is OK with the title `Middles`

**Negative constraints**
- The sequence number MUST NOT be recorded as, or reported as, a chapter number. `chapters` stays
  empty for a `seq` book.
- A `seq` book MUST NOT produce an OK, BAD_CHAPTER, or FILE_INDEX verdict. The only chapter fact
  available is the one the user pins.
- `_chapter_index.chapters.json` MUST NOT be written or modified by any code path.
- Strategy A (`^Ch(\d+)[_ ](.+)\.md$`) MUST stay case-sensitive, so the converter's lowercase
  `chNN` output cannot be read as a chapter number.
