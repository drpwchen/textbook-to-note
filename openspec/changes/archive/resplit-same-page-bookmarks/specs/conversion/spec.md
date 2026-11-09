# conversion — delta for resplit-same-page-bookmarks

## ADDED Requirements

### Requirement: Bookmarks that share a split line produce one chapter file

When splitting by PDF bookmarks, the system SHALL collapse level-1/2 bookmarks that resolve to
the same `<!-- page N -->` line into a single split point, and SHALL NOT write a chapter file
for the collapsed bookmarks. Survivors keep their original sequence number (`idx + 1` over the
uncollapsed list), so gaps in `chNN` are expected. Default ON; `T2N_SPLIT_SAME_PAGE_MERGE=0`
disables it.

#### Scenario: Part and chapter bookmark on the same page
- **GIVEN** a PDF whose bookmarks are `[1, "Part I", 12]`, `[2, "Chapter 1 Gait", 12]`,
  `[2, "Chapter 2 Balance", 30]` (plus ≥1 more so `get_pdf_bookmarks` returns them) and a
  `full_text.md` with markers for pages 12 and 30
- **WHEN** the book is split with no `T2N_*` variables set
- **THEN** `ch01_Part_I.md` does not exist, `ch02_Chapter_1_Gait.md` starts at the page-12
  marker and ends before the page-30 marker, and `ch03_Chapter_2_Balance.md` exists

#### Scenario: No empty chapter file is written
- **GIVEN** any book split by bookmarks with the behaviour on, whose level-1/2 bookmark pages
  never decrease (out-of-order bookmarks are out of scope, see the negative constraints)
- **WHEN** the split finishes
- **THEN** every written `ch*.md` has a size > 0 bytes **and** at least 1 non-whitespace
  character outside `<!-- page N -->` lines

#### Scenario: Kill switch restores prior output
- **GIVEN** the same PDF and `full_text.md`, and `T2N_SPLIT_SAME_PAGE_MERGE=0`
- **WHEN** the book is split
- **THEN** the set of file names and every file's bytes equal the v0.7.3 output, including the
  0-byte `ch01_Part_I.md`

#### Scenario: Every bookmark resolves to one page (broken bookmarks)
- **GIVEN** a PDF with 235 level-1/2 bookmarks that all resolve to page 1 (Lanken 2e shape)
- **WHEN** the book is split
- **THEN** the bookmark split is still used (the `< 2` check counts bookmarks before
  collapsing, as today), `ch00_Front_Matter.md` and `ch235_<last title>.md` are written with the
  same bytes as v0.7.3, and no other `ch*.md` is written

**Negative constraints**
- Files that are non-empty today MUST keep their exact file name and bytes; no file is renamed
  or renumbered. Sole exception: a file with 0 non-whitespace characters outside
  `<!-- page N -->` lines is not written.
- The system MUST NOT move any text between files; collapsing only removes empty files.
- The system MUST NOT switch a book from the bookmark split to pattern detection because of
  collapsing.
- The system MUST NOT change behaviour for bookmarks whose page is lower than the previous
  bookmark's page (out of scope; a separate future change).
- `full_text.md` MUST NOT change.
