"""test_corpus_handoff.py — the corpus hand-off to vault-search (openspec change corpus-handoff):
`--resplit` marks its output root with corpus.json (t2n-corpus/1), and `post_convert.py --index`
hands the corpus to `<VAULT_SEARCH_DIR>/server/corpus.py add` instead of calling an indexer CLI.
Synthetic corpora, synthetic PDFs and a fake corpus.py only.

  python converter/test_corpus_handoff.py   → exit 0 all pass · exit 1 any FAIL
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
import resplit as rs  # noqa: E402
import post_convert as pc  # noqa: E402
from test_resplit import GOOD_TOC, build, make_full_text, make_pdf, run, tree_state  # noqa: E402

results = []
MARKER = {"format": "t2n-corpus/1", "kind": "textbook"}


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def run_err(corpus, out, roots, **kw):
    """resplit.run with stdout and stderr captured."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, log = run(corpus, out, roots, **kw)
    return code, log, err.getvalue()


# --- corpus.json from --resplit ---------------------------------------------------------------

def test_marker_written():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()}},
                                      {"BookA": GOOD_TOC})
        code, log, err = run_err(corpus, out, [pdf_root])
        p = out / "corpus.json"
        check("fresh out: exit 0", code == 0, log)
        check("fresh out: corpus.json written", p.exists())
        check("fresh out: exact marker", p.exists() and json.loads(p.read_text(encoding="utf-8")) == MARKER)
        check("fresh out: nothing on stderr about it", "corpus.json" not in err, err)
        # same content, other whitespace: untouched, silent, also with --force
        p.write_text('{"kind":"textbook","format":"t2n-corpus/1"}', encoding="utf-8")
        before = (p.read_bytes(), p.stat().st_mtime_ns)
        code, log, err = run_err(corpus, out, [pdf_root], force=True)
        check("same marker: bytes+mtime unchanged under --force",
              (p.read_bytes(), p.stat().st_mtime_ns) == before)
        check("same marker: silent", "corpus.json" not in err + log, err)
        # --book limits the run, marker still written
        out2 = tmp / "out2"
        code, log, err = run_err(corpus, out2, [pdf_root], books=["BookA"])
        check("--book run: marker written",
              json.loads((out2 / "corpus.json").read_text(encoding="utf-8")) == MARKER)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_marker_never_overwritten():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()}},
                                      {"BookA": GOOD_TOC})
        for label, content in (("other kind", '{"format": "t2n-corpus/1", "kind": "lecture"}'),
                               ("other format", '{"format": "t2n-corpus/2", "kind": "textbook"}'),
                               ("not json", "hello")):
            if out.exists():
                shutil.rmtree(out)
            out.mkdir()
            (out / "corpus.json").write_text(content, encoding="utf-8")
            raw = (out / "corpus.json").read_bytes()
            code, log, err = run_err(corpus, out, [pdf_root])
            check(f"{label}: bytes unchanged", (out / "corpus.json").read_bytes() == raw)
            lines = [ln for ln in err.splitlines() if ln.startswith("[resplit] corpus.json")]
            check(f"{label}: one warning line naming the file",
                  len(lines) == 1 and str(out / "corpus.json") in lines[0] and "left as is" in lines[0], err)
            check(f"{label}: exit code still 0", code == 0, log)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_source_marker_not_copied():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()}},
                                      {"BookA": GOOD_TOC})
        (corpus / "corpus.json").write_text('{"format": "x", "kind": "lecture"}', encoding="utf-8")
        (corpus / "aliases.json").write_text("{}", encoding="utf-8")
        for force in (False, True):
            if out.exists():
                shutil.rmtree(out)
            run_err(corpus, out, [pdf_root], force=force)
            check(f"source corpus.json not copied (force={force})",
                  json.loads((out / "corpus.json").read_text(encoding="utf-8")) == MARKER)
            check(f"other root files still copied (force={force})", (out / "aliases.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_byte_identical_otherwise():
    """Every file other than corpus.json (and the timing-bearing run report) is the same as a
    run with the marker step switched off."""
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text(), "notes/n.md": "k"},
                                            "BookB": {"ch01_x.md": "<!-- page 1 -->\n\nhi"}},
                                      {"BookA": GOOD_TOC})
        run_err(corpus, out, [pdf_root])
        out_off = tmp / "out_off"
        real = rs.ensure_corpus_marker
        rs.ensure_corpus_marker = lambda o: None
        try:
            run_err(corpus, out_off, [pdf_root])
        finally:
            rs.ensure_corpus_marker = real

        def files(root):
            return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                    if p.is_file() and p.name not in ("corpus.json", rs.REPORT_NAME)}
        a, b = files(out), files(out_off)
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        check("byte-identical apart from corpus.json", not diff and len(a) > 3, diff)
        check("marker step off → no corpus.json", not (out_off / "corpus.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_guideline_layout():
    """`_Guidelines/<Name>/full_text.md` (what `OUTPUT_DIR=<corpus>/_Guidelines convert.py
    --batch-dir` produces) resplits into `<out>/_Guidelines/<Name>/` with one manifest at
    `<out>/_Guidelines/_manifest.json` — the layout the docs describe."""
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"_Guidelines": {"GuideX/full_text.md": make_full_text(),
                                                            "GuideY/full_text.md": make_full_text()},
                                            "BookA": {"full_text.md": make_full_text()}},
                                      {"BookA": GOOD_TOC, "GuideX": GOOD_TOC, "GuideY": GOOD_TOC})
        code, log, err = run_err(corpus, out, [pdf_root])
        check("guidelines: exit 0", code == 0, log)
        for g in ("GuideX", "GuideY"):
            d = out / "_Guidelines" / g
            check(f"guidelines: {g} split into chapter files",
                  d.is_dir() and len([p for p in d.glob("ch*.md")]) >= 3, sorted(os.listdir(d)) if d.is_dir() else d)
        m = out / "_Guidelines" / "_manifest.json"
        check("guidelines: one manifest for _Guidelines", m.exists())
        if m.exists():
            files = [f["file"] for f in json.loads(m.read_text(encoding="utf-8"))["files"]]
            check("guidelines: manifest paths are <Name>/<file>",
                  files and all(f.split("/")[0] in ("GuideX", "GuideY") for f in files), files[:4])
        check("guidelines: book beside them", (out / "BookA" / "_manifest.json").exists())
        check("guidelines: root marker", (out / "corpus.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- post_convert.py --index --------------------------------------------------------------

@contextlib.contextmanager
def configured(indexer=None, vsd=None, vspy=None):
    saved = (pc.INDEXER_SCRIPT, pc.VAULT_SEARCH_DIR, pc.VAULT_SEARCH_PY, pc.subprocess.run)
    calls = []

    class Done:
        def __init__(self, rc):
            self.returncode = rc

    rc_box = {"rc": 0}

    def fake_run(argv, *a, **kw):
        calls.append([str(x) for x in argv])
        return Done(rc_box["rc"])
    pc.INDEXER_SCRIPT = Path(indexer) if indexer else None
    pc.VAULT_SEARCH_DIR = Path(vsd) if vsd else None
    pc.VAULT_SEARCH_PY = Path(vspy) if vspy else None
    pc.subprocess.run = fake_run
    try:
        yield calls, rc_box
    finally:
        pc.INDEXER_SCRIPT, pc.VAULT_SEARCH_DIR, pc.VAULT_SEARCH_PY, pc.subprocess.run = saved


def captured(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        r = fn(*a)
    return r, out.getvalue(), err.getvalue()


def test_index_routes():
    tmp = Path(tempfile.mkdtemp())
    try:
        root = tmp / "root"
        root.mkdir()
        vs = tmp / "vs"
        (vs / "server").mkdir(parents=True)
        (vs / "server" / "corpus.py").write_text("", encoding="utf-8")
        legacy = tmp / "indexer.py"
        legacy.write_text("", encoding="utf-8")
        had_lancedb = "lancedb" in sys.modules

        # route 2: vault-search checkout
        with configured(vsd=vs) as (calls, rc):
            r, out, err = captured(pc.run_indexer, root)
            check("vs: one subprocess, corpus.py add <root>",
                  calls == [[sys.executable, str(vs / "server" / "corpus.py"), "add", str(root)]], calls)
            check("vs: exit code 0 passed through", r == 0)
            rc["rc"] = 3
            r, out, err = captured(pc.run_indexer, root)
            check("vs: exit code 3 passed through", r == 3)
            check("vs: no --incremental/--book", all("--incremental" not in c and "--book" not in c
                                                     for c in calls), calls)
            calls.clear()
            r, out, err = captured(pc.audit_index_coverage, True)
            check("vs: audit skipped, says it is part of corpus.py add",
                  "[skip]" in out and "corpus.py add" in out and not calls, out)
        with configured(vsd=vs, vspy=tmp / "vsenv" / "python.exe") as (calls, rc):
            captured(pc.run_indexer, root)
            check("vs: VAULT_SEARCH_PY used", calls and calls[0][0] == str(tmp / "vsenv" / "python.exe"), calls)

        # route 1: legacy INDEXER_SCRIPT wins even when the checkout is configured
        with configured(indexer=legacy, vsd=vs) as (calls, rc):
            r, out, err = captured(pc.run_indexer, root)
            check("legacy: deprecation warning on stderr",
                  "INDEXER_SCRIPT is deprecated" in err and "corpus.py add" in err, err)
            check("legacy: one call, --incremental, corpus.py not run",
                  calls == [[sys.executable, str(legacy), "--incremental"]], calls)
        with configured(indexer=tmp / "missing.py") as (calls, rc):
            r, out, err = captured(pc.run_indexer, root)
            check("legacy missing path: [skip], no call, still warns",
                  "[skip]" in out and not calls and "deprecated" in err, (out, err))

        # route 3: nothing configured
        with configured() as (calls, rc):
            r, out, err = captured(pc.run_indexer, root)
            check("none: no subprocess, exit 0", not calls and r == 0, calls)
            check("none, root not a corpus: [skip] + --resplit then corpus.py add <CORPUS_ROOT>",
                  "[skip]" in out and f'--resplit "{root}" --out "<CORPUS_ROOT>"' in out
                  and 'python "<path/to/vault-search>/server/corpus.py" add "<CORPUS_ROOT>"' in out, out)
            (root / "corpus.json").write_text(json.dumps(MARKER), encoding="utf-8")
            r, out, err = captured(pc.run_indexer, root)
            check("none, root is a corpus: [skip] + exact command, no --resplit",
                  "[skip]" in out and f'python "<path/to/vault-search>/server/corpus.py" add "{root}"' in out
                  and "--resplit" not in out and not calls and r == 0, out)

        # VAULT_SEARCH_DIR that is an old index dir, not a checkout
        old = tmp / "cache"
        old.mkdir()
        with configured(vsd=old) as (calls, rc):
            r, out, err = captured(pc.run_indexer, root)
            check("not a checkout: [skip], path, 'vault-search checkout', no call, exit 0",
                  "[skip]" in out and str(old) in out and "vault-search checkout" in out
                  and not calls and r == 0, out)
        if not had_lancedb:
            check("routes 2/3 never import lancedb", "lancedb" not in sys.modules)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_index_cli_exit_code():
    """The real CLI: env-configured, a fake corpus.py that records argv and exits 3."""
    tmp = Path(tempfile.mkdtemp())
    try:
        vs, root, log = tmp / "vs", tmp / "root", tmp / "argv.json"
        (vs / "server").mkdir(parents=True)
        root.mkdir()
        (vs / "server" / "corpus.py").write_text(
            "import json, sys\n"
            f"open(r'{log}', 'a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "sys.exit(3)\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("INDEXER_SCRIPT", "VAULT_SEARCH_PY")}
        env["VAULT_SEARCH_DIR"] = str(vs)
        # never let the resplit chain touch the repo's own ./output or ./corpus
        env["OUTPUT_DIR"] = str(tmp / "output")
        env["CORPUS_DIR"] = str(root)
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run([sys.executable, "-B", str(HERE / "post_convert.py"), "--index", "--corpus", str(root)],
                           env=env, capture_output=True, text=True, encoding="utf-8")
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        check("cli: corpus.py ran once with add <root>", lines == [json.dumps(["add", str(root)])], lines)
        check("cli: exit code 3 from corpus.py", p.returncode == 3, (p.returncode, p.stdout, p.stderr))
        env.pop("VAULT_SEARCH_DIR")
        p = subprocess.run([sys.executable, "-B", str(HERE / "post_convert.py"), "--index", "--corpus", str(root)],
                           env=env, capture_output=True, text=True, encoding="utf-8")
        check("cli: unconfigured → exit 0 with the command", p.returncode == 0 and "corpus.py" in p.stdout,
              (p.returncode, p.stdout, p.stderr))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_marker_written, test_marker_never_overwritten, test_source_marker_not_copied,
              test_byte_identical_otherwise, test_guideline_layout, test_index_routes,
              test_index_cli_exit_code):
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
