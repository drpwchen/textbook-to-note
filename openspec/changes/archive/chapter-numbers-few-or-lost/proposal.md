# chapter-numbers-few-or-lost

Tier 1 (resplit / validator; no OCR routing, table gating or figure QC).

## Why

1. A PDF with one or two chapters (an excerpt, the quickstart's OpenStax chapter 1) gets no
   chapter level: `choose_chapter_level()` needs 3 numbered titles. Its `Chapter 1` entry then
   maps to no file and the validator fails `chapter_unmapped` unless a split config names the
   level by hand.
2. Hopkins (2026-11-13): a letter-suffix `chapter_regex` read `8a`/`8b` as 8, 8 — "not
   increasing" — and every chapter number of the book was dropped. The validator passed it,
   because with no numbers none of the number checks has anything to look at.

## What changes

- A level whose numbered titles are all written `Chapter N` and increase is the chapter level
  even with 1–2 of them, when nothing else qualifies and no level goes backwards.
- The validator fails `chapter_numbers_missing` when no file is numbered although a chapter level
  was chosen, or the book's config set `chapter_regex` / `chapter_level` (resplit records that in
  the manifest as `chapter_numbers_expected`, only when it happens).
- The sample's `_split_config.json` keeps only the `Glossary` → `Key Terms` retitle; the
  quickstart's output is unchanged, its explanation now names the real reason.

## Rule chosen, and the ones rejected

The evidence that a book *should* be numbered: the split chose a chapter level, or the
maintainer told it how chapters are numbered (config). Outline titles reading `Chapter N` are
already covered by `chapter_unmapped`, and a TOC count by `toc_count`.
Rejected: "a `numbers not increasing` note and no numbers" — on the production corpus that is
true of 31 passing books (carried / pattern / per-chapter-PDF books whose file-name or heading
numbers restart, e.g. per part); failing them would flag books whose output is not wrong.

## Not doing

- No change to how numbers are parsed, to `numbers_if_increasing()`, or to any threshold.
- No change for books with ≥ 3 numbered titles at some level.

## Risk

Default-ON in both parts; measured below (tasks.md): no output byte and no verdict changed on the
real corpus.

## Affected specs

- `openspec/specs/conversion/spec.md` — 2 added requirements; the validator's check list gains
  `chapter_numbers_missing`.
