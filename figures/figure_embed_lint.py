#!/usr/bin/env python3
"""figure_embed_lint — prove every figure a note embeds actually exists on disk.

The extract contract returns the authoritative path in its `file` field. Embeds
must copy that string verbatim; anything rebuilt from the `--out` template or
from the `Fig_{id}_{Book}` naming convention is a guess. Guesses read correctly
in review (the name is *derived from* the real one) and only a machine catches
them.

Exact-match strings — filenames, ids, keys — are copied from an authoritative
list, never generated from a pattern. Injection prevents; this lint proves.

Two failure modes this catches:
  MISSING       — no such attachment anywhere in the notes tree (bad guess, or
                  the extract failed and the embed was written anyway)
  CASE MISMATCH — file exists under different casing. Windows and macOS open it
                  fine; git and Linux do not. A latent breakage.

Usage
  python figure_embed_lint.py <note.md|dir> ...      # lint notes
  python figure_embed_lint.py --files a.png b.png    # pre-write check of paths
  python figure_embed_lint.py --notes-dir <path> ... # notes tree to resolve against

Exit: 0 clean · 1 broken embeds found · 3 cannot verify (notes dir missing)
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
import os
from pathlib import Path

DEFAULT_NOTES_DIR = Path(os.environ.get("NOTES_DIR", ".")).expanduser()
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".pdf", ".mp4", ".webm"}
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__", "_render_cache"}

WIKI_EMBED = re.compile(r"!\[\[([^\]\|#]+)")
MD_EMBED = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def index_notes_dir(root: Path) -> dict[str, list[Path]]:
    """lowercased basename → actual paths (case-exact)."""
    idx: dict[str, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.is_dir() or SKIP_DIRS & set(p.parts):
            continue
        if p.suffix.lower() in ASSET_EXT:
            idx[p.name.lower()].append(p)
    return idx


def targets_in(note: Path) -> list[str]:
    text = note.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in WIKI_EMBED.finditer(text):
        out.append(m.group(1).strip())
    for m in MD_EMBED.finditer(text):
        t = m.group(1).strip()
        if not t.startswith(("http://", "https://", "data:")):
            out.append(t.split("#")[0])
    return out


def verdict(target: str, idx: dict[str, list[Path]]) -> str | None:
    """→ problem string, or None if the embed resolves exactly."""
    # `![[Fig.png\|300]]` inside a table escapes the alias pipe — drop the trailing backslash
    name = Path(target.replace("%20", " ").rstrip("\\")).name
    if Path(name).suffix.lower() not in ASSET_EXT:
        return None  # note transclusion, not an attachment
    hits = idx.get(name.lower(), [])
    if not hits:
        return f"MISSING       {target}"
    if any(h.name == name for h in hits):
        return None
    return f"CASE MISMATCH {target}\n                  on disk: {hits[0].name}"


def collect_notes(items: list[str]) -> list[Path]:
    paths: list[Path] = []
    for t in items:
        p = Path(t)
        paths.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", help="notes or dirs to lint")
    ap.add_argument("--files", nargs="+", metavar="PATH", help="check attachment paths directly")
    ap.add_argument("--notes-dir", default=str(DEFAULT_NOTES_DIR),
                    help="root to resolve embeds against (or set NOTES_DIR)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.notes_dir)
    if not root.is_dir():
        print(f"UNVERIFIABLE: notes dir not found: {root}", file=sys.stderr)
        return 3
    idx = index_notes_dir(root)
    if not idx:
        print(f"UNVERIFIABLE: no attachments indexed under {root}", file=sys.stderr)
        return 3

    problems: list[str] = []
    checked = 0

    for f in args.files or []:
        checked += 1
        if v := verdict(f, idx):
            problems.append(f"--files\n    {v}")

    for note in collect_notes(args.targets):
        found = []
        for t in targets_in(note):
            checked += 1
            if v := verdict(t, idx):
                found.append(v)
        if found:
            problems.append(note.name + "\n    " + "\n    ".join(found))

    if not args.quiet:
        print(f"{sum(len(v) for v in idx.values())} attachments indexed · checked {checked} embed(s)")
    if problems:
        print(f"\n❌ {len(problems)} note(s)/batch with broken embeds:\n")
        for p in problems:
            print("  " + p)
        print("\nFix by copying the `file` value the extract contract returned, verbatim.")
        print("Never rebuild the name from --out or from the Fig_{id}_{Book} convention")
        print("(the fast path returns .jpeg where the template says .png).")
        return 1
    if not args.quiet:
        print("✅ every embed resolves to a real file, exact case")
    return 0


if __name__ == "__main__":
    sys.exit(main())
