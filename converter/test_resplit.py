"""test_resplit.py — `--resplit`, the per-book manifest, the split config, deep headings and
the validator (openspec changes split-manifest-contract, split-config, resplit-cli,
resplit-deep-bookmarks). Synthetic corpora + synthetic PDFs only.

  python converter/test_resplit.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import convert as cv  # noqa: E402
import resplit as rs  # noqa: E402
import split_manifest as sm  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


CHAPTERS = [("Gait", 3), ("Balance", 6), ("Spasticity", 9)]  # (title, first page)
N_PAGES = 12


def page_body(p):
    for i, (t, start) in enumerate(CHAPTERS, 1):
        if p == start:
            return f"CHAPTER {i}\n{t.upper()}\nOverview\nIntro text of {t}.\nREFERENCES\n1. Someone 2020."
    return f"Body text of page {p}."


def make_full_text(pages=N_PAGES, header=True, blank=()):
    parts = ["# Book\n\nSource: `x.pdf`\n"] if header else []
    for p in range(1, pages + 1):
        parts.append(f"<!-- page {p} -->")
        parts.append("" if p in blank else page_body(p))
    return "\n\n".join(parts) + "\n"


def make_pdf(path: Path, toc, labels=True, pages=N_PAGES):
    doc = fitz.open()
    for p in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {p}")
    if labels:
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                             {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1}])
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


GOOD_TOC = [[1, "Preface", 1], [1, "Part I", 3], [2, "Chapter 1 Gait", 3], [3, "Overview", 3],
            [3, "References", 3], [2, "Chapter 2 Balance", 6], [3, "Overview", 6],
            [2, "Chapter 3 Spasticity", 9], [1, "Index", 12]]


def build(tmp: Path, books: dict, pdfs: dict):
    corpus, pdf_root, out = tmp / "corpus", tmp / "pdfs", tmp / "out"
    corpus.mkdir()
    pdf_root.mkdir()
    for name, files in books.items():
        d = corpus / name
        d.mkdir(parents=True)
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text, encoding="utf-8")
    for name, toc in pdfs.items():
        make_pdf(pdf_root / f"{name}.pdf", toc)
    return corpus, pdf_root, out


def run(corpus, out, roots, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rs.run(corpus, out, [str(r) for r in roots], **kw)
    return code, buf.getvalue()


def tree_state(root: Path):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, sm.sha256_file(p)
                                        if hasattr(sm, "sha256_file") else rs.sha256_file(p))
            for p in root.rglob("*") if p.is_file()}


def manifest(out, book):
    return json.loads((out / book / "_manifest.json").read_text(encoding="utf-8"))


# --- helpers ---------------------------------------------------------------------------------

def test_helpers():
    check("parse 'Chapter 5 Stroke'", sm.parse_chapter_number("Chapter 5 Stroke") == 5)
    check("parse '45 - Evaluation'", sm.parse_chapter_number("45 - Evaluation of X") == 45)
    check("parse '3. Spasticity'", sm.parse_chapter_number("3. Spasticity") == 3)
    check("parse 'CHAPTER FIVE'", sm.parse_chapter_number("CHAPTER FIVE") == 5)
    check("no number from '12.3 Stroke'", sm.parse_chapter_number("12.3 Stroke") is None)
    check("no number from '2019 Update'", sm.parse_chapter_number("2019 Update") is None)
    check("no number from 'Chest Bellows'", sm.parse_chapter_number("Chest Bellows") is None)
    check("strip keeps 'Chest Bellows'", sm.strip_numbering("Chest Bellows") == "Chest Bellows")
    check("kind references", sm.classify_kind("Suggested Readings") == "references")
    check("kind references numbered", sm.classify_kind("12.4 References") == "references")
    check("kind front", sm.classify_kind("Preface to Second Edition") == "front")
    check("kind back", sm.classify_kind("Index") == "back")
    check("kind body", sm.classify_kind("Chapter 2 Balance") == "body")
    t = rs._file_title_from_name("ch04_ListofContributors_2019_StrokeRehabilitation.md", False)
    check("carried title drops '_<year>_<BookName>'", t == "ListofContributors", t)
    t = rs._file_title_from_name("ch07_Index_2020_PainCareEssentialsandInnovations.md", False)
    check("…so Index is back matter", sm.classify_kind(t) == "back", t)
    t = rs._file_title_from_name("ch12_Stroke_After_2019.md", False)
    check("a bare trailing year is kept", t == "Stroke After 2019", t)
    lvl, _ = sm.choose_chapter_level([(1, "Part I"), (2, "Chapter 1 A"), (2, "Chapter 2 B"),
                                      (2, "Chapter 3 C"), (3, "1 Intro"), (3, "2 Methods"),
                                      (3, "1 Intro"), (3, "2 Methods")])
    check("chapter level = the increasing numbered level", lvl == 2, lvl)
    lvl, why = sm.choose_chapter_level([(1, "Chapter 4 Hip"), (1, "Chapter 2 Knee"), (1, "Chapter 5 Ankle")])
    check("inconsistent numbering → no level", lvl is None and "not increasing" in why, why)
    st = sm.chapter_state_walk([(1, "Preface"), (1, "Chapter 1 Gait"), (2, "Overview"),
                                (1, "2019 Update"), (1, "Chapter 2 Balance")], 1)
    check("state: own / inherited / null", st == [None, 1, 1, None, 2], st)


def test_page_labels_and_writeback():
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "a.pdf"
        make_pdf(p, GOOD_TOC)
        m = sm.read_pdf_meta(p)
        check("labels from the PDF table", m["page_labels"]["1"] == "i" and m["page_labels"]["3"] == "1",
              m["page_labels"])
        check("one label per page", len(m["page_labels"]) == N_PAGES)
        check("publisher outline is not write-back", m["writeback"] is False)
        q = tmp / "b.pdf"
        make_pdf(q, [], labels=False)
        check("no label table → None", sm.read_pdf_meta(q)["page_labels"] is None)
        doc = fitz.open(str(q))
        doc.set_toc([[1, "CHAPTER 1", 3], [1, "CHAPTER 2", 6], [1, "CHAPTER 3", 9]])
        doc.save(str(q), incremental=True, encryption=0)
        doc.close()
        check("old converter write-back detected", sm.read_pdf_meta(q)["writeback"] is True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- resplit ---------------------------------------------------------------------------------

def test_resplit_normal():
    tmp = Path(tempfile.mkdtemp())
    try:
        ft = make_full_text()
        old = {"full_text.md": ft, "ch01_Preface.md": "", "ch07_Stale.md": "x", "notes/n.md": "keep"}
        corpus, pdf_root, out = build(tmp, {"BookA": old, "BookB": {"ch01_x.md": "<!-- page 1 -->\n\nhi"}},
                                      {"BookA": GOOD_TOC})
        before = tree_state(tmp / "corpus")
        pdf_before = tree_state(pdf_root)
        code, log = run(corpus, out, [pdf_root])
        check("exit 0", code == 0, log)
        check("corpus untouched", tree_state(tmp / "corpus") == before)
        check("pdfs untouched", tree_state(pdf_root) == pdf_before)
        check("full_text byte copy", (out / "BookA" / "full_text.md").read_bytes() ==
              (corpus / "BookA" / "full_text.md").read_bytes())
        check("stale split file not carried", not (out / "BookA" / "ch07_Stale.md").exists())
        check("non-split file carried", (out / "BookA" / "notes" / "n.md").read_text() == "keep")
        m = manifest(out, "BookA")
        check("split_method bookmarks", m["split_method"] == "bookmarks", m["split_method"])
        check("source_pdf is a file name", m["source_pdf"] == "BookA.pdf")
        check("page labels printed", m["page_confidence"] == "printed" and m["page_labels"]["3"] == "1")
        nums = {f["title"]: f["chapter_number"] for f in m["files"]}
        check("real chapter numbers", [nums.get(f"Chapter {i} {t}") for i, (t, _) in
                                       enumerate(CHAPTERS, 1)] == [1, 2, 3], nums)
        check("index is back", any(f["kind"] == "back" for f in m["files"]))
        refs = [s for s in m["sections"] if s["kind"] == "references"]
        check("references section tagged", len(refs) == 1 and refs[0]["pdf_page"] == 3, refs)
        check("toc_chapters 3", m["toc_chapters"] == 3)
        v = sm.validate_book(out / "BookA")
        check("validator passes BookA", v["ok"], v["errors"])
        mb = manifest(out, "BookB")
        check("no full_text → carried", mb["split_method"] == "carried" and mb["source_pdf"] is None)
        check("carried validated", sm.validate_book(out / "BookB")["ok"])
        rep = json.loads((out / "_resplit_report.json").read_text(encoding="utf-8"))
        check("report per book", set(rep) >= {"BookA", "BookB"})
        # resume: output exists → skipped, untouched
        stamp = (out / "BookA" / "_manifest.json").stat().st_mtime_ns
        code, log = run(corpus, out, [pdf_root])
        check("existing output skipped", "BookA: skipped (output exists)" in log, log)
        check("existing output unchanged", (out / "BookA" / "_manifest.json").stat().st_mtime_ns == stamp)
        code, log = run(corpus, out, [pdf_root], force=True)
        check("--force rebuilds", "BookA: skipped" not in log and (out / "BookA" / "_manifest.json").exists())
        check("no tmp dir left", not any(p.name.startswith(".tmp-") for p in out.iterdir()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_same_as_conversion():
    tmp = Path(tempfile.mkdtemp())
    try:
        ft = make_full_text()
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": ft}}, {"BookA": GOOD_TOC})
        conv = tmp / "conv"
        conv.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            cv.split_into_chapters(ft, conv, N_PAGES, pdf_path=str(pdf_root / "BookA.pdf"))
        run(corpus, out, [pdf_root])
        a = {p.name: p.read_bytes() for p in conv.glob("ch*.md")}
        b = {p.name: p.read_bytes() for p in (out / "BookA").glob("ch*.md")}
        check("resplit split == conversion split", a == b and len(a) > 2, (sorted(a), sorted(b)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unsafe_and_missing_pdf():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text(),
                                                      "ch01_Old.md": "<!-- page 1 -->\nold text"}}, {})
        for bad in (corpus, corpus / "sub", tmp, pdf_root / "o"):
            code, _ = run(corpus, bad, [pdf_root])
            check(f"unsafe --out {bad.name} → exit 2", code == 2)
        check("nothing written for unsafe out", not (corpus / "sub").exists() and not (pdf_root / "o").exists())
        code, log = run(corpus, out, [pdf_root])
        m = manifest(out, "BookA")
        rep = json.loads((out / "_resplit_report.json").read_text(encoding="utf-8"))
        check("no pdf → existing split carried", m["split_method"] == "carried"
              and rep["BookA"]["units"][0]["reason"] == "no pdf", rep["BookA"]["units"])
        check("carried file kept", (out / "BookA" / "ch01_Old.md").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_anchoring_config():
    tmp = Path(tempfile.mkdtemp())
    try:
        broken = [[e[0], e[1], 1] for e in GOOD_TOC]
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()}}, {"BookA": broken})
        code, _ = run(corpus, out, [pdf_root])
        v = sm.validate_book(out / "BookA")
        check("broken outline fails validation without config", not v["ok"])
        (out / "_split_config.json").write_text(json.dumps({"BookA": {"anchor_titles": True}}),
                                                encoding="utf-8")
        run(corpus, out, [pdf_root], force=True)
        m = manifest(out, "BookA")
        starts = {f["chapter_number"]: f["pdf_page_start"] for f in m["files"] if f["chapter_number"]}
        check("anchored chapter pages", starts == {1: 3, 2: 6, 3: 9}, starts)
        check("split_method config", m["split_method"] == "config")
        check("anchored book validates", sm.validate_book(out / "BookA")["ok"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconstruct_and_overrides():
    tmp = Path(tempfile.mkdtemp())
    try:
        ft = make_full_text()
        split = tmp / "split"
        split.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            cv.split_by_bookmarks(ft, split, [[1, "Preface", 1], [1, "Section A", 3], [1, "Section B", 9]])
        files = {p.name: p.read_text(encoding="utf-8") for p in split.glob("ch*.md")}
        corpus, pdf_root, out = build(tmp, {"BookR": files, "BookS": dict(files)}, {"BookR": GOOD_TOC})
        (out).mkdir()
        (out / "_split_config.json").write_text(json.dumps({
            "BookR": {"method": "reconstruct", "reason": "test"},
            "BookS": {"chapter_number_overrides": {"ch03_Section_B.md": 7}}}), encoding="utf-8")
        run(corpus, out, [pdf_root])
        m = manifest(out, "BookR")
        nums = sorted(f["chapter_number"] for f in m["files"] if f["chapter_number"])
        check("reconstruct → resplit by the PDF outline", m["split_method"] == "bookmarks" and nums == [1, 2, 3],
              (m["split_method"], nums))
        check("reconstruct writes no full_text", not (out / "BookR" / "full_text.md").exists())
        check("reconstructed book validates", sm.validate_book(out / "BookR")["ok"])
        parts = sorted((corpus / "BookR").glob("ch*.md"), key=lambda p: int(p.name[2:4]))
        joined = "\n".join(t for t in (p.read_text(encoding="utf-8") for p in parts) if sm.has_text(t))
        got = rs.reconstruct_full_text(corpus / "BookR", parts)
        check("reconstruct without duplicates == plain join", got == joined)
        # the old splitter wrote page 9 into two files: identical copy → kept once
        p9 = rs._page_chunks(ft)
        page9 = "\n".join(next(c for pg, c in p9 if pg == 9))
        dup = corpus / "BookR" / "ch09_Dup.md"
        dup.write_text(page9 + "\n" + "\n".join(next(c for pg, c in p9 if pg == 10)), encoding="utf-8")
        got = rs.reconstruct_full_text(corpus / "BookR", parts + [dup])
        check("identical duplicate page kept once",
              sm.page_markers(got).count(9) == 1 and sm.strictly_increasing(sm.page_markers(got)),
              sm.page_markers(got))
        dup.unlink()
        # an excluded duplicate (no page markers) is not stitched into the reconstructed text
        (corpus / "BookR" / "ch08_Old_Whole_Book.md").write_text("old whole-book copy", encoding="utf-8")
        (out / "_split_config.json").write_text(json.dumps({"BookR": {
            "method": "reconstruct", "exclude_files": ["ch08_Old_Whole_Book.md"], "reason": "t"}}),
            encoding="utf-8")
        run(corpus, out, [pdf_root], books=["BookR"], force=True)
        allt = "".join(p.read_text(encoding="utf-8") for p in (out / "BookR").glob("*.md"))
        check("reconstruct honours exclude_files", "old whole-book copy" not in allt)
        (corpus / "BookR" / "ch08_Old_Whole_Book.md").unlink()
        ms = manifest(out, "BookS")
        got = {f["file"]: f["chapter_number"] for f in ms["files"]}
        check("chapter_number_overrides applied", got.get("ch03_Section_B.md") == 7, got)
        # stale duplicate pages → reconstruct refused, book reported as error
        (corpus / "BookR" / "ch09_Stale.md").write_text("<!-- page 4 -->\nold", encoding="utf-8")
        _, log = run(corpus, out, [pdf_root], force=True)
        check("non-monotonic pages refuse reconstruct", "BookR: error" in log or "reconstruct refused" in log, log)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- deep headings ---------------------------------------------------------------------------

def test_deep_headings():
    ft = make_full_text()
    toc = [[1, "Preface", 1], [2, "Chapter 1 Gait", 3], [3, "Overview", 3], [3, "Missing Title", 4],
           [2, "Chapter 2 Balance", 6], [4, "Overview", 6], [2, "Chapter 3 Spasticity", 9]]
    outs = {}
    for flag in (None, "0", "1"):
        d = Path(tempfile.mkdtemp())
        os.environ.pop("T2N_SPLIT_DEEP_HEADINGS", None)
        if flag is not None:
            os.environ["T2N_SPLIT_DEEP_HEADINGS"] = flag
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cv.split_by_bookmarks(ft, d, toc)
        outs[flag] = ({p.name: p.read_text(encoding="utf-8") for p in d.glob("ch*.md")}, buf.getvalue())
        shutil.rmtree(d)
    os.environ.pop("T2N_SPLIT_DEEP_HEADINGS", None)
    check("flag unset == flag 0", outs[None][0] == outs["0"][0])
    on = outs["1"][0]
    ch2 = next(v for k, v in on.items() if "Gait" in k)
    check("level 3 → '## ' above the matching line", "## Overview\nOverview" in ch2, ch2[:200])
    check("original line kept", ch2.count("\nOverview") == 1)
    check("unmatched title not inserted", "Missing Title" not in ch2)
    ch3 = next(v for k, v in on.items() if "Balance" in k)
    check("level 4 → '### '", "### Overview\nOverview" in ch3)
    check("summary line", "deep headings: 2/3 inserted" in outs["1"][1], outs["1"][1])
    removed = {k: "\n".join(l for l in v.split("\n") if not l.startswith(("## ", "### "))) for k, v in on.items()}
    check("only heading lines added", removed == outs[None][0])


# --- validator negatives ---------------------------------------------------------------------

def test_config_toc():
    tmp = Path(tempfile.mkdtemp())
    try:
        # PDF without any outline
        corpus, pdf_root, out = build(tmp, {"BookT": {"full_text.md": make_full_text()},
                                            "BookU": {"full_text.md": make_full_text()}},
                                      {"BookT": [], "BookU": []})
        out.mkdir()
        (out / "_split_config.json").write_text(json.dumps({
            "BookT": {"toc": [[1, "Chapter 1 Gait", 3], [1, "Chapter 2 Balance", 6],
                              [1, "Chapter 3 Spasticity", 9]], "reason": "t"},
            "BookU": {"toc": [[1, "Chapter 1 Gait", None], [1, "Chapter 2 Balance", None],
                              [1, "Chapter 3 Spasticity", None]], "anchor_titles": True, "reason": "t"}}),
            encoding="utf-8")
        run(corpus, out, [pdf_root])
        for b in ("BookT", "BookU"):
            m = manifest(out, b)
            starts = {f["chapter_number"]: f["pdf_page_start"] for f in m["files"] if f["chapter_number"]}
            check(f"{b}: config toc splits at 3/6/9", starts == {1: 3, 2: 6, 3: 9} and
                  m["split_method"] == "config", (m["split_method"], starts))
            check(f"{b}: validates", sm.validate_book(out / b)["ok"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_override_reclassifies_front():
    tmp = Path(tempfile.mkdtemp())
    try:
        toc = [list(e) for e in GOOD_TOC]
        toc[2][1] = "Gait"  # chapter 1 printed without its number in the outline
        corpus, pdf_root, out = build(tmp, {"BookO": {"full_text.md": make_full_text()}}, {"BookO": toc})
        out.mkdir()
        (out / "_split_config.json").write_text(json.dumps(
            {"BookO": {"chapter_level": 2, "reason": "test"}}), encoding="utf-8")
        run(corpus, out, [pdf_root])
        gait = next(f for f in manifest(out, "BookO")["files"] if f["title"] == "Gait")
        check("unnumbered chapter 1 starts as front", gait["kind"] == "front", gait)
        (out / "_split_config.json").write_text(json.dumps(
            {"BookO": {"chapter_level": 2, "chapter_number_overrides": {gait["file"]: 1}, "reason": "test"}}), encoding="utf-8")
        run(corpus, out, [pdf_root], force=True)
        g2 = next(f for f in manifest(out, "BookO")["files"] if f["file"] == gait["file"])
        check("override → numbered chapter is body", g2["kind"] == "body" and g2["chapter_number"] == 1, g2)
        pre = next(f for f in manifest(out, "BookO")["files"] if f["title"] == "Preface")
        check("front matter before it stays front", pre["kind"] == "front", pre)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_underscore_book_config():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"_doc": "notes", "_Guidelines": {"reason": "x"}}), encoding="utf-8")
        got = rs.load_json(cfg)
        check("'_Book' config entry kept, '_doc' dropped", list(got) == ["_Guidelines"], got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dot_folders_skipped():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()},
                                            ".drafts": {"draft.md": "# a draft note\n\ntext\n"}},
                                      {"BookA": GOOD_TOC})
        run(corpus, out, [pdf_root])
        rep = json.loads((out / rs.REPORT_NAME).read_text(encoding="utf-8"))
        check("dot folder not resplit", ".drafts" not in rep and not (out / ".drafts").exists(), list(rep))
        check("dot folder not validated", ".drafts" not in [d.name for d in sm.book_dirs(corpus)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_validator_negatives():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()}}, {"BookA": GOOD_TOC})
        run(corpus, out, [pdf_root])
        book = out / "BookA"
        good = manifest(out, "BookA")

        def with_change(fn, extra=None):
            m = json.loads(json.dumps(good))
            fn(m)
            if extra:
                extra()
            r = sm.validate_book(book, m)
            return {e["check"] for e in r["errors"]}

        (book / "ch99_Empty.md").write_text("<!-- page 12 -->\n", encoding="utf-8")
        got = with_change(lambda m: m["files"].append(dict(m["files"][-1], file="ch99_Empty.md", seq=99)))
        check("marker-only file → empty_file", "empty_file" in got, got)
        got = with_change(lambda m: None)
        check("unlisted md → unlisted_file", "unlisted_file" in got, got)
        (book / "ch99_Empty.md").unlink()

        def swap(m):
            nums = [f for f in m["files"] if f["chapter_number"]]
            nums[0]["chapter_number"], nums[1]["chapter_number"] = 2, 1
        check("chapter going back → chapter_order", "chapter_order" in with_change(swap))

        def gap(m):
            for f in m["files"]:
                if f["chapter_number"] == 3:
                    f["chapter_number"] = 4
        check("missing number → chapter_gap", "chapter_gap" in with_change(gap))
        check("toc count mismatch", "toc_count" in with_change(lambda m: m.update(toc_chapters=5)))

        def retitle(m):
            for f in m["files"]:
                if f["chapter_number"] == 2:
                    f["title"] = "Chapter 2 Cardiology Of Elephants"
        check("wrong title → title_mismatch", "title_mismatch" in with_change(retitle))

        def retitle_skipped(m):
            retitle(m)
            f = next(f for f in m["files"] if f["chapter_number"] == 2)
            m["title_check_skip"] = {f["file"]: "title only on an image page"}
        check("title_check_skip → no title_mismatch",
              "title_mismatch" not in with_change(retitle_skipped))
        m2 = json.loads(json.dumps(good))
        retitle_skipped(m2)
        w = {x["check"] for x in sm.validate_book(book, m2)["warnings"]}
        check("title_check_skip → warning kept", "title_check_skipped" in w, w)

        def same_start(m):  # two files claim one page sequence → overlap
            a, b = [f for f in m["files"] if f["chapter_number"] in (1, 2)]
            b["pdf_page_start"], b["pdf_page_end"] = a["pdf_page_start"], a["pdf_page_end"]
        two = [f for f in good["files"] if f["chapter_number"] in (1, 2)]
        (book / "ch98_Dup.md").write_text((book / two[0]["file"]).read_text(encoding="utf-8"),
                                          encoding="utf-8")

        def dup_file(unit):
            def fn(m):
                d = dict(two[0], file="ch98_Dup.md", seq=two[0]["seq"] + 0.5, chapter_number=None)
                if unit:
                    d["page_unit"] = "ch98_Dup.md"
                m["files"].append(d)
            return fn
        check("same pages, same unit → page_overlap", "page_overlap" in with_change(dup_file(False)))
        check("own page_unit → no page_overlap", "page_overlap" not in with_change(dup_file(True)))
        (book / "ch98_Dup.md").unlink()

        def overlap(m):
            f = next(f for f in m["files"] if f["chapter_number"] == 2)
            f["pdf_page_start"] -= 1
        check("range not matching markers → page_range", "page_range" in with_change(overlap))
        check("unchanged manifest passes", sm.validate_book(book)["ok"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cross_level_chapters():
    tmp = Path(tempfile.mkdtemp())
    try:
        deep = [[1, "Part I", 3], [2, "Chapter 1 Gait", 3], [2, "Chapter 2 Balance", 6],
                [3, "Section X", 9], [4, "Chapter 3 Spasticity", 9], [5, "Overview", 9], [1, "Index", 12]]
        shallow = [[1, "Chapter 1 Gait", 3], [2, "Overview", 3], [2, "More", 4], [1, "Part I", 5],
                   [2, "Chapter 2 Balance", 6], [2, "Chapter 3 Spasticity", 9]]
        taken = [[1, "Part I", 3], [2, "Chapter 1 Gait", 3], [2, "Chapter 2 Balance", 6],
                 [3, "Chapter 2 summary", 7], [2, "Chapter 3 Spasticity", 9]]
        books = {b: {"full_text.md": make_full_text()} for b in ("Deep", "Shallow", "Taken", "Off")}
        corpus, pdf_root, out = build(tmp, books, {"Deep": deep, "Shallow": shallow, "Taken": taken,
                                                   "Off": deep})
        out.mkdir()
        (out / "_split_config.json").write_text(json.dumps({
            "Deep": {"chapter_level": 2, "reason": "fixture has only 3 chapters (auto needs 3 at one level)"},
            "Shallow": {"chapter_level": 2, "reason": "t"},
            "Off": {"chapter_level": 2, "promote_chapters": False, "reason": "t"}}), encoding="utf-8")
        run(corpus, out, [pdf_root])
        m = manifest(out, "Deep")
        by = {f["chapter_number"]: f for f in m["files"] if f["chapter_number"]}
        check("deep: chapter 3 file starts p9", by.get(3, {}).get("pdf_page_start") == 9, by)
        check("deep: chapter 2 ends p8", by.get(2, {}).get("pdf_page_end") == 8, by)
        lv = [s["level"] for s in m["sections"] if s["title"].startswith("Chapter 3")]
        check("deep: sections keep level 4", lv == [4], lv)
        check("deep: validates", sm.validate_book(out / "Deep")["ok"], sm.validate_book(out / "Deep")["errors"])
        rep = json.loads((out / "_resplit_report.json").read_text(encoding="utf-8"))
        check("deep: report counts promotion", "promoted_chapters" in json.dumps(rep["Deep"]), rep["Deep"])
        m = manifest(out, "Shallow")
        one = [f for f in m["files"] if f["pdf_page_start"] in (3, 4)]
        check("shallow: pages 3-4 one body file numbered 1",
              len(one) == 1 and one[0]["chapter_number"] == 1 and one[0]["kind"] == "body", one)
        m = manifest(out, "Taken")
        by = {f["chapter_number"]: f for f in m["files"] if f["chapter_number"]}
        check("taken: nothing promoted", by.get(2, {}).get("pdf_page_end") == 8 and
              not any(f["pdf_page_start"] == 7 for f in m["files"]), m["files"])
        m = manifest(out, "Off")
        check("off: chapter 3 stays inside chapter 2",
              3 not in {f["chapter_number"] for f in m["files"]}, m["files"])
        v = sm.validate_book(out / "Off")
        check("off: validator says chapter_unmapped",
              any(e["check"] == "chapter_unmapped" for e in v["errors"]), v["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gap_chapters():
    gap = sm.promote_gap_chapters
    # Ropper shape: 3-4 one Section deeper between chapter-level 2 and 5
    e = [(2, "1 Approach", 14), (2, "2 Testing", 24), (2, "SECTION 1 Motility", 64),
         (3, "3 Paralysis", 65), (4, "Overview", 65), (3, "4 Movement", 81),
         (1, "PART 3", 590), (2, "5 Development", 592)]
    lv, n = gap(e, [x[0] for x in e], 2)
    check("gap: 3 and 4 promoted", n == 2 and lv == [2, 2, 2, 2, 3, 2, 1, 2], (n, lv))
    sub = [(2, "1 Gait", 3), (3, "1 Intro", 4), (3, "2 Methods", 5), (2, "3 Spasticity", 9)]
    check("gap: subsections 1,2 inside a 1→3 gap stay", gap(sub, [x[0] for x in sub], 2)[1] == 0)
    part = [(2, "1 Gait", 3), (3, "2 Balance", 6), (3, "4 Extra", 7), (2, "3 Spasticity", 9)]
    check("gap: run that overshoots the gap stays", gap(part, [x[0] for x in part], 2)[1] == 0)
    back = [(2, "1 Gait", 5), (3, "2 Balance", 3), (2, "3 Spasticity", 9)]
    check("gap: page before the previous chapter stays", gap(back, [x[0] for x in back], 2)[1] == 0)
    check("gap: no chapter level → nothing", gap(e, [x[0] for x in e], None)[1] == 0)

    tmp = Path(tempfile.mkdtemp())
    try:
        toc = [[1, "Part I", 3], [2, "1 Gait", 3], [2, "Section A", 5], [3, "2 Balance", 6],
               [4, "Overview", 6], [2, "3 Spasticity", 9], [1, "Index", 12]]
        books = {b: {"full_text.md": make_full_text()} for b in ("Gap", "Hand")}
        corpus, pdf_root, out = build(tmp, books, {"Gap": toc, "Hand": toc})
        out.mkdir()
        (out / "_split_config.json").write_text(json.dumps({
            "Gap": {"chapter_level": 2, "reason": "fixture has only 2 chapter-level numbers"},
            "Hand": {"chapter_level": 2, "chapter_number_overrides": {"no_such_file.md": 9},
                     "reason": "t"}}),
            encoding="utf-8")
        run(corpus, out, [pdf_root])
        by = {f["chapter_number"]: f for f in manifest(out, "Gap")["files"] if f["chapter_number"]}
        check("gap resplit: chapter 2 file starts p6", by.get(2, {}).get("pdf_page_start") == 6, by)
        check("gap resplit: chapter 1 ends before Section A",
              by.get(1, {}).get("pdf_page_end") == 4, by)
        check("gap resplit: validates", sm.validate_book(out / "Gap")["ok"],
              sm.validate_book(out / "Gap")["errors"])
        rep = json.loads((out / "_resplit_report.json").read_text(encoding="utf-8"))
        check("gap resplit: report counts promotion", "promoted_chapters" in json.dumps(rep["Gap"]))
        check("gap resplit: overrides key → never promoted",
              "promoted_chapters" not in json.dumps(rep["Hand"]), rep["Hand"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_force_keeps_figure_dirs():
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus, pdf_root, out = build(tmp, {"BookA": {"full_text.md": make_full_text()}},
                                      {"BookA": [[1, "Chapter 1 Gait", 3], [1, "Chapter 2 Balance", 6],
                                                 [1, "Chapter 3 Spasticity", 9]]})
        run(corpus, out, [pdf_root])
        target = tmp / "converted_figures"
        (target / "sub").mkdir(parents=True)
        (target / "sub" / "fig1.png").write_bytes(b"png")
        link = out / "BookA" / "figures"
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, check=True)
        else:
            os.symlink(target, link, target_is_directory=True)
        real = out / "BookA" / "images"
        real.mkdir()
        (real / "only_copy.png").write_bytes(b"img")
        stray = out / "BookA" / "notes_by_hand"
        stray.mkdir()
        code, log = run(corpus, out, [pdf_root], force=True)
        check("force keep: run ok", code == 0, log)
        check("force keep: figures link still a link",
              rs._is_dir_link(link) and (link / "sub" / "fig1.png").read_bytes() == b"png")
        check("force keep: link target untouched", (target / "sub" / "fig1.png").exists())
        check("force keep: real images folder kept", (real / "only_copy.png").read_bytes() == b"img")
        check("force keep: other folders still rebuilt", not stray.exists())
        check("force keep: no .tmp left", not any(p.name.startswith(".tmp-") for p in out.iterdir()))
        rep = json.loads((out / "_resplit_report.json").read_text(encoding="utf-8"))
        check("force keep: report lists kept dirs",
              rep["BookA"].get("kept_dirs") == ["figures", "images"], rep["BookA"].get("kept_dirs"))
        check("force keep: still validates", sm.validate_book(out / "BookA")["ok"],
              sm.validate_book(out / "BookA")["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_helpers, test_page_labels_and_writeback, test_resplit_normal, test_same_as_conversion,
              test_unsafe_and_missing_pdf, test_anchoring_config, test_reconstruct_and_overrides,
              test_deep_headings, test_dot_folders_skipped, test_underscore_book_config, test_override_reclassifies_front, test_config_toc,
              test_validator_negatives, test_cross_level_chapters, test_gap_chapters,
              test_force_keeps_figure_dirs):
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
