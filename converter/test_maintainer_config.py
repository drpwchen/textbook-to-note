"""test_maintainer_config.py — the hooks that let a library keep its settings and PDFs where it
likes (openspec change maintainer-config): T2N_CONFIG_DIR, books.json aliases and `~` paths,
PDF roots walked only when a lookup needs them, and the opt-in T2N_REQUIRE_SOURCE_PDF=1 that
fails a resplit with a fix instead of searching. Synthetic files only; no GPU, no network.

  python converter/test_maintainer_config.py   → exit 0 all pass · exit 1 any FAIL
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert  # noqa: E402
import post_convert as pc  # noqa: E402
import resplit as rs  # noqa: E402

results = []


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


def child_env(**kv):
    e = {k: v for k, v in os.environ.items()
         if k not in ("T2N_CONFIG_DIR", "BOOKS_DIR", "OUTPUT_DIR")}
    e.update(kv)
    e["PYTHONIOENCODING"] = "utf-8"
    return e


PROBE = ("import sys; sys.path[:0] = [{h!r}, {r!r}]; import convert, rename_books; "
         "print(convert.BOOKS_JSON); print(convert.ALREADY_CONVERTED_FILE); "
         "print(rename_books._SKIP_FILE)")


def test_config_dir():
    tmp = Path(tempfile.mkdtemp())
    try:
        books, cfg = tmp / "books", tmp / "cfg"
        books.mkdir()
        cfg.mkdir()
        code = PROBE.format(h=str(HERE), r=str(HERE.parent))
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                             encoding="utf-8", env=child_env(BOOKS_DIR=str(books), T2N_CONFIG_DIR=str(cfg)))
        lines = out.stdout.splitlines()
        check("config dir: books.json, already_converted.json, rename_skip.json read from T2N_CONFIG_DIR",
              lines == [str(cfg / "books.json"), str(cfg / "already_converted.json"), str(cfg / "rename_skip.json")],
              (lines, out.stderr[-300:]))
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                             encoding="utf-8", env=child_env(BOOKS_DIR=str(books)))
        lines = out.stdout.splitlines()
        check("config dir: unset → BOOKS_DIR (unchanged default)",
              lines == [str(books / "books.json"), str(books / "already_converted.json"), str(books / "rename_skip.json")],
              (lines, out.stderr[-300:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_book_aliases():
    tmp = Path(tempfile.mkdtemp())
    try:
        bj = tmp / "books.json"
        bj.write_text(json.dumps({
            "lien_basic": {"label": "Basic", "src": "~/no-such-basic", "out": str(tmp / "out" / "L"),
                           "prefix": "基礎_"},
            "lien_clinical": {"label": "Clinical", "src": str(tmp / "c"), "out": str(tmp / "out" / "L"),
                              "prefix": "臨床_"},
            "lien": ["lien_basic", "lien_clinical"],
            "broken": ["lien_basic", "nope"],
        }, ensure_ascii=False), encoding="utf-8")
        with patched(convert, BOOKS_JSON=bj):
            books = convert.load_books()
            aliases = convert.load_book_aliases()
        check("aliases: list entries are not books", sorted(books) == ["lien_basic", "lien_clinical"], sorted(books))
        check("aliases: ~ in src is expanded", books["lien_basic"]["src"] == Path.home() / "no-such-basic",
              books["lien_basic"]["src"])
        check("aliases: prefix kept", books["lien_clinical"]["prefix"] == "臨床_")
        check("aliases: exact alias → both books",
              convert.resolve_book_keys(books, "lien", aliases) == ["lien_basic", "lien_clinical"])
        check("aliases: substring match still works",
              convert.resolve_book_keys(books, "clin", aliases) == ["lien_clinical"])
        check("aliases: no aliases given → old substring behaviour",
              convert.resolve_book_keys(books, "lien") == ["lien_basic"])
        try:
            convert.resolve_book_keys(books, "broken", aliases)
            check("aliases: alias naming an unknown book raises", False)
        except ValueError as e:
            check("aliases: alias naming an unknown book raises", "nope" in str(e), str(e))

        # CLI: --batch <alias> reaches both books (no PDFs → nothing converted, both named)
        (tmp / "c").mkdir()
        cfg = tmp
        r = subprocess.run([sys.executable, "-B", str(HERE / "convert.py"), "--batch", "lien"],
                           capture_output=True, text=True, encoding="utf-8",
                           env=child_env(T2N_CONFIG_DIR=str(cfg), OUTPUT_DIR=str(tmp / "out"),
                                         BOOKS_DIR=str(tmp / "books")))
        check("aliases: --batch lien names both books",
              "Batch converting: Basic, Clinical" in r.stdout, (r.returncode, r.stdout[-400:], r.stderr[-400:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lazy_pdf_index():
    tmp = Path(tempfile.mkdtemp())
    try:
        root = tmp / "pdfs"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "Book.pdf").write_bytes(b"%PDF")
        (root / "Other.pdf").write_bytes(b"%PDF")
        walks = []
        real_walk = os.walk

        def counting_walk(top, *a, **kw):
            walks.append(str(top))
            return real_walk(top, *a, **kw)

        with patched(rs.os, walk=counting_walk):
            idx = rs.PdfIndex([root])
            check("lazy: constructing an index walks nothing", walks == [] and not idx.walked, walks)
            head = f"# B\n\nSource: `{root / 'sub' / 'Book.pdf'}`\n"
            pdf, how = idx.find("B", tmp / "B", head, None)
            check("lazy: existing Source PDF resolves without a walk",
                  pdf == root / "sub" / "Book.pdf" and how == "source_path" and walks == [], (pdf, how, walks))
            head = f"# B\n\nSource: `{tmp / 'gone' / 'Other.pdf'}`\n"
            pdf, how = idx.find("B", tmp / "B", head, None)
            check("lazy: missing Source PDF → root walked once, found by name",
                  pdf == root / "Other.pdf" and how == "source_name" and walks == [str(root)], (pdf, how, walks))
            idx.find("B", tmp / "B", head, None)
            check("lazy: a second lookup does not walk again", walks == [str(root)], walks)

            walks.clear()
            a, b = rs.PdfIndex([root]), rs.PdfIndex([tmp / "empty"])
            both = rs.PdfIndex.combine([a, b])
            check("lazy: combine walks nothing", walks == [] and both.roots == [root, tmp / "empty"], walks)
            check("lazy: combined lookup equals a direct index",
                  both.by_name == rs.PdfIndex([root, tmp / "empty"]).by_name)
            check("lazy: combined lookup walked each part once (plus the direct index)",
                  walks.count(str(root)) == 2, walks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_require_source_pdf():
    tmp = Path(tempfile.mkdtemp())
    try:
        src, corpus, roots_dir, lib = tmp / "md", tmp / "corpus", tmp / "roots", tmp / "lib"
        roots_dir.mkdir()
        lib.mkdir()
        (lib / "Present.pdf").write_bytes(b"%PDF")
        (roots_dir / "Moved.pdf").write_bytes(b"%PDF")
        heads = {
            "Gone": f"# Gone\n\nSource: `{tmp / 'old' / 'Gone.pdf'}`\n",
            "NoLine": "<!-- page 1 -->\ntext\n",
            "Moved": f"# Moved\n\nSource: `{tmp / 'old' / 'Moved.pdf'}`\n",
            "Present": f"# Present\n\nSource: `{lib / 'Present.pdf'}`\n",
        }
        for b, h in heads.items():
            (src / b).mkdir(parents=True)
            (src / b / "full_text.md").write_text(h, encoding="utf-8")
        runs = []

        def fake_run(corpus_, out, roots, books=None, force=False, pdf_index=None, **kw):
            runs.append(books[0])
            (Path(out) / books[0]).mkdir(parents=True, exist_ok=True)
            (Path(out) / books[0] / "_manifest.json").write_text("{}", encoding="utf-8")
            return 0

        plan = [(b, False) for b in heads]
        with patched(pc, OUTPUT_BASE=src, PDF_ROOTS=[roots_dir]), patched(rs, run=fake_run), \
                env(T2N_REQUIRE_SOURCE_PDF="1"):
            failed, log = quiet(pc.run_resplit, plan, corpus)
        check("require: Gone + NoLine fail, Moved + Present resplit",
              sorted(failed) == ["Gone", "NoLine"] and sorted(runs) == ["Moved", "Present"], (failed, runs))
        check("require: the missing PDF's path and a Fix are printed",
              f"source PDF not found: {tmp / 'old' / 'Gone.pdf'}" in log and "Fix: put the PDF back" in log
              and str(roots_dir) in log, log[-800:])
        check("require: a file with no Source line names the file and the fix",
              "NoLine/full_text.md has no `Source:` line. Fix:" in log, log[-800:])
        check("require: a failed book leaves the corpus alone",
              not (corpus / "Gone").exists() and not (corpus / "NoLine").exists())

        # every Source PDF present → the PDF_ROOTS index is never walked
        walks = []
        real_walk = os.walk

        def counting_walk(top, *a, **kw):
            walks.append(str(top))
            return real_walk(top, *a, **kw)
        runs.clear()
        shutil.rmtree(corpus, ignore_errors=True)
        with patched(pc, OUTPUT_BASE=src, PDF_ROOTS=[roots_dir]), patched(rs, run=fake_run), \
                patched(rs.os, walk=counting_walk), env(T2N_REQUIRE_SOURCE_PDF="1"):
            failed, _ = quiet(pc.run_resplit, [("Present", False)], corpus)
        check("require: all Source PDFs present → nothing walked", failed == [] and walks == [], (failed, walks))

        # unset → the old behaviour: every planned book goes to resplit
        runs.clear()
        shutil.rmtree(corpus, ignore_errors=True)
        with patched(pc, OUTPUT_BASE=src, PDF_ROOTS=[roots_dir]), patched(rs, run=fake_run), \
                env(T2N_REQUIRE_SOURCE_PDF=None):
            failed, _ = quiet(pc.run_resplit, plan, corpus)
        check("require: unset → no book is refused", failed == [] and sorted(runs) == sorted(heads), (failed, runs))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_config_dir, test_book_aliases, test_lazy_pdf_index, test_require_source_pdf):
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
