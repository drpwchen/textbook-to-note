# Tasks — corpus-handoff

## Implementation

- [x] `resplit.run` writes `<out>/corpus.json` when absent; leaves any existing one; warns on a
      mismatch; source `corpus.json` not copied  → Requirement: A resplit output root is marked
      as a `t2n-corpus/1` textbook corpus
- [x] `post_convert.py`: three-route `--index`, `--corpus DIR`, `VAULT_SEARCH_PY`, audit only on
      the legacy route  → Requirement: `post_convert.py --index` hands the corpus to vault-search
- [x] `shared/config.py`: `VAULT_SEARCH_DIR` / `VAULT_SEARCH_PY` / `INDEXER_SCRIPT` documented
- [x] Docs: AGENTS.md Step 1.1/3/5, docs/setup-details.md, README + README.zh-TW, docs/architecture.md,
      guideline layout + conversion recipe

## Verification

- [x] `converter/test_corpus_handoff.py` covers every scenario above
- [x] Full suite green
- [x] Byte-identical: every file other than `corpus.json` from a resplit matches a run without
      the change (test compares against a run with the marker step disabled)
- [x] End to end in a temp dir: `--batch-dir` books + guideline (`OUTPUT_DIR=<raw>/_Guidelines`)
      → `--resplit` → layout `<root>/<Book>/`, `<root>/_Guidelines/<Name>/`, `corpus.json`
- [x] `corpus.py check <root>` on that output: 0 errors, 0 warnings, also `--strict` (vault-search working tree, 2026-11-13; it counts manifests=1, i.e. it does not yet see `_Guidelines/_manifest.json`)

## Ship

- [x] `CHANGELOG.md` entry (0.8.1)
- [x] Version tag `v0.8.1` (local)
- [x] Archive: fold delta into `openspec/specs/`, move this directory to `changes/archive/`
