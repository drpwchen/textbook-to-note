# table-extraction — tables, and the ways they go wrong quietly

## Purpose

Recover textbook tables without emitting output that reads as clean citable data while
being wrong. A wrong dose in a table that looks fine is the worst thing this tool can
produce, so most requirements here are about **refusing or warning**, not extracting.

Implemented in `converter/convert.py` (gate, rejections, merge, book-level checks) and
`converter/review_queue.py`.

## Requirements

### Requirement: Table detection is gated behind a cheap pre-check

The system SHALL run `pdfplumber` table detection only on pages where a cheap `fitz`
pre-check finds a table signature, because table detection is the slowest part of
conversion.

The signature is: at least `TABLE_RULE_MIN` (2) horizontal AND vertical rulings longer than
`TABLE_RULE_MIN_LEN` (10.0 pt); or at least `TABLE_HRULE_MIN_COUNT` (3) horizontal rules
each spanning at least `TABLE_HRULE_MIN_WIDTH_FRAC` (0.40) of page width (the three-line
table case); or a multilingual table keyword ("Table" / 表).

#### Scenario: Table-sparse book converts faster
- **GIVEN** a book whose pages mostly contain no tables
- **WHEN** conversion runs with the gate enabled (the default)
- **THEN** conversion is materially faster than with `T2N_TABLE_GATE=0` and finds the same
  tables

#### Scenario: Three-line table is not missed
- **GIVEN** a page with a table drawn with three horizontal rules and no vertical rules
- **WHEN** the pre-check runs
- **THEN** the page is passed to `pdfplumber` for detection

**Negative constraints**
- The gate MUST NOT be tightened in a way that trades recall for speed. A missed table is a
  worse failure than a slow conversion.

### Requirement: Page decoration is rejected, not emitted as a table

The system SHALL drop a detected table that is page furniture rather than data, and SHALL
leave a visible trace comment in its place.

Rejection triggers: a candidate with at most `TABLE_FRAME_MAX_COLS` (1) column whose
largest cell exceeds `TABLE_FRAME_CELL_CHARS` (500) characters, or whose bbox alone covers
at least `TABLE_FRAME_AREA_FRAC` (0.50) of the page.

#### Scenario: Page-frame pseudo-table is dropped with a trace
- **GIVEN** a page whose content frame plus running-header rule produce a one-column
  "table" containing the whole page body in a single cell
- **WHEN** the page is converted
- **THEN** the table is absent from the markdown and replaced by
  `<!-- ⚠️ page-frame pseudo-table rejected on page N -->`

#### Scenario: A legitimate boxed list loses nothing
- **GIVEN** a boxed list that trips the rejection rule
- **WHEN** it is dropped
- **THEN** its text is still present in the page's own prose in the same markdown file

**Negative constraints**
- A rejection MUST leave a comment. Silent removal is forbidden — the reader must be able
  to tell "no table here" from "a table was dropped here".

### Requirement: Spanned category headers are collapsed structurally only

The system SHALL re-cast a row whose cells are an identical repeated string as a single
header cell, because the extractor broadcasts section headers across every column and
thereby shifts the alignment of real rows.

Trigger: at least `TABLE_HEADER_COLLAPSE_MIN_COLS` (3) non-empty cells that are the
identical string of at least `TABLE_HEADER_COLLAPSE_MIN_LEN` (15) characters.

#### Scenario: Broadcast header row becomes one header cell
- **GIVEN** a drug×attribute grid with a row reading "Corticosteroids: Used to reduce
  inflammation." repeated across four columns
- **WHEN** the table is emitted
- **THEN** that row appears once as a header cell and the rows below it keep their original
  column alignment

**Negative constraints**
- This transform MUST NOT move a value from one row to another. It is structural only and
  therefore cannot create a misbinding.

### Requirement: Cross-page table merge is opt-in and geometrically constrained

The system SHALL stitch a table continued across a page break only when the geometry
matches, and only when explicitly enabled with `T2N_TABLE_MERGE=1`.

Constraints: the upper table ends within `TABLE_MERGE_BOTTOM_FRAC` (0.12) of page height
from the bottom; the lower starts within `TABLE_MERGE_TOP_FRAC` (0.28) from the top; column
x-edges agree within `TABLE_MERGE_XTOL_FRAC` (0.02) of page width; the same column count;
no intervening heading; the top/bottom `TABLE_MERGE_MARGIN_FRAC` (0.08) band is treated as
running-header/footer furniture.

#### Scenario: Matching continuation is merged with a trace
- **GIVEN** a table ending at the bottom of page N and a geometrically matching table at
  the top of page N+1, with `T2N_TABLE_MERGE=1`
- **WHEN** conversion runs
- **THEN** the two are emitted as one table, the repeated header row appears once, and
  `<!-- table continues from page N -->` marks the join

#### Scenario: Default off is byte-identical
- **GIVEN** `T2N_TABLE_MERGE` is unset
- **WHEN** conversion runs
- **THEN** output is byte-identical to output produced before the merge feature existed

**Negative constraints**
- Merge MUST NOT proceed on column-count mismatch, on x-edge disagreement beyond tolerance,
  or across an intervening heading.

### Requirement: Misbinding is flagged for review, never auto-corrected

The system SHALL identify the table subset where a value-on-wrong-row misbinding is most likely
and mark it for a second opinion, and SHALL NOT attempt to repair it.

The high-risk subset is the union — either trigger alone flags a table — of:

1. **continuation-page tables**, detected by any of: a "(continued)" / ", Continued" / "cont'd"
   marker in the page text; a table the merge pass stitched across a page break; or page geometry
   alone — a table on the previous page ending within `TABLE_MERGE_BOTTOM_FRAC` (0.12) of page
   height from the bottom, this table starting within `TABLE_MERGE_TOP_FRAC` (0.28) of page height
   from the top, the same column count, and column x-edges agreeing within `TABLE_MERGE_XTOL_FRAC`
   (0.02) of page width.
2. **dosage/threshold tables** — at least 2 cells' worth of `mg`, `mL`, `mg/kg`, `IU`, `mEq/L`,
   `mmHg`-class tokens.

Opt in with `T2N_REVIEW_QUEUE=1`; default OFF.

#### Scenario: A dose table on a continuation page is queued
- **GIVEN** `T2N_REVIEW_QUEUE=1` and a continuation-page table containing dose units
- **WHEN** conversion runs
- **THEN** the markdown carries
  `<!-- ⚠️ table needs out-of-band review … verify against PDF page N -->` and a matching entry
  exists in the review queue

#### Scenario: A categorical table broken across pages with no caption is queued
- **GIVEN** `T2N_REVIEW_QUEUE=1`, `T2N_TABLE_MERGE` unset, a 2-column classification table
  (no dose tokens, no "(continued)" text) whose rows run from the bottom of page N to the top of
  page N+1 with matching column geometry
- **WHEN** conversion runs
- **THEN** the page N+1 table carries the review marker, its reason names geometry, and the
  review queue contains an entry for page N+1

#### Scenario: Two unrelated tables at a page boundary are not queued
- **GIVEN** `T2N_REVIEW_QUEUE=1` and a 3-column table at the bottom of page N followed by a
  2-column table at the top of page N+1
- **WHEN** conversion runs
- **THEN** neither table is flagged for the geometric continuation reason

#### Scenario: The queue stays off by default
- **GIVEN** `T2N_REVIEW_QUEUE` unset
- **WHEN** conversion runs over a book with cross-page tables
- **THEN** the markdown contains no review marker and output is byte-identical to output produced
  before the geometric continuation signal existed

**Negative constraints**
- The system MUST NOT reassign a value to a different row to "fix" a suspected misbinding. The
  correct assignment is exactly what is in doubt, so any automatic repair is a guess presented as
  data.
- The structural QC gate MUST NOT be described as catching misbinding. It cannot.
- The geometric signal MUST NOT stitch, reorder, drop, or otherwise alter a table. It sets a flag
  and nothing else.
- The geometry thresholds MUST be the `TABLE_MERGE_*` constants as they stand, not a second set
  tuned for flagging.
- Detecting the geometric continuation MUST NOT require a second table-detection pass over a page.
- The geometric signal applies to the FIRST table on a page only, and only when the immediately
  preceding page emitted a table. It MUST NOT be claimed across a table-free page.
- Unlike the merge decision, the flag predicate does not apply the intervening-heading veto (the
  plain path computes no per-line y-coordinates). A heading between the two tables therefore costs
  one false flag, which is the trade the review queue exists to make; it MUST NOT be resolved by
  loosening the merge path's own veto.

### Requirement: Whole-book table loss is announced loudly

The system SHALL detect books whose table extraction failed wholesale or partially, and
SHALL surface it in both the conversion report and the markdown itself.

Triggers: `pdfplumber` parses 0 pages while `fitz` opens the file; or the book yields 0
tables despite at least `BOOK_ZERO_TABLE_MIN_CAPTIONS` (10) table captions; or the
tables-per-caption ratio falls below `BOOK_PARTIAL_TABLE_RATIO` (0.20).

#### Scenario: Zero-table book is warned about in-band
- **GIVEN** a book with 40 table captions and 0 extracted tables
- **WHEN** conversion completes
- **THEN** the conversion report and the generated markdown both carry a warning

#### Scenario: A healthy book is not warned about
- **GIVEN** a book whose tables-per-caption ratio is near 1.0
- **WHEN** conversion completes
- **THEN** no whole-book table warning is emitted

**Negative constraints**
- This requirement is detection only. Extraction behaviour MUST NOT change based on it.

### Requirement: Books hostile to table extraction carry a reliability banner

The system SHALL hang a `> [!caution]` banner at the top of a book's markdown when a large
fraction of its tables lost content, so a downstream model is told to verify every table
against the source PDF.

Trigger: at least `BOOK_CONTENT_LOSS_RATE` (0.25) of the book's tables lost content, given
at least `BOOK_HIGH_FLAG_MIN_TABLES` (10) tables. `reliability_flagged`,
`content_loss_rate` and `flag_rate` are recorded in the per-book stats.

#### Scenario: Content-loss rate above threshold produces a banner
- **GIVEN** a book with 60 tables, 30% of which lost page text that reached no cell
- **WHEN** conversion completes
- **THEN** the markdown begins with a `> [!caution]` banner and `reliability_flagged` is
  true in the per-book stats

**Negative constraints**
- The trigger MUST be content loss, not any-QC-flag rate. Any-flag rates cluster at 39–64%
  across all dense clinical books and therefore separate nothing.
