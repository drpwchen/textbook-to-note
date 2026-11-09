"""test_split_same_page.py — bookmarks sharing a split line produce one chapter file
(openspec change resplit-same-page-bookmarks).

Each scenario builds a synthetic `full_text.md` (with `<!-- page N -->` markers) and, where the
scenario names a PDF, a synthetic PDF with a hand-set TOC; then splits it with the current
converter and compares against a frozen copy of the v0.7.3 splitter (`_legacy_split` below).

  python converter/test_split_same_page.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import os
import re
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


def _legacy_split(content: str, out_dir: Path, bookmarks: list):
    """Frozen copy of split_by_bookmarks() as shipped in v0.7.3 (the kill-switch reference)."""
    lines = content.split('\n')
    page_to_line = {}
    for i, line in enumerate(lines):
        m = re.match(r'<!-- page (\d+) -->', line)
        if m:
            page_to_line[int(m.group(1))] = i
    chapters = []
    for level, title, page in bookmarks:
        if level <= 2 and page in page_to_line:
            chapters.append((page, page_to_line[page], title.strip()))
    if len(chapters) < 2:
        return None
    front_end = chapters[0][1]
    front = '\n'.join(lines[:front_end]).strip()
    if front:
        (out_dir / 'ch00_Front_Matter.md').write_text(front, encoding='utf-8')
    for idx, (pg, line_start, title) in enumerate(chapters):
        line_end = chapters[idx + 1][1] if idx + 1 < len(chapters) else len(lines)
        ch_content = '\n'.join(lines[line_start:line_end])
        ch_num = idx + 1
        safe_title = re.sub(r'[^a-zA-Z0-9_ ]', '', title)[:50].strip().replace(' ', '_')
        fname = f'ch{ch_num:02d}_{safe_title}.md' if safe_title else f'ch{ch_num:02d}.md'
        (out_dir / fname).write_text(ch_content, encoding='utf-8')
    return chapters


def full_text(n_pages: int, blank_pages=(), preface="Preface text before page one.") -> str:
    parts = [preface] if preface else []
    for p in range(1, n_pages + 1):
        parts.append(f'<!-- page {p} -->')
        parts.append('' if p in blank_pages else f'Body text of page {p}.')
    return '\n\n'.join(parts) + '\n'


def snapshot(d: Path) -> dict:
    return {f.name: f.read_bytes() for f in sorted(d.glob('ch*.md'))}


def run(content, bookmarks, env_value=None, pdf_path=None):
    """Split with the current converter; returns {name: bytes}."""
    d = Path(tempfile.mkdtemp())
    old = os.environ.pop('T2N_SPLIT_SAME_PAGE_MERGE', None)
    try:
        if env_value is not None:
            os.environ['T2N_SPLIT_SAME_PAGE_MERGE'] = env_value
        if pdf_path:
            cv.split_into_chapters(content, d, 999, pdf_path=str(pdf_path))
        else:
            cv.split_by_bookmarks(content, d, bookmarks)
    finally:
        os.environ.pop('T2N_SPLIT_SAME_PAGE_MERGE', None)
        if old is not None:
            os.environ['T2N_SPLIT_SAME_PAGE_MERGE'] = old
    return snapshot(d)


def run_legacy(content, bookmarks):
    d = Path(tempfile.mkdtemp())
    _legacy_split(content, d, bookmarks)
    return snapshot(d)


def make_pdf(path: Path, n_pages: int, toc: list) -> None:
    doc = fitz.open()
    for p in range(n_pages):
        doc.new_page().insert_text((72, 72), f'page {p + 1}')
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def has_text(b: bytes) -> bool:
    return any(l.strip() for l in b.decode('utf-8').split('\n')
               if not re.match(r'^\s*<!-- page \d+ -->\s*$', l))


# ── Scenario 1: Part and chapter bookmark on the same page (through a real PDF TOC) ──
tmp = Path(tempfile.mkdtemp())
pdf = tmp / 'book.pdf'
toc1 = [[1, 'Part I', 12], [2, 'Chapter 1 Gait', 12], [2, 'Chapter 2 Balance', 30],
        [2, 'Chapter 3 Falls', 40]]
make_pdf(pdf, 50, toc1)
ft1 = full_text(50)
out1 = run(ft1, None, pdf_path=pdf)
check('S1 ch01_Part_I.md not written', 'ch01_Part_I.md' not in out1, sorted(out1))
g = out1.get('ch02_Chapter_1_Gait.md', b'').decode('utf-8')
check('S1 ch02 starts at page-12 marker', g.startswith('<!-- page 12 -->'), g[:40])
check('S1 ch02 ends before page-30 marker',
      '<!-- page 29 -->' in g and '<!-- page 30 -->' not in g)
check('S1 ch03_Chapter_2_Balance.md exists', 'ch03_Chapter_2_Balance.md' in out1, sorted(out1))
leg1 = run_legacy(ft1, cv.get_pdf_bookmarks(str(pdf)))
check('S1 survivors byte-identical to v0.7.3',
      all(leg1.get(k) == v for k, v in out1.items()), sorted(out1))
check('S1 only the empty file disappeared', set(leg1) - set(out1) == {'ch01_Part_I.md'},
      sorted(set(leg1) - set(out1)))

# ── Scenario 2: no empty chapter file is written (runs of 3, blank first page of a pair) ──
toc2 = [[1, 'Front', 1], [1, 'Part A', 3], [2, 'Ch A1', 3], [2, 'Ch A2', 3],
        [2, 'Ch A3', 5], [1, 'Part B', 9], [2, 'Ch B1', 10], [2, 'Ch B2', 14]]
ft2 = full_text(20, blank_pages={9})
out2 = run(ft2, toc2)
check('S2 every file > 0 bytes', all(len(v) > 0 for v in out2.values()), sorted(out2))
check('S2 every file has text outside page markers',
      all(has_text(v) for v in out2.values()),
      [k for k, v in out2.items() if not has_text(v)])
check('S2 page-marker-only Part B file not written', 'ch06_Part_B.md' not in out2, sorted(out2))
check('S2 numbering keeps gaps (ch04 survives, ch02/ch03 gone)',
      'ch04_Ch_A2.md' in out2 and 'ch02_Part_A.md' not in out2 and 'ch03_Ch_A1.md' not in out2,
      sorted(out2))
leg2 = run_legacy(ft2, toc2)
check('S2 survivors byte-identical to v0.7.3',
      all(leg2.get(k) == v for k, v in out2.items()))
check('S2 no text lost: legacy files dropped are exactly the text-less ones',
      all(not has_text(leg2[k]) for k in set(leg2) - set(out2)))

# ── Scenario 3: kill switch restores prior output byte for byte ──
for name, ft, toc in (('S1', ft1, cv.get_pdf_bookmarks(str(pdf))), ('S2', ft2, toc2)):
    off = run(ft, toc, env_value='0')
    check(f'S3 {name} T2N_SPLIT_SAME_PAGE_MERGE=0 == v0.7.3', off == run_legacy(ft, toc),
          sorted(set(off) ^ set(run_legacy(ft, toc))))
off1 = run(ft1, None, env_value='0', pdf_path=pdf)
check('S3 kill switch keeps 0-byte ch01_Part_I.md', off1.get('ch01_Part_I.md') == b'')

# ── Scenario 4: every bookmark resolves to page 1 (Lanken shape) ──
toc4 = [[1 if i % 5 == 0 else 2, f'Entry {i + 1}', 1] for i in range(234)] + \
       [[1, 'Appendix E Last', 1]]
ft4 = full_text(30)
out4 = run(ft4, toc4)
leg4 = run_legacy(ft4, toc4)
check('S4 bookmark split still used (ch00 + ch235 written)',
      sorted(out4) == ['ch00_Front_Matter.md', 'ch235_Appendix_E_Last.md'], sorted(out4))
check('S4 both files byte-identical to v0.7.3',
      all(leg4[k] == v for k, v in out4.items()))

# ── Negative: out-of-order bookmarks behave exactly as before ──
toc5 = [[1, 'One', 2], [1, 'Two', 8], [1, 'Three', 5], [1, 'Four', 12]]
ft5 = full_text(15)
check('N out-of-order bookmarks unchanged', run(ft5, toc5) == run_legacy(ft5, toc5),
      sorted(run(ft5, toc5)))
# A book with no collisions at all: identical output with the behaviour on.
toc6 = [[1, 'One', 2], [1, 'Two', 6], [2, 'Two point one', 7], [1, 'Three', 11]]
ft6 = full_text(15)
check('N clean book byte-identical with behaviour on', run(ft6, toc6) == run_legacy(ft6, toc6))

# ── report ──
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or not detail else f'  — {detail}'))
print(f'\n{len(results) - len(fails)}/{len(results)} pass')
sys.exit(1 if fails else 0)
