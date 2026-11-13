"""
Shared configuration for the textbook-to-note converter scripts.

Env-driven, with sensible defaults relative to the repo so it runs out of the
box with no configuration: drop PDFs/EPUBs in ./books, run a script, get
markdown in ./output.

Env overrides:
    BOOKS_DIR           -> source PDFs/EPUBs (default: ./books)
    OUTPUT_DIR          -> markdown output root (default: ./output)
    CORPUS_DIR          -> corpus root that post_convert.py re-splits OUTPUT_DIR into
                           (default: ./corpus); the folder ask-your-library indexes
    T2N_PDF_ROOTS       -> folders (os.pathsep-separated) post_convert.py's resplit searches
                           for a book's PDF when its `Source:` PDF is gone (default: BOOKS_DIR)
    T2N_CONFIG_DIR      -> folder holding books.json, already_converted.json, rename_skip.json
                           and rename_fixes.json (default: BOOKS_DIR) -- lets a library keep
                           its settings outside the folder of source PDFs
    SURYA_VENV_PY       -> path to a Surya OCR venv's Python interpreter (python.exe on Windows, bin/python on macOS/Linux) (optional)
    SURYA_ADAPTER       -> path to an OCR adapter script (optional); this repo ships
                           converter/surya_adapter.py -- see docs/surya-adapter.md for the
                           interface contract if you want to plug in a different engine
    VAULT_SEARCH_DIR    -> path to an ask-your-library checkout (optional); lets
                           `post_convert.py --index` run its server/corpus.py add
    VAULT_SEARCH_PY     -> Python interpreter of ask-your-library's environment for that
                           call (optional; default: the current interpreter)
    INDEXER_SCRIPT      -> DEPRECATED: an indexer with an --incremental/--book CLI;
                           when set, VAULT_SEARCH_DIR keeps its old meaning (the
                           index directory read by the legacy coverage audit)
    FIGURE_REGISTRY_SCRIPT -> path to a figure-registry generator script (optional)

The OCR/indexer hooks are all optional integrations: if unset (or the path
doesn't exist), the scripts skip that step rather than failing.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Canonical dash set for figure/table reference IDs ("Figure 33–3", "TABLE 5‑2").
# Publishers typeset these with whichever dash their house style uses, so the
# converter's reference detection and the figure stage's caption matching must
# agree on the same set (issue #10): ASCII hyphen, U+2010 hyphen, U+2011
# non-breaking hyphen, U+2012 figure dash, U+2013 en dash, U+2014 em dash,
# U+2015 horizontal bar, U+2212 minus.
DASH_CHARS = "-‐‑‒–—―−"
# Regex character class matching any of those dashes or a dot ("Figure 32.1").
SEP_CLASS = "[" + DASH_CHARS + ".]"

BOOKS_DIR = Path(os.environ.get("BOOKS_DIR") or (REPO_ROOT / "books"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR") or (REPO_ROOT / "output"))
CORPUS_DIR = Path(os.environ.get("CORPUS_DIR") or (REPO_ROOT / "corpus"))
PDF_ROOTS = [Path(r) for r in os.environ.get("T2N_PDF_ROOTS", "").split(os.pathsep) if r] or [BOOKS_DIR]
CONFIG_DIR = Path(os.environ.get("T2N_CONFIG_DIR") or BOOKS_DIR)

# Optional OCR engine (only used as a fallback for scanned/garbled PDFs).
SURYA_VENV_PY = Path(os.environ["SURYA_VENV_PY"]) if os.environ.get("SURYA_VENV_PY") else None
SURYA_ADAPTER = Path(os.environ["SURYA_ADAPTER"]) if os.environ.get("SURYA_ADAPTER") else None

# Optional hand-off to ask-your-library (not part of this repo): corpus.py add <corpus root>.
VAULT_SEARCH_DIR = Path(os.environ["VAULT_SEARCH_DIR"]) if os.environ.get("VAULT_SEARCH_DIR") else None
VAULT_SEARCH_PY = Path(os.environ["VAULT_SEARCH_PY"]) if os.environ.get("VAULT_SEARCH_PY") else None
# Deprecated: external indexer with an --incremental/--book CLI.
INDEXER_SCRIPT = Path(os.environ["INDEXER_SCRIPT"]) if os.environ.get("INDEXER_SCRIPT") else None
FIGURE_REGISTRY_SCRIPT = Path(os.environ["FIGURE_REGISTRY_SCRIPT"]) if os.environ.get("FIGURE_REGISTRY_SCRIPT") else None

# Folder names to skip entirely during recursive batch conversion (comma-separated).
SKIP_FOLDERS = set(f for f in os.environ.get("CONVERTER_SKIP_FOLDERS", "").split(",") if f)

if __name__ == "__main__":
    for name in ("REPO_ROOT", "BOOKS_DIR", "OUTPUT_DIR", "CORPUS_DIR", "PDF_ROOTS", "CONFIG_DIR", "SURYA_VENV_PY", "SURYA_ADAPTER",
                 "VAULT_SEARCH_DIR", "VAULT_SEARCH_PY", "INDEXER_SCRIPT", "FIGURE_REGISTRY_SCRIPT", "SKIP_FOLDERS"):
        print(f"{name} = {globals()[name]}")
