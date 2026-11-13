"""test_runin_pairs.py — run-in term/definition pairing on born-digital glossary pages
(openspec change runin-glossary-pairs, T2N_RUNIN_PAIRS=1).

Synthetic PDFs are built in the test with fitz; the content stream order is the order of the
insert_text() calls, so "all terms, then all definitions" is reproduced exactly. The bundled
OpenStax sample (Key Terms on pages 28-30) is the real-book check.

  python converter/test_runin_pairs.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert as cv  # noqa: E402

SAMPLE = HERE.parent / "examples" / "sample" / "OpenStax_Anatomy-and-Physiology-Ch1_1e_2013.pdf"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def render(page, flag):
    old = os.environ.pop("T2N_RUNIN_PAIRS", None)
    if flag is not None:
        os.environ["T2N_RUNIN_PAIRS"] = flag
    try:
        return cv.render_page_text(page, page.number + 1)[0]
    finally:
        os.environ.pop("T2N_RUNIN_PAIRS", None)
        if old is not None:
            os.environ["T2N_RUNIN_PAIRS"] = old


def plain(page):
    """The pre-change path: plain get_text(), cleaned and annotated."""
    return cv.annotate_fig_refs(cv.clean_text(page.get_text().strip()), page.number + 1)[0]


ENTRIES = [
    ("anabolism", "assembly of more complex molecules from simpler molecules in the body"),
    ("anatomy", "science that studies the form and composition of the body structures"),
    ("caudal", "describes a position below or lower than another part of the body proper"),
    ("effector", "organ that can cause a change in a value measured by a sensor somewhere"),
    ("growth", "process of increasing in size over the lifetime of the organism studied"),
]


def glossary_page(doc, entries, interleave=False, heading="KEY TERMS", gap=4):
    """Run-in glossary: bold term at x=72, definition on the same baseline right after it."""
    page = doc.new_page(width=612, height=792)
    rows = [(t, d, 100 + 24 * i) for i, (t, d) in enumerate(entries)]
    term_w = {t: fitz.get_text_length(t, fontname="hebo", fontsize=10) for t, _, _ in rows}
    if interleave:
        for t, d, y in rows:
            page.insert_text((72, y), t, fontname="hebo", fontsize=10)
            page.insert_text((72 + term_w[t] + gap, y), d, fontname="helv", fontsize=10)
    else:
        for t, _, y in rows:                                    # all terms first ...
            page.insert_text((72, y), t, fontname="hebo", fontsize=10)
        page.insert_text((72, 70), heading, fontname="hebo", fontsize=14)
        for t, d, y in rows:                                    # ... then all definitions
            page.insert_text((72 + term_w[t] + 4, y), d, fontname="helv", fontsize=10)
    return page


# --- 1. the failure: stream-separated glossary is rejoined under the flag ---------------------
doc = fitz.open()
pg = glossary_page(doc, ENTRIES)
off, on, zero = render(pg, None), render(pg, "1"), render(pg, "0")
check("unset: plain get_text() path, byte-identical", off == plain(pg))
check("=0: same as unset", zero == off)
check("unset reproduces the bug: definition is not next to its term",
      "anabolism assembly" not in off and "**anabolism**" not in off)
for t, d in ENTRIES:
    check(f"=1: entry '{t}' is one paragraph", f"**{t}** {d}" in on, on[:300])
pos = [on.find(f"**{t}**") for t, _ in ENTRIES]
check("=1: entries keep term order", min(pos) >= 0 and pos == sorted(pos), str(pos))
check("=1: heading kept", "KEY TERMS" in on)
check("=1: entries separated by a blank line (clean_text cannot glue them)",
      f"{ENTRIES[0][1]}\n\n**{ENTRIES[1][0]}**" in on)
check("=1: column sort declined this page (not two-column)", cv.column_sorted_text(pg) is None)

# --- 2. below threshold: two separated pairs are left alone -----------------------------------
doc2 = fitz.open()
pg2 = glossary_page(doc2, ENTRIES[:2])
check("2 pairs (< RUNIN_MIN_PAIRS=3): runin_paired_text is None", cv.runin_paired_text(pg2) is None)
check("2 pairs: =1 output == unset output", render(pg2, "1") == render(pg2, None))

# --- 3. stream already in reading order: nothing to fix ---------------------------------------
doc3 = fitz.open()
# gap=12 keeps term and definition as separate fitz lines (at 4 pt fitz merges them into one)
pg3 = glossary_page(doc3, ENTRIES, interleave=True, gap=12)
check("interleaved fixture: term and definition are separate lines",
      sum(len(b["lines"]) for b in pg3.get_text("dict")["blocks"]) == 2 * len(ENTRIES))
check("interleaved stream: runin_paired_text is None", cv.runin_paired_text(pg3) is None)
check("interleaved stream: =1 output == unset output", render(pg3, "1") == render(pg3, None))

# --- 4. table rows  bold A | B | C  (column-major stream) are never paired ----------------------
doc4 = fitz.open()
pg4 = doc4.new_page(width=612, height=792)
cells = [("Drug%d" % i, "dose%d mg" % i, "route%d" % i) for i in range(6)]
fonts = ("hebo", "helv", "helv")
for col in range(3):                                            # column-major stream
    for i, row in enumerate(cells):
        x = 72 + sum(fitz.get_text_length(row[c], fontname=fonts[c], fontsize=10) + 12
                     for c in range(col))
        pg4.insert_text((x, 100 + 20 * i), row[col], fontname=fonts[col], fontsize=10)
check("bold A | B | C table rows: runin_paired_text is None", cv.runin_paired_text(pg4) is None)

# --- 4b. figure panel letters beside labels, same weight (Lundy-Ekman-like) --------------------
doc4b = fitz.open()
pg4b = doc4b.new_page(width=612, height=792)
labels = ["Edema", "Before injury", "After death of cell", "Unmasked synapse"]
for i, _ in enumerate(labels):                                  # all panel letters first
    pg4b.insert_text((160, 100 + 120 * i), "ABCD"[i], fontname="helv", fontsize=10)
for i, lab in enumerate(labels):                                # then the labels beside them
    pg4b.insert_text((176, 100 + 120 * i), lab, fontname="helv", fontsize=10)
check("panel letters + labels, one weight: runin_paired_text is None",
      cv.runin_paired_text(pg4b) is None)

# --- 4c. rotated text on a paired page is kept --------------------------------------------------
doc4c = fitz.open()
pg4c = glossary_page(doc4c, ENTRIES)
pg4c.insert_text((40, 700), "SIDEWAYS MARGIN NOTE", fontsize=8, rotate=90)
on4c = render(pg4c, "1")
check("rotated line survives on a paired page", "SIDEWAYS MARGIN NOTE" in on4c and "**anatomy**" in on4c)

# --- 5. two-column body page: column sort owns it, flag changes nothing -----------------------
doc5 = fitz.open()
pg5 = doc5.new_page(width=612, height=792)
for i in range(8):
    pg5.insert_text((72, 100 + 14 * i), f"left column body line {i}", fontsize=10)
for i in range(8):
    pg5.insert_text((330, 100 + 14 * i), f"right column body line {i}", fontsize=10)
check("two-column page: =1 output == unset output", render(pg5, "1") == render(pg5, None))

# --- 6. real book: OpenStax sample, Key Terms on pages 28-30 ----------------------------------
if SAMPLE.exists():
    sd = fitz.open(str(SAMPLE))
    changed = [p.number + 1 for p in sd if render(p, "1") != render(p, None)]
    check("sample: =1 changes exactly pages 28-30", changed == [28, 29, 30], str(changed))
    unset_same = all(render(p, None) == plain(p) or cv.column_sorted_text(p) is not None
                     for p in sd)
    check("sample: unset keeps the pre-change path on every page", unset_same)
    p28 = render(sd[27], "1")
    check("sample p28: '**anatomy** science that studies' rejoined",
          "**anatomy** science that studies the form and composition" in p28)
    entry = p28.split("**anterior cavity**", 1)[-1].split("\n\n", 1)[0]
    check("sample p28: multi-line definition keeps its continuation inside the entry",
          entry.startswith(" larger body cavity") and "pelvic organs" in entry, entry[:80])
    def ink(s):  # characters, ignoring whitespace, the added '**' and clean_text's hyphen joins
        return sorted(c for c in s if not c.isspace() and c not in "*-")
    check("sample p28-30: no line dropped or duplicated (same characters as unset)",
          all(ink(render(sd[i], "1")) == ink(render(sd[i], None)) for i in (27, 28, 29)))
    check("synthetic page: no line dropped or duplicated", ink(on) == ink(off))
    n = sum(render(sd[i], "1").count("\n**") + render(sd[i], "1").startswith("**")
            for i in (27, 28, 29))
    check("sample p28-30: 70 entries rejoined", n == 70, str(n))
else:
    check("sample PDF present", False, str(SAMPLE))

fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"  [{detail}]"))
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
