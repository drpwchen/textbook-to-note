"""LEGACY — run visual_check.sample_check across N books (batch tooling only;
the note-writing workflow classifies every crop with a frontier-tier model
instead).

For each book, picks a few figures (single + multipart + late chapter) and
asks a local vision model (default minicpm-v:8b) to describe them. Modality /
anatomy keyword overlap with caption flags suspicious mismatches.

Usage:
    python visual_check_batch.py --books B1,B2,...  [--n 3]
"""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path

SCRIPTS = Path(__file__).parent

sys.path.insert(0, str(SCRIPTS.parent))
from shared.config import OUTPUT_DIR as BOOKS_MD


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vc = _load("visual_check", SCRIPTS / "visual_check.py")


def check_book(book: str, n: int) -> dict:
    fig_dir = BOOKS_MD / book / "figures"
    manifest = fig_dir / "figures_manifest.json"
    if not manifest.exists():
        return {"book": book, "_error": "no manifest"}
    figs = json.loads(manifest.read_text(encoding="utf-8")).get("figures", [])
    figs = [f for f in figs if f.get("file")]
    if not figs:
        return {"book": book, "_error": "no figs in manifest"}
    out = vc.sample_check(figs, fig_dir, n=n)
    out["book"] = book
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True, help="comma-separated book dirs")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()
    books = [b.strip() for b in args.books.split(",") if b.strip()]
    results = []
    for b in books:
        print(f"== {b} ==")
        try:
            r = check_book(b, args.n)
        except Exception as e:
            r = {"book": b, "_error": str(e)}
        results.append(r)
        if "_error" in r:
            print(f"  ERROR: {r['_error']}")
        else:
            print(f"  checked={len(r.get('checked', []))}, passes={r.get('passes',0)}, fails={r.get('fails',0)}")
            for s in r.get("suspicious", [])[:3]:
                print(f"    SUSP: {s.get('file')}: cap={(s.get('caption') or '')[:50]!r} desc={(s.get('description') or '')[:80]!r}")
    print()
    print("=== summary ===")
    for r in results:
        if "_error" in r:
            print(f"  {r['book']:60s} ERROR {r['_error']}")
        else:
            tot = len(r.get("checked", []))
            ok = r.get("passes", 0)
            print(f"  {r['book']:60s} {ok}/{tot} pass")


if __name__ == "__main__":
    main()
