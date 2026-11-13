"""resplit — re-split an existing converted corpus into a NEW directory, with a manifest per book.

openspec changes `resplit-cli`, `split-config`, `split-manifest-contract`. Entry point:

  python converter/convert.py --resplit <corpus> --out <new dir> --pdf-root <dir> [--pdf-root <dir> ...]
                              [--book NAME ...] [--split-config FILE] [--pdf-map FILE] [--force]

For every book folder of <corpus>:
  * a unit with `full_text.md` (the book folder itself, or each sub-folder for a book converted
    one chapter-PDF at a time) is split again from `full_text.md` + the source PDF's outline,
    with the same splitter conversion uses (same `T2N_SPLIT_*` env semantics);
  * a book with no `full_text.md` is carried over file by file (textless files dropped);
  * every book gets `_manifest.json` (see split_manifest.py), then is validated.
The source corpus and the PDFs are only read; each book is built in `<out>/.tmp-<book>` and
moved into place when complete, so `<out>/<book>/_manifest.json` existing = book done (resume).
"""
from __future__ import annotations

import contextlib
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import split_manifest as sm  # noqa: E402

CONFIG_NAME = "_split_config.json"
REPORT_NAME = "_resplit_report.json"
ROOT_FILES_NOT_COPIED = {"_chapter_index.json", "batch_progress.json", "conversion_summary.json",
                         "INDEX.md", "already_converted.json", REPORT_NAME}
SPLIT_NAME_RES = (
    re.compile(r"^ch\d{2,}[a-z]?(_[A-Za-z0-9_]*)?\.md$"),
    re.compile(r"^pages_\d{4}-\d{4}\.md$"),
)
SOURCE_RE = re.compile(r"^Source: `([^`]+)`", re.M)
UNIT_NUMBER_RE = re.compile(r"^(\d{1,3})[_ .\-]")
GENERATOR = "textbook-to-note resplit"


def _cv():
    import convert as cv  # heavy import (fitz, pdfplumber); deferred
    return cv


def is_split_name(name: str) -> bool:
    return any(r.match(name) for r in SPLIT_NAME_RES)


def norm_stem(s: str) -> str:
    s = re.sub(r"[_ ]\d{4}$", "", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(paths) -> dict:
    out = {}
    for p in paths:
        st = os.stat(p)
        out[str(p)] = (st.st_size, st.st_mtime_ns, sha256_file(p))
    return out


# --- PDF lookup ------------------------------------------------------------------------------

class PdfIndex:
    def __init__(self, roots):
        self.roots = [Path(r) for r in roots]
        self.by_name: dict[str, list[Path]] = {}
        self.by_stem: dict[str, list[Path]] = {}
        for r in self.roots:
            for dp, dn, fn in os.walk(r):
                for f in fn:
                    if f.lower().endswith(".pdf"):
                        p = Path(dp) / f
                        self.by_name.setdefault(f.lower(), []).append(p)
                        self.by_stem.setdefault(norm_stem(Path(f).stem), []).append(p)

    def resolve_configured(self, value: str) -> Path | None:
        p = Path(value).expanduser()
        if p.is_absolute():
            return p if p.exists() else None
        for r in self.roots:
            if (r / value).exists():
                return r / value
        return None

    def find(self, key: str, unit_dir: Path, head: str, configured: str | None):
        """(pdf path | None, how-or-reason)."""
        if configured:
            p = self.resolve_configured(configured)
            return (p, "config") if p else (None, f"configured pdf not found: {configured}")
        m = SOURCE_RE.search(head)
        if m:
            src = m.group(1).replace("\\", "/")
            sp = Path(src)
            if sp.exists() and any(_is_under(sp, r) for r in self.roots):
                return sp, "source_path"
            base = src.rsplit("/", 1)[-1]
            c = self.by_name.get(base.lower(), [])
            if len(c) == 1:
                return c[0], "source_name"
            if len(c) > 1:
                return None, "ambiguous pdf"
            c = self.by_stem.get(norm_stem(Path(base).stem), [])
            if len(c) == 1:
                return c[0], "source_stem"
            if len(c) > 1:
                return None, "ambiguous pdf"
        c = self.by_stem.get(norm_stem(unit_dir.name), [])
        if len(c) == 1:
            return c[0], "dir_stem"
        if len(c) > 1:
            return None, "ambiguous pdf"
        return None, "no pdf"


def _is_under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


# --- outline transforms (per-book config) -----------------------------------------------------

def _line_pages(lines):
    pages = []
    cur = 0
    for line in lines:
        m = sm.PAGE_MARKER_RE.match(line)
        if m:
            cur = int(m.group(1))
        pages.append(cur)
    return pages


def _similar_prefix(line_key: str, title_key: str) -> bool:
    """A running header / title line that is the title, possibly cut short or with a typo."""
    import difflib
    ref = title_key[:len(line_key)] if len(line_key) < len(title_key) else title_key
    return difflib.SequenceMatcher(None, line_key, ref).ratio() >= 0.85


def anchor_titles(content: str, toc, split_levels: int, body_start_page: int | None = None):
    """Re-locate outline entries by finding their titles in the text (for books whose outline
    destinations are broken). Returns (new toc with page or None, stats)."""
    lines = content.split("\n")
    pages = _line_pages(lines)
    keys: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        if sm.PAGE_MARKER_RE.match(line) or not line.strip():
            continue
        k = sm._heading_key(line)
        if k:
            keys.setdefault(k, []).append(i)
        joined = line
        for j in (i + 1, i + 2):  # a title wrapped over up to three lines
            if j >= len(lines) or not lines[j].strip() or sm.PAGE_MARKER_RE.match(lines[j]):
                break
            joined += " " + lines[j]
            k2 = sm._heading_key(joined)
            if k2 and k2 != k:
                keys.setdefault(k2, []).append(i)
    # lines that start with a chapter number ("23—CARE OF THE PATIENT…", "Chapter 5: …")
    numbered: dict[int, list[tuple[int, str]]] = {}
    for i, line in enumerate(lines):
        if len(line) > 200 or sm.PAGE_MARKER_RE.match(line):
            continue
        n = sm.parse_chapter_number(line)
        if n is not None:
            numbered.setdefault(n, []).append((i, sm.norm_text(sm.strip_numbering(line))))
    # contents pages: ≥5 distinct split-level titles matched on one page
    def title_key(title):
        # only the title proper: "Chapter 5" alone has nothing to look for
        return sm.norm_text(sm.strip_numbering(title))

    per_page: dict[int, set] = {}
    for lvl, title, _ in toc:
        if lvl > split_levels:
            continue
        k = title_key(title)
        for i in keys.get(k, ()) if k else ():
            per_page.setdefault(pages[i], set()).add(k)
    toc_pages = {p for p, s in per_page.items() if len(s) >= 5}
    floor_page = body_start_page or 0

    def ok(i):
        return pages[i] not in toc_pages and pages[i] >= floor_page and pages[i] > 0

    stats = {"split_total": 0, "split_anchored": 0, "deep_total": 0, "deep_anchored": 0,
             "contents_pages": sorted(toc_pages)}
    # candidates per split-level entry; a line that also carries the entry's chapter number
    # ("5—SEDATION AND ANALGESIA…") is a strong candidate and, when present, the only kind used
    out = [[lvl, title, None] for lvl, title, _ in toc]
    split_idx = [n for n, e in enumerate(out) if e[0] <= split_levels]
    stats["split_total"] = len(split_idx)
    cands, weak_of = [], []
    for n in split_idx:
        k = title_key(out[n][1])
        allc = [i for i in keys.get(k, ()) if ok(i)] if k else []
        num = sm.parse_chapter_number(out[n][1])
        strong = [i for i in allc if num is not None and sm.parse_chapter_number(lines[i]) == num]
        if num is not None and k:
            # same chapter number + the line is (a possibly truncated / misspelt) title
            for i, lk in numbered.get(num, ()):
                if ok(i) and len(lk) >= 15 and i not in strong and _similar_prefix(lk, k):
                    strong.append(i)
            strong.sort()
        cands.append(strong or allc)
        weak_of.append(allc)
    # longest strictly increasing choice of one line per entry (entries may stay unplaced)
    import bisect
    tails, tail_node, nodes = [], [], []
    for pos_e, cs in enumerate(cands):
        for line in sorted(set(cs), reverse=True):
            p = bisect.bisect_left(tails, line)
            nodes.append((pos_e, line, tail_node[p - 1] if p else None))
            if p == len(tails):
                tails.append(line)
                tail_node.append(len(nodes) - 1)
            else:
                tails[p] = line
                tail_node[p] = len(nodes) - 1
    chosen = {}
    node = tail_node[-1] if tail_node else None
    while node is not None:
        pos_e, line, node = nodes[node]
        chosen[pos_e] = line
    # second pass: an entry left out (e.g. its only number-carrying lines sit in front matter)
    # takes its earliest plain-title match strictly between its placed neighbours
    for pos_e in range(len(split_idx)):
        if pos_e in chosen:
            continue
        lo = max((chosen[q] for q in chosen if q < pos_e), default=-1)
        hi = min((chosen[q] for q in chosen if q > pos_e), default=len(lines))
        between = [i for i in weak_of[pos_e] + cands[pos_e] if lo < i < hi]
        if between:
            chosen[pos_e] = min(between)
    # pull each anchor back to the earliest matching line after the previous anchor that is
    # at most 2 pages before it (the chapter's opening page carries the bare title)
    prev = -1
    split_lines = []
    for pos_e, n in enumerate(split_idx):
        if pos_e not in chosen:
            continue
        line = chosen[pos_e]
        early = [i for i in weak_of[pos_e] if prev < i < line and pages[i] >= pages[line] - 2]
        if early:
            line = min(early)
        prev = line
        out[n][2] = pages[line]
        split_lines.append((n, line))
        stats["split_anchored"] += 1
    # deep entries: search forward inside their chapter's line span
    bounds = [h for _, h in split_lines] + [len(lines)]
    span_of = {}
    for n, (idx, h) in enumerate(split_lines):
        span_of[idx] = (h, bounds[n + 1])
    cur_span = None
    pos = -1
    for idx, e in enumerate(out):
        if idx in span_of:
            cur_span = span_of[idx]
            pos = cur_span[0]
            continue
        if e[0] <= split_levels:
            if title_key(e[1]):  # a real chapter we could not place: its sections are lost too
                cur_span = None
            continue
        stats["deep_total"] += 1
        if cur_span is None:
            continue
        k = sm._heading_key(e[1])
        hit = next((i for i in keys.get(k, ()) if pos < i < cur_span[1]), None) if k else None
        if hit is not None:
            e[2] = pages[hit]
            pos = hit
            stats["deep_anchored"] += 1
    return out, stats


def transform_toc(toc, cfg: dict, content: str):
    """Apply the book's split config to the PDF outline. Returns (toc with orig levels, notes)."""
    notes = {}
    split_levels = int(cfg.get("split_levels", 2))
    retitle = cfg.get("retitle", {})  # publisher typos in outline titles: {"old": "new"}
    entries = [[e[0], retitle.get(e[1].strip(), e[1]).split(chr(0))[0].strip(), e[2]] for e in toc]
    if retitle:
        notes["retitled"] = sum(1 for e in toc if e[1].strip() in retitle)
    # drop_titles: "regex" (any entry) or {"re": regex, "max_level": n, "after": regex}
    # ("after": only entries following the first entry whose title matches it)
    rules = []
    for d in cfg.get("drop_titles", []):
        d = {"re": d} if isinstance(d, str) else d
        rules.append((re.compile(d["re"], re.I), d.get("max_level"),
                      re.compile(d["after"], re.I) if d.get("after") else None))
    if rules:
        before = len(entries)
        seen_after = [False] * len(rules)
        kept = []
        for e in entries:
            drop = False
            for n, (rx, max_level, after) in enumerate(rules):
                if after is not None and not seen_after[n]:
                    if after.search(e[1]):
                        seen_after[n] = True
                    continue
                if rx.search(e[1]) and (max_level is None or e[0] <= max_level):
                    drop = True
            if not drop:
                kept.append(e)
        entries = kept
        notes["dropped_by_config"] = before - len(entries)
    off = int(cfg.get("page_offset", 0))
    if off:
        for e in entries:
            e[2] = e[2] + off
    if cfg.get("anchor_titles"):
        entries, st = anchor_titles(content, entries, split_levels, cfg.get("body_start_page"))
        notes["anchoring"] = st
    # an entry at a split level whose page goes backwards cannot bound a chapter: drop it
    last = 0
    dropped = 0
    kept = []
    for e in entries:
        if e[0] <= split_levels and e[2] is not None:
            if e[2] < last:
                dropped += 1
                e = [e[0], e[1], None]
            else:
                last = e[2]
        kept.append(e)
    if dropped:
        notes["backward_dropped"] = dropped
    return kept, notes


# --- records ---------------------------------------------------------------------------------

def _clean_title(stem: str) -> str:
    return " ".join(html.unescape(stem).replace("_", " ").split())


def _file_title_from_name(name: str, per_chapter: bool) -> str:
    stem = name[:-3] if name.endswith(".md") else name
    if not per_chapter:
        stem = re.sub(r"^ch\d{2,}[a-z]?_?", "", stem)
        # publisher file names "FrontMatter_2019_StrokeRehabilitation": drop "_<year>_<BookName>"
        stem = re.sub(r"_(?:19|20)\d{2}_[A-Z][A-Za-z]+$", "", stem) or stem
    return _clean_title(stem) or _clean_title(name)


def read_pdf(pdf: Path, ctx: dict) -> dict:
    """read_pdf_meta + remember the PDF's fingerprint taken BEFORE reading it."""
    if pdf not in ctx["pdfs_read"]:
        ctx["pdf_fp"].update(fingerprint([pdf]))
        ctx["pdfs_read"].add(pdf)
    return sm.read_pdf_meta(pdf)


def _record(book_out: Path, rel: str, seq, title, chapter, kind, level=None, source_pdf=None,
            confidence=None):
    text = (book_out / rel).read_text(encoding="utf-8", errors="replace")
    lo, hi = sm.page_range(text)
    r = {"file": rel, "seq": seq, "chapter_number": chapter, "title": title,
         "pdf_page_start": lo, "pdf_page_end": hi, "kind": kind, "level": level,
         "source_pdf": source_pdf}
    if confidence:
        r["page_confidence"] = confidence
    return r


PART_TITLE_RE = re.compile(r"^\s*(?:part|section|unit|volume|book)\b", re.I)


def mark_front_before_first_chapter(recs):
    """In a book with numbered chapters, an unnumbered file before chapter 1's file (cover,
    book-title page, series list, 'Inside Front Cover'…) is front matter. Part / Section title
    pages stay body."""
    nums = [i for i, r in enumerate(sorted(recs, key=lambda r: r["seq"])) if r["chapter"] is not None]
    if not nums:
        return
    first_seq = sorted(recs, key=lambda r: r["seq"])[nums[0]]["seq"]
    for r in recs:
        if r["seq"] < first_seq and r["kind"] == "body" and not PART_TITLE_RE.match(r["title"] or ""):
            r["kind"] = "front"


def numbers_if_increasing(values):
    nums = [v for v in values if v is not None]
    if len(nums) >= 2 and not sm.strictly_increasing(nums):
        return [None] * len(values), "numbers not increasing"
    return values, None


# --- one unit (a folder holding full_text.md) ---------------------------------------------------

def _page_chunks(text: str):
    """[(page | None, lines)]: the lines before the first marker (page None, only when there
    are any), then each marker line with the lines up to the next marker. Joining every chunk's
    lines with newlines gives `text` back exactly."""
    chunks = []
    page, cur = None, []
    for line in text.split("\n"):
        m = sm.PAGE_MARKER_RE.match(line)
        if m:
            if cur or page is not None:
                chunks.append((page, cur))
            page, cur = int(m.group(1)), [line]
        else:
            cur.append(line)
    if cur or page is not None:
        chunks.append((page, cur))
    return chunks


def reconstruct_full_text(src: Path, all_files) -> str:
    """Rebuild a book's full text from its old split files (config method "reconstruct"):
    files with text, in chNN order, joined by newline — exactly how the splitter cut them.
    A page the old splitter wrote into two files (out-of-order bookmarks) is kept once when both
    copies are identical. Refuses when page markers still do not strictly increase."""
    parts = [p for p in all_files if p.parent == src and is_split_name(p.name)
             and p.name.startswith("ch")]
    parts.sort(key=lambda p: int(re.match(r"ch(\d+)", p.name).group(1)))
    texts = [p.read_text(encoding="utf-8") for p in parts]
    texts = [t for t in texts if sm.has_text(t)]
    seen: dict[int, str] = {}  # page → its block (marker line excluded), as first written
    last = 0
    kept = []
    for t in texts:
        out = []
        for page, chunk in _page_chunks(t):
            if page is not None:
                body = "\n".join(chunk[1:]).strip()
                if page <= last and page in seen and seen[page] == body:
                    continue  # identical duplicate of a page already kept
                seen.setdefault(page, body)
                last = max(last, page)
            out.extend(chunk)
        kept.append("\n".join(out))
    content = "\n".join(kept)
    pages = sm.page_markers(content)
    bad = [b for a, b in zip(pages, pages[1:]) if b <= a]
    if not pages or bad:
        raise RuntimeError(f"reconstruct refused: page markers not strictly increasing ({bad[:5]})")
    return content


def resplit_unit(src_unit: Path, out_unit: Path, rel_prefix: str, pdf: Path | None, cfg: dict,
                 book_out: Path, ctx: dict, content: str | None = None):
    """Write the new split of one unit; return (records, sections, info)."""
    cv = _cv()
    if content is None:
        content = (src_unit / "full_text.md").read_text(encoding="utf-8")
    info = {"unit": rel_prefix.rstrip("/") or ".", "pdf": pdf.name if pdf else None}
    total_pages = max(sm.page_markers(content) or [0])
    meta = None
    toc = []
    if pdf is not None:
        meta = read_pdf(pdf, ctx)
        if meta["writeback"]:
            info["outline_ignored"] = "written by converter <= 0.7.3"
            ctx["writeback"].add(str(pdf))
        elif len(meta["toc"]) >= 3:
            toc = meta["toc"]
    if cfg.get("toc"):
        # the book's outline, given by the config: replaces the PDF's (missing, written by an
        # old converter, or pointing at the wrong pages). [level, title, pdf page]; with
        # anchor_titles the page may be 1 and the title is located in the text.
        toc = [[int(e[0]), str(e[1]), int(e[2]) if len(e) > 2 and e[2] is not None else 1]
               for e in cfg["toc"]]
        info["outline_from_config"] = len(toc)
    split_levels = int(cfg.get("split_levels", 2))
    if toc and "split_levels" not in cfg and cfg.get("chapter_numbers", True) is not False:
        # chapters numbered deeper than level 2 (Part › Section › Chapter): split at the
        # chapter level, otherwise no file would carry a chapter number
        retitle = cfg.get("retitle", {})
        lv = cfg.get("chapter_level") or sm.choose_chapter_level(
            [(e[0], retitle.get(e[1].strip(), e[1])) for e in toc], cfg.get("chapter_regex"))[0]
        if lv and lv > split_levels:
            split_levels = lv
            cfg = dict(cfg, split_levels=lv)
            info["split_levels"] = lv
    method = cfg.get("method")
    records, sections = [], []
    buf = io.StringIO()
    if toc and method != "pattern":
        entries, notes = transform_toc(toc, cfg, content)
        info.update(notes)
        orig_levels = [e[0] for e in entries]
        if cfg.get("promote_chapters", True) is not False and \
                cfg.get("chapter_numbers", True) is not False and \
                not cfg.get("chapter_number_overrides"):  # hand-numbered books keep their split
            # a `Chapter N` entry at another level is still a chapter (Magee 26-30 at L4,
            # Tecklin/Nordin chapter 1 at L1): move it and its subtree to the chapter level
            pre_level = cfg.get("chapter_level") or sm.choose_chapter_level(
                [(e[0], e[1]) for e in entries], cfg.get("chapter_regex"))[0]
            if pre_level is not None and pre_level <= split_levels:
                new_levels, promoted = sm.promote_keyword_chapters(entries, pre_level,
                                                                   cfg.get("chapter_regex"))
                if promoted:
                    info["promoted_chapters"] = promoted
                    entries = [[lv, e[1], e[2]] for lv, e in zip(new_levels, entries)]
        shift = split_levels - 2
        placed = [[e[0] - shift, e[1], e[2]] for e in entries if e[2] is not None]
        with contextlib.redirect_stdout(buf):
            chapters = cv.split_by_bookmarks(content, out_unit, placed)
        if chapters is not None:
            info["split"] = "bookmarks"
            # chNN ↔ the NN-th entry of the splitter's own filter (shifted level ≤ 2, page has a marker)
            page_set = set(sm.page_markers(content))
            idx_map = [i for i, e in enumerate(entries)
                       if e[2] is not None and e[0] - shift <= 2 and e[2] in page_set]
            level_entries = [(e[0], e[1]) for e in entries]
            ch_level = cfg.get("chapter_level")
            reason = "config"
            if cfg.get("chapter_numbers", True) is False:
                ch_level, reason = None, "disabled by config"
            elif ch_level is None:
                ch_level, reason = sm.choose_chapter_level(level_entries, cfg.get("chapter_regex"))
            state = sm.chapter_state_walk(level_entries, ch_level, cfg.get("chapter_regex"))
            info["chapter_level"] = ch_level
            if ch_level is None and reason != "no numbered titles":
                info["chapter_numbers_note"] = reason
            info["toc_chapters"] = len({sm.parse_chapter_number(t, cfg.get("chapter_regex"))
                                        for l, t in level_entries if l == ch_level} - {None}) \
                if ch_level is not None else None
            for name in sorted(os.listdir(out_unit)):
                if not is_split_name(name):
                    continue
                rel = rel_prefix + name
                n = int(re.match(r"ch(\d+)", name).group(1))
                if n == 0:
                    records.append(dict(rel=rel, seq=0, title="Front Matter", chapter=None,
                                        kind="front", level=None))
                    continue
                ei = idx_map[n - 1]
                lvl, title, _ = entries[ei]
                records.append(dict(rel=rel, seq=n, title=title.strip(), chapter=state[ei],
                                    kind=sm.classify_kind(title), level=lvl))
            sections = [dict(level=ol, title=e[1].strip(), pdf_page=e[2], kind=sm.classify_kind(e[1]))
                        for ol, e in zip(orig_levels, entries)]
    if not records:
        with contextlib.redirect_stdout(buf):
            chapters = cv.split_into_chapters(content, out_unit, total_pages, pdf_path=None)
        names = sorted(n for n in os.listdir(out_unit) if is_split_name(n))
        info["split"] = "pattern" if any(n.startswith("ch") for n in names) else (
            "pages" if names else "none")
        titles = []
        for name in names:
            rel = rel_prefix + name
            if name.startswith("pages_"):
                a, b = re.match(r"pages_(\d+)-(\d+)", name).groups()
                records.append(dict(rel=rel, seq=int(a), title=f"Pages {int(a)}-{int(b)}",
                                    chapter=None, kind="body", level=None))
                continue
            n = int(re.match(r"ch(\d+)", name).group(1))
            if n == 0:
                records.append(dict(rel=rel, seq=0, title="Front Matter", chapter=None,
                                    kind="front", level=None))
                continue
            title = chapters[n - 1][2] if chapters and n - 1 < len(chapters) else name
            records.append(dict(rel=rel, seq=n, title=title, chapter=None,
                                kind=sm.classify_kind(title), level=1))
            titles.append(len(records) - 1)
        if cfg.get("chapter_numbers", True) is not False:
            vals = [sm.parse_chapter_number(records[i]["title"], cfg.get("chapter_regex")) for i in titles]
            vals, note = numbers_if_increasing(vals)
            if note:
                info["chapter_numbers_note"] = note
            for i, v in zip(titles, vals):
                records[i]["chapter"] = v
        info["toc_chapters"] = cfg.get("toc_chapters")
    m = re.search(r"deep headings: (\d+)/(\d+)", buf.getvalue())
    if m:
        info["deep_headings"] = [int(m.group(1)), int(m.group(2))]
    info["page_labels"] = meta["page_labels"] if meta else None
    return records, sections, info


# --- one book --------------------------------------------------------------------------------

def _copy_file(src: Path, dst: Path, ctx):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    ctx["read"].add(src)


def _head(p: Path, n=3000) -> str:
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read(n)


def resplit_book(src: Path, out_root: Path, idx: PdfIndex, config: dict, pdf_map: dict,
                 force: bool) -> dict:
    book = src.name
    cfg = config.get(book, {}) or {}
    final = out_root / book
    tmp = out_root / f".tmp-{book}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    ctx = {"read": set(), "pdfs_read": set(), "writeback": set(), "pdf_fp": {}}
    excl = set(cfg.get("exclude_files", []))
    res = {"book": book, "status": "ok", "units": [], "dropped_empty": 0, "notes": []}

    # walk the book: units (folders with full_text.md) and everything else
    unit_dirs = []
    all_files = []
    for dp, dn, fn in os.walk(src):
        dn[:] = sorted(d for d in dn if not sm.SKIP_DIR_RE.search(d))
        if "full_text.md" in fn:
            unit_dirs.append(Path(dp))
        for f in fn:
            all_files.append(Path(dp) / f)
    recon = None
    if cfg.get("method") == "reconstruct" and not unit_dirs:
        recon = reconstruct_full_text(src, [p for p in all_files
                                            if p.relative_to(src).as_posix() not in excl])
        unit_dirs = [src]
        res["notes"].append("full_text reconstructed in memory from the existing chapter files")
    unit_set = set(unit_dirs)
    ctx["read"].update(p for p in all_files)
    before = fingerprint(sorted(ctx["read"]))

    # 1. copy everything that is not regenerated (split files of a unit are)
    carried_md = []
    for p in all_files:
        rel = p.relative_to(src).as_posix()
        if rel in excl:
            res["notes"].append(f"excluded by config: {rel}")
            continue
        if p.parent in unit_set and is_split_name(p.name):
            continue
        if p.suffix == ".md" and p.name not in sm.NON_CHAPTER_MD:
            if not sm.has_text(p.read_text(encoding="utf-8", errors="replace")):
                res["dropped_empty"] += 1
                continue
            carried_md.append(rel)
        _copy_file(p, tmp / rel, ctx)

    records, sections = [], []
    label_sets: dict[str, dict | None] = {}
    single_pdf = None
    unit_methods = []

    # 2. units
    for u in sorted(unit_dirs, key=lambda p: sm._natural_key(p.relative_to(src).as_posix())):
        rel_dir = u.relative_to(src).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        ucfg = dict(cfg)
        ucfg.update((cfg.get("units") or {}).get(rel_dir, {}))
        key = book if not prefix else f"{book}/{rel_dir}"
        configured = pdf_map.get(key) or ucfg.get("pdf")
        if ucfg.get("method") == "carry":
            pdf, how = None, "carry by config"
        else:
            head = recon[:3000] if recon is not None else _head(u / "full_text.md")
            pdf, how = idx.find(key, u, head, configured)
        out_unit = tmp / rel_dir if prefix else tmp
        out_unit.mkdir(parents=True, exist_ok=True)
        if pdf is None and ucfg.get("method") != "pattern":
            # no source PDF: keep the existing split as it is (textless files dropped)
            res["units"].append({"unit": rel_dir, "split": "carried", "pdf": None, "reason": how})
            unit_methods.append("carried")
            for p in sorted(u.iterdir(), key=lambda p: sm._natural_key(p.name)):
                if p.is_file() and is_split_name(p.name):
                    rel = prefix + p.name
                    if rel in excl:
                        continue
                    if not sm.has_text(p.read_text(encoding="utf-8", errors="replace")):
                        res["dropped_empty"] += 1
                        continue
                    _copy_file(p, tmp / rel, ctx)
                    carried_md.append(rel)
            continue
        recs, secs, info = resplit_unit(u, out_unit, prefix, pdf, ucfg, tmp, ctx, content=recon)
        if not prefix:
            mark_front_before_first_chapter(recs)
        info["pdf_found_by"] = how
        res["units"].append(info)
        unit_methods.append("config" if any(k in ucfg for k in (
            "anchor_titles", "page_offset", "split_levels", "drop_titles", "chapter_level",
            "chapter_regex", "retitle", "toc")) and info.get("split") == "bookmarks" else info.get("split"))
        src_name = pdf.name if pdf else None
        unit_labels = info.pop("page_labels", None)
        if src_name:
            label_sets[src_name] = unit_labels
            single_pdf = src_name if single_pdf is None else single_pdf
        conf = "printed" if unit_labels else "pdf_only"
        unit_no = None
        if prefix:
            m = UNIT_NUMBER_RE.match(rel_dir.split("/")[-1])
            unit_no = int(m.group(1)) if m and int(m.group(1)) > 0 else None
        for r in recs:
            kind = r["kind"]
            chapter = r["chapter"]
            if prefix:
                chapter = unit_no
                if r["seq"] == 0:
                    kind = "body"
            rec = _record(tmp, r["rel"], r["seq"], r["title"], chapter, kind, r["level"], src_name, conf)
            records.append(rec)
        # sections → file holding their page (same unit)
        unit_recs = [r for r in records if r["file"].startswith(prefix) and
                     ("/" not in r["file"][len(prefix):]) and r["pdf_page_start"] is not None]
        for s in secs:
            f = None
            if s["pdf_page"] is not None:
                for r in unit_recs:
                    if r["pdf_page_start"] <= s["pdf_page"] <= r["pdf_page_end"]:
                        f = r["file"]
                        break
            s["file"] = f
            sections.append(s)
        for k in ("toc_chapters", "chapter_level", "chapter_numbers_note"):
            if info.get(k) is not None:
                res.setdefault("_unit_" + k, []).append(info[k])

    # 3. carried md files (no full_text): per-chapter PDFs or leftovers
    per_chapter = False
    if carried_md:
        src_heads = {rel: _head(src / rel, 1500) for rel in carried_md}
        with_source = [rel for rel, h in src_heads.items() if SOURCE_RE.search(h)]
        per_chapter = not unit_dirs and len(with_source) >= max(1, len(carried_md) // 2)
        ordered = sorted(carried_md, key=lambda r: (_carried_number(r, per_chapter) is None,
                                                    _carried_number(r, per_chapter) or 0,
                                                    sm._natural_key(r)))
        if not per_chapter:
            ordered = sorted(carried_md, key=sm._natural_key)
        nums = [_carried_number(r, per_chapter) for r in ordered]
        if cfg.get("chapter_numbers", True) is False:
            nums = [None] * len(nums)
        titles = [_file_title_from_name(r.rsplit("/", 1)[-1], per_chapter) for r in ordered]
        nums = [None if sm.classify_kind(t) != "body" else n for n, t in zip(nums, titles)]
        nums, note = numbers_if_increasing(nums)
        if note:
            res.setdefault("_unit_chapter_numbers_note", []).append(note + " (file names)")
        base_seq = len(records)
        for i, (rel, n, t) in enumerate(zip(ordered, nums, titles), 1):
            src_name, conf = None, None
            if per_chapter and rel in with_source:
                pdf, how = idx.find(rel, src / rel, src_heads[rel], pdf_map.get(f"{book}/{rel}"))
                if pdf is not None:
                    meta = read_pdf(pdf, ctx)
                    src_name = pdf.name
                    label_sets[src_name] = meta["page_labels"]
                    conf = "printed" if meta["page_labels"] else "pdf_only"
                    if meta["writeback"]:
                        ctx["writeback"].add(str(pdf))
                    rec = _record(tmp, rel, base_seq + i, t, n, sm.classify_kind(t), None, src_name, conf)
                    rec["page_unit"] = rel  # its own PDF: its own page sequence
                    records.append(rec)
                    for lvl, title, page in meta["toc"]:
                        sections.append(dict(level=lvl, title=title.strip(), pdf_page=page, file=rel,
                                             kind=sm.classify_kind(title)))
                    continue
                res["notes"].append(f"per-chapter pdf not found ({how}): {rel}")
            # the file's own document: its Source: header names it even when the PDF is gone
            hdr = SOURCE_RE.search(src_heads.get(rel, "")) if per_chapter else None
            own = hdr.group(1).replace("\\", "/").rsplit("/", 1)[-1] if hdr else None
            if own and own not in label_sets:
                label_sets[own] = None
            rec = _record(tmp, rel, base_seq + i, t, n, sm.classify_kind(t), None, own, "pdf_only")
            if per_chapter:
                rec["page_unit"] = rel  # its own PDF: its own page sequence
            records.append(rec)
        unit_methods.append("per_chapter_pdf" if per_chapter else "carried")

    # 4. manifest
    if not unit_dirs:
        split_method = "per_chapter_pdf" if per_chapter else "carried"
    elif len(unit_dirs) > 1 or (unit_dirs and unit_dirs[0] != src):
        split_method = "per_chapter_pdf"
    else:
        m0 = unit_methods[0] if unit_methods else "carried"
        split_method = {"bookmarks": "bookmarks", "config": "config", "pattern": "pattern",
                        "pages": "pattern", "none": "pattern", "carried": "carried"}.get(m0, "carried")
    multi = split_method == "per_chapter_pdf" or len(label_sets) > 1
    if multi or single_pdf is None:
        top_pdf, top_labels = None, None
        sets = list(label_sets.values())
        conf = "printed" if sets and all(sets) else "pdf_only"
    else:
        top_pdf, top_labels = single_pdf, label_sets.get(single_pdf)
        conf = "printed" if top_labels else "pdf_only"
    for r in records:
        r.setdefault("page_confidence", None)
        if r["page_confidence"] is None:
            r["page_confidence"] = "pdf_only"
    overrides = cfg.get("chapter_number_overrides", {})  # {"book-relative md": n | null}
    for r in records:
        if r["file"] in overrides:
            r["chapter_number"] = overrides[r["file"]]
    if overrides:
        # front-by-position (mark_front_before_first_chapter) was decided before the overrides:
        # a file now at or after the first numbered chapter whose title is not front matter is body
        numbered = [r["seq"] for r in records if r["chapter_number"] is not None and "/" not in r["file"]]
        if numbered:
            for r in records:
                if r["kind"] == "front" and "/" not in r["file"] and r["seq"] >= min(numbered) and \
                        sm.classify_kind(r["title"] or "") == "body":
                    r["kind"] = "body"
    toc_vals = res.pop("_unit_toc_chapters", [])
    manifest = {
        "schema": sm.SCHEMA_VERSION, "book": book, "source_pdf": top_pdf,
        "split_method": split_method, "page_labels": top_labels, "page_confidence": conf,
        "files": records, "sections": sections,
        "page_label_sets": label_sets if multi else None,
        "toc_chapters": cfg.get("toc_chapters", toc_vals[0] if len(toc_vals) == 1 and
                                split_method in ("bookmarks", "config") else None),
        "chapter_level": (res.pop("_unit_chapter_level", [None]) or [None])[0]
        if split_method in ("bookmarks", "config") else None,
        "generator": GENERATOR,
    }
    res.pop("_unit_chapter_level", None)
    note = res.pop("_unit_chapter_numbers_note", None)
    if note:
        manifest["chapter_numbers_note"] = "; ".join(sorted(set(note)))
    if cfg.get("titles_are_labels"):
        manifest["titles_are_labels"] = True
    if cfg.get("missing_chapters_ok"):
        manifest["missing_chapters_ok"] = cfg["missing_chapters_ok"]
    if cfg.get("title_check_skip"):
        manifest["title_check_skip"] = cfg["title_check_skip"]
    notes = list(res["notes"])
    if res["dropped_empty"]:
        notes.append(f"{res['dropped_empty']} textless md file(s) not carried over")
    if cfg.get("reason"):
        notes.append("split config: " + cfg["reason"])
    if notes:
        manifest["notes"] = notes
    (tmp / sm.MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    # 5. the source must be byte-for-byte untouched
    if fingerprint(sorted(ctx["read"])) != before:
        raise RuntimeError("source corpus changed during resplit")
    if fingerprint(sorted(ctx["pdfs_read"])) != ctx["pdf_fp"]:
        raise RuntimeError("source PDF changed during resplit")
    res["pdfs"] = sorted(str(p) for p in ctx["pdfs_read"])
    res["writeback_pdfs"] = sorted(ctx["writeback"])
    res["split_method"] = split_method

    # 6. swap into place
    if final.exists():
        shutil.rmtree(final)
    os.replace(tmp, final)
    v = sm.validate_book(final)
    res["valid"] = v["ok"]
    res["errors"] = sorted({e["check"] for e in v["errors"]})
    res["error_count"] = len(v["errors"])
    res["stats"] = v["stats"]
    return res


def _carried_number(rel: str, per_chapter: bool):
    name = rel.rsplit("/", 1)[-1][:-3]
    if per_chapter:
        m = re.match(r"^(?:ch(?:apter)?[_ ]?)?(\d{1,3})(?:[_ .\-]|$)", name, re.I)
        return int(m.group(1)) if m and int(m.group(1)) > 0 else None
    return sm.parse_chapter_number(_file_title_from_name(name + ".md", False))


# --- driver ----------------------------------------------------------------------------------

def check_paths(corpus: Path, out: Path, pdf_roots) -> str | None:
    c, o = corpus.resolve(), out.resolve()
    if o == c or _is_under(o, c) or _is_under(c, o):
        return f"--out {out} overlaps the corpus {corpus}"
    for r in pdf_roots:
        if _is_under(o, Path(r)):
            return f"--out {out} is inside --pdf-root {r}"
    return None


def load_json(path) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    # "_doc" and other "_"-keys hold notes, except a dict value: a book folder may itself start
    # with "_" (e.g. "_Guidelines"), and its entry must not be dropped silently
    return {k: v for k, v in raw.items() if not k.startswith("_") or isinstance(v, dict)}


def run(corpus, out, pdf_roots, books=None, split_config=None, pdf_map=None, force=False) -> int:
    corpus, out = Path(corpus), Path(out)
    if not corpus.is_dir():
        print(f"[resplit] corpus not found: {corpus}", file=sys.stderr)
        return 2
    bad = check_paths(corpus, out, pdf_roots)
    if bad:
        print(f"[resplit] refused: {bad}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = Path(split_config) if split_config else out / CONFIG_NAME
    config = load_json(cfg_path)
    pmap = load_json(pdf_map)
    print(f"[resplit] indexing PDFs under {len(pdf_roots)} root(s)…", flush=True)
    idx = PdfIndex(pdf_roots)
    report_path = out / REPORT_NAME
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    # root-level files of the corpus (aliases, defaults, notes) — not generated bookkeeping
    if not books:
        for p in corpus.iterdir():
            if p.is_file() and p.name not in ROOT_FILES_NOT_COPIED and \
                    (force or not (out / p.name).exists()):
                shutil.copyfile(p, out / p.name)
    tally = {}
    for d in sorted(corpus.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith(".") or (books and d.name not in books):
            continue
        has_md = any(f.endswith(".md") for _, _, fn in os.walk(d) for f in fn)
        if not has_md:
            report[d.name] = {"book": d.name, "status": "skipped", "reason": "no md files"}
            tally["skipped"] = tally.get("skipped", 0) + 1
            print(f"{d.name}: skipped (no md files)", flush=True)
            continue
        if (out / d.name / sm.MANIFEST_NAME).exists() and not force:
            tally["done_before"] = tally.get("done_before", 0) + 1
            print(f"{d.name}: skipped (output exists)", flush=True)
            continue
        t0 = time.time()
        try:
            r = resplit_book(d, out, idx, config, pmap, force)
        except Exception as e:  # noqa: BLE001 — one book never aborts the run
            r = {"book": d.name, "status": "error", "reason": f"{type(e).__name__}: {e}"}
            tmp = out / f".tmp-{d.name}"
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        r["seconds"] = round(time.time() - t0, 1)
        report[d.name] = r
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        st = r["status"] if r["status"] != "ok" else ("valid" if r.get("valid") else "invalid")
        tally[st] = tally.get(st, 0) + 1
        extra = r.get("reason") or (",".join(r.get("errors", [])) or "")
        print(f"{d.name}: {r.get('split_method', r['status'])} {st} "
              f"{r.get('stats', {}).get('files', '')} files {extra}", flush=True)
    print(f"[resplit] {tally}", flush=True)
    return 0
