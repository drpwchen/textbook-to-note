# conversion — delta for split-manifest-contract

## ADDED Requirements

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

#### Scenario: Title check skipped by config
- **GIVEN** a section-divider file whose title is printed only as an image, and
  `title_check_skip: {"<file>": "<reason>"}` in the book's config entry
- **THEN** the book passes, with a `title_check_skipped` warning naming the file and the reason

**Negative constraints**
- The validator MUST NOT modify any file.
- A pattern / carried book with chapter numbers and no `toc_chapters` is a warning
  (`toc_unverified`), not a pass of `toc_count`.
