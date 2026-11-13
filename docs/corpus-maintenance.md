# Corpus maintenance — operating on a whole corpus, not one conversion

The main pipeline converts one book at a time. Four utilities in `converter/` work at the
other scale: over an existing corpus, or over a batch's output after it finishes. They exist
because problems that are invisible in a single conversion — a book that quietly lost every
table, false-positive tables left behind by an older converter, a scan that came out as text
with no tables, a corpus made unsearchable by unexpanded ligatures — only become visible when
you look across the whole shelf.

All three are **read-first**: the two that rewrite files default to a dry run and print what
they would change before you pass `--apply`.

## `triage_report.py` — which books can I trust?

A large batch produces one manifest entry per book and a log too big to read. Every signal you
need to say *do not trust this book* is already in the manifest — bytes per page, table
content-loss counts, the QC flag rate, dosage-table flags, chapter-split ratios — but nothing
assembles them, so a bad conversion gets found only when a human happens to look at that
particular book. This is that missing step.

```
python converter/triage_report.py MANIFEST.json [--corpus DIR] [--json OUT.json]
```

It sorts every book into one of four tiers, each with the reason and the evidence:

| Tier | Meaning |
|------|---------|
| `BLOCKED` | Unusable as a source — near-empty output, or the table pass never ran |
| `REVIEW` | Usable, but a specific claim can be wrong (table data loss, high flag rate, flagged dosage values) — check the PDF before citing |
| `ODD` | Structurally strange but probably fine (chapter split way off, table count collapsed) |
| `OK` | Nothing tripped |

**On the thresholds.** They are calibrated to the distribution of a reference corpus, not
picked from intuition, and they sit near the **p90** of that distribution — so a flagged book is
genuinely an outlier among its peers, not just above some round number. An absolute cutoff like
"flag rate > 0.40" sounds strict but flags ~84% of a real clinical corpus, which is the same as
flagging nothing. The constants at the top of the file record the measured median / p90 / max for
each signal. **Re-derive them whenever the table pipeline changes** — a pipeline that gets better
makes yesterday's p90 flag half the shelf again.

## `strip_fake_tables.py` — clean up a corpus converted by an older build

If you converted books before the page-frame pseudo-table rejection shipped (see
[`architecture.md`](architecture.md) § converter), the markdown still contains false-positive
tables. Two shapes cover nearly all of them:

- **single-column** — `| THE DRUGS vviiii |`: front matter, a table-of-contents line, or a
  running header caught as a one-cell "table". In one book, 33 of 104 "tables" were this.
- **mostly-empty** — a diagram's axis labels sliced by the ruling detector into a grid that is
  >70% empty cells.
- **word-split** — a small label cluster (≤4 rows) where words are split across cell boundaries
  (`| Hyster | esis area |`) and ≥40% of cells are empty. Real tables split words across columns
  too, so this fires only on the small, sparse clusters that diagrams produce.

```
python converter/strip_fake_tables.py --dry-run              # corpus-wide report, writes nothing
python converter/strip_fake_tables.py --apply                # rewrite in place
python converter/strip_fake_tables.py --apply --only-completed PROGRESS.json
```

It is **content-preserving by construction**: a demoted block's cell text is re-emitted as plain
lines, so a grep for any word inside it still hits — only the pipe scaffolding and its QC marker
go. A block the book itself numbered (`Table 15.4`) is **vetoed** and always kept, because the
author calling it a table outranks any shape heuristic — this protects legitimate one-column list
tables and real tables whose header row was sliced (exactly the fingerprint the word-split rule
otherwise hunts for). `--only-completed` / `--exclude-pending` keep it from racing a batch that is
still writing the same folders.

## `expand_ligatures.py` — make a corpus searchable after a ligature fix

A PDF text layer emits typographic ligatures (`ﬁ`, `ﬂ`, `ﬀ`, ...) as single codepoints, so a book
that typeset `speciﬁc` as one glyph does not match a search for `specific`. The converter's
`clean_text()` expands them, but a corpus converted before that fix carries the raw glyphs and is
silently unsearchable for any word that contains one.

```
python converter/expand_ligatures.py --dry-run              # corpus-wide report, writes nothing
python converter/expand_ligatures.py --apply                # rewrite in place
```

It applies the **same** ligature map the converter uses (imported from `convert.py`, so the two
never drift) and nothing else — not line-joining, not soft-hyphens — so the only bytes that change
are the ligature glyphs, and the pass is **idempotent** (a book already converted with the fix
reports zero replacements and is left byte-identical). It carries the same `--only-completed` /
`--exclude-pending` batch-racing guards as `strip_fake_tables.py`.

## `scan_table_pass.py` — recover tables from an OCR'd scan

A scan-only PDF has no text layer, so the pdfplumber table pass finds nothing and the book routes
to OCR, which returns running text and **zero tables** — every table in the book is silently lost.
Docling reads a rendered page *image*, so it can find tables on a scan; running it over every page
of an 800-page book is expensive, so this pass is **caption-targeted**. It reads the OCR markdown's
`<!-- page N -->` markers, finds the pages whose text names a table (`Table 5.1`), and asks Docling
only about those pages plus each successor (for tables that continue over a break).

```
python converter/scan_table_pass.py --book NAME --pdf PATH [--dry-run] [--max-pages N]
python converter/scan_table_pass.py --from-progress PROGRESS.json --books-json BOOKS.json
```

Requires the Docling rung (`T2N_DOCLING`; see [`architecture.md`](architecture.md)). Docling loads
a model onto the GPU — if you gate GPU jobs with a lease or semaphore, wrap the call in it. No
ligature repair is applied on this path: a scanned page has no text layer to vouch for a repair.

## When a resplit book fails validation

`post_convert.py` (default run, `--index`, `--resplit-only`, `--backfill`) reads
`<corpus>/_resplit_report.json`. A book recorded there as `valid: false` is **held alone**: its
folder moves to `<corpus>/_held/<book>/`, which ask-your-library skips, so it is out of the
index while **every other book is handed off as usual**. Next to it the run writes
`_held/<book>/_fix_request.md` — a self-contained brief for an AI agent or a person: what
failed (with the validator's details), which config keys address each check, the exact
commands, the done criterion and the rules. The run lists the book under `[FAIL]` and exits 1.

A held book is re-split automatically once its markdown or `_split_config.json` changes, and
comes back into the corpus (the held copy is removed, links inside it unlinked, never followed)
as soon as it validates.

The loop for one book:

1. Read `_held/<book>/_fix_request.md`. If the converted text itself is broken (two book
   pages interleaved line by line, garbled characters), re-convert the book — no split config
   fixes that. Otherwise the usual fix is an entry
   for the book in `<corpus>/_split_config.json` (every entry needs a `reason` that says what is
   in the book, not that the check failed):

   | Check | What it means | Config keys that address it |
   |---|---|---|
   | `chapter_gap` | a chapter number missing between the lowest and highest | `missing_chapters_ok` (really absent from the source); `chapter_regex` / `chapter_level` / `chapter_number_overrides` (misread); `chapter_numbers: false` (book not numbered) |
   | `chapter_order` | a number lower than an earlier one | `chapter_number_overrides`, `drop_titles` |
   | `chapter_unmapped` | an outline `Chapter N` no file carries | `split_levels` / `chapter_level`; `missing_chapters_ok` |
   | `toc_count` | chapter count ≠ `toc_chapters` | fix `toc_chapters`, or the split level |
   | `chapter_numbers_missing` | no file has a number, though one was expected | `chapter_regex` / `chapter_level`; `chapter_numbers: false` |
   | `title_mismatch` | a file's title is not on its first pages | `page_offset`; `anchor_titles: true` or a `toc` with pages; `retitle`; `title_check_skip` (title is an image) |
   | `page_overlap` / `page_gap` | files share pages / pages with text fall between files | `page_offset`, `anchor_titles`, `toc`, `body_start_page`; `method: reconstruct` + `exclude_files` for a duplicated old file |
   | anything else | manifest and files disagree | re-split with `--force`; if it persists, it is a bug — report it |

2. Re-split that book and validate it (the fix request has these with the book's real paths):

   ```
   python converter/convert.py --resplit <OUTPUT_DIR> --out <CORPUS_DIR> --pdf-root <dir> --book "<book>" --force
   python converter/split_manifest.py --validate <CORPUS_DIR> --book "<book>"
   ```

3. Run `post_convert.py --index` again: the book is back in the index and its `_held/` copy is gone.

**Chapter numbers the validator cannot see.** A split can be internally consistent and still
number every chapter wrong (all off by a constant). Each book resplit in a run also gets
`chapter_check.py`: every numbered file's first two pages are searched for its own number
(`Chapter n`, a bare `n` line, `n` + a title word, `n.x` figure/table numbering). The counts are
printed; a book with more files *contradicting* their number than confirming it gets `[WARN]`
and the first few files to look at. This never fails the run: in-text cross-references
("see Chapter 17") make a few files contradict in many correct books. Run it over a whole
corpus with `python converter/chapter_check.py <CORPUS_DIR> [--book B] [--sample N] [--json FILE]`.
