# conversion — delta for resplit-deep-bookmarks

## ADDED Requirements

### Requirement: Deep bookmarks become markdown headings when opted in

With `T2N_SPLIT_DEEP_HEADINGS=1`, after a bookmark split the system SHALL insert one line per
bookmark below the split levels into the chapter file containing that bookmark's page — level 3
→ `## <title>`, level ≥4 → `### <title>` — directly above the first line on that page whose
normalised text equals the normalised title, or equals it when joined with the next line; and
SHALL print `deep headings: <inserted>/<total> inserted`. Default OFF.

#### Scenario: Matched section title
- **GIVEN** bookmarks `[2, "Chapter 1 Gait", 3]`, `[3, "Overview", 3]`, and page 3 of
  `full_text.md` containing a line `Overview`
- **WHEN** the book is split with `T2N_SPLIT_DEEP_HEADINGS=1`
- **THEN** the chapter file contains `## Overview` directly above `Overview`, and `Overview`
  occurs in it exactly as often as without the flag

#### Scenario: Level 4
- **GIVEN** `[4, "Overview", 6]` and a line `Overview` on page 6
- **THEN** `### Overview` is inserted above it

#### Scenario: Title not found on its page
- **GIVEN** `[3, "Missing Title", 4]` with no such line on page 4
- **THEN** nothing is inserted for it and the summary reads `deep headings: 2/3 inserted` for
  the three deep bookmarks of this book

#### Scenario: Flag unset
- **GIVEN** any outline with level-3+ entries
- **WHEN** the book is split with the flag unset or `0`
- **THEN** every chapter file's bytes equal the 0.7.6 output

**Negative constraints**
- MUST NOT delete, reorder or edit an existing line; removing the inserted `## ` / `### ` lines
  restores the flag-off output exactly.
- MUST NOT change file boundaries or names.
- MUST NOT write to `full_text.md` or `ch00_Front_Matter.md`.
- MUST NOT insert a heading where the title does not match.
