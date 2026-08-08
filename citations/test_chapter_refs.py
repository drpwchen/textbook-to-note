#!/usr/bin/env python3
"""test_chapter_refs.py — regression guard for the chapter-reference lint.

Run after ANY edit to textbook_chapter_index.py or textbook_ref_lint.py.
Fully hermetic: builds a fake corpus in a temp dir, so it needs no textbooks
and no network. `python test_chapter_refs.py`.

Locks the invariants that were each a real bug caught on a live corpus:

  1. All six filename conventions map to the right chapter numbers.
  2. "<part>_<chapter>_Title.md" is read as part+chapter, not chapter+title —
     ranking strategies by "most matches" gets this backwards and mislabels
     every chapter in the book.
  3. "Ch01" and "Ch.1" are the same chapter (leading zero).
  4. A possessive suffix is stripped, a real trailing 's' is not (Helms).
  5. One name, several books, different chapter N → AMBIGUOUS, not a silent
     pick. This is the failure the lint exists for.
  6. A pinned default edition resolves the ambiguity.
  7. A chapter past the end of a FULLY converted book → BAD_CHAPTER.
  8. The same gap in a PARTLY converted book → UNVERIFIABLE, never BAD_CHAPTER.
  9. A file-sequence number is reported as FILE_INDEX with the chapter it
     really belongs to, ordered numerically (ch100 must not sort before ch11).
 10. Two inline-code references on one line stay independent.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import textbook_chapter_index as tci  # noqa: E402
import textbook_ref_lint as trl  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        FAILURES.append(name)


def touch(d: Path, *names: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("x", encoding="utf-8")


def build_corpus(root: Path) -> dict:
    # A: file index IS the chapter number
    touch(root / "Alpha_Handbook_7e_2021",
          "00_Front Matter.md", "Ch01_Beginnings.md", "Ch02_Middles.md", "Ch03_Ends.md")
    # B: ch<seq>_<chapter>_Title
    touch(root / "Beta_Manual_2e_2019",
          "ch00_Front_Matter.md", "ch06_1_Physics.md", "ch07_2_Anatomy.md", "ch08_3_Pathology.md")
    # C: stub file carries the number, the next file carries the title
    touch(root / "Gamma_Atlas_1e_2015",
          "ch00_Front_Matter.md", "ch05_Chapter1.md", "ch06_Foundations.md",
          "ch07_Chapter2.md", "ch08_Techniques.md", "ch09_Chapter3.md", "ch10_Findings.md")
    # D: <chapter>.Title
    touch(root / "Delta_Text_6e_2015",
          "0_Contents.md", "1.Introduction.md", "2.Methods.md", "3.Results.md")
    # E: <part>_<chapter>_Title — the trap: D also matches these filenames
    touch(root / "Epsilon_System_3e_2013",
          "01_1_Pain.md", "01_2_Pressure.md", "02_8_Mechanical_disorders.md",
          "02_9_Whiplash.md", "03_60_Applied_anatomy.md")
    # F: one sub-directory per chapter
    for sub in ("000_Front_Matter", "001_Joints", "002_Synovium", "003_Cartilage"):
        (root / "Zeta_Reference_11e_2021" / sub).mkdir(parents=True, exist_ok=True)
        (root / "Zeta_Reference_11e_2021" / sub / "full_text.md").write_text("x", encoding="utf-8")
    # Same surname, two different books, same chapter number, different content
    touch(root / "Twin_Rheumatology_1e_2015",
          "ch05_Chapter1.md", "ch06_Basics.md", "ch07_Chapter5.md", "ch08_Psoriatic_Arthritis.md",
          "ch09_Chapter6.md", "ch10_Osteoarthritis.md")
    touch(root / "Twin_Pediatrics_2e_2019",
          "ch06_1_Technique.md", "ch07_5_Tissue_Spectrum.md", "ch08_6_Upper_Limb.md")
    # Fully converted (chapters 1..4, no gaps) but sliced into many files, so a
    # file-sequence number looks like a chapter number. ch11 and ch100 both
    # exist: a lexical sort puts ch100 first and would answer "no owner" for
    # ch11, so this fixture separates numeric from lexical ordering.
    touch(root / "Sigma_Review_4e_2020",
          "ch00_Front_Matter.md", "ch09_1_Stroke.md", "ch11_Aphasia.md",
          "ch50_2_Spinal_Cord_Injuries.md", "ch95_3_Pain.md", "ch100_4_Rehabilitation.md")
    # Partly converted: chapters 1, 2, 40, 41 exist → coverage 4/41, gaps
    # everywhere. File sequence 7 exists too, so a cited "Ch.7" collides with
    # it by accident — the coverage threshold is what stops that from being
    # reported as a file-sequence citation when it is more likely a real
    # chapter nobody converted.
    touch(root / "Omega_Essentials_4e_2019",
          "ch01_1_Alpha.md", "ch02_2_Beta.md", "ch03_40_Omega.md", "ch07_41_Zeta.md")
    # Not chapter-structured at all
    touch(root / "Kappa_Sliced_1e_2008", "pages_0001-0030.md", "pages_0031-0060.md")

    idx = tci.build(root)
    tci.index_path(root).write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    (root / "_chapter_index.aliases.json").write_text(
        json.dumps({"_comment": "cited name → folder prefix", "series": "Epsilon_"}), encoding="utf-8")
    return idx


def verdict_of(ref: str, root: Path, index: dict) -> tuple[str, str]:
    aliases, defaults = tci.load_aliases(root), trl.load_defaults(root)
    (names, ch, ed, _), = trl.scan_text(ref)
    v, detail, _ = trl.check_ref(names, ch, ed, index, root, aliases, defaults)
    return v, detail


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chapref-"))
    try:
        root = tmp / "corpus"
        index = build_corpus(root)
        books = index["books"]

        print("1. filename conventions")
        check("A: file index is the chapter", books["Alpha_Handbook_7e_2021"]["chapters"]["2"], "Middles")
        check("B: inner number", books["Beta_Manual_2e_2019"]["chapters"]["2"], "Anatomy")
        check("C: title from the next file", books["Gamma_Atlas_1e_2015"]["chapters"]["2"], "Techniques")
        check("D: <chapter>.Title", books["Delta_Text_6e_2015"]["chapters"]["3"], "Results")
        check("F: one dir per chapter", books["Zeta_Reference_11e_2021"]["chapters"]["2"], "Synovium")
        check("no chapter structure", books["Kappa_Sliced_1e_2008"]["strategy"], "none")

        print("\n2. part_chapter is not chapter_title")
        eps = books["Epsilon_System_3e_2013"]
        check("strategy E wins over D", eps["strategy"], "E")
        check("Ch.8 is the chapter, not part 02", eps["chapters"]["8"], "Mechanical disorders")
        check("part numbers are not chapters", "60" in eps["chapters"], True)

        print("\n3. leading zero")
        check("Ch01 == Ch.1", verdict_of("Alpha Ch01", root, index)[0], "OK")

        print("\n4. possessive vs real trailing s")
        check("Zeta's → Zeta", tci.resolve("Zeta's", index, {}), ["Zeta_Reference_11e_2021"])
        check("Alphas is not Alpha", tci.resolve("Alphas", index, {}), [])
        check("alias resolves", tci.resolve("Series", index, tci.load_aliases(root)),
              ["Epsilon_System_3e_2013"])

        print("\n5. one name, two books")
        v, detail = verdict_of("Twin Ch.5", root, index)
        check("verdict", v, "AMBIGUOUS")
        check("both titles shown", "Psoriatic Arthritis" in detail and "Tissue Spectrum" in detail, True)

        print("\n6. pinned default resolves it")
        (root / "_chapter_index.defaults.json").write_text(
            json.dumps({"twin": "Twin_Pediatrics_2e_2019"}), encoding="utf-8")
        v, detail = verdict_of("Twin Ch.5", root, index)
        check("verdict", v, "OK")
        check("uses the pinned book", "Tissue Spectrum" in detail, True)
        (root / "_chapter_index.defaults.json").unlink()

        print("\n7/8. past the end vs a gap")
        check("fully converted → BAD_CHAPTER", verdict_of("Delta Ch.99", root, index)[0], "BAD_CHAPTER")
        v, detail = verdict_of("Omega Ch.7", root, index)
        # File sequence 7 exists in this book. Reporting FILE_INDEX here would
        # be a guess dressed as a finding — the book is 10% converted.
        check("partly converted → UNVERIFIABLE", v, "UNVERIFIABLE")
        check("says why", "not converted" in detail, True)

        print("\n9. file sequence, numerically ordered")
        v, detail = verdict_of("Sigma Ch.11", root, index)
        check("verdict", v, "FILE_INDEX")
        check("names the file it means", "ch11_Aphasia" in detail, True)
        # A lexical sort visits ch100 first and answers "no owner" here.
        check("owning chapter found numerically", "Ch.1 Stroke" in detail, True)
        check("a real chapter still resolves", verdict_of("Sigma Ch.2", root, index)[0], "OK")

        print("\n10. two references on one line")
        refs = trl.scan_text("- claim (`Alpha Ch.1` `Beta Ch.2`)")
        check("two independent refs", len(refs), 2)
        check("second does not inherit the first", refs[1][0][0], "Beta")

        print("\n11. exit codes")
        check("clean → 0", trl.lint(refs=["Alpha Ch.1"], textbook_dir=str(root), quiet=True), 0)
        check("offender → 1", trl.lint(refs=["Delta Ch.99"], textbook_dir=str(root), quiet=True), 1)
        check("unverifiable → 3", trl.lint(refs=["Nobody Ch.1"], textbook_dir=str(root), quiet=True), 3)
        check("no index → 3", trl.lint(refs=["Alpha Ch.1"], textbook_dir=str(tmp / "nope"), quiet=True), 3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("all chapter-reference checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
