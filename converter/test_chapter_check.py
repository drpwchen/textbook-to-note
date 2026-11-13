"""test_chapter_check.py — chapter_check verdicts and the post_convert report (openspec change
split-acceptance-gate). Synthetic books only.

  python converter/test_chapter_check.py   → exit 0 all pass · exit 1 any FAIL
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import chapter_check as cc  # noqa: E402
import post_convert as pc  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def make_book(root: Path, name: str, files: list[tuple[str, int, str, str]]) -> Path:
    """files: (file name, chapter_number, title, opening text)."""
    d = root / name
    d.mkdir(parents=True)
    entries = []
    for i, (fn, n, title, body) in enumerate(files):
        (d / fn).write_text(f"<!-- page {i + 1} -->\n{body}\nSome body text about the topic.\n", encoding="utf-8")
        entries.append({"file": fn, "chapter_number": n, "title": title, "kind": "body", "seq": i})
    (d / "_manifest.json").write_text(json.dumps({"book": name, "files": entries}), encoding="utf-8")
    return d


def test_off_by_constant():
    tmp = Path(tempfile.mkdtemp())
    try:
        d = make_book(tmp, "Off", [("ch01.md", 8, "Gait", "Chapter 1\nGait"),
                                   ("ch02.md", 9, "Balance", "Chapter 2\nBalance"),
                                   ("ch03.md", 10, "Spasticity", "Chapter 3\nSpasticity")])
        r = cc.check_book(d)
        check("off: all contradicted", [x["verdict"] for x in r["rows"]] == ["contradicted"] * 3, r["rows"])
        check("off: suspect", r["suspect"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ret = pc.check_chapters(tmp, ["Off"])
        check("off: [WARN] printed, nothing returned", "[WARN] Off" in out.getvalue() and ret is None,
              out.getvalue())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_correct_numbers():
    tmp = Path(tempfile.mkdtemp())
    try:
        d = make_book(tmp, "Good", [("ch04.md", 4, "Amputation", "CHAPTER 4\nAmputation"),
                                    ("ch05.md", 5, "Spinal Cord Injury", "5\nSpinal Cord Injury"),
                                    ("ch06.md", 6, "Gait Training", "6 Gait Training")])
        r = cc.check_book(d)
        check("good: all confirmed", [x["verdict"] for x in r["rows"]] == ["confirmed"] * 3, r["rows"])
        check("good: not suspect", not r["suspect"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pc.check_chapters(tmp, ["Good"])
        check("good: counts line, no WARN",
              "chapter check Good: confirmed 3, unknown 0, contradicted 0" in out.getvalue()
              and "[WARN]" not in out.getvalue(), out.getvalue())
        check("cross-reference alone is contradicted, not a WARN for the book",
              cc.file_verdict("<!-- page 1 -->\nIntro text, see Chapter 17.\n", 4, "Intro")["verdict"]
              == "contradicted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli():
    tmp = Path(tempfile.mkdtemp())
    try:
        make_book(tmp, "Off", [("ch01.md", 8, "Gait", "Chapter 1\nGait")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cc.main([str(tmp), "--json", str(tmp / "r.json")])
        data = json.loads((tmp / "r.json").read_text(encoding="utf-8"))
        check("cli: exit 0, SUSPECT line, json counts",
              rc == 0 and "SUSPECT  Off" in out.getvalue() and data["counts"]["contradicted"] == 1,
              (rc, out.getvalue()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_off_by_constant, test_correct_numbers, test_cli):
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
