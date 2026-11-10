# Tasks — resplit-deep-bookmarks

## Implementation

- [x] `split_manifest.insert_deep_headings()` (normaliser, page-scoped matcher, insert-only)
      → Requirement: Deep bookmarks become markdown headings when opted in
- [x] Hook at the end of `split_by_bookmarks`, gated by `T2N_SPLIT_DEEP_HEADINGS` (default `"0"`)

## Verification

- [x] `converter/test_resplit.py::test_deep_headings` — each Scenario, unset == `0`, insert-only
- [x] Existing `test_split_same_page.py` 19/19, `test_clear_stale_chapters.py` 27/27 unchanged
- [x] Insert rate across the full corpus recorded in phase2-after.md (informational)
      Done 2026-11-10: 58,385 / 63,406 deep entries inserted (92.1%, 341 split units); validator unchanged (283/284).

## Ship

- [x] `CHANGELOG.md` entry
- [x] Version bump + tag
- [x] Archive
