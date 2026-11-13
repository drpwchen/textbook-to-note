# Delta — conversion

## ADDED Requirements

### Requirement: An OCR'd book records its source PDF

`surya_ocr_pdf()` SHALL write `Source: `<pdf path>`` as the first line of `full_text.md`,
followed by a blank line, then the `<!-- page N -->` blocks as before — the same `Source:`
form the text-layer path writes. Routing (which PDFs go to OCR) and the OCR text are unchanged.

#### Scenario: OCR output
- **GIVEN** a PDF converted through the OCR path
- **THEN** line 1 of its `full_text.md` is exactly ``Source: `<the PDF path as passed>` ``
- **AND** every `<!-- page N -->` block is byte-identical to the previous release's

### Requirement: Library settings can live outside `BOOKS_DIR`

`books.json`, `already_converted.json`, `rename_skip.json` and `rename_fixes.json` SHALL be read
from `T2N_CONFIG_DIR` when set, else from `BOOKS_DIR` (the previous location).

A `books.json` value that is a list SHALL be an alias: `--batch <alias>` (exact, case-insensitive
match, checked before the substring match) converts every key it lists, in order; an alias
naming a key that is not a book is an error naming that key. `src` and `out` SHALL accept a
leading `~`.

#### Scenario: config dir set
- **GIVEN** `T2N_CONFIG_DIR=C` and `BOOKS_DIR=B`
- **THEN** the four files are read from `C`, none from `B`

#### Scenario: config dir unset
- **THEN** the four files are read from `BOOKS_DIR`, as before

#### Scenario: alias
- **GIVEN** `{"a_one": {...}, "a_two": {...}, "a": ["a_one", "a_two"]}`
- **WHEN** `--batch a` runs
- **THEN** the run converts `a_one` then `a_two`; `--batch one` still converts `a_one` alone

#### Scenario: broken alias (failure case)
- **GIVEN** `"x": ["a_one", "missing"]`
- **WHEN** `--batch x` runs
- **THEN** it stops with an error naming `missing` and converts nothing

### Requirement: PDF roots are walked only on demand

A `PdfIndex` SHALL NOT list any root at construction or in `combine()`. A root SHALL be walked
the first time a lookup needs the file-name / stem tables, and at most once per index. A unit
whose `Source:` PDF exists under a root SHALL resolve by path without any walk.

#### Scenario: every Source PDF present
- **GIVEN** a resplit of books whose `Source:` PDFs all exist
- **THEN** no PDF root is listed (`os.walk` is never called) and the output is unchanged

#### Scenario: a Source PDF gone
- **GIVEN** a unit whose `Source:` path no longer exists
- **THEN** each root is walked once, and the lookup finds the PDF by name as before

### Requirement: `T2N_REQUIRE_SOURCE_PDF=1` fails a book instead of searching for its PDF

With `T2N_REQUIRE_SOURCE_PDF=1` (default off), `post_convert.py`'s resplit SHALL refuse a planned
book when any of its `full_text.md` files has no `Source:` line, or names a PDF that does not
exist and that no single file of the same name under `T2N_PDF_ROOTS` replaces. Each such file
SHALL be printed as `[FAIL] resplit <book>: …` naming the file, the missing path when there is
one, and the fix (put the PDF back / copy it into `T2N_PDF_ROOTS` / edit the `Source:` line);
the book SHALL NOT be resplit (its corpus copy is left as it was) and counts as a failure
(exit 1).

#### Scenario: missing PDF (failure case)
- **GIVEN** `T2N_REQUIRE_SOURCE_PDF=1`, book `Gone` whose `Source:` PDF is missing, and no
  `Gone.pdf` under `T2N_PDF_ROOTS`
- **THEN** stdout carries `source PDF not found: <path>` and `Fix:`, `Gone` is not resplit,
  and the exit code is 1

#### Scenario: moved PDF found by name
- **GIVEN** the `Source:` PDF missing but a file of the same name under `T2N_PDF_ROOTS`
- **THEN** the book is resplit as before

#### Scenario: unset
- **GIVEN** `T2N_REQUIRE_SOURCE_PDF` unset
- **THEN** every planned book is resplit; a book with no PDF carries its existing split, as before

#### Negative constraints
- MUST NOT list any folder other than the `T2N_PDF_ROOTS` folders to make this decision, and
  those only when a `Source:` PDF is actually missing.

## MODIFIED Requirements

### Requirement: `post_convert.py` keeps the corpus in step with the converted output

`run_resplit()`: "Each root is walked at most once per run" becomes "Each root is walked at most
once per run, and only when a lookup needs it". Nothing else changes.
