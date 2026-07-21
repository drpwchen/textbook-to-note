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
import os
import re
import shutil
import sys
import time
import argparse
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import BOOKS_DIR, OUTPUT_DIR, SURYA_VENV_PY, SURYA_ADAPTER, SKIP_FOLDERS

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
def clean_text(text: str) -> str:
    """Remove control chars, soft hyphens, join broken lines."""
    # Remove control characters (keep \n and \t)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
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
SEP = r'[-‐‑‒–—―−.]'
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

    if len(lines) < 4:
        return None

    page_w = page.rect.width
    center = page.rect.x0 + page_w / 2.0
    tol = page_w * COLUMN_TOL_FRAC

    full, left, right = [], [], []
    for ln in lines:
        x0, _, x1, _, _ = ln
        if x0 < center - tol and x1 > center + tol:
            full.append(ln)              # spans both columns → header/title/full-width para
        elif (x0 + x1) / 2.0 < center:
            left.append(ln)
        else:
            right.append(ln)

    # Need two genuine clusters, or it's not a two-column page.
    if len(left) < COLUMN_MIN_LINES or len(right) < COLUMN_MIN_LINES:
        return None
    # Gutter integrity: left lines must end before center and right lines start after it
    # (with tolerance). Overlap across the center means the split is ambiguous → fall back.
    if max(l[2] for l in left) > center + tol:
        return None
    if min(r[0] for r in right) < center - tol:
        return None

    # Emit in bands split by full-width lines: for each band, left column top-to-bottom then
    # right column top-to-bottom, with the full-width separator between bands. A line's band
    # is the count of full-width lines above it, so top full-width blocks (e.g. a heading) and
    # their bands come first.
    full.sort(key=lambda l: l[1])

    def band(ln):
        return sum(1 for f in full if f[1] <= ln[1])

    ordered = []
    for bi in range(len(full) + 1):
        ordered.extend(sorted((l for l in left if band(l) == bi), key=lambda l: l[1]))
        ordered.extend(sorted((r for r in right if band(r) == bi), key=lambda r: r[1]))
        if bi < len(full):
            ordered.append(full[bi])

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
def extract_tables_md(plumber_page) -> list[str]:
    """Extract tables from a pdfplumber page, return as markdown strings."""
    tables = plumber_page.extract_tables()
    if not tables:
        return []

    md_tables = []
    for table in tables:
        if not table or len(table) < 2:
            continue

        # Clean cells
        cleaned = []
        for row in table:
            cleaned_row = []
            for cell in row:
                if cell is None:
                    cleaned_row.append("")
                else:
                    cleaned_row.append(clean_text(str(cell)).replace("\n", " ").strip())
            cleaned.append(cleaned_row)

        # Filter: keep tables with >5% content cells (low bar — prefer to keep)
        content_cells = sum(1 for row in cleaned for cell in row if cell.strip())
        total_cells = sum(len(row) for row in cleaned)
        if total_cells == 0 or content_cells / total_cells < 0.05:
            continue

        # Normalize column count
        max_cols = max(len(r) for r in cleaned)
        if max_cols == 0:
            continue
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

        md_tables.append("\n".join(lines))

    return md_tables


# === Core conversion ===
def convert_pdf(pdf_path: str, out_path: str, book_label: str) -> dict:
    """Convert a single PDF to markdown. Returns stats dict."""
    doc = fitz.open(pdf_path)
    plumber = pdfplumber.open(pdf_path)
    total_pages = len(doc)

    output = [f"# {book_label}\n"]
    output.append(f"Source: `{pdf_path}`\n")

    total_tables = 0
    total_fig_refs = 0
    table_gate_on = os.environ.get("T2N_TABLE_GATE", "1") != "0"

    for i in range(total_pages):
        # Improvement 1: two-column reading order. column_sorted_text() returns None for
        # single-column / ambiguous pages, so those keep the exact pre-change get_text() path.
        ordered = column_sorted_text(doc[i])
        if ordered is None:
            page_text = clean_text(doc[i].get_text().strip())
        else:
            page_text = clean_text(ordered.strip())

        # Annotate figure/table references
        page_text, fig_count = annotate_fig_refs(page_text, i + 1)
        total_fig_refs += fig_count

        output.append(f"\n<!-- page {i+1} -->\n")
        output.append(page_text)

        # Improvement 2: only run the expensive pdfplumber pass on pages that plausibly hold a
        # table. Skipped pages never touch plumber.pages[i], so they don't pay the parse cost.
        try:
            if table_gate_on and not page_has_table_candidate(doc[i], page_text):
                md_tables = []
            else:
                md_tables = extract_tables_md(plumber.pages[i])
        except Exception:
            md_tables = []
        for j, tbl in enumerate(md_tables):
            total_tables += 1
            output.append(f"\n**[Table on page {i+1}]**\n")
            output.append(tbl)
            output.append("")

    doc.close()
    plumber.close()

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
                print(f"  ✓ {pdf.name} ({stats['pages']}p, {stats['tables']}t, {stats['fig_refs']}f, {stats['size_kb']}KB)")
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
    text = ft.read_text(encoding="utf-8")

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


def surya_ocr_pdf(pdf_path: str, out_md_path: str, dpi: int = 300, batch_size: int = 20) -> dict:
    """OCR a PDF via Surya (GPU). Used when fitz extraction fails — CID Identity-H
    without ToUnicode or pure scan. Renders pages → calls adapter in isolated venv →
    writes full_text.md with <!-- page N --> markers. Returns stats dict.
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
        for i in range(total):
            pix = doc[i].get_pixmap(dpi=dpi)
            p = os.path.join(tmpdir, f"p{i+1:04d}.png")
            pix.save(p)
            img_paths.append(p)
        doc.close()

        pages_text = ["" for _ in range(total)]
        for start in range(0, total, batch_size):
            batch = img_paths[start:start+batch_size]
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
                    continue
                fname = obj.get("fixture", "")
                try:
                    stem = fname.rsplit(".", 1)[0]  # "p0011"
                    idx = int(stem[1:]) - 1
                except Exception:
                    continue
                items = []
                for b in obj.get("blocks", []):
                    text = (b.get("text") or "").strip()
                    if not text:
                        continue
                    bbox = b.get("bbox") or [0, 0, 0, 0]
                    items.append((bbox[1], bbox[0], text))
                items.sort()
                if 0 <= idx < total:
                    pages_text[idx] = "\n".join(t for _, _, t in items)

        md = []
        for i in range(total):
            md.append(f"<!-- page {i+1} -->\n{pages_text[i]}\n")
        Path(out_md_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_md_path).write_text("\n".join(md), encoding="utf-8")
        size_kb = round(Path(out_md_path).stat().st_size / 1024, 1)
        return {"pages": total, "tables": 0, "fig_refs": 0, "size_kb": size_kb, "engine": "surya"}
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
                print(f"  [{converted + 1}/{len(all_pdfs)}] {pdf.name}: {stats['pages']}p (surya), {stats['size_kb']} KB", flush=True)
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

                print(f"  [{converted + 1}/{len(all_pdfs)}] {pdf.name}: {stats['pages']}p, {stats['tables']}t, {stats['fig_refs']}f", flush=True)

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
            print(f"Output: {out_path}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
