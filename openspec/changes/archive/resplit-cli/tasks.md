# Tasks — resplit-cli

## Implementation

- [x] `--resplit`, `--out`, `--pdf-root` (repeatable), `--book`, `--split-config`, `--pdf-map`
      on `convert.py`; path-safety check → exit 2
      → Requirement: `--resplit` re-splits an existing corpus into a new directory without touching the source
- [x] `converter/resplit.py`: PDF index + lookup ladder, per-book temp folder + rename, carried
      books, per-chapter-PDF books, fingerprint before/after, per-book report, resume → same Requirement

## Verification

- [x] `converter/test_resplit.py` — each Scenario (synthetic corpus + PDFs)
- [x] Pilot 5 books into a scratch out dir: validator 5/5
- [x] Full corpus into `~/Documents/Projects/textbook-md-v2/`, numbers in phase2-after.md
      Done 2026-11-10: 284 books, 283 valid (Weiss Schroth: no outline, needs re-OCR), Cyriax/Sivan skipped (no md); 0-byte files 5,046 → 0.

## Ship

- [x] `CHANGELOG.md` entry + README usage line (only after the full run)
- [x] Version bump + tag
- [x] Archive
