"""test_clear_stale_chapters.py — re-conversion removes the book's own stale split files
(openspec change force-clears-stale-chapters).

Each scenario runs batch_dir_convert on a temp OUTPUT_BASE with the expensive parts stubbed
(convert_pdf / surya_ocr_pdf / pandoc write a small full_text.md; the split is stubbed or real as
noted), then inspects the book directory.

  python converter/test_clear_stale_chapters.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert as cv  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


NEW_CH02 = "<!-- page 2 -->\nnew gait text\n"
OLD_CH02 = "old gait text"
UNRELATED = {
    "notes.md": "my notes",
    "Ch12_Hand.md": "capital C",
    "ch1.md": "one digit",
    "_manifest.json": "{}",
    "full_text_backup.md": "x",
}


def make_pdf(path: Path) -> None:
    doc = fitz.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "Body text " * 20, fontsize=11)
    doc.save(str(path))
    doc.close()


def fake_convert_pdf(pdf_path, out_md_path, label="", table_worker=None):
    Path(out_md_path).write_text("<!-- page 1 -->\nfront\n" + NEW_CH02, encoding="utf-8")
    return {"pages": 3, "tables": 0, "fig_refs": 0}


def fake_split(content, out_dir, total_pages, pdf_path=None):
    (Path(out_dir) / "ch02_Gait.md").write_text(NEW_CH02, encoding="utf-8")
    return [(2, 0, "Gait")]


def boom(*a, **k):
    raise RuntimeError("conversion failed")


def fake_pandoc(cmd, check=True, capture_output=True):
    out = Path(cmd[cmd.index("-o") + 1])
    body = "".join(f"# Chapter {n} Topic {n}\n\nText of chapter {n}.\n\n" for n in (2, 3, 4, 5, 6))
    out.write_text(body, encoding="utf-8")


@contextlib.contextmanager
def patched(out_base: Path, env=None, **stubs):
    names = ["OUTPUT_BASE", "PROGRESS_FILE", "load_already_converted", "is_scan_only",
             "detect_fitz_silent_failure"] + list(stubs)
    saved = {n: getattr(cv, n) for n in names}
    saved_which, saved_run = cv.shutil.which, cv.subprocess.run
    saved_env = os.environ.get("T2N_CLEAR_STALE_CHAPTERS")
    cv.OUTPUT_BASE, cv.PROGRESS_FILE = out_base, out_base / "batch_progress.json"
    cv.load_already_converted = lambda: set()
    cv.is_scan_only = lambda p: False
    cv.detect_fitz_silent_failure = lambda p: False
    cv.shutil.which = lambda name: "pandoc"
    cv.subprocess.run = fake_pandoc
    for n, v in stubs.items():
        setattr(cv, n, v)
    if env is None:
        os.environ.pop("T2N_CLEAR_STALE_CHAPTERS", None)
    else:
        os.environ["T2N_CLEAR_STALE_CHAPTERS"] = env
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            yield log
    finally:
        for n, v in saved.items():
            setattr(cv, n, v)
        cv.shutil.which, cv.subprocess.run = saved_which, saved_run
        if saved_env is None:
            os.environ.pop("T2N_CLEAR_STALE_CHAPTERS", None)
        else:
            os.environ["T2N_CLEAR_STALE_CHAPTERS"] = saved_env


def setup_book(tmp: Path, *, suffix=".pdf", stale_full_text=False, fresh_full_text=False):
    """books/BookA<suffix> + out/BookA/ holding old split files and unrelated files."""
    src, out = tmp / "books", tmp / "out"
    src.mkdir()
    out.mkdir()
    pdf = src / f"BookA{suffix}"
    if suffix == ".pdf":
        make_pdf(pdf)
    else:
        pdf.write_bytes(b"not a real epub; pandoc is stubbed")
    book = out / "BookA"
    book.mkdir()
    (book / "full_text.md").write_text("old full text", encoding="utf-8")
    (book / "ch01_Part_I.md").write_bytes(b"")
    (book / "ch02_Gait.md").write_text(OLD_CH02, encoding="utf-8")
    (book / "ch12a_Extra.md").write_text("old", encoding="utf-8")
    (book / "ch00_Front_Matter.md").write_text("old front", encoding="utf-8")
    (book / "pages_0001-0030.md").write_text("old", encoding="utf-8")
    (book / "ch07.md").write_text("old", encoding="utf-8")
    for name, text in UNRELATED.items():
        (book / name).write_text(text, encoding="utf-8")
    (book / "figures").mkdir()
    (book / "figures" / "ch03_x.md").write_text("fig", encoding="utf-8")
    now = time.time()
    if stale_full_text:   # source newer than full_text.md
        os.utime(book / "full_text.md", (now - 3600, now - 3600))
        os.utime(pdf, (now, now))
    if fresh_full_text:   # full_text.md newer than source → book skipped
        os.utime(pdf, (now - 3600, now - 3600))
        os.utime(book / "full_text.md", (now, now))
    return src, out, book


def snapshot(book: Path):
    return {str(p.relative_to(book)): p.read_bytes() for p in sorted(book.rglob("*")) if p.is_file()}


OLD_SPLIT = ["ch00_Front_Matter.md", "ch01_Part_I.md", "ch02_Gait.md", "ch07.md",
             "ch12a_Extra.md", "pages_0001-0030.md"]


def unrelated_intact(book: Path) -> bool:
    ok = all((book / n).read_text(encoding="utf-8") == t for n, t in UNRELATED.items())
    return ok and (book / "figures" / "ch03_x.md").read_text(encoding="utf-8") == "fig"


# --- Scenario: a chapter the new split no longer produces disappears (--force, fitz path) ---
with tempfile.TemporaryDirectory() as td:
    src, out, book = setup_book(Path(td))
    with patched(out, convert_pdf=fake_convert_pdf, split_into_chapters=fake_split) as log:
        cv.batch_dir_convert(src, force=True)
    names = sorted(p.name for p in book.glob("*.md"))
    check("force: ch01_Part_I.md removed", not (book / "ch01_Part_I.md").exists())
    check("force: all old split files gone except the rewritten ch02",
          [n for n in OLD_SPLIT if (book / n).exists()] == ["ch02_Gait.md"], names)
    check("force: ch02_Gait.md has the new bytes",
          (book / "ch02_Gait.md").read_text(encoding="utf-8") == NEW_CH02)
    check("force: unrelated files + figures/ intact", unrelated_intact(book))
    check("force: full_text.md is the new one",
          (book / "full_text.md").read_text(encoding="utf-8").endswith(NEW_CH02))
    clear_lines = [l for l in log.getvalue().splitlines() if "[clear]" in l]
    check("force: exactly one log line, naming the removed files",
          len(clear_lines) == 1 and "ch01_Part_I.md" in clear_lines[0]
          and "pages_0001-0030.md" in clear_lines[0] and "notes.md" not in clear_lines[0],
          clear_lines)

# --- Scenario: source-newer re-conversion (no --force) clears too ---
with tempfile.TemporaryDirectory() as td:
    src, out, book = setup_book(Path(td), stale_full_text=True)
    with patched(out, convert_pdf=fake_convert_pdf, split_into_chapters=fake_split) as log:
        cv.batch_dir_convert(src, force=False)
    check("mtime: ch01_Part_I.md removed", not (book / "ch01_Part_I.md").exists())
    check("mtime: log names it", "ch01_Part_I.md" in log.getvalue())
    check("mtime: unrelated intact", unrelated_intact(book))

# --- Scenario: EPUB re-conversion clears (real split_epub_by_headings, pandoc stubbed) ---
with tempfile.TemporaryDirectory() as td:
    src, out, book = setup_book(Path(td), suffix=".epub", stale_full_text=True)
    with patched(out) as log:
        cv.batch_dir_convert(src, force=False)
    names = sorted(p.name for p in book.glob("ch*.md"))
    check("epub: stale ch01_Part_I.md / ch12a / pages_ removed",
          not any((book / n).exists() for n in ("ch01_Part_I.md", "ch12a_Extra.md",
                                                "pages_0001-0030.md", "ch07.md")), names)
    check("epub: new split written (ch02..ch06)",
          [n[:5] for n in names if n.startswith("ch") and n != "ch1.md"]
          == ["ch02_", "ch03_", "ch04_", "ch05_", "ch06_"], names)
    check("epub: log names ch01_Part_I.md", "ch01_Part_I.md" in log.getvalue())
    check("epub: unrelated intact", unrelated_intact(book))

# --- Scenario: Surya path clears too ---
def fake_surya(pdf_path, out_md_path):
    fake_convert_pdf(pdf_path, out_md_path)
    return {"pages": 3, "size_kb": 1, "empty_pages": 0, "chars_total": 30,
            "parse_failures": 0, "missing_lines": 0}


with tempfile.TemporaryDirectory() as td:
    src, out, book = setup_book(Path(td))
    with patched(out, surya_available=lambda: True, surya_ocr_pdf=fake_surya,
                 split_into_chapters=fake_split):
        cv.batch_dir_convert(src, force=True, force_surya=True)
    check("surya: ch01_Part_I.md removed", not (book / "ch01_Part_I.md").exists())
    check("surya: ch02 new bytes", (book / "ch02_Gait.md").read_text(encoding="utf-8") == NEW_CH02)
    check("surya: unrelated intact", unrelated_intact(book))

# --- Scenario: failed conversion keeps the old chapters (fitz, surya, epub) ---
for label, kwargs, stubs, suffix in [
    ("fitz", {"force": True}, {"convert_pdf": boom}, ".pdf"),
    ("surya", {"force": True, "force_surya": True},
     {"surya_available": lambda: True, "surya_ocr_pdf": boom}, ".pdf"),
    ("epub", {"force": True}, {"recover_epub_headings": boom, "strip_epub_noise": boom}, ".epub"),
]:
    with tempfile.TemporaryDirectory() as td:
        src, out, book = setup_book(Path(td), suffix=suffix)
        before = {n: b for n, b in snapshot(book).items() if n in OLD_SPLIT}
        with patched(out, **stubs):
            cv.batch_dir_convert(src, **kwargs)
        after = {n: b for n, b in snapshot(book).items() if n in OLD_SPLIT}
        check(f"failure ({label}): every old split file still there, same bytes",
              before == after and len(after) == len(OLD_SPLIT), sorted(after))

# --- Scenario: kill switch → the pre-change listing (stale file stays) ---
with tempfile.TemporaryDirectory() as td:
    src, out, book = setup_book(Path(td))
    with patched(out, env="0", convert_pdf=fake_convert_pdf, split_into_chapters=fake_split) as log:
        cv.batch_dir_convert(src, force=True)
    after = snapshot(book)
    check("kill switch: every old split file still present",
          all(n in after for n in OLD_SPLIT), sorted(after))
    check("kill switch: ch02 rewritten as before the change",
          (book / "ch02_Gait.md").read_text(encoding="utf-8") == NEW_CH02)
    check("kill switch: no [clear] line", "[clear]" not in log.getvalue())

# --- Negative: a skipped book (full_text.md newer than the PDF) is not cleared ---
with tempfile.TemporaryDirectory() as td:
    src, out, book = setup_book(Path(td), fresh_full_text=True)
    before = snapshot(book)
    with patched(out, convert_pdf=boom, split_into_chapters=boom):
        cv.batch_dir_convert(src, force=False)
    check("skipped book: directory untouched", snapshot(book) == before)

# --- Negative: a directory that is not directly under OUTPUT_BASE is never cleared ---
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    base, outside = td / "out", td / "elsewhere"
    nested = base / "BookA" / "sub"
    for d in (base, outside, nested):
        d.mkdir(parents=True)
    for d in (outside, nested):
        (d / "ch01_X.md").write_text("keep", encoding="utf-8")
    with patched(base) as log:
        r1 = cv.clear_stale_split_files(outside)
        r2 = cv.clear_stale_split_files(nested)
        r3 = cv.clear_stale_split_files(base / "BookA" / ".." / ".." / "elsewhere")
    check("containment: nothing removed outside OUTPUT_BASE/<book>",
          r1 == r2 == r3 == [] and (outside / "ch01_X.md").exists() and (nested / "ch01_X.md").exists())
    check("containment: warning printed", log.getvalue().count("[warn]") == 3, log.getvalue())

# --- Coverage: every split file the real splitters write matches the clear patterns ---
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    written = []
    # pattern split + front matter, odd titles
    content = "<!-- page 1 -->\nPreface text\n" + "".join(
        f"<!-- page {p} -->\nChapter {p - 1} Gait & Balance: (Part {p})—über\nbody {p}\n"
        for p in range(2, 7))
    d1 = td / "a"
    d1.mkdir()
    cv.split_into_chapters(content, d1, 6)
    written += [p.name for p in d1.iterdir()]
    # page-range fallback
    d2 = td / "b"
    d2.mkdir()
    cv._force_split("".join(f"<!-- page {p} -->\nx\n" for p in range(1, 70)), d2, 69)
    written += [p.name for p in d2.iterdir()]
    # EPUB split incl. lettered and title-less chapters
    d3 = td / "c"
    d3.mkdir()
    cv.split_epub_by_headings("intro\n# Chapter 1 A\nx\n# Chapter 12a — B\ny\n# Chapter 3\nz\n", d3, "L")
    written += [p.name for p in d3.iterdir()]
    unmatched = [n for n in written if not any(rx.match(n) for rx in cv._STALE_SPLIT_RES)]
    check("coverage: splitters wrote files", len(written) >= 8, written)
    check("coverage: every written split name matches a clear pattern", not unmatched, unmatched)


fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"  -- {detail}"))
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
