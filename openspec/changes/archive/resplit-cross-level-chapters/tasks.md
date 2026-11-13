# Tasks — resplit-cross-level-chapters

## Implementation

- [x] Promote keyword chapter entries to the chapter level (split + chapter state), sections keep original level → Requirement: A `Chapter N` outline entry is a chapter at whatever level it sits
- [x] `promote_chapters: false` config key, `promoted_chapters` report count → same
- [x] Validator `chapter_unmapped` → Requirement: The validator fails a book…
- [x] Config entries for Quinn, Tarbox (data)

## Verification

- [x] Test for each Scenario
- [x] Corpus: resplit all books before/after into scratch, diff manifests; every changed book reviewed
- [x] Validator over the new output: no new failures except books that were silently wrong

## Ship

- [x] `CHANGELOG.md` entry, version 0.8.1 + tag
- [x] Archive
