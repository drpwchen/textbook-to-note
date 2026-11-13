# Delta — conversion

## ADDED Requirements

### Requirement: A book that needs OCR while Surya is unavailable fails the batch

In `--batch-dir`, when a PDF is routed to OCR (`SCAN`, `FITZ-FAIL` or `FORCE-SURYA`, decided
exactly as before) and the Surya rung cannot run (`SURYA_VENV_PY` or `SURYA_ADAPTER` unset or
not an existing file), the system SHALL treat that book as FAILED: print one line starting
`  [FAIL] [<reason>] <rel>: FAILED` that names each missing piece and its env var
(`SURYA_VENV_PY`, `SURYA_ADAPTER`), add that line to the progress file's `errors`, leave the
book out of `completed`, continue with the next PDF, and exit 1 at the end of the run.

#### Scenario: scanned PDF, no Surya
- **GIVEN** a PDF with no text layer and `SURYA_ADAPTER` pointing at a missing file
- **WHEN** `convert.py --batch-dir <dir>` runs
- **THEN** stdout contains `[FAIL]` and `SURYA_ADAPTER`, the progress file's `completed` does
  not contain the PDF, its `errors` contains a line with `FAILED` and the PDF's name, and the
  exit code is 1

#### Scenario: the next run retries
- **GIVEN** the run above
- **WHEN** `--batch-dir` runs again (without `--force`), still without Surya
- **THEN** the book is reported FAILED again (not skipped as completed)

#### Scenario: born-digital books in the same run (failure isolation)
- **GIVEN** the scanned PDF plus a born-digital PDF in the same directory
- **WHEN** `--batch-dir` runs without Surya
- **THEN** the born-digital book is converted and marked completed exactly as before; only the
  exit code and the scanned book's bookkeeping differ

#### Negative constraints
- MUST NOT change which PDFs are routed to OCR.
- MUST NOT write any file under `OUTPUT_DIR/<book>/` for the failed book, and MUST NOT run the
  stale-chapter clear for it.
- A run in which no book needs OCR, or Surya is available, MUST exit 0 as before.

### Requirement: OCR pages can be split at a measured gutter (opt-in)

With `T2N_OCR_GUTTER=1`, `order_ocr_blocks()` SHALL first look for a measured gutter
(`find_ocr_gutter()`) and, when one is found, split the page's OCR blocks there; when none is
found, or the split at it is not trusted, it SHALL use the page-center split as before. With
`T2N_OCR_GUTTER` unset or not `1`, OCR output SHALL be byte-identical to 0.8.0.

`find_ocr_gutter()` scans x from 0.25 to 0.75 of the page width in steps of 0.0025 of the width.
A candidate is eligible when at most 0.15 of the blocks straddle it. Eligible candidates rank
by (1) fewest straddling blocks narrower than 0.30 of the width, (2) most text characters on
the lighter side, (3) nearest the page center; the result is the midpoint of the best-ranked
contiguous run, rejected when either side holds fewer than `COLUMN_MIN_LINES` (2) blocks.

#### Scenario: off-center boxed layout
- **GIVEN** OCR blocks with a left column ending at 0.38 of the width, a right column of eight
  lines and a banner over the right column, `T2N_OCR_GUTTER=1`
- **WHEN** the page is ordered
- **THEN** the gutter found is within 0.02 of the width of 0.38, and every left-column line
  comes before every right-column line

#### Scenario: single-column page (failure case)
- **GIVEN** twenty prose blocks spanning most of the width, `T2N_OCR_GUTTER=1`
- **THEN** no gutter is found and the order is top-to-bottom

#### Scenario: off by default
- **GIVEN** the off-center blocks above and `T2N_OCR_GUTTER` unset
- **THEN** the order equals 0.8.0's (page-center split)

#### Negative constraints
- MUST NOT change the fitz text path: `column_order_boxes()` called without `gutter_x` splits
  at the page center exactly as before.
- `T2N_COLUMN_SORT=0` still disables all column sorting, gutter included.

### Requirement: One Docling worker serves a whole batch run

With `T2N_DOCLING=1`, `--batch-dir` and `--batch` SHALL create at most one Docling worker per
process and pass it to every `convert_pdf()` call; the GPU lease `t2n_docling` SHALL be held
from just before the worker is created until process exit. With `T2N_DOCLING` unset no worker
and no lease are created. Converted output SHALL be identical to building a worker per book.

#### Scenario: two books, Docling on
- **GIVEN** `T2N_DOCLING=1` and a batch of two PDFs
- **WHEN** the batch runs
- **THEN** the worker constructor is called once and both `convert_pdf()` calls receive it

#### Scenario: Docling off (failure case: nothing to share)
- **GIVEN** `T2N_DOCLING` unset
- **THEN** `convert_pdf()` receives `table_worker=None` and no lease is taken

#### Negative constraints
- A single-PDF conversion (`convert.py <pdf>`) MUST keep creating and closing its own worker.

### Requirement: `post_convert.py` keeps the corpus in step with the converted output

`post_convert.py` SHALL treat `CORPUS_DIR` (env; default `<repo>/corpus`; `--corpus DIR`
overrides) as the resplit target of `OUTPUT_DIR`:

- `resplit_plan()`: every top-level folder of `OUTPUT_DIR` holding a `.md` file (skipping
  `misc`, `scripts`, `figures_test`, `__pycache__`, `.drafts` and `.`-folders) is planned when
  `CORPUS_DIR/<book>/_manifest.json` is missing (plain), or older than the book's newest `.md`
  (forced rebuild). Up-to-date books are not planned.
- `run_resplit()`: each planned book is resplit with `--pdf-root` = the folder(s) of the PDFs
  named by its `full_text.md` `Source:` lines that exist, else the `T2N_PDF_ROOTS` folders
  (`os.pathsep`-separated; default `BOOKS_DIR`) that exist. A book whose manifest is still
  missing afterwards is a failure.
- `ensure_figure_links()`: every `figures*` / `images` folder of a book (names containing
  `.bak` or `undo` excluded) is linked into `CORPUS_DIR/<book>/` (a junction on Windows, a
  symlink elsewhere); an existing entry is left alone; a link that cannot be made is a failure.

Flags: `--resplit-only` runs plan + resplit + links. `--index` runs them and then the 0.8.1
hand-off on `CORPUS_DIR`; `--backfill` is the hand-off alone. The default (no flag) runs
verify, `INDEX.md`, the figure registry, then what `--index` runs. Any failure SHALL be listed
after `[FAIL]` and the process SHALL exit 1 (or the hand-off's own non-zero code, if that is
the only failure). With `INDEXER_SCRIPT` set, `--index` and the default skip the resplit
chain and behave as in 0.8.1.

#### Scenario: plan
- **GIVEN** books `New` (no corpus manifest), `Reconverted` (md newer than its manifest),
  `Current` (manifest newer), `scripts`, and an empty folder
- **THEN** `resplit_plan()` returns `[("New", False), ("Reconverted", True)]`

#### Scenario: pdf roots
- **GIVEN** `full_text.md` whose `Source:` names an existing PDF in `pdfs/sub/`
- **THEN** the roots are `[pdfs/sub]`; with the PDF gone, they are the existing `T2N_PDF_ROOTS`

#### Scenario: figure links
- **GIVEN** `figures`, `figures_Part_I`, `figures.old.bak`, `figures.x_undo` in `OUTPUT_DIR/Book`
  and `CORPUS_DIR/Book` existing
- **THEN** exactly `figures` and `figures_Part_I` appear in `CORPUS_DIR/Book`, a file inside
  `figures` is readable through the link, a second run creates nothing and fails nothing, and
  no link is made for a book absent from `CORPUS_DIR`

#### Scenario: a resplit that leaves no manifest (failure case)
- **GIVEN** a planned book whose resplit errors
- **WHEN** `post_convert.py --resplit-only` runs
- **THEN** stdout lists `resplit: <book>` after `[FAIL]` and the exit code is 1

#### Negative constraints
- MUST NOT write under `OUTPUT_DIR` other than `INDEX.md` (the resplit guarantee applies).
- MUST NOT copy figure files into `CORPUS_DIR`.
- MUST NOT import any vault-search module.

### Requirement: Guidelines are listed one by one in verification and INDEX.md

`verify_conversion()` and `update_index_md()` SHALL list each `OUTPUT_DIR/_Guidelines/<Name>/`
as its own entry named `_Guidelines/<Name>`, and SHALL NOT list `_Guidelines` itself or any
other top-level folder whose name starts with `_` or `.`.

#### Scenario: two guidelines and a book
- **GIVEN** `Book/`, `_Guidelines/G1/`, `_Guidelines/G2/` (each with `.md`), and `_figure_remap/`
- **THEN** `INDEX.md` has rows `Book`, `_Guidelines/G1`, `_Guidelines/G2` and no other

#### Negative constraints
- A book folder whose name does not start with `_` MUST be listed exactly as in 0.8.0.

## MODIFIED Requirements

### Requirement: `--resplit` re-splits an existing corpus into a new directory without touching the source

(Unchanged except the added scenario and negative constraint below.)

#### Scenario: bookkeeping inside a book folder is not copied
- **GIVEN** `<corpus>/_Guidelines/batch_progress.json` (left there by a `--batch-dir` run with
  `OUTPUT_DIR=<corpus>/_Guidelines`)
- **WHEN** `--resplit` runs
- **THEN** `<out>/_Guidelines/batch_progress.json` does not exist; every other file is as before

#### Negative constraints (added)
- MUST NOT copy a file named `batch_progress.json`, `conversion_summary.json` or
  `already_converted.json` at any depth.

### Requirement: `post_convert.py --index` hands the corpus to vault-search and never indexes itself

(Unchanged except: the hand-off runs on `CORPUS_DIR` after the resplit chain above; the
`--corpus DIR` option sets `CORPUS_DIR`.)
