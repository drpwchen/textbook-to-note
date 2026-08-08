#!/usr/bin/env python3
"""textbook_chapter_index — build the authoritative book → chapter → title table.

Source of truth: your converted markdown corpus (the tree this repo's converter
writes). Chapter numbering lives in the filenames, and the converter has
produced several naming conventions over the years; this module normalises them
and refuses to guess when a book has no chapter structure at all (page-sliced
books, or trees that are only front matter).

A book that cannot be mapped is recorded with `strategy: "none"`. A reference
into it is UNVERIFIABLE, never a pass.

Usage
  python textbook_chapter_index.py --rebuild            # rescan, write the index
  python textbook_chapter_index.py --stats              # summarise the cached index
  python textbook_chapter_index.py --show Braddom       # chapter table for a cited name
  python textbook_chapter_index.py --textbook-dir PATH  # or set TEXTBOOK_DIR

Data files, all beside the corpus and all optional except the index itself:
  _chapter_index.json           written by --rebuild
  _chapter_index.aliases.json   {"cyriax": "Ombregt_"} — cited name → folder prefix,
                                for books known by their original author or series
                                name rather than the folder's current editor
  _chapter_index.defaults.json  {"braddom": "Braddom_PM-and-R_7e_2021"} — which
                                edition a bare surname means, when you own several
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TEXTBOOK_ROOT = Path(os.environ.get("TEXTBOOK_DIR", ".")).expanduser()
INDEX_NAME = "_chapter_index.json"
ALIASES_NAME = "_chapter_index.aliases.json"


def index_path(root: Path | None = None) -> Path:
    return (root or TEXTBOOK_ROOT) / INDEX_NAME


def load_aliases(root: Path | None = None) -> dict[str, str]:
    p = (root or TEXTBOOK_ROOT) / ALIASES_NAME
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k.lower(): v for k, v in raw.items() if not k.startswith("_")}
    return {}


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

ORDER = (("E", RE_E), ("B", RE_B), ("C", RE_C), ("A", RE_A), ("D", RE_D))
SKIP_DIRS = {"_Guidelines", "_figure_remap", "_epub_src", "__pycache__"}


def clean_title(raw: str) -> str:
    t = raw.replace("_", " ").strip()
    t = re.sub(r"\.pdf$", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t)


def map_book(files: list[str], subdirs: list[str]) -> tuple[str, dict[str, str]]:
    """→ (strategy, {chapter_number: title}). Strategy 'none' = unmappable."""
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
            return strat, chapters
    chapters = {}
    for name in sorted(subdirs):
        m = re.match(r"^(\d+)_(.+)$", name)
        if m and int(m.group(1)) > 0:
            chapters.setdefault(str(int(m.group(1))), clean_title(m.group(2)))
    if len(chapters) >= 3:
        return "F", chapters
    return "none", {}


def build(root: Path) -> dict:
    if not root.is_dir():
        raise RuntimeError(f"textbook dir not found: {root}")
    books: dict[str, dict] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if d.name in SKIP_DIRS:
            continue
        files = sorted(f.name for f in d.glob("*.md"))
        subdirs = [s.name for s in d.iterdir() if s.is_dir() and s.name not in SKIP_DIRS]
        if not files and not subdirs:
            continue
        strategy, chapters = map_book(files, subdirs)
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
        }
    return {"root": str(root), "books": books}


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
    alias = (aliases if aliases is not None else load_aliases()).get(s)
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
    ap.add_argument("--textbook-dir", default=str(TEXTBOOK_ROOT),
                    help="converted markdown corpus (or set TEXTBOOK_DIR)")
    args = ap.parse_args()
    root = Path(args.textbook_dir).expanduser()

    if args.rebuild:
        idx = build(root)
        index_path(root).write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        mapped = sum(1 for b in idx["books"].values() if b["strategy"] != "none")
        print(f"OK  {index_path(root)}\n    {len(idx['books'])} books · {mapped} with a chapter map · "
              f"{len(idx['books']) - mapped} unmappable")
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
            print(f"\n=== {h}  [strategy {b['strategy']}, {len(b['chapters'])} chapters, "
                  f"{int(b['coverage'] * 100)}% converted]")
            for n in sorted(b["chapters"], key=int):
                print(f"  Ch.{n:<4} {b['chapters'][n]}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
