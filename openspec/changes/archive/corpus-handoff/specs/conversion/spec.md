# Delta — conversion

## ADDED Requirements

### Requirement: A resplit output root is marked as a `t2n-corpus/1` textbook corpus

`convert.py --resplit <corpus> --out <dir>` SHALL ensure `<dir>/corpus.json` exists with the
JSON object `{"format": "t2n-corpus/1", "kind": "textbook"}`. When `<dir>/corpus.json`
already exists it SHALL NOT be written, whatever its content.

#### Scenario: fresh output directory
- **GIVEN** `--out` names a directory with no `corpus.json`
- **WHEN** `--resplit` runs (with or without `--book`)
- **THEN** `<out>/corpus.json` parses to exactly `{"format": "t2n-corpus/1", "kind": "textbook"}`
- **AND** every other file the run writes is byte-identical to a run of 0.8.0

#### Scenario: an existing marker with the same content
- **GIVEN** `<out>/corpus.json` already holds the expected object (any whitespace)
- **WHEN** `--resplit` runs, including with `--force`
- **THEN** the file's bytes and mtime are unchanged and nothing is printed about it

#### Scenario: an existing marker with different content (failure case)
- **GIVEN** `<out>/corpus.json` holds anything else — another `kind`, another `format`, or
  text that is not JSON
- **WHEN** `--resplit` runs
- **THEN** the file's bytes are unchanged
- **AND** stderr carries one line starting `[resplit] corpus.json` that names the file and
  says it was left as is
- **AND** the exit code is the same as without the file (0 for a normal run)

#### Scenario: the source corpus carries its own corpus.json
- **GIVEN** `<corpus>/corpus.json` exists (any content) and `<out>` has none
- **WHEN** `--resplit` runs, with or without `--force`
- **THEN** `<out>/corpus.json` is the expected object, not a copy of the source's file

#### Negative constraints
- `corpus.json` is never written by `--batch-dir`, `--batch` or single-file conversion.
- An existing `<out>/corpus.json` is never overwritten, deleted, or rewritten to normalize it.

### Requirement: `post_convert.py --index` hands the corpus to ask-your-library and never indexes itself

`post_convert.py --index [--corpus DIR]` SHALL choose exactly one route, in this order
(`<root>` = `--corpus DIR`, default `OUTPUT_DIR`):

1. `INDEXER_SCRIPT` set — print a deprecation warning to stderr that contains
   `INDEXER_SCRIPT is deprecated` and the text `corpus.py add`, then behave as 0.8.0 did
   (`<INDEXER_SCRIPT> --incremental`; `[skip]` when the path does not exist).
2. else `VAULT_SEARCH_DIR` set and `<VAULT_SEARCH_DIR>/server/corpus.py` exists — run
   `<python> <VAULT_SEARCH_DIR>/server/corpus.py add <root>`, where `<python>` is
   `VAULT_SEARCH_PY` when set, else the current interpreter; exit with that process's exit code.
3. else — run nothing, print `[skip]` and the exact command to run once ask-your-library is
   installed (`python <VAULT_SEARCH_DIR or path/to/ask-your-library>/server/corpus.py add <root>`),
   exit 0.

The full pipeline (no flag) runs the same route after verification, `INDEX.md` and the
figure registry. The coverage audit (`--audit`, and the audit step of the full pipeline) runs
only on route 1; on routes 2 and 3 it prints `[skip]` and that the audit is part of
`corpus.py add`.

#### Scenario: ask-your-library checkout configured
- **GIVEN** `INDEXER_SCRIPT` unset, `VAULT_SEARCH_DIR=<vs>` with `<vs>/server/corpus.py`
- **WHEN** `post_convert.py --index --corpus <root>` runs
- **THEN** exactly one subprocess runs, with argv `[<python>, <vs>/server/corpus.py, "add", <root>]`
- **AND** `post_convert.py` exits with that subprocess's exit code (0 → 0, 3 → 3)

#### Scenario: legacy INDEXER_SCRIPT
- **GIVEN** `INDEXER_SCRIPT=<script>` that exists (and `VAULT_SEARCH_DIR` set or not)
- **WHEN** `post_convert.py --index` runs
- **THEN** stderr contains `INDEXER_SCRIPT is deprecated` and `corpus.py add`
- **AND** exactly one subprocess runs, with argv `[<python>, <script>, "--incremental"]`
- **AND** `corpus.py` is not run

#### Scenario: nothing configured (failure case)
- **GIVEN** neither `INDEXER_SCRIPT` nor `VAULT_SEARCH_DIR` is set, and `<root>/corpus.json` exists
- **WHEN** `post_convert.py --index --corpus <root>` runs
- **THEN** no subprocess runs, stdout contains `[skip]` and the command
  `python "<path/to/ask-your-library>/server/corpus.py" add "<root>"`, exit 0

#### Scenario: nothing configured and the root is not a corpus yet (failure case)
- **GIVEN** as above, but `<root>/corpus.json` is absent (e.g. the default `OUTPUT_DIR`)
- **WHEN** `post_convert.py --index` runs
- **THEN** no subprocess runs, stdout contains `[skip]`, the command
  `--resplit "<root>" --out "<CORPUS_ROOT>"` and the command
  `python "<path/to/ask-your-library>/server/corpus.py" add "<CORPUS_ROOT>"`, exit 0

#### Scenario: VAULT_SEARCH_DIR is not an ask-your-library checkout (failure case)
- **GIVEN** `INDEXER_SCRIPT` unset, `VAULT_SEARCH_DIR=<d>` with no `<d>/server/corpus.py`
  (e.g. an old index cache dir)
- **WHEN** `post_convert.py --index` runs
- **THEN** no subprocess runs, stdout contains `[skip]`, the path `<d>` and the words
  `ask-your-library checkout`, exit 0

#### Negative constraints
- `post_convert.py` never imports `lancedb` or opens an index on routes 2 and 3.
- No route passes `--incremental` or `--book` to `corpus.py`.
- Route 2 never runs when `INDEXER_SCRIPT` is set.
