"""split_manifest — the per-book `_manifest.json` contract: build helpers and the validator.

openspec change `split-manifest-contract`. A manifest describes one book folder of a converted
corpus: which chapter file holds which PDF pages, the book's real chapter number for each file,
the printed page label for each PDF page (only when the PDF itself defines labels), and a
`kind` for every file and bookmark so an indexer can tell References / front / back matter
from body text. Nothing here writes markdown; it only reads it.

  python converter/split_manifest.py --validate <corpus> [--book NAME ...] [--json report.json]
      exit 0 every validated book passes · exit 1 any book fails · exit 2 bad arguments

Schema 1 (fields may be ADDED in later versions, never renamed or re-defined):

  {"schema": 1, "book": "<folder>", "source_pdf": "<file name>" | null,
   "split_method": "bookmarks|config|pattern|per_chapter_pdf|carried",
   "page_labels": {"<pdf page>": "<printed label>"} | null,
   "page_confidence": "printed|pdf_only",
   "files": [{"file": "ch02_x.md", "seq": 2, "chapter_number": 1 | null, "title": "...",
              "pdf_page_start": 12 | null, "pdf_page_end": 29 | null,
              "kind": "body|references|front|back",
              # added fields
              "level": 2 | null, "source_pdf": "<file name>" | null,
              "page_confidence": "printed|pdf_only"}],
   "sections": [{"level": 3, "title": "...", "pdf_page": 28 | null, "file": "..." | null,
                 "kind": "..."}],
   # added fields
   "page_label_sets": {"<source pdf file name>": {"<pdf page>": "<label>"} | null},
   "toc_chapters": 12 | null, "chapter_level": 2 | null, "generator": "...",
   "notes": ["..."]}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1
MANIFEST_NAME = "_manifest.json"
SPLIT_METHODS = ("bookmarks", "config", "pattern", "per_chapter_pdf", "carried")
KINDS = ("body", "references", "front", "back")

# md files that are never chapter files (never listed in a manifest, never validated as one)
NON_CHAPTER_MD = {"full_text.md"}
# directories never copied, listed or validated (figure crops, backups, raw sources)
SKIP_DIR_RE = re.compile(r"^(figures.*|images|_epub_src|__pycache__|\.drafts_bak)$|\.bak$", re.I)

PAGE_MARKER_RE = re.compile(r"^\s*<!-- page (\d+) -->\s*$")
# ![alt](path) and Obsidian ![[path]]
IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\(<?([^)>\s]+)>?[^)]*\)|!\[\[([^\]]+)\]\]")

# --- title helpers -------------------------------------------------------------------------

_ROMAN = r"(?=[ivxlcdm]+\b)m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_LEADING_NUMBERING_RE = re.compile(
    r"^\s*(?:chapter\s*(?:\d{1,3}|[a-z]+(?:-[a-z]+)?)\b|(?:chap|ch)\.?\s*\d{1,3}\b|"
    r"(?:part|section|unit|module)\b\s*(?:\d{1,3}|[ivxlcdm]+|[a-z]+(?:-[a-z]+)?)\b|\d{1,3}(?=[A-Za-z])|"
    r"\d{1,3}(?:\.\d{1,3})*\b|" + _ROMAN + r"\.)\s*[.:)\-–—]*\s*",
    re.I,
)


def norm_text(s: str) -> str:
    """casefold, NFKC, every non-alphanumeric run → one space."""
    s = (s or "").split("\x00")[0]  # outline titles sometimes carry UTF-16 garbage after a NUL
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))  # Calvé → Calve
    s = unicodedata.normalize("NFKC", s).casefold()
    s = re.sub(r"[^0-9a-zÀ-ɏ㐀-鿿]+", " ", s)
    return " ".join(s.split())


def strip_numbering(title: str) -> str:
    """'Chapter 5 Stroke' / '5 - Stroke' / '12.3 Stroke' / 'IV. Stroke' → 'Stroke'."""
    t = (title or "").strip()
    stripped = _LEADING_NUMBERING_RE.sub("", t, count=1).strip()
    return stripped


_WORDNUM = {}
for _i, _w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()):
    _WORDNUM[_w] = _i
for _v, _w in ((20, "twenty"), (30, "thirty"), (40, "forty"), (50, "fifty"), (60, "sixty"),
               (70, "seventy"), (80, "eighty"), (90, "ninety")):
    _WORDNUM[_w] = _v
    for _i, _o in enumerate("one two three four five six seven eight nine".split(), 1):
        _WORDNUM[f"{_w}-{_o}"] = _v + _i

CHAPTER_NUMBER_PATTERNS = (
    re.compile(r"^\s*(?:chapter|chap\.?|ch\.?)\s*(\d{1,3})\b", re.I),
    # "5 Stroke", "5. Stroke", "5 - Stroke", "5: Stroke", "05_Stroke" — never "12.3 Stroke"
    re.compile(r"^\s*(\d{1,3})(?!\.\d)(?:\s*[.:)_\-–—]\s*|\s+)(?=[^\d\s.])", re.I),
)
_WORD_CHAPTER_RE = re.compile(r"^\s*chapter\s+([a-z]+(?:-[a-z]+)?)\b", re.I)


def parse_chapter_number(title: str, regex: str | None = None) -> int | None:
    """The real chapter number written in a title, or None. Never a sequence number."""
    t = (title or "").strip()
    if regex:
        m = re.search(regex, t, re.I)
        if not m:
            return None
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            return None
    for pat in CHAPTER_NUMBER_PATTERNS:
        m = pat.match(t)
        if m:
            return int(m.group(1))
    m = _WORD_CHAPTER_RE.match(t)
    if m and m.group(1).lower() in _WORDNUM:
        return _WORDNUM[m.group(1).lower()]
    return None


_REFERENCES_RE = re.compile(
    r"^(?:(?:selected|suggested|recommended|further|additional|key|general|cited)\s+)?"
    r"(?:references?|bibliography|readings?|reading list|literature cited|works cited|"
    r"reference list|sources)"
    r"(?:\s+(?:and|&)\s+(?:(?:suggested|further|recommended|additional)\s+)?(?:readings?|references?|resources))?$"
)
_FRONT_RE = re.compile(
    r"^(?:front matter|(?:front )?cover|half title(?: page)?|title(?: page)?|copyright(?: page)?|"
    r"dedication|epigraph|(?:brief |detailed |table of )?contents(?: at a glance)?|"
    r"preface\b.*|foreword\b.*|acknowledge?ments?|(?:list of )?contributors|contributing authors|"
    r"about the (?:authors?|editors?|book)|editors?|reviewers|series page|in memoriam|"
    r"how to use this book|(?:list of )?abbreviations|frontmatter|fmatter|inside front cover|"
    r"cover page|half ?title\b.*|title(?: page| volume)\b.*|evolve page|series|blank page|untitled|"
    r"isbn\b.*|about the contributors)$"
)
_BACK_RE = re.compile(r"^(?:(?:subject |author )?index|back matter|(?:inside )?back cover|ad|"
                      r"advertisement|backmatter|bmatter|colou?r plates?)$")


def classify_kind(title: str) -> str:
    """body | references | front | back, from the whole title (leading numbering ignored)."""
    t = norm_text(strip_numbering(title)) or norm_text(title)
    if not t:
        return "body"
    if _REFERENCES_RE.match(t):
        return "references"
    if _FRONT_RE.match(t):
        return "front"
    if _BACK_RE.match(t):
        return "back"
    return "body"


def strictly_increasing(numbers) -> bool:
    vals = [n for n in numbers if n is not None]
    return all(b > a for a, b in zip(vals, vals[1:]))


def choose_chapter_level(entries, regex: str | None = None, min_numbers: int = 3):
    """entries: [(level, title)] in outline order. Returns (level | None, reason).

    The chapter level is the outline level whose numbered titles are strictly increasing and
    most numerous (ties → the shallower level). A level whose numbers go backwards is never
    chosen; if numbered levels exist but none increases, the reason says so.

    Fewer than `min_numbers` numbered titles everywhere (an excerpt, a one- or two-chapter PDF):
    a level whose every numbered title is written `Chapter N` and increases is still the chapter
    level — the title says so in words, which a bare `5 Stroke` does not. Only when no level
    qualifies otherwise and none goes backwards."""
    by_level: dict[int, list] = {}
    titles_by_level: dict[int, list] = {}
    for lvl, title in entries:
        by_level.setdefault(lvl, []).append(parse_chapter_number(title, regex))
        titles_by_level.setdefault(lvl, []).append(title)
    best = None
    inconsistent = []
    for lvl in sorted(by_level):
        nums = [n for n in by_level[lvl] if n is not None]
        if len(nums) < min_numbers:
            continue
        if not strictly_increasing(nums):
            inconsistent.append(lvl)
            continue
        if best is None or len(nums) > best[1]:
            best = (lvl, len(nums))
    if best:
        return best[0], "ok"
    if inconsistent:
        return None, f"numbers not increasing at level(s) {inconsistent}"
    few = None
    for lvl in sorted(by_level):
        numbered = [(n, t) for n, t in zip(by_level[lvl], titles_by_level[lvl]) if n is not None]
        if not numbered or not strictly_increasing([n for n, _ in numbered]):
            continue
        if all(keyword_chapter_number(t) == n for n, t in numbered) and \
                (few is None or len(numbered) > few[1]):
            few = (lvl, len(numbered))
    if few:
        return few[0], "few chapters, titled `Chapter N`"
    return None, "no numbered titles"


KEYWORD_CHAPTER_RE = re.compile(r"^\s*(?:chapter|chap\.)\s*(\d{1,3})\b", re.I)


def keyword_chapter_number(title: str) -> int | None:
    """N for a title written `Chapter N` / `Chap. N`; None for anything else (`7 Gait` too)."""
    m = KEYWORD_CHAPTER_RE.match(title or "")
    return int(m.group(1)) if m else None


def promote_keyword_chapters(entries, chapter_level: int | None, regex: str | None = None):
    """entries: [(level, title, pdf_page | None)] in outline order. Returns (levels, promoted).

    An entry titled `Chapter N` at another level than the chapter level, whose N no
    chapter-level entry carries, moves to the chapter level together with the entries below it
    (until the next entry at its original level or shallower). Entries without a page, or whose
    page is lower than the last chapter-level page before them, stay where they are."""
    levels = [e[0] for e in entries]
    if chapter_level is None:
        return levels, 0
    taken = {parse_chapter_number(e[1], regex) for e in entries if e[0] == chapter_level} - {None}
    promoted = 0
    last_page = 0
    i = 0
    while i < len(entries):
        lvl, title, page = entries[i][0], entries[i][1], entries[i][2]
        n = keyword_chapter_number(title)
        if (levels[i] != chapter_level and lvl == levels[i] and n is not None and n not in taken
                and page is not None and page >= last_page):
            delta = chapter_level - lvl
            j = i + 1
            while j < len(entries) and entries[j][0] > lvl:
                levels[j] = max(1, entries[j][0] + delta)
                j += 1
            levels[i] = chapter_level
            taken.add(n)
            promoted += 1
        if levels[i] == chapter_level and page is not None:
            last_page = max(last_page, page)
        i += 1
    return levels, promoted


def chapter_state_walk(entries, chapter_level: int | None, regex: str | None = None):
    """For each outline entry (level, title) in order, the chapter number in force at it:
    its own number at the chapter level, inherited below it, None above it."""
    out = []
    current = None
    for lvl, title in entries:
        if chapter_level is None:
            out.append(None)
            continue
        if lvl == chapter_level:
            current = parse_chapter_number(title, regex)
            out.append(current)
        elif lvl < chapter_level:
            current = None
            out.append(None)
        else:
            out.append(current)
    return out


# --- page helpers --------------------------------------------------------------------------

def page_markers(text: str) -> list[int]:
    return [int(m.group(1)) for m in (PAGE_MARKER_RE.match(l) for l in text.split("\n")) if m]


def page_range(text: str):
    pages = page_markers(text)
    if not pages:
        return None, None
    return min(pages), max(pages)


def has_text(text: str) -> bool:
    """≥1 non-whitespace character on a line that is not a page marker."""
    return any(l.strip() for l in text.split("\n") if not PAGE_MARKER_RE.match(l))


def textless_pages(full_text: str) -> set[int]:
    """Pages of a full_text.md whose body (between its marker and the next) has no text."""
    out = set()
    cur = None
    buf_has = False
    for line in full_text.split("\n"):
        m = PAGE_MARKER_RE.match(line)
        if m:
            if cur is not None and not buf_has:
                out.add(cur)
            cur = int(m.group(1))
            buf_has = False
        elif line.strip():
            buf_has = True
    if cur is not None and not buf_has:
        out.add(cur)
    return out


def first_pages_text(text: str, n_pages: int = 2, max_chars: int = 20000) -> str:
    """The text of a file's first `n_pages` page-marker sections (or its head when it has none)."""
    lines = text.split("\n")
    out = []
    seen = 0
    for line in lines:
        if PAGE_MARKER_RE.match(line):
            seen += 1
            if seen > n_pages:
                break
            continue
        out.append(line)
        if sum(len(x) for x in out) > max_chars:
            break
    return "\n".join(out)


FILENAME_TITLE_RE = re.compile(r"\.(?:jpe?g|png|tiff?|pdf)\s*$|[0-9a-f]{16,}|webpdf\s*$", re.I)
GENERIC_TITLE_RE = re.compile(r"^(front matter|pages? \d+.*|part [ivxlcdm\d]+|section [ivxlcdm\d]+)$",
                              re.I)


def title_matches_text(title: str, text: str) -> bool | None:
    """Does a chapter title appear at the start of its file? None = title too generic to test.

    Pass when the numbering-stripped title is a substring of the first two pages (whitespace
    and punctuation ignored, so line wraps and hyphenation at a space do not matter), or when
    ≥ 80% of its words of ≥ 3 letters occur there."""
    if FILENAME_TITLE_RE.search(title or ""):
        return None  # an outline entry named after a file or a hash says nothing about the text
    core = norm_text(strip_numbering(title))
    if len(core.replace(" ", "")) < 4 or GENERIC_TITLE_RE.match(core):
        return None
    hay = norm_text(first_pages_text(text))
    if core in hay or core.replace(" ", "") in hay.replace(" ", ""):
        return True
    words = [w for w in core.split() if len(w) >= 3]
    if not words:
        return None
    hay_words = set(hay.split())
    hay_flat = hay.replace(" ", "")
    long_words = [w for w in hay_words if len(w) >= 5]

    def present(w):
        if w in hay_words or w in hay_flat:
            return True
        # a publisher typo in the outline ("Chatper", "Motot", "Kienbcks"): a word of >= 5
        # letters within similarity 0.8 of a word in the text
        if len(w) >= 5:
            import difflib
            return bool(difflib.get_close_matches(w, long_words, n=1, cutoff=0.8))
        return False

    hit = sum(1 for w in words if present(w))
    return hit / len(words) >= 0.8


# --- deep bookmarks → headings (openspec change resplit-deep-bookmarks) --------------------

def _heading_key(s: str) -> str:
    return norm_text(strip_numbering(s)) or norm_text(s)


def insert_deep_headings(out_dir: Path, written: list[str], bookmarks, split_level: int = 2):
    """Insert `## title` (first level below the split levels) / `### title` (deeper) lines into
    the chapter files just written, each directly above the first line of the bookmark's page
    whose normalised text equals the normalised title (or equals it once joined with the next
    line). No match → nothing inserted. Never deletes, reorders or edits an existing line.
    Returns (inserted, total)."""
    out_dir = Path(out_dir)
    files: dict[str, list[str]] = {}
    page_loc: dict[int, tuple[str, int]] = {}
    for name in written:
        if name.startswith("ch00_"):
            continue
        lines = (out_dir / name).read_text(encoding="utf-8").split("\n")
        files[name] = lines
        for i, line in enumerate(lines):
            m = PAGE_MARKER_RE.match(line)
            if m:
                page_loc.setdefault(int(m.group(1)), (name, i))
    inserts: dict[str, dict[int, list[str]]] = {}
    last_hit: dict[int, int] = {}
    total = inserted = 0
    for level, title, page in bookmarks:
        if level <= split_level:
            continue
        total += 1
        loc = page_loc.get(page)
        target = _heading_key(title)
        if not loc or not target:
            continue
        name, mi = loc
        lines = files[name]
        j = max(mi + 1, last_hit.get(page, -1) + 1)
        hit = None
        while j < len(lines) and not PAGE_MARKER_RE.match(lines[j]):
            cur = _heading_key(lines[j])
            if cur and cur == target:
                hit = j
                break
            if cur and target.startswith(cur) and j + 1 < len(lines) \
                    and not PAGE_MARKER_RE.match(lines[j + 1]):
                if _heading_key(lines[j] + " " + lines[j + 1]) == target:
                    hit = j
                    break
            j += 1
        if hit is None:
            continue
        last_hit[page] = hit
        hashes = "##" if level == split_level + 1 else "###"
        inserts.setdefault(name, {}).setdefault(hit, []).append(f"{hashes} {' '.join(title.split())}")
        inserted += 1
    for name, at in inserts.items():
        lines = files[name]
        out = []
        for i, line in enumerate(lines):
            out.extend(at.get(i, ()))
            out.append(line)
        (out_dir / name).write_text("\n".join(out), encoding="utf-8")
    return inserted, total


# --- PDF metadata (read-only) --------------------------------------------------------------

def _writeback_tail(data: bytes, page_count: int) -> bool:
    """True when the PDF's last incremental update only added an outline that the previous
    revision did not have — the signature of converter ≤ 0.7.3 writing guessed bookmarks."""
    import fitz
    ends = [m.start() for m in re.finditer(rb"%%EOF", data)]
    if len(ends) < 2:
        return False
    prev_end = ends[-2] + len(b"%%EOF")
    tail = data[prev_end:]
    if len(tail) > 1_000_000 or b"/Outlines" not in tail:
        return False
    try:
        prev = fitz.open(stream=data[:prev_end], filetype="pdf")
        try:
            return prev.page_count == page_count and not prev.get_toc()
        finally:
            prev.close()
    except Exception:
        return False


def read_pdf_meta(pdf_path) -> dict:
    """One read of a PDF: outline, page labels (only from its own page-label table), page
    count, and whether its outline was written by an old converter. Never writes."""
    import fitz
    data = Path(pdf_path).read_bytes()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        toc = doc.get_toc()
        labels = None
        if doc.get_page_labels():
            got = {}
            for i in range(doc.page_count):
                lab = doc[i].get_label()
                if lab:
                    got[str(i + 1)] = lab
            labels = got or None
        writeback = bool(toc) and all(e[0] == 1 for e in toc) and _writeback_tail(data, doc.page_count)
        return {"toc": toc, "page_labels": labels, "page_count": doc.page_count,
                "writeback": writeback}
    finally:
        doc.close()


# --- validator -----------------------------------------------------------------------------

def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def listed_md_files(book_dir: Path) -> list[str]:
    """Every chapter md file under a book folder (relative, '/' separated)."""
    out = []
    for root, dirs, files in os.walk(book_dir):
        dirs[:] = [d for d in dirs if not SKIP_DIR_RE.search(d)]
        for f in files:
            if f.endswith(".md") and f not in NON_CHAPTER_MD:
                rel = (Path(root) / f).relative_to(book_dir).as_posix()
                out.append(rel)
    return sorted(out, key=_natural_key)


def validate_book(book_dir: Path, manifest: dict | None = None, figures_root=None) -> dict:
    """Check one book folder against its manifest. Returns
    {"book", "ok", "errors": [{"check", "file"?, "detail"}], "warnings": [...], "stats": {...}}."""
    book_dir = Path(book_dir)
    errors, warnings = [], []
    res = {"book": book_dir.name, "ok": False, "errors": errors, "warnings": warnings, "stats": {}}

    def err(check, detail, file=None):
        e = {"check": check, "detail": detail}
        if file:
            e["file"] = file
        errors.append(e)

    if manifest is None:
        p = book_dir / MANIFEST_NAME
        if not p.exists():
            err("manifest_missing", "no _manifest.json")
            return res
        try:
            manifest = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            err("manifest_unreadable", str(e))
            return res

    # schema
    for key in ("schema", "book", "source_pdf", "split_method", "page_labels", "page_confidence",
                "files", "sections"):
        if key not in manifest:
            err("schema", f"missing key {key!r}")
    if errors:
        return res
    if manifest["schema"] != SCHEMA_VERSION:
        err("schema", f"schema {manifest['schema']!r} != {SCHEMA_VERSION}")
    if manifest["split_method"] not in SPLIT_METHODS:
        err("schema", f"split_method {manifest['split_method']!r}")
    if manifest["page_confidence"] not in ("printed", "pdf_only"):
        err("schema", f"page_confidence {manifest['page_confidence']!r}")
    if (manifest["page_labels"] is None) != (manifest["page_confidence"] == "pdf_only") and \
            manifest["source_pdf"] is not None:
        err("schema", "page_confidence disagrees with page_labels")
    if manifest["book"] != book_dir.name:
        err("schema", f"book {manifest['book']!r} != folder {book_dir.name!r}")

    files = manifest["files"]
    names = [f.get("file") for f in files]
    on_disk = listed_md_files(book_dir)

    # every md file listed, every listed file present, no duplicates
    dup = {n for n in names if names.count(n) > 1}
    for n in sorted(dup):
        err("duplicate_entry", "listed twice", n)
    for n in sorted(set(on_disk) - set(names)):
        err("unlisted_file", "md file not in manifest", n)
    for n in sorted(set(names) - set(on_disk)):
        err("missing_file", "listed in manifest but not on disk", n)

    texts = {}
    for f in files:
        n = f.get("file")
        if n in on_disk:
            texts[n] = (book_dir / n).read_text(encoding="utf-8", errors="replace")
        if f.get("kind") not in KINDS:
            err("schema", f"kind {f.get('kind')!r}", n)
        for k in ("seq", "chapter_number", "title", "pdf_page_start", "pdf_page_end", "kind"):
            if k not in f:
                err("schema", f"file entry missing {k!r}", n)

    # 1. no empty chapter file (page markers only counts as empty)
    for n, t in texts.items():
        if not has_text(t):
            err("empty_file", "no text outside page markers", n)

    # 2. page ranges match the file and are ordered, non-overlapping, gap only over blank pages
    units: dict[str, list] = {}
    for f in files:
        n = f.get("file")
        if n not in texts:
            continue
        lo, hi = page_range(texts[n])
        if (f.get("pdf_page_start"), f.get("pdf_page_end")) != (lo, hi):
            err("page_range", f"manifest {f.get('pdf_page_start')}-{f.get('pdf_page_end')} "
                              f"!= file markers {lo}-{hi}", n)
        if lo is not None:
            # one page sequence per (folder, source PDF): a chapter-per-PDF book restarts at 1
            # page_unit: a file that is its own PDF (per-chapter PDFs, whose names may repeat)
            unit_key = ("page_unit", f["page_unit"]) if f.get("page_unit") else \
                (n.rsplit("/", 1)[0] if "/" in n else "", f.get("source_pdf"))
            units.setdefault(unit_key, []).append((f.get("seq"), lo, hi, n))
    for (unit, _pdf), rows in units.items():
        if unit == "page_unit":
            continue  # one file, one PDF: nothing to overlap with
        ft_path = book_dir / unit / "full_text.md" if unit else book_dir / "full_text.md"
        blank = None
        if ft_path.exists():
            ft_text = ft_path.read_text(encoding="utf-8", errors="replace")
            present = set(page_markers(ft_text))
            # a page with no marker at all in full_text.md (extraction skipped it) holds no text
            blank = textless_pages(ft_text) | (set(range(1, max(present or {0}) + 1)) - present)
        if any(r[0] is None for r in rows):
            continue
        rows.sort(key=lambda r: r[0])
        for a, b in zip(rows, rows[1:]):
            if b[1] <= a[2]:
                err("page_overlap", f"pages {b[1]}-{b[2]} overlap {a[3]} ({a[1]}-{a[2]})", b[3])
            elif b[1] > a[2] + 1:
                gap = set(range(a[2] + 1, b[1]))
                if blank is None:
                    warnings.append({"check": "page_gap_unverified", "file": b[3],
                                     "detail": f"pages {a[2] + 1}-{b[1] - 1} in no file"})
                elif not gap <= blank:
                    err("page_gap", f"pages with text {sorted(gap - blank)[:5]} are in no file", b[3])

    # 3. chapter numbers: chapter-head numbers strictly increase; inherited ones never go back
    seq_sorted = sorted((f for f in files if f.get("file") in texts),
                        key=lambda f: (f.get("file").rsplit("/", 1)[0] if "/" in f.get("file") else "",
                                       f.get("seq") if f.get("seq") is not None else -1))
    last = None
    for f in seq_sorted:
        c = f.get("chapter_number")
        if c is None:
            continue
        if last is not None and c < last:
            err("chapter_order", f"chapter {c} after chapter {last}", f.get("file"))
        last = c if last is None else max(last, c)
    numbers = sorted({f.get("chapter_number") for f in files if f.get("chapter_number") is not None})
    toc = manifest.get("toc_chapters")
    if numbers:
        missing = sorted(set(range(numbers[0], numbers[-1] + 1)) - set(numbers))
        allowed = set(manifest.get("missing_chapters_ok") or [])
        if set(missing) - allowed:
            err("chapter_gap", f"chapter numbers missing: {sorted(set(missing) - allowed)[:20]}")
    # outline says `Chapter N` exists but no file carries N (merged into a neighbour, or unnumbered)
    if manifest["split_method"] != "per_chapter_pdf":
        allowed = set(manifest.get("missing_chapters_ok") or [])
        named = {keyword_chapter_number(s.get("title")) for s in manifest["sections"]} - {None}
        unmapped = sorted(named - set(numbers) - allowed)
        if unmapped:
            err("chapter_unmapped", f"outline chapters with no file: {unmapped[:20]}")
    if toc is not None:
        if len(numbers) != toc:
            err("toc_count", f"{len(numbers)} numbered chapters, TOC lists {toc}")
    elif numbers and manifest["split_method"] in ("pattern", "carried"):
        warnings.append({"check": "toc_unverified",
                         "detail": f"{len(numbers)} numbered chapters, no TOC count to compare"})
    if manifest.get("chapter_numbers_note"):
        warnings.append({"check": "chapter_numbers", "detail": manifest["chapter_numbers_note"]})
    # a book that was supposed to be numbered but where no file carries a number: the split
    # chose a chapter level, or its config named one / gave a chapter regex. A book with no
    # numbered titles at all (guidelines, handbooks) has neither and keeps passing.
    if not numbers:
        why = []
        if manifest.get("chapter_level") is not None:
            why.append(f"chapter level {manifest['chapter_level']} was chosen")
        if manifest.get("chapter_numbers_expected"):
            why.append(str(manifest["chapter_numbers_expected"]))
        if why:
            err("chapter_numbers_missing", "no file carries a chapter number, but " + "; ".join(why))

    # 4. chapter title found at the start of its file
    tested = matched = 0
    skip_titles = manifest.get("title_check_skip") or {}
    for f in files:
        n = f.get("file")
        if n not in texts or f.get("kind") != "body" or not f.get("title") or \
                manifest.get("titles_are_labels"):
            continue
        if n in skip_titles:  # per-book config, with its reason
            warnings.append({"check": "title_check_skipped", "file": n, "detail": str(skip_titles[n])})
            continue
        ok = title_matches_text(f["title"], texts[n])
        if ok is None:
            continue
        tested += 1
        if ok:
            matched += 1
        else:
            err("title_mismatch", f"title {f['title'][:70]!r} not in the first pages", n)

    # sections point at listed files
    for s in manifest["sections"]:
        if s.get("file") is not None and s["file"] not in set(names):
            err("section_file", f"section {s.get('title')!r} points at unlisted file", s["file"])

    # image links (counted always; resolved only when a figures root is given)
    links = broken = 0
    for n, t in texts.items():
        for target in IMAGE_LINK_RE.findall(t):
            target = target[0] or target[1]
            if re.match(r"^[a-z]+:", target, re.I):
                continue  # URL
            links += 1
            if figures_root is not None:
                rel_dir = n.rsplit("/", 1)[0] if "/" in n else ""
                tgt = target.split("|")[0].split("#")[0]
                found = (book_dir / rel_dir / tgt).exists() or \
                    (Path(figures_root) / book_dir.name / rel_dir / tgt).exists()
                if not found:
                    broken += 1
                    if broken <= 20:
                        err("image_link", f"image not found: {tgt[:80]}", n)
    if broken > 20:
        err("image_link", f"… {broken - 20} more unresolved image links")

    res["stats"] = {
        "image_links": links, "image_links_unresolved": broken if figures_root is not None else None,
        "files": len(files), "md_on_disk": len(on_disk),
        "numbered_files": sum(1 for f in files if f.get("chapter_number") is not None),
        "distinct_chapters": len(numbers), "toc_chapters": toc,
        "titles_tested": tested, "titles_matched": matched,
        "sections": len(manifest["sections"]),
        "sections_unplaced": sum(1 for s in manifest["sections"] if s.get("pdf_page") is None),
        "split_method": manifest["split_method"],
        "page_confidence": manifest["page_confidence"],
    }
    res["ok"] = not errors
    return res


GUIDELINES_DIR = "_Guidelines"


def is_tooling_dir(name: str) -> bool:
    """A top-level corpus folder that is not a book: `.`-folders, and `_`-folders other than
    `_Guidelines` (`_figure_remap`, `__pycache__`, …) — the convention ask-your-library's
    indexer uses too."""
    return name.startswith(".") or (name.startswith("_") and name != GUIDELINES_DIR)


def book_dirs(corpus: Path, only=None) -> list[Path]:
    out = []
    for d in sorted(corpus.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or is_tooling_dir(d.name):
            continue
        if only and d.name not in only:
            continue
        out.append(d)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate _manifest.json for each book of a corpus")
    ap.add_argument("--validate", metavar="CORPUS", required=True)
    ap.add_argument("--book", action="append", help="only this book folder (repeatable)")
    ap.add_argument("--json", metavar="FILE", help="write the full report as JSON")
    ap.add_argument("--quiet", action="store_true", help="print failing books only")
    ap.add_argument("--figures-root", metavar="DIR",
                    help="also require every image link to resolve, in the book or under DIR/<book>/")
    a = ap.parse_args(argv)
    corpus = Path(a.validate)
    if not corpus.is_dir():
        print(f"not a directory: {corpus}", file=sys.stderr)
        return 2
    results = []
    for d in book_dirs(corpus, a.book):
        if not (d / MANIFEST_NAME).exists() and not listed_md_files(d):
            continue  # not a book (tooling folder)
        r = validate_book(d, figures_root=a.figures_root)
        results.append(r)
        if not a.quiet or not r["ok"]:
            checks = sorted({e["check"] for e in r["errors"]})
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {d.name}"
                  + (f"  [{', '.join(checks)}] ({len(r['errors'])})" if checks else ""))
    n_ok = sum(r["ok"] for r in results)
    print(f"\n{n_ok}/{len(results)} books pass")
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
