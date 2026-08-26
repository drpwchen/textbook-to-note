# geometric-continuation-review-flag

## Why

Reported in #14 against Morgan & Mikhail's Clinical Anesthesiology 7e with `T2N_REVIEW_QUEUE=1`:
the ASA Physical Status table breaks across pages 477→478 and comes out with every class label
one row away from its definition. The grid is well-formed — two consistent columns, no ragged
rows, no empty first cell — so it reads as clean citable data. Nothing flagged it.

The report attributes this to the review queue requiring continuation **and** dosage content.
That is not what the code does: `review_queue.py:review_reasons()` appends a reason for either
trigger independently, and both call sites in `convert.py` (lines 1244 and 1300) flag on a
non-empty reason list. `docs/table-review.md:69` ("the danger concentrates exactly where both
triggers meet") is where that reading comes from, and it is misleading.

The actual gap is narrower and worse: **continuation is detected only from a textual marker.**
`page_has_continuation_marker()` looks for "(continued)" / ", Continued" / "cont'd" in the page
text, and the only other signal, `stitched_continuation`, is passed exclusively from the merge
path, which requires `T2N_TABLE_MERGE=1` (default OFF). A textbook that simply runs a table past
a page break without printing a continuation caption — which is most of them — is invisible to
the queue no matter what its content is. The dosage trigger then covers the subset that happens
to contain `mg`/`mL` tokens, and a categorical clinical table (ASA class, NYHA class, Mallampati)
is covered by nothing.

`convert.py` already contains the right predicate: `_is_continuation()` decides, from geometry
alone, whether a table at the top of page N+1 continues one at the bottom of page N. It is only
ever consulted when the merge feature is on.

## What changes

With `T2N_REVIEW_QUEUE=1`, a table is flagged as a continuation when the page geometry says so —
a table ending within `TABLE_MERGE_BOTTOM_FRAC` of the bottom of the previous page and a table
starting within `TABLE_MERGE_TOP_FRAC` of the top of this one, with matching column count and
column x-edges — regardless of whether a "(continued)" caption exists and regardless of
`T2N_TABLE_MERGE`. The ASA case then carries the existing review marker.

The queue stays flag-only: no rejection, no repair, no change to table content, and the reason
string names geometry so a reviewer can tell it from the caption-driven one.

`docs/table-review.md` is corrected to say the triggers are independent (either one flags), and
to state that continuation is now geometric.

## Not doing

- Not flagging every table. The queue's volume bound is deliberate; this widens the continuation
  trigger to what it always meant, and does not remove it.
- Not detecting the row-shift itself. Whether a value is bound to the right row is semantic;
  `openspec/specs/table-extraction/spec.md` already forbids claiming the structural gate can see
  it, and forbids auto-repair. This change only widens *which* tables enter the human/model
  review pass.
- Not touching `T2N_TABLE_MERGE`, its constants, or the merge decision. The geometry predicate is
  read, never re-tuned, and no table is stitched that is not stitched today.
- Not changing the QC gate (`docling_tables.py`), `figures/figure_qc_gate.py`, or any
  `T2N_TABLE_*` threshold.
- Not enabling the review queue by default.
- Not extending the geometric signal to the Docling table rung (`T2N_DOCLING=1`, default OFF).
  Docling returns tables without the pdfplumber geometry this reads, so a Docling-handled page
  contributes no tail for the next page to continue: on such a page the trigger falls back to the
  caption and dose signals, exactly as today. No regression, but not a gain either.

## Risk

This is inside an opt-in flag that is default OFF, so output with `T2N_REVIEW_QUEUE` unset stays
byte-identical and no corpus measurement is owed for a default-ON behaviour change.

The real risk is flag volume for users who *do* turn it on: dense reference books break many
tables across pages. The geometry tests are the merge path's own and are already conservative
(column count must match exactly, x-edges within 2% of page width), so this will not fire on two
unrelated tables that merely sit at a page boundary. It does **not** inherit the merge path's
intervening-heading veto — that needs per-line y-coordinates the plain path never computes — so a
body heading between the two tables costs one false flag. That is the trade the queue exists to
make, and it is written into the delta spec's negative constraints rather than fixed by loosening
the merge veto. Measurement of the flag-rate delta on a real book is a verification task, not an
assumption.

The predicate needs the previous page's table geometry in the plain (non-merge) path, which
streams page by page today. Only the last table's geometry from the previous page is retained —
a single dict, not the whole book.

## Affected specs

- `openspec/specs/table-extraction/spec.md` — modified ("Misbinding is flagged for review, never
  auto-corrected")
