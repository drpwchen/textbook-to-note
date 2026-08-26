"""test_review_queue.py — regression guard for the out-of-band table-review trigger
(T2N_REVIEW_QUEUE). Same shape as test_table_fixes.py: check()/skip(), a printed report,
exit 0 (all pass/skip) / exit 1 (any FAIL). Fixtures are tiny synthetic PDFs built with fitz.

Covers:
  * the pure detector (dose tokens, continuation marker, geometric continuation, reason
    composition, marker wording);
  * the kill-switch default (OFF);
  * integration through convert_pdf: markers appear ONLY with the flag on, the flag OFF is
    byte-identical to the flag unset, and turning it on ADDS a marker without changing the table.

  python converter/test_review_queue.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert as cv  # noqa: E402
import review_queue as rq  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, "PASS" if cond else "FAIL", detail))


PAGE_W, PAGE_H = 595.0, 842.0
tmp = Path(tempfile.mkdtemp(prefix="t2n_review_"))


def draw_grid(page, x0, y0, col_ws, row_h, rows):
    xs = [x0]
    for w in col_ws:
        xs.append(xs[-1] + w)
    ys = [y0 + r * row_h for r in range(len(rows) + 1)]
    for x in xs:
        page.draw_line((x, y0), (x, ys[-1]), width=0.8)
    for y in ys:
        page.draw_line((x0, y), (xs[-1], y), width=0.8)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((xs[c] + 3, ys[r] + row_h - 5), str(cell), fontsize=8)


def convert(path, out_name, env=None):
    old = {}
    for k, v in (env or {}).items():
        old[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        out = str(path) + f".{out_name}.md"
        stats = cv.convert_pdf(str(path), out, "test")
        return Path(out).read_text(encoding="utf-8"), stats
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


# ── unit: dose-token counting ────────────────────────────────────────────────
check("dose: counts mass/volume/threshold units",
      rq.dose_token_count("10 mg IV, 2-4 mg/kg, 0.5 mL, 30 mEq/L") == 4,
      f"n={rq.dose_token_count('10 mg IV, 2-4 mg/kg, 0.5 mL, 30 mEq/L')}")
check("dose: 'mg' inside a word is not a token",
      rq.dose_token_count("smgat program 5mg") == 1,  # only the real '5mg'
      f"n={rq.dose_token_count('smgat program 5mg')}")
check("dose: prose with no numeric doses = 0",
      rq.dose_token_count("Antihistamines showed no benefit in otitis media.") == 0)

# ── unit: continuation marker ────────────────────────────────────────────────
check("cont: ', Continued' caption fires", rq.page_has_continuation_marker("TABLE 40.3 ..., Continued"))
check("cont: '(Continued)' fires", rq.page_has_continuation_marker("... (Continued)"))
check("cont: plain page does not fire", not rq.page_has_continuation_marker("ordinary body prose here"))

# ── unit: reason composition ─────────────────────────────────────────────────
r_both = rq.review_reasons("| Drug | 10 mg | 2 mg/kg |", "Table 5, Continued")
check("reasons: dose+continuation → two reasons", len(r_both) == 2, str(r_both))
check("reasons: dose only", rq.review_reasons("| Drug | 10 mg | 2 mg/kg |", "plain") ==
      ["dosage/threshold values (a value on the wrong row = wrong clinical data)"])
check("reasons: continuation only (no doses in table)",
      rq.review_reasons("| A | B |\n| x | y |", "..., Continued")[0].startswith("continuation-page"))
check("reasons: stitched_continuation forces the continuation reason without a text marker",
      any(x.startswith("continuation-page") for x in rq.review_reasons("| A | B |", "plain", stitched_continuation=True)))
check("reasons: geometric_continuation flags without any caption, and says so",
      rq.review_reasons("| A | B |", "plain", geometric_continuation=True) ==
      ["continuation-page by geometry, no continuation caption "
       "(orphan first row can be fused into the wrong label)"])
check("reasons: a printed caption wins over the geometric wording (one reason, not two)",
      rq.review_reasons("| A | B |", "..., Continued", geometric_continuation=True) ==
      ["continuation-page (orphan first row can be fused into the wrong label)"])
check("reasons: neither trigger → empty", rq.review_reasons("| A | B |", "plain text") == [])

# ── unit: the geometric continuation predicate ───────────────────────────────
def _geom(top, bottom, cols=2, xedges=(40.0, 180.0, 340.0)):
    return {"bbox": (40.0, top, 340.0, bottom), "page_w": PAGE_W, "page_h": PAGE_H,
            "col_count": cols, "xedges": list(xedges)}


_tail = _geom(680.0, 780.0)          # runs to the bottom of the page
_head = _geom(60.0, 200.0)           # resumes at the top of the next
check("geom: bottom-then-top with matching columns is a continuation",
      cv.geometric_continuation(_tail, _head) is True)
check("geom: a table that does not reach the bottom is not continued",
      cv.geometric_continuation(_geom(100.0, 300.0), _head) is False)
check("geom: a table starting mid-page does not continue anything",
      cv.geometric_continuation(_tail, _geom(400.0, 600.0)) is False)
check("geom: column-count mismatch is not a continuation",
      cv.geometric_continuation(_tail, _geom(60.0, 200.0, cols=3)) is False)
check("geom: shifted column edges are not a continuation",
      cv.geometric_continuation(_tail, _geom(60.0, 200.0, xedges=(120.0, 260.0, 420.0))) is False)
check("geom: no previous table → False", cv.geometric_continuation(None, _head) is False)

# ── unit: marker wording + kill-switch default ───────────────────────────────
mk = rq.format_review_marker(524, ["continuation-page", "dosage/threshold"])
check("marker: is an HTML comment naming the page and pointing at the review doc",
      mk.startswith("<!--") and mk.endswith("-->") and "page 524" in mk and "docs/table-review.md" in mk)
check("killswitch: default OFF", rq.review_queue_enabled() is False)
os.environ["T2N_REVIEW_QUEUE"] = "1"
check("killswitch: =1 turns it on", rq.review_queue_enabled() is True)
os.environ.pop("T2N_REVIEW_QUEUE", None)

# ── integration: a real dose table through convert_pdf ───────────────────────
p = tmp / "dose_table.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
pg.insert_text((40, 40), "TABLE 1 Analgesic Doses", fontsize=9)   # caption → passes the table gate
draw_grid(pg, 40, 60, [140, 160], 22,
          [["Drug", "Dose"], ["Morphine", "10 mg IV"], ["Ketorolac", "30 mg IV"],
           ["Gabapentin", "300 mg/d"]])
doc.save(str(p)); doc.close()

md_default, st_default = convert(p, "default")                    # flag unset
md_off, st_off = convert(p, "off", {"T2N_REVIEW_QUEUE": "0"})     # flag explicitly off
md_on, st_on = convert(p, "on", {"T2N_REVIEW_QUEUE": "1"})        # flag on

check("integration: default (unset) emits NO review marker",
      "needs out-of-band review" not in md_default and st_default["review_flagged"] == 0)
check("integration: OFF is byte-identical to unset", md_off == md_default)
check("integration: ON emits the review marker for the dose table",
      "needs out-of-band review" in md_on and st_on["review_flagged"] >= 1,
      f"review_flagged={st_on['review_flagged']}")
check("integration: ON reason is 'dosage/threshold' (no continuation marker on this page)",
      "dosage/threshold" in md_on and "continuation-page" not in md_on)
def _drop_markers(s):
    return "\n".join(l for l in s.splitlines() if "needs out-of-band review" not in l)
check("integration: ON only ADDS the marker — dropping the marker lines restores the OFF output",
      _drop_markers(md_on) == _drop_markers(md_off))
check("integration: the review queue is recorded in stats for a manifest",
      len(st_on["review_queue"]) >= 1 and st_on["review_queue"][0][0] == 1,
      f"queue={st_on['review_queue']}")

# continuation trigger even when the table itself has no dose tokens
p2 = tmp / "cont_table.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
pg.insert_text((40, 40), "TABLE 2 Findings, Continued", fontsize=9)
draw_grid(pg, 40, 60, [140, 160], 22,
          [["Finding", "Result"], ["Alpha", "present"], ["Beta", "absent"]])
doc.save(str(p2)); doc.close()
md2_on, st2_on = convert(p2, "on", {"T2N_REVIEW_QUEUE": "1"})
check("integration: a continuation page with no doses still enters the queue",
      "continuation-page" in md2_on and st2_on["review_flagged"] >= 1,
      f"review_flagged={st2_on['review_flagged']}")

# ── integration: issue #14 — a categorical table broken across a page break with no
#    "(continued)" caption anywhere and no dose token. Before the geometric signal this was
#    flagged by nothing, and its rows came out shifted relative to their labels.
p3 = tmp / "silent_continuation.pdf"
doc = fitz.open()
pg1 = doc.new_page(width=PAGE_W, height=PAGE_H)
pg1.insert_text((40, 40), "TABLE 3 ASA Physical Status Classification", fontsize=9)
draw_grid(pg1, 40, 680, [140, 160], 22,
          [["Class", "Definition"], ["1", "Normal healthy patient"],
           ["2", "Mild systemic disease"]])
pg2 = doc.new_page(width=PAGE_W, height=PAGE_H)
draw_grid(pg2, 40, 60, [140, 160], 22,
          [["3", "Severe systemic disease"], ["4", "Constant threat to life"],
           ["5", "Moribund patient"]])
doc.save(str(p3)); doc.close()

md3_unset, st3_unset = convert(p3, "unset")
md3_on, st3_on = convert(p3, "on", {"T2N_REVIEW_QUEUE": "1"})
check("integration: a caption-less cross-page table is flagged by geometry",
      "continuation-page by geometry" in md3_on,
      f"review_flagged={st3_on['review_flagged']}")
check("integration: only the continuation page is flagged, not the first page",
      [pg for pg, _ in st3_on["review_queue"]] == [2],
      f"queue={st3_on['review_queue']}")
check("integration: the geometric flag changes nothing with the queue unset",
      "needs out-of-band review" not in md3_unset and st3_unset["review_flagged"] == 0)
check("integration: the geometric flag only ADDS a marker",
      _drop_markers(md3_on) == _drop_markers(md3_unset))

# the same page break, but the two tables are not the same table
p4 = tmp / "unrelated_neighbours.pdf"
doc = fitz.open()
pg1 = doc.new_page(width=PAGE_W, height=PAGE_H)
pg1.insert_text((40, 40), "TABLE 4 Three Columns", fontsize=9)
draw_grid(pg1, 40, 680, [100, 100, 100], 22,
          [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"]])
pg2 = doc.new_page(width=PAGE_W, height=PAGE_H)
pg2.insert_text((40, 40), "TABLE 5 Two Columns", fontsize=9)
draw_grid(pg2, 40, 60, [140, 160], 22,
          [["Finding", "Result"], ["Alpha", "present"], ["Beta", "absent"]])
doc.save(str(p4)); doc.close()

md4_on, st4_on = convert(p4, "on", {"T2N_REVIEW_QUEUE": "1"})
check("integration: two unrelated tables at a page boundary are not flagged",
      "continuation-page" not in md4_on, f"queue={st4_on['review_queue']}")

# ── report ───────────────────────────────────────────────────────────────────
npass = sum(1 for _, s, _ in results if s == "PASS")
nfail = sum(1 for _, s, _ in results if s == "FAIL")
for name, status, detail in results:
    mark = "[OK]" if status == "PASS" else "[XX]"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail and status == "FAIL" else ""))
print(f"--- {npass} pass, {nfail} fail ---")
sys.exit(1 if nfail else 0)
