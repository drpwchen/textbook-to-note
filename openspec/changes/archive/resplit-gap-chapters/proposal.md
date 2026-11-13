# resplit-gap-chapters

Tier 1. Maintainer standing instruction (2026-11-09): drive to the output contract + validator,
no per-spec approval round. Requested by the maintainer 2026-11-14 (bare-numbered cross-level
chapter titles).

## Why

0.8.0 moves a `Chapter N` outline entry to the chapter level wherever it sits, but a
bare-numbered title (`3 Paralysis and Weakness`) is left alone: section numbers look the same.
Ropper (Adams & Victor's Neurology 12e) has chapters 1–2 and 27–49 at outline level 2 and
chapters 3–26 one Section deeper at level 3. Today it splits correctly only because
`_split_config.json` carries `split_levels: 3` plus 24 hand-written `chapter_number_overrides`.
Any new book of that shape needs the same hand work.

## What changes

A bare-numbered outline entry at another level becomes a chapter only when it fills a gap in
the chapter-level numbering exactly: between two consecutive numbered chapter-level entries
`a < b`, one other level holds numbered entries that are exactly `a+1 … b-1`, in outline order,
on non-decreasing pages between those of `a` and `b`. Those entries and their subtrees move to
the chapter level, the same way `Chapter N` entries do.

## Not doing

- A gap before the first or after the last chapter-level number (no upper bound to check
  against — subsection numbers could match).
- A gap filled by entries from more than one level, or only partly filled.
- Books with `chapter_number_overrides` (unchanged: never promoted), or `promote_chapters: false`.
- Editing the maintainer's `_split_config.json` (Ropper's entry is measured with and without its
  overrides; dropping them is a data change outside this repo).

## Risk

Default-ON in `--resplit`. A wrong promotion splits a chapter at a numbered subsection. Guard:
the run must equal the missing numbers exactly, which ordinary subsection numbering (1, 2, 3 …
restarting under each chapter) does not. Gate: full-corpus resplit before/after; every changed
book must be a correction.

## Affected specs

- `openspec/specs/conversion/spec.md` — added requirement; one negative constraint of "A
  `Chapter N` outline entry is a chapter at whatever level it sits" modified.
