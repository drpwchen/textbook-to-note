# resplit-cli

Tier 1. Rewritten 2026-11-09 under the maintainer's outcome-based approval mode (see
`split-manifest-contract`); the draft's open questions are decided below.

## Why

The split fixes reach a book only when it is split again, and today that means full
re-conversion (`--batch-dir --force`: hours of extraction/OCR, overwriting the live corpus in
place). Splitting needs only `full_text.md` plus the PDF's outline, both already on disk.

## What changes

```
python converter/convert.py --resplit <corpus> --out <new dir> --pdf-root <dir> [--pdf-root <dir> …]
                            [--book NAME …] [--split-config FILE] [--pdf-map FILE] [--force]
```

Per book folder of `<corpus>`:
- Each **unit** — the book folder or a sub-folder holding `full_text.md` (books converted one
  chapter-PDF at a time) — is split again from `full_text.md` + its PDF's outline with the same
  splitter and the same `T2N_SPLIT_*` env semantics as conversion. `full_text.md` is copied
  byte-identical. The unit's old split files are not carried.
- Everything else under the book (other md, json, notes) is copied; folders named `figures*`,
  `images`, `_epub_src`, `__pycache__`, `*.bak` are not (text corpus only).
- A book with no `full_text.md` is **carried**: its md files are copied, files with no text
  outside page markers are dropped. If most of its files carry a `Source:` header naming a
  chapter PDF, it is `per_chapter_pdf` and each file gets its own PDF's labels and outline.
- The book is built in `<out>/.tmp-<book>/` and renamed to `<out>/<book>/` when complete, then
  validated; `_resplit_report.json` in `<out>` records each book (method, PDF found by, anchoring
  stats, validator result). A finished book = `<out>/<book>/_manifest.json` exists.
- Root files of the corpus are copied except generated bookkeeping (`_chapter_index.json`,
  `batch_progress.json`, `conversion_summary.json`, `INDEX.md`, `already_converted.json`).

Decisions (draft open questions):
1. PDF with < 3 outline entries, an outline written by converter ≤ 0.7.3, or a bookmark split
   that yields < 2 chapters → the same fallback as conversion (`detect_chapters` → `_force_split`).
2. `<out>/<book>/_manifest.json` exists → book skipped (`skipped (output exists)`) — the run is
   resumable; `--force` rebuilds it (old output folder deleted, it is regenerable).
3. PDF lookup, first hit wins: `--pdf-map` (`{"book" or "book/unit": path}`) → per-book config
   `pdf` → the `Source:` path in `full_text.md` when it exists under a `--pdf-root` → that file
   name under the roots → its stem (year suffix and punctuation ignored) → the folder name's
   stem. Two candidates → `ambiguous pdf`. No PDF → the unit's existing split is carried and the
   reason is listed in the report (the corpus stays complete).
4. `--resplit` turns no `T2N_SPLIT_*` flag on by itself.
5. The draft's "call `clear_stale_split_files`" is subsumed: the output folder is rebuilt whole
   in a temp folder, so no old split file can survive.

## Not doing

- No text extraction, OCR or table work; no EPUB re-split (EPUB books are carried).
- No in-place mode and no swap of `<out>` into the live corpus (manual step).
- No reconstruction of `full_text.md` from chapter files.
- No figure copying (figure folders stay in the source corpus; image links in v2 resolve once
  the figure folders are moved / junctioned at switch-over).

## Risk

Opt-in by construction (new flag). The source corpus and PDFs are fingerprinted (size, mtime,
sha256) before and after each book; any change fails that book.

## Affected specs

- `openspec/specs/conversion/spec.md` — added (one requirement)
