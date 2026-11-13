# Tasks — private-upport

Source of the up-port: the maintainer's private converter copy, diffed file by file on
2026-11-13 (`--strip-trailing-cr`) against this repo at v0.8.1.

| file | private-only content | decision |
|---|---|---|
| `convert.py` | Surya-unavailable = FAILED (exit 1) | up-ported, default on (Tier 2) |
| | `find_ocr_gutter()` + `column_order_boxes(gutter_x=)` | up-ported, opt-in `T2N_OCR_GUTTER=1` |
| | `shared_table_worker()` (one warm Docling worker per run) | up-ported (lease `t2n_docling`) |
| | richer batch log line | up-ported |
| | `ocr_bbox_to_xyxy` (private adapter emits `[x,y,w,h]`) | not ported: adapter contract is xyxy; wrap the adapter |
| | hardcoded book table / library paths / venv defaults / `ALREADY_CONVERTED` / `SKIP_SUBDIRS` | not ported: `books.json`, `already_converted.json`, `CONVERTER_SKIP_FOLDERS`, `SURYA_*`, `DOCLING_VENV_PY` already cover them |
| | single-file output to `misc/`, no EPUB error handling | public is newer; kept |
| `post_convert.py` | resplit plan → resplit → figure links → v3 index → audit/backfill, exit 1 | up-ported; indexing/audit via `corpus.py add` |
| | direct import of the indexer for `--audit` | not ported (hand-off contract) |
| `scan_fix_negatives.py`, `rename_books.py` (+ `_v2`), `review_queue.py`, `scan_table_pass.py`, `strip_fake_tables.py`, `triage_report.py` | — | public is already the generic (or newer) form; nothing to port |

## Implementation

- [x] Surya unavailable → FAILED, not completed, exit 1  → Requirement: A book that needs OCR while Surya is unavailable fails the batch
- [x] `T2N_OCR_GUTTER=1` measured gutter  → Requirement: OCR pages can be split at a measured gutter (opt-in)
- [x] `shared_table_worker()` in `--batch-dir` and `--batch`  → Requirement: One Docling worker serves a whole batch run
- [x] `post_convert.py` resplit chain, `CORPUS_DIR`, `T2N_PDF_ROOTS`, exit codes  → Requirement: `post_convert.py` keeps the corpus in step
- [x] `listed_books()` for verify / `INDEX.md`  → Requirement: Guidelines are listed one by one
- [x] resplit skips nested bookkeeping  → MODIFIED `--resplit` requirement
- [x] `pyproject.toml` (`pip install -e .`, `t2n-convert`, `t2n-post-convert`, `t2n-split-validate`)
- [x] Docs: AGENTS.md Step 2/5, setup-details Step 3/4.5/5, surya-adapter.md, requirements.txt, READMEs, skill

## Verification

- [x] `converter/test_private_upport.py` 42/42, one check per scenario; mutation-checked (each
      of the five behaviours removed in turn → 35, 41, 38, 40, 41 of 42)
- [x] Full suite green (13 files)
- [x] Validator, private-old vs public-new on real PDFs (`--batch-dir`, then `--resplit-only`):
      byte-identical apart from intended differences (see CHANGELOG 0.8.1 / report)
- [x] `pip install -e .` in a throwaway venv: the three commands run
- [x] Independent verifier pass (Tier 2): ACCEPT WITH FIXES on 8a70e68; fixes applied —
      no shared worker under `T2N_TABLE_MERGE=1`; a disabled shared worker is replaced for the
      next book; each PDF root walked once per post_convert run (`PdfIndex.combine`);
      test_resplit / test_corpus_handoff / test_private_upport added to CI;
      ask-your-library wording in printed text, `CORPUS_FORMAT_URL` and doc placeholders

## Ship

- [x] `CHANGELOG.md` (folded into 0.8.1, unreleased)
- [x] Tag `v0.8.1` moved to the new commit (local only)
- [x] Archive
