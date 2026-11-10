# Tasks — split-manifest-contract

## Implementation

- [x] `converter/split_manifest.py`: chapter-number parser, chapter-level choice, kind classifier,
      page-label read (same read as the outline), manifest validator + CLI
      → all three Requirements
- [x] Manifest assembly in `converter/resplit.py` → Requirement: Every resplit book carries a schema-1 manifest

## Verification

- [x] `converter/test_resplit.py` — scenarios for parser, kinds, labels, validator negatives
- [x] Pilot 5 books (Braddom 7e, DeLisa 6e, Kelley 11e, Lanken 2e, Zasler 2e): validator 5/5
- [x] Full corpus into `textbook-md-v2/`: validator result + 100-chapter hand check recorded in
      Done 2026-11-10: validator 283/284; 100-chapter sample 99/100 (DeLee merged-PDF outline read +7 → fixed by config), 100/100 after fix; manifests 283/283.
      `_scratch/rag-review-2026-11-05/phase2-after.md`

## Ship

- [x] `CHANGELOG.md` entry
- [x] Version bump + tag
- [x] Archive
