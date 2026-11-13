"""
Post-conversion: verify, INDEX.md, keep the corpus in step, hand it to ask-your-library.
Run AFTER batch-dir conversion completes.

Usage:
    python post_convert.py          # Full pipeline: verify + INDEX.md + figure registry + resplit + hand-off
    python post_convert.py --verify # Only verify conversion quality
    python post_convert.py --resplit-only [--corpus DIR]  # Only re-split new / re-converted books into DIR
    python post_convert.py --index [--corpus DIR]  # Resplit into DIR, link figures, hand DIR to ask-your-library
    python post_convert.py --backfill [--corpus DIR]  # Only the hand-off (ask-your-library re-indexes + audits)
    python post_convert.py --audit  # Only report index coverage (legacy INDEXER_SCRIPT route only)

Flow: OUTPUT_DIR (converted books) --resplit--> CORPUS_DIR (default ./corpus; a t2n-corpus/1
folder with per-book _manifest.json and a root corpus.json) --ask-your-library corpus.py add-->
searchable. A book is re-split when it is new to CORPUS_DIR or its markdown is newer than its
manifest; its figure folders are linked (not copied) into CORPUS_DIR.

This repo does not index anything. Making a corpus searchable is ask-your-library's job:
    python <ask-your-library>/server/corpus.py add <CORPUS_DIR>
`--index` runs that command when VAULT_SEARCH_DIR points at an ask-your-library checkout
(VAULT_SEARCH_PY = its interpreter, if different). With nothing set it prints the command
and exits 0. INDEXER_SCRIPT (an indexer with an --incremental/--book CLI) still works but
is deprecated; while it is set, the resplit chain is skipped and 0.8.0 behaviour applies.
The figure-registry step is an optional integration too (FIGURE_REGISTRY_SCRIPT).

T2N_REQUIRE_SOURCE_PDF=1 (opt-in, default off) makes the resplit refuse a book whose source PDF
it cannot find without searching: every `full_text.md` must carry a `Source:` line naming an
existing PDF, or a PDF of that file name must sit under T2N_PDF_ROOTS; a book with no
`full_text.md` needs `Source:` lines in its chapter files or a `pdf` entry in
<corpus>/_split_config.json. Such a book is listed under [FAIL] with the fix, and its corpus
copy is left as it was. Unset, a book with no PDF is
resplit by carrying its existing split, as before.

A book the validator fails (`valid: false` in <corpus>/_resplit_report.json) moves to
<corpus>/_held/<book>/ with a _fix_request.md (what failed, which split-config keys, the exact
commands, the done criterion) and is listed under [FAIL]; ask-your-library skips `_`-folders, so
every other book is still handed off. It is re-split once its markdown or the split config
changes, and comes back when it validates. Books resplit in the
run also get a chapter-number check against their own pages (report only, [WARN] when more
files contradict their number than confirm it).

Exit code: 1 (listing what failed) when a resplit, a figure link or the hand-off fails, or a
book fails validation.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from shared.config import (OUTPUT_DIR, CORPUS_DIR, PDF_ROOTS, INDEXER_SCRIPT, VAULT_SEARCH_DIR,
                           VAULT_SEARCH_PY, FIGURE_REGISTRY_SCRIPT)

OUTPUT_BASE = OUTPUT_DIR
PROGRESS_FILE = OUTPUT_BASE / "batch_progress.json"
SKIP_DIRS = {"misc", "scripts", "figures_test", "__pycache__", ".drafts"}
GUIDELINE_DIR = "_Guidelines"
HELD_DIR = "_held"  # <corpus>/_held/<book>: failed validation, out of the index
SOURCE_RE = re.compile(r"^Source: `([^`]+)`", re.M)
FIGURE_BACKUP_MARKERS = (".bak", "undo")


def listed_books(root: Path) -> list[tuple[str, Path]]:
    """(label, folder) for verification and INDEX.md: every top-level book folder, and each
    guideline under _Guidelines as its own entry. Other "_"- and "."-folders are tooling."""
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS:
            continue
        if d.name == GUIDELINE_DIR:
            out += [(f"{GUIDELINE_DIR}/{g.name}", g) for g in sorted(d.iterdir()) if g.is_dir()]
        elif not d.name.startswith(("_", ".")):
            out.append((d.name, d))
    return out


def verify_conversion():
    """Check conversion quality across all books."""
    print("=== Verification ===")

    books = listed_books(OUTPUT_BASE)

    total_books = len(books)
    books_with_pages = 0
    books_without_pages = 0
    epub_books = 0
    total_chapters = 0
    total_fig_refs = 0
    scan_only = []
    no_chapters = []

    for label, d in books:
        md_files = list(d.glob("*.md"))
        if not md_files:
            continue

        has_pages = False
        is_epub = False
        fig_refs = 0
        chapter_files = [f for f in md_files if f.name.startswith("ch") or f.name.startswith("pages_")]

        for f in md_files[:3]:
            content = f.read_text(encoding="utf-8", errors="replace")[:5000]
            if "<!-- page" in content:
                has_pages = True
            if "<!-- SOURCE: epub" in content:
                is_epub = True
            fig_refs += content.count("<!-- REF:")

        if has_pages:
            books_with_pages += 1
        elif is_epub:
            epub_books += 1  # reflowable — no page numbers by design, not a defect
        else:
            books_without_pages += 1

        total_fig_refs += fig_refs

        if len(chapter_files) > 1:
            total_chapters += len(chapter_files)
        elif len(md_files) == 1 and md_files[0].name == "full_text.md":
            # Only full_text.md, no chapters
            content = md_files[0].read_text(encoding="utf-8", errors="replace")
            if len(content) < 500:
                scan_only.append(label)
            else:
                no_chapters.append(label)

    print(f"Total books: {total_books}")
    print(f"With <!-- page N --> markers: {books_with_pages}")
    print(f"epub (no pages by design): {epub_books}")
    print(f"Without page markers (PDF, suspect): {books_without_pages}")
    print(f"Total chapter files: {total_chapters}")
    print(f"Figure refs (sampled): {total_fig_refs}")

    if scan_only:
        print(f"\nScan-only (needs OCR): {len(scan_only)}")
        for name in scan_only[:10]:
            print(f"  {name}")

    if no_chapters:
        print(f"\nNo chapter split: {len(no_chapters)}")
        for name in no_chapters[:10]:
            print(f"  {name}")

    # Check progress file
    if PROGRESS_FILE.exists():
        progress = json.loads(PROGRESS_FILE.read_bytes().decode("utf-8"))
        errors = [e for e in progress.get("errors", []) if not e.startswith("[SCAN]")]
        scans = [e for e in progress.get("errors", []) if e.startswith("[SCAN]")]
        print(f"\nBatch progress: {len(progress['completed'])} completed")
        if errors:
            print(f"Errors: {len(errors)}")
            for e in errors:
                print(f"  {e}")
        if scans:
            print(f"Scanned PDFs skipped: {len(scans)}")

    return books_without_pages == 0


def book_folders(root: Path) -> list[Path]:
    """Top-level folders `--resplit` treats as books (_Guidelines is one of them; other `_`- and
    `.`-folders are tooling)."""
    if not root.is_dir():
        return []
    import split_manifest as sm
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and d.name not in SKIP_DIRS and not sm.is_tooling_dir(d.name))


def _newest_md(d: Path) -> float:
    return max((f.stat().st_mtime for f in d.rglob("*.md")), default=0.0)


def resplit_plan(corpus: Path = None) -> list[tuple[str, bool]]:
    """(book, force) for every OUTPUT_DIR book whose CORPUS_DIR copy is missing or older.
    No corpus manifest -> new book (plain resplit); markdown newer than the manifest ->
    re-converted (force, which rebuilds that book's corpus folder from scratch)."""
    corpus = Path(corpus) if corpus else CORPUS_DIR
    plan = []
    for d in book_folders(OUTPUT_BASE):
        newest = _newest_md(d)
        if not newest:
            continue
        manifest = corpus / d.name / "_manifest.json"
        held = corpus / HELD_DIR / d.name / "_manifest.json"
        if not manifest.exists() and held.exists():
            # held (failed validation): retry only once the markdown or the split config changed
            cfg = corpus / "_split_config.json"
            changed = max(newest, cfg.stat().st_mtime if cfg.exists() else 0.0)
            if changed > held.stat().st_mtime:
                plan.append((d.name, True))
        elif not manifest.exists():
            plan.append((d.name, False))
        elif newest > manifest.stat().st_mtime:
            plan.append((d.name, True))
    return plan


def pdf_roots_for(book: str) -> list[Path]:
    """The folder(s) of the book's `Source:` PDF(s) that still exist; else the PDF_ROOTS that
    exist. Searching only the Source folder keeps a resplit from walking a whole library.
    A book with no `full_text.md` (chapter files only) takes the `Source:` lines of its chapter
    files instead — the PDF a reconstructed or per-chapter-PDF book is split from."""
    roots = []
    files = list((OUTPUT_BASE / book).rglob("full_text.md")) or \
        sorted((OUTPUT_BASE / book).rglob("*.md"))
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            m = SOURCE_RE.search(fh.read(2000))
        if m and Path(m.group(1)).exists():
            parent = Path(m.group(1)).parent
            if parent not in roots:
                roots.append(parent)
    return roots or [r for r in PDF_ROOTS if r.exists()]


def require_source_pdf() -> bool:
    return os.environ.get("T2N_REQUIRE_SOURCE_PDF", "0") == "1"


def configured_pdf(book: str, corpus: Path) -> Path | None:
    """The book's `pdf` entry in <corpus>/_split_config.json, if it names an existing file
    (absolute, or relative to one of T2N_PDF_ROOTS)."""
    cfg_path = Path(corpus) / "_split_config.json" if corpus else None
    if not cfg_path or not cfg_path.exists():
        return None
    try:
        value = (json.loads(cfg_path.read_text(encoding="utf-8")).get(book) or {}).get("pdf")
    except (ValueError, AttributeError):
        return None
    if not value:
        return None
    p = Path(value).expanduser()
    if p.is_absolute():
        return p if p.exists() else None
    for r in PDF_ROOTS:
        if (r / value).exists():
            return r / value
    return None


def unresolved_sources(book: str, roots_index, corpus: Path = None) -> list[str]:
    """Problems that leave `book` without a source PDF unless something is searched: one line
    per `full_text.md` with no `Source:` line, or whose `Source:` PDF is gone and no PDF of that
    file name is under T2N_PDF_ROOTS (`roots_index`: a PdfIndex over those roots, walked only
    if a Source PDF is actually missing). A book with no `full_text.md` at all needs its chapter
    files' own `Source:` lines (one PDF per chapter) or a `pdf` entry in the split config."""
    book_dir = OUTPUT_BASE / book
    if configured_pdf(book, corpus):
        return []
    full_texts = sorted(book_dir.rglob("full_text.md"))
    if not full_texts:
        chapter_sources = []
        for f in sorted(book_dir.rglob("*.md")):
            with open(f, encoding="utf-8", errors="replace") as fh:
                m = SOURCE_RE.search(fh.read(1500))
            if m:
                chapter_sources.append(Path(m.group(1)))
        found = [s for s in chapter_sources
                 if s.exists() or len(roots_index.by_name.get(s.name.lower(), [])) == 1]
        if chapter_sources and len(found) == len(chapter_sources):
            return []
        cfg_file = Path(corpus) / "_split_config.json" if corpus else "<corpus>/_split_config.json"
        return [f"{book} has no full_text.md and no source PDF it can find without searching, so "
                f"it would be carried, not split from its PDF. Fix: add \"pdf\": \"<path to the "
                f"book's PDF>\" to the \"{book}\" entry of {cfg_file} (or resplit it by hand with "
                f"convert.py --resplit ... --pdf-map <json>)"]
    problems = []
    for f in full_texts:
        rel = f.relative_to(OUTPUT_BASE).as_posix()
        with open(f, encoding="utf-8", errors="replace") as fh:
            m = SOURCE_RE.search(fh.read(2000))
        if not m:
            problems.append(f"{rel} has no `Source:` line. Fix: add the line `Source: `<path to the "
                            f"book's PDF>`` at the top of {f}, or re-convert the book")
            continue
        src = Path(m.group(1))
        if src.exists():
            continue
        if len(roots_index.by_name.get(src.name.lower(), [])) == 1:
            continue
        roots = os.pathsep.join(str(r) for r in PDF_ROOTS)
        problems.append(f"{rel}: source PDF not found: {src}. Fix: put the PDF back at that path, "
                        f"or copy {src.name} into one of T2N_PDF_ROOTS ({roots}), or edit the "
                        f"`Source:` line of {f}")
    return problems


def run_resplit(plan, corpus: Path = None) -> list[str]:
    """Re-split each planned book into the corpus. Returns the books that failed (no
    manifest afterwards, or — with T2N_REQUIRE_SOURCE_PDF=1 — no findable source PDF)."""
    corpus = Path(corpus) if corpus else CORPUS_DIR
    print(f"\n=== Resplit into {corpus} ===")
    if not plan:
        print("  ✓ nothing to resplit (every book is already in the corpus and up to date)")
        return []
    import resplit
    failed = []
    walked = {}  # root -> PdfIndex: each PDF root is walked at most once per run, not once per book
    strict = require_source_pdf()
    for book, force in plan:
        print(f"  [resplit] {book}{' (force: re-converted)' if force else ''} ...", flush=True)
        if strict:
            key = os.pathsep.join(str(r) for r in PDF_ROOTS if r.exists())
            if key not in walked:
                walked[key] = resplit.PdfIndex([r for r in PDF_ROOTS if r.exists()])
            problems = unresolved_sources(book, walked[key], corpus)
            if problems:
                for p in problems:
                    print(f"  [FAIL] resplit {book}: {p}", flush=True)
                failed.append(book)
                continue
        try:
            roots = [str(r) for r in pdf_roots_for(book)]
            for r in roots:
                if r not in walked:
                    walked[r] = resplit.PdfIndex([r])
            idx = resplit.PdfIndex.combine(walked[r] for r in roots)
            rc = resplit.run(OUTPUT_BASE, corpus, roots, books=[book], force=force, pdf_index=idx)
        except Exception as e:  # noqa: BLE001 — one book never aborts the chain
            print(f"  [FAIL] resplit {book}: {type(e).__name__}: {e}", flush=True)
            failed.append(book)
            continue
        if rc != 0 or not (corpus / book / "_manifest.json").exists():
            print(f"  [FAIL] resplit {book}: exit {rc}", flush=True)
            failed.append(book)
    return failed


def figure_dirs(book_dir: Path) -> list[Path]:
    """figures* / images folders of a converted book, minus backup copies."""
    return sorted(f for f in book_dir.iterdir()
                  if f.is_dir() and (f.name.startswith("figures") or f.name == "images")
                  and not any(m in f.name for m in FIGURE_BACKUP_MARKERS))


def _is_link(p: Path) -> bool:
    is_junction = getattr(p, "is_junction", None)  # Python 3.12+
    return p.is_symlink() or bool(is_junction and is_junction())


def ensure_figure_links(corpus: Path = None, dry_run: bool = False) -> list[str]:
    """--resplit never copies figures; CORPUS_DIR/<book>/<figures*> links to the OUTPUT_DIR
    folder instead (one copy of the images). A junction on Windows (no privilege needed), a
    symlink elsewhere. A --force resplit moves existing links into the rebuilt book folder
    (0.8.3); this only creates the missing ones. Returns the links that could not be created."""
    corpus = Path(corpus) if corpus else CORPUS_DIR
    print(f"\n=== Figure links ({corpus} -> {OUTPUT_BASE}) ===")
    made, failed = 0, []
    for d in book_folders(OUTPUT_BASE):
        corpus_book = corpus / d.name
        if not corpus_book.is_dir():
            continue
        for f in figure_dirs(d):
            link = corpus_book / f.name
            if link.exists() or _is_link(link):
                continue
            if dry_run:
                print(f"  [dry-run] {d.name}/{f.name}")
                made += 1
                continue
            r = None
            try:
                if os.name == "nt":
                    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(f)],
                                       capture_output=True, text=True)
                    ok = r.returncode == 0 and link.exists()
                else:
                    os.symlink(f, link, target_is_directory=True)
                    ok = link.is_symlink()
            except OSError as e:
                ok, r = False, None
                print(f"  [FAIL] {d.name}/{f.name}: {e}")
            if not ok:
                failed.append(f"{d.name}/{f.name}")
                if r is not None:
                    print(f"  [FAIL] {d.name}/{f.name}: {(r.stderr or r.stdout).strip()[:120]}")
            else:
                made += 1
    print(f"  {'would create' if dry_run else 'created'} {made}, failed {len(failed)}")
    return failed


def refresh_corpus(corpus: Path) -> list[str]:
    """resplit_plan -> run_resplit -> ensure_figure_links -> chapter check of this run's books
    -> held books. Returns failure labels."""
    plan = resplit_plan(corpus)
    failed = run_resplit(plan, corpus)
    failures = [f"resplit: {b}" for b in failed]
    failures += [f"figure link: {x}" for x in ensure_figure_links(corpus)]
    check_chapters(corpus, [b for b, _ in plan if b not in failed])
    failures += report_held(corpus)
    return failures


# What to change in <corpus>/_split_config.json when the validator fails a book on a check
# (spec: openspec/specs/conversion/spec.md, "A per-book split config steers --resplit").
# Every entry needs a `reason`; *_ok / *_skip keys only for a cause that is really in the book.
CHECK_HINTS = {
    "chapter_gap": "chapters really absent from the source -> missing_chapters_ok: [N, ...]; numbers "
                   "misread -> chapter_regex / chapter_level / chapter_number_overrides; the book has "
                   "no chapter numbering -> chapter_numbers: false",
    "chapter_order": "a number read from the wrong outline entry -> chapter_number_overrides / drop_titles",
    "chapter_unmapped": "outline chapters merged into a neighbour -> split_levels / chapter_level; chapter "
                        "absent from the source -> missing_chapters_ok",
    "toc_count": "toc_chapters is the book's printed chapter count; fix it, or the split level "
                 "(split_levels / chapter_level)",
    "chapter_numbers_missing": "chapter_regex / chapter_level so the chapter entries are found, or "
                               "chapter_numbers: false if the book is not numbered",
    "title_mismatch": "outline pages off by a constant -> page_offset; outline points at wrong pages -> "
                      "anchor_titles: true (or a toc with pages); title differs from the print -> retitle; "
                      "title is an image -> title_check_skip: {file: reason}",
    "page_overlap": "outline pages wrong -> page_offset / anchor_titles / toc; duplicate old file -> "
                    "method: reconstruct + exclude_files",
    "page_gap": "outline skips pages -> toc / anchor_titles; body starts late -> body_start_page",
    "page_range": "the md's page markers disagree with the manifest: re-split with --force; if it "
                  "persists, report a bug",
    "empty_file": "a split entry with no text -> drop_titles",
}
DEFAULT_HINT = "not a config problem: re-split with --force; if it persists, report a bug"


def _report_entries(corpus: Path) -> dict:
    p = Path(corpus) / "_resplit_report.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def held_books(corpus: Path) -> list[tuple[str, list[str]]]:
    """Books <corpus>/_resplit_report.json records as resplit but invalid, whose folder exists
    in the corpus or under <corpus>/_held/."""
    corpus = Path(corpus)
    return [(b, list(r.get("errors") or [])) for b, r in sorted(_report_entries(corpus).items())
            if isinstance(r, dict) and r.get("status") == "ok" and r.get("valid") is False
            and ((corpus / b).is_dir() or (corpus / HELD_DIR / b).is_dir())]


def remove_tree_keep_links(d: Path) -> None:
    """Delete a folder; every link (junction / symlink) inside is removed as a link first, so
    its target — e.g. a converted book's figures — is never touched."""
    for dp, dn, fn in os.walk(d, topdown=True):
        for name in list(dn):
            p = Path(dp) / name
            if _is_link(p):
                try:
                    os.unlink(p)
                except OSError:
                    os.rmdir(p)  # a Windows junction
                dn.remove(name)
    shutil.rmtree(d)


def release_fixed(corpus: Path) -> list[str]:
    """Remove _held/<book> copies whose book now validates and is back in the corpus."""
    corpus = Path(corpus)
    hd = corpus / HELD_DIR
    if not hd.is_dir():
        return []
    report = _report_entries(corpus)
    released = []
    for d in sorted(hd.iterdir()):
        r = report.get(d.name)
        if d.is_dir() and isinstance(r, dict) and r.get("valid") is True and (corpus / d.name).is_dir():
            remove_tree_keep_links(d)
            released.append(d.name)
            print(f"  [released] {d.name}: validates now; held copy removed")
    if hd.exists() and not any(hd.iterdir()):
        hd.rmdir()
    return released


def write_fix_request(corpus: Path, book: str, errors: list[str]) -> Path:
    """_held/<book>/_fix_request.md: a brief an agent (or a person) can carry out on its own."""
    import split_manifest as sm
    held = corpus / HELD_DIR / book
    try:
        details = sm.validate_book(held)["errors"]
    except Exception as e:  # noqa: BLE001 — the brief must still be written
        details = [{"check": "?", "detail": f"validator raised {type(e).__name__}: {e}"}]
    cfg = corpus / "_split_config.json"
    roots = " ".join(f'--pdf-root "{r}"' for r in pdf_roots_for(book)) or "--pdf-root <BOOKS_DIR>"
    lines = [
        f"# Fix request — {book}",
        "",
        f"This book's split failed validation, so it was moved here ({held}) and is NOT in the",
        "search index. Every other book was handed off as usual. Goal: make it validate.",
        "",
        "## What failed",
        "",
    ]
    for e in details[:30]:
        where = f" ({e['file']})" if e.get("file") else ""
        lines.append(f"- `{e.get('check')}`{where}: {e.get('detail')}")
    if len(details) > 30:
        lines.append(f"- … {len(details) - 30} more")
    lines += ["", "## Where to look", ""]
    for c in errors or ["?"]:
        lines.append(f"- `{c}`: {CHECK_HINTS.get(c, DEFAULT_HINT)}")
    lines += [
        "",
        "## Steps",
        "",
        f"1. Read the split files here and the book's text in `{OUTPUT_BASE / book}`; decide what is",
        "   really wrong. If the converted text itself is broken (two pages interleaved line by line,",
        "   garbled characters, pages missing), the fix is re-converting the book, not split config.",
        f"2. Otherwise add or fix the `\"{book}\"` entry of `{cfg}`.",
        "3. Re-split and validate:",
        "",
        "```",
        f'python "{HERE / "convert.py"}" --resplit "{OUTPUT_BASE}" --out "{corpus}" {roots} --book "{book}" --force',
        f'python "{HERE / "split_manifest.py"}" --validate "{corpus}" --book "{book}"',
        f'python "{HERE / "chapter_check.py"}" "{corpus}" --book "{book}"',
        "```",
        "",
        "4. Run `post_convert.py --index`: the book goes back into the index and this folder is removed.",
        "",
        "## Done when",
        "",
        "- the validator prints `PASS` for the book, and",
        "- `chapter_check.py` does not mark it `SUSPECT`.",
        "",
        "## Rules",
        "",
        "- Every config entry needs a `reason` saying what is in the book, not that a check failed.",
        "- `missing_chapters_ok`, `title_check_skip`, `exclude_files` only for a cause that is really",
        "  in the book (chapter absent from the source, title is an image, duplicate file) — never to",
        "  make a wrong split pass.",
        "- Never change a validator check or threshold to make this book pass.",
        "- Back up the config before editing it.",
        "",
    ]
    p = held / "_fix_request.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def report_held(corpus: Path) -> list[str]:
    """Move each held book to <corpus>/_held/<book>/ (out of the hand-off; every other book is
    unaffected), write its fix request, list it. Returns the failure labels."""
    corpus = Path(corpus).resolve()
    release_fixed(corpus)
    held = held_books(corpus)
    if not held:
        return []
    print("\n=== Invalid splits (held under _held/, out of the index; everything else is handed off) ===")
    for book, errors in held:
        src, dst = corpus / book, corpus / HELD_DIR / book
        if src.is_dir():
            if dst.exists():
                remove_tree_keep_links(dst)
            dst.parent.mkdir(exist_ok=True)
            os.replace(src, dst)
        req = write_fix_request(corpus, book, errors)
        print(f"  [FAIL] {book}: {', '.join(errors) or 'invalid'}")
        print(f"    fix request: {req}")
    return [f"invalid split: {b} [{','.join(e)}]" for b, e in held]


def check_chapters(corpus: Path, books: list[str]) -> None:
    """Chapter numbers vs each file's own pages, for the books resplit in this run. Report only."""
    if not books:
        return
    import chapter_check
    print("\n=== Chapter check (report only) ===")
    for b in books:
        d = Path(corpus) / b
        if not (d / "_manifest.json").exists():
            continue
        r = chapter_check.check_book(d)
        c = r["counts"]
        if not r["rows"]:
            continue
        print(f"  chapter check {b}: confirmed {c['confirmed']}, unknown {c['unknown']}, "
              f"contradicted {c['contradicted']}")
        if r["suspect"]:
            print(f"  [WARN] {b}: more files contradict their chapter number than confirm it - "
                  f"look at these openings (chapter_number_overrides / chapter_regex):")
            for row in [x for x in r["rows"] if x["verdict"] == "contradicted"][:5]:
                print(f"    {row['file']}: n={row['chapter_number']}, page says chapter "
                      f"{row['other_chapter_numbers']}")


CORPUS_FORMAT_URL = "https://github.com/drpwchen/ask-your-library/blob/main/docs/corpus-format.md"


def corpus_add_command(root: Path) -> str:
    script = (Path(VAULT_SEARCH_DIR) / "server" / "corpus.py" if VAULT_SEARCH_DIR
              else "<path/to/ask-your-library>/server/corpus.py")
    return f'python "{script}" add "{root}"'


def _corpus_script():
    """ask-your-library's corpus CLI when VAULT_SEARCH_DIR is a checkout that has one, else None."""
    if not VAULT_SEARCH_DIR:
        return None
    p = VAULT_SEARCH_DIR / "server" / "corpus.py"
    return p if p.exists() else None


def run_legacy_indexer():
    """Deprecated: an external indexer with an --incremental / --book CLI (INDEXER_SCRIPT)."""
    if not INDEXER_SCRIPT.exists():
        print(f"  [skip] INDEXER_SCRIPT not found: {INDEXER_SCRIPT}")
        return True
    result = subprocess.run(
        [sys.executable, str(INDEXER_SCRIPT), "--incremental"],
        capture_output=False,
    )
    return result.returncode == 0


def warn_indexer_script_deprecated(root: Path):
    print("[deprecated] INDEXER_SCRIPT is deprecated and will be removed: this repo no longer "
          "talks to an indexer CLI. Hand the corpus to ask-your-library's corpus.py add instead:\n"
          f"    {corpus_add_command(root)}\n"
          f"  (corpus format: {CORPUS_FORMAT_URL})", file=sys.stderr)


def run_indexer(root: Path = None) -> int:
    """Hand `root` to ask-your-library. Returns an exit code; 0 when nothing was run."""
    root = Path(root) if root else OUTPUT_BASE
    print("\n=== Semantic search hand-off ===")
    if INDEXER_SCRIPT:
        warn_indexer_script_deprecated(root)
        run_legacy_indexer()
        return 0  # the legacy route never set the exit code; unchanged
    script = _corpus_script()
    if script:
        py = str(VAULT_SEARCH_PY) if VAULT_SEARCH_PY else sys.executable
        return subprocess.run([py, str(script), "add", str(root)]).returncode
    if VAULT_SEARCH_DIR:
        print(f"  [skip] VAULT_SEARCH_DIR={VAULT_SEARCH_DIR} has no server/corpus.py - it must "
              f"point at an ask-your-library checkout (the repo, not an index directory).")
    else:
        print("  [skip] semantic search is opt-in and lives in ask-your-library; nothing was indexed.")
    if (root / "corpus.json").exists():
        print("  Once ask-your-library is installed, run:")
        print(f"    {corpus_add_command(root)}")
    else:
        # not a --resplit output yet: show both steps, with a placeholder corpus root
        new_root = "<CORPUS_ROOT>"
        print(f"  {root} has no corpus.json - it is not a corpus yet. Re-split it into one, then "
              "hand that to ask-your-library:")
        print(f'    python converter/convert.py --resplit "{root}" --out "{new_root}" --pdf-root <BOOKS_DIR>')
        print(f"    {corpus_add_command(new_root)}")
    print(f"  (corpus format: {CORPUS_FORMAT_URL})")
    return 0


def audit_index_coverage(backfill: bool = True):
    """Legacy INDEXER_SCRIPT route only (deprecated): list book folders that have md but no
    rows in the v1 index (`<VAULT_SEARCH_DIR>/lance_db`, table `textbook_chunks`), and
    (optionally) backfill each via `indexer --book`. ask-your-library's `corpus.py add` runs its
    own audit, so on that route this step is skipped."""
    print("\n=== Index Coverage Audit ===")
    if not INDEXER_SCRIPT:
        print("  [skip] the coverage audit is part of ask-your-library's `corpus.py add`")
        return
    if not VAULT_SEARCH_DIR:
        print("  [skip] legacy audit needs VAULT_SEARCH_DIR (the index directory) - see shared/config.py")
        return
    try:
        import lancedb
    except ImportError:
        print("  [skip] lancedb not importable")
        return
    db_path = VAULT_SEARCH_DIR / "lance_db"
    try:
        db = lancedb.connect(str(db_path))
        tbl = db.open_table("textbook_chunks")
        indexed = set(tbl.to_lance().to_table(columns=["book"]).to_pydict()["book"])
    except Exception as e:
        print(f"  [skip] cannot read index: {e}")
        return

    # folders with ANY md (recursive — some books nest chapters in subdirs)
    folders = set()
    for d in OUTPUT_BASE.iterdir():
        if d.is_dir() and d.name not in {"misc", "scripts", "figures_test", "__pycache__"}:
            if any(d.rglob("*.md")):
                folders.add(d.name)

    missing = sorted(folders - indexed)
    print(f"  Folders with md: {len(folders)} | in index: {len(folders & indexed)} | missing: {len(missing)}")
    if not missing:
        print("  ✓ full coverage")
        return
    for name in missing:
        print(f"  - MISSING: {name}")
    if backfill:
        for name in missing:
            print(f"  [backfill] indexing {name} ...", flush=True)
            subprocess.run([sys.executable, str(INDEXER_SCRIPT), "--book", name], capture_output=False)


def update_figure_registry():
    """Regenerate a figure registry from current figures, if a generator script is configured."""
    print("\n=== Updating Figure Registry ===")
    if FIGURE_REGISTRY_SCRIPT and FIGURE_REGISTRY_SCRIPT.exists():
        result = subprocess.run(
            [sys.executable, str(FIGURE_REGISTRY_SCRIPT)],
            capture_output=False,
        )
        return result.returncode == 0
    else:
        print(f"  [skip] FIGURE_REGISTRY_SCRIPT not configured — see shared/config.py")
        return True


def update_index_md():
    """Regenerate INDEX.md from current output contents."""
    print("\n=== Updating INDEX.md ===")

    books = []
    for label, d in listed_books(OUTPUT_BASE):
        md_files = list(d.glob("*.md"))
        if not md_files:
            continue

        total_words = 0
        for f in md_files:
            content = f.read_text(encoding="utf-8", errors="replace")
            total_words += len(content.split())

        books.append((label, len(md_files), total_words))

    lines = [
        f"# Textbook-to-Note Index",
        f"",
        f"Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total: {len(books)} books | {sum(f for _, f, _ in books)} md files | {sum(w for _, _, w in books):,} words",
        f"",
        f"| Book | Files | Words |",
        f"|------|-------|-------|",
    ]
    for name, files, words in books:
        lines.append(f"| {name} | {files} | {words:,} |")

    index_path = OUTPUT_BASE / "INDEX.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"INDEX.md updated: {len(books)} books")


def main():
    parser = argparse.ArgumentParser(description="Post-conversion: verify, keep the corpus in step, "
                                                 "hand it to ask-your-library")
    parser.add_argument("--verify", action="store_true", help="Only verify conversion quality")
    parser.add_argument("--resplit-only", action="store_true",
                        help="Only re-split new / re-converted books into the corpus (+ figure links)")
    parser.add_argument("--index", action="store_true",
                        help="Resplit + figure links, then hand the corpus to ask-your-library (corpus.py add)")
    parser.add_argument("--backfill", action="store_true",
                        help="Only the hand-off (ask-your-library re-indexes changed files and audits)")
    parser.add_argument("--corpus", metavar="DIR",
                        help="corpus root (default CORPUS_DIR, ./corpus)")
    parser.add_argument("--audit", action="store_true",
                        help="Only audit index coverage (legacy INDEXER_SCRIPT route; report, no backfill)")
    args = parser.parse_args()
    root = Path(args.corpus) if args.corpus else CORPUS_DIR
    failures = []
    handoff_rc = 0

    def handoff():
        nonlocal handoff_rc
        if not INDEXER_SCRIPT and args.backfill:
            failures.extend(report_held(root))  # held books move under _held/; the rest is handed off
        handoff_rc = run_indexer(root)
        if handoff_rc:
            failures.append(f"ask-your-library hand-off exited {handoff_rc}")

    if args.verify:
        verify_conversion()
    elif args.resplit_only:
        failures += refresh_corpus(root)
    elif args.index:
        if not INDEXER_SCRIPT:
            failures += refresh_corpus(root)
        handoff()
    elif args.backfill:
        if INDEXER_SCRIPT:
            audit_index_coverage(backfill=True)
        else:
            handoff()
    elif args.audit:
        audit_index_coverage(backfill=False)
    else:
        # Full pipeline. Page markers are informational only — an indexer handles
        # marker-less books (epub / vector-glyph PDFs) fine (page metadata = 0), so
        # a few suspect books must NOT block indexing the rest. Coverage is enforced
        # afterward by audit_index_coverage(), which backfills anything that slipped.
        all_good = verify_conversion()
        update_index_md()
        update_figure_registry()
        if not all_good:
            print("\n[note] some PDF books lack page markers (see above) — indexing anyway; "
                  "re-run conversion on those if search quality is poor.")
        if not INDEXER_SCRIPT:
            failures += refresh_corpus(root)
        handoff()
        audit_index_coverage(backfill=True)

    # A caller (or a scheduled run) must not read "exit 0" as "the new books are in the corpus".
    if failures:
        print(f"\n[FAIL] {len(failures)} step(s) failed:")
        for f in failures:
            print(f"  - {f}")
        only_handoff = failures == [f"ask-your-library hand-off exited {handoff_rc}"]
        sys.exit(handoff_rc if only_handoff else 1)


if __name__ == "__main__":
    main()
