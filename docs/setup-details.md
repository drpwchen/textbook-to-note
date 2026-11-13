# Setup details (moved out of AGENTS.md)

These passages were moved verbatim from `AGENTS.md` so the agent instruction chain
stays within the 32 KiB limit some agents apply. `AGENTS.md` links here from each
step; the text below is unchanged.

## Step 1.1 — Profile C: the semantic search index

- **Profile C only** — the semantic search index (LanceDB + a local embedding
  model via ollama). Semantic search pays off once there are more than a
  handful of books; for a small personal library, A or B first and add it later.
  **This repo ships no indexer and talks to no indexer CLI.** It produces a
  corpus folder (format `t2n-corpus/1`); the companion repo
  [vault-search](https://github.com/drpwchen/vault-search) makes that folder
  searchable with one command, `server/corpus.py add <corpus root>` — see
  Step 5. Grep works out of the box with no extra install. Profiles A and B
  need none of this.

## Step 2 — What requirements.txt does and does not cover

`requirements.txt` covers **only** the core converter: PDF parsing, table
extraction, and the image helpers. Everything else is a separate manual
install, listed as comments at the bottom of the file rather than as
installable lines:

- EPUB conversion needs the `pandoc` CLI on `PATH` (not a pip package) —
  check with `pandoc --version` and prompt the user to install it if missing
- `requests` — only for `scan_fix_negatives.py --verify-dark` (ollama vision)
- `lancedb` — not needed by this repo at all on the current path; vault-search
  installs its own. Only the deprecated `INDEXER_SCRIPT` route's coverage
  audit imports it (see Step 5)
- OCR (Surya) lives in its own venv entirely — see Step 4.5

So `pip install -r requirements.txt` alone never gives the user semantic
search; do not tell them otherwise.

## Step 3 — Environment variables

Set env vars (in the shell, or a `.env` you source) only where the defaults
don't fit:
- `BOOKS_DIR` — where the user's PDFs/EPUBs live (default `./books`)
- `OUTPUT_DIR` — where converted markdown goes (default `./output`; keep it
  outside the notes vault — this corpus is for your reference, not for the
  user to read directly)
- Optional OCR fallback: `SURYA_VENV_PY` + `SURYA_ADAPTER` — **skip these
  unless Step 4.5 sends you here.** Setting them now buys nothing and pulls
  you toward installing an engine the user's books may never need.
  `SURYA_ADAPTER` points at `converter/surya_adapter.py`, which ships in this
  repo; `SURYA_VENV_PY` points at a *separate* venv's interpreter, since the
  OCR stack must not share dependencies with the converter
- Optional semantic search (Profile C): `VAULT_SEARCH_DIR` = the vault-search
  checkout, and `VAULT_SEARCH_PY` = the Python of vault-search's environment if
  it differs from this one. Only used by the `post_convert.py --index`
  shortcut; running `corpus.py add` yourself needs neither. `INDEXER_SCRIPT`
  is deprecated (Step 5)
- Figure output/cache locations: env-driven constants documented at the top
  of `figures/figure_qc_gate.py` (default `./output/figures`, inside the
  user's vault attachments folder if they want embeds to resolve)

## Step 4.5 — OCR: routing, diagnosis, engine setup, re-run

Know where the OCR path actually lives before you reach for it:

- Routing to OCR exists **only** in the `--batch-dir` code path
  (`converter/convert.py`), where a PDF is sent to OCR if it is scan-only, if
  a silent `fitz` failure is detected, or if you passed `--force-surya`.
- The **single-file** path (`python converter/convert.py one.pdf`) never
  invokes OCR at all. If a single-file conversion produced text, OCR was not
  involved and installing an engine will change nothing.

If the output really is broken, diagnose before installing anything:

1. Open the PDF in a normal viewer and try to select text on a bad page. Text
   selects cleanly ⇒ it is a born-digital file and the problem is not OCR.
2. Garbled-but-present text usually means a broken font encoding (CID /
   Identity-H without ToUnicode, PUA codepoints) — the silent-failure case
   the OCR ladder exists to catch.
3. No selectable text at all ⇒ a true scan, and OCR is the right answer.

Only for cases 2 and 3, read [`docs/ocr-ladder.md`](docs/ocr-ladder.md),
including its "Choosing your hardware tier" table (CPU-only / Apple Silicon
8GB / Apple Silicon 16GB+ / NVIDIA 8GB / NVIDIA 16GB+), and size the engine
to the user's actual hardware before installing. On a CPU-only machine the
honest answer is that scanned books are not locally OCR-able — say so rather
than installing an engine that cannot finish a book.

Then set up the engine. A reference adapter for **Surya 0.22.x** ships as
`converter/surya_adapter.py`; [`docs/surya-adapter.md`](docs/surya-adapter.md)
has the venv commands, the inference-server launch line **with the memory caps
already in it**, and the interface contract if you would rather drop in a
different engine. Read the memory-cap section before starting a server — the
defaults size a KV cache far larger than the model itself, and that is what
turned into a multi-tens-of-GB run for one user.

```bash
export SURYA_VENV_PY=/path/to/surya22-venv/bin/python   # Scripts/python.exe on Windows
export SURYA_ADAPTER="$PWD/converter/surya_adapter.py"
```

Then re-run the affected book through `--batch-dir` — the only path that can
route to OCR. `--force` is required: a book already converted in Step 4 is
skipped both by the progress file and by the up-to-date `full_text.md` check,
so without it the re-run is a no-op.

```bash
python converter/convert.py --batch-dir "path/to/folder/with/that/book" --force
```

Cases 2 and 3 are both auto-detected, so that command is usually enough. Add
`--force-surya` only when auto-detection did *not* fire and the output is
still bad — a text layer healthy enough to pass the check while being
worthless (OCR-overlay scans, some digitized reprints). It forces OCR on
every PDF under that directory, so point `--batch-dir` at a folder holding
just the affected book.

## Step 5 — Hand the corpus to vault-search

Profile C only. Profiles A and B stop before this step and need no index,
no embedding model and no vault-search.

**This repo does no indexing.** It produces a corpus folder; vault-search
indexes it. The hand-off is a folder contract, `t2n-corpus/1`, specified once
in vault-search:
[`docs/corpus-format.md`](https://github.com/drpwchen/vault-search/blob/main/docs/corpus-format.md).

### The sequence

```bash
# 1. convert (books → OUTPUT_DIR/<Book>/full_text.md + chapter files)
python converter/convert.py --batch-dir <BOOKS_DIR>
# 2. re-split into the corpus root: per-book _manifest.json + root corpus.json
python converter/convert.py --resplit <OUTPUT_DIR> --out <CORPUS_ROOT> --pdf-root <BOOKS_DIR>
# 3. in vault-search's environment: validate, index, audit, print the env line
python <vault-search>/server/corpus.py add <CORPUS_ROOT>
```

- `<CORPUS_ROOT>` is a new directory, never `OUTPUT_DIR` itself (`--resplit`
  refuses an `--out` that overlaps the source).
- Step 2 is required. The `_manifest.json` per book (page ranges, real chapter
  numbers, `kind`) is what the indexer reads, and `corpus.json`
  (`{"format": "t2n-corpus/1", "kind": "textbook"}`) is what marks the folder
  as a corpus. `--resplit` writes `corpus.json` only when it is absent and
  never overwrites one. `python converter/split_manifest.py --validate
  <CORPUS_ROOT>/<Book>` checks one book's split.
- `python <vault-search>/server/corpus.py check <CORPUS_ROOT>` validates
  without indexing — run it first if the corpus is large.
- After adding books, re-run steps 1–3; `--resplit` skips books whose output
  already exists (`--force` rebuilds).

### Guidelines

Clinical guidelines live in their own folder of the corpus,
`<CORPUS_ROOT>/_Guidelines/<Name>/`, one folder per guideline; vault-search
treats each as its own source and tags it `guideline`. `--batch-dir` writes
every PDF to `OUTPUT_DIR/<stem>/`, so convert guidelines in a separate run
with `OUTPUT_DIR` pointed at the `_Guidelines` folder of the same source
corpus — no flag needed:

```bash
OUTPUT_DIR=<OUTPUT_DIR>/_Guidelines python converter/convert.py --batch-dir <GUIDELINE_PDFS>
# PowerShell: $env:OUTPUT_DIR="<OUTPUT_DIR>\_Guidelines"; python converter\convert.py --batch-dir <GUIDELINE_PDFS>; Remove-Item Env:OUTPUT_DIR
python converter/convert.py --resplit <OUTPUT_DIR> --out <CORPUS_ROOT> --pdf-root <BOOKS_DIR> --pdf-root <GUIDELINE_PDFS>
```

`--resplit` treats `_Guidelines` as one top-level folder: each
`_Guidelines/<Name>/` is split from its own PDF, and a single
`<CORPUS_ROOT>/_Guidelines/_manifest.json` covers all of them (file paths
inside it are `<Name>/<file>`). Keep the guideline PDFs' file names equal to
the folder names you want (`<Name>.pdf` → `_Guidelines/<Name>/`).

### The `post_convert.py --index` shortcut

`python converter/post_convert.py --index --corpus <CORPUS_ROOT>` runs step 3
for you when `VAULT_SEARCH_DIR` points at a vault-search checkout (the repo,
containing `server/corpus.py`); set `VAULT_SEARCH_PY` to vault-search's
Python if it lives in another environment. It exits with `corpus.py`'s exit
code. With `VAULT_SEARCH_DIR` unset, or pointing at something that is not a
checkout, it runs nothing, prints `[skip]` and the exact command, and exits
0 — so a green run does *not* mean an index exists.

`INDEXER_SCRIPT` (an indexer with an `--incremental` / `--book <name>` CLI)
is **deprecated**: when set it still runs as before, with a warning on stderr,
and `--audit` still reads that indexer's `lance_db` / `textbook_chunks` table
under `VAULT_SEARCH_DIR`. vault-search's current indexer does not have that
CLI; move to `corpus.py add`.

Then verify a test query returns results (vault-search's README) before
telling the user it's ready. If the user does not want any of this, say
plainly that grep over `<OUTPUT_DIR>/` is the whole search story — that is a
supported configuration, not a degraded one.

## Step 6 — Placeholder substitution after copying the skills

Each copied `SKILL.md` references its scripts as `{REPO}/figures/...` or
`{REPO}/converter/...` — the scripts themselves stay in this cloned repo,
only the `SKILL.md` files move. Open each copied `SKILL.md` and replace every
`{REPO}` placeholder with the absolute path of this clone (e.g.
`C:\Users\you\textbook-to-note` or `/home/you/textbook-to-note`), so the
example commands resolve to the actual `converter/` and `figures/` code.

## Step 7 — Running the note-writing workflow

`workflows/note-writing.md` is the full specification for turning a
converted textbook chapter into a structured note. It is written generically
so you should adapt phases 2/3 (the optional pluggable enrichment stages) to
whatever domain-specific tools the user actually has — a clinical evidence
API, a regulations database, a literature search tool, or nothing at all if
their domain doesn't need it.

Before writing the user's first real note, walk through
`examples/example-note.md` with them so they can confirm the target format
(nested bullets, per-claim citations, figure embed style) matches what they
want before you commit to writing dozens of notes in that shape.

Then, for a real topic:
1. Follow Phase 1 → 1.5 as written — draft blind from the textbook corpus,
   skeleton before prose
2. Skip phases 2/3 unless the user has a relevant enrichment source
   configured
3. Run Phase 3.5 figure harvest through the `figure-remap` skill — never
   hand-crop images yourself, always go through the QC-gated entrypoint
4. Run Phase 4 to merge with any existing note on the topic, following the
   deconstruct-and-reslot approach — do not just append new content to old
5. Optionally run Phase 5 link suggestion

## Step 8 — The note-format hook

If you (the agent) or the user want a mechanical pre-flight check before
every note write — catching missing citations, unescaped table syntax,
widthless figure embeds, and similar structural issues — build a small
format-lint script and wire it in as a pre-write hook in your agent
framework. This repo doesn't ship one by default since notes-tool
conventions vary too much across users, but `workflows/note-writing.md`'s
self-check section lists exactly what such a hook should catch.
