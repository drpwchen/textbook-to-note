# resplit-cross-level-chapters

Tier 1. Maintainer standing instruction (2026-11-09): drive to the output contract + validator,
no per-spec approval round.

## Why

Measured on the v0.8.0 corpus (`textbook-md-v2`, 2026-11-13): validator 283/284 pass, but 5
books that pass have outline entries titled `Chapter N` whose number no file carries:

| Book | What happens today |
|---|---|
| Magee Athletic & Sport Issues | Chapters 26–30 sit one Section deeper (level 4, others level 2): all five chapters, p650–end, are inside the file of chapter 25 |
| Tecklin Pediatric PT 5e | Chapter 1 is level 1 (others level 2): its sections become six unnumbered files marked `front` |
| Nordin Basic Biomechanics 4e | Same as Tecklin: Chapter 1 is `front`, unnumbered |
| Quinn Documentation 2e | 16 `Chapter N:` entries at level 2, but Appendix C repeats `Chapter 1…14` (answers) → numbers not increasing → no chapter level → 0 numbered files |
| Tarbox Early Intervention ASD | Chapters are `Chapter-1` at level 3 (title one level deeper); split at level 2 → 2 body files of 133 and 502 pages |

The validator cannot see any of this: it checks the numbers that were assigned, never the
chapters the outline says exist. Three more books (Seffinger 26, Ropper 24, Rinaldi 9 entries)
carry hand-written `chapter_number_overrides` for the same cross-level shape.

## What changes

1. An outline entry titled `Chapter N` (keyword form) at a level other than the book's chapter
   level is treated as a chapter: it and its sub-entries move to the chapter level for splitting.
2. The validator fails a book with `chapter_unmapped` when an outline entry titled `Chapter N`
   names a number no file carries.
3. Quinn and Tarbox get `_split_config.json` entries (data, not code).

## Not doing

- Same-page chapter-title entries (ACSM, Rinaldi 24, Seffinger 41/47–49): all are fixed by
  existing config; measured residual on the corpus = 0 books. No code change.
- Numbered-only titles (`7 Gait`) at another level: too ambiguous (section numbers look the same).
- Merged-PDF outlines (`26.pdf (p.321-325)`, DeLee): one book, handled by config.
- Removing existing `chapter_number_overrides` from the config: measured, not edited, in this change.

## Risk

Default-ON for `--resplit`. Wrong promotion would split a chapter at an in-text cross reference
bookmarked as `Chapter N` — guarded by: only when a chapter level exists, only numbers no
chapter-level entry already carries. Corpus measurement before/after over all books is the gate:
every changed book must be a correction; `promote_chapters: false` per book is the off switch.

## Affected specs

- `openspec/specs/conversion/spec.md` — added requirement; validator requirement modified.
