# Tasks — split-config

## Implementation

- [x] Config loading + per-unit override in `converter/resplit.py`
      → Requirement: A per-book split config steers `--resplit` without code changes
- [x] `transform_toc`: drop_titles (incl. `after` / `max_level`), page_offset, anchoring,
      backward-entry removal → same Requirement
- [x] `anchor_titles`: title index (1–3 joined lines), numbered-line candidates, longest
      increasing choice, gap fill, deep-entry placement → same Requirement

## Verification

- [x] `converter/test_resplit.py` — anchoring scenario (with / without the entry)
- [x] Lanken 2e 112/112 chapters, Zasler 2e 86/86 (validator pass) — pilot
- [x] Config entries written for every book the full run fails, listed with reasons in phase2-after.md
      Done 2026-11-10: 47 entries in textbook-md-v2/_split_config.json, each with a reason.

## Ship

- [x] `CHANGELOG.md` entry
- [x] Version bump + tag
- [x] Archive
