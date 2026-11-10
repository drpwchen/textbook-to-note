# conversion — delta for resplit-cli

## ADDED Requirements

### Requirement: `--resplit` re-splits an existing corpus into a new directory without touching the source

`converter/convert.py --resplit <corpus> --out <dir> --pdf-root <dir>…` SHALL, for every
immediate sub-folder not starting with `.` of `<corpus>` that contains a `.md` file (or only the `--book` names), write
`<out>/<book>/` holding: each unit's `full_text.md` (byte copy) plus the split produced from it
and its PDF's outline, every other non-figure file of the book, and `_manifest.json`; then
validate the book and record the result in `<out>/_resplit_report.json`.

#### Scenario: Normal resplit
- **GIVEN** `<corpus>/BookA/` with `full_text.md`, an empty `ch01_Preface.md`, a stale
  `ch07_Stale.md`, `notes/n.md`, and `BookA.pdf` under `--pdf-root` with ≥3 outline entries
- **WHEN** `--resplit` runs
- **THEN** `<out>/BookA/full_text.md` equals the source bytes, `ch07_Stale.md` is absent,
  `notes/n.md` is present, `_manifest.json` exists, and every file under `<corpus>` and
  `--pdf-root` has the same size, mtime and sha256 as before

#### Scenario: Same result as conversion
- **GIVEN** a book whose outline pages never decrease, no `T2N_SPLIT_*` set
- **WHEN** it is resplit
- **THEN** the `ch*.md` names and bytes equal those `split_into_chapters` writes for the same
  `full_text.md` and PDF

#### Scenario: Carried titles from publisher file names
- **GIVEN** a book without `full_text.md` holding `ch04_ListofContributors_2019_StrokeRehabilitation.md`
- **THEN** its manifest title is `ListofContributors` (the `_<year>_<BookName>` tail dropped);
  `ch12_Stroke_After_2019.md` keeps `Stroke After 2019`

#### Scenario: Book without full_text.md
- **GIVEN** a book folder holding only `ch01_x.md` with text
- **THEN** `<out>/<book>/ch01_x.md` is a copy and the manifest says `carried`

#### Scenario: No PDF found
- **GIVEN** a book with `full_text.md` and old split files, and no matching PDF
- **THEN** the old split files with text are copied, the report lists the unit with
  `"split": "carried", "reason": "no pdf"`, and the run continues

#### Scenario: Unsafe output path
- **GIVEN** `--out` equal to, inside, or containing `<corpus>`, or inside a `--pdf-root`
- **THEN** the command exits 2 before reading any book and writes nothing

#### Scenario: Resume
- **GIVEN** `<out>/BookA/_manifest.json` exists
- **WHEN** `--resplit` runs without `--force`
- **THEN** it prints `BookA: skipped (output exists)` and BookA's files are unchanged; with
  `--force` the folder is rebuilt and no `.tmp-*` folder remains

**Negative constraints**
- MUST NOT create, modify or delete any file under `<corpus>` or a `--pdf-root`.
- MUST NOT open a PDF for writing.
- MUST NOT run text extraction, OCR or table extraction, nor write a `full_text.md` (the config
  `method: reconstruct` rebuilds the text in memory only).
- MUST NOT write `batch_progress.json` / `already_converted.json` bookkeeping.
- An exception in one book MUST NOT abort the run; that book is reported as `error` and its
  temp folder removed.
- MUST NOT set any `T2N_SPLIT_*` environment variable.
