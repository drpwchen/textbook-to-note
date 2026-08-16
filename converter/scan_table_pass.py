"""Recover tables from a book that was converted through Surya OCR.

A scan-only PDF has no text layer, so `convert_pdf()`'s pdfplumber table pass finds nothing and
the book routes to Surya OCR instead -- which returns running text and **zero tables**. Every
table in such a book is silently lost: two scanned references in one corpus mentioned a table
caption 178 times between them, and all 178 were missing from the markdown until this pass
existed.

Docling reads a rendered page image, so it CAN find tables on a scan. Running it over every page
of an 800-page book is expensive, so this pass is caption-targeted: it looks in the OCR text for
pages that say "Table 5.1" and asks Docling only about those pages (plus the following page, for
tables that continue over a break).

The OCR markdown is annotated with `<!-- page N -->` markers, which is how a caption maps back to
a 1-based PDF page number.

Usage:
  python scan_table_pass.py --book NAME --pdf PATH [--dry-run] [--max-pages N]
  python scan_table_pass.py --from-progress PROGRESS.json --books-json BOOKS.json   # all OCR books

GPU: Docling loads a model onto the GPU. If you gate GPU jobs with a lease/semaphore, wrap the
call in it.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root -> shared.config
sys.path.insert(0, str(Path(__file__).resolve().parent))          # converter/ -> sibling modules
import convert  # noqa: E402
from docling_tables import DoclingTableWorker  # noqa: E402
from shared.config import OUTPUT_DIR  # noqa: E402

PAGE_MARK = re.compile(r"<!-- page (\d+) -->")
CAPTION = re.compile(r"\b(?:Table|TABLE|Tabelle|Tableau)\s+[A-Z]?\d+[.\d]*", re.I)


def pages_with_captions(text: str) -> list[int]:
    """1-based PDF pages whose OCR text contains a table caption, plus each one's successor."""
    marks = [(m.start(), int(m.group(1))) for m in PAGE_MARK.finditer(text)]
    if not marks:
        return []
    hits = set()
    for cap in CAPTION.finditer(text):
        page = marks[0][1]
        for pos, n in marks:
            if pos > cap.start():
                break
            page = n
        hits.add(page)
        hits.add(page + 1)          # continuation onto the next page
    return sorted(hits)


def splice(text: str, page: int, tables_md: list[str]) -> str:
    """Insert rendered tables at the end of `page`'s section, before the next page marker."""
    m = PAGE_MARK.search(text, 0)
    start = None
    while m:
        if int(m.group(1)) == page:
            start = m.end()
            break
        m = PAGE_MARK.search(text, m.end())
    if start is None:
        return text
    nxt = PAGE_MARK.search(text, start)
    end = nxt.start() if nxt else len(text)
    block = "\n\n" + f"**[Table on page {page}]**\n" + "\n\n".join(tables_md) + "\n"
    return text[:end].rstrip() + block + text[end:]


def process_book(book: str, pdf: str, corpus: Path, worker, dry_run: bool, max_pages: int | None):
    ft = corpus / book / "full_text.md"
    if not ft.exists():
        print(f"  [skip] {book}: no full_text.md")
        return 0, 0
    text = ft.read_text(encoding="utf-8")
    targets = pages_with_captions(text)
    if max_pages:
        targets = targets[:max_pages]
    print(f"  {book}: {len(targets)} pages have table captions, querying Docling ...", flush=True)

    found = 0
    for i, page in enumerate(targets, 1):
        try:
            tables = worker.tables_for_page(pdf, page)
        except Exception as e:
            print(f"    p{page}: worker error {e}")
            continue
        if not tables:
            continue
        # Render rows -> markdown the same way convert.py's Docling rung does. DoclingTable has
        # no to_markdown(); str(t) would splice the dataclass repr into the corpus. No ligature
        # repair here: a scanned page has no text layer to vouch for a repair.
        md = [m for m in (convert._table_to_md(t.rows) for t in tables) if m]
        if not md:
            continue
        found += len(md)
        if not dry_run:
            text = splice(text, page, md)
        if i % 25 == 0:
            print(f"    ...{i}/{len(targets)} pages, {found} tables so far", flush=True)
    if not dry_run and found:
        ft.write_text(text, encoding="utf-8")
    return len(targets), found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book")
    ap.add_argument("--pdf")
    ap.add_argument("--from-progress", help="progress.json from a batch; picks its OCR-routed books")
    ap.add_argument("--books-json", help="books.json giving each book's pdf path")
    ap.add_argument("--corpus", default=str(OUTPUT_DIR))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-pages", type=int, help="cap pages per book (smoke test)")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    jobs = []
    if args.book:
        if not args.pdf:
            ap.error("--book requires --pdf")
        jobs.append((args.book, args.pdf))
    elif args.from_progress and args.books_json:
        pdfs = {b["book"]: b["pdf"] for b in json.loads(Path(args.books_json).read_text(encoding="utf-8"))}
        for name in json.loads(Path(args.from_progress).read_text(encoding="utf-8")):
            if name in pdfs and convert.is_scan_only(pdfs[name]):
                jobs.append((name, pdfs[name]))
    else:
        ap.error("pass --book/--pdf, or --from-progress + --books-json")

    print(f"books to recover tables for: {len(jobs)}" + (" [DRY-RUN]" if args.dry_run else ""))
    # GPU lease brackets the warm Docling worker's whole lifetime — it keeps its
    # model in VRAM until close(). (The docstring rule "wrap the call in a GPU
    # lease" is now enforced in code, not just prose; no-op without a broker.)
    _lease = convert._gpu_lease_ctx("t2n_scan_tables")
    _lease.__enter__()
    worker = DoclingTableWorker()
    total_pages = total_tables = 0
    try:
        for book, pdf in jobs:
            p, t = process_book(book, pdf, corpus, worker, args.dry_run, args.max_pages)
            total_pages += p
            total_tables += t
            print(f"  -> {book}: {t} tables / {p} pages")
    finally:
        status = worker.status()
        worker.close()
        _lease.__exit__(None, None, None)
    print(f"\ntotal {total_tables} tables / {total_pages} pages scanned"
          f" (docling failure pages: {status.get('failure_count', 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
