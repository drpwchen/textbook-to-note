# private-upport

Tier 2. Part of it changes what `--batch-dir` does with a PDF that needs OCR (OCR routing).
Written 2026-11-13 under the maintainer's outcome-based mode; the independent verifier pass
is the acceptance step.

## Why

The maintainer converts with a private copy of `converter/` that has drifted from this repo
(SYNC-LEDGER X1–X11, P6). Three of its behaviours are fixes this repo still lacks, and one is
a pipeline this repo cannot express:

1. **A scanned book skipped forever.** When a PDF needs OCR and Surya is not installed (or
   its adapter moved), `--batch-dir` prints `skipped (Surya unavailable)`, marks the book
   *completed*, and exits 0. Once Surya is installed, every later run skips the book again.
   This happened on the private side in 2026-08 (an adapter path moved): every scanned book
   in a batch was silently dropped.
2. **Off-center gutters on OCR pages.** OCR pages are column-sorted at the page center. A
   boxed course-manual layout whose gutter sits at 38% of the width puts every right-column
   line across the center, so they become "full-width" lines and interleave with the left
   column again (two measured pages; not a corpus measurement — so opt-in here).
3. **Warm Docling worker per run.** With `T2N_DOCLING=1`, `--batch-dir` / `--batch` build a
   new Docling worker per book (4–12 s model load each).
4. **Post-conversion chain.** The private `post_convert.py` re-splits new and re-converted
   books into the corpus, links their figure folders, indexes and backfills. This repo's
   `post_convert.py` has only the 0.8.1 hand-off.

## What changes

- `--batch-dir`: a book that needs OCR while Surya is unavailable is **FAILED**: a `[FAIL]`
  line naming the missing path and env var, left out of `completed` (the next run retries
  it), and the run exits 1.
- `T2N_OCR_GUTTER=1` (default off; unset = byte-identical) splits OCR pages at a measured
  gutter.
- With `T2N_DOCLING=1`, one Docling worker (and one GPU lease) serves the whole batch run.
  Output is unchanged.
- The batch log line also reports Docling / review-queue / sideways / reliability counts.
- `post_convert.py`: `--resplit-only`, `--index` (resplit → figure links → hand-off),
  `--backfill`; failures exit 1. `CORPUS_DIR` (default `./corpus`) and `T2N_PDF_ROOTS`
  (default `BOOKS_DIR`). `verify` and `INDEX.md` list each guideline under `_Guidelines/`.
- `--resplit` no longer copies batch bookkeeping (`batch_progress.json`,
  `conversion_summary.json`, `already_converted.json`) found inside a book folder.
- `pyproject.toml`: `pip install -e .` installs the repo with console scripts.

## Not doing

- No change to *which* PDFs go to Surya (`is_scan_only`, `detect_fitz_silent_failure`,
  `--force-surya`) or to any `T2N_TABLE_*` constant, gate or QC threshold.
- No support for the private adapter's `[x, y, w, h]` bboxes: the adapter contract
  (`docs/surya-adapter.md`) is `[x0, y0, x1, y1]`; a non-conforming adapter is wrapped by its
  owner.
- No import of vault-search internals; indexing and coverage audit stay in `corpus.py add`.
  `post_convert.py --audit` without `INDEXER_SCRIPT` stays a `[skip]`.
- No removal of `INDEXER_SCRIPT` (deprecated in 0.8.1).
- No change to `scan_fix_negatives.py` / `rename_books.py` / `review_queue.py` /
  `scan_table_pass.py` / `strip_fake_tables.py` / `triage_report.py`: the public copies are
  already the generic form of the private ones (diffed 2026-11-13).

## Risk

- A public user with a scanned PDF and no Surya now gets exit 1 on every `--batch-dir` run
  until Surya is installed or the PDF is moved out. That is the point; the message says what
  to set.
- `post_convert.py --index` now writes `CORPUS_DIR` (resplit) before handing off. It never
  writes `OUTPUT_DIR` or the PDFs (resplit's own guarantee).
- Figure folders are linked (junction on Windows, symlink elsewhere), not copied.

## Affected specs

- `openspec/specs/conversion/spec.md` — 4 added, 2 modified.
