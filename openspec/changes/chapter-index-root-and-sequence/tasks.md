# Tasks — chapter-index-root-and-sequence

## Implementation

- [ ] `default_root()` in `textbook_chapter_index.py`: `--textbook-dir` → `TEXTBOOK_DIR` →
      `{repo}/output` → None  → Requirement: The chapter index never scans an unnamed directory
- [ ] `textbook_ref_lint.py:lint()` uses the same resolver and exits 2 when it yields nothing
      → Requirement: The chapter index never scans an unnamed directory
- [ ] `--rebuild` refuses when no book is recognised; `--allow-empty` overrides
      → Requirement: A rebuild that recognises no book is refused
- [ ] per-directory try/except around `iterdir()` / `glob()` in `build()`, skipped count in the
      summary  → Requirement: A rebuild that recognises no book is refused
- [ ] `RE_SEQ` + `strategy: "seq"` with a `sequence` table  → Requirement: A sequence-split book
      is named as such, never guessed at
- [ ] `_chapter_index.chapters.json` loader; pinned books get `strategy: "pinned"`
      → Requirement: A sequence-split book is named as such, never guessed at
- [ ] `check_ref()` excludes `seq` from `mapped` and emits the specific UNVERIFIABLE detail
      → Requirement: A sequence-split book is named as such, never guessed at
- [ ] `workflows/note-writing.md` self-check block shows `--textbook-dir`

## Verification

- [ ] `citations/test_chapter_refs.py` extended: one test per Scenario above
- [ ] Existing 11 test groups still pass unchanged (no verdict that resolves today regresses)
- [ ] No `T2N_*` flag involved; no default-ON extraction behaviour changed

## Ship

- [ ] `CHANGELOG.md` entry under Unreleased
- [ ] Archive: fold delta into `openspec/specs/`, move this directory to `changes/archive/`
