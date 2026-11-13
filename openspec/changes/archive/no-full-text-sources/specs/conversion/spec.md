# Delta — conversion

## MODIFIED Requirements

### Requirement: `post_convert.py` keeps the corpus in step with the converted output

`run_resplit()` roots: "the folder(s) of the PDFs named by its `full_text.md` `Source:` lines that
exist" gains "(a book with no `full_text.md`: by its chapter files' `Source:` lines)".

#### Scenario: pdf roots of a book with no full_text.md
- **GIVEN** a book holding only chapter files, its `ch00_Front_Matter.md` carrying
  `Source:` → an existing `pdfs/sub/Book.pdf`
- **THEN** the roots are `[pdfs/sub]`

### Requirement: `T2N_REQUIRE_SOURCE_PDF=1` fails a book instead of searching for its PDF

#### Scenario: a book with no full_text.md and no findable PDF (failure case)
- **GIVEN** `T2N_REQUIRE_SOURCE_PDF=1` and a book of chapter files only, none with a `Source:`
  line naming an existing PDF (or found by name under `T2N_PDF_ROOTS`), and no `pdf` entry for
  it in `<corpus>/_split_config.json`
- **THEN** stdout carries `[FAIL] resplit <book>: <book> has no full_text.md …` with
  `Fix: add "pdf": "<path to the book's PDF>" to the "<book>" entry of <corpus>/_split_config.json`
  and the `--pdf-map` alternative; the book is not resplit; exit 1
- **AND** with a resolvable `pdf` entry, or every chapter `Source:` resolvable, it is resplit
