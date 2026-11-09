# conversion — delta for force-clears-stale-chapters

## ADDED Requirements

### Requirement: Re-conversion removes the book's own stale split files before splitting

When `--batch-dir` re-converts a PDF or EPUB whose output directory already exists (`--force` or
the source being newer than `full_text.md`), the system SHALL,
after `full_text.md` has been written successfully and before the chapter split writes, remove
from that book's output directory every file whose name matches the converter's split naming:

- `^ch\d{2,}(_[A-Za-z0-9_]*)?\.md$` (bookmark / pattern split, incl. `ch00_Front_Matter.md`)
- `^pages_\d{4}-\d{4}\.md$` (`_force_split`)

Removal is a direct delete (`os.remove`); the run log prints the removed file names, one line
per book. Default ON; `T2N_CLEAR_STALE_CHAPTERS=0` disables it.

#### Scenario: A chapter the new split no longer produces disappears
- **GIVEN** `OUTPUT_BASE/BookA/` holds `full_text.md`, `ch01_Part_I.md` (0 bytes),
  `ch02_Gait.md`, and the new split produces only `ch02_Gait.md`
- **WHEN** `--batch-dir --force` re-converts `BookA.pdf`
- **THEN** `OUTPUT_BASE/BookA/ch01_Part_I.md` does not exist and `ch02_Gait.md` has the new bytes

#### Scenario: Source-newer re-conversion and EPUB clear too
- **GIVEN** a stale `ch01_Part_I.md` in `OUTPUT_BASE/BookA/`, and the source (PDF, or an EPUB
  `BookA.epub`) is newer than `full_text.md`; no `--force`
- **WHEN** `--batch-dir` runs
- **THEN** `ch01_Part_I.md` does not exist after the run and the log names it

#### Scenario: Unrelated files survive
- **GIVEN** the same directory also holds `notes.md`, `Ch12_Hand.md` (capital C), `ch1.md`
  (one digit), `figures/ch03_x.md`, `_manifest.json`
- **WHEN** the book is re-converted
- **THEN** all five still exist with unchanged bytes

#### Scenario: Failed conversion keeps the old chapters
- **GIVEN** a book with existing chapter files
- **WHEN** `convert_pdf` raises before `full_text.md` is written
- **THEN** every pre-existing `ch*.md` / `pages_*.md` still exists with unchanged bytes

#### Scenario: Kill switch
- **GIVEN** `T2N_CLEAR_STALE_CHAPTERS=0`
- **WHEN** the same re-conversion runs
- **THEN** the directory listing after the run equals v0.7.3's (stale `ch01_Part_I.md` remains)

**Negative constraints**
- The system MUST NOT copy or move the removed files to a holding folder inside `OUTPUT_BASE`
  (the indexer would pick them up again).
- The system MUST NOT recurse into subdirectories or follow symlinks/junctions.
- The system MUST NOT remove `full_text.md`, or any file not matching the two patterns above.
- The system MUST NOT touch any directory other than `OUTPUT_BASE/<stem of the PDF being
  converted>`; a resolved path outside `OUTPUT_BASE` aborts the clear for that book with a
  warning and no removal.
- The system MUST NOT clear a book that is skipped in this run (already converted,
  `already_converted.json`, OCR unavailable).
