# Tasks — geometric-continuation-review-flag

## Implementation

- [ ] `extract_tables_md()` returns each table's continuation geometry alongside its markdown
      → Requirement: Misbinding is flagged for review, never auto-corrected
- [ ] `geometric_continuation()` predicate reusing `TABLE_MERGE_*` constants and the same
      bottom/top/col-count/x-edge tests as `_is_continuation()`
      → Requirement: Misbinding is flagged for review, never auto-corrected
- [ ] plain path carries the previous page's last-table geometry and passes
      `geometric_continuation=` into `review_reasons()`
      → Requirement: Misbinding is flagged for review, never auto-corrected
- [ ] `review_queue.review_reasons()` accepts the new signal and names geometry in its reason
      → Requirement: Misbinding is flagged for review, never auto-corrected
- [ ] `docs/table-review.md`: triggers are independent (either flags), continuation is geometric

## Verification

- [ ] Test: categorical cross-page table, no caption, `T2N_TABLE_MERGE` unset → flagged
- [ ] Test: column-count mismatch across the boundary → not flagged
- [ ] Test: `T2N_REVIEW_QUEUE` unset → no marker, output unchanged
- [ ] `converter/test_review_queue.py` and `converter/test_table_merge.py` pass unchanged
- [ ] `T2N_REVIEW_QUEUE` default is still OFF and `T2N_TABLE_MERGE` behaviour is untouched
- [ ] Independent verifier pass (Tier 2) over the implementation

## Ship

- [ ] `CHANGELOG.md` entry under Unreleased
- [ ] Archive: fold delta into `openspec/specs/`, move this directory to `changes/archive/`
