# split-manifest-contract

Tier 1. Supersedes the draft `resplit-real-chapter-numbers-and-manifest`. Approval mode: the
maintainer ruled on 2026-11-09 that Phase 2 is accepted by **output contract + automatic
validator**, not by line-by-line spec review; the drafts' open questions were decided by the
maintainer's dispatcher (Phase 2 brief, 2026-11-09) and are recorded below as decisions.

## Why

Split files are named `chNN_` where `NN` is a sequence over split points, not the printed
chapter number (baseline 2026-11-09: sequence = real number for 3 of 1,349 sampled bookmarks).
`<!-- page N -->` is the physical PDF page, never the printed page a citation needs. References
sections look like body text. And nothing checks a split: 5,046 of 22,461 chapter files in the
maintainer's corpus are empty and no tool noticed. The downstream indexer (obsidian-vault-search,
Phase 3) needs one machine-readable description per book, and the corpus needs a check that
fails loudly.

## What changes

1. Every book folder written by `--resplit` (change `resplit-cli`) carries `_manifest.json`,
   schema 1 (full schema in `converter/split_manifest.py`'s docstring):
   `schema, book, source_pdf, split_method, page_labels, page_confidence, files[], sections[]`
   plus the added fields `page_label_sets, toc_chapters, chapter_level, generator, notes`, and per
   file `level, source_pdf, page_confidence`. Cross-repo contract: fields may be **added** later,
   never renamed or re-defined.
2. `python converter/split_manifest.py --validate <corpus> [--book …] [--json …]` checks every
   book against its manifest (exit 0 all pass / 1 any fail / 2 bad arguments).

Decisions (were open questions in the draft):
- Chapter numbers: `^(chapter|chap.|ch.) N`, `^N` followed by `. : ) _ - – —` or a space and a
  non-digit (never `12.3`), and `chapter <word number>`. The chapter level is the outline level
  whose numbers strictly increase and are most numerous (≥3; ties → shallower level). Below it a
  file inherits the number; above it (Parts) it is null. Per-book config may override
  (`chapter_level`, `chapter_regex`, `chapter_numbers: false`).
- `kind`: `references` (References, Bibliography, Suggested/Further Reading(s), Literature Cited…),
  `front` (Cover, Copyright, Contents, Preface…, Foreword, Acknowledgments, Contributors…),
  `back` (Index, Back Matter, Color Plates), else `body`. Tagged in the manifest only; markdown
  text is never changed.
- `source_pdf`: file name only, no path and no hash.
- Book with several PDFs (one PDF per chapter): top-level `source_pdf` / `page_labels` null; each
  file names its `source_pdf`; labels per PDF in `page_label_sets`.
- `toc_chapters` = the number of distinct chapter numbers on the chosen outline level (bookmark
  books), or a hand-written value in the per-book config (pattern / carried books).

## Not doing

- Not renaming chapter files to real numbers (`chNN` stays the sequence; #15's `seq` strategy and
  `_chapter_index.chapters.json` keep working).
- Not teaching `citations/textbook_chapter_index.py` to read manifests.
- Not writing a manifest from the normal conversion path (`--batch-dir`, single file) — only
  `--resplit` writes one in this change.
- Not deriving printed pages from offsets, running headers or OCR.

## Risk

New file and new command only; conversion output is unchanged. The risk is a confidently wrong
`chapter_number` — hence null rather than a guess, and the validator's order / gap / TOC-count
checks that make a wrong book fail instead of pass.

## Affected specs

- `openspec/specs/conversion/spec.md` — added (three requirements)
