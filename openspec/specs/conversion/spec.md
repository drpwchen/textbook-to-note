# conversion — PDF/EPUB → markdown

## Purpose

Turn a book the user owns into greppable markdown with page markers, preserving reading
order and detecting the cases where extraction *looks* fine but silently lost content.
Entry point: `converter/convert.py`. Target cost: zero LLM tokens.

## Requirements

### Requirement: Local text extraction is the default path

The system SHALL extract text from born-digital PDFs using PyMuPDF locally, without calling
any network service or language model.

#### Scenario: Born-digital PDF converts offline
- **GIVEN** a PDF with a usable native text layer
- **WHEN** `converter/convert.py` runs against it with no OCR environment configured
- **THEN** markdown is written under `OUTPUT_DIR` and no network request is made

#### Scenario: CPU-only machine is a supported end state
- **GIVEN** a machine with no GPU, no ollama, and no Surya venv
- **WHEN** conversion runs on a born-digital PDF
- **THEN** conversion completes and the absence of a GPU is not reported as an error

#### Scenario: Source PDFs are never modified
- **GIVEN** any conversion run
- **WHEN** the run finishes, whether it succeeded or failed
- **THEN** the source file under `BOOKS_DIR` is byte-identical to before the run

**Negative constraints**
- The converter MUST NOT write to, move, or delete anything inside `BOOKS_DIR` other than
  its own bookkeeping files (`books.json`, `already_converted.json`).
- The converter MUST NOT send page images or page text to a remote API on the default path.

### Requirement: Chapter detection never writes to the input PDF

The system SHALL treat every input PDF as read-only in every mode (`--batch-dir`, `--batch`,
single file), including after a chapter split that found chapters in a PDF without bookmarks.
There is no flag that enables writing to it.

#### Scenario: Bookmark-less PDF split by pattern detection
- **GIVEN** a PDF with 0 bookmarks whose text yields ≥2 chapters via `detect_chapters()`
- **WHEN** `--batch-dir` converts it (fitz path)
- **THEN** the PDF's sha256, size and mtime are identical before and after the run, and
  `fitz.open(pdf).get_toc()` still returns `[]`

#### Scenario: Same on the OCR path
- **GIVEN** a scan-only PDF with 0 bookmarks routed to Surya, whose OCR text yields ≥2 chapters
- **WHEN** `--batch-dir` converts it
- **THEN** the PDF's sha256, size and mtime are identical before and after the run

#### Scenario: Split output unaffected
- **GIVEN** any PDF converted by v0.7.3 and by this version with the same env
- **WHEN** both outputs are compared
- **THEN** `full_text.md` and every chapter file are byte-identical

#### Scenario: Read-only PDF
- **GIVEN** a PDF with the filesystem read-only attribute set and 0 bookmarks
- **WHEN** `--batch-dir` converts it
- **THEN** conversion completes with no PDF write attempted and no warning about it

**Negative constraints**
- The converter MUST NOT write to the input PDF's path by any means (`save`/`saveIncr` targeting
  it, `set_toc` followed by a save, opening it for writing). Saving a repaired copy to a
  separate temp path (the pdfplumber 0-page repair, `convert.py` ~:1115) remains allowed.
- No `T2N_*` variable or CLI flag MAY re-enable the write-back.
- Documentation MUST NOT claim the converter adds bookmarks to source PDFs.

### Requirement: Bookmarks that share a split line produce one chapter file

When splitting by PDF bookmarks, the system SHALL collapse level-1/2 bookmarks that resolve to
the same `<!-- page N -->` line into a single split point, and SHALL NOT write a chapter file
for the collapsed bookmarks. Survivors keep their original sequence number (`idx + 1` over the
uncollapsed list), so gaps in `chNN` are expected. Default ON; `T2N_SPLIT_SAME_PAGE_MERGE=0`
disables it.

#### Scenario: Part and chapter bookmark on the same page
- **GIVEN** a PDF whose bookmarks are `[1, "Part I", 12]`, `[2, "Chapter 1 Gait", 12]`,
  `[2, "Chapter 2 Balance", 30]` (plus ≥1 more so `get_pdf_bookmarks` returns them) and a
  `full_text.md` with markers for pages 12 and 30
- **WHEN** the book is split with no `T2N_*` variables set
- **THEN** `ch01_Part_I.md` does not exist, `ch02_Chapter_1_Gait.md` starts at the page-12
  marker and ends before the page-30 marker, and `ch03_Chapter_2_Balance.md` exists

#### Scenario: No empty chapter file is written
- **GIVEN** any book split by bookmarks with the behaviour on, whose level-1/2 bookmark pages
  never decrease (out-of-order bookmarks are out of scope, see the negative constraints)
- **WHEN** the split finishes
- **THEN** every written `ch*.md` has a size > 0 bytes **and** at least 1 non-whitespace
  character outside `<!-- page N -->` lines

#### Scenario: Kill switch restores prior output
- **GIVEN** the same PDF and `full_text.md`, and `T2N_SPLIT_SAME_PAGE_MERGE=0`
- **WHEN** the book is split
- **THEN** the set of file names and every file's bytes equal the v0.7.3 output, including the
  0-byte `ch01_Part_I.md`

#### Scenario: Every bookmark resolves to one page (broken bookmarks)
- **GIVEN** a PDF with 235 level-1/2 bookmarks that all resolve to page 1 (Lanken 2e shape)
- **WHEN** the book is split
- **THEN** the bookmark split is still used (the `< 2` check counts bookmarks before
  collapsing, as today), `ch00_Front_Matter.md` and `ch235_<last title>.md` are written with the
  same bytes as v0.7.3, and no other `ch*.md` is written

**Negative constraints**
- Files that are non-empty today MUST keep their exact file name and bytes; no file is renamed
  or renumbered. Sole exception: a file with 0 non-whitespace characters outside
  `<!-- page N -->` lines is not written.
- The system MUST NOT move any text between files; collapsing only removes empty files.
- The system MUST NOT switch a book from the bookmark split to pattern detection because of
  collapsing.
- The system MUST NOT change behaviour for bookmarks whose page is lower than the previous
  bookmark's page (out of scope; a separate future change).
- `full_text.md` MUST NOT change.

### Requirement: Re-conversion removes the book's own stale split files before splitting

When `--batch-dir` re-converts a PDF or EPUB whose output directory already exists (`--force` or
the source being newer than `full_text.md`), the system SHALL,
after `full_text.md` has been written successfully and before the chapter split writes, remove
from that book's output directory every file whose name matches the converter's split naming:

- `^ch\d{2,}[a-z]?(_[A-Za-z0-9_]*)?\.md$` (bookmark / pattern / EPUB split, incl.
  `ch00_Front_Matter.md`; the optional letter covers EPUB `Chapter 12a` → `ch12a_*.md`)
- `^pages_\d{4}-\d{4}\.md$` (`_force_split`)

Removal is a direct delete (`os.remove`); the run log prints the removed file names, one line
per book. Default ON; `T2N_CLEAR_STALE_CHAPTERS=0` disables it.

#### Scenario: A chapter the new split no longer produces disappears
- **GIVEN** `OUTPUT_BASE/BookA/` holds `full_text.md`, `ch01_Part_I.md` (0 bytes),
  `ch02_Gait.md`, and the new split produces only `ch02_Gait.md`
- **WHEN** `--batch-dir --force` re-converts `BookA.pdf`
- **THEN** `OUTPUT_BASE/BookA/ch01_Part_I.md` does not exist and `ch02_Gait.md` has the new bytes

#### Scenario: Source-newer re-conversion and EPUB clear too
- **GIVEN** a stale `ch01_Part_I.md` in `OUTPUT_BASE/BookA/`, and the source (PDF, or an EPUB
  `BookA.epub`) is newer than `full_text.md`; no `--force`
- **WHEN** `--batch-dir` runs
- **THEN** `ch01_Part_I.md` does not exist after the run and the log names it

#### Scenario: Unrelated files survive
- **GIVEN** the same directory also holds `notes.md`, `Ch12_Hand.md` (capital C), `ch1.md`
  (one digit), `figures/ch03_x.md`, `_manifest.json`
- **WHEN** the book is re-converted
- **THEN** all five still exist with unchanged bytes

#### Scenario: Failed conversion keeps the old chapters
- **GIVEN** a book with existing chapter files
- **WHEN** `convert_pdf` raises before `full_text.md` is written
- **THEN** every pre-existing `ch*.md` / `pages_*.md` still exists with unchanged bytes

#### Scenario: Kill switch
- **GIVEN** `T2N_CLEAR_STALE_CHAPTERS=0`
- **WHEN** the same re-conversion runs
- **THEN** the directory listing after the run equals v0.7.3's (stale `ch01_Part_I.md` remains)

**Negative constraints**
- The system MUST NOT copy or move the removed files to a holding folder inside `OUTPUT_BASE`
  (the indexer would pick them up again).
- The system MUST NOT recurse into subdirectories or follow symlinks/junctions.
- The system MUST NOT remove `full_text.md`, or any file not matching the two patterns above.
- The system MUST NOT touch any directory other than `OUTPUT_BASE/<stem of the PDF being
  converted>`; a resolved path outside `OUTPUT_BASE` aborts the clear for that book with a
  warning and no removal.
- The system MUST NOT clear a book that is skipped in this run (already converted,
  `already_converted.json`, OCR unavailable).

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

#### Scenario: bookkeeping inside a book folder is not copied
- **GIVEN** `<corpus>/_Guidelines/batch_progress.json` (left there by a `--batch-dir` run with
  `OUTPUT_DIR=<corpus>/_Guidelines`)
- **WHEN** `--resplit` runs
- **THEN** `<out>/_Guidelines/batch_progress.json` does not exist; every other file is as before

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
- MUST NOT copy a file named `batch_progress.json`, `conversion_summary.json` or
  `already_converted.json` at any depth.
- An exception in one book MUST NOT abort the run; that book is reported as `error` and its
  temp folder removed.
- MUST NOT set any `T2N_SPLIT_*` environment variable.

### Requirement: Every resplit book carries a schema-1 manifest

`--resplit` SHALL write `<out>/<book>/_manifest.json` (schema 1) for every book it writes,
listing every chapter `.md` file under the book folder (all depths, figure folders excluded,
`full_text.md` excluded) with `seq`, `chapter_number`, `title`, `pdf_page_start`, `pdf_page_end`
(the first and last `<!-- page N -->` marker in that file, null when it has none) and `kind`,
and every outline entry of the source PDF in `sections` with `level`, `title`, `pdf_page`
(null when it could not be placed), `file` (the listed file holding that page, else null) and
`kind`.

#### Scenario: Bookmark book
- **GIVEN** a book with `full_text.md` and a PDF whose outline is Preface (p1), Part I (p3),
  Chapter 1 Gait (p3) › References (p3), Chapter 2 Balance (p6), Chapter 3 Spasticity (p9), Index (p12)
- **WHEN** it is resplit
- **THEN** `split_method == "bookmarks"`, `source_pdf == "<pdf file name>"`, the files titled
  `Chapter 1 Gait` / `Chapter 2 Balance` / `Chapter 3 Spasticity` have `chapter_number` 1 / 2 / 3,
  the Index file has `kind == "back"`, `toc_chapters == 3`, and `sections` contains
  `{"level": 3, "title": "References", "pdf_page": 3, "kind": "references", …}`

#### Scenario: Book without full_text.md
- **GIVEN** a book folder holding only `ch01_x.md`
- **WHEN** it is resplit
- **THEN** the manifest has `split_method == "carried"`, `source_pdf == null`, and lists `ch01_x.md`

**Negative constraints**
- MUST NOT write an absolute path or a directory into `source_pdf`.
- MUST NOT write a sequence number into `chapter_number`; an unparseable title yields `null`.
- MUST NOT modify any markdown text to record `kind`.
- MUST NOT write or modify `_chapter_index.chapters.json` or `_chapter_index.json`.

### Requirement: Printed page labels come only from the PDF's own page-label table

`page_labels` SHALL map each 1-based physical PDF page to its non-empty label from the PDF's
page-label table, and SHALL be `null` with `page_confidence == "pdf_only"` when the PDF defines
no labels. For a book with several PDFs, top-level `page_labels` is `null` and each PDF's map is
in `page_label_sets[<pdf file name>]`.

#### Scenario: Roman front matter
- **GIVEN** a 12-page PDF labelled `i`, `ii` on pages 1–2 and `1`… from page 3
- **WHEN** the manifest is written
- **THEN** `page_labels["1"] == "i"`, `page_labels["3"] == "1"`, it has 12 entries, and
  `page_confidence == "printed"`

#### Scenario: No label table
- **GIVEN** a PDF with no page labels
- **THEN** `page_labels` is `null` and `page_confidence` is `"pdf_only"`

**Negative constraints**
- MUST NOT derive a label from an offset, a running header or OCR.

### Requirement: The validator fails a book whose split breaks the contract

`converter/split_manifest.py --validate <corpus>` SHALL report a book as FAIL, naming the
check, when any of these holds:
`empty_file` (a listed file has no text outside page markers) ·
`unlisted_file` / `missing_file` (md on disk vs manifest differ) ·
`page_range` (manifest pages ≠ the file's own markers) ·
`page_overlap` (two files of the same folder and source PDF share a page; a file whose entry
carries `page_unit` — a chapter-per-PDF file — is its own page sequence) ·
`page_gap` (pages between consecutive files hold text in `full_text.md`) ·
`chapter_order` (a chapter number lower than an earlier one, in seq order) ·
`chapter_gap` (a number missing between the lowest and highest, unless listed in the config's
`missing_chapters_ok`) ·
`toc_count` (distinct chapter numbers ≠ `toc_chapters` when that is not null) ·
`chapter_unmapped` (a `sections` entry whose title starts with `Chapter N` / `Chap. N` names a
number no file carries, unless listed in `missing_chapters_ok`; books with
`split_method == "per_chapter_pdf"` are exempt, their outline numbers belong to each chapter PDF) ·
`title_mismatch` (a `body` file's numbering-stripped title is neither a substring of its first
two pages, punctuation and whitespace ignored, nor has ≥ 80% of its ≥3-letter words there);
a file named in the manifest's `title_check_skip` {file: reason}, copied from the split config,
is a `title_check_skipped` warning instead).
Folders whose name starts with `.` are not books and are never validated.
Exit code 0 when every book passes, 1 otherwise, 2 for a missing corpus.

#### Scenario: Marker-only file
- **GIVEN** a listed `ch99_Empty.md` containing only `<!-- page 12 -->`
- **THEN** the book FAILs with `empty_file`

#### Scenario: Chapter numbers out of order
- **GIVEN** a manifest whose files carry chapter numbers 2, 1, 3 in seq order
- **THEN** the book FAILs with `chapter_order`

#### Scenario: A chapter-per-PDF book
- **GIVEN** 50 files, each from its own PDF and each starting at page 1
- **THEN** no `page_overlap` is reported (page sequences are per source PDF)

#### Scenario: Two chapter PDFs with the same file name
- **GIVEN** a chapter-per-PDF book holding `基礎_00_目錄.md` and `臨床_00_目錄.md`, both from a PDF
  named `00_目錄.pdf` (two folders), both pages 1-2, each entry with its own `page_unit`
- **THEN** no `page_overlap` is reported

#### Scenario: Chapters merged into the previous file
- **GIVEN** a manifest whose `sections` list `CHAPTER 26 Z` and no file has `chapter_number` 26
- **THEN** the book FAILs with `chapter_unmapped` naming 26

#### Scenario: Title check skipped by config
- **GIVEN** a section-divider file whose title is printed only as an image, and
  `title_check_skip: {"<file>": "<reason>"}` in the book's config entry
- **THEN** the book passes, with a `title_check_skipped` warning naming the file and the reason

**Negative constraints**
- The validator MUST NOT modify any file.
- A pattern / carried book with chapter numbers and no `toc_chapters` is a warning
  (`toc_unverified`), not a pass of `toc_count`.

### Requirement: A `Chapter N` outline entry is a chapter at whatever level it sits

When `--resplit` has a chapter level for a PDF outline (from the config's `chapter_level` or
chosen automatically), every entry whose title starts with `Chapter N` / `Chap. N` (case
ignored, `N` = 1–3 digits) at another level, whose `N` no chapter-level entry carries, SHALL be
split as a chapter: it and the entries below it (until the next entry at its original level or
shallower) move by the same level difference. The file it starts carries `chapter_number` N.
`sections` keep the PDF's original levels. The run report counts moved entries as
`promoted_chapters`. A config entry `promote_chapters: false` turns this off for that book; a book whose config
entry has `chapter_number_overrides` is never promoted (its split was numbered by hand).

#### Scenario: Chapters nested one Section deeper
- **GIVEN** an outline `CHAPTER 24 X` (L2, p600), `CHAPTER 25 Y` (L2, p615), `SECTION IV` (L3,
  p650), `CHAPTER 26 Z` (L4, p650) › `Introduction` (L5, p650), `CHAPTER 27 W` (L4, p670)
- **WHEN** the book is resplit
- **THEN** files with `chapter_number` 26 and 27 start at pages 650 and 670, the chapter-25 file
  ends at page 649, and `sections` still lists `CHAPTER 26 Z` with `level: 4`

#### Scenario: First chapter one level shallower
- **GIVEN** `CHAPTER 1: Family-Centered Care` (L1, p19) › 6 sections (L2), `PART I` (L1, p33),
  `CHAPTER 2: Motor Development` (L2, p35)
- **THEN** pages 19–32 are one file with `chapter_number` 1 and `kind: body`, not six unnumbered files

#### Scenario: Number already taken
- **GIVEN** chapter-level entries `Chapter 1`…`Chapter 5` and an L3 entry `Chapter 3 summary`
- **THEN** nothing is promoted

#### Scenario: Turned off
- **GIVEN** the first scenario's book with config `{"promote_chapters": false}`
- **THEN** the output is identical to before this change

**Negative constraints**
- MUST NOT promote when the book has no chapter level.
- MUST NOT promote in a book whose config entry has `chapter_number_overrides`.
- MUST NOT promote a title that only starts with a number (`26 Female Athlete`).

### Requirement: A per-book split config steers `--resplit` without code changes

`--resplit` SHALL read `--split-config FILE`, else `<out>/_split_config.json`, and apply the
entry named after the book folder (unit entries under `units` override it for that sub-folder)
before splitting: `drop_titles`, then `page_offset`, then `anchor_titles`, then removal of
split-level entries whose page is lower than an earlier one. With `anchor_titles`, a split-level
entry is placed at the line whose normalised text equals its numbering-stripped title (also
over up to 3 joined lines), preferring lines that carry the entry's own chapter number
(including such a line that is the title cut short or with a typo, similarity ≥ 0.85); among
candidates the longest increasing sequence is kept; pages where ≥ 5 distinct titles match are
treated as a contents listing and skipped. Deeper entries are placed inside their chapter's span.

#### Scenario: Broken outline repaired by anchoring
- **GIVEN** a PDF whose outline entries all point at page 1, `full_text.md` with `CHAPTER 1 /
  GAIT` on page 3, `CHAPTER 2 / BALANCE` on page 6, `CHAPTER 3 / SPASTICITY` on page 9, and the
  config `{"BookA": {"anchor_titles": true}}`
- **WHEN** BookA is resplit
- **THEN** the files with `chapter_number` 1 / 2 / 3 start at PDF pages 3 / 6 / 9,
  `split_method == "config"`, and the book validates

#### Scenario: Without the entry
- **GIVEN** the same book, no config entry
- **THEN** the book fails validation (its text lands in one file)

#### Scenario: Title not found
- **GIVEN** an anchored entry whose title occurs nowhere after the previous placed entry
- **THEN** it is not a split point, its `sections` row has `pdf_page: null`, and the run report
  counts it in `split_total` but not `split_anchored`

#### Scenario: A file whose title cannot be verified
- **GIVEN** a config entry `title_check_skip: {"ch26_SECTION_1.md": "divider page, title is an image"}`
- **THEN** the manifest carries the same `title_check_skip` map and that file is not title-checked

#### Scenario: Outline given by the config
- **GIVEN** a PDF with no usable outline (none, or one written by converter ≤ 0.7.3) and the entry
  `toc: [[1, "Chapter 1 Gait", 3], [1, "Chapter 2 Balance", 6], [1, "Chapter 3 Spasticity", 9]]`
- **THEN** the files with `chapter_number` 1 / 2 / 3 start at PDF pages 3 / 6 / 9 and
  `split_method == "config"`; with `anchor_titles: true` the pages may be `null` and the
  titles are located in the text as for a PDF outline

#### Scenario: Book folder starting with `_`
- **GIVEN** a config entry for the book folder `_Guidelines` (a dict) next to the `_doc` note
- **THEN** the `_Guidelines` entry is applied; `_doc` (a string) is ignored

#### Scenario: Override makes a chapter of a front-by-position file
- **GIVEN** an unnumbered chapter-1 file marked `front` because it precedes the first numbered
  chapter, and `chapter_number_overrides` giving it 1
- **THEN** its `kind` is `body`; a file before it whose title is front matter stays `front`

#### Scenario: Reconstruct over pages the old splitter duplicated
- **GIVEN** `method: reconstruct` and old split files in which page 252 appears in two files with
  identical text
- **THEN** page 252 appears once in the rebuilt text; when the two copies differ the book is
  refused (`error`, "page markers not strictly increasing") and nothing is written for it

#### Scenario: Reconstruct honours `exclude_files`
- **GIVEN** `method: reconstruct` and an excluded old whole-book duplicate `ch08_x.md`
- **THEN** none of its text appears in the reconstructed book

**Negative constraints**
- Every config entry MUST carry a `reason`; `title_check_skip`, `missing_chapters_ok` and
  `exclude_files` are only for a cause written in it (image-only title, chapter absent from the
  source, duplicate file), never to make a wrong split pass.
- The converter MUST NOT write or modify the config file.
- A book without an entry MUST be split exactly as without the config file.
- An entry MUST NOT be placed at a guessed position when no line matches.

### Requirement: Deep bookmarks become markdown headings when opted in

With `T2N_SPLIT_DEEP_HEADINGS=1`, after a bookmark split the system SHALL insert one line per
bookmark below the split levels into the chapter file containing that bookmark's page — level 3
→ `## <title>`, level ≥4 → `### <title>` — directly above the first line on that page whose
normalised text equals the normalised title, or equals it when joined with the next line; and
SHALL print `deep headings: <inserted>/<total> inserted`. Default OFF.

#### Scenario: Matched section title
- **GIVEN** bookmarks `[2, "Chapter 1 Gait", 3]`, `[3, "Overview", 3]`, and page 3 of
  `full_text.md` containing a line `Overview`
- **WHEN** the book is split with `T2N_SPLIT_DEEP_HEADINGS=1`
- **THEN** the chapter file contains `## Overview` directly above `Overview`, and `Overview`
  occurs in it exactly as often as without the flag

#### Scenario: Level 4
- **GIVEN** `[4, "Overview", 6]` and a line `Overview` on page 6
- **THEN** `### Overview` is inserted above it

#### Scenario: Title not found on its page
- **GIVEN** `[3, "Missing Title", 4]` with no such line on page 4
- **THEN** nothing is inserted for it and the summary reads `deep headings: 2/3 inserted` for
  the three deep bookmarks of this book

#### Scenario: Flag unset
- **GIVEN** any outline with level-3+ entries
- **WHEN** the book is split with the flag unset or `0`
- **THEN** every chapter file's bytes equal the 0.7.6 output

**Negative constraints**
- MUST NOT delete, reorder or edit an existing line; removing the inserted `## ` / `### ` lines
  restores the flag-off output exactly.
- MUST NOT change file boundaries or names.
- MUST NOT write to `full_text.md` or `ch00_Front_Matter.md`.
- MUST NOT insert a heading where the title does not match.

### Requirement: A resplit output root is marked as a `t2n-corpus/1` textbook corpus

`convert.py --resplit <corpus> --out <dir>` SHALL ensure `<dir>/corpus.json` exists with the
JSON object `{"format": "t2n-corpus/1", "kind": "textbook"}`. When `<dir>/corpus.json`
already exists it SHALL NOT be written, whatever its content.

#### Scenario: fresh output directory
- **GIVEN** `--out` names a directory with no `corpus.json`
- **WHEN** `--resplit` runs (with or without `--book`)
- **THEN** `<out>/corpus.json` parses to exactly `{"format": "t2n-corpus/1", "kind": "textbook"}`
- **AND** every other file the run writes is byte-identical to a run of 0.8.0

#### Scenario: an existing marker with the same content
- **GIVEN** `<out>/corpus.json` already holds the expected object (any whitespace)
- **WHEN** `--resplit` runs, including with `--force`
- **THEN** the file's bytes and mtime are unchanged and nothing is printed about it

#### Scenario: an existing marker with different content (failure case)
- **GIVEN** `<out>/corpus.json` holds anything else — another `kind`, another `format`, or
  text that is not JSON
- **WHEN** `--resplit` runs
- **THEN** the file's bytes are unchanged
- **AND** stderr carries one line starting `[resplit] corpus.json` that names the file and
  says it was left as is
- **AND** the exit code is the same as without the file (0 for a normal run)

#### Scenario: the source corpus carries its own corpus.json
- **GIVEN** `<corpus>/corpus.json` exists (any content) and `<out>` has none
- **WHEN** `--resplit` runs, with or without `--force`
- **THEN** `<out>/corpus.json` is the expected object, not a copy of the source's file

#### Negative constraints
- `corpus.json` is never written by `--batch-dir`, `--batch` or single-file conversion.
- An existing `<out>/corpus.json` is never overwritten, deleted, or rewritten to normalize it.

### Requirement: `post_convert.py --index` hands the corpus to ask-your-library and never indexes itself

`post_convert.py --index [--corpus DIR]` SHALL, after the resplit chain of the requirement
"`post_convert.py` keeps the corpus in step with the converted output", choose exactly one
route, in this order (`<root>` = `CORPUS_DIR`, which `--corpus DIR` sets):

1. `INDEXER_SCRIPT` set — print a deprecation warning to stderr that contains
   `INDEXER_SCRIPT is deprecated` and the text `corpus.py add`, then behave as 0.8.0 did
   (`<INDEXER_SCRIPT> --incremental`; `[skip]` when the path does not exist).
2. else `VAULT_SEARCH_DIR` set and `<VAULT_SEARCH_DIR>/server/corpus.py` exists — run
   `<python> <VAULT_SEARCH_DIR>/server/corpus.py add <root>`, where `<python>` is
   `VAULT_SEARCH_PY` when set, else the current interpreter; exit with that process's exit code.
3. else — run nothing, print `[skip]` and the exact command to run once ask-your-library is
   installed (`python <VAULT_SEARCH_DIR or path/to/ask-your-library>/server/corpus.py add <root>`),
   exit 0.

The full pipeline (no flag) runs the same route after verification, `INDEX.md` and the
figure registry. The coverage audit (`--audit`, and the audit step of the full pipeline) runs
only on route 1; on routes 2 and 3 it prints `[skip]` and that the audit is part of
`corpus.py add`.

#### Scenario: ask-your-library checkout configured
- **GIVEN** `INDEXER_SCRIPT` unset, `VAULT_SEARCH_DIR=<vs>` with `<vs>/server/corpus.py`
- **WHEN** `post_convert.py --index --corpus <root>` runs
- **THEN** exactly one subprocess runs, with argv `[<python>, <vs>/server/corpus.py, "add", <root>]`
- **AND** `post_convert.py` exits with that subprocess's exit code (0 → 0, 3 → 3)

#### Scenario: legacy INDEXER_SCRIPT
- **GIVEN** `INDEXER_SCRIPT=<script>` that exists (and `VAULT_SEARCH_DIR` set or not)
- **WHEN** `post_convert.py --index` runs
- **THEN** stderr contains `INDEXER_SCRIPT is deprecated` and `corpus.py add`
- **AND** exactly one subprocess runs, with argv `[<python>, <script>, "--incremental"]`
- **AND** `corpus.py` is not run

#### Scenario: nothing configured (failure case)
- **GIVEN** neither `INDEXER_SCRIPT` nor `VAULT_SEARCH_DIR` is set, and `<root>/corpus.json` exists
- **WHEN** `post_convert.py --index --corpus <root>` runs
- **THEN** no subprocess runs, stdout contains `[skip]` and the command
  `python "<path/to/ask-your-library>/server/corpus.py" add "<root>"`, exit 0

#### Scenario: nothing configured and the root is not a corpus yet (failure case)
- **GIVEN** as above, but `<root>/corpus.json` is absent (e.g. a `CORPUS_DIR` no book has been resplit into yet)
- **WHEN** `post_convert.py --index` runs
- **THEN** no subprocess runs, stdout contains `[skip]`, the command
  `--resplit "<root>" --out "<CORPUS_ROOT>"` and the command
  `python "<path/to/ask-your-library>/server/corpus.py" add "<CORPUS_ROOT>"`, exit 0

#### Scenario: VAULT_SEARCH_DIR is not an ask-your-library checkout (failure case)
- **GIVEN** `INDEXER_SCRIPT` unset, `VAULT_SEARCH_DIR=<d>` with no `<d>/server/corpus.py`
  (e.g. an old index cache dir)
- **WHEN** `post_convert.py --index` runs
- **THEN** no subprocess runs, stdout contains `[skip]`, the path `<d>` and the words
  `ask-your-library checkout`, exit 0

#### Negative constraints
- `post_convert.py` never imports `lancedb` or opens an index on routes 2 and 3.
- No route passes `--incremental` or `--book` to `corpus.py`.
- Route 2 never runs when `INDEXER_SCRIPT` is set.

### Requirement: A book that needs OCR while Surya is unavailable fails the batch

In `--batch-dir`, when a PDF is routed to OCR (`SCAN`, `FITZ-FAIL` or `FORCE-SURYA`, decided
exactly as before) and the Surya rung cannot run (`SURYA_VENV_PY` or `SURYA_ADAPTER` unset or
not an existing file), the system SHALL treat that book as FAILED: print one line starting
`  [FAIL] [<reason>] <rel>: FAILED` that names each missing piece and its env var
(`SURYA_VENV_PY`, `SURYA_ADAPTER`), add that line to the progress file's `errors`, leave the
book out of `completed`, continue with the next PDF, and exit 1 at the end of the run.

#### Scenario: scanned PDF, no Surya
- **GIVEN** a PDF with no text layer and `SURYA_ADAPTER` pointing at a missing file
- **WHEN** `convert.py --batch-dir <dir>` runs
- **THEN** stdout contains `[FAIL]` and `SURYA_ADAPTER`, the progress file's `completed` does
  not contain the PDF, its `errors` contains a line with `FAILED` and the PDF's name, and the
  exit code is 1

#### Scenario: the next run retries
- **GIVEN** the run above
- **WHEN** `--batch-dir` runs again (without `--force`), still without Surya
- **THEN** the book is reported FAILED again (not skipped as completed)

#### Scenario: born-digital books in the same run (failure isolation)
- **GIVEN** the scanned PDF plus a born-digital PDF in the same directory
- **WHEN** `--batch-dir` runs without Surya
- **THEN** the born-digital book is converted and marked completed exactly as before; only the
  exit code and the scanned book's bookkeeping differ

#### Negative constraints
- MUST NOT change which PDFs are routed to OCR.
- MUST NOT write any file under `OUTPUT_DIR/<book>/` for the failed book, and MUST NOT run the
  stale-chapter clear for it.
- A run in which no book needs OCR, or Surya is available, MUST exit 0 as before.

### Requirement: OCR pages can be split at a measured gutter (opt-in)

With `T2N_OCR_GUTTER=1`, `order_ocr_blocks()` SHALL first look for a measured gutter
(`find_ocr_gutter()`) and, when one is found, split the page's OCR blocks there; when none is
found, or the split at it is not trusted, it SHALL use the page-center split as before. With
`T2N_OCR_GUTTER` unset or not `1`, OCR output SHALL be byte-identical to 0.8.0.

`find_ocr_gutter()` scans x from 0.25 to 0.75 of the page width in steps of 0.0025 of the width.
A candidate is eligible when at most 0.15 of the blocks straddle it. Eligible candidates rank
by (1) fewest straddling blocks narrower than 0.30 of the width, (2) most text characters on
the lighter side, (3) nearest the page center; the result is the midpoint of the best-ranked
contiguous run, rejected when either side holds fewer than `COLUMN_MIN_LINES` (2) blocks.

#### Scenario: off-center boxed layout
- **GIVEN** OCR blocks with a left column ending at 0.38 of the width, a right column of eight
  lines and a banner over the right column, `T2N_OCR_GUTTER=1`
- **WHEN** the page is ordered
- **THEN** the gutter found is within 0.02 of the width of 0.38, and every left-column line
  comes before every right-column line

#### Scenario: single-column page (failure case)
- **GIVEN** twenty prose blocks spanning most of the width, `T2N_OCR_GUTTER=1`
- **THEN** no gutter is found and the order is top-to-bottom

#### Scenario: off by default
- **GIVEN** the off-center blocks above and `T2N_OCR_GUTTER` unset
- **THEN** the order equals 0.8.0's (page-center split)

#### Negative constraints
- MUST NOT change the fitz text path: `column_order_boxes()` called without `gutter_x` splits
  at the page center exactly as before.
- `T2N_COLUMN_SORT=0` still disables all column sorting, gutter included.

### Requirement: One Docling worker serves a whole batch run

With `T2N_DOCLING=1`, `--batch-dir` and `--batch` SHALL keep one live Docling worker per
process and pass it to every `convert_pdf()` call; the GPU lease `t2n_docling` SHALL be taken
once, just before the first worker is created, and held until process exit. When the worker
reports `disabled` (it gave up after repeated restart failures), the next book SHALL get a
freshly created worker under the same lease, as a per-book worker would have been. With
`T2N_DOCLING` unset, or with `T2N_TABLE_MERGE=1` (where `convert_pdf()` does not use Docling),
no worker and no lease are created. Converted output SHALL be identical to building a worker
per book.

#### Scenario: two books, Docling on
- **GIVEN** `T2N_DOCLING=1` and a batch of two PDFs
- **WHEN** the batch runs
- **THEN** the worker constructor is called once and both `convert_pdf()` calls receive it

#### Scenario: the shared worker disabled itself (failure case)
- **GIVEN** the shared worker's `status()` reports `disabled`
- **WHEN** the next book asks for the worker
- **THEN** the old worker is closed, a new one is created and returned, and no second lease is
  taken

#### Scenario: cross-page merge on
- **GIVEN** `T2N_DOCLING=1` and `T2N_TABLE_MERGE=1`
- **THEN** no worker is created and no lease is taken

#### Scenario: Docling off (failure case: nothing to share)
- **GIVEN** `T2N_DOCLING` unset
- **THEN** `convert_pdf()` receives `table_worker=None` and no lease is taken

#### Negative constraints
- A single-PDF conversion (`convert.py <pdf>`) MUST keep creating and closing its own worker.

### Requirement: `post_convert.py` keeps the corpus in step with the converted output

`post_convert.py` SHALL treat `CORPUS_DIR` (env; default `<repo>/corpus`; `--corpus DIR`
overrides) as the resplit target of `OUTPUT_DIR`:

- `resplit_plan()`: every top-level folder of `OUTPUT_DIR` holding a `.md` file (skipping
  `misc`, `scripts`, `figures_test`, `__pycache__`, `.drafts` and `.`-folders) is planned when
  `CORPUS_DIR/<book>/_manifest.json` is missing (plain), or older than the book's newest `.md`
  (forced rebuild). Up-to-date books are not planned.
- `run_resplit()`: each planned book is resplit with `--pdf-root` = the folder(s) of the PDFs
  named by its `full_text.md` `Source:` lines that exist, else the `T2N_PDF_ROOTS` folders
  (`os.pathsep`-separated; default `BOOKS_DIR`) that exist. Each root is walked at most once
  per run, and only when a lookup needs it (see "PDF roots are walked only on demand"). A book
  whose manifest is still missing afterwards is a failure.
- `ensure_figure_links()`: every `figures*` / `images` folder of a book (names containing
  `.bak` or `undo` excluded) is linked into `CORPUS_DIR/<book>/` (a junction on Windows, a
  symlink elsewhere); an existing entry is left alone; a link that cannot be made is a failure.

Flags: `--resplit-only` runs plan + resplit + links. `--index` runs them and then the 0.8.1
hand-off on `CORPUS_DIR`; `--backfill` is the hand-off alone. The default (no flag) runs
verify, `INDEX.md`, the figure registry, then what `--index` runs. Any failure SHALL be listed
after `[FAIL]` and the process SHALL exit 1 (or the hand-off's own non-zero code, if that is
the only failure). With `INDEXER_SCRIPT` set, `--index` and the default skip the resplit
chain and behave as in 0.8.1.

#### Scenario: plan
- **GIVEN** books `New` (no corpus manifest), `Reconverted` (md newer than its manifest),
  `Current` (manifest newer), `scripts`, and an empty folder
- **THEN** `resplit_plan()` returns `[("New", False), ("Reconverted", True)]`

#### Scenario: pdf roots
- **GIVEN** `full_text.md` whose `Source:` names an existing PDF in `pdfs/sub/`
- **THEN** the roots are `[pdfs/sub]`; with the PDF gone, they are the existing `T2N_PDF_ROOTS`

#### Scenario: figure links
- **GIVEN** `figures`, `figures_Part_I`, `figures.old.bak`, `figures.x_undo` in `OUTPUT_DIR/Book`
  and `CORPUS_DIR/Book` existing
- **THEN** exactly `figures` and `figures_Part_I` appear in `CORPUS_DIR/Book`, a file inside
  `figures` is readable through the link, a second run creates nothing and fails nothing, and
  no link is made for a book absent from `CORPUS_DIR`

#### Scenario: a resplit that leaves no manifest (failure case)
- **GIVEN** a planned book whose resplit errors
- **WHEN** `post_convert.py --resplit-only` runs
- **THEN** stdout lists `resplit: <book>` after `[FAIL]` and the exit code is 1

#### Negative constraints
- MUST NOT write under `OUTPUT_DIR` other than `INDEX.md` (the resplit guarantee applies).
- MUST NOT copy figure files into `CORPUS_DIR`.
- MUST NOT import any ask-your-library module.

### Requirement: An OCR'd book records its source PDF

`surya_ocr_pdf()` SHALL write `Source: `<pdf path>`` as the first line of `full_text.md`,
followed by a blank line, then the `<!-- page N -->` blocks as before — the same `Source:`
form the text-layer path writes. Routing (which PDFs go to OCR) and the OCR text are unchanged.

#### Scenario: OCR output
- **GIVEN** a PDF converted through the OCR path
- **THEN** line 1 of its `full_text.md` is exactly ``Source: `<the PDF path as passed>` ``
- **AND** every `<!-- page N -->` block is byte-identical to the previous release's

### Requirement: Library settings can live outside `BOOKS_DIR`

`books.json`, `already_converted.json`, `rename_skip.json` and `rename_fixes.json` SHALL be read
from `T2N_CONFIG_DIR` when set, else from `BOOKS_DIR` (the previous location).

A `books.json` value that is a list SHALL be an alias: `--batch <alias>` (exact, case-insensitive
match, checked before the substring match) converts every key it lists, in order; an alias
naming a key that is not a book is an error naming that key. `src` and `out` SHALL accept a
leading `~`.

#### Scenario: config dir set
- **GIVEN** `T2N_CONFIG_DIR=C` and `BOOKS_DIR=B`
- **THEN** the four files are read from `C`, none from `B`

#### Scenario: config dir unset
- **THEN** the four files are read from `BOOKS_DIR`, as before

#### Scenario: alias
- **GIVEN** `{"a_one": {...}, "a_two": {...}, "a": ["a_one", "a_two"]}`
- **WHEN** `--batch a` runs
- **THEN** the run converts `a_one` then `a_two`; `--batch one` still converts `a_one` alone

#### Scenario: broken alias (failure case)
- **GIVEN** `"x": ["a_one", "missing"]`
- **WHEN** `--batch x` runs
- **THEN** it stops with an error naming `missing` and converts nothing

### Requirement: PDF roots are walked only on demand

A `PdfIndex` SHALL NOT list any root at construction or in `combine()`. A root SHALL be walked
the first time a lookup needs the file-name / stem tables, and at most once per index. A unit
whose `Source:` PDF exists under a root SHALL resolve by path without any walk.

#### Scenario: every Source PDF present
- **GIVEN** a resplit of books whose `Source:` PDFs all exist
- **THEN** no PDF root is listed (`os.walk` is never called) and the output is unchanged

#### Scenario: a Source PDF gone
- **GIVEN** a unit whose `Source:` path no longer exists
- **THEN** each root is walked once, and the lookup finds the PDF by name as before

### Requirement: `T2N_REQUIRE_SOURCE_PDF=1` fails a book instead of searching for its PDF

With `T2N_REQUIRE_SOURCE_PDF=1` (default off), `post_convert.py`'s resplit SHALL refuse a planned
book when any of its `full_text.md` files has no `Source:` line, or names a PDF that does not
exist and that no single file of the same name under `T2N_PDF_ROOTS` replaces. Each such file
SHALL be printed as `[FAIL] resplit <book>: …` naming the file, the missing path when there is
one, and the fix (put the PDF back / copy it into `T2N_PDF_ROOTS` / edit the `Source:` line);
the book SHALL NOT be resplit (its corpus copy is left as it was) and counts as a failure
(exit 1).

#### Scenario: missing PDF (failure case)
- **GIVEN** `T2N_REQUIRE_SOURCE_PDF=1`, book `Gone` whose `Source:` PDF is missing, and no
  `Gone.pdf` under `T2N_PDF_ROOTS`
- **THEN** stdout carries `source PDF not found: <path>` and `Fix:`, `Gone` is not resplit,
  and the exit code is 1

#### Scenario: moved PDF found by name
- **GIVEN** the `Source:` PDF missing but a file of the same name under `T2N_PDF_ROOTS`
- **THEN** the book is resplit as before

#### Scenario: unset
- **GIVEN** `T2N_REQUIRE_SOURCE_PDF` unset
- **THEN** every planned book is resplit; a book with no PDF carries its existing split, as before

#### Negative constraints
- MUST NOT list any folder other than the `T2N_PDF_ROOTS` folders to make this decision, and
  those only when a `Source:` PDF is actually missing.

### Requirement: Guidelines are listed one by one in verification and INDEX.md

`verify_conversion()` and `update_index_md()` SHALL list each `OUTPUT_DIR/_Guidelines/<Name>/`
as its own entry named `_Guidelines/<Name>`, and SHALL NOT list `_Guidelines` itself or any
other top-level folder whose name starts with `_` or `.`.

#### Scenario: two guidelines and a book
- **GIVEN** `Book/`, `_Guidelines/G1/`, `_Guidelines/G2/` (each with `.md`), and `_figure_remap/`
- **THEN** `INDEX.md` has rows `Book`, `_Guidelines/G1`, `_Guidelines/G2` and no other

#### Negative constraints
- A book folder whose name does not start with `_` MUST be listed exactly as in 0.8.0.

### Requirement: Silent extraction failure is detected, not trusted

The system SHALL score each page for extraction failure that leaves plausible-looking text,
and route affected books to the OCR ladder rather than emitting the damaged text as if it
were correct.

Detection signals are per page: glyph-garble ratio, character density, font risk
(CID/Identity-H, PUA codepoints), and domain-pattern miss.

#### Scenario: Book with broken font encoding is routed to OCR
- **GIVEN** a PDF whose text layer decodes to garbled glyphs
- **WHEN** the book is converted during a `--batch-dir` run
- **THEN** the book is routed to the OCR ladder rather than written out as extracted text

#### Scenario: Sparse page count triggers the density check
- **GIVEN** a book where more than `OCR_EMPTY_PAGE_MAX` (0.35) of pages carry fewer than
  `OCR_MIN_CHARS_PER_PAGE` (200) characters
- **WHEN** the check runs
- **THEN** the book is flagged as an OCR candidate

#### Scenario: OCR routing decision is per book, not per page
- **GIVEN** a book where only some pages trip the detection signals
- **WHEN** the routing decision is made during a `--batch-dir` run
- **THEN** the **entire file** goes to OCR, not the tripped pages alone

**Negative constraints**
- The pipeline MUST NOT emit a book that failed detection as if extraction succeeded.
- Documentation MUST NOT describe the ladder as mixing engines page-by-page inside one
  book. Per-page routing is unimplemented; claiming it is a public-claim violation.

### Requirement: OCR is a ladder with the frontier model last

The system SHALL attempt OCR engines in a fixed order of increasing cost: Surya →
PaddleOCR-VL → local vision model → frontier vision as a last resort, and SHALL treat every
engine below the first as optional.

#### Scenario: No OCR engine is configured
- **GIVEN** `SURYA_VENV_PY` and `SURYA_ADAPTER` are unset
- **WHEN** a book trips the silent-failure check
- **THEN** the step is skipped with a reported reason and the run does not crash

#### Scenario: A different engine is plugged in
- **GIVEN** a script conforming to the adapter interface in `docs/surya-adapter.md`
- **WHEN** `SURYA_ADAPTER` points at it
- **THEN** it is used for that ladder rung with no change to `converter/convert.py`

**Negative constraints**
- The system MUST NOT use tesseract at any rung.
- Frontier vision MUST NOT be reached without the lower rungs having been attempted or
  explicitly unavailable.

### Requirement: Reading order survives multi-column layout

The system SHALL reconstruct true reading order on multi-column pages by clustering lines
into columns, and SHALL leave single-column pages byte-identical to trivial extraction.

A column boundary requires a line to clear the gutter by `COLUMN_TOL_FRAC` (0.05) of page
width, and each column requires at least `COLUMN_MIN_LINES` (2) lines before it is trusted.

#### Scenario: Two-column page is not interleaved
- **GIVEN** a two-column textbook page
- **WHEN** the page is extracted with column sort enabled (the default)
- **THEN** the left column's text appears in full before the right column's

#### Scenario: Exact fallback on single-column pages
- **GIVEN** a single-column page
- **WHEN** the page is extracted with column sort enabled
- **THEN** the output is byte-identical to the output with `T2N_COLUMN_SORT=0`

**Negative constraints**
- A garble check that inspects characters alone MUST NOT be treated as evidence that
  reading order is correct — interleaved columns pass any such check.

### Requirement: Every behaviour-changing flag has an off switch and a documented default

The system SHALL expose each optional conversion behaviour as a `T2N_*` environment
variable, and SHALL default to ON only for behaviours that correct demonstrably wrong
output.

#### Scenario: Correction behaviours default ON
- **GIVEN** no environment variables are set
- **WHEN** conversion runs
- **THEN** page-frame pseudo-table rejection, spanned-header collapse, the table gate,
  column sort, and the whole-book table check are all active

#### Scenario: Additive behaviours default OFF
- **GIVEN** no environment variables are set
- **WHEN** conversion runs
- **THEN** cross-page table merge (`T2N_TABLE_MERGE`) and the out-of-band review queue
  (`T2N_REVIEW_QUEUE`) are inactive

#### Scenario: Disabling a default-ON behaviour restores prior output
- **GIVEN** a behaviour documented as "restores byte-identical output when disabled"
- **WHEN** its flag is set to `0`
- **THEN** the markdown matches the output produced before that behaviour existed

**Negative constraints**
- A new default-ON behaviour MUST NOT be added without a measurement over the real corpus
  showing it corrects wrong output; "seems better" is not sufficient.
