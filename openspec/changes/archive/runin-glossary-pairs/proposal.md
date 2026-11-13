# runin-glossary-pairs

Tier 1 (text-layer reading order; no OCR routing, no table gating).

## Why

On born-digital glossary / Key Terms pages a term and its definition no longer line up: the
bundled OpenStax sample's pages 28–30 come out as one line of every term glued together, then
the definitions. The layout is not two columns and not a table — each entry is a run-in head
(bold term, definition on the same baseline, wrapped under a hanging indent) — but the PDF's
content stream holds all terms first and all definitions after. The definitions cross the page
center, so `column_order_boxes()` rightly declines the page, and the plain `get_text()` fallback
follows the stream. `clean_text()` then joins the lowercase terms into one line.

## What changes

`T2N_RUNIN_PAIRS=1` rejoins each stream-separated pair as `**term** definition`, one paragraph
per entry, on pages with at least three such pairs. Unset = byte-identical to 0.8.1.

## Not doing

- No change to the column sort, OCR routing, the OCR path, or table extraction / gating.
- No geometric re-sort of the whole page: tried, it glued the running header onto a definition
  continued from the previous page (they share the top baseline). Other lines keep stream order,
  so a "KEY TERMS" heading the stream puts after the terms stays there.
- No default-ON: the measurement (see `tasks.md`) found no regression, but in 24 real books
  (15,482 pages) it corrects one page; the pairing rule was also shaped on two pages of that
  same corpus. One page is not a corpus, so this ships opt-in like `T2N_OCR_GUTTER`.
- No pairing of a definition continued from the previous page with that page's last entry.

## Risk

A false pair moves a line and adds bold markup. Guarded by the bold-term / non-bold-definition
signature (the first, unguarded draft paired figure panel letters with their labels in
Lundy-Ekman and chart axis labels with a caption in Whittle — both rejected by it), the
third-cell table guard, and the three-pair minimum. Opt-in, so default output cannot regress.

## Affected specs

- `openspec/specs/conversion/spec.md` — added: "Run-in glossary entries can be rejoined on the
  text-layer path (opt-in)"
