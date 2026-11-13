"""test_private_upport.py — behaviours up-ported from the maintainer's private converter
(openspec change private-upport): a book that needs OCR without Surya fails the batch, the
opt-in measured OCR gutter, one warm Docling worker per batch run, the post_convert resplit
chain (plan, PDF roots, figure links, exit code), guideline-aware listing, and resplit not
copying batch bookkeeping. Synthetic PDFs / corpora only; no GPU, no network.

  python converter/test_private_upport.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert  # noqa: E402
import post_convert as pc  # noqa: E402
import resplit as rs  # noqa: E402
from test_resplit import GOOD_TOC, build, make_full_text, run  # noqa: E402

results = []
W = 2481.0  # 300 dpi A4 render width, the coordinate space an OCR adapter reports in


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


@contextlib.contextmanager
def patched(obj, **attrs):
    saved = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


@contextlib.contextmanager
def env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        r = fn(*a, **kw)
    return r, out.getvalue()


def scanned_pdf(path: Path):
    doc = fitz.open()
    doc.new_page()  # no text layer -> is_scan_only() is True
    doc.save(str(path))
    doc.close()


def digital_pdf(path: Path):
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 540, 760),
                            f"Page {i + 1}. " + "Born-digital body text for the test. " * 30)
    doc.save(str(path))
    doc.close()


# --- Surya unavailable: FAILED, not completed ------------------------------------------------

def test_surya_unavailable_fails_book():
    tmp = Path(tempfile.mkdtemp())
    try:
        src, out = tmp / "src", tmp / "out"
        src.mkdir()
        out.mkdir()
        scanned_pdf(src / "ScanBook.pdf")
        digital_pdf(src / "DigitalBook.pdf")
        with patched(convert, OUTPUT_BASE=out, PROGRESS_FILE=out / "batch_progress.json",
                     SURYA_VENV_PY=tmp / "venv" / "python.exe",
                     SURYA_ADAPTER=tmp / "gone" / "surya_adapter.py"):
            res, log = quiet(convert.batch_dir_convert, src)
            prog = convert.load_progress()
            check("no surya: one book failed", len(res["surya_failed"]) == 1, res)
            check("no surya: [FAIL] names SURYA_ADAPTER and SURYA_VENV_PY",
                  "[FAIL]" in log and "SURYA_ADAPTER" in log and "SURYA_VENV_PY" in log, log[-600:])
            check("no surya: scanned book not completed", "ScanBook.pdf" not in prog["completed"], prog)
            check("no surya: errors carry FAILED + name",
                  any("FAILED" in e and "ScanBook.pdf" in e for e in prog["errors"]), prog["errors"])
            check("no surya: nothing written for the failed book", not (out / "ScanBook").exists())
            check("no surya: digital book converted + completed",
                  "DigitalBook.pdf" in prog["completed"] and (out / "DigitalBook" / "full_text.md").exists(),
                  prog)
            res2, log2 = quiet(convert.batch_dir_convert, src)
            check("no surya: next run retries (FAILED again, not skipped)",
                  len(res2["surya_failed"]) == 1, log2[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_surya_unavailable_cli_exit_codes():
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "src"
        src.mkdir()
        scanned_pdf(src / "ScanBook.pdf")
        e = {k: v for k, v in os.environ.items() if k not in ("SURYA_VENV_PY", "SURYA_ADAPTER")}
        e.update(OUTPUT_DIR=str(tmp / "out"), PYTHONIOENCODING="utf-8",
                 SURYA_ADAPTER=str(tmp / "gone.py"), T2N_GPU_LEASE_DIR=str(tmp / "no-broker"))
        cmd = [sys.executable, "-B", str(HERE / "convert.py"), "--batch-dir", str(src)]
        p = subprocess.run(cmd, env=e, capture_output=True, text=True, encoding="utf-8")
        check("cli: scanned book without surya → exit 1", p.returncode == 1, (p.returncode, p.stdout[-400:]))
        (src / "ScanBook.pdf").unlink()
        digital_pdf(src / "DigitalBook.pdf")
        p = subprocess.run(cmd, env=e, capture_output=True, text=True, encoding="utf-8")
        check("cli: no book needs OCR → exit 0", p.returncode == 0, (p.returncode, p.stdout[-400:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- OCR gutter (opt-in) -------------------------------------------------------------------

def _line(x0, y, x1, text):
    return (float(x0), float(y), float(x1), float(y + 40), text)


def _two_col_off_center(gutter_frac=0.38):
    g = W * gutter_frac
    items = [_line(g + 120, 150, W - 120, "TITLE BANNER OVER THE RIGHT BOX")]
    for i in range(8):
        y = 300 + i * 60
        items.append(_line(80, y, g - 20, f"L{i} left column text line"))
        items.append(_line(g + 30, y, W - 100, f"R{i} right box text line that is quite long"))
    return items


def test_ocr_gutter():
    items = _two_col_off_center()
    gx = convert.find_ocr_gutter(items, W)
    check("gutter: off-center found within 0.02", gx is not None and abs(gx - W * 0.38) < W * 0.02, gx)
    with env(T2N_OCR_GUTTER="1", T2N_COLUMN_SORT=None):
        out = convert.order_ocr_blocks(items, W)
        lefts = [i for i, t in enumerate(out) if t.startswith("L")]
        rights = [i for i, t in enumerate(out) if t.startswith("R")]
        check("gutter on: left column before right", max(lefts) < min(rights), out)
        single = [_line(150, 200 + i * 60, W - 150 - (i % 3) * 400, f"prose line {i}") for i in range(20)]
        check("gutter on: single column → no gutter", convert.find_ocr_gutter(single, W) is None)
        check("gutter on: single column top-to-bottom",
              convert.order_ocr_blocks(single, W) == [f"prose line {i}" for i in range(20)])
        # figure-left page: label gaps must not win over the text gutter
        fig = [_line(W * 0.20, 100, W * 0.70, "PAGE BANNER TITLE")]
        labels = [(0.10, 0.18), (0.30, 0.42), (0.12, 0.27), (0.33, 0.45), (0.15, 0.20), (0.36, 0.44)]
        for i, (a, b) in enumerate(labels):
            fig.append(_line(W * a, 300 + i * 150, W * b, f"lab{i}"))
        for i in range(20):
            fig.append(_line(W * 0.51, 250 + i * 60, W * 0.95, f"T{i} body text of the right column"))
        g2 = convert.find_ocr_gutter(fig, W)
        check("gutter on: figure-left page gutter beside text", g2 is not None and W * 0.45 <= g2 <= W * 0.51,
              g2 / W if g2 else g2)
        o2 = convert.order_ocr_blocks(fig, W)
        body = [i for i, t in enumerate(o2) if t.startswith("T")]
        check("gutter on: body text contiguous", body == list(range(body[0], body[0] + 20)), o2)
    with env(T2N_OCR_GUTTER=None, T2N_COLUMN_SORT=None):
        base = convert.column_order_boxes(items, W, 0.0, min_items=convert.OCR_COLUMN_MIN_BLOCKS)
        want = [b[4] for b in base] if base is not None else \
            [b[4] for b in sorted(items, key=lambda b: (b[1], b[0]))]
        check("gutter off (default): page-center order, as 0.8.0", convert.order_ocr_blocks(items, W) == want)
    with env(T2N_OCR_GUTTER="1", T2N_COLUMN_SORT="0"):
        check("T2N_COLUMN_SORT=0 disables the gutter too",
              convert.order_ocr_blocks(items, W) == [b[4] for b in sorted(items, key=lambda b: (b[1], b[0]))])
    two = [_line(50, 100 + i * 50, 280, f"a{i}") for i in range(3)] + \
          [_line(320, 100 + i * 50, 560, f"b{i}") for i in range(3)]
    out = convert.column_order_boxes(two, 600.0)
    check("fitz path default still splits at page center",
          [b[4] for b in out] == ["a0", "a1", "a2", "b0", "b1", "b2"], out)


# --- one Docling worker per batch run ------------------------------------------------------

def test_shared_docling_worker():
    made, leases = [], []

    class FakeWorker:
        def __init__(self):
            made.append(self)

        def status(self):
            return {"failure_count": 0}

    @contextlib.contextmanager
    def fake_lease(name, **kw):
        leases.append(name)
        yield

    tmp = Path(tempfile.mkdtemp())
    try:
        src, out = tmp / "src", tmp / "out"
        src.mkdir()
        out.mkdir()
        digital_pdf(src / "A.pdf")
        digital_pdf(src / "B.pdf")
        seen = []

        def fake_convert_pdf(pdf, out_path, label, table_worker=None):
            seen.append(table_worker)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text("<!-- page 1 -->\n\nx\n", encoding="utf-8")
            return {"pages": 1, "tables": 0, "fig_refs": 0, "warnings": []}

        with patched(convert, DoclingTableWorker=FakeWorker, _gpu_lease_ctx=fake_lease,
                     _SHARED_TABLE_WORKER=None, convert_pdf=fake_convert_pdf, OUTPUT_BASE=out,
                     PROGRESS_FILE=out / "batch_progress.json"):
            with env(T2N_DOCLING="1"):
                quiet(convert.batch_dir_convert, src)
                check("docling on: one worker for two books", len(made) == 1, made)
                check("docling on: both books got that worker",
                      len(seen) == 2 and seen[0] is made[0] and seen[1] is made[0], seen)
                check("docling on: one t2n_docling lease", leases == ["t2n_docling"], leases)
            convert._SHARED_TABLE_WORKER = None
            seen.clear()
            with env(T2N_DOCLING=None):
                quiet(convert.batch_dir_convert, src, force=True)
                check("docling off: table_worker None, no new worker, no lease",
                      seen == [None, None] and len(made) == 1 and leases == ["t2n_docling"], (seen, leases))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- post_convert resplit chain -------------------------------------------------------------

def test_resplit_plan_and_roots():
    tmp = Path(tempfile.mkdtemp())
    try:
        src, corpus = tmp / "md", tmp / "corpus"
        for b in ("New", "Reconverted", "Current", "scripts"):
            (src / b).mkdir(parents=True)
            (src / b / "full_text.md").write_text("x", encoding="utf-8")
        (src / "Empty").mkdir()
        for b in ("Reconverted", "Current"):
            (corpus / b).mkdir(parents=True)
            (corpus / b / "_manifest.json").write_text("{}", encoding="utf-8")
        os.utime(src / "Reconverted" / "full_text.md", (2e9, 2e9))
        os.utime(corpus / "Current" / "_manifest.json", (2e9, 2e9))
        with patched(pc, OUTPUT_BASE=src):
            plan = pc.resplit_plan(corpus)
        check("plan: new plain, re-converted forced, rest skipped",
              plan == [("New", False), ("Reconverted", True)], plan)

        pdf = tmp / "pdfs" / "sub" / "Book.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF")
        (src / "Book").mkdir()
        (src / "Book" / "full_text.md").write_text(f"# Book\n\nSource: `{pdf}`\n", encoding="utf-8")
        fallback = tmp / "fallback"
        fallback.mkdir()
        with patched(pc, OUTPUT_BASE=src, PDF_ROOTS=[fallback, tmp / "missing"]):
            check("roots: the Source PDF's folder", pc.pdf_roots_for("Book") == [pdf.parent],
                  pc.pdf_roots_for("Book"))
            pdf.unlink()
            check("roots: Source gone → existing PDF_ROOTS", pc.pdf_roots_for("Book") == [fallback],
                  pc.pdf_roots_for("Book"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_figure_links():
    tmp = Path(tempfile.mkdtemp())
    try:
        src, corpus = tmp / "md", tmp / "corpus"
        for name in ("figures", "figures_Part_I", "figures.old.bak", "figures.x_undo"):
            (src / "Book" / name).mkdir(parents=True)
        (src / "Book" / "figures" / "a.png").write_bytes(b"x")
        (corpus / "Book").mkdir(parents=True)
        (src / "NotInCorpus" / "figures").mkdir(parents=True)
        with patched(pc, OUTPUT_BASE=src):
            failed, _ = quiet(pc.ensure_figure_links, corpus)
            check("links: none failed", failed == [], failed)
            check("links: exactly figures + figures_Part_I",
                  sorted(p.name for p in (corpus / "Book").iterdir()) == ["figures", "figures_Part_I"],
                  sorted(p.name for p in (corpus / "Book").iterdir()))
            check("links: file readable through the link", (corpus / "Book" / "figures" / "a.png").read_bytes() == b"x")
            check("links: not copied (it is a link)", pc._is_link(corpus / "Book" / "figures"))
            check("links: no folder for a book absent from the corpus", not (corpus / "NotInCorpus").exists())
            again, log = quiet(pc.ensure_figure_links, corpus)
            check("links: second run creates nothing, fails nothing", again == [] and "created 0" in log, log)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resplit_failure_exits_1():
    tmp = Path(tempfile.mkdtemp())
    try:
        src, corpus = tmp / "md", tmp / "corpus"
        (src / "Broken").mkdir(parents=True)
        (src / "Broken" / "full_text.md").write_text("x", encoding="utf-8")

        def boom(*a, **kw):
            raise RuntimeError("synthetic")
        with patched(pc, OUTPUT_BASE=src, CORPUS_DIR=corpus, INDEXER_SCRIPT=None), patched(rs, run=boom):
            argv = sys.argv
            sys.argv = ["post_convert.py", "--resplit-only"]
            code = None
            try:
                _, log = quiet(pc.main)
            except SystemExit as e:
                code = e.code
            finally:
                sys.argv = argv
        check("resplit failure: exit 1", code == 1, code)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_index_end_to_end():
    """--index on a real synthetic book: resplit into CORPUS_DIR, then corpus.py add <CORPUS_DIR>."""
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus_src, pdf_root, _ = build(tmp, {"BookA": {"full_text.md": make_full_text()}}, {"BookA": GOOD_TOC})
        (corpus_src / "BookA" / "figures").mkdir()
        vs, log = tmp / "vs", tmp / "argv.json"
        (vs / "server").mkdir(parents=True)
        (vs / "server" / "corpus.py").write_text(
            "import json, sys\n"
            f"open(r'{log}', 'a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n')\n",
            encoding="utf-8")
        cdir = tmp / "the-corpus"
        e = {k: v for k, v in os.environ.items() if k not in ("INDEXER_SCRIPT", "VAULT_SEARCH_PY")}
        e.update(OUTPUT_DIR=str(corpus_src), CORPUS_DIR=str(cdir), T2N_PDF_ROOTS=str(pdf_root),
                 VAULT_SEARCH_DIR=str(vs), PYTHONIOENCODING="utf-8")
        cmd = [sys.executable, "-B", str(HERE / "post_convert.py"), "--index"]
        p = subprocess.run(cmd, env=e, capture_output=True, text=True, encoding="utf-8")
        check("e2e: exit 0", p.returncode == 0, (p.returncode, p.stdout[-800:], p.stderr[-400:]))
        check("e2e: book resplit into CORPUS_DIR", (cdir / "BookA" / "_manifest.json").exists())
        check("e2e: corpus.json at CORPUS_DIR", (cdir / "corpus.json").exists())
        check("e2e: figures linked", pc._is_link(cdir / "BookA" / "figures"))
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        check("e2e: corpus.py add <CORPUS_DIR> once", lines == [json.dumps(["add", str(cdir)])], lines)
        stamp = (cdir / "BookA" / "_manifest.json").stat().st_mtime_ns
        p = subprocess.run(cmd, env=e, capture_output=True, text=True, encoding="utf-8")
        check("e2e: second run resplits nothing",
              (cdir / "BookA" / "_manifest.json").stat().st_mtime_ns == stamp and "already in the corpus" in p.stdout,
              p.stdout[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- guideline-aware listing + bookkeeping not copied ------------------------------------------

def test_listing_and_bookkeeping():
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "out"
        for d in ("BookOne", "_Guidelines/G1", "_Guidelines/G2", "_figure_remap", ".drafts", "scripts"):
            (out / d).mkdir(parents=True)
            (out / d / "full_text.md").write_text("<!-- page 1 -->\nword word\n", encoding="utf-8")
        with patched(pc, OUTPUT_BASE=out):
            quiet(pc.update_index_md)
        rows = [ln.split("|")[1].strip() for ln in (out / "INDEX.md").read_text(encoding="utf-8").splitlines()
                if ln.startswith("| ") and not ln.startswith("| Book |")]
        check("INDEX.md rows: BookOne + each guideline only", sorted(rows) == sorted(["BookOne", "_Guidelines/G1", "_Guidelines/G2"]), rows)

        (tmp / "rs").mkdir()
        corpus, pdf_root, o = build(tmp / "rs", {"_Guidelines": {"G1/full_text.md": make_full_text(),
                                                                 "batch_progress.json": "{}",
                                                                 "G1/conversion_summary.json": "{}"}},
                                    {"G1": GOOD_TOC})
        run(corpus, o, [pdf_root])
        check("resplit: _Guidelines/batch_progress.json not copied",
              not (o / "_Guidelines" / "batch_progress.json").exists())
        check("resplit: nested conversion_summary.json not copied",
              not (o / "_Guidelines" / "G1" / "conversion_summary.json").exists())
        check("resplit: guideline still split", (o / "_Guidelines" / "_manifest.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_surya_unavailable_fails_book, test_surya_unavailable_cli_exit_codes, test_ocr_gutter,
              test_shared_docling_worker, test_resplit_plan_and_roots, test_figure_links,
              test_resplit_failure_exits_1, test_index_end_to_end, test_listing_and_bookkeeping):
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
