"""
textbook-to-note converter
Converts PDF/EPUB textbooks to searchable markdown.

Usage:
    python convert.py <pdf_path> <output_path> [--book-label "Book Name"]
    python convert.py --batch-dir <dir>            # recursively convert every PDF/EPUB under dir
    python convert.py --batch <key>                 # convert a book defined in books.json (see below)

Named-book batches (--batch) are driven by an optional BOOKS_DIR/books.json file, e.g.:
    {
      "example_book": {
        "label": "My Textbook 6th Edition",
        "src": "./books/MyTextbook",
        "out": "./output/MyTextbook_6e",
        "glob": "*.pdf"
      }
    }
If books.json doesn't exist, --batch prints the expected format and exits.
"""

import fitz
import pdfplumber
import io
import os
import re
import shutil
import sys
import time
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.config import BOOKS_DIR, OUTPUT_DIR, SURYA_VENV_PY, SURYA_ADAPTER, SKIP_FOLDERS, SEP_CLASS

# Out-of-band table-review trigger (T2N_REVIEW_QUEUE=1). Pure-stdlib (os/re), always importable.
from review_queue import review_queue_enabled, review_reasons, format_review_marker

# Optional Docling table rung (T2N_DOCLING=1). Imported defensively: docling_tables only needs
# fitz on this side (the heavy Docling stack lives in its own venv and is reached by subprocess),
# but a missing/broken module must degrade to the pdfplumber-only path rather than break every
# conversion. _DOCLING_IMPORT_ERROR is surfaced once per book in the conversion report.
try:
    from docling_tables import (DoclingTableWorker, qc_flags, format_trace_marker,
                                repair_ligatures, format_repair_marker)
    _DOCLING_IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover - exercised only on a broken install
    DoclingTableWorker = None
    qc_flags = format_trace_marker = None
    repair_ligatures = format_repair_marker = None
    _DOCLING_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

# === Config ===
OUTPUT_BASE = OUTPUT_DIR
BOOKS_JSON = BOOKS_DIR / "books.json"
PROGRESS_FILE = OUTPUT_BASE / "batch_progress.json"

# Book folders/output dirs that already have a high-quality conversion and should be
# skipped by --batch-dir. Populate via BOOKS_DIR/already_converted.json (a JSON list
# of output-folder names) if you maintain a curated library; empty by default.
ALREADY_CONVERTED_FILE = BOOKS_DIR / "already_converted.json"


def load_already_converted() -> set:
    if ALREADY_CONVERTED_FILE.exists():
        try:
            return set(json.loads(ALREADY_CONVERTED_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def load_books() -> dict:
    """Load named-book definitions from BOOKS_DIR/books.json. See module docstring for format."""
    if not BOOKS_JSON.exists():
        return {}
    raw = json.loads(BOOKS_JSON.read_text(encoding="utf-8"))
    books = {}
    for key, spec in raw.items():
        books[key] = {
            "label": spec["label"],
            "src": Path(spec["src"]),
            "out": Path(spec["out"]),
            "glob": spec.get("glob", "*.pdf"),
            "prefix": spec.get("prefix", ""),
        }
    return books


# === Text cleaning ===
# Typographic ligatures that PDF text layers emit as single codepoints.
# Left as-is they silently break search: "specific" finds 0 hits in a corpus
# that contains 116 occurrences of "speciﬁc". Mapped explicitly rather than via
# NFKC so scientific notation (μ, −, superscripts) is left untouched.
LIGATURES = {
    'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl',
    'ﬃ': 'ffi', 'ﬄ': 'ffl', 'ﬅ': 'st', 'ﬆ': 'st',
}
LIGATURE_RE = re.compile('[' + ''.join(LIGATURES) + ']')


def clean_text(text: str) -> str:
    """Remove control chars, soft hyphens, ligatures, join broken lines."""
    # Remove control characters (keep \n and \t)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    # Expand typographic ligatures so the corpus stays greppable
    text = LIGATURE_RE.sub(lambda m: LIGATURES[m.group()], text)
    # Remove soft hyphens
    text = text.replace('\xad', '')
    # Join hyphenated line breaks: "hyphen-\nated" → "hyphenated"
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    # Join mid-sentence line breaks: "end of line\nstart" → "end of line start"
    text = re.sub(r'([a-z,;])\n([a-z])', r'\1 \2', text)
    return text


# === Figure/Table reference detection ===
# Publishers typeset figure numbers with whichever dash their house style uses. Katzung 16e
# uses en dashes throughout ("Figure 33–3"): 717 en dashes vs 8 ASCII hyphens, so matching
# only [-.] found 14 of them and left figure extraction blind for the whole book.
# The set itself lives in shared/config.py so the figure stage matches the same dashes (#10).
SEP = SEP_CLASS
FIG_TABLE_PATTERNS = [
    # English patterns
    rf'(?:Fig(?:ure)?\.?\s*\d+{SEP}\d+[A-Za-z]*(?:\s*(?:and|to|,)\s*[A-Za-z])?)',
    rf'(?:(?:TABLE|Table)\s*\d+{SEP}\d+[A-Za-z]*)',
    rf'(?:Box\s*\d+{SEP}\d+)',
    # Chinese patterns
    rf'(?:圖\s*\d+{SEP}\d+)',
    rf'(?:表\s*\d+{SEP}\d+)',
]
FIG_TABLE_RE = re.compile('|'.join(FIG_TABLE_PATTERNS))


def annotate_fig_refs(text: str, page_num: int) -> tuple[str, int]:
    """Find figure/table references and insert HTML comment markers."""
    refs_found = set()

    def replacer(match):
        ref = match.group(0).strip()
        refs_found.add(ref)
        return f'{ref} <!-- REF: {ref} → see PDF page {page_num} -->'

    annotated = FIG_TABLE_RE.sub(replacer, text)
    return annotated, len(refs_found)


# === Two-column reading order (Improvement 1) ===
# fitz's default get_text() can interleave the two columns of a two-column layout
# (left-line-1, right-line-1, left-line-2, ...) because it reads roughly top-to-bottom.
# The garbled-glyph silent-failure detector does NOT catch this (the glyphs are fine, only
# their order is wrong), so we reorder line-by-line: all left-column lines, then all right,
# separated by any full-width blocks (headers/titles that span both columns).
#
# Kill-switch: set T2N_COLUMN_SORT=0 to disable and always use plain get_text() (default on).
#
# Heuristic constants (documented at definition site):
COLUMN_TOL_FRAC = 0.05   # gutter tolerance as a fraction of page width; a line must clear the
                         # page center by this much to count as belonging to one column, and a
                         # line straddling center by this much on both sides is "full-width".
COLUMN_MIN_LINES = 2     # each column needs at least this many lines before we trust that the
                         # page is really two-column (guards against a stray short line).


def column_order_boxes(items: list, page_w: float, page_x0: float = 0.0,
                       min_items: int = 4) -> list | None:
    """Reorder positioned text boxes into two-column reading order, or None when the page is
    single-column / ambiguous (every caller then falls back to its own previous behaviour).

    `items` are `(x0, y0, x1, y1, payload)` tuples in ONE coordinate space; the payload is
    opaque. Two callers share this: `column_sorted_text()` passes fitz dict-mode *lines*, and
    the OCR path passes an engine's *layout blocks*. They differ only in granularity, and the
    geometry argument — which side of the gutter does this box sit on — is identical, so it
    lives here once rather than being re-derived (and re-tuned) per rung.

    `min_items` differs by caller because the granularities differ: four fitz lines is a
    trivially low bar, four OCR layout blocks is already most of a page.
    """
    if len(items) < min_items:
        return None

    center = page_x0 + page_w / 2.0
    tol = page_w * COLUMN_TOL_FRAC

    full, left, right = [], [], []
    for it in items:
        x0, _, x1, _, _ = it
        if x0 < center - tol and x1 > center + tol:
            full.append(it)              # spans both columns → header/title/full-width para
        elif (x0 + x1) / 2.0 < center:
            left.append(it)
        else:
            right.append(it)

    # Need two genuine clusters, or it's not a two-column page.
    if len(left) < COLUMN_MIN_LINES or len(right) < COLUMN_MIN_LINES:
        return None
    # Gutter integrity: left boxes must end before center and right boxes start after it
    # (with tolerance). Overlap across the center means the split is ambiguous → fall back.
    if max(l[2] for l in left) > center + tol:
        return None
    if min(r[0] for r in right) < center - tol:
        return None

    # Emit in bands split by full-width boxes: for each band, left column top-to-bottom then
    # right column top-to-bottom, with the full-width separator between bands. A box's band
    # is the count of full-width boxes above it, so top full-width blocks (e.g. a heading) and
    # their bands come first.
    full.sort(key=lambda l: l[1])

    def band(it):
        return sum(1 for f in full if f[1] <= it[1])

    ordered = []
    for bi in range(len(full) + 1):
        ordered.extend(sorted((l for l in left if band(l) == bi), key=lambda l: l[1]))
        ordered.extend(sorted((r for r in right if band(r) == bi), key=lambda r: r[1]))
        if bi < len(full):
            ordered.append(full[bi])
    return ordered


def column_sorted_text(page) -> str | None:
    """Return page text reordered into two-column reading order, or None when the page is
    single-column / ambiguous (caller then falls back to plain get_text() → byte-identical to
    the pre-change behavior). Operates at dict-mode line granularity: fitz merges same-baseline
    left+right text at the block level, so lines are the reliable unit for column separation.
    """
    if os.environ.get("T2N_COLUMN_SORT", "1") == "0":
        return None

    d = page.get_text("dict")
    lines = []  # (x0, y0, x1, y1, text)
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:  # skip image blocks
            continue
        for ln in blk.get("lines", []):
            txt = "".join(s.get("text", "") for s in ln.get("spans", []))
            if not txt.strip():
                continue
            bb = ln["bbox"]
            lines.append((bb[0], bb[1], bb[2], bb[3], txt))

    ordered = column_order_boxes(lines, page.rect.width, page.rect.x0)
    if ordered is None:
        return None
    return "\n".join(l[4] for l in ordered)


# === Table-pass gating (Improvement 2) ===
# pdfplumber.extract_tables() is the runtime hot spot on big books (it re-parses page geometry).
# Most textbook pages have no table at all, so we cheaply pre-screen each page with fitz (already
# open) and only hand pdfplumber the pages that plausibly contain a table. Skipped pages never
# trigger pdfplumber's per-page parse (the .pages[i] access is what costs the time).
#
# Kill-switch: set T2N_TABLE_GATE=0 to restore always-on behavior (run pdfplumber on every page).
#
# Heuristic constants:
TABLE_RULE_MIN = 2         # a ruled table has at least this many horizontal AND vertical rulings
TABLE_RULE_MIN_LEN = 10.0  # ignore ruling segments shorter than this (pt) — stray marks, ticks
# Three-line ("booktabs") tables common in academic books have horizontal rulings only (top rule,
# midrule, bottom rule) and no verticals. Signature: >= this many WIDE horizontal rulings, each
# spanning at least TABLE_HRULE_MIN_WIDTH_FRAC of the page width (≈ the text-column width). The
# ">= 3 wide rules" bar rejects a lone header underline or a single hairline separator.
TABLE_HRULE_MIN_COUNT = 3
TABLE_HRULE_MIN_WIDTH_FRAC = 0.40

# Table caption keywords. Default covers English "Table"/"Tables"/"Tab." (abbrev.) and CJK 表.
# Whole-word bounds on the ASCII terms avoid "comfortable"/"notable"; CJK 表 needs no boundary.
# Extra literal keywords for other languages (e.g. Tabelle, Tableau, Tabla) can be appended via
# env T2N_TABLE_KEYWORDS as a comma-separated list, e.g. T2N_TABLE_KEYWORDS="Tabelle,Tableau".
_TABLE_KW_BASE = [r'\btables?\b', r'\btab\.', '表']
_TABLE_KW_CACHE: dict[str, "re.Pattern"] = {}


def _table_keyword_re() -> "re.Pattern":
    """Compiled caption-keyword regex, base terms plus any env T2N_TABLE_KEYWORDS, cached per env."""
    extra = os.environ.get("T2N_TABLE_KEYWORDS", "")
    if extra not in _TABLE_KW_CACHE:
        parts = list(_TABLE_KW_BASE)
        for kw in extra.split(","):
            kw = kw.strip()
            if kw:
                parts.append(re.escape(kw))
        _TABLE_KW_CACHE[extra] = re.compile("|".join(parts), re.IGNORECASE)
    return _TABLE_KW_CACHE[extra]


def page_has_table_candidate(fitz_page, page_text: str) -> bool:
    """Cheap fitz-only pre-check: does this page plausibly contain a table? True if the page text
    mentions a table caption keyword (Table/Tab./表/…), OR the page's vector drawings form a grid
    (>= TABLE_RULE_MIN horizontal AND vertical rulings), OR they form a three-line table
    (>= TABLE_HRULE_MIN_COUNT wide horizontal rulings, no verticals needed). Gates pdfplumber.
    """
    if _table_keyword_re().search(page_text):
        return True
    wide_min = fitz_page.rect.width * TABLE_HRULE_MIN_WIDTH_FRAC
    h = v = wide_h = 0
    for d in fitz_page.get_drawings():
        for it in d.get("items", []):
            op = it[0]
            if op == "l":  # line segment: (x0,y0)-(x1,y1)
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) < 1.0 and abs(p1.x - p2.x) >= TABLE_RULE_MIN_LEN:
                    h += 1
                    if abs(p1.x - p2.x) >= wide_min:
                        wide_h += 1
                elif abs(p1.x - p2.x) < 1.0 and abs(p1.y - p2.y) >= TABLE_RULE_MIN_LEN:
                    v += 1
            elif op == "re":  # rectangle: contributes one horizontal + one vertical rule pair
                r = it[1]
                if r.width >= TABLE_RULE_MIN_LEN:
                    h += 1
                    if r.width >= wide_min:
                        wide_h += 1
                if r.height >= TABLE_RULE_MIN_LEN:
                    v += 1
            if (h >= TABLE_RULE_MIN and v >= TABLE_RULE_MIN) or wide_h >= TABLE_HRULE_MIN_COUNT:
                return True
    return False


# === Table extraction ===
def _clean_row(row) -> list[str]:
    """Clean one raw table row (list of cell values) to display strings."""
    return [
        (clean_text(str(cell)).replace("\n", " ").strip() if cell is not None else "")
        for cell in row
    ]


# A category/section header that the extractor broadcast across every column of a wide grid
# reads as a data row whose non-empty cells are all the byte-identical, sentence-length string
# (e.g. "Corticosteroids: Used to reduce inflammation." repeated across 9 columns). A real data
# row never repeats one long string across every column, so this signature is unambiguous. Dense
# drug×attribute references (e.g. rehab pharmacology books) hit it on ~half their tables.
TABLE_HEADER_COLLAPSE_MIN_COLS = 3    # need at least this many identical non-empty cells
TABLE_HEADER_COLLAPSE_MIN_LEN = 15    # ...each at least this long (a label, not a "++" rating)


def _collapse_spanned_header(cleaned: list[list[str]]) -> int:
    """In-place: re-cast any row whose >=3 non-empty cells are the identical >=15-char string as
    a single header cell (text in column 0, the rest blanked). Returns the number of rows changed.
    Structural only — it never moves a value between rows, so it cannot create a misbinding."""
    n = 0
    for row in cleaned:
        nonempty = [c for c in row if c.strip()]
        if (len(nonempty) >= TABLE_HEADER_COLLAPSE_MIN_COLS
                and len(set(nonempty)) == 1
                and len(nonempty[0]) >= TABLE_HEADER_COLLAPSE_MIN_LEN):
            text = nonempty[0]
            for i in range(len(row)):
                row[i] = text if i == 0 else ""
            n += 1
    return n


def _table_to_md(table) -> str | None:
    """Render one raw table (list of rows of cell values) to a markdown table string, or
    None when the table is too small / too empty to keep. This is the single source of
    truth for table-cell cleaning + markdown layout, shared by the plain per-page path
    (extract_tables_md) and the cross-page merge path, so an un-merged table is byte-for-byte
    identical however it was produced."""
    if not table or len(table) < 2:
        return None

    cleaned = [_clean_row(row) for row in table]

    # Fix: collapse category/section headers the extractor spanned across every column into a
    # single header cell. Default ON (corrects wrong output, like the frame/furniture rejects);
    # set T2N_TABLE_HEADER_COLLAPSE=0 to restore the byte-identical pre-change output.
    if os.environ.get("T2N_TABLE_HEADER_COLLAPSE", "1") != "0":
        _collapse_spanned_header(cleaned)

    # Filter: keep tables with >5% content cells (low bar — prefer to keep)
    content_cells = sum(1 for row in cleaned for cell in row if cell.strip())
    total_cells = sum(len(row) for row in cleaned)
    if total_cells == 0 or content_cells / total_cells < 0.05:
        return None

    # Normalize column count
    max_cols = max(len(r) for r in cleaned)
    if max_cols == 0:
        return None
    for row in cleaned:
        while len(row) < max_cols:
            row.append("")

    # Build markdown table
    lines = []
    header = cleaned[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in cleaned[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


# === Fix 1: page-frame pseudo-table rejection ===
# Page-decoration rectangles (typically a running-header box plus a full-page content frame) give
# pdfplumber >= 2 horizontal and >= 2 vertical intersecting edges, so find_tables() returns ONE
# table whose bbox is the whole page body with rows=2, cols=1 — and every word on the page lands in
# that single cell, in raw left-to-right order. Three different things arrive through that door,
# all of them defective output:
#   A. a REAL multi-column table collapsed into 1 column, its columns interleaved line by line.
#      This is the worst failure in the corpus: the caption and every cell value survive, so it
#      reads as clean citable data, but the row<->column binding is destroyed and nothing signals
#      it (a sensitivity can end up spliced into the middle of another test's description).
#   B. ordinary prose, a figure, or a title page fabricated as a table.
#   C. a legitimate single-column boxed list, which a 1-column markdown table reproduces faithfully
#      — and also entirely redundantly, since a 1-column table encodes no column bindings at all.
# All three are dropped. Measured on 34 rendered pages across 10 books (see wp3-fixes-report.md):
# 19 Class A, 9 Class B, 6 Class C; and 100% of Class C / 97% of Class A distinct tokens are
# already present in the page prose render_page_text() emitted immediately above, so the drop
# removes a misleading duplicate rather than content. Class C is the reason the predicate is not
# tightened further: bbox coverage does NOT separate A from C (measured A 0.13-0.73 of page area,
# C 0.09-0.47), so requiring "bbox covers most of the page" would spare 6 harmless boxed lists at
# the cost of leaving 13 of 19 Class A collapses in place.
#
# Kill-switch: set T2N_TABLE_FRAME_REJECT=0 to restore the old (defective) behavior. Defaults ON,
# unlike T2N_TABLE_MERGE — this is a bug fix to wrong output, not a new capability.
TABLE_FRAME_MAX_COLS = 1       # column count at/below which a table encodes no structure
TABLE_FRAME_CELL_CHARS = 500   # a single cell this long is a page-body dump, not a table cell
TABLE_FRAME_AREA_FRAC = 0.50   # ...or the bbox alone already covers this much of the page


def _content_col_count(rows) -> int:
    """How many columns actually CONTAIN content, as opposed to how many pdfplumber ruled.
    Contributed by @Alan9981 (PR #1): a candidate whose text all sits in one column encodes no
    column bindings no matter how many empty columns were ruled beside it, so the normalized
    count lets that case walk straight past a `cols <= 1` bail-out."""
    return len({ci for r in rows for ci, c in enumerate(r) if c and str(c).strip()})


def page_frame_reject_reason(rows, bbox, page_w: float, page_h: float) -> str | None:
    """Reason string if this candidate table is a page-frame pseudo-table that should be discarded,
    else None. Only ever fires on tables that resolve to TABLE_FRAME_MAX_COLS columns or fewer
    EITHER by normalized count or by content-bearing count, so a table whose columns pdfplumber
    actually resolved and filled is never at risk."""
    if os.environ.get("T2N_TABLE_FRAME_REJECT", "1") == "0":
        return None
    if not rows:
        return None
    n_cols = max((len(r) for r in rows), default=0)
    if os.environ.get("T2N_TABLE_FRAME_CONTENT_COLS", "1") == "0":
        n_content = n_cols                      # narrow kill-switch: pre-PR#1 behavior
    else:
        n_content = _content_col_count(rows)
    if n_cols > TABLE_FRAME_MAX_COLS and n_content > TABLE_FRAME_MAX_COLS:
        return None
    shape = ("single column" if n_cols <= TABLE_FRAME_MAX_COLS
             else f"content in {n_content} of {n_cols} columns")
    max_chars = max((len(str(c)) for r in rows for c in r if c), default=0)
    if max_chars > TABLE_FRAME_CELL_CHARS:
        return f"page frame: {shape}, {max_chars}-char cell"
    if bbox and page_w > 0 and page_h > 0:
        x0, top, x1, bottom = (float(v) for v in bbox)
        area = ((x1 - x0) * (bottom - top)) / (page_w * page_h)
        if area >= TABLE_FRAME_AREA_FRAC:
            return f"page frame: {shape}, bbox covers {area:.0%} of page"
    return None


# === Running-header / footer pseudo-table rejection ===
# A second family of fabricated tables, mechanically distinct from the page-frame one above.
# Page FURNITURE — a running-header band, a shaded chapter-title bar, an e-reader "Back" nav bar —
# is drawn as nested or stacked rectangles. Those rectangles intersect, so find_tables() returns a
# small table sitting entirely inside the top (or bottom) margin band, which then harvests whatever
# text falls in it: the running head, the folio, and — where the furniture overlays the text block —
# the first line or two of body prose, interleaved with the furniture's own words.
#
# It escapes page_frame_reject_reason() by construction: it is not a 1-column page dump. Measured
# column counts are 2-3, the largest cell is ~125 chars, and the bbox covers ~4% of the page, so all
# three of that predicate's branches miss it.
#
# Measured (faketable-rootcause.md): 47 books / 1,313 sampled pages / 241 tables that survive
# production today. 67 (27.8%) lie entirely inside a 0.10 band, spread over 5 books; each was read
# as a rendered PNG against its extracted markdown, and every one was furniture — a running head
# ("Section 2 Therapy", "FURTHER READING 19"), a chapter-title bar, or a box title — never a table.
# The drop costs no content: distinct-token retention in the page prose that render_page_text()
# emitted immediately above is 100% on every case measured (the single exception is a token the
# furniture itself corrupted, "FiBnakc" for "Fink", whose correct form is in the prose).
#
# Both constants come from the measured distribution rather than from taste:
#   * BAND_FRAC — the highest furniture table ends at 0.097 of page height and the next candidate
#     above it starts at 0.135, a clean 0.038-wide gap; 0.10 sits inside that gap. It is deliberately
#     NOT reused from TABLE_MERGE_MARGIN_FRAC (0.08): that constant answers a different question
#     ("is this heading body text?"), and 0.08 would clear the worst book (Steffens, 0.076) by only
#     5% of the band.
# A text-volume guard ("...and holds under N characters") was tried and REJECTED. It is inert on the
# 47-book sample (largest furniture table there = 125 chars) but it defeats the very worst case:
# Steffens' nav bar overlays the text block, so its pseudo-table swallows the first two lines of body
# prose and reaches ~366 chars — a cap tight enough to be a meaningful guard (200) left 7 of 8
# sampled Steffens pages unfixed. Geometry alone is both sufficient and safer: the top/bottom tenth
# of a page is margin, so a table lying ENTIRELY inside it is furniture by construction, and a real
# table reaching into the body is untouched no matter how little text it holds.
#
# Kill-switch: set T2N_TABLE_FURNITURE_REJECT=0 to restore the old (defective) behavior. Defaults ON,
# like T2N_TABLE_FRAME_REJECT — this corrects output that is currently wrong, so default-OFF would
# preserve the bug.
TABLE_FURNITURE_BAND_FRAC = 0.10   # band a table must lie ENTIRELY within to count as furniture


def page_furniture_reject_reason(rows, bbox, page_h: float) -> str | None:
    """Reason string if this candidate table is a running-header/footer pseudo-table that should be
    discarded, else None. Fires only when the bbox lies ENTIRELY inside the top or bottom furniture
    band, so any table that reaches into the page body is never at risk."""
    if os.environ.get("T2N_TABLE_FURNITURE_REJECT", "1") == "0":
        return None
    if not rows or not bbox or not page_h or page_h <= 0:
        return None
    top, bottom = float(bbox[1]), float(bbox[3])
    band = TABLE_FURNITURE_BAND_FRAC * page_h
    if bottom <= band:
        where = "running-header"
    elif top >= page_h - band:
        where = "running-footer"
    else:
        return None
    chars = sum(len(str(c)) for r in rows for c in r if c)
    return f"{where} band only, {chars} chars, spans {top / page_h:.0%}-{bottom / page_h:.0%} of page height"


# === Fix 3: sideways (rotated) text pages ===
# A landscape mega-table is often printed sideways on a portrait page: the PAGE is /Rotate 0, but
# every glyph carries a rotated text matrix (upright=False, matrix ≈ (0, s, -s, 0)). fitz reads such
# a page correctly, so the page prose is fine — but pdfplumber orders words by (top, x0) on the
# assumption that text is upright, so a line whose reading direction runs UP the page comes back
# character-reversed ("periodontitis" → "sititnodoirep") with its columns scrambled. The markdown
# then looks like a populated table and greps clean, which makes it the worst kind of silent
# failure: the data is present, wrong, and unfindable. Observed on Carranza's Clinical
# Periodontology 12e, TABLE 6-3 (PDF p.157–384, 228 pages of gene-association data).
#
# Two independent guards, because they fail differently:
#   1. REPAIR — a page whose text is overwhelmingly sideways is re-parsed through an in-memory
#      one-page copy with /Rotate set so the glyphs stand upright. pdfplumber then sees ordinary
#      horizontal text, and both its word ordering and its ruling geometry come out right.
#   2. DETECT — after extraction, a table whose tokens match the page's own text layer far better
#      REVERSED than forward is rejected through the normal pseudo-table channel. This covers what
#      repair cannot reach (mixed upright/sideways pages, an unexpected glyph orientation) and is
#      what guarantees the pipeline never emits reversed cells silently.
#
# Kill-switch: T2N_SIDEWAYS=0 disables both guards (restores the pre-fix output).
SIDEWAYS_MIN_CHARS = 40        # below this a page has too few glyphs to judge orientation
SIDEWAYS_UPRIGHT_MAX = 0.20    # page counts as sideways when at most this fraction is upright
REVERSED_MIN_TOKENS = 6        # need this many one-way tokens before calling a table reversed
REVERSED_RATIO = 3.0           # reversed matches must beat forward matches by this factor

_REV_TOKEN_RE = re.compile(r"[A-Za-z]{4,}")


def sideways_enabled() -> bool:
    return os.environ.get("T2N_SIDEWAYS", "1") != "0"


def page_sideways_rotation(plumber_page) -> int | None:
    """The /Rotate value that would stand this page's text upright, or None if it is already upright
    (or too sparse, or too mixed to call). A glyph's text matrix (a, b, c, d, e, f) advances along
    (a, b): upright text is (s, 0); text reading UP the page is (0, +s) and needs a 90° clockwise
    turn; text reading DOWN is (0, -s) and needs 270°."""
    try:
        chars = plumber_page.chars
    except Exception:
        return None
    if len(chars) < SIDEWAYS_MIN_CHARS:
        return None
    upright = up_dir = down_dir = 0
    for c in chars:
        if c.get("upright"):
            upright += 1
            continue
        m = c.get("matrix")
        if not m or len(m) < 2:
            continue
        if m[1] > 0:
            up_dir += 1
        elif m[1] < 0:
            down_dir += 1
    if upright / len(chars) > SIDEWAYS_UPRIGHT_MAX:
        return None
    if up_dir == down_dir == 0:
        return None          # non-upright but no usable direction (e.g. flipped, not rotated)
    return 90 if up_dir >= down_dir else 270


def reversed_text_reject_reason(rows, page_text: str) -> str | None:
    """Reject a table whose cell text matches the page's own text layer far better REVERSED than
    forward — the signature of sideways glyphs read bottom-to-top. The page's fitz text is the
    oracle, so this needs no word list and makes no language assumption; a table can only be
    rejected on evidence present in the source PDF itself.

    Judged per ROW as well as over the whole table. A landscape table is often imposed sideways on
    a page that is otherwise upright — a rotated header strip above upright body rows, or a page
    that already carries `/Rotate 90` so only some of its glyphs come back non-upright. Such a page
    is mixed, so the repair guard cannot stand it upright; and aggregating this check over the whole
    table lets the upright body's forward tokens outvote the reversed header, so that row reaches
    the markdown anyway. One reversed row is enough to reject, because every body cell under a
    reversed header is bound to a column label that was read backwards and out of order. Rejecting
    loses no content: the page prose, which fitz read correctly, is emitted either way."""
    if not page_text or not rows:
        return None
    ref = page_text.lower()
    t_fwd = t_rev = 0
    worst_row = None                  # (rev, fwd) of the most clearly reversed single row
    for row in rows:
        r_fwd = r_rev = 0
        for cell in row:
            if not cell:
                continue
            for tok in _REV_TOKEN_RE.findall(str(cell)):
                t = tok.lower()
                if t == t[::-1]:
                    continue          # a palindrome carries no direction
                in_f, in_r = t in ref, t[::-1] in ref
                if in_f and not in_r:
                    r_fwd += 1
                elif in_r and not in_f:
                    r_rev += 1
        t_fwd += r_fwd
        t_rev += r_rev
        if r_rev >= REVERSED_MIN_TOKENS and r_rev >= r_fwd * REVERSED_RATIO:
            if worst_row is None or r_rev > worst_row[0]:
                worst_row = (r_rev, r_fwd)
    if t_rev >= REVERSED_MIN_TOKENS and t_rev >= t_fwd * REVERSED_RATIO:
        return (f"cell text reads reversed against the page text layer ({t_rev} tokens match only "
                f"when reversed vs {t_fwd} forward) — sideways/rotated glyphs mis-ordered by the "
                f"table pass")
    if worst_row is not None:
        r_rev, r_fwd = worst_row
        return (f"one row reads reversed against the page text layer ({r_rev} tokens match only "
                f"when reversed vs {r_fwd} forward in that row) — a sideways header strip on an "
                f"otherwise upright page, so the body cells below it are bound to column labels "
                f"that were read backwards")
    return None


class SidewaysReparse:
    """Serves an upright stand-in for pages whose text is printed sideways.

    `substitute()` returns the (pdfplumber page, fitz page) the table pass should actually use: the
    originals for an ordinary page, or a rotated one-page copy when the page is sideways. Both
    stand-ins come from the SAME rotated bytes, so the geometry `_gather_page_tables()` reads off
    the fitz page matches the pdfplumber page it is pairing it with. The stand-in stays open until
    the next `substitute()` or `release()` — each page is visited exactly once, so one live copy at
    a time is enough."""

    def __init__(self):
        self._doc = None
        self._plumber = None
        self.repaired_pages = []
        self.last_rotation = None      # rotation applied to the page just handed out, else None

    def substitute(self, plumber_page, doc, index):
        self.release()
        self.last_rotation = None
        if not sideways_enabled():
            return plumber_page, doc[index]
        rot = page_sideways_rotation(plumber_page)
        if not rot:
            return plumber_page, doc[index]
        try:
            one = fitz.open()
            one.insert_pdf(doc, from_page=index, to_page=index)
            one[0].set_rotation((doc[index].rotation + rot) % 360)
            data = one.tobytes()
            one.close()
            self._doc = fitz.open("pdf", data)
            self._plumber = pdfplumber.open(io.BytesIO(data))
        except Exception:
            # Repair is best-effort. On failure the original page is used and the reversed-text
            # detector below still stops the defective table from being emitted.
            self.release()
            return plumber_page, doc[index]
        self.repaired_pages.append((index + 1, rot))
        self.last_rotation = rot
        return self._plumber.pages[0], self._doc[0]

    def release(self):
        for obj in (self._plumber, self._doc):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._plumber = self._doc = None


def extract_tables_md(plumber_page, page_text: str | None = None) -> tuple[list[str], list[str]]:
    """Extract tables from a pdfplumber page. Returns (markdown tables, rejection reasons).
    find_tables() is used rather than extract_tables() only to get each table's bbox — pdfplumber
    defines extract_tables() as exactly [t.extract() for t in find_tables()], so the extracted rows
    are identical. `page_text` is the page's fitz text, used only as the oracle for the
    reversed-text check (Fix 3); omitting it just skips that check."""
    md_tables, rejects = [], []
    page_w = float(plumber_page.width or 0)
    page_h = float(plumber_page.height or 0)
    check_reversed = sideways_enabled()
    for table in plumber_page.find_tables():
        rows = table.extract()
        md = _table_to_md(rows)
        if not md:
            continue
        bbox = getattr(table, "bbox", None)
        reason = (page_frame_reject_reason(rows, bbox, page_w, page_h)
                  or page_furniture_reject_reason(rows, bbox, page_h)
                  or (reversed_text_reject_reason(rows, page_text) if check_reversed else None))
        if reason:
            rejects.append(reason)
            continue
        md_tables.append(md)
    return md_tables, rejects


# === Improvement 3: cross-page table merging ===
# Textbook tables often run past a page break. When enabled (T2N_TABLE_MERGE=1), a table that
# ends near the bottom of page N and is continued by a table at the top of page N+1 whose column
# geometry matches is stitched into one markdown table. A `<!-- table continues from page N+1 -->`
# comment is emitted after the merged table so page-traceability survives.
#
# Kill-switch: default OFF (T2N_TABLE_MERGE unset / !=1). When off, the table path is the plain
# per-page extract_tables_md() emission → byte-identical to the pre-change output (same exact-
# fallback discipline as column-sort and the table-gate).
#
# Detection (a continuation = ALL of):
#   * the upper table's bbox bottom is within TABLE_MERGE_BOTTOM_FRAC of the page height from the
#     bottom edge (it "runs off" the page),
#   * the lower table's bbox top is within TABLE_MERGE_TOP_FRAC of the page height from the top
#     edge (it "resumes" at the top, below the running header),
#   * both tables have the same normalized column count,
#   * their column x-edges (and outer left/right edges) align within TABLE_MERGE_XTOL_FRAC of the
#     page width,
#   * no body heading sits between them (running headers/footers in the page margin bands are
#     ignored — only headings in the text body block a merge).
# A repeated header row on the lower table is dropped before appending.
TABLE_MERGE_BOTTOM_FRAC = 0.12   # upper table must end within this frac of page height from bottom
TABLE_MERGE_TOP_FRAC = 0.28      # lower table must start within this frac of page height from top
TABLE_MERGE_XTOL_FRAC = 0.02     # column x-edge tolerance as a fraction of page width
TABLE_MERGE_MARGIN_FRAC = 0.08   # top/bottom band treated as running-header/footer furniture


def _looks_like_heading(s: str) -> bool:
    """True if a line of text reads as a section/chapter heading (used to veto a table merge when a
    heading sits between the two candidate tables). Conservative: chapter/part/section patterns,
    an ALL-CAPS run, or a numbered section like '3.2 Something' — not ordinary body text."""
    s = s.strip()
    if not s or len(s) > MAX_HEADING_LINE_LEN:
        return False
    for pat in CHAPTER_PATTERNS:
        if pat.match(s):
            return True
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters):
        return True
    if re.match(r'^\d+(?:\.\d+){1,3}\s+[A-Z]', s):
        return True
    return False


def _gather_page_tables(plumber_page, fitz_page, page_num: int) -> tuple[list[dict], list[str]]:
    """Find tables on a page with the geometry the merge pass needs: markdown, normalized column
    count, column left-x edges, bbox, page size, and whether a body heading sits above/below the
    table (running headers/footers in the margin bands are excluded). Returns (tables, page-frame
    rejection reasons)."""
    found = plumber_page.find_tables()
    if not found:
        return [], []

    page_w = float(fitz_page.rect.width)
    page_h = float(fitz_page.rect.height)
    top_margin = TABLE_MERGE_MARGIN_FRAC * page_h
    bot_margin = page_h - TABLE_MERGE_MARGIN_FRAC * page_h

    check_reversed = sideways_enabled()
    fitz_text = fitz_page.get_text() if check_reversed else ""

    heading_ys = []  # y-centers of heading lines within the body band
    d = fitz_page.get_text("dict")
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            txt = "".join(s.get("text", "") for s in ln.get("spans", []))
            if not _looks_like_heading(txt):
                continue
            bb = ln["bbox"]
            yc = (bb[1] + bb[3]) / 2.0
            if top_margin < yc < bot_margin:
                heading_ys.append(yc)

    out = []
    rejects = []
    for t in found:
        rows = t.extract()
        md = _table_to_md(rows)
        if not md:
            continue
        # Same page-frame reject as the plain path, so the merge path cannot resurrect the defect
        # (and cannot stitch two page-frame pseudo-tables into an even larger one). The
        # reversed-text reject rides along for the same reason: a sideways table must not reach
        # the merge pass, where it would also drag its neighbours into a stitched mega-table.
        _bbox = getattr(t, "bbox", None)
        reason = (page_frame_reject_reason(rows, _bbox, page_w, page_h)
                  or page_furniture_reject_reason(rows, _bbox, page_h)
                  or (reversed_text_reject_reason(rows, fitz_text) if check_reversed else None))
        if reason:
            rejects.append(reason)
            continue
        cells = [c for c in (getattr(t, "cells", None) or []) if c]
        xedges = sorted({round(c[0], 1) for c in cells})
        col_count = max((len(r) for r in rows), default=0)
        bbox = tuple(float(v) for v in t.bbox)  # (x0, top, x1, bottom), top-origin points
        out.append({
            "page": page_num,
            "rows": rows,
            "md": md,
            "col_count": col_count,
            "xedges": xedges,
            "bbox": bbox,
            "page_w": page_w,
            "page_h": page_h,
            "heading_above": any(y < bbox[1] for y in heading_ys),
            "heading_below": any(y > bbox[3] for y in heading_ys),
        })
    return out, rejects


def _xedges_match(xa: list, xb: list, bbox_a: tuple, bbox_b: tuple, page_w: float) -> bool:
    """Column x-edges align: same outer left/right edges and every edge of the shorter list has a
    partner in the longer list, all within a page-width-relative tolerance. Allows a ±1 difference
    in edge count (merged cells can drop an interior separator on one page)."""
    tol = page_w * TABLE_MERGE_XTOL_FRAC
    if abs(bbox_a[0] - bbox_b[0]) > tol or abs(bbox_a[2] - bbox_b[2]) > tol:
        return False
    if abs(len(xa) - len(xb)) > 1:
        return False
    short, long = (xa, xb) if len(xa) <= len(xb) else (xb, xa)
    for x in short:
        if not any(abs(x - y) <= tol for y in long):
            return False
    return True


def _is_continuation(t1: dict, t2: dict) -> bool:
    """Does table t2 (top of a later page) continue table t1 (bottom of an earlier page)?"""
    if (t1["page_h"] - t1["bbox"][3]) > TABLE_MERGE_BOTTOM_FRAC * t1["page_h"]:
        return False
    if t2["bbox"][1] > TABLE_MERGE_TOP_FRAC * t2["page_h"]:
        return False
    if t1["col_count"] == 0 or t1["col_count"] != t2["col_count"]:
        return False
    if not _xedges_match(t1["xedges"], t2["xedges"], t1["bbox"], t2["bbox"], t1["page_w"]):
        return False
    if t1["heading_below"] or t2["heading_above"]:
        return False
    return True


def _rows_header_equal(rows_a, rows_b) -> bool:
    """True if the two tables share an identical (non-empty) header row — a repeated header to dedup."""
    if not rows_a or not rows_b:
        return False
    ha = _clean_row(rows_a[0])
    if not any(c for c in ha):
        return False
    return ha == _clean_row(rows_b[0])


def merge_page_tables(page_tables: list[list[dict]]) -> dict:
    """Stitch cross-page table continuations. `page_tables[i]` is the tables on page i (in reading
    order). Returns {page_index: [(md, [continued_page_nums], anchor_page_num), ...]} — the table
    blocks to emit on each page. A continued table is emitted once, on its first (anchor) page, with
    the follow-on pages' rows appended and their page numbers recorded for the trace comment; the
    consumed follow-on tables are not emitted again."""
    n = len(page_tables)
    consumed = set()  # (page_index, table_index) folded into an earlier table
    emit: dict = {}
    for p in range(n):
        for idx, t in enumerate(page_tables[p]):
            if (p, idx) in consumed:
                continue
            rows = list(t["rows"])
            cont_pages = []
            candidates = []  # (page_index, table_index) this merge would consume
            # Only the last table on a page can run off its bottom edge into the next page.
            if idx == len(page_tables[p]) - 1:
                cur = t
                q = p + 1
                while q < n and page_tables[q] and (q, 0) not in consumed:
                    nt = page_tables[q][0]
                    if not _is_continuation(cur, nt):
                        break
                    add = list(nt["rows"])
                    if _rows_header_equal(t["rows"], add):
                        add = add[1:]
                    rows.extend(add)
                    candidates.append((q, 0))
                    cont_pages.append(nt["page"])
                    cur = nt
                    q += 1
            # Commit the merge only if the stitched table survives _table_to_md's content filter.
            # A merge of sparse tables can drop the combined content ratio below the keep threshold;
            # rather than silently lose the data, fall back to emitting the anchor table alone and
            # leaving the continuation tables to emit on their own pages (byte-for-byte as today).
            merged_md = _table_to_md(rows) if cont_pages else None
            if cont_pages and merged_md:
                consumed.update(candidates)
                emit.setdefault(p, []).append((merged_md, cont_pages, t["page"]))
            else:
                emit.setdefault(p, []).append((t["md"], [], t["page"]))
    return emit


# === Fix 2: whole-book table-failure detection ===
# The per-book failure is bimodal: a book either extracts tables fine or loses every single one,
# silently. Corpus audit (wp3-borderless-report.md §2): 34 of 260 converted books ship markdown with
# zero extracted tables, the largest carrying 274 / 239 / 218 / 142 / 128 / 95 table captions each.
# Causes are mixed — pdfplumber parsing 0 pages where fitz reads the file fine, ruling lines drawn
# as sub-3pt dash segments, and genuinely borderless tables — but the symptom is uniform and, until
# now, produced no error at all. These checks change nothing about extraction; they only make the
# loss loud, in the conversion report and in the markdown itself.
#
# N = 10 captions. Rationale, measured over 260 books: books with tables can never trip this check
# (total_tables == 0 short-circuits it), so N only governs false alarms among the 34 zero-table
# books. At N = 10 the check fires on 22 of those 34, including every one of the six largest; the
# 12 it spares all have <= 9 caption hits and are mostly ultrasound/manual-therapy atlases where
# that count is plausibly cross-references ("as listed in Table 5.2") rather than real tables. The
# caption regex deliberately over-counts, so a low N would convert genuine table-free books into
# recurring false warnings and the check would stop being read.
#
# Kill-switch: T2N_BOOK_TABLE_CHECK=0. Defaults ON — detection only, no extraction change.
BOOK_ZERO_TABLE_MIN_CAPTIONS = 10
# Partial-loss detection. The zero-table rule above only fires when a book extracts NOTHING, which
# is how a corpus-wide table loss can stay invisible: a book that recovers a handful of tables
# looks healthy. One reference in the measured corpus ships 274 table captions and, even with the
# Docling rung on, recovers 45 tables (ratio 0.16) — the same failure as the books the zero-rule
# catches, at nearly the same size, but silent.
#
# Threshold from the corpus, not from taste. Measured over 171 converted books carrying >=10
# captions: median ratio 0.99, p75 1.99 (the caption regex deliberately over-counts — it matches
# "as listed in Table 5.2" cross-references too — so a healthy book sits at or above 1). The low
# tail is dense and unambiguous: 21 books at ratio <0.01 and 42 at <0.20, including two large
# references at 0.03 and 0.07 that the zero-rule does not flag. 0.20 sits at p25 of that
# distribution and a factor of 5 below the median.
#
# This is a WARNING, not a rejection: a low ratio can also mean a book whose captions are mostly
# cross-references, so the wording says "likely". The cost of a false alarm is one warning line;
# the cost of the miss is the silent failure this check exists to end.
BOOK_PARTIAL_TABLE_RATIO = 0.20

# Book-level table-reliability banner. The numerator is deliberately NOT "any QC flag": measured
# across a 6-book pilot, any-flag rates cluster at 39-64% for every dense clinical book (64, 59,
# 53, 51, 39), so an any-flag threshold cannot separate a book whose tables are systematically
# wrong from one whose tables are mostly right — it would hang a banner on two thirds of a corpus
# and mean nothing.
#
# CONTENT-RETENTION does separate them, because it is the one check that measures actual data loss
# (text present in the page's own text layer that reached no cell) rather than formatting: the same
# six books run 40%, 27%, 17%, 12%, 2%, 0%. The ragged_row / empty_first_cell checks fire on benign
# layouts (spanning headers, indented sub-rows) and are left to the per-table markers, which name
# the exact table and rows.
#
# 0.25 sits in the gap between the two clusters. Small sample (n=6) — revisit against a full-corpus
# distribution.
BOOK_CONTENT_LOSS_RATE = 0.25
BOOK_HIGH_FLAG_MIN_TABLES = 10


def _book_reliability_flagged(content_loss_tables: int, total_tables: int) -> bool:
    """True when a high enough fraction of a book's tables lost content to warrant a whole-book
    'verify against the PDF' banner. Pure predicate (the >= min-tables guard is checked first, so
    a 0-table book never divides by zero)."""
    return (total_tables >= BOOK_HIGH_FLAG_MIN_TABLES
            and content_loss_tables / total_tables >= BOOK_CONTENT_LOSS_RATE)
TABLE_CAPTION_RE = re.compile(r'^\s*(?:TABLE|Table)\s+\d+[.\-]\d+', re.M)


# === Core conversion ===
def render_page_text(page, page_num: int) -> tuple[str, int]:
    """Extracted, cleaned, column-sorted, fig-ref-annotated text for one page + its fig-ref count.
    column_sorted_text() returns None for single-column / ambiguous pages, so those keep the exact
    plain get_text() path (byte-identical to the pre-column-sort behavior)."""
    ordered = column_sorted_text(page)
    if ordered is None:
        page_text = clean_text(page.get_text().strip())
    else:
        page_text = clean_text(ordered.strip())
    return annotate_fig_refs(page_text, page_num)


def _release_plumber_page(page) -> None:
    """Drop a pdfplumber Page's cached geometry as soon as the table pass is done with it (#5).

    `PDF.pages` materializes every accessed page into a list held for the lifetime of the PDF
    object, and each `Page` caches its own `chars` / `edges` / `rects` / textmap the first time
    a table call touches them. Nothing released them before `PDF.close()` at the end of the
    book, so peak memory grew roughly linearly with the number of table-bearing pages -- on a
    table-dense reference that is tens of thousands of char dicts per page.

    Safe because every page is read EXACTLY ONCE: the non-merge path touches `plumber.pages[i]`
    only at the single `extract_tables_md()` call site, the merge path only at the single
    `_gather_page_tables()` call site, and both of those return plain values (markdown strings,
    row lists, floats) with no lazy reference back into the page. Nothing revisits a page later,
    so this cannot change output -- and if something ever did revisit one, pdfplumber would
    simply re-parse it, at a speed cost rather than a correctness cost.

    `flush_cache()` / `close()` have been on `Page` for many pdfplumber releases (verified on
    0.11.9), but they are reached defensively so an older pin degrades to the previous
    behaviour instead of raising.
    """
    for name in ("flush_cache", "close"):
        fn = getattr(page, name, None)
        if fn is None:
            continue  # older pdfplumber without the cache API -- nothing to release
        try:
            fn()
        except Exception:
            pass  # releasing a cache must never be able to fail a conversion


def convert_pdf(pdf_path: str, out_path: str, book_label: str, table_worker=None) -> dict:
    """Convert a single PDF to markdown. Returns stats dict.

    `table_worker` is an optional live DoclingTableWorker. It is a PARAMETER rather than
    something built here because Docling costs ~4-12 s of model load on its first call and the
    same warm process is reusable across different PDFs (measured: switching books mid-session
    does not reload models). Building one per book would pay that cold start ~288 times over a
    corpus run. Batch callers build one worker and pass it to every book; a standalone
    single-PDF invocation passes None and gets a private worker (paying the cold start once,
    which is fine for one book)."""
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    plumber = pdfplumber.open(pdf_path)
    plumber_repair_note = None
    if len(plumber.pages) == 0 and total_pages > 0:
        # pdfminer (pdfplumber's parser) is stricter than MuPDF about a damaged page tree / xref,
        # and returns ZERO pages where fitz reads the book fine. Silently, that costs every table
        # in the book (2026-07-21 batch: two references of 464 and 752 pages both lost 100% of
        # their tables this way). Rewriting the PDF through fitz normalizes the structure and
        # pdfminer then parses it.
        try:
            repaired = Path(tempfile.gettempdir()) / f"t2n_repair_{os.getpid()}_{Path(pdf_path).stem[:40]}.pdf"
            doc.save(str(repaired), garbage=4, clean=True, deflate=True)
            retry = pdfplumber.open(str(repaired))
            if len(retry.pages) > 0:
                plumber.close()
                plumber = retry
                plumber_repair_note = (
                    f"pdfplumber parsed 0 pages from the original PDF; a fitz-normalized rewrite "
                    f"parsed {len(retry.pages)} — the table pass ran on the repaired copy")
            else:
                retry.close()
        except Exception as e:
            plumber_repair_note = f"pdfplumber parsed 0 pages and the fitz repair attempt failed: {e}"

    output = [f"# {book_label}\n"]
    output.append(f"Source: `{pdf_path}`\n")
    warn_slot = len(output)  # book-level warnings are spliced in here once the pass is done

    total_tables = 0
    total_fig_refs = 0
    total_rejected = 0
    total_captions = 0
    total_review_flagged = 0            # tables routed to the out-of-band review queue
    review_queue_entries = []           # [(page_no, [reasons])] — a batch caller can write a manifest
    page_errors = []  # (page_num, exc_type, message) — counted, never swallowed
    sideways = SidewaysReparse()        # Fix 3: upright stand-in for sideways-printed pages
    detect_on = os.environ.get("T2N_BOOK_TABLE_CHECK", "1") != "0"
    table_gate_on = os.environ.get("T2N_TABLE_GATE", "1") != "0"
    merge_on = os.environ.get("T2N_TABLE_MERGE", "0") == "1"  # Improvement 3, default OFF
    review_on = review_queue_enabled()  # out-of-band review markers, default OFF

    # Docling table rung (default OFF: a new capability, not a fix to currently-wrong output, so
    # it follows T2N_TABLE_MERGE's convention rather than T2N_TABLE_FRAME_REJECT's). When on, the
    # SAME page gate decides which pages are worth an engine call — Docling is only ever asked
    # about pages page_has_table_candidate() already flagged, never the whole book. Docling
    # replaces pdfplumber as the table source for a page only when it actually returns a table;
    # otherwise the page falls back to pdfplumber, whose known collapse modes are still caught by
    # page_frame_reject_reason(). The fitz text stream is never touched by Docling: it reorders
    # page content (observed interleaving unrelated body prose between two halves of a table), so
    # only its TABLES are taken, never its reading order.
    docling_on = os.environ.get("T2N_DOCLING", "0") == "1"
    docling_note = None
    owns_worker = False
    if docling_on:
        if DoclingTableWorker is None:
            docling_on = False
            docling_note = f"T2N_DOCLING=1 but docling_tables could not be imported ({_DOCLING_IMPORT_ERROR}); used pdfplumber only"
        elif merge_on:
            # The merge path's continuation logic reads pdfplumber geometry (xedges/bbox). Wiring
            # Docling into it needs that logic ported to Docling's cell coordinates, which is a
            # separate piece of work — until then the two features are mutually exclusive rather
            # than silently producing a half-Docling, half-pdfplumber merge.
            docling_on = False
            docling_note = "T2N_DOCLING=1 ignored: not yet supported together with T2N_TABLE_MERGE=1"
        elif table_worker is None:
            table_worker = DoclingTableWorker()
            owns_worker = True

    # Snapshot the worker's lifetime failure counter so the book-level warning below can report
    # failures THIS BOOK caused, rather than any failure the shared worker ever had (a shared
    # worker outlives the book on purpose — see the batch callers).
    docling_failures_at_start = (
        table_worker.status().get("failure_count", 0)
        if (docling_on and table_worker is not None) else 0)

    total_docling_tables = 0
    total_flagged = 0
    total_content_loss = 0   # tables whose QC flags include actual data loss (content-retention)
    total_repairs = 0

    if not merge_on:
        # Plain per-page path — byte-identical to the pre-merge output.
        for i in range(total_pages):
            # Improvement 1: two-column reading order (exact-fallback inside render_page_text).
            page_text, fig_count = render_page_text(doc[i], i + 1)
            total_fig_refs += fig_count
            total_captions += len(TABLE_CAPTION_RE.findall(page_text))

            output.append(f"\n<!-- page {i+1} -->\n")
            output.append(page_text)

            # Improvement 2: only run the expensive pdfplumber pass on pages that plausibly hold a
            # table. Skipped pages never touch plumber.pages[i], so they don't pay the parse cost.
            # md_tables holds (markdown, qc_flags) pairs. With T2N_DOCLING off every pair carries
            # an empty flag list, so nothing extra is emitted and the output stays byte-identical
            # to the pdfplumber-only version.
            md_tables, rejects = [], []
            page_sideways_rot = None
            try:
                if not (table_gate_on and not page_has_table_candidate(doc[i], page_text)):
                    if docling_on:
                        for dt in table_worker.tables_for_page(pdf_path, i + 1):
                            # Ligature repair runs BEFORE _table_to_md and before the QC checks,
                            # so both the emitted markdown and the retention check see the
                            # corrected text. It is the one place in this pipeline that rewrites
                            # content rather than flagging it, which is allowed only because
                            # every rewrite is gated on the source PDF's own text layer: a token
                            # changes only when its current spelling is absent from the page and
                            # the proposed one is present there. No oracle => no repair.
                            dt.rows, repairs, _ = repair_ligatures(dt, doc=doc)
                            md = _table_to_md(dt.rows)
                            if md:
                                # Fix 3 guard 2 rides on this rung too. The repair below only
                                # reaches the pdfplumber path, so without this a sideways page
                                # whose tables Docling DOES return would still emit reversed
                                # cells silently — the one promise this fix has to keep is that
                                # no rung of the ladder does that. Rejecting here also lets the
                                # `if not md_tables` fallback hand the page to pdfplumber, which
                                # re-parses it upright and can recover the table for real.
                                rr = (reversed_text_reject_reason(dt.rows, page_text)
                                      if sideways_enabled() else None)
                                if rr:
                                    rejects.append(f"docling rung: {rr}")
                                    continue
                                total_repairs += len(repairs)
                                flags = qc_flags(dt, pdf_path=pdf_path, doc=doc)
                                if repairs:
                                    # Repairs are traced separately from QC flags: a repaired
                                    # cell is not "unverified", it is verified-and-corrected.
                                    md = format_repair_marker(i + 1, repairs) + "\n" + md
                                md_tables.append((md, flags))
                        total_docling_tables += len(md_tables)
                    if not md_tables:
                        # Either Docling is off, or it found nothing here. pdfplumber still gets
                        # its turn: it catches borderless cases Docling misses, and its own
                        # collapse modes remain guarded by page_frame_reject_reason().
                        _pp = plumber.pages[i]
                        try:
                            # Fix 3: a sideways-printed page is re-parsed upright first, otherwise
                            # pdfplumber reads its glyphs bottom-to-top and every cell comes back
                            # reversed.
                            _tp, _ = sideways.substitute(_pp, doc, i)
                            page_sideways_rot = sideways.last_rotation
                            plain, plumber_rejects = extract_tables_md(_tp, page_text)
                            # Extend rather than replace: a Docling table rejected above must
                            # still be reported even when pdfplumber then handles the page.
                            rejects.extend(plumber_rejects)
                        finally:
                            sideways.release()
                            _release_plumber_page(_pp)
                        md_tables = [(m, []) for m in plain]
            except Exception as e:
                # Count, don't swallow. A single unparseable page and a wholly unopenable book used
                # to look identical here (both produced zero tables and zero output); now the page
                # failures are tallied and reported, and the book-level checks below cover the rest.
                page_errors.append((i + 1, type(e).__name__, str(e)[:200]))
            if page_sideways_rot:
                output.append(f"\n<!-- ⚠️ page {i+1} is printed sideways — table pass re-parsed it "
                              f"at /Rotate {page_sideways_rot} (Fix 3) -->")
            for reason in rejects:
                total_rejected += 1
                output.append(f"\n<!-- ⚠️ pseudo-table rejected on page {i+1} "
                              f"({reason}) — its text is retained in the page prose above -->")
            for tbl, flags in md_tables:
                total_tables += 1
                output.append(f"\n**[Table on page {i+1}]**\n")
                if flags:
                    # Flag, never reject and never auto-fix: the downstream note-writing LLM must
                    # be able to see that this table needs checking against the PDF before it is
                    # cited as clean data.
                    total_flagged += 1
                    if any(f.startswith('content-retention') for f in flags):
                        total_content_loss += 1
                    output.append(format_trace_marker(i + 1, flags))
                if review_on:
                    # Orthogonal to the QC flag above: this catches MISBINDING (a value on the
                    # wrong row), which is structurally invisible so `flags` may be empty here.
                    rr = review_reasons(tbl, page_text)
                    if rr:
                        total_review_flagged += 1
                        review_queue_entries.append((i + 1, rr))
                        output.append(format_review_marker(i + 1, rr))
                output.append(tbl)
                output.append("")
    else:
        # Improvement 3: cross-page merge path. Gather text + table geometry for every page first
        # (merge decisions need the next page's tables), then emit with continuations stitched.
        page_texts = []
        page_tables = []
        page_rejects = []
        page_sideways = []
        for i in range(total_pages):
            page_text, fig_count = render_page_text(doc[i], i + 1)
            total_fig_refs += fig_count
            total_captions += len(TABLE_CAPTION_RE.findall(page_text))
            page_texts.append(page_text)
            tbls, rejects, rot_note = [], [], None
            try:
                if not (table_gate_on and not page_has_table_candidate(doc[i], page_text)):
                    _pp = plumber.pages[i]
                    try:
                        # Fix 3: same upright re-parse as the plain path. The stand-in supplies BOTH
                        # pages so the fitz geometry read here matches the pdfplumber page it pairs.
                        _tp, _tf = sideways.substitute(_pp, doc, i)
                        rot_note = sideways.last_rotation
                        tbls, rejects = _gather_page_tables(_tp, _tf, i + 1)
                    finally:
                        sideways.release()
                        _release_plumber_page(_pp)
            except Exception as e:
                page_errors.append((i + 1, type(e).__name__, str(e)[:200]))
            page_tables.append(tbls)
            page_rejects.append(rejects)
            page_sideways.append(rot_note)

        emit_blocks = merge_page_tables(page_tables)

        for i in range(total_pages):
            output.append(f"\n<!-- page {i+1} -->\n")
            output.append(page_texts[i])
            if page_sideways[i]:
                output.append(f"\n<!-- ⚠️ page {i+1} is printed sideways — table pass re-parsed it "
                              f"at /Rotate {page_sideways[i]} (Fix 3) -->")
            for reason in page_rejects[i]:
                total_rejected += 1
                output.append(f"\n<!-- ⚠️ pseudo-table rejected on page {i+1} "
                              f"({reason}) — its text is retained in the page prose above -->")
            for md, cont_pages, anchor_page in emit_blocks.get(i, []):
                total_tables += 1
                output.append(f"\n**[Table on page {anchor_page}]**\n")
                if review_on:
                    # A stitched table (cont_pages non-empty) IS a continuation by construction —
                    # pass that in so the trigger does not depend on a textual "(continued)" marker.
                    rr = review_reasons(md, page_texts[i], stitched_continuation=bool(cont_pages))
                    if rr:
                        total_review_flagged += 1
                        review_queue_entries.append((anchor_page, rr))
                        output.append(format_review_marker(anchor_page, rr))
                output.append(md)
                for cp in cont_pages:
                    output.append(f"<!-- table continues from page {cp} -->")
                output.append("")

    plumber_pages = len(plumber.pages) if detect_on else None
    docling_status = table_worker.status() if (docling_on and table_worker is not None) else None
    doc.close()
    plumber.close()
    if owns_worker and table_worker is not None:
        # Only a worker this call created is closed here. A worker passed in by a batch caller
        # outlives this book on purpose — that is the whole point of hoisting the cold start.
        table_worker.close()

    # Book-level failure detection (pure detection — nothing about extraction changes here).
    warnings_out = []
    if docling_note:
        warnings_out.append(docling_note)
    if plumber_repair_note:
        warnings_out.append(plumber_repair_note)
    docling_failures_this_book = (
        docling_status.get("failure_count", 0) - docling_failures_at_start) if docling_status else 0
    if docling_status and (docling_status.get("disabled") or docling_failures_this_book > 0):
        # A Docling worker that died or was disabled mid-book is not a silent event: the failed
        # pages fell back to pdfplumber, a different quality baseline. Count the failures this book
        # actually caused — testing `last_error` for truthiness cannot do that, because it is sticky
        # across the whole life of a shared worker (a single timeout in book 61 of a 154-book run
        # flagged the next 62 books as degraded before this was a delta).
        warnings_out.append(
            f"Docling worker failed on {docling_failures_this_book} page(s) of this book "
            f"(disabled={docling_status.get('disabled')}, "
            f"restarts={docling_status.get('consecutive_restarts')}, "
            f"last_error={docling_status.get('last_error')}) — those pages fell back to pdfplumber")
    if detect_on:
        if plumber_pages == 0 and total_pages > 0:
            warnings_out.append(
                f"pdfplumber parsed 0 pages while fitz opened {total_pages} — the table pass never "
                f"ran on ANY page of this book, so every table in it is missing from this markdown")
        elif total_tables == 0 and total_captions >= BOOK_ZERO_TABLE_MIN_CAPTIONS:
            warnings_out.append(
                f"0 tables extracted despite {total_captions} table captions in the text — this "
                f"book's tables are very likely all missing (borderless or sub-3pt ruling lines)")
        elif (total_captions >= BOOK_ZERO_TABLE_MIN_CAPTIONS
              and total_tables / total_captions < BOOK_PARTIAL_TABLE_RATIO):
            # Partial loss is the silent version of the same failure: enough tables come through
            # that the zero-table rule stays quiet, while most of the book's tables are missing.
            warnings_out.append(
                f"only {total_tables} tables extracted against {total_captions} table captions "
                f"(ratio {total_tables / total_captions:.2f}, below {BOOK_PARTIAL_TABLE_RATIO:.2f}) "
                f"— most of this book's tables are likely still missing from this markdown")
        if page_errors:
            kinds = ", ".join(sorted({k for _, k, _ in page_errors}))
            warnings_out.append(
                f"{len(page_errors)} page(s) raised an error during the table pass ({kinds}); "
                f"first at page {page_errors[0][0]}: {page_errors[0][2]}")

    if warnings_out:
        block = ["\n> [!warning] Conversion warnings — table extraction is incomplete for this book"]
        block += [f"> - {w}" for w in warnings_out]
        output[warn_slot:warn_slot] = ["\n".join(block) + "\n"]

    # Data-driven table-reliability banner (hang the flag at book level, not per-table): a high
    # QC-flag rate means this book's tables are systematically mis-structured. The structural gate
    # cannot see a value bound to the wrong row, so this banner tells the note-writing LLM to treat
    # EVERY table here as needing a check against the source PDF.
    flag_rate = (total_flagged / total_tables) if total_tables else 0.0
    loss_rate = (total_content_loss / total_tables) if total_tables else 0.0
    reliability_flagged = _book_reliability_flagged(total_content_loss, total_tables)
    if reliability_flagged:
        banner = [
            "\n> [!caution] Table reliability — verify EVERY table in this book against the PDF",
            f"> {total_content_loss}/{total_tables} tables ({loss_rate:.0%}) lost content that is "
            f"present in the page's text layer but reached no cell; {total_flagged} "
            f"({flag_rate:.0%}) carry some structural QC flag. "
            f"This book's tables are systematically mis-structured (category headers cast as data "
            f"rows, brand/rating misbindings, truncated multi-line cells). The structural QC gate "
            f"CANNOT detect a value bound to the wrong row, so do not cite any table in this book "
            f"as clean data without checking it against the source PDF."]
        output[warn_slot:warn_slot] = ["\n".join(banner) + "\n"]

    # Write output
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output))

    size_kb = os.path.getsize(out_path) / 1024

    return {
        "pages": total_pages,
        "tables": total_tables,
        "fig_refs": total_fig_refs,
        "size_kb": round(size_kb, 1),
        "rejected_tables": total_rejected,
        "table_captions": total_captions,
        "page_errors": len(page_errors),
        # Fix 3 telemetry: pages whose text was printed sideways and re-parsed upright before the
        # table pass. Non-zero means this book contains landscape tables that the pre-fix pipeline
        # would have emitted character-reversed.
        "sideways_pages": len(sideways.repaired_pages),
        "warnings": warnings_out,
        # Docling rung telemetry. `docling_tables` counts tables sourced from Docling (the rest of
        # `tables` came from the pdfplumber fallback); `flagged_tables` counts tables carrying at
        # least one QC flag. Both are 0 when the rung is off, so the corpus run can be audited for
        # how much of the output each engine actually produced.
        "docling_tables": total_docling_tables,
        "flagged_tables": total_flagged,
        "content_loss_tables": total_content_loss,
        "ligature_repairs": total_repairs,
        # Out-of-band review queue (T2N_REVIEW_QUEUE). `review_flagged` counts tables routed to
        # the second-opinion pass; `review_queue` lists (page, reasons) so a batch caller can write
        # a per-book review manifest. Both are 0/empty when the queue is off.
        "review_flagged": total_review_flagged,
        "review_queue": review_queue_entries,
        # Data-driven book-level reliability flag: True when >=BOOK_HIGH_FLAG_RATE of the book's
        # tables carry a QC flag (a whole-book "verify against PDF" banner was emitted). `flag_rate`
        # is the fraction regardless of whether the banner fired, for corpus-level auditing.
        "reliability_flagged": reliability_flagged,
        "flag_rate": round(flag_rate, 3),
        "content_loss_rate": round(loss_rate, 3),
    }


# === Batch conversion ===
def should_convert(pdf_path: str, md_path: str) -> bool:
    """Skip if md exists and is newer than PDF."""
    if not os.path.exists(md_path):
        return True
    pdf_mtime = os.path.getmtime(pdf_path)
    md_mtime = os.path.getmtime(md_path)
    return pdf_mtime > md_mtime


def batch_convert(books: dict, book_keys: list[str]) -> dict:
    """Convert all PDFs in specified books. Returns aggregate stats."""
    results = []
    skipped = 0
    errors = []

    for key in book_keys:
        book = books[key]
        src_dir = book["src"]
        out_dir = book["out"]
        prefix = book.get("prefix", "")

        if not src_dir.exists():
            errors.append(f"Source dir not found: {src_dir}")
            continue

        pdfs = sorted(src_dir.glob(book["glob"]))
        for pdf in pdfs:
            stem = prefix + pdf.stem.replace(" ", "_").replace(",", "")
            md_path = str(out_dir / (stem + ".md"))

            if not should_convert(str(pdf), md_path):
                skipped += 1
                continue

            label = f"{book['label']} — {pdf.stem}"
            try:
                stats = convert_pdf(str(pdf), md_path, label)
                stats["file"] = pdf.name
                stats["book"] = book["label"]
                results.append(stats)
                rev = f", {stats['review_flagged']}⚠rev" if stats.get("review_flagged") else ""
                print(f"  ✓ {pdf.name} ({stats['pages']}p, {stats['tables']}t, {stats['fig_refs']}f, {stats['size_kb']}KB{rev})")
                for w in stats.get("warnings", []):
                    print(f"    [WARN] {pdf.name}: {w}")
                    errors.append(f"[TABLE-WARN] {pdf.name}: {w}")
            except Exception as e:
                errors.append(f"{pdf.name}: {e}")
                print(f"  ✗ {pdf.name}: {e}")

    return {
        "converted": len(results),
        "skipped": skipped,
        "errors": len(errors),
        "error_details": errors,
        "total_pages": sum(r["pages"] for r in results),
        "total_tables": sum(r["tables"] for r in results),
        "total_fig_refs": sum(r["fig_refs"] for r in results),
        "total_size_mb": round(sum(r["size_kb"] for r in results) / 1024, 1),
        "details": results,
    }


def resolve_book_keys(books: dict, name: str) -> list[str]:
    """Resolve book name to list of books dict keys."""
    name = name.lower().strip()
    if name == "all":
        return list(books.keys())
    # Fuzzy match
    for key in books:
        if name in key:
            return [key]
    raise ValueError(f"Unknown book: {name}. Options: {', '.join(books.keys())}")


# === Chapter splitting ===
# Numbered heading: "5 Hip and Groin Region" (1-2 digit number + Title-Case text).
# Constrained to avoid noise: must start with capital after digits, all letters/spaces, no trailing punct.
# NOTE: this pattern also matches page running headers like "16 Postoperative Orthopaedic
# Rehabilitation" (page number + book title). Those are filtered out in detect_chapters by a
# frequency guard (a numbered-heading title that repeats across many pages = running header).
NUMBERED_HEADING_PATTERN = re.compile(r'^\d{1,2}\s+[A-Z][a-zA-Z][a-zA-Z\s]{2,60}$')

CHAPTER_PATTERNS = [
    # CHAPTER ONE, Chapter 1, CHAPTER 1 — must end cleanly (not mid-sentence)
    re.compile(r'^CHAPTER\s+(?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|'
               r'ELEVEN|TWELVE|THIRTEEN|FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|'
               r'NINETEEN|TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY|'
               r'TWENTY-\w+|THIRTY-\w+|FORTY-\w+|FIFTY-\w+|'
               r'\d+)\b(?:\s|$)', re.IGNORECASE),
    # Part I, PART III, Part 1
    re.compile(r'^(?:PART|Part)\s+(?:[IVXLCDMivxlcdm]+|\d+)\b'),
    # Section 1, SECTION 1, Unit 1
    re.compile(r'^(?:SECTION|Section|UNIT|Unit)\s+\d+\b'),
    NUMBERED_HEADING_PATTERN,
]

# A numbered-heading title that pairs with at least this many DISTINCT leading numbers is a
# page running header (page-number + book-title, e.g. "16 …", "24 …", "34 …"), not a chapter.
# A genuine repeated chapter running header keeps the SAME number ("13 Head and Neck" on every
# page of ch.13), so it pairs with a single number and is NOT dropped (first-occurrence dedup
# already handles it). Distinct-number count is what separates page headers from chapter headers.
RUNNING_HEADER_MIN = 3
# Table-of-Contents cluster detection: a dense run of >= TOC_MIN_ENTRIES heading matches
# (consecutive entries no more than TOC_MAX_GAP lines apart), whose chapter keys mostly
# reappear later in the body, is a contents listing — not chapter starts. A real contents
# page lists chapters a few lines apart; body chapter headings are hundreds of lines apart.
# Its lines are skipped so chapters anchor at their real body start (first occurrence of each
# "Chapter N" running header) instead of the listing.
TOC_MIN_ENTRIES = 6
TOC_MAX_GAP = 60
# Max line length for a heading — anything longer is likely a paragraph
MAX_HEADING_LINE_LEN = 120

WORD_TO_NUM = {}
_ones = ['ZERO','ONE','TWO','THREE','FOUR','FIVE','SIX','SEVEN','EIGHT','NINE',
         'TEN','ELEVEN','TWELVE','THIRTEEN','FOURTEEN','FIFTEEN','SIXTEEN',
         'SEVENTEEN','EIGHTEEN','NINETEEN']
for i, w in enumerate(_ones):
    WORD_TO_NUM[w] = i
_tens = {20:'TWENTY',30:'THIRTY',40:'FORTY',50:'FIFTY',60:'SIXTY',70:'SEVENTY',80:'EIGHTY',90:'NINETY'}
for v, w in _tens.items():
    WORD_TO_NUM[w] = v
    for i in range(1, 10):
        WORD_TO_NUM[f"{w}-{_ones[i]}"] = v + i


def _numbered_heading_title(stripped: str) -> str:
    """Title signature of a NUMBERED_HEADING line, with the leading number stripped."""
    return re.sub(r'^\d{1,2}\s+', '', stripped).strip().lower()


def _chapter_key(stripped: str) -> str:
    """Identifier used to dedup repeated headings (running headers) of the same chapter."""
    ch_id = re.match(r'(?:CHAPTER|Chapter|PART|Part|SECTION|Section|UNIT|Unit)?\s*(\S+)',
                     stripped, re.IGNORECASE)
    return ch_id.group(1).upper() if ch_id else stripped[:20]


def _find_toc_span(matches: list[tuple[int, int, str, str]]) -> tuple[int, int] | None:
    """Locate a Table-of-Contents listing among heading matches.

    `matches` is [(page_num, line_idx, ch_key, stripped), ...] in document order. A TOC is a
    dense cluster (>= TOC_MIN_ENTRIES headings within <= TOC_MAX_PAGES pages) whose chapter
    keys mostly reappear *after* the cluster — i.e. the contents page lists chapters that then
    show up again as body running headers. Returns the (first_line, last_line) span to skip, or
    None when no such listing exists (the normal case for books without a matched TOC).
    """
    if len(matches) < TOC_MIN_ENTRIES:
        return None

    best = None  # (entry_count, (line_start, line_end))
    n = len(matches)
    i = 0
    while i < n:
        # grow a dense run: consecutive heading matches <= TOC_MAX_GAP lines apart
        j = i + 1
        while j < n and matches[j][1] - matches[j - 1][1] <= TOC_MAX_GAP:
            j += 1
        run = matches[i:j]
        if len(run) >= TOC_MIN_ENTRIES:
            run_keys = {m[2] for m in run}
            after_keys = {m[2] for m in matches[j:]}
            # require a clear majority of the listed chapters to recur later in the body
            if len(run_keys & after_keys) >= max(3, len(run_keys) // 2):
                span = (run[0][1], run[-1][1])
                if best is None or len(run) > best[0]:
                    best = (len(run), span)
        i = j if j > i else i + 1
    return best[1] if best else None


def detect_chapters(content: str) -> list[tuple[int, int, str]]:
    """Detect chapter boundaries. Returns [(page_num, line_idx, title), ...]."""
    lines = content.split('\n')

    # Pass 1: for each numbered-heading title, collect the set of distinct leading numbers it
    # appears with. A page running header ("16 …", "24 …", "34 Some Book Title Here")
    # pairs one constant title with many page numbers; a real chapter heading
    # ("5 Hip and Groin Region") — even when repeated as a running header — keeps one number.
    numbered_title_numbers: dict[str, set] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 3 or len(stripped) > MAX_HEADING_LINE_LEN:
            continue
        if NUMBERED_HEADING_PATTERN.match(stripped):
            sig = _numbered_heading_title(stripped)
            num_m = re.match(r'^(\d{1,2})\b', stripped)
            if sig and num_m:
                numbered_title_numbers.setdefault(sig, set()).add(num_m.group(1))

    # Pass 2: collect every heading match (page, line, key, text), dropping running headers.
    matches: list[tuple[int, int, str, str]] = []
    page_num = 0
    for i, line in enumerate(lines):
        m = re.match(r'<!-- page (\d+) -->', line)
        if m:
            page_num = int(m.group(1))
            continue

        stripped = line.strip()
        if not stripped or len(stripped) < 3 or len(stripped) > MAX_HEADING_LINE_LEN:
            continue

        for pat in CHAPTER_PATTERNS:
            if pat.match(stripped):
                # Drop page running headers: a numbered-heading title paired with many
                # distinct leading numbers is a page-number + book-title line, not a chapter.
                if pat is NUMBERED_HEADING_PATTERN:
                    if len(numbered_title_numbers.get(_numbered_heading_title(stripped), ())) >= RUNNING_HEADER_MIN:
                        break
                matches.append((page_num, i, _chapter_key(stripped), stripped))
                break

    # Pass 3: skip a Table-of-Contents listing so chapters are anchored at their real body
    # start (first occurrence per key after the TOC), not at the contents-page listing.
    toc_span = _find_toc_span(matches)

    chapters = []
    seen_chapters = set()
    for page_num, line_idx, ch_key, stripped in matches:
        if toc_span and toc_span[0] <= line_idx <= toc_span[1]:
            continue
        if ch_key not in seen_chapters:
            seen_chapters.add(ch_key)
            chapters.append((page_num, line_idx, stripped))

    return chapters


def get_pdf_bookmarks(pdf_path: str) -> list[tuple[int, int, str]]:
    """Get chapter-level bookmarks from PDF. Returns [(level, page, title), ...]."""
    try:
        doc = fitz.open(pdf_path)
        toc = doc.get_toc()
        doc.close()
        if len(toc) < 3:
            return []
        return toc  # [[level, title, page], ...]
    except Exception:
        return []


def split_by_bookmarks(content: str, out_dir: Path, bookmarks: list):
    """Split content using PDF bookmarks as chapter boundaries."""
    lines = content.split('\n')

    # Build page->line mapping
    page_to_line = {}
    for i, line in enumerate(lines):
        m = re.match(r'<!-- page (\d+) -->', line)
        if m:
            page_to_line[int(m.group(1))] = i

    # Filter to level-1 and level-2 bookmarks (chapters)
    chapters = []
    for level, title, page in bookmarks:
        if level <= 2 and page in page_to_line:
            chapters.append((page, page_to_line[page], title.strip()))

    if len(chapters) < 2:
        return None

    # Write front matter
    front_end = chapters[0][1]
    front = '\n'.join(lines[:front_end]).strip()
    if front:
        (out_dir / 'ch00_Front_Matter.md').write_text(front, encoding='utf-8')

    # Write each chapter
    for idx, (pg, line_start, title) in enumerate(chapters):
        line_end = chapters[idx + 1][1] if idx + 1 < len(chapters) else len(lines)
        ch_content = '\n'.join(lines[line_start:line_end])

        ch_num = idx + 1
        safe_title = re.sub(r'[^a-zA-Z0-9_ ]', '', title)[:50].strip().replace(' ', '_')
        fname = f'ch{ch_num:02d}_{safe_title}.md' if safe_title else f'ch{ch_num:02d}.md'
        (out_dir / fname).write_text(ch_content, encoding='utf-8')

    return chapters


def split_into_chapters(content: str, out_dir: Path, total_pages: int, pdf_path: str = None):
    """Split full_text.md into chapter files. Tries bookmarks first, then pattern detection."""
    # Strategy 1: Use PDF bookmarks if available
    if pdf_path:
        bookmarks = get_pdf_bookmarks(pdf_path)
        if bookmarks:
            result = split_by_bookmarks(content, out_dir, bookmarks)
            if result:
                return result

    # Strategy 2: Pattern detection
    chapters = detect_chapters(content)
    lines = content.split('\n')

    if not chapters and total_pages > 200:
        _force_split(content, out_dir, total_pages)
        return None

    if not chapters:
        return None

    # Write front matter
    front_end = chapters[0][1]
    front = '\n'.join(lines[:front_end]).strip()
    if front:
        (out_dir / 'ch00_Front_Matter.md').write_text(front, encoding='utf-8')

    # Write each chapter
    for idx, (pg, line_start, title) in enumerate(chapters):
        line_end = chapters[idx + 1][1] if idx + 1 < len(chapters) else len(lines)
        ch_content = '\n'.join(lines[line_start:line_end])

        ch_num = idx + 1
        safe_title = re.sub(r'[^a-zA-Z0-9_ ]', '', title)[:50].strip().replace(' ', '_')
        fname = f'ch{ch_num:02d}_{safe_title}.md' if safe_title else f'ch{ch_num:02d}.md'
        (out_dir / fname).write_text(ch_content, encoding='utf-8')

    return chapters


# === EPUB conversion (pandoc + heading recovery + chapter split) ===
def _epub_slug(s: str) -> str:
    s = re.sub(r'[^A-Za-z0-9 ]+', '', s).strip()
    return re.sub(r'\s+', '_', s)[:60]


def recover_epub_headings(text: str) -> str:
    """For epubs whose CSS-styled chapter titles pandoc emits as plain text (no #
    headings): rebuild headings from the TOC link table + body <span id> anchors.

    pandoc gfm output convention for such epubs:
      TOC:  <a href="#NN-ChNN.xhtml" class="...">Chapter N</a>
            [Chapter Title](#NN-ChNN.xhtml)
            <a href="#NN-part.xhtml" ...>Part X<span...>NAME</a>
      body: <span id="NN-ChNN.xhtml"></span>
    Returns text with `# Chapter N — Title` / `# Part ...` headings inserted at anchors.
    """
    lines = text.split("\n")
    title_map = {}   # anchor_id -> ("chapter"|"part", label)
    chap_num = {}    # anchor_id -> int
    for i, l in enumerate(lines):
        m = re.search(r'<a href="#([^"]+)"[^>]*>Chapter (\d+)\s*</a>', l)
        if m:
            aid, num = m.group(1), int(m.group(2))
            for j in range(i + 1, min(i + 4, len(lines))):
                mt = re.match(r'\[([^\]]+)\]\(#' + re.escape(aid), lines[j])
                if mt:
                    title_map[aid] = ("chapter", mt.group(1))
                    chap_num[aid] = num
                    break
            continue
        mp = re.search(r'<a href="#([^"]+)"[^>]*>(Part [IVXLC0-9]+)\s*<span[^>]*>\s*</span>(.+?)</a>', l)
        if mp:
            title_map[mp.group(1)] = ("part", f"{mp.group(2)} — {mp.group(3).strip()}")

    if not chap_num:
        return text  # nothing recoverable — leave as-is

    anchor_re = re.compile(r'<span id="([0-9]+-[^"]+\.xhtml)"></span>')
    out = []
    for l in lines:
        m = anchor_re.match(l.strip())
        if m and m.group(1) in title_map:
            kind, label = title_map[m.group(1)]
            if kind == "part":
                out.append(f"\n# {label}\n")
            else:
                out.append(f"\n# Chapter {chap_num[m.group(1)]} — {label}\n")
            continue
        out.append(l)
    return "\n".join(out)


def split_epub_by_headings(text: str, out_dir: Path, label: str):
    """Split epub markdown into chapter files on top-level (#) headings.
    Prefers `# Chapter ...` headings; falls back to all h1 if none. Front matter
    (before first split heading) -> ch00. epubs have no page numbers."""
    lines = text.split("\n")
    h1 = [(i, l[2:].strip()) for i, l in enumerate(lines) if re.match(r'^# \S', l)]
    if not h1:
        return None
    chap = [(i, t) for i, t in h1 if re.match(r'(?i)chapter\b', t)]
    if not chap:
        chap = h1  # no explicit chapters -> every h1 is a section

    header = f"<!-- BOOK: {label} -->\n<!-- SOURCE: epub (chapter-based, no page numbers) -->\n\n"
    segs = []
    first = chap[0][0]
    if first > 0:
        segs.append(("ch00_Front_Matter.md", 0, first))
    for k, (ln, title) in enumerate(chap):
        end = chap[k + 1][0] if k + 1 < len(chap) else len(lines)
        # derive ch number from "Chapter N" if present, else sequential
        mnum = re.search(r'(?i)chapter\s+(\d+[a-z]?)', title)
        num = mnum.group(1) if mnum else f"{k + 1:02d}"
        if num.isdigit():
            num = f"{int(num):02d}"
        # strip leading "Chapter N —/:" from title for a clean filename
        clean = re.sub(r'(?i)^chapter\s+\d+[a-z]?\s*[—:-]\s*', '', title)
        segs.append((f"ch{num}_{_epub_slug(clean)}.md", ln, end))

    written = 0
    for name, s, e in segs:
        content = "\n".join(lines[s:e]).strip() + "\n"
        (out_dir / name).write_text(header + content, encoding="utf-8")
        written += 1
    return written


# Presentational HTML that ebook toolchains (calibre in particular) leave behind and
# pandoc passes straight through in gfm. It carries no meaning but dominates the output:
# on one 52-chapter textbook it was 41% of all characters, diluting every chunk the
# indexer builds. Semantic tags are deliberately NOT in this list — <sup>/<sub> carry
# chemical formulae and charges, and table tags carry structure.
_EPUB_NOISE_TAGS = ("a", "div", "span")
_EPUB_NOISE_RE = re.compile(
    r"</?(?:" + "|".join(_EPUB_NOISE_TAGS) + r")(?:\s[^>]*)?/?>", re.IGNORECASE
)


def strip_epub_noise(text: str) -> str:
    """Drop presentational wrappers, keeping their inner text and all semantic tags."""
    text = _EPUB_NOISE_RE.sub("", text)
    # Collapse the blank-line runs the removed block wrappers leave behind.
    return re.sub(r"\n{3,}", "\n\n", text)


def convert_epub(epub_path: str, out_dir: Path, label: str = "") -> dict:
    """Convert an epub to searchable markdown (full_text.md + chapter files).
    Uses pandoc for text, recovers headings if pandoc emits none, then chapter-splits.
    epubs are reflowable -> no <!-- page N --> markers (page metadata = 0 downstream)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ft = out_dir / "full_text.md"
    label = label or out_dir.name

    if not shutil.which("pandoc"):
        raise RuntimeError(
            "pandoc not found on PATH — install from pandoc.org, needed only for EPUB input"
        )

    subprocess.run(
        ["pandoc", str(epub_path), "-t", "gfm", "--wrap=none", "-o", str(ft)],
        check=True, capture_output=True,
    )
    text = strip_epub_noise(ft.read_text(encoding="utf-8"))

    n_head = len(re.findall(r'(?m)^#{1,3} \S', text))
    if n_head < 5:
        text = recover_epub_headings(text)
    header = f"<!-- BOOK: {label} -->\n<!-- SOURCE: epub (chapter-based, no page numbers) -->\n\n"
    ft.write_text(header + text, encoding="utf-8")

    chapters = split_epub_by_headings(text, out_dir, label)
    words = len(text.split())
    return {
        "pages": 0, "tables": 0, "fig_refs": 0,
        "chapters": chapters or 0, "words": words,
        "size_kb": round(len(text.encode("utf-8")) / 1024, 1),
    }


def _force_split(content: str, out_dir: Path, total_pages: int):
    """Split by page ranges when no chapters detected."""
    lines = content.split('\n')
    page_lines = {}  # page_num -> line_idx
    for i, line in enumerate(lines):
        m = re.match(r'<!-- page (\d+) -->', line)
        if m:
            page_lines[int(m.group(1))] = i

    chunk_size = 30
    for start_page in range(1, total_pages + 1, chunk_size):
        end_page = min(start_page + chunk_size - 1, total_pages)
        start_line = page_lines.get(start_page, 0)
        end_line = page_lines.get(end_page + 1, len(lines))
        chunk = '\n'.join(lines[start_line:end_line])
        fname = f'pages_{start_page:04d}-{end_page:04d}.md'
        (out_dir / fname).write_text(chunk, encoding='utf-8')


def add_pdf_bookmarks(pdf_path: str, chapters: list[tuple[int, int, str]]):
    """Write chapter structure back to PDF as bookmarks if none exist."""
    try:
        doc = fitz.open(pdf_path)
        if doc.get_toc():
            doc.close()
            return False  # Already has bookmarks
        toc = [[1, title[:80], page] for page, _, title in chapters]
        if toc:
            doc.set_toc(toc)
            doc.save(pdf_path, incremental=True, encryption=0)
        doc.close()
        return bool(toc)
    except Exception:
        return False


# === Batch-dir conversion ===
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding='utf-8'))
    return {"completed": [], "errors": []}


def save_progress(progress: dict):
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding='utf-8')


def is_scan_only(pdf_path: str, sample_pages: int = 5) -> bool:
    """Quick check: is this a scanned PDF with no extractable text?"""
    try:
        doc = fitz.open(pdf_path)
        total_chars = 0
        for i in range(min(sample_pages, len(doc))):
            total_chars += len(doc[i].get_text().strip())
        doc.close()
        return total_chars < 100
    except Exception:
        return True


def detect_fitz_silent_failure(pdf_path: str, sample_pages: int = 5, threshold: float = 0.5) -> bool:
    """Detect CID Identity-H without ToUnicode: fitz returns high char count but garbled glyphs.

    Heuristic: sample content pages with >100 chars; count "normal" chars
    (ASCII printable, CJK, Latin-1 supplement, CJK punct). If ratio < threshold,
    fitz output is unusable — caller should route to Surya OCR.
    """
    try:
        doc = fitz.open(pdf_path)
        total_chars = 0
        good_chars = 0
        sampled = 0
        step = max(1, len(doc) // (sample_pages + 1))
        for i in range(step, len(doc), step):
            if sampled >= sample_pages:
                break
            text = doc[i].get_text()
            if len(text) < 100:
                continue
            sampled += 1
            for c in text:
                total_chars += 1
                cp = ord(c)
                if (0x20 <= cp <= 0x7E or
                    c in '\n\t\r' or
                    0x4E00 <= cp <= 0x9FFF or
                    0x3000 <= cp <= 0x303F or
                    0xFF00 <= cp <= 0xFFEF or
                    0x00A0 <= cp <= 0x00FF):
                    good_chars += 1
        doc.close()
        if sampled == 0 or total_chars == 0:
            return False
        return (good_chars / total_chars) < threshold
    except Exception:
        return False


def surya_available() -> bool:
    return bool(SURYA_VENV_PY and SURYA_ADAPTER and SURYA_VENV_PY.exists() and SURYA_ADAPTER.exists())


# === OCR output QC ==========================================================================
# The OCR rung used to accept anything the adapter produced: a line that failed to parse was
# `continue`d, an image with no output line was invisible, and a book that came back almost
# empty still reported success with a size in KB. That is the same silent-failure shape
# docs/ocr-ladder.md exists to catch on the fitz side, and it has bitten for real -- a scanned
# book once produced a 20 KB full_text.md where ~1.6 MB was expected, and nothing in the
# pipeline said so.
#
# Two book-level gates, both env-tunable, both sized off real scanned books rather than taste.
# Calibration (two scanned clinical references converted through this path, 689 and 788 pages):
# empty-page ratios were 0.054 and 0.003, mean chars/page 1791 and 2005. So the defaults sit
# ~6x above the worse observed empty ratio and ~9x below the lower observed char density --
# scanned books legitimately contain blank, plate and figure-only pages, and the gate must not
# fire on those. The 20 KB incident above works out to ~25 chars/page, an order of magnitude
# under the floor.
OCR_EMPTY_PAGE_MAX = 0.35
OCR_MIN_CHARS_PER_PAGE = 200


def ocr_quality_verdict(total_pages: int, empty_pages: int, chars_total: int,
                        empty_page_max: float = None, min_chars_per_page: int = None) -> str | None:
    """Return a diagnostic string if an OCR'd book looks like a failed conversion, else None.

    Split out from surya_ocr_pdf() so the thresholds are testable without an OCR engine.
    A book with zero pages is not judged: there is nothing to be wrong about, and the caller
    already treats an empty PDF as its own error.
    """
    if total_pages <= 0:
        return None
    # Env is read here rather than at import so a caller can change a threshold for one book
    # (and so the gate is testable) without reloading the module.
    if empty_page_max is None:
        try:
            empty_page_max = float(os.environ.get("T2N_OCR_EMPTY_PAGE_MAX", OCR_EMPTY_PAGE_MAX))
        except ValueError:
            empty_page_max = OCR_EMPTY_PAGE_MAX
    if min_chars_per_page is None:
        try:
            min_chars_per_page = int(os.environ.get("T2N_OCR_MIN_CHARS_PER_PAGE",
                                                    OCR_MIN_CHARS_PER_PAGE))
        except ValueError:
            min_chars_per_page = OCR_MIN_CHARS_PER_PAGE
    empty_ratio = empty_pages / total_pages
    mean_chars = chars_total / total_pages
    problems = []
    if empty_ratio > empty_page_max:
        problems.append(f"{empty_pages}/{total_pages} pages came back empty "
                        f"({empty_ratio:.1%} > T2N_OCR_EMPTY_PAGE_MAX {empty_page_max:.1%})")
    if mean_chars < min_chars_per_page:
        problems.append(f"mean {mean_chars:.0f} chars/page "
                        f"(< T2N_OCR_MIN_CHARS_PER_PAGE {min_chars_per_page})")
    if not problems:
        return None
    return ("OCR output failed quality check: " + "; ".join(problems) +
            ". This is what a silently-failed OCR run looks like — check the adapter's stderr, "
            "the render DPI, and whether the inference server actually had the model loaded. "
            "Raise the thresholds only if this book really is that sparse.")


# Four OCR layout blocks is already most of a two-column page, whereas the fitz path counts
# individual lines — same heuristic, different granularity, so the minimum differs.
OCR_COLUMN_MIN_BLOCKS = 4


def order_ocr_blocks(blocks: list, page_w: float) -> list[str]:
    """Reading order for one OCR'd page. `blocks` are `(x0, y0, x1, y1, text)` in image pixels.

    A plain `(y0, x0)` sort — what this path did before — is correct for single-column pages
    and WRONG for two-column ones: it walks across the gutter and back on every band of the
    page, interleaving the columns line by line. That is the same defect the fitz path has a
    dedicated column sort for, and it is invisible to every quality check downstream because
    the characters are all present and individually correct.

    So: try the shared column split first, fall back to the old `(y0, x0)` sort when the page
    is single-column or the split is ambiguous. `T2N_COLUMN_SORT=0` restores the old sort
    unconditionally, same kill-switch as the fitz path.
    """
    if not blocks:
        return []
    if page_w > 0 and os.environ.get("T2N_COLUMN_SORT", "1") != "0":
        ordered = column_order_boxes(blocks, page_w, 0.0, min_items=OCR_COLUMN_MIN_BLOCKS)
        if ordered is not None:
            return [b[4] for b in ordered]
    return [b[4] for b in sorted(blocks, key=lambda b: (b[1], b[0]))]


def surya_ocr_pdf(pdf_path: str, out_md_path: str, dpi: int = 300, batch_size: int = 20) -> dict:
    """OCR a PDF via Surya (GPU). Used when fitz extraction fails — CID Identity-H
    without ToUnicode or pure scan. Renders pages → calls adapter in isolated venv →
    writes full_text.md with <!-- page N --> markers. Returns stats dict.

    Raises if the assembled book trips ocr_quality_verdict(). Failing loud here is
    deliberate: a near-empty full_text.md that reports success is worse than no conversion,
    because every downstream step then treats the book as done.
    """
    import tempfile
    if not surya_available():
        raise RuntimeError(f"Surya venv not found at {SURYA_VENV_PY} (set SURYA_VENV_PY/SURYA_ADAPTER)")
    doc = fitz.open(pdf_path)
    total = doc.page_count
    print(f"  [SURYA] OCR {total} pages @ {dpi}dpi ...", flush=True)

    tmpdir = tempfile.mkdtemp(prefix="surya_")
    img_paths = []
    try:
        page_widths = []  # rendered pixel width per page — the coordinate space the adapter
                          # reports bboxes in, and what the column split needs to find the gutter
        for i in range(total):
            pix = doc[i].get_pixmap(dpi=dpi)
            p = os.path.join(tmpdir, f"p{i+1:04d}.png")
            pix.save(p)
            img_paths.append(p)
            page_widths.append(float(pix.width))
        doc.close()

        pages_text = ["" for _ in range(total)]
        parse_failures = 0      # stdout lines that were not valid JSON
        unmatched_lines = 0     # lines whose "fixture" did not resolve to a page index
        missing_lines = 0       # input images the adapter never emitted a line for
        for start in range(0, total, batch_size):
            batch = img_paths[start:start+batch_size]
            seen_in_batch = set()
            result = subprocess.run(
                [str(SURYA_VENV_PY), str(SURYA_ADAPTER)] + batch,
                capture_output=True, timeout=1800
            )
            if result.returncode != 0:
                # Windows subprocess stdout/stderr defaults to the system codepage
                # (e.g. cp950 on a Traditional Chinese locale), not UTF-8 — decode
                # with errors="replace" to avoid crashing on CJK output.
                raise RuntimeError(
                    f"Surya batch failed: {result.stderr.decode('utf-8', errors='replace')[:400]}"
                )
            for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    # Counted, not swallowed: a malformed line means a page's text is gone,
                    # and the old silent `continue` is exactly how a book ends up 20 KB.
                    parse_failures += 1
                    continue
                fname = obj.get("fixture", "")
                try:
                    stem = fname.rsplit(".", 1)[0]  # "p0011"
                    idx = int(stem[1:]) - 1
                except Exception:
                    unmatched_lines += 1
                    continue
                seen_in_batch.add(os.path.basename(fname))
                items = []
                for b in obj.get("blocks", []):
                    text = (b.get("text") or "").strip()
                    if not text:
                        continue
                    bbox = b.get("bbox") or [0, 0, 0, 0]
                    try:
                        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
                    except Exception:
                        # A block with an unusable bbox cannot be placed; keep its text but
                        # pin it at the origin, which is where the old `or [0,0,0,0]` default
                        # put it too.
                        x0 = y0 = x1 = y1 = 0.0
                    items.append((x0, y0, x1, y1, text))
                if 0 <= idx < total:
                    pages_text[idx] = "\n".join(order_ocr_blocks(items, page_widths[idx]))
                else:
                    unmatched_lines += 1

            batch_missing = [os.path.basename(p) for p in batch
                             if os.path.basename(p) not in seen_in_batch]
            if batch_missing:
                missing_lines += len(batch_missing)
                print(f"    [warn] adapter returned no line for {len(batch_missing)} image(s): "
                      f"{', '.join(batch_missing[:5])}"
                      f"{' ...' if len(batch_missing) > 5 else ''}", flush=True)

        empty_pages = sum(1 for t in pages_text if not t.strip())
        chars_total = sum(len(t.strip()) for t in pages_text)
        if parse_failures or unmatched_lines:
            print(f"    [warn] adapter output: {parse_failures} unparseable line(s), "
                  f"{unmatched_lines} line(s) with no matching page", flush=True)

        verdict = ocr_quality_verdict(total, empty_pages, chars_total)
        if verdict:
            raise RuntimeError(
                f"{verdict} (parse_failures={parse_failures}, missing_lines={missing_lines}, "
                f"unmatched_lines={unmatched_lines})")

        md = []
        for i in range(total):
            md.append(f"<!-- page {i+1} -->\n{pages_text[i]}\n")
        Path(out_md_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_md_path).write_text("\n".join(md), encoding="utf-8")
        size_kb = round(Path(out_md_path).stat().st_size / 1024, 1)
        return {"pages": total, "tables": 0, "fig_refs": 0, "size_kb": size_kb, "engine": "surya",
                "empty_pages": empty_pages, "parse_failures": parse_failures,
                "missing_lines": missing_lines, "unmatched_lines": unmatched_lines,
                "chars_total": chars_total}
    finally:
        for p in img_paths:
            try: os.remove(p)
            except Exception: pass
        try: os.rmdir(tmpdir)
        except Exception: pass


def batch_dir_convert(root_dir: Path, force: bool = False, force_surya: bool = False):
    """Convert all PDFs and EPUBs in root_dir recursively."""
    progress = load_progress()
    completed_set = set(progress["completed"])
    already_converted = load_already_converted()

    # Find all PDFs and EPUBs
    all_pdfs = []
    sources = sorted(root_dir.rglob("*.pdf")) + sorted(root_dir.rglob("*.epub"))
    for pdf in sources:
        rel = str(pdf.relative_to(root_dir))

        # Skip excluded folders
        skip = False
        for sf in SKIP_FOLDERS:
            if rel.startswith(sf):
                skip = True
                break
        if skip:
            continue

        # Skip already completed (unless force)
        if not force and rel in completed_set:
            continue

        all_pdfs.append((pdf, rel))

    print(f"Found {len(all_pdfs)} PDFs to convert (skipped {len(completed_set)} completed)")
    total_pages_done = 0
    total_tables_done = 0
    total_fig_refs_done = 0
    converted = 0
    skipped_scan = 0
    errors = []

    for idx, (pdf, rel) in enumerate(all_pdfs):
        # Determine output folder name from PDF filename
        folder_name = pdf.stem
        out_dir = OUTPUT_BASE / folder_name

        # Check if already has a high-quality conversion (curated via already_converted.json)
        if folder_name in already_converted:
            completed_set.add(rel)
            continue

        full_text_path = out_dir / "full_text.md"

        # Skip if md exists and is newer than PDF (unless force)
        if not force and full_text_path.exists():
            if full_text_path.stat().st_mtime > pdf.stat().st_mtime:
                completed_set.add(rel)
                continue

        is_epub = pdf.suffix.lower() == ".epub"

        # Decide if we should route to Surya OCR (scan or fitz silent failure or forced)
        ocr_reason = None
        if not is_epub:
            if force_surya:
                ocr_reason = "FORCE-SURYA"
            elif is_scan_only(str(pdf)):
                ocr_reason = "SCAN"
            elif detect_fitz_silent_failure(str(pdf)):
                ocr_reason = "FITZ-FAIL"

        if ocr_reason and not surya_available():
            print(f"  [{ocr_reason}] {pdf.name} - skipped (Surya unavailable)")
            skipped_scan += 1
            progress["errors"].append(f"[{ocr_reason}] {rel}")
            completed_set.add(rel)
            save_progress({"completed": list(completed_set), "errors": progress["errors"]})
            continue

        if ocr_reason:
            print(f"  [{ocr_reason}] {pdf.name} → Surya OCR", flush=True)
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                stats = surya_ocr_pdf(str(pdf), str(full_text_path))
                print(f"  [{converted + 1}/{len(all_pdfs)}] {pdf.name}: {stats['pages']}p (surya), "
                      f"{stats['size_kb']} KB | empty={stats['empty_pages']} "
                      f"chars/pg={stats['chars_total'] // max(stats['pages'], 1)} "
                      f"parse_fail={stats['parse_failures']} missing={stats['missing_lines']}",
                      flush=True)
                converted += 1
                total_pages_done += stats["pages"]

                # Try chapter split on Surya output — numbered-heading pattern often survives OCR
                try:
                    content = full_text_path.read_text(encoding='utf-8')
                    chapters = split_into_chapters(content, out_dir, stats["pages"], pdf_path=str(pdf))
                    if chapters:
                        print(f"    chapters detected: {len(chapters)}", flush=True)
                        add_pdf_bookmarks(str(pdf), chapters)
                except Exception as se:
                    print(f"    [warn] chapter split on Surya output failed: {se}", flush=True)
            except Exception as e:
                err_msg = f"{rel}: surya OCR failed: {e}"
                errors.append(err_msg)
                print(f"  [ERR] {pdf.stem}: surya OCR failed: {e}", flush=True)
            completed_set.add(rel)
            progress["completed"] = list(completed_set)
            progress["errors"] = list(set(progress.get("errors", []) + errors))
            save_progress(progress)
            continue

        # Convert
        try:
            label = pdf.stem.replace('_', ' ').replace('-', ' ')
            out_dir.mkdir(parents=True, exist_ok=True)
            if is_epub:
                stats = convert_epub(str(pdf), out_dir, label)
                print(f"  [{converted + 1}/{len(all_pdfs)}] {pdf.name}: epub, {stats['chapters']} chapters, {stats['words']} words", flush=True)
            else:
                stats = convert_pdf(str(pdf), str(full_text_path), label)

                # Chapter split (prefer PDF bookmarks, fallback to pattern detection)
                content = full_text_path.read_text(encoding='utf-8')
                chapters = split_into_chapters(content, out_dir, stats["pages"], pdf_path=str(pdf))

                # Add PDF bookmarks if chapters detected and PDF has none
                if chapters:
                    add_pdf_bookmarks(str(pdf), chapters)

                rj = f", {stats['rejected_tables']} page-frame rejected" if stats.get("rejected_tables") else ""
                print(f"  [{converted + 1}/{len(all_pdfs)}] {pdf.name}: {stats['pages']}p, {stats['tables']}t, {stats['fig_refs']}f{rj}", flush=True)
                for w in stats.get("warnings", []):
                    print(f"    [WARN] {pdf.stem}: {w}", flush=True)
                    errors.append(f"[TABLE-WARN] {rel}: {w}")

            converted += 1
            total_pages_done += stats["pages"]
            total_tables_done += stats["tables"]
            total_fig_refs_done += stats["fig_refs"]

        except Exception as e:
            err_msg = f"{rel}: {e}"
            errors.append(err_msg)
            print(f"  [ERR] {pdf.stem}: {e}", flush=True)

        # Save progress after each book
        completed_set.add(rel)
        progress["completed"] = list(completed_set)
        progress["errors"] = list(set(progress.get("errors", []) + errors))
        save_progress(progress)

    print(f"\n=== Batch-dir done ===")
    print(f"Converted: {converted} | Scanned (skipped): {skipped_scan} | Errors: {len(errors)}")
    print(f"Pages: {total_pages_done} | Tables: {total_tables_done} | Fig refs: {total_fig_refs_done}")
    if errors:
        print(f"Errors:")
        for e in errors[:10]:
            print(f"  {e}")


# === CLI ===
def main():
    parser = argparse.ArgumentParser(description="Convert PDF/EPUB textbooks to searchable markdown")
    parser.add_argument("pdf_path", nargs="?", help="Path to a single PDF/EPUB to convert")
    parser.add_argument("output_path", nargs="?", help="Output markdown path (or folder, for epub)")
    parser.add_argument("--book-label", default="", help="Label for the book/chapter")
    parser.add_argument("--batch", metavar="KEY", help="Batch convert a book defined in BOOKS_DIR/books.json, or 'all'")
    parser.add_argument("--batch-dir", metavar="DIR", help="Convert all PDFs/EPUBs in directory recursively")
    parser.add_argument("--force", action="store_true", help="Re-convert even if md exists")
    parser.add_argument("--force-surya", action="store_true", help="Force Surya OCR for all PDFs in batch-dir (bypass fitz)")

    args = parser.parse_args()

    if args.batch_dir:
        batch_dir_convert(Path(args.batch_dir), force=args.force, force_surya=args.force_surya)

    elif args.batch:
        books = load_books()
        if not books:
            print(f"No books.json found at {BOOKS_JSON}. Example format:\n")
            print(json.dumps({
                "example_book": {
                    "label": "My Textbook 6th Edition",
                    "src": "./books/MyTextbook",
                    "out": "./output/MyTextbook_6e",
                    "glob": "*.pdf",
                }
            }, indent=2))
            sys.exit(1)
        keys = resolve_book_keys(books, args.batch)
        book_names = ", ".join(books[k]["label"] for k in keys)
        print(f"Batch converting: {book_names}")
        start = time.time()
        stats = batch_convert(books, keys)
        elapsed = time.time() - start
        print(f"\n=== Done in {elapsed:.0f}s ===")
        print(f"Converted: {stats['converted']} | Skipped: {stats['skipped']} | Errors: {stats['errors']}")
        print(f"Pages: {stats['total_pages']} | Tables: {stats['total_tables']} | Fig refs: {stats['total_fig_refs']}")
        print(f"Total size: {stats['total_size_mb']} MB")
        if stats["error_details"]:
            print(f"Errors: {json.dumps(stats['error_details'], indent=2)}")
        # Write summary
        summary_path = OUTPUT_BASE / "conversion_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        print(f"Summary saved: {summary_path}")

    elif args.pdf_path:
        pdf_path = args.pdf_path

        if Path(pdf_path).suffix.lower() == ".epub":
            # epub -> folder of chapter md files (output_path treated as a folder)
            if args.output_path:
                out_dir = Path(args.output_path)
            else:
                stem = Path(pdf_path).stem.replace(" ", "_").replace(",", "")
                out_dir = OUTPUT_BASE / stem
            label = args.book_label or out_dir.name
            print(f"Converting epub: {pdf_path}")
            try:
                stats = convert_epub(pdf_path, out_dir, label)
            except RuntimeError as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
            print(f"[OK] epub | {stats['chapters']} chapters | {stats['words']} words | {stats['size_kb']} KB")
            print(f"Output: {out_dir}")
        else:
            if not args.output_path:
                # Auto-generate output path using the SAME book-folder convention
                # --batch-dir uses (OUTPUT_BASE/<pdf stem>/full_text.md) — not a
                # separate misc/ tree — so a later --batch-dir run recognizes this
                # file as already converted (should_convert()/mtime check) instead
                # of duplicating it under a differently-named markdown.
                stem = Path(pdf_path).stem
                out_path = str(OUTPUT_BASE / stem / "full_text.md")
            else:
                out_path = args.output_path

            label = args.book_label or Path(pdf_path).stem
            print(f"Converting: {pdf_path}")
            stats = convert_pdf(pdf_path, out_path, label)
            print(f"[OK] {stats['pages']} pages | {stats['tables']} tables | {stats['fig_refs']} fig refs | {stats['size_kb']} KB")
            if stats.get("sideways_pages"):
                print(f"     {stats['sideways_pages']} sideways page(s) re-parsed upright (Fix 3)")
            if stats.get("rejected_tables"):
                print(f"     {stats['rejected_tables']} page-frame pseudo-table(s) rejected")
            for w in stats.get("warnings", []):
                print(f"[WARN] {w}")
            print(f"Output: {out_path}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
