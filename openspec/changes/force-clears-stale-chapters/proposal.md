# force-clears-stale-chapters

Tier 1 — approved by the maintainer 2026-11-09.

## Why

`batch_dir_convert()` re-converts a book into the same `OUTPUT_BASE/<pdf stem>/` when `--force`
is given, or when the PDF is newer than `full_text.md` (`convert.py:2311-2314`). The split then
writes new chapter files over old ones by name only. Any old `ch*.md` / `pages_*.md` whose name
the new split does not produce stays on disk, and the indexer keeps serving a chapter that no
longer exists (RAG review 2026-11-05, R1-a). This becomes certain, not occasional, once
`resplit-same-page-bookmarks` stops writing empty files: every old empty file would survive a
re-conversion.

Measurement (`_scratch/rag-review-2026-11-05/phase2-baseline.md`, 2026-11-09): 10 books hold
24 files that duplicate a chapter number in the same directory (Furman, Ross 4 each; 8 books 2
each) — the signature of leftovers from an earlier conversion. The larger exposure is
prospective: the 5,046 empty files `resplit-same-page-bookmarks` stops writing would all survive
a re-conversion without this change.

## What changes

- Before the chapter split writes anything, the book's previously generated split files are
  removed — only files whose names match the converter's own split naming, only directly in that
  book's output directory.
- The removal happens after `full_text.md` was rewritten successfully, so a failed conversion
  leaves the old chapter files in place.
- Applies to every re-conversion in `--batch-dir`, not only `--force` (the mtime path has the same
  bug) — approved.
- Kill switch `T2N_CLEAR_STALE_CHAPTERS=0`. (name approved).

## Not doing

- Not touching `--batch` (`books.json`) or single-file mode: neither writes chapter files.
- Not removing anything in subdirectories, `full_text.md`, figures, `_manifest.json`,
  `_chapter_index*.json`, or any file not matching the patterns below.
- Not cleaning books that are not being re-converted in this run.

## Risk

Default-ON deletion inside the user's output tree. Files are deleted directly (`os.remove`) —
they are superseded artifacts of the re-conversion, regenerable from `full_text.md` (maintainer
decision 2026-11-09: no holding folder, no recycle bin). Containment is by exact filename regex +
non-recursive listing + the book directory being the one this run just wrote `full_text.md`
into. A user who hand-edited a `chNN_*.md` loses that edit on re-conversion — which already
happens today for any file whose name is reproduced, so the new loss is limited to files the new
split no longer produces.

## Affected specs

- `openspec/specs/conversion/spec.md` — added (one requirement)

## Decisions (2026-11-09)

1. **Yes**: also clear on the mtime-triggered re-conversion, not only `--force`.
2. **Included here**: the EPUB splitter (`convert_epub` → `split_epub_by_headings`) uses the same `chNN_` naming and gets the same clear.
3. **Names in the run log, one line per book** (deletion is direct and unrecoverable).
