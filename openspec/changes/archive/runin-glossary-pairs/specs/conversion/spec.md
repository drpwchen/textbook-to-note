# Delta — conversion

## ADDED Requirements

### Requirement: Run-in glossary entries can be rejoined on the text-layer path (opt-in)

With `T2N_RUNIN_PAIRS=1`, a born-digital (fitz text-layer) page that column sort declined SHALL
have each stream-separated run-in term/definition pair emitted as one paragraph,
`**<term>** <definition>`, with entries separated by a blank line. With `T2N_RUNIN_PAIRS` unset
or not `1`, output SHALL be byte-identical to 0.8.1.

A pair is two upright lines on one baseline (within `RUNIN_BASELINE_TOL` = 1.5 pt): the term,
every inked span bold (font flag bit 4, or `bold|black|heavy|semibold|demi` in the font name),
and the definition, whose first inked span is not bold, starting between −1 pt and
`RUNIN_MAX_GAP_FRAC` (0.04) of the page width right of the term's end. A pair is rejected when
another line starts within that same gap right of the definition (a third cell on the row), or
when two terms claim the same definition. A pair is stream-separated when the definition is not
the line immediately after the term in content-stream order. A page is rewritten only when it
holds at least `RUNIN_MIN_PAIRS` (3) stream-separated pairs.

The definition carries its first line plus the following lines of its fitz block, up to the next
term or definition line. Every other line keeps content-stream order.

#### Scenario: terms-then-definitions stream (OpenStax Key Terms)
- **GIVEN** the bundled sample `OpenStax_Anatomy-and-Physiology-Ch1_1e_2013.pdf`, whose Key Terms
  pages 28–30 store all terms before all definitions, `T2N_RUNIN_PAIRS=1`
- **WHEN** it is converted
- **THEN** pages 28–30 hold 70 entries in the form `**<term>** <definition>`, e.g.
  `**anatomy** science that studies the form and composition`, and no other page changes

#### Scenario: a multi-line definition
- **GIVEN** a definition that wraps to a hanging indent within its fitz block
- **THEN** the wrapped lines are inside the entry's paragraph, before the next blank line

#### Scenario: stream already in reading order (failure case: nothing to fix)
- **GIVEN** a glossary whose stream interleaves term, definition, term, definition
- **THEN** the page output is byte-identical to the flag unset

#### Scenario: fewer than three separated pairs (failure case)
- **GIVEN** a page with two stream-separated pairs
- **THEN** the page output is byte-identical to the flag unset

#### Scenario: figure panel letters beside their labels (failure case)
- **GIVEN** panel letters and labels in the same, non-bold weight, letters first in the stream
- **THEN** nothing is paired and the page is unchanged

#### Scenario: table rows (failure case)
- **GIVEN** rows `bold label | value | value` with 12 pt column gaps, stored column by column
- **THEN** nothing is paired and the page is unchanged

#### Scenario: rotated text on a rewritten page
- **GIVEN** a rewritten glossary page that also carries a rotated margin line
- **THEN** the rotated line is still in the page output

#### Scenario: off by default
- **GIVEN** `T2N_RUNIN_PAIRS` unset
- **THEN** every page's text equals 0.8.1's (`render_page_text()` of 0.8.1 and of this change
  agree on every page of the measured books)

#### Negative constraints
- MUST NOT run on pages that `column_sorted_text()` reordered (two-column pages keep the column
  sort's output exactly).
- MUST NOT change OCR routing, the OCR path, or anything in table extraction or table gating
  (`T2N_TABLE_*`, figure QC).
- MUST NOT drop or duplicate a line: every line of the page appears exactly once.
- MUST NOT become default-ON without a corpus measurement showing corrections only.
