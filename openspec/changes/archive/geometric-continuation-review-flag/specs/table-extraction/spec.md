# table-extraction — delta for geometric-continuation-review-flag

## MODIFIED Requirements

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
  before this change

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
