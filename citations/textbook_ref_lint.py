#!/usr/bin/env python3
"""textbook_ref_lint — prove every "Book Ch.N" citation in a note points at a
chapter that actually exists, and print that chapter's real title.

The companion of figure_embed_lint (figure filenames). Same principle: a string
that must match something outside the note is verified against the authoritative
source, never trusted because it looks right.

A book + chapter number reads plausible no matter what you write. Two failures
this catches that no human review does:
  · The same surname is two different books. "ElMiedany Ch.5" is Psoriatic
    Arthritis in one and something unrelated in the other; the note says neither.
  · The number cited is the converter's file sequence, not the book's chapter.
    The content behind it is usually right, so nothing looks wrong — but a
    reader holding the paper book cannot follow the citation.

Source of truth: _chapter_index.json (see textbook_chapter_index.py).

Verdicts
  BAD_CHAPTER   chapter number past the end of a fully-converted
                book                                            → offender (exit 1)
  AMBIGUOUS     the name maps to several books whose chapter N
                differs — the citation cannot be resolved as
                written                                         → offender (exit 1)
  FILE_INDEX    the number is the converter's file sequence,
                not the book's chapter number                   → offender (exit 1)
  UNVERIFIABLE  book not in the corpus, or only partly
                converted so the chapter may simply be missing  → exit 3, never a pass
  OK            chapter exists; its real title is printed so a
                human can see whether it matches the claim

Usage
  python textbook_ref_lint.py <note.md|dir> ...
  python textbook_ref_lint.py --refs "ElMiedany Ch.5"   # pre-write check
  python textbook_ref_lint.py --show-ok                 # print resolved titles too
  python textbook_ref_lint.py --decisions               # names needing a pinned edition
  python textbook_ref_lint.py --textbook-dir PATH       # or set TEXTBOOK_DIR

Exit: 0 clean · 1 offenders found · 3 cannot verify (no index / nothing to check)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from textbook_chapter_index import load, load_aliases, resolve  # noqa: E402

DEFAULTS_NAME = "_chapter_index.defaults.json"
# A book whose md covers >=90% of its chapter run counts as fully converted, so
# a chapter number past its last chapter is a real error rather than a gap.
COMPLETE = 0.9

# The chapter marker itself; the book is whatever precedes it on the same line.
# Covers "Ch.49", "Ch 7", "Ch01", "Chapter 333".
CH_RE = re.compile(r"\bCh(?:apter)?\.?[ _]?(\d+)\b")
EDITION_RE = re.compile(r"^(?:e\d+|\d+e|\d+(?:st|nd|rd|th))$", re.IGNORECASE)
MAX_LOOKBACK = 12  # tokens; enough for "Frontera WR. Essentials of ... 4e Ch.63"
STRIP = "`[]()*_,;:"


def load_defaults(root: Path) -> dict[str, str]:
    """Which edition a bare surname means, when several are in the corpus.

    Written by a human — picking an edition is a judgment call, so this file is
    never auto-populated.
    """
    p = root / DEFAULTS_NAME
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k.lower(): v for k, v in raw.items() if not k.startswith("_")}
    return {}


def parse_ref(blob: str) -> tuple[list[str], str | None]:
    """Split the text before 'Ch.N' into (candidate name tokens, edition hint).

    Candidates are ordered nearest-first: "Frontera Essentials Ch.100" tries
    Essentials, then Frontera. Titles are cited as often as surnames, so one
    token is not enough to resolve a book.
    """
    tokens = [t.strip(STRIP) for t in re.split(r"[ _]+", blob) if t.strip(STRIP)]
    edition = next((t for t in tokens if EDITION_RE.match(t)), None)
    names = [t for t in tokens
             if not EDITION_RE.match(t) and not t.replace(".", "").isdigit() and len(t) > 1]
    return list(reversed(names)), edition


def edition_matches(folder: str, hint: str | None) -> bool:
    if not hint:
        return True
    n = re.sub(r"(?:st|nd|rd|th|e)$", "", hint.lower().lstrip("e"))
    return bool(n) and re.search(rf"_{n}e_", folder.lower()) is not None


_file_index_cache: dict[str, dict[str, str]] = {}


def file_index(root: Path, book: str) -> dict[str, str]:
    """{file-sequence number: title} for a book's md files."""
    if book in _file_index_cache:
        return _file_index_cache[book]
    out: dict[str, str] = {}
    d = root / book
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            m = (re.match(r"^ch(\d+)_(.+)\.md$", f.name, re.IGNORECASE)
                 or re.match(r"^(\d+)[._](.+)\.md$", f.name))
            if m:
                out.setdefault(str(int(m.group(1))), m.group(2).replace("_", " ").strip())
    _file_index_cache[book] = out
    return out


def owning_chapter(root: Path, book: str, seq: str) -> str | None:
    """The book chapter that the file at this sequence number belongs to.

    Preferring the chapter number embedded in the file's OWN name; falling back
    to the nearest preceding file that carries one. Ordering is numeric — a
    lexical sort puts ch100 before ch11 and picks the wrong owner.
    """
    d = root / book
    if not d.is_dir():
        return None
    numbered: list[tuple[int, int, str]] = []
    for f in d.glob("*.md"):
        m = re.match(r"^ch(\d+)_(\d+)[_ ](.+)\.md$", f.name, re.IGNORECASE)
        if m:
            numbered.append((int(m.group(1)), int(m.group(2)), m.group(3).replace("_", " ").strip()))
    numbered.sort()
    best = None
    for file_no, chap_no, title in numbered:
        if file_no <= int(seq):
            best = f"Ch.{chap_no} {title}"
        else:
            break
    return best


def check_ref(names: list[str], chapter: str, edition: str | None, index: dict,
              root: Path, aliases: dict[str, str], defaults: dict[str, str]) -> tuple[str, str, str]:
    """→ (verdict, detail, the name token that resolved)."""
    surname, books = "", []
    for cand in names:
        books = resolve(cand, index, aliases)
        if books:
            surname = cand
            break
    if not books:
        return "UNVERIFIABLE", f"'{names[0] if names else '?'}' is not in the corpus " \
                               f"(book not converted, or its cited name needs an alias)", ""

    if edition:
        narrowed = [b for b in books if edition_matches(b, edition)]
        if narrowed:
            books = narrowed
    pinned = defaults.get(surname.lower())
    if pinned and pinned in books:
        books = [pinned]

    mapped = [b for b in books if index["books"][b]["strategy"] != "none"]
    if not mapped:
        return "UNVERIFIABLE", f"{books[0]} has no machine-readable chapter structure " \
                               f"(sliced by page or by section)", surname

    have = [b for b in mapped if chapter in index["books"][b]["chapters"]]
    if not have:
        # A gap inside a partially-converted book proves nothing about the
        # citation — only a chapter number beyond the end of a book whose
        # conversion is complete is provably wrong.
        n = int(chapter)
        provable = [b for b in mapped
                    if n > index["books"][b]["max_chapter"] and index["books"][b]["coverage"] >= COMPLETE]
        # Before calling it wrong: is it the converter's file sequence? Only
        # trustworthy for a fully-converted book — in a half-converted one the
        # cited number is more likely a real chapter that was never converted,
        # and file sequence N would coincide with it by accident.
        for b in mapped:
            title = file_index(root, b).get(chapter)
            if title and index["books"][b]["coverage"] >= COMPLETE:
                owner = owning_chapter(root, b, chapter)
                where = f", which sits in {owner}" if owner else ""
                return "FILE_INDEX", (f"{b}: Ch.{chapter} is the converter's file sequence, not the "
                                      f"book's chapter — ch{chapter}_{title}{where}"), surname
        rng = ", ".join(f"{b.split('_')[0]} ends at Ch.{index['books'][b]['max_chapter']} "
                        f"({int(index['books'][b]['coverage'] * 100)}% converted)" for b in mapped)
        who = mapped[0] if len(mapped) == 1 else f"{len(mapped)} candidate books"
        if len(provable) == len(mapped):
            return "BAD_CHAPTER", (f"{who} has no Ch.{chapter}, and it is past the end of the book "
                                   f"({rng}) — wrong number, or a same-name book that is not in "
                                   f"the corpus"), surname
        return "UNVERIFIABLE", (f"{who}: Ch.{chapter} was not converted, so it cannot be checked "
                                f"({rng})"), surname

    titles = {index["books"][b]["chapters"][chapter] for b in have}
    if len(have) > 1 and len(titles) > 1:
        detail = " | ".join(f"{b} → Ch.{chapter} = {index['books'][b]['chapters'][chapter]}" for b in have)
        return "AMBIGUOUS", detail, surname
    b = have[0]
    note = "" if len(mapped) == len(books) else "  (a same-name book could not be checked)"
    return "OK", f"{b} Ch.{chapter} = {index['books'][b]['chapters'][chapter]}{note}", surname


def collect_notes(targets: list[str]) -> list[Path]:
    paths: list[Path] = []
    for t in targets:
        p = Path(t)
        paths.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    return paths


def scan_text(text: str) -> list[tuple[list[str], str, str | None, str]]:
    """Find every "... Ch.N" on each line, with the book candidates before it.

    Line-scoped, and cut at the opening backtick when the reference sits in
    inline code — so `A Ch.1` `B Ch.2` on one line stay two independent
    references instead of bleeding into each other.
    """
    out = []
    for line in text.splitlines():
        for m in CH_RE.finditer(line):
            before = line[:m.start()]
            if before.count("`") % 2 == 1:
                before = before.rsplit("`", 1)[-1]
            names, edition = parse_ref(before)
            if names:
                # "Ch01" and "Ch.1" are the same chapter — index keys are bare ints
                out.append((names[:MAX_LOOKBACK], str(int(m.group(1))), edition,
                            (before[-40:] + m.group(0)).strip()))
    return out


def lint(targets: list[str] | None = None, refs: list[str] | None = None, *,
         textbook_dir: str | None = None, show_ok: bool = False,
         decisions: bool = False, quiet: bool = False) -> int:
    """Run the chapter check. Exit codes as documented at the top of the file."""
    root = Path(textbook_dir or os.environ.get("TEXTBOOK_DIR", ".")).expanduser()
    try:
        index = load(root)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"UNVERIFIABLE: chapter index unusable ({e}).", file=sys.stderr)
        print("  Run: python textbook_chapter_index.py --rebuild", file=sys.stderr)
        print("  Do NOT report the chapter references as verified.", file=sys.stderr)
        return 3
    aliases, defaults = load_aliases(root), load_defaults(root)

    items: list[tuple[str, list[str], str, str | None, str]] = []
    for raw in refs or []:
        for names, ch, ed, txt in scan_text(raw):
            items.append(("--refs", names, ch, ed, txt))
    if not refs:
        for note in collect_notes(targets or []):
            try:
                text = note.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for names, ch, ed, txt in scan_text(text):
                items.append((note.name, names, ch, ed, txt))

    if not items:
        print("UNVERIFIABLE: no 'Book Ch.N' references found in the target(s).", file=sys.stderr)
        return 3

    verdicts: dict[str, list[str]] = defaultdict(list)
    ambiguous_names: Counter = Counter()
    unknown_names: Counter = Counter()
    seen: set[tuple[str, tuple[str, ...], str, str | None]] = set()

    for where, names, ch, ed, raw in items:
        key = (where, tuple(names), ch, ed)
        if key in seen:
            continue  # a note citing the same chapter 40x is one finding
        seen.add(key)
        verdict, detail, surname = check_ref(names, ch, ed, index, root, aliases, defaults)
        verdicts[verdict].append(f"{where} · `{raw}`\n      {detail}")
        if verdict == "AMBIGUOUS":
            ambiguous_names[surname.lower()] += 1
        elif verdict == "UNVERIFIABLE" and "not in the corpus" in detail:
            unknown_names[(surname or names[0]).lower()] += 1

    if decisions:
        print("Names needing a pinned edition (several books, same chapter differs):")
        for s, n in ambiguous_names.most_common():
            print(f"\n  {s} — {n} ambiguous citation(s)")
            for b in resolve(s, index, aliases):
                meta = index["books"][b]
                print(f"      {b}  [{meta['strategy']}, {len(meta['chapters'])} ch]")
        print(f"\nPin them in {root / DEFAULTS_NAME}, e.g. "
              f'{{"braddom": "Braddom_PM-and-R_7e_2021"}}')
        print("\nNames that could not be resolved (missing alias, or not a textbook citation):")
        for s, n in unknown_names.most_common(30):
            print(f"  {s} ({n})")
        return 0

    if not quiet:
        counts = " · ".join(f"{k} {len(v)}" for k, v in sorted(verdicts.items()))
        print(f"chapter check: {len(seen)} distinct reference(s) · {counts}")

    for v in ("BAD_CHAPTER", "AMBIGUOUS", "FILE_INDEX"):
        if verdicts[v]:
            print(f"\n{v} ({len(verdicts[v])})")
            for line in verdicts[v][:60]:
                print("    " + line)
            if len(verdicts[v]) > 60:
                print(f"    ...{len(verdicts[v]) - 60} more")
    if verdicts["UNVERIFIABLE"] and not quiet:
        print(f"\nUNVERIFIABLE ({len(verdicts['UNVERIFIABLE'])}) — cannot be proven either way, "
              f"do not read as a pass")
        for line in verdicts["UNVERIFIABLE"][:20]:
            print("    " + line)
        if len(verdicts["UNVERIFIABLE"]) > 20:
            print(f"    ...{len(verdicts['UNVERIFIABLE']) - 20} more (--decisions for a summary)")
    if show_ok:
        print(f"\nOK ({len(verdicts['OK'])})")
        for line in verdicts["OK"]:
            print("    " + line)

    if verdicts["BAD_CHAPTER"] or verdicts["AMBIGUOUS"] or verdicts["FILE_INDEX"]:
        print("\nFix by reading the book and chapter off the corpus "
              "(textbook_chapter_index.py --show <name>), never by inferring the number from the")
        print("topic. When one name means several books, cite the edition (`Braddom 7e Ch.49`).")
        print("FILE_INDEX means you cited a file sequence: the content is probably right, but a")
        print("reader holding the book cannot find it — replace it with the book's chapter number.")
        return 1
    if verdicts["UNVERIFIABLE"]:
        return 3
    if not quiet:
        print("OK — every chapter reference resolves")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", help="notes or dirs to lint")
    ap.add_argument("--refs", nargs="+", metavar="REF", help='check bare refs, e.g. "ElMiedany Ch.5"')
    ap.add_argument("--textbook-dir", help="converted markdown corpus (or set TEXTBOOK_DIR)")
    ap.add_argument("--show-ok", action="store_true", help="print resolved chapter titles too")
    ap.add_argument("--decisions", action="store_true", help="list names needing a pinned edition")
    ap.add_argument("--quiet", action="store_true", help="only print offenders")
    a = ap.parse_args()
    return lint(a.targets, a.refs, textbook_dir=a.textbook_dir,
                show_ok=a.show_ok, decisions=a.decisions, quiet=a.quiet)


if __name__ == "__main__":
    sys.exit(main())
