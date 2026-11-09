# conversion — PDF/EPUB → markdown

## Purpose

Turn a book the user owns into greppable markdown with page markers, preserving reading
order and detecting the cases where extraction *looks* fine but silently lost content.
Entry point: `converter/convert.py`. Target cost: zero LLM tokens.

## Requirements

### Requirement: Local text extraction is the default path

The system SHALL extract text from born-digital PDFs using PyMuPDF locally, without calling
any network service or language model.

#### Scenario: Born-digital PDF converts offline
- **GIVEN** a PDF with a usable native text layer
- **WHEN** `converter/convert.py` runs against it with no OCR environment configured
- **THEN** markdown is written under `OUTPUT_DIR` and no network request is made

#### Scenario: CPU-only machine is a supported end state
- **GIVEN** a machine with no GPU, no ollama, and no Surya venv
- **WHEN** conversion runs on a born-digital PDF
- **THEN** conversion completes and the absence of a GPU is not reported as an error

#### Scenario: Source PDFs are never modified
- **GIVEN** any conversion run
- **WHEN** the run finishes, whether it succeeded or failed
- **THEN** the source file under `BOOKS_DIR` is byte-identical to before the run

**Negative constraints**
- The converter MUST NOT write to, move, or delete anything inside `BOOKS_DIR` other than
  its own bookkeeping files (`books.json`, `already_converted.json`).
- The converter MUST NOT send page images or page text to a remote API on the default path.

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

### Requirement: Re-conversion removes the book's own stale split files before splitting

When `--batch-dir` re-converts a PDF or EPUB whose output directory already exists (`--force` or
the source being newer than `full_text.md`), the system SHALL,
after `full_text.md` has been written successfully and before the chapter split writes, remove
from that book's output directory every file whose name matches the converter's split naming:

- `^ch\d{2,}[a-z]?(_[A-Za-z0-9_]*)?\.md$` (bookmark / pattern / EPUB split, incl.
  `ch00_Front_Matter.md`; the optional letter covers EPUB `Chapter 12a` → `ch12a_*.md`)
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

### Requirement: Silent extraction failure is detected, not trusted

The system SHALL score each page for extraction failure that leaves plausible-looking text,
and route affected books to the OCR ladder rather than emitting the damaged text as if it
were correct.

Detection signals are per page: glyph-garble ratio, character density, font risk
(CID/Identity-H, PUA codepoints), and domain-pattern miss.

#### Scenario: Book with broken font encoding is routed to OCR
- **GIVEN** a PDF whose text layer decodes to garbled glyphs
- **WHEN** the book is converted during a `--batch-dir` run
- **THEN** the book is routed to the OCR ladder rather than written out as extracted text

#### Scenario: Sparse page count triggers the density check
- **GIVEN** a book where more than `OCR_EMPTY_PAGE_MAX` (0.35) of pages carry fewer than
  `OCR_MIN_CHARS_PER_PAGE` (200) characters
- **WHEN** the check runs
- **THEN** the book is flagged as an OCR candidate

#### Scenario: OCR routing decision is per book, not per page
- **GIVEN** a book where only some pages trip the detection signals
- **WHEN** the routing decision is made during a `--batch-dir` run
- **THEN** the **entire file** goes to OCR, not the tripped pages alone

**Negative constraints**
- The pipeline MUST NOT emit a book that failed detection as if extraction succeeded.
- Documentation MUST NOT describe the ladder as mixing engines page-by-page inside one
  book. Per-page routing is unimplemented; claiming it is a public-claim violation.

### Requirement: OCR is a ladder with the frontier model last

The system SHALL attempt OCR engines in a fixed order of increasing cost: Surya →
PaddleOCR-VL → local vision model → frontier vision as a last resort, and SHALL treat every
engine below the first as optional.

#### Scenario: No OCR engine is configured
- **GIVEN** `SURYA_VENV_PY` and `SURYA_ADAPTER` are unset
- **WHEN** a book trips the silent-failure check
- **THEN** the step is skipped with a reported reason and the run does not crash

#### Scenario: A different engine is plugged in
- **GIVEN** a script conforming to the adapter interface in `docs/surya-adapter.md`
- **WHEN** `SURYA_ADAPTER` points at it
- **THEN** it is used for that ladder rung with no change to `converter/convert.py`

**Negative constraints**
- The system MUST NOT use tesseract at any rung.
- Frontier vision MUST NOT be reached without the lower rungs having been attempted or
  explicitly unavailable.

### Requirement: Reading order survives multi-column layout

The system SHALL reconstruct true reading order on multi-column pages by clustering lines
into columns, and SHALL leave single-column pages byte-identical to trivial extraction.

A column boundary requires a line to clear the gutter by `COLUMN_TOL_FRAC` (0.05) of page
width, and each column requires at least `COLUMN_MIN_LINES` (2) lines before it is trusted.

#### Scenario: Two-column page is not interleaved
- **GIVEN** a two-column textbook page
- **WHEN** the page is extracted with column sort enabled (the default)
- **THEN** the left column's text appears in full before the right column's

#### Scenario: Exact fallback on single-column pages
- **GIVEN** a single-column page
- **WHEN** the page is extracted with column sort enabled
- **THEN** the output is byte-identical to the output with `T2N_COLUMN_SORT=0`

**Negative constraints**
- A garble check that inspects characters alone MUST NOT be treated as evidence that
  reading order is correct — interleaved columns pass any such check.

### Requirement: Every behaviour-changing flag has an off switch and a documented default

The system SHALL expose each optional conversion behaviour as a `T2N_*` environment
variable, and SHALL default to ON only for behaviours that correct demonstrably wrong
output.

#### Scenario: Correction behaviours default ON
- **GIVEN** no environment variables are set
- **WHEN** conversion runs
- **THEN** page-frame pseudo-table rejection, spanned-header collapse, the table gate,
  column sort, and the whole-book table check are all active

#### Scenario: Additive behaviours default OFF
- **GIVEN** no environment variables are set
- **WHEN** conversion runs
- **THEN** cross-page table merge (`T2N_TABLE_MERGE`) and the out-of-band review queue
  (`T2N_REVIEW_QUEUE`) are inactive

#### Scenario: Disabling a default-ON behaviour restores prior output
- **GIVEN** a behaviour documented as "restores byte-identical output when disabled"
- **WHEN** its flag is set to `0`
- **THEN** the markdown matches the output produced before that behaviour existed

**Negative constraints**
- A new default-ON behaviour MUST NOT be added without a measurement over the real corpus
  showing it corrects wrong output; "seems better" is not sufficient.
