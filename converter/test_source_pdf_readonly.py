"""test_source_pdf_readonly.py — the converter never writes to an input PDF
(openspec change stop-pdf-bookmark-writeback).

Before this change, --batch-dir wrote the chapters it detected back into a bookmark-less source
PDF (incremental save). Each scenario snapshots sha256 / size / mtime of a synthetic
bookmark-less PDF whose text yields chapters, runs batch_dir_convert, and checks nothing moved.

  python converter/test_source_pdf_readonly.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert as cv  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


CHAPTERS = ["Chapter 1 Introduction", "Chapter 2 Assessment", "Chapter 3 Treatment"]


def make_pdf(path: Path) -> None:
    doc = fitz.open()
    for title in CHAPTERS:
        for k in range(2):
            page = doc.new_page()
            body = (title + "\n\n") if k == 0 else ""
            body += "Body text of the chapter for the converter to read. " * 12
            page.insert_textbox(fitz.Rect(50, 50, 550, 800), body, fontsize=11)
    doc.save(str(path))
    doc.close()


def snapshot(path: Path):
    st = path.stat()
    return (hashlib.sha256(path.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)


def run_batch(tmp: Path, *, force_surya=False, readonly=False):
    src = tmp / "books"
    src.mkdir()
    pdf = src / "Synthetic_Book.pdf"
    make_pdf(pdf)
    if readonly:
        os.chmod(pdf, stat.S_IREAD)
    before = snapshot(pdf)
    out = tmp / "out"
    out.mkdir()
    saved = (cv.OUTPUT_BASE, cv.PROGRESS_FILE, cv.load_already_converted)
    cv.OUTPUT_BASE, cv.PROGRESS_FILE = out, out / "batch_progress.json"
    cv.load_already_converted = lambda: set()
    try:
        cv.batch_dir_convert(src, force=True, force_surya=force_surya)
    finally:
        cv.OUTPUT_BASE, cv.PROGRESS_FILE, cv.load_already_converted = saved
        if readonly:
            os.chmod(pdf, stat.S_IREAD | stat.S_IWRITE)
    after = snapshot(pdf)
    doc = fitz.open(str(pdf))
    toc = doc.get_toc()
    doc.close()
    book = out / "Synthetic_Book"
    chapters = sorted(p.name for p in book.glob("ch*.md")) if book.exists() else []
    return before, after, toc, chapters


def fake_surya(pdf_path, out_md_path, *a, **k):
    lines = []
    for i, title in enumerate(CHAPTERS):
        lines += [f"<!-- page {2 * i + 1} -->", title, "OCR body text. " * 20,
                  f"<!-- page {2 * i + 2} -->", "OCR body text. " * 20]
    Path(out_md_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"pages": 6, "size_kb": 1, "empty_pages": 0, "chars_total": 6000,
            "parse_failures": 0, "missing_lines": 0}


def main() -> int:
    check("write-back helper is gone", not hasattr(cv, "add_pdf_bookmarks"))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        before, after, toc, chapters = run_batch(Path(d))
        check("fitz path: chapters were detected (precondition)", len(chapters) >= 2, chapters)
        check("fitz path: PDF sha256/size/mtime unchanged", before == after)
        check("fitz path: PDF still has no bookmarks", toc == [], toc)

    saved = (cv.surya_available, cv.surya_ocr_pdf)
    cv.surya_available, cv.surya_ocr_pdf = (lambda: True), fake_surya
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            before, after, toc, chapters = run_batch(Path(d), force_surya=True)
            check("OCR path: chapters were detected (precondition)", len(chapters) >= 2, chapters)
            check("OCR path: PDF sha256/size/mtime unchanged", before == after)
            check("OCR path: PDF still has no bookmarks", toc == [], toc)
    finally:
        cv.surya_available, cv.surya_ocr_pdf = saved

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        before, after, toc, chapters = run_batch(Path(d), readonly=True)
        check("read-only PDF: converts and splits", len(chapters) >= 2, chapters)
        check("read-only PDF: unchanged", before == after)

    fails = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    print(f"{len(results) - len(fails)} pass / {len(fails)} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
