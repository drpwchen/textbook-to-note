# Architecture

`textbook-to-note` turns your own PDF textbooks into a searchable, AI-navigable
knowledge base, and then into fully-cited, structured notes in your personal
knowledge vault (Obsidian, Logseq, or plain markdown folders). It is designed
to be deployed and operated by an AI coding agent (see `AGENTS.md`), not by
hand-running scripts.

## Design philosophy

1. **Local-first.** Every heavy-lifting step — PDF parsing, OCR, embedding,
   image cropping — runs on your machine with open-source tools. No PDF page
   and no textbook image is ever sent to a hosted LLM by default.
2. **Zero LLM tokens for extraction.** Converting a 600-page textbook to
   markdown, indexing it for semantic search, and cropping a figure out of a
   page are all deterministic, scriptable operations. The AI agent only
   spends tokens on the part that actually needs judgment: writing the note.
3. **Deterministic QC gates before any AI vision fallback.** Every pipeline
   stage that could plausibly fail silently (a garbled OCR page, a
   mis-cropped figure) has a cheap, rule-based check placed *before* the
   expensive vision-model or frontier-model fallback. Fallbacks exist, but
   they are the last resort, not the first move — see `docs/ocr-ladder.md`
   and the figure QC gate below.
4. **Deconstruct, don't append.** When new content meets an existing note,
   the system re-derives structure from scratch and re-slots content into it,
   rather than bolting new paragraphs onto old ones. See
   `workflows/note-writing.md` Phase 4.

## Pipeline overview

```
┌──────────────┐     ┌───────────────────┐     ┌────────────────────────┐
│  PDF / EPUB  │ --> │  converter/        │ --> │  markdown corpus        │
│  textbooks   │     │  (fitz/pandoc/OCR) │     │  (searchable by agent)  │
└──────────────┘     └───────────────────┘     └───────────┬─────────────┘
                                                              │
                              ┌───────────────────────────────┴───────────────┐
                              │                                                │
                    ┌─────────▼─────────┐                          ┌───────────▼───────────┐
                    │ optional semantic  │                          │  AI note-writing        │
                    │ index (LanceDB +   │◄────── query ───────────│  workflow                │
                    │ bge-m3 via ollama) │                          │  (workflows/note-        │
                    └────────────────────┘                          │  writing.md)             │
                                                                     └───────────┬───────────┘
                                                                                 │
                                                                     ┌───────────▼───────────┐
                                                                     │ figure harvest          │
                                                                     │ (figures/, QC-gated)    │
                                                                     └───────────┬───────────┘
                                                                                 │
                                                                     ┌───────────▼───────────┐
                                                                     │ structured note         │
                                                                     │ written to your vault   │
                                                                     └────────────────────────┘
```

## Components

### 1. `converter/` — PDF/EPUB → markdown

Converts each textbook (PDF or EPUB) into one markdown file per chapter plus
a `full_text.md`, with:

- `<!-- page N -->` markers so any passage can be traced back to a PDF page
- Cleaned, de-hyphenated text
- Tables extracted as markdown tables. Optionally (`T2N_TABLE_MERGE=1`),
  tables that run past a page break are stitched into one markdown table —
  a table ending near a page bottom is joined to a geometrically-matching
  table at the top of the next page (same column count / x-edges, no
  intervening heading), a repeated header row is deduped, and a
  `<!-- table continues from page N -->` comment preserves page-traceability.
  Default OFF (exact-fallback: byte-identical output when disabled).
- Page-frame pseudo-tables rejected. A content frame plus the rule under a
  running header gives `pdfplumber` enough intersecting edges to return one
  "table" spanning the page body with 1 column, into whose single cell every
  word on the page is dumped. Candidates of 1 column whose largest cell
  exceeds `TABLE_FRAME_CELL_CHARS` (500) or whose bbox covers
  `TABLE_FRAME_AREA_FRAC` (0.50) of the page are dropped, leaving a
  `<!-- ⚠️ page-frame pseudo-table rejected on page N (reason) -->` comment so
  the removal is auditable. Column count is the only gate, so a table whose
  columns pdfplumber actually resolved is never at risk. Default ON
  (`T2N_TABLE_FRAME_REJECT=0` restores the previous behaviour).
- Whole-book table-failure detection. If `pdfplumber` parses 0 pages while
  `fitz` opens the file, or the book yields 0 tables despite
  `BOOK_ZERO_TABLE_MIN_CAPTIONS` (10) table captions, or pages raised errors
  during the table pass, a `> [!warning]` block is written at the top of the
  markdown and the warnings are surfaced in the batch report. Pure detection —
  extraction is unchanged (`T2N_BOOK_TABLE_CHECK=0` to disable).
- `<!-- REF: Fig. X.Y → see PDF page N -->` markers wherever the text
  references a figure or table, so downstream steps know where to look in
  the source PDF without re-scanning it

This step costs 0 LLM tokens — it is pure Python (`fitz` / `pdfplumber` /
`pandoc` for EPUB). Pages that fitz cannot read reliably (scans, broken
fonts) are automatically routed up the OCR ladder — see `docs/ocr-ladder.md`
for the detection heuristics and fallback order.

Output lives in a flat markdown corpus, one folder per book:

```
<OUTPUT_DIR>/                 # default ./output (shared/config.py OUTPUT_DIR)
├── Author_Title_Edition_Year/
│   ├── ch01_Introduction.md
│   ├── ch02_....md
│   └── full_text.md
└── Another_Book/
    └── ...
```

Single-file conversions without an explicit output path land in the same
`<book>/full_text.md` layout, so a later `--batch-dir` run recognizes them
as already converted.

This corpus is **for the agent's own reference**, not meant to be read by a
human — it's the substrate the note-writing workflow searches.

**Corpus maintenance.** Four utilities operate on a whole corpus after a batch
rather than on a single conversion — a post-batch triage report
(`triage_report.py`), a post-hoc fake-table cleanup for markdown produced before
the frame-reject fixes (`strip_fake_tables.py`), a ligature-expansion pass for
corpora converted before ligatures were expanded (`expand_ligatures.py`), and a
table-recovery pass for scan-only books that routed through OCR
(`scan_table_pass.py`). See [`corpus-maintenance.md`](corpus-maintenance.md).

### 2. Semantic index (optional)

For large corpora, a local vector index (LanceDB + a local embedding model
served through ollama, e.g. `bge-m3`) enables concept-level search across
every converted book, in addition to plain-text `grep`. This is optional:
grep-only works fine for a handful of books; the index pays off once you have
dozens.

**No indexer ships in this repo.** `converter/post_convert.py` provides the
wiring only: `run_indexer()` invokes `INDEXER_SCRIPT --incremental`, and
`audit_index_coverage()` compares book folders against rows in the LanceDB
directory at `VAULT_SEARCH_DIR`, backfilling via `INDEXER_SCRIPT --book
<name>`. With `INDEXER_SCRIPT` unset both steps print `[skip]` and return
success. To turn semantic search on, point `INDEXER_SCRIPT` at the indexer
from the companion repo
[vault-search](https://github.com/drpwchen/vault-search), or at any script
of your own implementing those two flags. `lancedb` is likewise a manual
install, not part of `requirements.txt`.

Search strategy used by the note-writing workflow:
- Known keyword → grep first (fastest, exact, 0 tokens)
- Concept exploration → semantic search (finds related content under
  different wording)
- Both, for precision + coverage

### 3. AI note-writing workflow

See `workflows/note-writing.md` for the full phase-by-phase spec. In short:
the agent drafts a note **blind** from the textbook alone (to avoid
inheriting a stale existing note's structure), builds the section skeleton
before expanding prose, optionally enriches with pluggable external sources,
harvests figures, and only then merges with anything already in your vault —
by deconstructing the old note into labeled fragments and re-slotting them
into the new structure, never by appending.

### 4. Figure extraction (`figures/`)

Figures are extracted **on demand, one at a time**, not batch-dumped per
book. When the note-writing workflow decides a figure is needed, it calls a
single entrypoint that:

1. Tries a fast path (a previously-extracted crop), QC-checks it
2. Falls back to a deterministic geometric match (caption bbox ↔ image bbox)
   against the source PDF page — this is the default and preferred path
3. Only if that fails, retries with a local vision model suggesting a crop
   region
4. Only if that also fails, escalates to a frontier-model vision read as the
   final fallback

Every candidate crop is checked by cheap deterministic rules (whitespace
fill, text bleed-in) before being accepted — never trusted on the vision
model's say-so alone. In fact no vision model runs in the QC chain at all:
a model-backed check gave the same crop different verdicts on repeat runs
(issue #16), which is not something a gate — or a per-crop log signal — can
be built on. Crop-worthiness is judged downstream by a frontier-tier
classification pass over every crop. See
`skills/figure-remap/SKILL.md` for the full QC-gate and result contract.

## Why this order (fitz → deterministic match → local vision → frontier vision)

Every fallback step in this system is strictly more expensive (in latency,
GPU/CPU cost, or LLM tokens) than the step before it, and each step exists
specifically to catch a *silent* failure mode of the step before — a page
that "looks" readable but isn't, a caption region that "looks" matched but
crops the wrong raster. Putting a deterministic, 0-token check ahead of every
fallback means the AI agent only pays for vision tokens on the small residual
of cases that genuinely need judgment, and it never trusts an extraction
without first trying to verify it mechanically.
