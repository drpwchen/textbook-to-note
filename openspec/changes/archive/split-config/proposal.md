# split-config

Tier 1, new 2026-11-09 (outcome-based approval mode, see `split-manifest-contract`). Replaces
the draft idea of a separate "broken bookmarks" change.

## Why

Default rules split most books correctly, but some books' PDFs lie: every outline destination
resolves to page 1 (Lanken 2e, Zasler 2e — the two books whose whole text ended up in one
chapter file), publisher artefact entries (`Anchor 995`), index letters that anchor nowhere,
duplicate chapter files from two conversions. Each needs a book-specific decision. That decision
should be written down once, read by the converter, and cost 0 tokens on every later run.

## What changes

`--resplit` reads `<out>/_split_config.json` (or `--split-config FILE`):
`{"<book folder>": {…}}`, keys starting `_` ignored. Per book (and per unit under `"units":
{"<sub-folder>": {…}}`):

| key | effect |
|---|---|
| `pdf` | source PDF (absolute, or relative to a `--pdf-root`) |
| `method` | `"carry"` (keep existing split) / `"pattern"` (ignore the outline) |
| `anchor_titles` | outline pages are ignored; each split-level entry is placed at the line where its title occurs in `full_text.md` (see Requirement) |
| `body_start_page` | anchoring ignores matches before this PDF page |
| `page_offset` | integer added to every outline page |
| `split_levels` | outline levels that become files (default 2) |
| `drop_titles` | regex, or `{"re", "max_level", "after"}` — outline entries removed before splitting |
| `chapter_level`, `chapter_regex`, `chapter_numbers: false` | chapter-number overrides |
| `toc_chapters` | hand-counted chapters in the printed contents (pattern / carried books) |
| `missing_chapters_ok` | chapter numbers known to be absent from the source |
| `exclude_files` | book-relative md files not carried (duplicates) |
| `reason` | why the entry exists — copied into the manifest's `notes` |

The file is written by a person or by an LLM that has read the book; the converter never
writes it. A book without an entry is split by the default rules.

## Not doing

- Not read by `--batch-dir` / single-file conversion in this change.
- Not merged into `_chapter_index.chapters.json`: that file is a human chapter table, any book
  listed there becomes `strategy: "pinned"`, and the note-workflow spec forbids code writing it.
- No automatic detection of broken outlines (the validator flags them; a config entry fixes them).

## Risk

Only books with an entry change. Anchoring places a split at the page of a title match, so a
wrong match moves a chapter boundary — the validator's title / order / count checks run on the
result, and an unmatched entry is left unplaced rather than guessed.

## Affected specs

- `openspec/specs/conversion/spec.md` — added (one requirement)
