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
