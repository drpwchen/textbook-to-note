"""
Rename PDF books to Author_Title_Ne_YYYY.pdf standard format.

Reads edition/year off each PDF's copyright page (fitz text extraction), falling back to
filename hints and PDF metadata. Skips files that already match the standard pattern,
chapter-split PDFs, and anything listed in T2N_CONFIG_DIR/rename_skip.json
(T2N_CONFIG_DIR defaults to BOOKS_DIR).

Optional one-off corrections (e.g. a copyright page that misleads the year heuristic)
can be supplied via T2N_CONFIG_DIR/rename_fixes.json: {"old_filename.pdf": "new_filename.pdf"}.
"""
import json
import os
import re
import sys
import fitz
from pathlib import Path

# Windows console (e.g. a Traditional Chinese cp950 locale) defaults stdout to the
# system codepage, not UTF-8 — reconfigure so CJK filenames print cleanly instead of
# raising UnicodeEncodeError or emitting mojibake.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import BOOKS_DIR, CONFIG_DIR

BASE = str(BOOKS_DIR)

# Folder names to skip entirely (e.g. legacy-edition archives), and specific
# multi-file book folders whose per-chapter PDFs shouldn't be renamed. Populate via
# T2N_CONFIG_DIR/rename_skip.json: {"skip_folders": [...], "skip_subfolder_books": [...], "skip_files": [...]}
_SKIP_FILE = CONFIG_DIR / "rename_skip.json"
_skip_cfg = json.loads(_SKIP_FILE.read_text(encoding="utf-8")) if _SKIP_FILE.exists() else {}
SKIP_FOLDERS = set(_skip_cfg.get("skip_folders", []))
SKIP_SUBFOLDER_BOOKS = set(_skip_cfg.get("skip_subfolder_books", []))
SKIP_FILES = set(_skip_cfg.get("skip_files", []))

STANDARD_RE = re.compile(r'^.+_\d+e_\d{4}\.pdf$')
CHAPTER_RE = re.compile(r'^(Ch\d|[0-9]+\. |000_|Appendix |Examination )')

# Filename patterns
FNAME_YEAR_RE = re.compile(r'_(\d{4})\.pdf$')
FNAME_EDITION_RE = re.compile(r'_(\d+)e(?:_|\.pdf$)')

EDITION_WORDS = {
    'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
    'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
    'eleventh': 11, 'twelfth': 12,
}


def extract_info_from_pdf(filepath):
    """Extract edition and year from PDF copyright page."""
    edition = None
    year = None
    try:
        doc = fitz.open(filepath)
    except Exception:
        return edition, year

    # Read first 8 pages
    pages_text = []
    for i in range(min(8, len(doc))):
        try:
            text = doc[i].get_text()
            if text:
                pages_text.append(text)
        except Exception:
            pass
    doc.close()

    combined = '\n'.join(pages_text)

    # --- Extract YEAR ---
    # Strategy: find "Copyright (c) YYYY" line - first year is the current edition
    copyright_line_re = re.compile(
        r'(?:copyright|©|\(c\))\s*(?:\?\s*)?(\d{4})',
        re.IGNORECASE
    )
    m = copyright_line_re.search(combined)
    if m:
        y = int(m.group(1))
        if 1950 <= y <= 2030:
            year = y

    # If no copyright line, try "Published YYYY" or "Printed YYYY"
    if not year:
        m = re.search(r'(?:published|printed)\s+(?:in\s+)?(\d{4})', combined, re.I)
        if m:
            y = int(m.group(1))
            if 1950 <= y <= 2030:
                year = y

    # Try LCCN/description lines: "Champaign, IL : Publisher, [2016]"
    if not year:
        m = re.search(r'\[(\d{4})\]', combined)
        if m:
            y = int(m.group(1))
            if 1980 <= y <= 2030:
                year = y

    # Try standalone year near "edition" keyword within 200 chars
    if not year:
        m = re.search(r'edition.*?(\d{4})|(\d{4}).*?edition', combined[:3000], re.I)
        if m:
            y = int(m.group(1) or m.group(2))
            if 1980 <= y <= 2030:
                year = y

    # Last resort: metadata creationDate
    if not year:
        try:
            doc2 = fitz.open(filepath)
            meta = doc2.metadata
            doc2.close()
            for key in ['creationDate', 'modDate']:
                val = meta.get(key, '')
                if val:
                    m2 = re.search(r'D:(\d{4})', val)
                    if m2:
                        y = int(m2.group(1))
                        if 1980 <= y <= 2030:
                            year = y
                            break
        except Exception:
            pass

    # --- Extract EDITION ---
    # Word editions
    for word, num in EDITION_WORDS.items():
        if re.search(rf'\b{word}\s+edition\b', combined, re.I):
            edition = num
            break

    # Numeric editions: "4th edition", "2nd ed"
    if not edition:
        m = re.search(r'(\d+)(?:st|nd|rd|th)\s+(?:edition|ed\b)', combined, re.I)
        if m:
            edition = int(m.group(1))

    # "Edition N" or "edition: N"
    if not edition:
        m = re.search(r'edition[:\s]+(\d+)', combined, re.I)
        if m:
            edition = int(m.group(1))

    return edition, year


def compute_new_name(fname, pdf_edition, pdf_year):
    """Compute new filename from old name + extracted info."""
    # Extract info from filename
    fname_year = None
    fname_edition = None
    m = FNAME_YEAR_RE.search(fname)
    if m:
        fname_year = int(m.group(1))
    m = FNAME_EDITION_RE.search(fname)
    if m:
        fname_edition = int(m.group(1))

    final_edition = fname_edition or pdf_edition or 1
    final_year = pdf_year or fname_year  # prefer PDF year over filename year

    if final_year is None:
        return None, "no year found"

    # Build stem: remove .pdf, then strip trailing _YYYY and/or _Ne
    stem = fname[:-4]
    # Remove trailing _YYYY
    stem = re.sub(r'_\d{4}$', '', stem)
    # Remove trailing _Ne
    stem = re.sub(r'_\d+e$', '', stem)

    new_name = f"{stem}_{final_edition}e_{final_year}.pdf"
    return new_name, None


def process_files(dry_run=False):
    renamed = 0
    skipped = 0
    warnings = []
    already_standard = 0
    errors = []

    for root, dirs, files in os.walk(BASE):
        rel = os.path.relpath(root, BASE)
        parts = rel.split(os.sep)
        if parts[0] in SKIP_FOLDERS:
            continue
        if any(p in SKIP_SUBFOLDER_BOOKS for p in parts):
            skipped += len([f for f in files if f.endswith('.pdf')])
            continue

        for fname in files:
            if not fname.endswith('.pdf'):
                continue
            if fname in SKIP_FILES:
                skipped += 1
                continue
            if CHAPTER_RE.match(fname):
                skipped += 1
                continue

            # Check if already standard
            if STANDARD_RE.match(fname):
                already_standard += 1
                continue

            filepath = os.path.join(root, fname)
            pdf_edition, pdf_year = extract_info_from_pdf(filepath)
            new_name, err = compute_new_name(fname, pdf_edition, pdf_year)

            if err:
                warnings.append(f"WARNING: {err} — {fname}")
                skipped += 1
                continue

            if new_name == fname:
                already_standard += 1
                continue

            new_path = os.path.join(root, new_name)
            if os.path.exists(new_path):
                errors.append(f"ERROR: Target exists: {new_name} (from {fname})")
                continue

            action = "WOULD RENAME" if dry_run else "RENAMED"
            print(f"{fname} → {new_name}")
            if not dry_run:
                try:
                    os.rename(filepath, new_path)
                    renamed += 1
                except PermissionError as e:
                    errors.append(f"PERMISSION ERROR: {fname} — {e}")
                except Exception as e:
                    errors.append(f"ERROR: {fname} — {e}")
            else:
                renamed += 1

    print(f"\n{'='*60}")
    print(f"SUMMARY {'(DRY RUN)' if dry_run else ''}")
    print(f"{'='*60}")
    print(f"Renamed:           {renamed}")
    print(f"Already standard:  {already_standard}")
    print(f"Skipped (chapters/special): {skipped}")
    print(f"Warnings (no year): {len(warnings)}")
    print(f"Errors:            {len(errors)}")

    if warnings:
        print(f"\n--- Warnings ---")
        for w in warnings:
            print(w)
    if errors:
        print(f"\n--- Errors ---")
        for e in errors:
            print(e)


def load_fixes() -> dict:
    """Load {old_name: new_name} one-off corrections from T2N_CONFIG_DIR/rename_fixes.json, if present."""
    fixes_file = CONFIG_DIR / "rename_fixes.json"
    if fixes_file.exists():
        return json.loads(fixes_file.read_text(encoding="utf-8"))
    return {}


def fix_wrong_renames():
    """Apply one-off filename corrections from T2N_CONFIG_DIR/rename_fixes.json (e.g. to fix a
    year the copyright-page heuristic got wrong on a previous run). No-op if absent."""
    fixes = load_fixes()
    fixed = 0
    for root, dirs, files in os.walk(BASE):
        for fname in files:
            if fname in fixes:
                old_path = os.path.join(root, fname)
                new_name = fixes[fname]
                new_path = os.path.join(root, new_name)
                if os.path.exists(old_path) and not os.path.exists(new_path):
                    print(f"FIX: {fname} → {new_name}")
                    try:
                        os.rename(old_path, new_path)
                        fixed += 1
                    except Exception as e:
                        print(f"  ERROR: {e}")
    print(f"\nFixed {fixed} renames from rename_fixes.json.")


if __name__ == '__main__':
    print("=" * 60)
    print("PHASE 1: Apply one-off fixes (rename_fixes.json)")
    print("=" * 60)
    fix_wrong_renames()

    print("\n" + "=" * 60)
    print("PHASE 2: Process remaining files")
    print("=" * 60)
    process_files(dry_run=False)
