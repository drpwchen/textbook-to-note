# conversion — delta for stop-pdf-bookmark-writeback

## ADDED Requirements

### Requirement: Chapter detection never writes to the input PDF

The system SHALL treat every input PDF as read-only in every mode (`--batch-dir`, `--batch`,
single file), including after a chapter split that found chapters in a PDF without bookmarks.
There is no flag that enables writing to it.

#### Scenario: Bookmark-less PDF split by pattern detection
- **GIVEN** a PDF with 0 bookmarks whose text yields ≥2 chapters via `detect_chapters()`
- **WHEN** `--batch-dir` converts it (fitz path)
- **THEN** the PDF's sha256, size and mtime are identical before and after the run, and
  `fitz.open(pdf).get_toc()` still returns `[]`

#### Scenario: Same on the OCR path
- **GIVEN** a scan-only PDF with 0 bookmarks routed to Surya, whose OCR text yields ≥2 chapters
- **WHEN** `--batch-dir` converts it
- **THEN** the PDF's sha256, size and mtime are identical before and after the run

#### Scenario: Split output unaffected
- **GIVEN** any PDF converted by v0.7.3 and by this version with the same env
- **WHEN** both outputs are compared
- **THEN** `full_text.md` and every chapter file are byte-identical

#### Scenario: Read-only PDF
- **GIVEN** a PDF with the filesystem read-only attribute set and 0 bookmarks
- **WHEN** `--batch-dir` converts it
- **THEN** conversion completes with no PDF write attempted and no warning about it

**Negative constraints**
- The converter MUST NOT write to the input PDF's path by any means (`save`/`saveIncr` targeting
  it, `set_toc` followed by a save, opening it for writing). Saving a repaired copy to a
  separate temp path (the pdfplumber 0-page repair, `convert.py` ~:1115) remains allowed.
- No `T2N_*` variable or CLI flag MAY re-enable the write-back.
- Documentation MUST NOT claim the converter adds bookmarks to source PDFs.
