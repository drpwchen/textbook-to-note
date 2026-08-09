"""test_table_fixes.py — regression guard for the three table-correctness fixes:

  * T2N_TABLE_FRAME_REJECT — discard page-frame pseudo-tables (1-column blobs produced by page
    decoration rectangles), which otherwise ship a real multi-column table collapsed into one
    column, or ordinary prose, as citable-looking markdown.
  * T2N_BOOK_TABLE_CHECK  — warn loudly when a whole book yields no tables at all.
  * page-error counting   — a page that raises during the table pass is counted and reported
    instead of being swallowed by a bare `except Exception`.

Same shape as test_table_merge.py: check()/skip() results, a printed report, exit 0 (all pass/skip)
/ exit 1 (any FAIL). Fixtures are tiny synthetic PDFs built with fitz, so the suite is
self-contained — no real textbook needed.

  python converter/test_table_fixes.py   → exit 0 all pass/skip · exit 1 any FAIL
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so `shared.config` (repo-root/shared) resolves
sys.path.insert(0, str(HERE))
import convert as cv  # noqa: E402

results = []  # (name, "PASS"|"FAIL"|"SKIP", detail)


def check(name, cond, detail=""):
    results.append((name, "PASS" if cond else "FAIL", detail))


def skip(name, detail=""):
    results.append((name, "SKIP", detail))


PAGE_W, PAGE_H = 595.0, 842.0
tmp = Path(tempfile.mkdtemp(prefix="t2n_fixes_"))

LOREM = ("Antihistamine and decongestant combinations showed no significant benefit in treating "
         "otitis media with effusion, and no additional studies have been published since to "
         "change this recommendation. Adverse effects include insomnia and hyperactivity. ")


def draw_grid(page, x0, y0, col_ws, row_h, rows):
    """Draw a ruled grid table and fill its cells (top-origin points)."""
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


def add_page_frame(page):
    """The page decoration that fools pdfplumber: a full-page content frame plus the rule under the
    running header. Together they give 3 horizontal and 2 vertical intersecting edges, so
    find_tables() returns ONE table whose bbox is the page body, with rows=2 and columns=1 — the
    exact shape measured on the real pages (wp3-borderless-report.md §5b)."""
    page.draw_rect(fitz.Rect(25.3, 30.0, 570.0, 750.5), width=0.7)  # full-page content frame
    page.draw_line((25.3, 49.1), (570.0, 49.1), width=0.7)          # rule under the running header


def convert(path, out_name, env=None):
    """Convert with a temporary env overlay; returns (markdown, stats)."""
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
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── case 1: page-frame pseudo-table over 2-column prose is rejected ──────────
p1 = tmp / "case1_page_frame.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
add_page_frame(pg)
pg.insert_text((40, 33), "OTITIS MEDIA WITH EFFUSION", fontsize=8)
# A cross-reference, not a caption — this is how a table-free prose page passes the table gate in
# the real corpus (the gate's first branch is a text test for the word "Table"; report §1).
pg.insert_textbox(fitz.Rect(40, 60, 290, 740),
                  "Evidence is summarized in Table 3.1. " + LOREM * 6, fontsize=9)  # left column
pg.insert_textbox(fitz.Rect(310, 60, 570, 740), LOREM * 6, fontsize=9)              # right column
doc.save(str(p1))
doc.close()

md1, st1 = convert(p1, "on")
check("case1: page-frame pseudo-table is not emitted as a table",
      md1.count("**[Table on page") == 0, f"blocks={md1.count('**[Table on page')}")
check("case1: rejection leaves a visible trace comment (loss is never silent)",
      "pseudo-table rejected on page 1" in md1)
check("case1: rejection is counted in stats",
      st1["rejected_tables"] >= 1, f"rejected={st1['rejected_tables']}")
check("case1: the page prose itself survives the rejection",
      "Antihistamine and decongestant" in md1.replace("\n", " "))

md1_off, st1_off = convert(p1, "off", {"T2N_TABLE_FRAME_REJECT": "0"})
check("case1: kill-switch restores the old behavior (pseudo-table comes back)",
      md1_off.count("**[Table on page") == 1 and st1_off["rejected_tables"] == 0,
      f"blocks={md1_off.count('**[Table on page')}")

# ── case 2: a genuine multi-column table is NOT rejected ─────────────────────
TABLE_ROWS = [
    ["Test", "Sensitivity", "Specificity"],
    ["Neer sign", "88.7%", "30.5%"],
    ["Hawkins test", "91.7%", "44.3%"],
    ["Full can test", "77.0%", "74.0%"],
    ["Drop arm test", "26.9%", "88.4%"],
]
p2 = tmp / "case2_real_table.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
draw_grid(pg, 60, 120, [130, 130, 130], 18, TABLE_ROWS)
doc.save(str(p2))
doc.close()

md2, st2 = convert(p2, "on")
check("case2: genuine multi-column table is kept",
      md2.count("**[Table on page") == 1, f"blocks={md2.count('**[Table on page')}")
check("case2: its column bindings are intact",
      "| Neer sign | 88.7% | 30.5% |" in md2)
check("case2: nothing rejected on a healthy table page",
      st2["rejected_tables"] == 0, f"rejected={st2['rejected_tables']}")

# ── case 2b: page frame AND a real table on the same page ───────────────────
# The decisive case: the frame must be dropped while the real table survives untouched, proving
# the predicate keys on column count rather than on the presence of page decoration.
p2b = tmp / "case2b_frame_plus_table.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
add_page_frame(pg)
draw_grid(pg, 60, 120, [130, 130, 130], 18, TABLE_ROWS)
doc.save(str(p2b))
doc.close()

md2b, st2b = convert(p2b, "on")
check("case2b: the framed page yields exactly one table — the real one",
      md2b.count("**[Table on page") == 1, f"blocks={md2b.count('**[Table on page')}")
check("case2b: real table's column bindings survive alongside a rejected frame",
      "| Neer sign | 88.7% | 30.5% |" in md2b and st2b["rejected_tables"] == 1,
      f"rejected={st2b['rejected_tables']}")
md2b_off, st2b_off = convert(p2b, "off", {"T2N_TABLE_FRAME_REJECT": "0"})
check("case2b: with the fix off, the frame pseudo-table pollutes the page (the bug being fixed)",
      md2b_off.count("**[Table on page") == 2, f"blocks={md2b_off.count('**[Table on page')}")

# ── case 3: genuine 1-column boxed list — locked-in behavior ─────────────────
# A 1-column markdown table encodes no column bindings, and the box text is re-emitted verbatim in
# the page prose, so a LONG boxed list is dropped (its content is not lost) while a SHORT one, which
# cannot be a page-body dump, is left alone. This is the deliberate trade, pinned here.
p3 = tmp / "case3_boxed_list.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
pg.draw_rect(fitz.Rect(60, 100, 520, 700), width=0.8)
pg.draw_line((60, 130), (520, 130), width=0.8)  # rule under the box's own title band
pg.insert_text((66, 122), "TABLE 9-5  Indications for Stopping an Exercise Stress Test", fontsize=9)
pg.insert_textbox(fitz.Rect(66, 136, 514, 694),
                  "Absolute contraindications to exercise testing. " + LOREM * 4, fontsize=9)
doc.save(str(p3))
doc.close()

md3, st3 = convert(p3, "on")
check("case3: long 1-column boxed list is dropped, with a trace",
      md3.count("**[Table on page") == 0 and "pseudo-table rejected" in md3,
      f"blocks={md3.count('**[Table on page')}")
check("case3: its text is still present in the page prose (no content loss)",
      "Absolute contraindications to exercise testing" in md3.replace("\n", " "))

p3b = tmp / "case3b_short_box.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
draw_grid(pg, 60, 300, [200], 18, [["Red flags"], ["Fever"], ["Weight loss"], ["Night pain"]])
doc.save(str(p3b))
doc.close()

md3b, st3b = convert(p3b, "on")
check("case3b: short 1-column list (no page-body-sized cell, small bbox) is kept",
      st3b["rejected_tables"] == 0, f"rejected={st3b['rejected_tables']}")

# ── case 4: whole-book detector fires when pdfplumber sees 0 pages ───────────
# Reproduces the Zasler/Lanken/NSCA mode: fitz opens the file fine, pdfminer's page-tree walk
# returns nothing, and every table in the book is lost with no error at all.
p4 = tmp / "case4_zero_plumber.pdf"
doc = fitz.open()
for _ in range(3):
    pg = doc.new_page(width=PAGE_W, height=PAGE_H)
    pg.insert_textbox(fitz.Rect(60, 100, 520, 700),
                      "TABLE 4.1 Pretest likelihood of ischemic heart disease\n" * 6, fontsize=9)
doc.save(str(p4))
doc.close()


class _NoPages:
    pages: list = []

    def close(self):
        pass


real_open = cv.pdfplumber.open
cv.pdfplumber.open = lambda *a, **k: _NoPages()
try:
    md4, st4 = convert(p4, "on")
finally:
    cv.pdfplumber.open = real_open

check("case4: detector fires when pdfplumber parses 0 pages but fitz opens the book",
      any("parsed 0 pages" in w for w in st4["warnings"]), f"warnings={st4['warnings']}")
check("case4: the warning is written into the markdown, not just the console",
      "[!warning] Conversion warnings" in md4)
check("case4: per-page failures are counted, not swallowed",
      st4["page_errors"] == 3, f"page_errors={st4['page_errors']}")

# ── case 5: detector does NOT fire on a healthy table-bearing book ───────────
md5, st5 = convert(p2, "healthy")
check("case5: no warning on a book that extracts tables fine",
      st5["warnings"] == [] and "[!warning]" not in md5, f"warnings={st5['warnings']}")
check("case5: no spurious page errors on a healthy book",
      st5["page_errors"] == 0, f"page_errors={st5['page_errors']}")

# ── case 6: caption-based detector needs BOTH zero tables and enough captions ─
p6 = tmp / "case6_captions_no_tables.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
pg.insert_textbox(fitz.Rect(60, 60, 520, 780),
                  "\n".join(f"TABLE 3.{i} Borderless summary of findings" for i in range(1, 15)),
                  fontsize=9)
doc.save(str(p6))
doc.close()

md6, st6 = convert(p6, "on")
check("case6: >=10 captions with 0 tables extracted raises the book-level warning",
      any("0 tables extracted despite" in w for w in st6["warnings"]),
      f"captions={st6['table_captions']} warnings={st6['warnings']}")

md6_off, st6_off = convert(p6, "off", {"T2N_BOOK_TABLE_CHECK": "0"})
check("case6: kill-switch silences the book-level detector",
      st6_off["warnings"] == [] and "[!warning]" not in md6_off, f"warnings={st6_off['warnings']}")

# ── case 7: running-header furniture pseudo-table (T2N_TABLE_FURNITURE_REJECT) ─
# The second fabricated-table family (faketable-rootcause.md BUG 2). Page furniture — a running
# header, a shaded chapter bar, an e-reader nav bar — is drawn as stacked/nested rectangles that
# intersect, so find_tables() returns a small table lying entirely inside the top margin band. It
# escapes page_frame_reject_reason() by construction: it is not a 1-column page dump (measured
# column counts are 2-3, bbox ~4-8% of the page).
def add_nav_bar(doc, page):
    """The e-reader furniture that fools pdfplumber, using the exact rectangle set measured on
    Steffens_APA-Textbook-Geriatric-Psychiatry_6e_2022 p200 (a PDF-XChange capture of a web
    reader; its sticky "< Back" bar is baked into all 1,128 pages).

    Do NOT rewrite this with page.draw_rect() — it cannot reproduce the bug, and that costs an
    afternoon to rediscover. Two reasons, both measured:
      1. fitz emits draw_rect() as a STROKED PATH, which pdfplumber classifies as a `curve`, not
         a `rect` (verified: rects=0, curves=2, edges=10, tables=0 at every stroke width tried).
         The real file uses filled `re` operators, so the fixture appends a raw content stream.
      2. The two nav-bar rectangles are NESTED, and nested rectangles never intersect, so on
         their own they yield no table at all (verified: rects=2, edges=8, tables=0). The one
         vertical that closes the grid comes from the TALL VIEWPORT RECT, whose left edge at
         x=567 runs the height of the page and cuts through the header band. It must be present.

    With all of it: rects=5, edges=20, tables=2, header table ncols=3 / content_cols=2 / rows=2 —
    the real Steffens output reproduced exactly."""
    H = float(page.rect.height)

    def re_(x0, t0, x1, t1):            # top-origin rect -> PDF bottom-origin `re f`
        return "%f %f %f %f re f " % (x0, H - t1, x1 - x0, t1 - t0)

    stream = ("q 1 1 1 rg "
              + re_(567.0, 26.9, 1105.5, 812.2)    # viewport rect, drawn twice, extends off-page
              + re_(567.0, 26.9, 1105.5, 812.2)    # its left edge at x=567 is the only vertical
              + re_(23.3, 21.7, 572.2, 62.3)       # nav-bar outer band
              + re_(28.5, 26.9, 567.0, 56.9)       # nav-bar inner bar (nested inside the outer)
              + re_(23.3, 807.7, 572.2, 818.8)     # footer band
              + "Q\n").encode()
    xref = page.get_contents()[0]
    doc.update_stream(xref, doc.xref_stream(xref) + stream)


p7 = tmp / "case7_nav_bar.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
# Body prose starts INSIDE the top band, exactly as in Steffens: the nav bar is an opaque overlay
# sitting on top of live text, so the pseudo-table harvests real prose rather than furniture text.
pg.insert_textbox(fitz.Rect(39.8, 26.0, 556.0, 800.0),
                  "Delirium assessments are described in Table 2. " + LOREM * 8, fontsize=9)
pg.insert_text((55.7, 48.0), "Back", fontsize=9)
add_nav_bar(doc, pg)
doc.save(str(p7))
doc.close()

md7, st7 = convert(p7, "on")
check("case7: running-header furniture pseudo-table is not emitted as a table",
      md7.count("**[Table on page") == 0, f"blocks={md7.count('**[Table on page')}")
check("case7: rejection leaves a visible trace comment naming the band",
      "running-header band only" in md7)
check("case7: rejection is counted in stats",
      st7["rejected_tables"] >= 1, f"rejected={st7['rejected_tables']}")
check("case7: the body prose the nav bar overlaid survives the rejection",
      "Antihistamine and decongestant" in md7.replace("\n", " "))

md7_off, st7_off = convert(p7, "off", {"T2N_TABLE_FURNITURE_REJECT": "0"})
check("case7: kill-switch restores the old behavior (furniture pseudo-table comes back)",
      md7_off.count("**[Table on page") == 1 and "running-header band only" not in md7_off,
      f"blocks={md7_off.count('**[Table on page')}")

# ── case 8: a genuine table reaching into the page body is NOT furniture ─────
# The guard that matters: the predicate fires on geometry alone, so a real table must survive as
# long as it reaches out of the band. Starts at y=40 (inside the 84.2pt band) and ends at y=160.
p8 = tmp / "case8_table_from_top.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
draw_grid(pg, 60, 40, [130, 130, 130], 24, TABLE_ROWS)
doc.save(str(p8))
doc.close()

md8, st8 = convert(p8, "on")
check("case8: a genuine table starting in the band but reaching the body is kept",
      md8.count("**[Table on page") == 1, f"blocks={md8.count('**[Table on page')}")
check("case8: its column bindings are intact",
      "| Neer sign | 88.7% | 30.5% |" in md8)

# ── case 9: KNOWN LIMITATION — a real table lying ENTIRELY in the band is dropped ──
# Not a passing guard: this pins a real false positive so it fails loudly if anyone changes it.
# A genuine 3-column table with intact bindings, short enough to fit inside the top 84.2pt band
# (3 rows at 16pt, bottom at 8.6% of page height), IS rejected as furniture. Geometry alone
# cannot tell it from a running header, which is the documented cost of the geometry-only rule.
#
# The cost was then MEASURED (faketable-rootcause.md, "BUG 2 follow-up"): 44 books / 1,980 random
# pages / 31 furniture-band hits, every hit rendered or read. 30 were genuine furniture; exactly
# ONE was a real table — and it cost zero characters, because render_page_text() had already
# emitted every word of it in the page prose above. So the limitation this case pins is real but
# structural, not content loss.
#
# Three narrowing signals were tested against that measurement and ALL THREE FAIL — do not
# re-propose them without new data:
#   * content_cols >= 3 — the real false positive has 2, exactly like the Steffens pseudo-tables.
#     (This synthetic fixture's 3 columns are NOT representative; that is why it stays a fixture.)
#   * row count — every hit in the sample, real and fake alike, is rows=2.
#   * character volume — the real false positive is 90 chars, inside the furniture range (6-285).
# Content-BEARING rows do separate ordinary running heads (1) from a real fragment (2) — but
# Steffens is also 2, so that guard resurrects the exact defect the rule exists for.
#
# The realistic shape is NOT a continuation-table tail. Measured over 1,263 consecutive pages in
# the 8 most table-rich books, 20 real cross-page continuations were found and NONE lay inside the
# band: the closest tail bottom sits at 0.135 of page height, clear of the 0.10 band by 3.5pp.
# The one real false positive was a continuation-table HEADER — the repeated column header plus a
# section row of a "Table 4.5 › continued" table, with the data rows below it never resolved as a
# table at all. Note the interaction with the merge feature (currently default-OFF):
# _gather_page_tables() applies this same predicate, and TABLE_MERGE_TOP_FRAC = 0.28 expects a
# continuation to start within the top 28% — so a tail inside the top 10% would be dropped BEFORE
# the merge could stitch it. Latent, never observed to fire; fix the ordering when merge ships.
p9 = tmp / "case9_real_table_in_band.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
draw_grid(pg, 60, 24, [120, 90, 90], 16, TABLE_ROWS[:3])
pg.insert_textbox(fitz.Rect(60, 300, 520, 700), LOREM * 4, fontsize=9)
doc.save(str(p9))
doc.close()

md9, st9 = convert(p9, "on")
check("case9: KNOWN LIMITATION — a real table entirely inside the band is rejected as furniture",
      md9.count("**[Table on page") == 0 and "running-header band only" in md9,
      f"blocks={md9.count('**[Table on page')}")
md9_off, st9_off = convert(p9, "off", {"T2N_TABLE_FURNITURE_REJECT": "0"})
check("case9: ...and the kill-switch is the escape hatch for it (table returns intact)",
      "| Neer sign | 88.7% | 30.5% |" in md9_off,
      f"blocks={md9_off.count('**[Table on page')}")

# ── case 10: doubled page frame with ZERO rulings (the AAP geometry) ─────────
# Regression pin for faketable-rootcause.md BUG 1. AAP_Pediatric-Clinical-Practice-Guidelines
# pp.358-368 carry NO line segments at all — only a running-header rectangle drawn twice and a
# content frame drawn twice ~1pt apart. case1 uses a frame plus an explicit rule, a different
# shape, so nothing currently pins this one. (The suspected "header hairline + column gutter read
# as rulings" was refuted: fitz reports n_lines=0, n_curves=0 on those pages.)
p10 = tmp / "case10_doubled_frame.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
pg.draw_rect(fitz.Rect(30.0, 23.5, 574.0, 41.5), width=0.7)    # running-header box
pg.draw_rect(fitz.Rect(30.0, 23.5, 574.0, 41.5), width=0.7)    # ...drawn a second time
pg.draw_rect(fitz.Rect(23.4, 47.5, 578.6, 753.0), width=0.7)   # content frame
pg.draw_rect(fitz.Rect(23.4, 48.5, 578.6, 757.0), width=0.7)   # ...and again, 1pt off
pg.insert_textbox(fitz.Rect(40, 60, 290, 740), "See Table 3.1. " + LOREM * 6, fontsize=9)
pg.insert_textbox(fitz.Rect(310, 60, 570, 740), LOREM * 6, fontsize=9)
doc.save(str(p10))
doc.close()

md10, st10 = convert(p10, "on")
check("case10: doubled page frame with no rulings is rejected as a page frame",
      md10.count("**[Table on page") == 0 and "page frame" in md10,
      f"blocks={md10.count('**[Table on page')}")
check("case10: the two-column prose survives",
      "Antihistamine and decongestant" in md10.replace("\n", " "))

# ── case 11: content_cols blind spot (PR #1, @Alan9981) ─────────────────────
# A frame whose right edge is doubled, so pdfplumber resolves TWO columns while every word lands
# in the left one. The normalized count (2) walks past a `cols <= 1` bail-out; the content-bearing
# count (1) does not. Measured across 56 books / 1,584 pages: 30 of 263 surviving tables (11.4%)
# have this shape, every one of them page decoration.
p11 = tmp / "case11_content_cols.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)
pg.draw_rect(fitz.Rect(25.0, 40.0, 570.0, 750.0), width=0.7)
pg.draw_line((25.0, 60.0), (570.0, 60.0), width=0.7)      # spurious internal horizontal
pg.draw_line((555.0, 40.0), (555.0, 750.0), width=0.7)    # narrow, permanently empty right column
pg.insert_textbox(fitz.Rect(35, 70, 545, 740), "See Table 9. " + LOREM * 4, fontsize=9)
doc.save(str(p11))
doc.close()

md11, st11 = convert(p11, "on")
check("case11: 2-column candidate with one always-empty column is rejected",
      md11.count("**[Table on page") == 0, f"blocks={md11.count('**[Table on page')}")
check("case11: the trace comment reports the content-column shape, not the normalized one",
      "content in 1 of 2 columns" in md11)

md11_narrow, _ = convert(p11, "narrowoff", {"T2N_TABLE_FRAME_CONTENT_COLS": "0"})
check("case11: narrow kill-switch restores pre-PR#1 behavior (the pseudo-table comes back)",
      md11_narrow.count("**[Table on page") == 1,
      f"blocks={md11_narrow.count('**[Table on page')}")

md11_off, _ = convert(p11, "off", {"T2N_TABLE_FRAME_REJECT": "0"})
check("case11: the whole-predicate kill-switch also restores it",
      md11_off.count("**[Table on page") == 1, f"blocks={md11_off.count('**[Table on page')}")

md11c, _ = convert(p8, "ccols_control")
check("case11: a genuine 3-column table is unaffected by the content_cols widening",
      md11c.count("**[Table on page") == 1 and "| Neer sign | 88.7% | 30.5% |" in md11c)


# ── predicate unit checks ───────────────────────────────────────────────────
def with_env(env, fn):
    """Call fn() with a temporary env overlay (predicate-level equivalent of convert()'s)."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return fn()
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


big = "x" * 600
check("predicate: 1 column + oversized cell → rejected",
      cv.page_frame_reject_reason([[big], ["y"]], (30, 40, 570, 800), PAGE_W, PAGE_H) is not None)
check("predicate: 1 column + page-covering bbox → rejected even with short cells",
      cv.page_frame_reject_reason([["a"], ["b"]], (30, 40, 570, 800), PAGE_W, PAGE_H) is not None)
check("predicate: 1 column, short cells, small bbox → kept",
      cv.page_frame_reject_reason([["a"], ["b"]], (60, 300, 260, 380), PAGE_W, PAGE_H) is None)
# Renamed: "multi-column" is no longer the operative property — a multi-column candidate whose
# content sits in ONE column is now rejected (see the content_cols checks below).
check("predicate: multi-CONTENT-column is never rejected, however large the cell or bbox",
      cv.page_frame_reject_reason([[big, big], ["a", "b"]], (30, 40, 570, 800), PAGE_W, PAGE_H) is None)

# content_cols (PR #1, @Alan9981)
check("predicate: 2 ruled columns but content in only one → rejected",
      cv.page_frame_reject_reason([[big, ""], ["a", ""]], (30, 40, 570, 800), PAGE_W, PAGE_H) is not None)
check("predicate: ...and the reason names the content-column shape",
      "content in 1 of 2 columns" in (cv.page_frame_reject_reason(
          [[big, ""], ["a", ""]], (30, 40, 570, 800), PAGE_W, PAGE_H) or ""))
check("predicate: content_cols kill-switch restores the pre-PR#1 bail-out",
      with_env({"T2N_TABLE_FRAME_CONTENT_COLS": "0"},
               lambda: cv.page_frame_reject_reason([[big, ""], ["a", ""]],
                                                   (30, 40, 570, 800), PAGE_W, PAGE_H)) is None)
check("predicate: content_cols widening still needs a size branch to fire "
      "(tiny decoration is left alone)",
      cv.page_frame_reject_reason([["CHAPTER 6", ""], ["", ""]],
                                  (336, 30, 531, 99), PAGE_W, PAGE_H) is None)
check("predicate: _content_col_count counts columns holding content, not ruled columns",
      cv._content_col_count([["a", "", ""], ["b", "", ""]]) == 1
      and cv._content_col_count([["a", "b", ""], ["c", "", ""]]) == 2)

# furniture band (BUG 2)
BAND = cv.TABLE_FURNITURE_BAND_FRAC * PAGE_H
check("predicate: bbox entirely inside the top band → rejected as running-header",
      "running-header" in (cv.page_furniture_reject_reason(
          [["a", "b"], ["c", "d"]], (23, 23, 572, BAND - 20), PAGE_H) or ""))
check("predicate: bbox entirely inside the bottom band → rejected as running-footer",
      "running-footer" in (cv.page_furniture_reject_reason(
          [["a", "b"], ["c", "d"]], (23, PAGE_H - BAND + 20, 572, PAGE_H - 5), PAGE_H) or ""))
check("predicate: a bbox that crosses out of the band is never furniture",
      cv.page_furniture_reject_reason([["a", "b"], ["c", "d"]],
                                      (23, 23, 572, BAND + 1), PAGE_H) is None)
check("predicate: furniture rule is geometry-only — text volume does not exempt it",
      cv.page_furniture_reject_reason([[big, big], ["c", "d"]],
                                      (23, 23, 572, BAND - 20), PAGE_H) is not None)
check("predicate: furniture kill-switch disables the band rule",
      with_env({"T2N_TABLE_FURNITURE_REJECT": "0"},
               lambda: cv.page_furniture_reject_reason([["a", "b"], ["c", "d"]],
                                                       (23, 23, 572, BAND - 20), PAGE_H)) is None)

# ── Fix: collapse category headers spanned across every column (T2N_TABLE_HEADER_COLLAPSE) ──
# 9-col row whose non-empty cells are all the same >=15-char sentence → collapses to header cell.
_spanned = [["Corticosteroids: Used to reduce inflammation."] * 9]
_n = cv._collapse_spanned_header(_spanned)
check("header-collapse: a 9-col spanned category header collapses to one cell",
      _n == 1 and _spanned[0][0] == "Corticosteroids: Used to reduce inflammation."
      and _spanned[0][1:] == [""] * 8)
# a genuine data row (distinct values) is left untouched.
_real = [["Dexamethasone", "++", "0", "++", "++", "++", "+", "++", "GI upset"]]
check("header-collapse: a real data row with distinct values is unchanged",
      cv._collapse_spanned_header(_real) == 0
      and _real[0] == ["Dexamethasone", "++", "0", "++", "++", "++", "+", "++", "GI upset"])
# short identical tokens (e.g. a Yes/Yes/Yes rating row) are below the length guard → unchanged.
_short = [["Yes", "Yes", "Yes"]]
check("header-collapse: short identical cells (below min_len) are not collapsed",
      cv._collapse_spanned_header(_short) == 0 and _short[0] == ["Yes", "Yes", "Yes"])
# only 2 identical cells (below min_cols) → unchanged.
_two = [["A long repeated label here", "A long repeated label here"]]
check("header-collapse: only two identical cells (below min_cols) are not collapsed",
      cv._collapse_spanned_header(_two) == 0)
# end-to-end through _table_to_md: header row collapsed, real row intact.
_tbl = [["Cat: a long spanned category label"] * 4,
        ["Diltiazem", "+", "0", "angina"]]
_md_on = with_env({"T2N_TABLE_HEADER_COLLAPSE": "1"}, lambda: cv._table_to_md([list(r) for r in _tbl]))
check("header-collapse: _table_to_md drops the spanned header's phantom columns",
      "Cat: a long spanned category label |" in _md_on
      and _md_on.count("Cat: a long spanned category label") == 1
      and "Diltiazem | + | 0 | angina" in _md_on)
# kill-switch restores byte-identical pre-change output (phantom columns retained).
_md_off = with_env({"T2N_TABLE_HEADER_COLLAPSE": "0"}, lambda: cv._table_to_md([list(r) for r in _tbl]))
check("header-collapse: T2N_TABLE_HEADER_COLLAPSE=0 restores byte-identical output",
      _md_off.count("Cat: a long spanned category label") == 4)

# ── Fix: data-driven book-level reliability banner predicate ──
# The numerator is tables that LOST CONTENT (a content-retention flag), not tables carrying any
# QC flag: any-flag rates measured 39-64% on every dense clinical book in the pilot, so they do
# not separate a systematically-wrong book from a mostly-right one. Content-loss rates on the same
# books were 40 / 27 / 17 / 12 / 2 / 0 %, and the threshold sits in that gap.
check("reliability: 40% content-loss rate over >=10 tables flags the book",
      cv._book_reliability_flagged(4, 10) is True)
check("reliability: exactly 25% over >=10 tables flags the book (boundary)",
      cv._book_reliability_flagged(25, 100) is True)
check("reliability: 24% content-loss rate does not flag the book (boundary-1)",
      cv._book_reliability_flagged(24, 100) is False)
check("reliability: 12% content-loss rate does not flag the book",
      cv._book_reliability_flagged(12, 100) is False)
check("reliability: a high rate under the min-table floor does not flag (and no div-by-zero)",
      cv._book_reliability_flagged(9, 9) is False and cv._book_reliability_flagged(0, 0) is False)

# ── Fix: partial table-loss detection (the zero-table rule's silent sibling) ──
# Threshold measured over 171 converted books with >=10 captions: median ratio 0.99, p75 1.99,
# and a dense low tail (21 books <0.01, 42 <0.20). A book recovering SOME tables used to produce
# no warning at all no matter how many it lost.
def _partial(tables, caps):
    """Reproduce convert_pdf()'s partial-loss branch condition."""
    return (caps >= cv.BOOK_ZERO_TABLE_MIN_CAPTIONS and tables > 0
            and tables / caps < cv.BOOK_PARTIAL_TABLE_RATIO)

check("partial-loss: 45 tables against 274 captions (ratio 0.16) warns",
      _partial(45, 274) is True)
check("partial-loss: 9 tables against 266 captions warns (large reference, zero-rule silent)",
      _partial(9, 266) is True)
check("partial-loss: exactly at the threshold does not warn (ratio 0.20)",
      _partial(20, 100) is False)
check("partial-loss: a healthy book (ratio ~1) does not warn",
      _partial(77, 67) is False and _partial(100, 100) is False)
check("partial-loss: a book with few captions is never judged on the ratio",
      _partial(0, 9) is False and _partial(1, 9) is False)

# ── Fix 3: sideways-printed pages ───────────────────────────────────────────
# A landscape mega-table imposed sideways on a portrait page: the page is /Rotate 0, but every
# glyph carries a rotated text matrix. pdfplumber orders words by (top, x0) assuming upright text,
# so it reads such a page bottom-to-top and hands back every cell character-reversed — markdown
# that looks populated, greps clean, and is unusable. The fixture is built the way the real book is
# (a landscape source page shown rotated into a portrait page), not by hand-rolling a text matrix,
# so it exercises the same geometry the bug was found on (Carranza 12e TABLE 6-3, PDF p.157-384).
SIDE_ROWS = [
    ["Gene Symbol", "Disease Type", "Cases", "Population"],
    ["ABCA1", "chronic", "22", "Japanese"],
    ["ADCY9", "chronic", "41", "Japanese"],
    ["IL4R", "aggressive", "251", "Caucasian"],
]
_land = fitz.open()
_lp = _land.new_page(width=PAGE_H, height=PAGE_W)          # landscape source
_lp.insert_text((40, 45), "TABLE 6-3  Association Study", fontsize=9)
draw_grid(_lp, 40, 60, [130, 110, 70, 110], 22, SIDE_ROWS)

p_side = tmp / "case_sideways.pdf"
doc = fitz.open()
pg = doc.new_page(width=PAGE_W, height=PAGE_H)             # portrait page, /Rotate 0
pg.show_pdf_page(pg.rect, _land, 0, rotate=90)             # ...carrying the table sideways
doc.save(str(p_side))
doc.close()
_land.close()

import pdfplumber  # noqa: E402  (only needed for the two detector unit checks)

with pdfplumber.open(str(p_side)) as _pl:
    rot_side = cv.page_sideways_rotation(_pl.pages[0])
with pdfplumber.open(str(p2)) as _pl:                      # the ordinary ruled table from case 2
    rot_upright = cv.page_sideways_rotation(_pl.pages[0])

check("Fix 3: a sideways page is detected and assigned a rotation",
      rot_side in (90, 270), f"rot={rot_side}")
check("Fix 3: an ordinary upright page is left alone",
      rot_upright is None, f"rot={rot_upright}")

md_s, st_s = convert(p_side, "sideways_on")
md_s_off, st_s_off = convert(p_side, "sideways_off", {"T2N_SIDEWAYS": "0"})

check("Fix 3: the fixture reproduces the bug when the fix is off (cells come back reversed)",
      "1ACBA" in md_s_off, "expected reversed 'ABCA1' in the unfixed output")
check("Fix 3: repaired output carries the cell text forward, not reversed",
      "ABCA1" in md_s and "1ACBA" not in md_s)
check("Fix 3: repaired output recovers the real header row",
      "Gene Symbol" in md_s and "lobmyS eneG" not in md_s)
check("Fix 3: the repair leaves a visible trace comment (a rewrite is never silent)",
      "printed sideways" in md_s)
check("Fix 3: the repair is counted in stats",
      st_s.get("sideways_pages") == 1, f"sideways_pages={st_s.get('sideways_pages')}")
check("Fix 3: kill-switch restores the pre-fix behavior",
      st_s_off.get("sideways_pages") == 0 and "printed sideways" not in md_s_off)

# Guard 2 — the detector that catches what the repair cannot reach (mixed-orientation pages, an
# unexpected glyph orientation). It judges only against the page's own text layer: no word list,
# no language assumption, and nothing is rejected without evidence from the source PDF.
REV_PAGE_TEXT = ("Association study of periodontitis in a Japanese population. "
                 "Multiple SNPs were genotyped.")
check("Fix 3: cells that only match the page text reversed are rejected",
      cv.reversed_text_reject_reason(
          [["noitaicossA", "sititnodoirep"],
           ["esenapaJ", "elpitluM", "noitalupop", "yduts"]], REV_PAGE_TEXT) is not None)
check("Fix 3: ordinary forward cells are never rejected",
      cv.reversed_text_reject_reason(
          [["Association", "periodontitis"],
           ["Japanese", "Multiple", "population", "study"]], REV_PAGE_TEXT) is None)
check("Fix 3: a single reversed-looking token is not enough evidence",
      cv.reversed_text_reject_reason([["sititnodoirep", "22"]], REV_PAGE_TEXT) is None)
check("Fix 3: with no page text there is no oracle, so nothing is rejected",
      cv.reversed_text_reject_reason([["sititnodoirep", "esenapaJ", "elpitluM",
                                       "noitalupop", "yduts", "noitaicossA"]], "") is None)

# ── report ──────────────────────────────────────────────────────────────────
fails = [r for r in results if r[1] == "FAIL"]
for name, status, detail in results:
    mark = {"PASS": "OK", "FAIL": "XX", "SKIP": "--"}[status]
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and status != "PASS" else ""))
print(f"--- {sum(r[1]=='PASS' for r in results)} pass, "
      f"{len(fails)} fail, {sum(r[1]=='SKIP' for r in results)} skip ---")
sys.exit(1 if fails else 0)
