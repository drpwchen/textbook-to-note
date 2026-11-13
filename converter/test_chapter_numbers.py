"""test_chapter_numbers.py — openspec change chapter-numbers-few-or-lost: an outline with only
one or two `Chapter N` entries still has a chapter level (no split config needed), and the
validator fails a book whose chapter numbers were all lost although the split was told the book
is numbered — while a book with no numbered titles keeps passing. Synthetic PDFs only.

  python converter/test_chapter_numbers.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import resplit as rs  # noqa: E402
import split_manifest as sm  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def make_book(tmp: Path, name: str, pages: dict, toc, config=None):
    """pages: {pdf page: text}; every other page gets filler. Returns (corpus, pdf_root, out)."""
    corpus, pdf_root, out = tmp / "corpus", tmp / "pdfs", tmp / "out"
    for d in (corpus / name, pdf_root, out):
        d.mkdir(parents=True, exist_ok=True)
    n = max(max(pages), max(e[2] for e in toc)) + 1
    parts = [f"# {name}\n\nSource: `{pdf_root / (name + '.pdf')}`\n"]
    for p in range(1, n + 1):
        parts.append(f"<!-- page {p} -->")
        parts.append(pages.get(p, f"Body text of page {p}, enough words to count as text."))
    (corpus / name / "full_text.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    doc = fitz.open()
    for p in range(1, n + 1):
        doc.new_page().insert_text((72, 72), f"page {p}")
    doc.set_toc(toc)
    doc.save(str(pdf_root / f"{name}.pdf"))
    doc.close()
    if config is not None:
        (out / rs.CONFIG_NAME).write_text(json.dumps({name: config}), encoding="utf-8")
    return corpus, pdf_root, out


def resplit(corpus, pdf_root, out):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = rs.run(corpus, out, [str(pdf_root)])
    return code


def manifest(out, name):
    return json.loads((out / name / sm.MANIFEST_NAME).read_text(encoding="utf-8"))


def checks_of(r):
    return sorted({e["check"] for e in r["errors"]})


# --- choose_chapter_level -------------------------------------------------------------------

def test_choose_level():
    lvl, why = sm.choose_chapter_level([(1, "Chapter 1. Intro"), (2, "1.1. Overview"), (2, "1.2. More"),
                                        (2, "Glossary")])
    check("one `Chapter 1` entry → its level", lvl == 1 and "Chapter N" in why, (lvl, why))
    lvl, _ = sm.choose_chapter_level([(1, "Preface"), (2, "Chapter 4 Hip"), (2, "Chapter 5 Knee"),
                                      (3, "Overview")])
    check("two `Chapter N` entries → their level", lvl == 2, lvl)
    lvl, why = sm.choose_chapter_level([(1, "5 Stroke"), (2, "Overview")])
    check("one bare `5 Stroke` → no level (a number alone is not enough)", lvl is None, (lvl, why))
    lvl, why = sm.choose_chapter_level([(1, "Chapter 1 A"), (1, "2 B")])
    check("a level mixing `Chapter N` and bare numbers → no level", lvl is None, (lvl, why))
    lvl, why = sm.choose_chapter_level([(1, "Chapter 3 A"), (1, "Chapter 2 B")])
    check("two `Chapter N` going backwards → no level", lvl is None, (lvl, why))
    lvl, why = sm.choose_chapter_level([(1, "Chapter 1 A"), (2, "4 x"), (2, "2 y"), (2, "3 z")])
    check("a level with >=3 numbers going backwards blocks the fallback",
          lvl is None and "not increasing" in why, (lvl, why))
    lvl, _ = sm.choose_chapter_level([(1, "Part I"), (2, "Chapter 1 A"), (2, "Chapter 2 B"),
                                      (2, "Chapter 3 C"), (1, "Chapter 9 Appendix")])
    check("three or more numbers: unchanged (most numerous level)", lvl == 2, lvl)


# --- end to end: a one-chapter PDF needs no config --------------------------------------------

ONE_CH = {1: "CHAPTER 1\nAN INTRODUCTION\nWhat this chapter covers.",
          3: "OVERVIEW OF ANATOMY\nText about anatomy.",
          5: "HOMEOSTASIS\nText about feedback loops."}
ONE_TOC = [[1, "Chapter 1. An Introduction", 1], [2, "1.1. Overview of Anatomy", 3],
           [2, "1.2. Homeostasis", 5]]


def test_single_chapter_pdf():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdfs, out = make_book(tmp, "Excerpt", ONE_CH, ONE_TOC)
        code = resplit(corpus, pdfs, out)
        m = manifest(out, "Excerpt")
        r = sm.validate_book(out / "Excerpt")
        check("single chapter: resplit exit 0", code == 0, code)
        check("single chapter: chapter level 1 found without config", m["chapter_level"] == 1, m["chapter_level"])
        body = [f for f in m["files"] if f["kind"] == "body"]
        check("single chapter: every body file is chapter 1",
              body and all(f["chapter_number"] == 1 for f in body), [(f["file"], f["chapter_number"]) for f in body])
        check("single chapter: validator passes", r["ok"], checks_of(r))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_two_chapter_pdf():
    tmp = Path(tempfile.mkdtemp())
    try:
        pages = {1: "CHAPTER 4\nHIP\nHip text.", 3: "CHAPTER 5\nKNEE\nKnee text."}
        toc = [[1, "Chapter 4 Hip", 1], [1, "Chapter 5 Knee", 3]]
        corpus, pdfs, out = make_book(tmp, "Two", pages, toc)
        resplit(corpus, pdfs, out)
        m = manifest(out, "Two")
        r = sm.validate_book(out / "Two")
        nums = [f["chapter_number"] for f in m["files"] if f["kind"] == "body"]
        check("two chapters: numbered 4, 5 without config", nums == [4, 5], nums)
        check("two chapters: validator passes", r["ok"], checks_of(r))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- the validator: numbers lost vs no numbers at all ----------------------------------------

LETTER = {1: "7. SEVEN\nText.", 3: "8A. EIGHT A\nText.", 5: "8B. EIGHT B\nText.", 7: "9. NINE\nText."}
LETTER_TOC = [[1, "7. Seven", 1], [1, "8a. Eight A", 3], [1, "8b. Eight B", 5], [1, "9. Nine", 7]]


def test_numbers_lost_fails():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = {"chapter_regex": r"^(\d+)[a-z]?\b", "reason": "test: letter-suffixed chapters"}
        corpus, pdfs, out = make_book(tmp, "Lost", LETTER, LETTER_TOC, cfg)
        resplit(corpus, pdfs, out)
        m = manifest(out, "Lost")
        r = sm.validate_book(out / "Lost")
        nums = [f["chapter_number"] for f in m["files"]]
        check("lost: 7,8,8,9 → every number dropped (the Hopkins case)", all(n is None for n in nums), nums)
        check("lost: manifest says numbers were expected",
              "chapter_regex" in (m.get("chapter_numbers_expected") or ""), m.get("chapter_numbers_expected"))
        check("lost: validator FAILS with chapter_numbers_missing",
              not r["ok"] and "chapter_numbers_missing" in checks_of(r), checks_of(r))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unnumbered_book_passes():
    tmp = Path(tempfile.mkdtemp())
    try:
        pages = {1: "INTRODUCTION\nText.", 3: "METHODS\nText.", 5: "RECOMMENDATIONS\nText."}
        toc = [[1, "Introduction", 1], [1, "Methods", 3], [1, "Recommendations", 5]]
        corpus, pdfs, out = make_book(tmp, "Guideline", pages, toc)
        resplit(corpus, pdfs, out)
        m = manifest(out, "Guideline")
        r = sm.validate_book(out / "Guideline")
        check("unnumbered: no chapter level, nothing expected",
              m["chapter_level"] is None and "chapter_numbers_expected" not in m)
        check("unnumbered: validator passes", r["ok"], checks_of(r))

        # same book, manifest claiming a chosen chapter level → numbers were expected
        m2 = dict(m, chapter_level=1)
        r2 = sm.validate_book(out / "Guideline", manifest=m2)
        check("validator: chapter level chosen but no numbers → chapter_numbers_missing",
              "chapter_numbers_missing" in checks_of(r2), checks_of(r2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_choose_level, test_single_chapter_pdf, test_two_chapter_pdf, test_numbers_lost_fails,
              test_unnumbered_book_passes):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} raised", False, repr(e))
    bad = [r for r in results if not r[1]]
    for name, ok, detail in results:
        if not ok:
            print(f"FAIL {name}: {detail}")
    print(f"{len(results) - len(bad)}/{len(results)} pass")
    sys.exit(1 if bad else 0)
