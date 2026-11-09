# Tasks — stop-pdf-bookmark-writeback

## Implementation

- [x] Delete `add_pdf_bookmarks()` and its calls at `convert.py:2357` and `:2386`
      → Requirement: Chapter detection never writes to the input PDF
- [x] Remove "Writes PDF bookmarks to source PDFs if none exist" from
      `skills/textbook-to-md/SKILL.md` → same Requirement (documentation constraint)
- [x] `grep` the repo for `.save(` / `saveIncr` / `set_toc` on input PDFs → none remain

## Verification

- [x] Test for each Scenario (synthetic bookmark-less PDF; sha256/size/mtime snapshot)
- [x] Byte-identical split output vs. v0.7.3 on ≥3 real books (run on copies of the PDFs)
      — Kelley 002 / 045 chapter PDFs, Braddom 7e Ch02: identical except the `Source:` path line
- [x] No `T2N_*` flag involved

## Ship

- [x] `CHANGELOG.md` entry
- [x] Version bump + tag
- [x] Archive
