"""test_surya_ocr.py — regression guard for the OCR rung: the adapter contract, the OCR
output QC gate, and reading order.

Same shape as the other test scripts here: check() results, a printed report, exit 0 (all
pass) / exit 1 (any FAIL).

**No GPU, no Surya install, no model download.** The engine is replaced by a fake adapter —
a tiny generated script that emits canned JSON Lines and is invoked exactly the way the real
one is (`<SURYA_VENV_PY> <SURYA_ADAPTER> p0001.png ...`). That makes this file, not a live
OCR run, the primary test of the contract in `converter/surya_adapter.py`'s docstring: page
alignment, bbox-driven reading order, and every way an adapter can misbehave (malformed
line, missing line, empty page, non-zero exit).

  python converter/test_surya_ocr.py   -> exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import convert as cv  # noqa: E402
import surya_adapter as sa  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, "PASS" if cond else "FAIL", detail))


def raises(fn, needle=""):
    try:
        fn()
    except Exception as e:
        return needle.lower() in str(e).lower()
    return False


tmp = Path(tempfile.mkdtemp(prefix="t2n_surya_"))
# The fake adapter uses no GPU: point the lease hook at an empty dir so a machine-wide GPU
# broker (paused, or busy with a real job) cannot make this test queue forever.
os.environ["T2N_GPU_LEASE_DIR"] = str(tmp / "no-gpu-lease")

# ── the fake adapter ────────────────────────────────────────────────────────
# Modes are chosen by T2N_FAKE_MODE so one script covers every failure shape. Page geometry
# is written for a 612 px wide render (US Letter at 72 dpi), which is what the tests ask
# surya_ocr_pdf for -- rendering the real 300 dpi would make this suite slow for no gain.
FAKE = tmp / "fake_adapter.py"
FAKE.write_text(r'''
import json, os, sys
mode = os.environ.get("T2N_FAKE_MODE", "good")
if mode == "fail":
    print("simulated engine crash", file=sys.stderr)
    sys.exit(1)
for n, path in enumerate(sys.argv[1:]):
    name = os.path.basename(path)
    if mode == "missing" and n % 2 == 1:
        continue                      # drop every other page entirely
    if mode == "good":
        # emitted bottom-first on purpose: the caller must sort by geometry, not by
        # the order the engine happened to produce
        blocks = [{"text": "BOTTOM " + name, "bbox": [100, 300, 500, 400]},
                  {"text": "TOP " + name, "bbox": [100, 100, 500, 200]}]
    elif mode == "twocol":
        blocks = [{"text": "R1", "bbox": [320, 90, 560, 190]},
                  {"text": "L1", "bbox": [50, 100, 290, 200]},
                  {"text": "L2", "bbox": [50, 300, 290, 400]},
                  {"text": "R2", "bbox": [320, 320, 560, 420]}]
    elif mode == "nobbox":
        blocks = [{"text": "ALPHA"}, {"text": "BETA"}]
    elif mode == "empty":
        blocks = []
    elif mode == "sparse":
        blocks = [{"text": "x", "bbox": [10, 10, 20, 20]}]
    else:
        blocks = [{"text": "TEXT " + name, "bbox": [100, 100, 500, 200]}]
    sys.stdout.write(json.dumps({"fixture": name, "blocks": blocks}) + "\n")
    if mode == "malformed":
        sys.stdout.write("{this is not json\n")
sys.exit(0)
''', encoding="utf-8")

PAGES = 6
PDF = tmp / "book.pdf"
_d = fitz.open()
for _ in range(PAGES):
    _d.new_page(width=612, height=792)
_d.save(str(PDF))
_d.close()


def run_ocr(mode, out_name, gate=False, **kw):
    """`gate=False` opens the QC thresholds so a test about contract mechanics is not also
    a test of the gate — the fake adapter emits a handful of characters per page, far below
    any sane real-book floor. `gate=True` leaves the shipped defaults in place."""
    os.environ["T2N_FAKE_MODE"] = mode
    if gate:
        os.environ.pop("T2N_OCR_EMPTY_PAGE_MAX", None)
        os.environ.pop("T2N_OCR_MIN_CHARS_PER_PAGE", None)
    else:
        os.environ["T2N_OCR_EMPTY_PAGE_MAX"] = "1.0"
        os.environ["T2N_OCR_MIN_CHARS_PER_PAGE"] = "0"
    out = tmp / f"{out_name}.md"
    return cv.surya_ocr_pdf(str(PDF), str(out), dpi=72, **kw), out


_old = (cv.SURYA_VENV_PY, cv.SURYA_ADAPTER)
cv.SURYA_VENV_PY = Path(sys.executable)
cv.SURYA_ADAPTER = FAKE

try:
    check("surya_available(): true once both paths exist", cv.surya_available() is True)

    # ── happy path ──────────────────────────────────────────────────────────
    stats, out = run_ocr("good", "good")
    md = out.read_text(encoding="utf-8")
    check("good: first line names the source PDF (provenance, as the fitz path writes it)",
          md.splitlines()[0] == f"Source: `{PDF}`", md.splitlines()[:2])
    check("good: one page marker per PDF page",
          all(f"<!-- page {i+1} -->" in md for i in range(PAGES)) and stats["pages"] == PAGES)
    check("good: blocks are ordered by geometry, not by emission order",
          md.index("TOP p0001.png") < md.index("BOTTOM p0001.png"))
    check("good: each page gets its OWN text (fixture->page mapping is not off by one)",
          "TOP p0004.png" in md.split("<!-- page 4 -->")[1].split("<!-- page 5 -->")[0])
    check("good: clean run reports no defects",
          stats["parse_failures"] == 0 and stats["missing_lines"] == 0
          and stats["empty_pages"] == 0 and stats["chars_total"] > 0,
          json.dumps({k: stats[k] for k in ("parse_failures", "missing_lines", "empty_pages")}))

    # ── two-column reading order ────────────────────────────────────────────
    stats, out = run_ocr("twocol", "twocol")
    page1 = out.read_text(encoding="utf-8").split("<!-- page 2 -->")[0]
    order = [ln for ln in page1.splitlines() if ln.strip() in ("L1", "L2", "R1", "R2")]
    check("twocol: columns are not interleaved (L1,L2 before R1,R2)",
          order == ["L1", "L2", "R1", "R2"], f"{order}")
    # The bare (y0, x0) sort this path used before would have produced R1,L1,L2,R2 --
    # assert that explicitly so the regression can't come back unnoticed.
    naive = [t for _, _, t in sorted([(90, 320, "R1"), (100, 50, "L1"),
                                      (300, 50, "L2"), (320, 320, "R2")])]
    check("twocol: the old naive sort really did interleave (the defect is real)",
          naive == ["R1", "L1", "L2", "R2"] and naive != order)

    os.environ["T2N_COLUMN_SORT"] = "0"
    try:
        stats, out = run_ocr("twocol", "twocol_off")
        page1 = out.read_text(encoding="utf-8").split("<!-- page 2 -->")[0]
        order_off = [ln for ln in page1.splitlines() if ln.strip() in ("L1", "L2", "R1", "R2")]
        check("twocol: T2N_COLUMN_SORT=0 restores the old (y0,x0) order",
              order_off == ["R1", "L1", "L2", "R2"], f"{order_off}")
    finally:
        del os.environ["T2N_COLUMN_SORT"]

    # ── adapter misbehaviour ────────────────────────────────────────────────
    stats, out = run_ocr("malformed", "malformed")
    check("malformed: unparseable lines are COUNTED, not silently skipped",
          stats["parse_failures"] == PAGES, f"parse_failures={stats['parse_failures']}")
    check("malformed: the valid lines still land, so one bad line does not lose a book",
          stats["empty_pages"] == 0 and "TEXT p0003.png" in out.read_text(encoding="utf-8"))

    stats, out = run_ocr("missing", "missing")
    check("missing: images with no output line are counted",
          stats["missing_lines"] == PAGES // 2, f"missing_lines={stats['missing_lines']}")
    check("missing: the dropped pages show up as empty pages too",
          stats["empty_pages"] == PAGES // 2)

    stats, out = run_ocr("nobbox", "nobbox")
    check("nobbox: text without a bbox is kept rather than dropped",
          "ALPHA" in out.read_text(encoding="utf-8"))

    check("fail: a non-zero adapter exit raises for the whole batch",
          raises(lambda: run_ocr("fail", "fail"), "surya batch failed"))

    # ── the QC gate ─────────────────────────────────────────────────────────
    check("empty: an all-empty book RAISES instead of reporting a tiny success",
          raises(lambda: run_ocr("empty", "empty", gate=True), "came back empty"))
    check("sparse: a book with almost no text per page RAISES",
          raises(lambda: run_ocr("sparse", "sparse", gate=True), "chars/page"))
    check("empty: the failing run leaves NO markdown behind to look converted",
          not (tmp / "empty.md").exists())

    stats, out = run_ocr("empty", "empty_allowed")   # gate opened via env
    check("thresholds: env vars can open the gate for a genuinely sparse book",
          stats["empty_pages"] == PAGES and out.exists())
finally:
    cv.SURYA_VENV_PY, cv.SURYA_ADAPTER = _old
    for _v in ("T2N_FAKE_MODE", "T2N_OCR_EMPTY_PAGE_MAX", "T2N_OCR_MIN_CHARS_PER_PAGE"):
        os.environ.pop(_v, None)

# ── ocr_quality_verdict, directly ───────────────────────────────────────────
check("verdict: a healthy book passes",
      cv.ocr_quality_verdict(100, 5, 100 * 1800) is None)
check("verdict: real calibration point A (689 pages, 5.4% empty, 1791 chars/pg) passes",
      cv.ocr_quality_verdict(689, 37, 689 * 1791) is None)
check("verdict: real calibration point B (788 pages, 0.3% empty, 2005 chars/pg) passes",
      cv.ocr_quality_verdict(788, 2, 788 * 2005) is None)
check("verdict: the 20 KB-instead-of-1.6 MB incident shape is caught",
      "chars/page" in (cv.ocr_quality_verdict(788, 40, 20 * 1024) or ""))
check("verdict: too many empty pages is caught",
      "empty" in (cv.ocr_quality_verdict(100, 40, 100 * 1800) or ""))
check("verdict: both problems are reported together, not just the first",
      len((cv.ocr_quality_verdict(100, 90, 100) or "").split(";")) == 2)
check("verdict: a zero-page book is not judged", cv.ocr_quality_verdict(0, 0, 0) is None)
check("verdict: thresholds are arguments, not hardcoded",
      cv.ocr_quality_verdict(100, 40, 100 * 1800, empty_page_max=0.5) is None)

# ── order_ocr_blocks, directly ──────────────────────────────────────────────
single = [(50, 300, 550, 400, "second"), (50, 100, 550, 200, "first")]
check("order: a single-column page falls back to top-to-bottom",
      cv.order_ocr_blocks(single, 612) == ["first", "second"])
check("order: no blocks -> no lines", cv.order_ocr_blocks([], 612) == [])
check("order: an unknown page width falls back rather than guessing a gutter",
      cv.order_ocr_blocks(single, 0) == ["first", "second"])
straddle = [(50, 100, 550, 150, "banner"), (50, 200, 290, 300, "L"),
            (320, 210, 560, 310, "R"), (50, 400, 290, 500, "L2"), (320, 410, 560, 510, "R2")]
check("order: a full-width banner separates bands instead of joining a column",
      cv.order_ocr_blocks(straddle, 612)[0] == "banner")

# ── adapter helpers (importable with no Surya installed) ────────────────────
check("html: <br> becomes a newline", sa.html_to_text("a<br>b") == "a\nb")
check("html: tags are stripped", sa.html_to_text("<p>hello <b>world</b></p>") == "hello world")
check("html: a table becomes pipe-separated rows",
      sa.html_to_text("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>")
      == "a | b\nc | d")
check("html: <math> keeps its inner text", sa.html_to_text("x <math>E=mc^2</math> y") == "x E=mc^2 y")
check("html: entities are decoded", sa.html_to_text("a &amp; b &lt;c&gt;") == "a & b <c>")
check("html: malformed markup degrades to text instead of raising",
      "hello" in sa.html_to_text("<p>hello <unclosed"))
check("html: empty input is empty output", sa.html_to_text("") == "")
check("html: blank runs are capped, not multiplied",
      "\n\n\n" not in sa.html_to_text("<p>a</p><p></p><p></p><p>b</p>"))
check("bbox: identity scale is a no-op", sa.scale_bbox([10, 20, 30, 40], 1.0) == [10, 20, 30, 40])
check("bbox: a downscaled bbox maps back to original pixels",
      sa.scale_bbox([100, 200, 300, 400], 2550 / 2048.0) == [125, 249, 374, 498])
check("bbox: reversed corners are normalized",
      sa.scale_bbox([30, 40, 10, 20], 1.0) == [10, 20, 30, 40])

# ── report ──────────────────────────────────────────────────────────────────
fails = [r for r in results if r[1] == "FAIL"]
for name, status, detail in results:
    mark = {"PASS": "OK", "FAIL": "XX"}[status]
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and status != "PASS" else ""))
print(f"--- {sum(r[1]=='PASS' for r in results)} pass, {len(fails)} fail ---")
sys.exit(1 if fails else 0)
