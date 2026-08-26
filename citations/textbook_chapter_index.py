#!/usr/bin/env python3
"""textbook_chapter_index — build the authoritative book → chapter → title table.

Source of truth: your converted markdown corpus (the tree this repo's converter
writes). Chapter numbering lives in the filenames, and the converter has
produced several naming conventions over the years; this module normalises them
and refuses to guess when a book has no chapter structure at all (page-sliced
books, or trees that are only front matter).

A book that cannot be mapped is recorded with `strategy: "none"`. A reference
into it is UNVERIFIABLE, never a pass.

The corpus root is never the current working directory. It is `--textbook-dir`, else
`TEXTBOOK_DIR`, else the repo's own `output/` (`OUTPUT_DIR`) when that exists; with none
of those the command fails instead of scanning wherever it happens to have been run.

Usage
  python textbook_chapter_index.py --rebuild            # rescan, write the index
  python textbook_chapter_index.py --stats              # summarise the cached index
  python textbook_chapter_index.py --show Braddom       # chapter table for a cited name
  python textbook_chapter_index.py --textbook-dir PATH  # or set TEXTBOOK_DIR
  python textbook_chapter_index.py --rebuild --allow-empty   # index a corpus where no book
                                                             # is chapter-shaped (rare)

Data files, all beside the corpus and all optional except the index itself:
  _chapter_index.json           written by --rebuild
  _chapter_index.aliases.json   {"cyriax": "Ombregt_"} — cited name → folder prefix,
                                for books known by their original author or series
                                name rather than the folder's current editor
  _chapter_index.defaults.json  {"braddom": "Braddom_PM-and-R_7e_2021"} — which
                                edition a bare surname means, when you own several
  _chapter_index.chapters.json  {"Book_Folder": {"7": "Real Chapter Title"}} — the real
                                chapter table for a book whose filenames do not carry one
                                (see strategy "seq" below). Human-written, never generated:
                                deciding a book's chapter numbers is a judgment call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import OUTPUT_DIR  # noqa: E402

INDEX_NAME = "_chapter_index.json"
ALIASES_NAME = "_chapter_index.aliases.json"
CHAPTERS_NAME = "_chapter_index.chapters.json"

NO_ROOT_MSG = (
    "no textbook corpus to scan: pass --textbook-dir PATH, or set TEXTBOOK_DIR, "
    "or convert some books so this repo's output/ directory exists"
)


def default_root() -> Path | None:
    """The corpus root when the caller named none — TEXTBOOK_DIR, else the repo's own
    output/ if it exists. Never the process working directory.

    Defaulting to cwd was silent in both directions: run from the repo root it scanned
    `converter/`, `docs/`, … as if each were a book and wrote a 0-book index reporting
    OK; run from a Windows home directory it died on the `Application Data` junction
    with PermissionError. Neither said which tree it had actually scanned.
    """
    env = os.environ.get("TEXTBOOK_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return OUTPUT_DIR if OUTPUT_DIR.is_dir() else None


def resolve_root(textbook_dir: str | None) -> Path | None:
    """--textbook-dir wins; otherwise default_root(). None means "nothing to scan"."""
    return Path(textbook_dir).expanduser() if textbook_dir else default_root()


def index_path(root: Path) -> Path:
    return root / INDEX_NAME


def _load_side_file(root: Path, name: str) -> dict:
    p = root / name
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    return {}


def load_aliases(root: Path) -> dict[str, str]:
    return {k.lower(): v for k, v in _load_side_file(root, ALIASES_NAME).items()}


def load_pinned_chapters(root: Path) -> dict[str, dict[str, str]]:
    """{folder name: {chapter number: title}} — a hand-written chapter table for a book
    whose filenames carry only a file sequence. Never written by this module."""
    out: dict[str, dict[str, str]] = {}
    for book, table in _load_side_file(root, CHAPTERS_NAME).items():
        if isinstance(table, dict):
            out[book] = {str(int(k)): str(v) for k, v in table.items() if str(k).strip().isdigit()}
    return out


# --- chapter-number extraction strategies -----------------------------------
# Ordered MOST SPECIFIC FIRST: the first strategy that yields a real chapter
# table wins. Ranking by "most chapters found" instead would let a loose pattern
# outvote a precise one — a book numbered "<part>_<chapter>_Title.md" has its
# part numbers beat its chapter numbers that way.
# E: "02_8_Mechanical_disorders.md"  → part_chapter
RE_E = re.compile(r"^(?:\d+)_(\d+)[_ ](.+)\.md$")
# B: "ch07_2_Standardized_....md"    → inner number is the chapter number
RE_B = re.compile(r"^ch\d+_(\d+)[_ ](.+)\.md$")
# C: "ch09_Chapter_4_Nutrition.md" / "ch13_Chapter5.md" (title in the NEXT file)
RE_C = re.compile(r"^ch\d+_Chapter[_ ]?(\d+)(?:[_ ](.+))?\.md$", re.IGNORECASE)
# A: "Ch12_Title.md"                 → file index IS the chapter number
RE_A = re.compile(r"^Ch(\d+)[_ ](.+)\.md$")
# D: "22.Title.md" / "05_Title.md"
RE_D = re.compile(r"^(\d+)[._](.+)\.md$")
# F: one sub-DIRECTORY per chapter, "001_Biology_of_the_Normal_Joint/"
# SEQ: "ch01_Introduction.md" — this repo's OWN converter output. The number is a file
# sequence (convert.py's split_into_chapters: `ch_num = idx + 1`, counted over detected
# split points, which for a bookmark split includes level-2 sub-sections), NOT the book's
# printed chapter number. It is therefore matched LAST and never yields a chapter table.
RE_SEQ = re.compile(r"^ch(\d+)[_ ](.+)\.md$", re.IGNORECASE)

ORDER = (("E", RE_E), ("B", RE_B), ("C", RE_C), ("A", RE_A), ("D", RE_D))
SKIP_DIRS = {"_Guidelines", "_figure_remap", "_epub_src", "__pycache__"}


def clean_title(raw: str) -> str:
    t = raw.replace("_", " ").strip()
    t = re.sub(r"\.pdf$", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t)


def map_book(files: list[str], subdirs: list[str]) -> tuple[str, dict[str, str], dict[str, str]]:
    """→ (strategy, {chapter_number: title}, {file_sequence: title}).

    Strategy 'seq' = this repo's converter split the book, so the numbers on disk are a
    file sequence and no chapter table exists; the sequence table is returned instead so
    the lint can say which file a cited number points at. Strategy 'none' = unmappable.
    """
    for strat, rx in ORDER:
        chapters: dict[str, str] = {}
        for i, name in enumerate(files):
            m = rx.match(name)
            if not m:
                continue
            num = int(m.group(1))
            title = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            # Strategy C stub files ("ch13_Chapter5.md") carry no title — the
            # chapter body is the next file. Take its name, never invent one.
            if strat == "C" and not title and i + 1 < len(files):
                nxt = files[i + 1]
                if not RE_C.match(nxt):
                    title = re.sub(r"^ch\d+_", "", nxt[:-3])
            if not title or num == 0:  # ch0/00 is front matter by convention
                continue
            chapters.setdefault(str(num), clean_title(title))
        # A real numbering scheme covers a book; two or three hits is noise.
        if len(chapters) >= 3:
            return strat, chapters, {}
    chapters = {}
    for name in sorted(subdirs):
        m = re.match(r"^(\d+)_(.+)$", name)
        if m and int(m.group(1)) > 0:
            chapters.setdefault(str(int(m.group(1))), clean_title(m.group(2)))
    if len(chapters) >= 3:
        return "F", chapters, {}
    # Last: the converter's own naming. Recognising it is worth doing even though it
    # yields no chapter numbers — "split by file sequence" is a different answer from
    # "no structure at all", and it is the one the user can act on.
    sequence: dict[str, str] = {}
    for name in files:
        m = RE_SEQ.match(name)
        if m and int(m.group(1)) > 0:
            sequence.setdefault(str(int(m.group(1))), clean_title(m.group(2)))
    if len(sequence) >= 3:
        return "seq", {}, sequence
    return "none", {}, {}


def build(root: Path) -> dict:
    if not root.is_dir():
        raise RuntimeError(f"textbook dir not found: {root}")
    pinned = load_pinned_chapters(root)
    books: dict[str, dict] = {}
    skipped: list[str] = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError as e:
        raise RuntimeError(f"cannot list textbook dir {root}: {e}") from e
    for d in entries:
        if d.name in SKIP_DIRS:
            continue
        # One unreadable directory must not take down the rebuild. A Windows user profile
        # is full of junctions ("Application Data") that raise PermissionError on listing,
        # and an aborted rebuild writes nothing at all.
        try:
            files = sorted(f.name for f in d.glob("*.md"))
            subdirs = [s.name for s in d.iterdir() if s.is_dir() and s.name not in SKIP_DIRS]
        except OSError:
            skipped.append(d.name)
            continue
        if not files and not subdirs:
            continue
        if d.name in pinned:
            strategy, chapters, sequence = "pinned", dict(pinned[d.name]), {}
        else:
            strategy, chapters, sequence = map_book(files, subdirs)
        nums = sorted(int(k) for k in chapters)
        # Coverage says whether the CONVERSION is complete, which decides how a
        # missing chapter must be read: a gap in a half-converted book means
        # "cannot verify", not "the citation is wrong".
        books[d.name] = {
            "strategy": strategy,
            "surname": d.name.split("_")[0],
            "files": len(files),
            "max_chapter": nums[-1] if nums else 0,
            "coverage": round(len(nums) / nums[-1], 3) if nums else 0.0,
            "chapters": chapters,
            "sequence": sequence,
        }
    return {"root": str(root), "books": books, "skipped": skipped}


def load(root: Path | None = None) -> dict:
    p = index_path(root)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} missing — run: python textbook_chapter_index.py --rebuild"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def resolve(surname: str, index: dict, aliases: dict[str, str] | None = None) -> list[str]:
    """→ folder names whose surname matches the cited token (case-insensitive)."""
    # strip a possessive suffix only — str.rstrip("'s") would eat a trailing
    # 's' from a real surname (Helms → helm).
    s = re.sub(r"['’]s$", "", surname.strip().lower())
    alias = (aliases or {}).get(s)
    hits = []
    for name, meta in index["books"].items():
        if meta["surname"].lower() == s or (alias and name.lower().startswith(alias.lower())):
            hits.append(name)
    return sorted(hits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="rescan the corpus and write the index")
    ap.add_argument("--stats", action="store_true", help="summarise the cached index")
    ap.add_argument("--show", metavar="NAME", help="print the chapter table for a cited name")
    ap.add_argument("--textbook-dir", help="converted markdown corpus (or set TEXTBOOK_DIR)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="write the index even when no book is recognised (default: refuse)")
    args = ap.parse_args()
    root = resolve_root(args.textbook_dir)
    if root is None:
        print(f"REFUSED: {NO_ROOT_MSG}", file=sys.stderr)
        return 2

    if args.rebuild:
        idx = build(root)
        books = idx["books"]
        recognised = sum(1 for b in books.values() if b["strategy"] != "none")
        if not recognised and not args.allow_empty:
            # This is what scanning the wrong directory looks like, and it used to print OK.
            print(f"REFUSED: nothing under {root} looks like a converted book "
                  f"({len(books)} directories examined, 0 recognised) — wrong --textbook-dir? "
                  f"Pass --allow-empty to write the index anyway. No index was written.",
                  file=sys.stderr)
            return 2
        index_path(root).write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        mapped = sum(1 for b in books.values() if b["strategy"] not in ("none", "seq"))
        seq = sum(1 for b in books.values() if b["strategy"] == "seq")
        skipped = idx.get("skipped") or []
        line = (f"OK  {index_path(root)}\n    scanned {root}\n    {len(books)} books · "
                f"{mapped} with a chapter map · {seq} sequence-split · "
                f"{len(books) - mapped - seq} unmappable")
        if skipped:
            line += f"\n    {len(skipped)} directories skipped (unreadable): {', '.join(skipped[:5])}"
        print(line)
        return 0

    try:
        idx = load(root)
    except FileNotFoundError as e:
        print(f"UNVERIFIABLE: {e}", file=sys.stderr)
        return 3

    if args.stats:
        by_strat: dict[str, int] = {}
        for b in idx["books"].values():
            by_strat[b["strategy"]] = by_strat.get(b["strategy"], 0) + 1
        print(f"{len(idx['books'])} books")
        for k in sorted(by_strat):
            print(f"  strategy {k}: {by_strat[k]}")
        print("\nSequence-split (chapter numbers are not on disk; pin them in "
              f"{CHAPTERS_NAME}):")
        for n, b in sorted(idx["books"].items()):
            if b["strategy"] == "seq":
                print(f"  {n}  ({b['files']} files)")
        print("\nUnmappable (references into these are UNVERIFIABLE):")
        for n, b in sorted(idx["books"].items()):
            if b["strategy"] == "none":
                print(f"  {n}  ({b['files']} files)")
        return 0

    if args.show:
        hits = resolve(args.show, idx, load_aliases(root))
        if not hits:
            print(f"no book matches '{args.show}'")
            return 1
        for h in hits:
            b = idx["books"][h]
            if b["strategy"] == "seq":
                seq = b.get("sequence") or {}
                print(f"\n=== {h}  [strategy seq — {len(seq)} files, split by the converter's "
                      f"file sequence; these are NOT chapter numbers]")
                for n in sorted(seq, key=int):
                    print(f"  file {n:<4} {seq[n]}")
                continue
            print(f"\n=== {h}  [strategy {b['strategy']}, {len(b['chapters'])} chapters, "
                  f"{int(b['coverage'] * 100)}% converted]")
            for n in sorted(b["chapters"], key=int):
                print(f"  Ch.{n:<4} {b['chapters'][n]}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
