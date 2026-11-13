# corpus-handoff

Tier 1. Written 2026-11-13 under the maintainer's outcome-based mode (drive to an output
contract + validator, no per-spec review round).

## Why

An outside user following Profile C today cannot reach a searchable index:

- The docs say `INDEXER_SCRIPT` points at vault-search with an `--incremental` / `--book`
  CLI. vault-search's current textbook indexer (v3) requires `--corpus` and has no
  `--incremental`, so `post_convert.py --index` dies with an argparse error.
- Nothing says the corpus must be re-split (`convert.py --resplit`, which writes the
  `_manifest.json` the v3 indexer reads) before indexing.
- The guideline layout `_Guidelines/<Name>/` exists only in code; `--batch-dir` writes every
  PDF to `OUTPUT_DIR/<stem>`, so guidelines land beside the books.

The fix is a contract, not a better hook: this repo produces a corpus folder; vault-search
makes it searchable with one command (`server/corpus.py add <root>`). The format is
`t2n-corpus/1`, specified once in vault-search
(<https://github.com/drpwchen/vault-search/blob/main/docs/corpus-format.md>).

## What changes

1. `--resplit` writes `<out>/corpus.json` = `{"format": "t2n-corpus/1", "kind": "textbook"}`
   when the file is absent. An existing `corpus.json` is never overwritten.
2. `post_convert.py --index` no longer assumes an indexer CLI:
   - `INDEXER_SCRIPT` set → the old call, unchanged, plus a deprecation warning naming
     `corpus.py add`.
   - else `VAULT_SEARCH_DIR` points at a vault-search checkout → runs
     `<VAULT_SEARCH_DIR>/server/corpus.py add <root>` (`<root>` = `--corpus DIR`, default
     `OUTPUT_DIR`).
   - else → prints the exact next command and exits 0.
3. Docs (AGENTS.md Step 5, docs/setup-details.md, README + README.zh-TW Profile C,
   docs/architecture.md): Profile C is the sequence `convert.py` → `convert.py --resplit` →
   `corpus.py add <root>`; Profiles A/B need no RAG at all; guidelines are converted into
   `_Guidelines/<Name>/` by pointing `OUTPUT_DIR` at `<corpus>/_Guidelines` for a
   `--batch-dir` run of the guideline PDFs (no new flag).

## Not doing

- No indexer, embedding or LanceDB code in this repo; no copy of `corpus.py`.
- No change to OCR routing, table gating, figure QC, or the split / manifest logic.
- No change to where `--resplit` writes manifests (one per top-level folder, so
  `_Guidelines/_manifest.json` covers every guideline).
- No up-port of the maintainer's private `post_convert.py` pipeline (resplit plan → v3 indexer →
  per-source audit). That is a later change (B2). The legacy coverage audit keeps reading the
  v1 `lance_db` / `textbook_chunks` table, because it only runs on the legacy `INDEXER_SCRIPT`
  path whose v1 indexer writes that table.
- No `corpus.json` from `--batch-dir` / `--batch` / single-file conversion: those outputs have
  no manifests and are not a valid `t2n-corpus/1` corpus.
- No new `--batch-dir` flag for guidelines.

## Risk

- `corpus.json` is a new file in `--resplit` output; existing files are byte-identical.
  A consumer that treats every root file as data would see one more file.
- Users with `INDEXER_SCRIPT` set see a warning on stderr; their run is otherwise unchanged.
- `VAULT_SEARCH_DIR` changes meaning when `INDEXER_SCRIPT` is unset (a vault-search checkout,
  not an index cache dir). A user with only `VAULT_SEARCH_DIR` set got `[skip]` before and now
  gets either a run of `corpus.py add` (if the dir holds `server/corpus.py`) or a `[skip]` that
  says what the variable must point at.

## Affected specs

- `openspec/specs/conversion/spec.md` — two requirements added.
