# Tasks — force-clears-stale-chapters

## Implementation

- [x] `clear_stale_split_files(out_dir)` with the two filename regexes, non-recursive, path
      containment check against `OUTPUT_BASE`
      → Requirement: Re-conversion removes the book's own stale split files before splitting
- [x] Call it in both the fitz and the Surya branch of `batch_dir_convert`, after `full_text.md`
      is written, before `split_into_chapters`
      → same Requirement
- [x] `T2N_CLEAR_STALE_CHAPTERS` default `"1"`; removal via `os.remove`

## Verification

- [x] Test for each Scenario (temp `OUTPUT_BASE`, stubbed `convert_pdf`)
- [x] Kill switch → directory listing identical to v0.7.3
- [x] Corpus measurement recorded (phase2-baseline.md): 403 folders, 0 `ch`/`pages` names
      outside the patterns; 39 bookmarked books → 1,214 stale files without the clear, all textless

## Ship

- [x] `CHANGELOG.md` entry
- [x] Version bump + tag
- [x] Archive
