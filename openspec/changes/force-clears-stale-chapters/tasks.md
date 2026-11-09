# Tasks — force-clears-stale-chapters

## Implementation

- [ ] `clear_stale_split_files(out_dir)` with the two filename regexes, non-recursive, path
      containment check against `OUTPUT_BASE`
      → Requirement: Re-conversion removes the book's own stale split files before splitting
- [ ] Call it in both the fitz and the Surya branch of `batch_dir_convert`, after `full_text.md`
      is written, before `split_into_chapters`
      → same Requirement
- [ ] `T2N_CLEAR_STALE_CHAPTERS` default `"1"`; removal via `os.remove`

## Verification

- [ ] Test for each Scenario (temp `OUTPUT_BASE`, stubbed `convert_pdf`)
- [ ] Kill switch → directory listing identical to v0.7.3
- [ ] Corpus measurement recorded (phase2-baseline.md)

## Ship

- [ ] `CHANGELOG.md` entry
- [ ] Version bump + tag
- [ ] Archive
