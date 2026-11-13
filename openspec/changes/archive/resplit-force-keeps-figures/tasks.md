# Tasks — resplit-force-keeps-figures

## Implementation

- [x] `resplit.carry_unowned_dirs()` before `shutil.rmtree(final)` in `resplit_book()`
      → Requirement: `--resplit --force` keeps figure folders and links

## Verification

- [x] `test_resplit.py::test_force_keeps_figure_dirs` (a real junction on Windows, a symlink elsewhere)
- [x] Real book: forced resplit of a book whose corpus copy has a figures junction, in a scratch copy
- [x] Full suite green

## Ship

- [x] `CHANGELOG.md` entry, version, tag, archive

### Measurement (2026-11-14)

- Strakowski (476 figures): corpus copy given a `figures` junction to the converted book, then
  `--resplit --force --book`: `kept_dirs: ["figures"]`, still a junction, 476 files reachable,
  validator passes. Rest of the corpus: same 0-change run as resplit-gap-chapters (no `--force`
  folders existed in the scratch output, so the write path is unchanged).
