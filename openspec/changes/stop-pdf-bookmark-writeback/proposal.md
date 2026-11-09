# stop-pdf-bookmark-writeback

Tier 1 — approved by the maintainer 2026-11-09; shipped in 0.7.4.

## Why

`add_pdf_bookmarks()` (`converter/convert.py:1970`) is called from both `--batch-dir` branches
(Surya at `:2357`, fitz at `:2386`) whenever a split returned chapters. If the source PDF has no
bookmarks, it writes the detected chapters into it as level-1 bookmarks and saves the **user's
source file** with `doc.save(pdf_path, incremental=True)`. That violates the existing
requirement "Local text extraction is the default path" → scenario "Source PDFs are never
modified" in `openspec/specs/conversion/spec.md`, and `skills/textbook-to-md/SKILL.md:92`
advertises it as a feature. It also poisons every later bookmark-based split: a re-split cannot
tell the publisher's bookmarks from the converter's earlier `detect_chapters()` guesses.

## What changes

- The write-back is removed entirely — no flag, no opt-in (maintainer decision 2026-11-09).
  `add_pdf_bookmarks()` and both call sites are deleted.
- Conversion output (`full_text.md`, chapter files) is unchanged.
- `skills/textbook-to-md/SKILL.md` drops the "Writes PDF bookmarks to source PDFs" line.

## Not doing

- Not detecting, counting, or restoring PDFs that earlier versions already modified (open
  question 1).
- Not offering the write-back as an opt-in or as a separate export (e.g. a sidecar bookmarks file).
- Not changing how bookmarks are *read* for splitting.

## Ordering

Lands **before** every resplit change (`resplit-same-page-bookmarks`, `resplit-deep-bookmarks`,
`resplit-real-chapter-numbers-and-manifest`, `resplit-cli`).

## Risk

Removes a documented, user-visible side effect: a PDF without bookmarks no longer gains them in
the user's PDF reader after conversion. That is the intended correction — a converter must not
edit the user's books. Split output is byte-identical. Side effect: since the PDF's mtime no
longer changes after conversion, the `--batch-dir` "PDF newer than `full_text.md`" check stops
seeing freshly written PDFs as changed. No default-ON extraction behaviour changes, so no corpus
measurement is needed; this is a spec/code disagreement resolved in the spec's direction.

## Affected specs

- `openspec/specs/conversion/spec.md` — added (one requirement, making the existing "Source PDFs
  are never modified" scenario concrete for the split path)

## Decisions

1. **Decided 2026-11-09: out of scope here.** This change only stops new writes; already-modified PDFs get a separate read-only count first, then the maintainer decides on restoring. How to count / restore PDFs already modified. Idea, not decided: an incremental save
   appends an extra xref/trailer section, so a modified file has ≥2 `%%EOF` markers and its
   last revision's outline matches `detect_chapters()`-style titles (level 1 only, ≤ 80 chars);
   truncating at the previous `%%EOF` would restore the original bytes. Needs a read-only
   survey first; publisher PDFs can legitimately carry several revisions.
2. **Decided 2026-11-09:** CHANGELOG lists it under Fixed (it broke the spec's own promise).
