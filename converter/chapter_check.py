"""chapter_check.py — does each file's chapter_number match what its own pages print? Read-only.

The validator checks that a split is internally consistent (no gaps, overlaps, order breaks); it
cannot see a book whose every chapter number is off by a constant (DeLee, +7, found 2026-11-05
by a by-hand sample). This looks at the first two pages of every `body` file that carries a
`chapter_number` n:

  confirmed     "Chapter n", a line that is just n, a line "n <one of the title's words>", or
                n.x figure / table / section numbering
  contradicted  none of that, and "Chapter m" (m != n) is there
  unknown       neither

Cross-references ("see Chapter 17") make a few files `contradicted` in many correct books, so
this is a report, not a gate: a book with more contradicted than confirmed files is worth a look.

  python converter/chapter_check.py <corpus> [--book B]... [--sample N] [--seed S] [--json FILE]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_manifest as sm  # noqa: E402

_SPACED = re.compile(r"(?<!\S)((?:\S ){2,}\S)(?!\S)")       # "C H A P T E R  8" -> "CHAPTER 8"
_WORD_CHAPTER = re.compile(r"(?im)^\s*chapter\s+([a-z]+(?:-[a-z]+)?)\b")
_GLUED = re.compile(r"(?i)\bchapter(\d)")


def file_verdict(text: str, n: int, title: str) -> dict:
    head = sm.first_pages_text(text, 2, 6000)
    head = _SPACED.sub(lambda m: m.group(1).replace(" ", ""), head)
    head = _WORD_CHAPTER.sub(lambda m: f"CHAPTER {sm._WORDNUM.get(m.group(1).lower(), m.group(1))}", head)
    head = _GLUED.sub(r"CHAPTER \1", head)
    lines = [ln.strip() for ln in head.split("\n") if ln.strip()][:60]
    title_words = [w for w in sm.norm_text(sm.strip_numbering(title or "")).split() if len(w) >= 3][:3]
    ev = []
    if re.search(rf"\bchapter\s*{n}\b", head, re.I):
        ev.append("chapter n")
    for ln in lines:
        if re.fullmatch(rf"{n}", ln):
            ev.append("bare n line")
            break
        m = re.match(rf"^{n}(?:\.|\s|:)\s*(.*)$", ln)
        if m and title_words and any(w in sm.norm_text(m.group(1)) for w in title_words):
            ev.append("n + title")
            break
    if not ev and re.search(rf"(?:\b(?:fig(?:ure)?|table|box)\.?\s*|^){n}\.\d{{1,2}}\b", head, re.I | re.M):
        ev.append("n.x numbering")
    other = {int(x) for x in re.findall(r"\bchapter\s*(\d{1,3})\b", head, re.I)} - {n}
    verdict = "confirmed" if ev else ("contradicted" if other else "unknown")
    return {"verdict": verdict, "evidence": ev, "other_chapter_numbers": sorted(other),
            "head": " | ".join(lines[:8])[:300]}


def numbered_files(book_dir: Path) -> list[dict]:
    mp = book_dir / sm.MANIFEST_NAME
    if not mp.exists():
        return []
    m = json.loads(mp.read_text(encoding="utf-8"))
    return [f for f in m.get("files", [])
            if f.get("chapter_number") is not None and f.get("kind") == "body"]


def check_book(book_dir: Path, files: list[dict] | None = None) -> dict:
    """{"book", "counts": {confirmed, unknown, contradicted}, "rows": [...], "suspect": bool}."""
    book_dir = Path(book_dir)
    rows = []
    for f in (numbered_files(book_dir) if files is None else files):
        p = book_dir / f["file"]
        if not p.exists():
            continue
        r = file_verdict(p.read_text(encoding="utf-8", errors="replace"), f["chapter_number"], f.get("title"))
        rows.append({"book": book_dir.name, "file": f["file"], "chapter_number": f["chapter_number"],
                     "title": f.get("title"), **r})
    counts = {k: sum(r["verdict"] == k for r in rows) for k in ("confirmed", "unknown", "contradicted")}
    return {"book": book_dir.name, "counts": counts, "rows": rows,
            "suspect": counts["contradicted"] > counts["confirmed"]}


def book_dirs(corpus: Path, books=None) -> list[Path]:
    return [d for d in sorted(corpus.iterdir(), key=lambda p: p.name.lower())
            if d.is_dir() and not sm.is_tooling_dir(d.name) and (d / sm.MANIFEST_NAME).exists()
            and (not books or d.name in books)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check chapter numbers against each file's own pages (read-only)")
    ap.add_argument("corpus")
    ap.add_argument("--book", action="append", help="only this book folder (repeatable)")
    ap.add_argument("--sample", type=int, help="check N files, one per book first, then at random")
    ap.add_argument("--seed", type=int, default=20261109)
    ap.add_argument("--json", metavar="FILE", help="write every row as JSON")
    a = ap.parse_args(argv)
    corpus = Path(a.corpus)
    dirs = book_dirs(corpus, a.book)
    if a.sample:
        pool = [(d, f) for d in dirs for f in numbered_files(d)]
        rng = random.Random(a.seed)
        rng.shuffle(pool)
        picked, seen = [], set()
        for d, f in pool:  # one file per book first (spread), then fill at random
            if d.name not in seen:
                picked.append((d, f))
                seen.add(d.name)
        rest = [x for x in pool if x not in picked]
        picked = picked[:a.sample] if len(picked) >= a.sample else picked + rest[: a.sample - len(picked)]
        results = [check_book(d, [f for dd, f in picked if dd == d]) for d in dirs
                   if any(dd == d for dd, _ in picked)]
    else:
        results = [check_book(d) for d in dirs]
    total = {"confirmed": 0, "unknown": 0, "contradicted": 0}
    for r in results:
        for k in total:
            total[k] += r["counts"][k]
        if r["suspect"]:
            c = r["counts"]
            print(f"SUSPECT  {r['book']}  confirmed {c['confirmed']}, unknown {c['unknown']}, "
                  f"contradicted {c['contradicted']}")
    print(f"{sum(len(r['rows']) for r in results)} files in {len(results)} books: {total}; "
          f"{sum(r['suspect'] for r in results)} suspect")
    if a.json:
        Path(a.json).write_text(json.dumps({"counts": total, "books": results}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
