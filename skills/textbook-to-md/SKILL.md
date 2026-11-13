---
name: textbook-to-md
description: >
  Convert PDF/EPUB textbooks to searchable markdown files for an AI agent's own reference.
  Use this skill whenever: (1) the user asks to convert a textbook/PDF chapter to markdown,
  (2) you need to search textbook content and no markdown version exists yet,
  (3) batch-converting a set of reference books into a knowledge base.
  This is a 0-token local conversion — no vision model needed.
---

# Textbook-to-Markdown Converter

> This skill calls scripts in your clone of the textbook-to-note repo. At
> install time, replace `{REPO}` below with the absolute path of the clone.

## Purpose

Convert PDF or EPUB textbooks into **searchable markdown** that the agent can
grep/read directly, eliminating the need for PDF-library extraction at every
query.

Output lives outside the note vault, at the path configured by
`OUTPUT_DIR` in `shared/config.py` (default `./output/`), and is
for the agent's consumption, not for the user's reading.

> **Figures are on-demand, not pre-extracted.** This skill produces markdown
> text only. Figures are extracted one at a time *when a note needs them*,
> via the `figure-remap` skill's entrypoint (QC-gated). Do not batch-extract
> a whole book's figures into a `figures/` folder — that approach does not
> scale and is unnecessary since the on-demand path already handles it. A
> legacy `figures/` folder may exist for books converted before this design;
> new conversions are markdown-only.

## When to use

- User explicitly asks to convert a textbook or chapter
- You need to search textbook content and want to avoid per-query PDF
  re-parsing overhead
- Building up a knowledge base from a personal library of reference books
- Before starting work on a new topic, convert the relevant chapters first

## Quick reference

```bash
# Single file
python {REPO}/converter/convert.py "path/to/chapter.pdf"

# Single file with custom output and label
python {REPO}/converter/convert.py "path/to/chapter.pdf" "output.md" --book-label "Author Title 2e — Ch32"

# Batch-dir: convert ALL PDFs in a directory tree (auto chapter split; source PDFs are never modified)
python {REPO}/converter/convert.py --batch-dir "path/to/your/textbook/folder"

# Force re-convert (ignore existing md)
python {REPO}/converter/convert.py --batch-dir "path/to/your/textbook/folder" --force

# Force OCR for ALL PDFs in batch-dir (bypass the text-extraction path entirely).
# Use when the text layer "looks" healthy but quality is actually bad (OCR-overlay
# scans, some digitized reprints) — auto-detection won't trigger because the text
# layer passes the shallow check.
python {REPO}/converter/convert.py --batch-dir DIR --force --force-surya

# EPUB → markdown (the 2nd arg is a FOLDER, not a .md file)
python {REPO}/converter/convert.py "path/to/book.epub" "<OUTPUT_DIR>/Author_Title_2e_2022"
```

Batch mode skips files whose markdown already exists and is newer than the
source PDF. Batch-dir mode saves progress to `batch_progress.json` — if
interrupted, re-running resumes where it stopped. `--batch-dir` also picks
up `.epub` files automatically.

### EPUB support
- Uses `pandoc` (`epub → gfm`). EPUBs are reflowable, so there are no
  `<!-- page N -->` markers; files carry a `<!-- SOURCE: epub -->` marker
  instead.
- If pandoc emits proper heading markup, that drives the chapter split. If
  the EPUB is CSS-styled with no semantic headings (common), headings are
  rebuilt from the TOC link table + body anchors before splitting.
- Output is a **folder** of `chNN_*.md` + `full_text.md` (same shape as a PDF
  book) — the single-file 2nd argument is the output **directory**, not a
  `.md` path.
- Requires `pandoc` on `PATH`. Figures are not extracted from EPUB (the
  on-demand figure flow is PDF-only).

### Batch-dir features
- Recursively finds all PDFs, auto-outputs to `<OUTPUT_DIR>/{PDF_stem}/`
  (`OUTPUT_DIR` from `shared/config.py`; default `./output`)
- Produces `full_text.md` (complete with `<!-- page N -->` markers) plus a
  chapter split
- **Chapter-splitting priority**: PDF bookmarks → pattern detection →
  force-split (every 30 pages)
- **Auto-routes OCR-needed PDFs** to the local OCR engine (GPU). Two
  triggers: (1) a page is scan-only (near-zero extractable characters); (2)
  fitz-silent-failure is detected (see `docs/ocr-ladder.md`). Falls back to
  skip-with-explanation if the OCR environment is missing. Output is
  `full_text.md` with page markers, and a chapter split is *attempted* on the
  OCR'd text too (numbered-heading patterns often survive OCR); it is
  best-effort and simply yields no chapters when heading detection fails

## Output structure

```
<OUTPUT_DIR>/                       ← default ./output (shared/config.py)
├── Author_Title_Edition_Year/
│   ├── ch01_Chapter_Title.md      ← conversion produces md only
│   ├── ch02_....md
│   ├── full_text.md
│   └── figures/                   ← LEGACY ONLY — not produced by conversion
└── Another_Book/                  ← single-file conversions use the same <book>/full_text.md layout
```

Figure-registry generation (`figure_registry.json`) is an optional external
hook, not a shipped script — set the `FIGURE_REGISTRY_SCRIPT` env var to a
generator script if you have one; if unset, `post_convert.py` skips that
step.

==Conversion writes `.md` files only.== A `figures/` subdirectory is legacy —
present only on books converted before the on-demand switch. New conversions
never create one; figures are pulled on demand by `figure-remap`.

## What the converter produces

Each markdown file contains:
- **Page markers**: `<!-- page X -->` at every page boundary, to map content
  back to the source PDF
- **Cleaned text**: control characters removed, broken/hyphenated words
  rejoined, grep-searchable
- **Tables**: extracted as markdown tables (a low content-cell threshold is
  used to avoid discarding real tables)
- **Figure/table reference markers**: when the text mentions a figure or
  table (`Fig. 32.1`, `Table 31.2`, etc.), an HTML comment
  `<!-- REF: Fig. 32.1 → see PDF page X -->` is inserted so the agent knows
  where to look in the original PDF

## Searching converted textbooks

Two search methods:

### 1. Keyword search (grep) — exact match, 0 tokens
```bash
grep -r "your search term" "$OUTPUT_DIR"      # default ./output
```

### 2. Semantic search (optional) — concept match
If the user has handed the corpus to vault-search (Profile C: `convert.py
--resplit` → vault-search's `corpus.py add` — see `docs/setup-details.md`
Step 5), a `textbook_search` tool becomes available for concept-level
queries that don't depend on exact wording.

### Search strategy
- **Known keyword** → grep first (fastest, exact)
- **Concept/topic exploration** → semantic search (finds related content
  even with different phrasing)
- **Both** → grep for precision, semantic search for coverage

## Post-conversion pipeline

After converting new books, run the post-pipeline:
```bash
# Full pipeline: verify quality + refresh index.md + refresh figure registry + vault-search hand-off
python {REPO}/converter/post_convert.py

# Or individual steps:
python {REPO}/converter/post_convert.py --verify   # check quality only
python {REPO}/converter/post_convert.py --resplit-only  # re-split new / re-converted books into CORPUS_DIR
python {REPO}/converter/post_convert.py --index    # that, then vault-search corpus.py add CORPUS_DIR
```

The pipeline does **not** extract figures — it only verifies page markers,
refreshes `index.md` + `figure_registry.json` from whatever figures already
exist, and (optionally) hands a `--resplit` corpus root to vault-search's
`corpus.py add`, which indexes and audits it. This repo never indexes
anything itself; with `VAULT_SEARCH_DIR` unset the hand-off prints the
command and `[skip]`. Figure extraction is never a batch post-step.

Missing page markers do not block indexing by design — EPUBs (reflowable)
and some vector-glyph PDFs legitimately lack `<!-- page N -->` and index with
page metadata = 0.

### Figures — do NOT batch-extract
No pre-extraction step. A whole-book batch figure dump is retired and should
not be run on new books. When a note needs a figure, `figure-remap` extracts
that single figure on demand with QC. See "Using figures in notes" below.

## Chapter splitting (batch mode)

### Chapter detection patterns (in priority order)
1. `Chapter N` / `CHAPTER N` — standard
2. `Part N` / `PART N` — with roman numerals (`Part III`)
3. `Section N` / `SECTION N` / `Unit N`
4. Numbered headings: `1 Introduction`, `23 Shoulder` (digit + title-case
   text)

### Running header deduplication
Many textbooks repeat `Chapter N Title` as a running header on every page.
Only count the **first occurrence** of each unique chapter number — track a
`seen_chapters` set and skip duplicates.

### Force-split rules
- No chapter breaks detected AND >200 pages → split every 30 pages
  (`pages_0001-0030.md`)
- Single output file >500K words → re-split (likely missed chapter breaks)

### Output naming
- Folder name = PDF filename without `.pdf` (use an
  `Author_Title_Edition_Year` convention)
- Chapter files: `ch01_Chapter_Title.md`, `ch00_Front_Matter.md`
- Forced splits: `pages_0001-0030.md`

## Quality verification

After conversion, check:
- **Total words < 500** for a book → likely scanned; batch-dir auto-routes
  to OCR. If it ran the text-extraction path anyway, the OCR-trigger
  heuristic missed and needs tuning
- **Single file > 500K words** → chapter detection failed, re-split needed
- **0 chars extracted from sample pages** → pure scan; batch-dir handles
  this automatically. Single-file mode does not auto-route — use batch-dir
  on a temp folder for OCR needs
- **Garbled glyphs** (high char count, mostly private-use-area codepoints)
  → CID-encoded font without a Unicode map; detected by the silent-failure
  check, auto-routes to OCR in batch-dir

Always cite with year when referencing converted content:
`(Author 2e, 2021)`, not just `(Author)`.

## Using figures in notes (on-demand)

When writing or supplementing a note that needs a figure (anatomy,
classification, imaging, algorithm), extract **that one figure on demand** —
never batch-dump a book. The markdown's
`<!-- REF: Fig. 5.1 → see PDF page 42 -->` markers tell you which figure
exists and what PDF page it's on; hand that to the gate.

### On-demand workflow — single entry point
Call the `figure-remap` skill's **public entrypoint** (`figure_remap.py
extract` — not the internal gate script). The entrypoint runs deterministic
geometric matching by default and returns a stable contract:

```bash
python {REPO}/figures/figure_remap.py extract \
  --book "{Book}" \
  --fig-id "5-1" \
  --caption "<caption text from the REF marker / md>" \
  --out "path/to/your/vault/attachments/Fig_5-1_{BookShort}.jpeg" \
  --pdf "<source PDF path>" \
  --page {1-indexed PDF page from the REF marker}
```

Contract: `{status: pass|fail|escalate, match_quality: exact|uncertain|failed,
hard_fail, file, fig_id, reason, qc_degraded, qc_skipped}` — those eight keys
exactly (`figures/figure_remap.py` `CONTRACT_KEYS`; the validator raises on any
extra or missing key, so branching on the engine's internal `match_method` is
not just discouraged, it is impossible).
`status:fail` (exit 1) is a deterministic miss — a correct refusal, not a
wrong crop; fix `--page`, escalate to vision, or leave a `<!-- TODO -->`.
Read the real caption to confirm the figure depicts what you intend.

On `pass` (exit 0), embed the `--out` path (`result.file`) in the note:
```markdown
![[Fig_5-1_{BookShort}.jpeg|400]]
*Fig 5.1 — description (Author 2e, p.42)*
```

When writing a fresh note via the note-writing workflow (see
`workflows/note-writing.md`), figure harvest is Phase 3.5 — don't call the
gate manually there, the workflow does it. The manual call above is for
ad-hoc figure needs outside that workflow. Full fallback ladder + per-book
calibration: see the `figure-remap` skill.

### When to include figures
- **Always**: anatomy diagrams, classification systems, algorithm
  flowcharts, key reference images
- **Skip**: decorative images, author photos, generic stock photos
- **Ask the user** if unsure whether a figure adds value

### Figure registry
`figure_registry.json` (produced by the post-conversion pipeline) records
each book's figure status. A status of "not extracted" or "lazy-only" is
the **expected normal state** — it does not mean a batch extraction has to
run first; the on-demand gate handles extraction from the PDF regardless.

### Naming convention for note attachments
`{FigID}_{BookShort}.{ext}` — e.g. `Fig_5-1_AuthorName.jpeg`,
`Fig_32-1_AuthorName.jpeg`. Avoids filename collisions across books in your
attachments folder.

## Known limitations

- **Image-based tables** (scanned/embedded as pictures): the table extractor
  can't read these. They show up as mostly-empty tables and get filtered
  out. Check the original PDF at the page number shown in `<!-- page X -->`.
- **Merged cells**: the table extractor sometimes splits or duplicates
  merged cells. Still readable, but may have redundant columns.
- **CJK OCR**: text extraction works well for CJK text in native (born-
  digital) PDFs. For scanned CJK PDFs, fall back to the sanctioned OCR
  ladder — see `docs/ocr-ladder.md`.
- **`dump_all` figures**: books where caption detection failed get
  page-based filenames (`page_0042.jpeg`) without captions. Still usable,
  but you must identify content by reading the image.
- **Windows subprocess OCR encoding**: any OCR engine run as a subprocess
  must read stdout in bytes mode and decode explicitly as UTF-8 with error
  replacement — do not rely on the platform's default text-mode decoding.

## Conversion for other PDFs

The script accepts any PDF, not just your priority set. For ad-hoc
conversions:

```bash
python {REPO}/converter/convert.py "path/to/any.pdf" "path/to/output.md" --book-label "Book Name — Chapter"
```

If no output path is specified, output goes to `OUTPUT_DIR/<pdf name>/full_text.md` — the same layout `--batch-dir` uses, so a later batch run skips it as already converted.
