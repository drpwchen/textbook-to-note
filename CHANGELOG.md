# Changelog

All notable changes to this project are documented here. The through-line since the initial
release has been **table fidelity** — a textbook's tables are where its densest, most citable
data lives, and also where PDF extraction fails most silently. Each entry below is a distinct
failure mode found by measuring real books, with a deliberate fix and a kill-switch.

The format is based on [Keep a Changelog](https://keepachangelog.com/); this project uses
loose semantic versioning.

## [Unreleased] — the behaviour contract moves into the repo

### Added
- **`openspec/` — the tool's behaviour contract, written down.** Four domain specs
  (`conversion`, `table-extraction`, `figure-extraction`, `note-workflow`) state what this
  tool does as testable requirements: each scenario's THEN is something a program or a
  person can observe, every threshold carries its number, and every requirement states what
  must *not* happen. They were extracted from this repo's own `README.md`, `AGENTS.md`,
  `docs/`, `CHANGELOG.md`, and the constants in `converter/convert.py` and `figures/*.py`,
  and describe behaviour that shipped as of 0.7.1 — **no behaviour changed in this entry**.

  The reason to have them: the rules this project keeps re-deriving in prose — *never tune a
  QC threshold to make a failing case pass*, *a default-ON behaviour needs a corpus
  measurement*, *the gate refuses rather than guesses*, *misbinding is flagged, never
  auto-repaired* — are now in one place a reviewer can check a change against, instead of
  being scattered across changelog entries nobody re-reads.

- **`.claude/hooks/spec-sync-guard.py`** — a `PreToolUse` hook that blocks a `git commit`
  staging `converter/`, `figures/`, `citations/`, `shared/`, `skills/`, `workflows/`, or
  `templates/` while staging nothing under `openspec/` and no `CHANGELOG.md` entry.
  Test-only changesets are exempt; `[no-spec]` in the commit message bypasses it. Written in
  Python rather than the usual bash+`jq` so it runs unchanged on Windows, and it fails open
  on any unexpected condition — a guard that blocks commits for its own reasons is worse
  than no guard.

- **`.githooks/commit-msg` — the guard now runs for every committer, not just some sessions.**
  The `PreToolUse` hook above is only loaded when the Claude Code session's project root *is*
  this repo; a session rooted elsewhere never reads `.claude/settings.json`, so the guard
  never fires. That was measured on 2026-08-20 in a sibling repo running the same guard: a
  commit touching only implementation code went through with exit 0, and the guard's own
  test suite passed 21/21 the whole time. **The logic was correct; nothing was connected
  to it.**

  The git hook has no such condition — Claude, another agent, or a person typing by hand all
  go through it. It is `commit-msg` rather than `pre-commit` because the `[no-spec]` escape
  hatch lives in the commit message, which `pre-commit` cannot see; `commit-msg` also runs
  after the index is final, so `git commit -a` content is covered without guessing at flags.
  Merges, rebases, cherry-picks and reverts pass through untouched — those replay decisions
  someone already made. Both entry points call one `verdict()`; a second copy of the rule
  would drift from the first within days.

  **Install once per clone** (git hooks do not travel with a clone):

  ```
  git config core.hooksPath .githooks
  ```

  Without it, commits proceed as before — this is defence in depth, not the only defence.

- **`.claude/hooks/test_spec_guard_git.sh`** — 11 behaviour tests that run real `git commit`
  calls in a throwaway repo against the real guard and the real hook file. The existing
  synthetic-payload tests prove the *logic*; these prove the *wiring*, and the 2026-08-20
  incident is precisely the failure the first kind cannot see.

- **`.gitattributes`** — pins `.githooks/*` and `*.sh` to LF. With `core.autocrlf=true` a
  checkout rewrites the hook to CRLF and `#!/bin/sh` fails as `bad interpreter: /bin/sh^M`,
  which would take the guard down silently.

- **`CLAUDE.md`** — routes the repo's two AI audiences apart: an agent *deploying* this tool
  for a user reads `AGENTS.md`; an agent *changing this repo* reads `openspec/README.md`.

- **Change tiers — how much ceremony a change earns, decided before it starts**
  (`CLAUDE.md`). A three-tier rule: one follow-up prompt fixes a misread requirement → no spec, `[no-spec]`, tests still
  green; unambiguous scope → half a page of acceptance criteria; OCR routing, table gating, or
  release discipline → the full cycle plus an independent verifier pass. Running the full
  ceremony on every change was costing more than it caught, and the alternative to *some*
  ceremony was never *less* ceremony — it was the spec quietly becoming a changelog again.
  The tier test is "cheap to notice and cheap to undo", **not "small diff"**: a two-line change
  with fuzzy scope is exactly where an agent improvises. Outside contributors default to
  Tier 1. No behaviour changed.

## [0.7.1] — 2026-08-19 — A check that asks a model cannot be the one that blocks

### Changed
- **The QC chain is now model-free: the OCR long-line check (`qc_text_contamination`) and the
  caption-match check (`qc_caption_match`) are no longer called at all**
  (`figures/figure_qc_gate.py`, issue #16). Two checks remain, both pure computation, both
  blocking: whitespace fill and fitz text bleed. `figure_remap.py extract` is documented as a
  deterministic contract: a `pass` is the figure the caption owns, a `fail` is a correct
  refusal. It wasn't one — the long-line check asked the local vision model to transcribe the
  crop and counted lines ≥ 30 chars, so the gate inherited the model's run-to-run variation,
  and a crop whose count sat near the threshold of 7 came back `pass` on one run and `fail` on
  the next with no input changed.

  **Measured** on 25 crops (the ones that actually reach this check, out of 48 figures across
  12 born-digital textbooks), each crop run 5×:

  | | drifting line count | verdict crossed the threshold |
  |---|---|---|
  | unchanged code | 16/24 | 7/24 |
  | greedy decoding pinned | 10/25 | 2/25 |

  Pinning the decode removed the *sampling* half and nothing else. What survived has a shape:
  every unstable crop reads `[first, x, x, x, x]` — only the FIRST call on a crop differs,
  because it is computed against whatever the previous image left in the server's cache;
  repeats hit that cache and agree. An extraction only ever makes that first call. End-to-end,
  48 specs run twice, `status` + output sha256 compared: 3/48 flipped before, 1/48 with
  decoding pinned, 0/48 with the calls removed.

  So there is no threshold to fix, and no "advisory log" worth keeping either: a per-crop
  number that changes between runs cannot gate *or* inform, and it cost ~2 s of GPU per
  successful extraction to record. Both model-backed checks were removed from the chain
  outright (the functions remain as opt-in diagnostics, marked legacy). With them gone, the
  strict extraction path — the default, and the only path the note-writing workflow uses —
  **makes no ollama call at all**; the only vision call left anywhere in the gate is the bbox
  suggestion inside the discouraged `--no-strict` fallback ladder, whose proposal must still
  pass the computed checks. Whether a QC-passed crop is *worth embedding* is judged where it
  already lives: the workflow's frontier-tier classification step, which reads every crop
  before any embed (added in 0.7.0).

  **Removing them costs nothing on the path that matters:** `qc_text_bleed` reads the PDF's
  own text layer and catches the same failure mode — a crop that swallowed a sentence — with
  better evidence, and the scanned backend never enters this chain at all
  (`figure_scanned.py` runs its own whitespace-only QC). `qc_degradation()` accordingly only
  tracks the two computed checks. One semantic side effect, stated for the record: the QC
  log's per-book fail rate (which drives the `purge_recommended` suggestion at >30%) no
  longer includes contamination fails, so books' fail rates will read lower than under ≤0.7.0
  — the two counts are not comparable across the boundary.

  **Verified** on a rebuilt set of 48 specs across 12 books (fig_id + page harvested from the
  converted corpus's REF markers), run twice with the model reachable and once with it
  unreachable: 0/48 differences in `status` or output sha256 between any pair of runs —
  23 passes and 25 deterministic refusals, identical every time. The verdict depends on
  nothing but the PDF, and turning the model off does not change a single crop.

### Added
- **The remaining legacy vision calls are pinned to greedy decoding** — `temperature 0`,
  `top_k 1`, fixed `seed` (`_vision_options()`; the seed is overridable with
  `T2N_OLLAMA_SEED`). They previously sent `temperature 0.1` with no seed. This matters only
  for the `--no-strict` ladder's bbox suggestion, which feeds a crop attempt; it keeps that
  proposal from drifting between runs.
- **Five contract tests**, all hermetic — the transport is stubbed, so CI needs no model and
  no GPU: `run_local_qc` never calls a vision helper (a helper that raises on call proves
  it), the reasons list carries exactly the two computed checks, a computed check still
  blocks, a skipped computed check still degrades the gate, and the legacy vision helpers
  send greedy decoding options when invoked directly.

### Fixed
- **Documentation that claimed a determinism the code did not have.** The README described
  "whitespace-fill, text-bleed, and OCR-long-line checks" as running "before any AI is allowed
  to judge the crop" — the third of those *was* the AI. Corrected in `README.md`,
  `README.zh-TW.md`, `docs/architecture.md`, `skills/figure-remap/SKILL.md`,
  `figures/CALIBRATION.md`, and the module and function docstrings, which now say plainly
  that the QC chain is two computed checks and no model. Also corrected: the READMEs listed
  ollama as a figure-QC requirement (it no longer is), and `skills/figure-remap/SKILL.md`
  described `visual_check.py` as "used inside the gate" (it never was — only the legacy
  whole-book batch tools call it; both scripts are now marked legacy, superseded by the
  workflow's per-crop frontier classification).

## [0.7.0] — 2026-08-19 — A crop that passes QC still has to be a figure

### Added
- **A deterministic junk pre-gate on extracted crops** (`figures/pregate.py`, ported from the
  private deployment of this pipeline). The QC gate answers *"is this crop the figure the caption
  names?"* — a correctness question, where a wrong answer puts a wrong figure in a note. It has no
  opinion on a second question: *"is this a figure at all?"* A full-width chapter-title banner at
  the top of a chapter's first page has caption-like text above figure-like geometry, crops
  cleanly, and passes every QC check, because nothing in QC is looking for "this is typography".

  Two rules, both on page metadata fitz already has at crop time — no model, no pixels beyond a
  standard deviation, no per-book tuning: `blank` (crop pixel std < 3.0) and `banner` (crop starts
  at the top of the page, ends in its upper third, spans ≥ 95% of page width, and contains fewer
  than 40 vector paths). A kill returns `status:fail` with `hard_fail:false` and a
  `reason:"pregate=…"` — a **skip**, not a miss: the caller drops that fig_id instead of retrying,
  because a retry only re-crops the same banner.

  **Measured**, thresholds fitted on a dev set of n=301 crops with frozen reference labels and then
  measured unchanged on a later held-out set of n=244: zero kills on crops labelled embed or
  callout on **both** sets (the hard requirement — a killed good figure has no downstream rescue);
  banner recall **42/45** on dev and **50/50** on held-out; killed banners carried ≤ 19 vector paths
  while good figures with the same top-of-page geometry carried ≥ 72, so the threshold of 40 sits in
  an empty band. Known cost, reported rather than hidden: a damaged figure lying under a full-width
  banner is killed too — 6 such crops per set go un-embedded, which is the price of the
  zero-false-kill requirement.

  A third rule keyed on table detection was **tried and rejected**: fitz `find_tables` fires on
  flowcharts and designed figures, and on the held-out set every threshold combination killed
  crops labelled embed/callout, three of them flowcharts. `figures/CALIBRATION.md` records why,
  so the next person does not re-derive it.

  **Scope and safety**: the pre-gate needs the winning crop's bbox, so it runs on the born-digital
  geometric path and abstains on the scanned `caption_anchor` backend and the `existing` fast path.
  It is fail-open — any exception inside it means no kill — so it can never become a new source of
  missing figures through its own bugs. Unlike the QC thresholds, its numbers are **not per-book
  knobs**: `pregate.py` and `CALIBRATION.md` both say not to refit them on the set you then quote,
  which is what turns a measurement into a claim.

- **The figure-classification step of the note workflow, with its frozen prompt**
  (`figures/classify_prompt.txt`, and a new section in `workflows/note-writing.md`). A QC `pass`
  says the crop is the raster the caption owns; it does not say the crop is worth embedding. The
  workflow previously went straight from `pass` to embed, so tables, truncated figures, and the
  banners the pre-gate cannot see reached notes. Now every crop is classified in one batch before
  the first embed — image and caption only, never the filename or figure number — into
  embed / callout / skip / retry, with one deterministic abstention rule on top: an `embed` whose
  `usable` is not `yes`, or whose `crop_quality` is `uncertain`, or whose `confidence` is `low`,
  becomes `retry`, the one case a human looks at the image.

  **Measured** on the same held-out set of 244 crops: **Claude Sonnet 5** with this prompt and rule
  let **1 junk crop of 100** through; **Claude Haiku 4.5** let **6 of 100** through on the same set,
  and every one of its misses was a high-confidence perceptual error with all four fields
  self-consistent — a table called `other`, a chapter banner called `imaging`. The abstention rule
  reads the model's own fields, so it cannot reach that class of error; the cheaper tier is not
  substitutable with more rules here, which is why the workflow says to run this step on a
  frontier-tier model. Both numbers are measurements of those models on that set, not properties of
  the pipeline, and the prompt is frozen for the same reason: reword it and the numbers no longer
  describe it.

- **13 contract regression checks for the pre-gate** in `figures/test_contract.py` — rule verdicts,
  fail-open behavior on missing pdf/page/bbox and on an unreadable PDF, and the kill-path contract
  shape (fail + `hard_fail:false` + skip-not-retry reason + crop removed). All are pure-unit, so
  they run in CI without a fixture PDF.

## [0.6.3] — 2026-08-16 — The GPU rungs can take a machine-wide lease

### Added
- **Optional GPU-lease integration for the Surya and Docling rungs** (ported from the private
  deployment of this pipeline, where it has been running since 2026-08-10). On a machine that runs
  several GPU jobs (OCR batches, embedding indexers, transcription), two of them loading models
  onto the same card at once ends in OOM or a silently throttled crawl. `_gpu_lease_ctx()` in
  `converter/convert.py` now brackets the GPU rungs with a FIFO lease when a broker is present:
  Surya per adapter subprocess batch (so queued short jobs can slip in mid-book), Docling for the
  worker's whole lifetime (its model sits in VRAM until `close()` — `convert_pdf`'s self-created
  worker and `scan_table_pass.py`'s batch worker both hold it create-to-close).

  **No setup required**: without a broker the context is a `contextlib.nullcontext` and behavior
  is byte-identical to 0.6.2 — the pipeline stays standalone. To integrate, point
  `T2N_GPU_LEASE_DIR` at a directory whose `gpu_lease.py` exposes
  `lease(name, min_free_mb=..., timeout=...)` as a context manager. The fitz/pdfplumber path is
  pure CPU and never takes the lease.

## [0.6.2] — 2026-08-16 — The caption counter learns the same dashes the fig-ref pass already knew

### Fixed
- **The whole-book table-loss warning never fired on books that number tables with an en dash**
  ([#13](https://github.com/drpwchen/textbook-to-note/issues/13)). Both book-level checks
  (zero-table and partial-loss) key on `total_captions`, and the caption counter's regex accepted
  only a literal `.` or ASCII `-` between the chapter and table number. Morgan & Mikhail's Clinical
  Anesthesiology 7e writes "TABLE 1–1" (en dash) throughout: the counter saw **0 captions**, the
  `>= 10 captions` guard on both branches never opened, and a book that lost essentially all of its
  tables (2 extracted against ~600 caption/ref mentions) shipped with **no `[!warning]` block at
  all** — precisely the silent outcome those checks exist to prevent.

  This is the whole-book twin of [#10](https://github.com/drpwchen/textbook-to-note/issues/10)
  (figure refs blind to en dashes in Katzung 16e). The fig-ref fix moved its dash set into
  `shared/config.py` as `SEP_CLASS`; the caption counter simply never adopted it. It does now —
  one regex separator changed, no thresholds touched, extraction itself unchanged. On the
  reporter's book the partial-loss branch fires as designed and the warning block appears at the
  top of the markdown.

  Note the warning is the fix's whole payload: the underlying loss (tables pdfplumber cannot see
  on those pages) is a known limitation the warning exists to surface, not something this release
  changes.

  Tests: caption counting across en dash / em dash / hyphen / period numbering plus a
  false-positive control, in `test_table_fixes.py` (the end-to-end counter wiring was already
  covered by case 6; a PDF fixture cannot carry the en dash itself because fitz's base-14 font
  maps it to "?").

## [0.6.1] — 2026-08-09 — The reversed-cell guard is judged per row, not per table

### Fixed
- **A sideways header strip on an otherwise upright page slipped past both guards.** 0.6.0's
  repair guard stands a page upright only when its glyphs are *overwhelmingly* non-upright, and its
  detect guard aggregated the reversed-vs-forward tally over the **whole table**. Neither covers the
  common mixed page: a landscape table imposed sideways above upright body text, or a page that
  already carries `/Rotate 90` so only part of its glyphs come back non-upright. The page is too
  mixed to stand upright — rotating it would lay the upright half on its side — and the table's
  upright body rows contribute enough forward tokens to outvote the reversed header, so the
  table-wide ratio never trips. The reversed row reaches the markdown with a full body of real data
  hanging under column labels that were read backwards and out of order.

  The detect guard is now evaluated **per row as well as over the table**: one row meeting the same
  unchanged thresholds (≥6 reversed-only tokens, beating forward matches ≥3:1) rejects the table.
  No threshold was loosened — only the granularity of the tally — and rejecting costs no content,
  since the page prose that fitz read correctly is emitted either way.

  Blast radius measured before the change, over all 47,350 tables in a 286-book physical-medicine
  corpus using each table's own page prose as the oracle: **17 tables newly rejected, in 5 books**
  (Braddom 6e and 7e Table 43.1 "Inherited and Acquired Myopathies", two VA/DoD CPG recommendation
  categorization appendices, one PRM research handbook). Every one was inspected and every one was
  genuinely reversed — no good table is threatened by the rule. The failure predates 0.6.0: these
  are tables the old aggregate happily emitted.

  Tests: a mixed table (reversed header + three upright body rows) that the table-level rule scores
  as clean, plus negative controls for an upright header, a 5-token row (one below the floor) and a
  row that mixes reversed and forward text. Mutation-verified — dropping the per-row branch,
  lowering the floor to 5, and removing the per-row ratio test each break a distinct test.

## [0.6.0] — 2026-08-09 — Tables printed sideways stop coming out backwards

### Fixed
- **Sideways-printed pages emitted character-reversed tables** (#11, contributed by
  [@retyu3245-arch](https://github.com/retyu3245-arch)). A landscape table is often imposed
  *sideways* on a portrait page: the page is `/Rotate 0`, but every glyph carries a rotated text
  matrix. fitz reads such a page correctly, so the prose is fine — but pdfplumber orders words by
  `(top, x0)` assuming upright text, so it reads the page bottom-to-top and hands back every cell
  reversed with its columns scrambled: `periodontitis` → `sititnodoirep`.

  This is the failure mode this project exists to end. The log is green, the markdown looks like a
  populated table, and grep never errors — it just silently never matches. The data is present,
  wrong, and unfindable.

  Two independent guards, because they fail differently. **Repair:** a page whose glyphs are
  overwhelmingly non-upright is re-parsed through an in-memory one-page copy with `/Rotate` set so
  the text stands upright; the direction comes from the text matrix rather than being hardcoded,
  and both stand-in pages come from the same bytes so the fitz geometry matches the pdfplumber page
  it is paired with. **Detect:** a table whose tokens match the page's own text layer far better
  reversed than forward is rejected through the existing pseudo-table channel — no word list and no
  language assumption, so a table is rejected only on evidence from the source PDF itself. The
  repair leaves an HTML comment and is counted in `stats["sideways_pages"]`; a rewrite is never
  silent. Kill-switch `T2N_SIDEWAYS=0` disables both.

  Measured on a six-book dental corpus (~7,100 pages): 249 sideways pages repaired in one 12th
  edition alone, 1,784 reversed tokens → 0, usable tables 92 → 334. Measured again before merge on
  an unrelated physical-medicine corpus of 286 books: **34 books, ~99 tables, 378 rows** were
  reversed — electrotherapy parameter tables, hip-OA injection trial tables, differential-diagnosis
  grids. Not a typesetting quirk of one publisher.

  Pages that are not sideways are byte-identical before and after, in both the plain and the
  cross-page-merge path.
- **The Docling rung could still emit reversed cells.** The repair above only reaches the
  pdfplumber path: when `T2N_DOCLING=1` and Docling returns tables for a page,
  `extract_tables_md()` is never called for it, so neither guard ran. The detect guard now rides on
  that rung too — its oracle (the page's fitz text) is already in hand there, so the cost is a
  string comparison. A rejected Docling table also lets the page fall through to pdfplumber, which
  re-parses it upright and can recover the table for real. The reject list is now extended rather
  than replaced at that hand-off, so a Docling-rung rejection is still reported when pdfplumber
  then handles the page.
- **The figure crop fast path looked under the source-PDF folder** (#12, contributed by
  [@drivysu](https://github.com/drivysu)). `figures/figure_qc_gate.py` imported `BOOKS_DIR` as
  `TEXTBOOK_MD_BASE` and then looked for the pre-extracted crop at `<root>/<book>/figures/`. But
  `BOOKS_DIR` is the *source PDF* folder everywhere else in the repo (`books.json`,
  `already_converted.json`, `--batch-dir`, `rename_skip.json`), and a PDF folder has no per-book
  markdown directories and no `figures/` — so no single value could satisfy both readings. Setting
  it for the converter broke the figure cache; setting it for the figure gate broke the converter.
  The crop root now resolves from `OUTPUT_DIR`, where `convert.py` writes.

  Quiet rather than loud: under `--source auto` the cache simply always missed and the entrypoint
  fell through to strict geometric re-extraction, so figures still came out *correct*, just
  re-extracted on every call. `--source existing` is the loud case — cache-only, so it hard-failed
  on a corpus where the crops did exist.

### Changed
- The figure crop root now resolves `T2N_FIGURE_MD_BASE` → `TEXTBOOK_DIR` → `OUTPUT_DIR`.
  `citations/` already used `TEXTBOOK_DIR` for this same "converted markdown corpus", so a corpus
  that has been pointed at once no longer has to be pointed at twice under a second name.

## [0.5.0] — 2026-08-08 — Chapter references are checked, not trusted

### Added
- **`citations/textbook_chapter_index.py` + `citations/textbook_ref_lint.py`** — proves every
  `Author Ch.N` citation in a note points at a chapter that actually exists, and prints that
  chapter's real title so a human can see whether it matches the claim. The gap this closes: 0.4.0
  made *figure filenames* exact-match strings, but a book plus a chapter number is one too — and a
  wrong one reads perfectly. Right surname, plausible number, true claim, wrong location. Two
  failure modes that survive human review because the content behind them is usually right:
  - **One name, several books.** A note cited `ElMiedany Ch.5`; that chapter is Psoriatic Arthritis
    in the rheumatology volume, and the intended source was a different ElMiedany book entirely.
    Nothing in the citation says which book it means. Fix by citing the edition
    (`Braddom 7e Ch.49`) or pinning a default in `_chapter_index.defaults.json`.
  - **File sequence cited as chapter number.** `ch105_Medical_Complications_of_SCI.md` is chapter 7
    of its book. "Ch.105" leads to the right content and to a location that does not exist on paper.
  The index normalises the six filename conventions the converter has emitted (including
  `<part>_<chapter>_Title.md`, and one-directory-per-chapter books) and records per-book
  **coverage**, because a gap in a half-converted book means *cannot verify*, not *wrong citation*.
  Verdicts: `BAD_CHAPTER` / `AMBIGUOUS` / `FILE_INDEX` (exit 1), `UNVERIFIABLE` (exit 3 — never a
  pass), `OK`. Corpus location comes from `--textbook-dir` or `TEXTBOOK_DIR`.
- **`citations/test_chapter_refs.py`** — hermetic regression suite (fake corpus in a temp dir, no
  textbooks or network needed), wired into CI. Every case locks an invariant that was a real bug
  found while running the lint over a live corpus: strategy ordering (ranking by "most chapters
  matched" reads `02_8_Title.md` as chapter 2 and mislabels a whole book), leading-zero
  normalisation, possessive stripping that must not eat a real trailing `s`, numeric — not lexical —
  ordering when mapping a file sequence back to its chapter (`ch100` sorts before `ch11`), and the
  coverage threshold that keeps a gap in a partly-converted book out of the offender list.

### Changed
- **`workflows/note-writing.md`** — new drafting rule ("read the chapter number off the corpus,
  never off the topic") with both failure modes and the commands, plus a self-check item. Exit 3 is
  called out explicitly as *not* a pass.

## [0.4.0] — 2026-08-07 — Exact-match strings

### Added
- **`figures/figure_embed_lint.py`** — proves every figure a note embeds actually exists. The
  extract contract already returns the authoritative path in its `file` field, but nothing checked
  that notes used it: a filename rebuilt from the `--out` template or from the `Fig_{id}_{Book}`
  convention looks right in review and only breaks at render time. Reports MISSING (a guessed name,
  or an embed written for an extract that actually failed) and CASE MISMATCH (opens on
  Windows/macOS, breaks on git and Linux). Exits 0 clean / 1 offenders / **3 unverifiable** — a lint
  that can't reach its source of truth must never read as a pass.

### Changed
- **Exact-match strings are copied, never generated** — documented in
  [`figures/CALIBRATION.md`](figures/CALIBRATION.md) and the `figure-remap` skill. `--caption` is
  pasted verbatim from the converted markdown, `--book` verbatim from the corpus directory name, and
  a fig_id that isn't in the manifest is a **stop**, not something to construct from the pattern you
  just calibrated (a plausible-but-wrong fig_id makes geometric matching claim the *neighbouring*
  figure — and it passes QC, because it is a perfectly good crop of the wrong thing). Notes embed
  the contract's `file` value verbatim; a failed extract gets a `<!-- TODO -->` comment and never an
  embed. No behavior change to the extractor itself.

### Added
- **CI (`.github/workflows/ci.yml`)** — GitHub Actions: the five hermetic test scripts (`converter/test_review_queue.py`, `test_table_fixes.py`, `test_table_merge.py`, `test_surya_ocr.py`, `figures/test_contract.py`) on an ubuntu + windows matrix, plus a gitleaks full-history secret scan on every push and PR. `test_docling_tables.py` stays local-only (needs a full Docling install). No runtime behavior change.
- **Corpus-maintenance tools for whole-corpus and post-batch operations**, ported from the
  production toolchain ([`docs/corpus-maintenance.md`](docs/corpus-maintenance.md)). All three act
  on an existing corpus rather than a single conversion, and the two that rewrite files default to
  a dry-run report:
  - `converter/triage_report.py` turns a batch manifest into BLOCKED / REVIEW / ODD / OK buckets,
    each with the reason and the evidence, so a bad conversion is caught by the report instead of
    by a human happening to look at that one book. Thresholds sit near the p90 of a reference
    corpus — an absolute cutoff like flag-rate > 0.40 flags ~84% of a clinical corpus, i.e.
    nothing — and are meant to be re-derived whenever the table pipeline changes.
  - `converter/strip_fake_tables.py` demotes false-positive tables — single-column pipe-wrapped
    text, mostly-empty diagram-label grids, and small word-split fragments — back to plain text in
    a corpus converted before the frame-reject fixes shipped. Content-preserving (cell text is
    re-emitted as lines, so grep still hits), a book's own `Table N.N` label vetoes demotion, and
    `--only-completed` / `--exclude-pending` keep it from racing a running batch.
  - `converter/expand_ligatures.py` expands typographic ligatures (`ﬁ` / `ﬂ` / `ﬀ` / ...) in a
    corpus converted before `clean_text()` learned to — the raw single-codepoint glyphs leave a
    book unsearchable for any word that contains one (`speciﬁc` does not match `specific`). It
    reuses the converter's own ligature map (so the two never drift), changes only those glyphs,
    and is idempotent.
  - `converter/scan_table_pass.py` recovers tables from scan-only books that routed through Surya
    OCR and so came out as running text with zero tables. It is caption-targeted: it finds the
    pages whose OCR text names a table and asks the Docling rung only about those pages (plus each
    successor), rather than rendering every page of an 800-page book.

## [0.3.1] — 2026-07-23 — Contributor fixes and production hardening

The first release to carry outside contributions. Three fixes to corpus text fidelity —
typographic ligatures, en/em dashes in figure references, and presentational HTML in EPUBs —
arrived as pull requests from [@pig18888](https://github.com/pig18888); a follow-up then
collapses the project's scattered dash definitions into one. Alongside them: two fixes proven on
a full 154-book production run before landing here, and a docs-only split into three usage
profiles so an agent installs only the stack the user actually asked for.

### Fixed
- **Typographic ligatures are expanded so the corpus stays searchable**
  ([#6](https://github.com/drpwchen/textbook-to-note/pull/6), thanks
  [@pig18888](https://github.com/pig18888)). PDF text layers emit `ﬁ`, `ﬂ`, `ﬀ`, `ﬃ`, `ﬄ`, `ﬅ`,
  `ﬆ` as single codepoints, so a search for `specific` finds zero hits in a book that holds 116
  occurrences of `speciﬁc`. `clean_text()` now maps them to their ASCII pairs explicitly — not via
  NFKC, which would also rewrite the scientific notation (`μ`, the U+2212 minus, superscripts) the
  corpus needs to keep intact.
- **Figure and table references are matched across the full range of dashes**
  ([#8](https://github.com/drpwchen/textbook-to-note/pull/8), thanks
  [@pig18888](https://github.com/pig18888)). Publishers typeset figure numbers with whatever dash
  their house style uses; Katzung 16e runs on en dashes throughout (`Figure 33–3`) — 717 en dashes
  to 8 ASCII hyphens — so the old `[-.]` pattern matched 14 references and left figure extraction
  blind for the whole book. The detection patterns now span U+2010–U+2015 and U+2212.
- **Presentational HTML is stripped from converted EPUBs**
  ([#9](https://github.com/drpwchen/textbook-to-note/pull/9), thanks
  [@pig18888](https://github.com/pig18888)). calibre leaves `<a>` / `<div>` / `<span>` wrappers
  that pandoc passes straight through in gfm; they carry no meaning but on one 52-chapter textbook
  they were 41% of every character, diluting each chunk the indexer builds. `strip_epub_noise()`
  drops the wrappers and keeps their inner text — semantic tags stay in, since `<sup>` / `<sub>`
  carry chemical formulae and charges and the table tags carry structure.
- **One dash definition, shared by both sides of the figure pipeline**
  ([#10](https://github.com/drpwchen/textbook-to-note/issues/10)). #8 widened the converter's
  figure/table reference detection to U+2010–U+2015 and U+2212, but the figure stage kept its own
  narrower, hand-maintained copies (`qc_metrics._SEP`, `figure_qc_gate._DASHES`,
  `figure_scanned._DASHES`) — so a book typeset in, say, non-breaking hyphens would get
  `<!-- REF: ... -->` markers the caption matcher silently failed to consume. The canonical set
  now lives in `shared/config.py` (`DASH_CHARS` / `SEP_CLASS`) and all four call sites import it;
  the OCR-substitution neighbour checks inside `normalize_fig_id()` were widened with it. Existing
  normalize/caption tests re-run: 242 pass / 0 fail / 5 skip, unchanged from baseline.
- **Docling degraded-book warning no longer cries wolf on every book after one failure.** The
  warning tested the worker's `last_error` for truthiness, but that field is sticky by design (the
  *last* error, never cleared on success) — with a shared warm worker, a single page timeout in book
  61 of a 154-book run flagged the next 62 books as degraded. The worker now keeps a cumulative
  `failure_count` and the book-level check compares a before/after snapshot, so the warning names
  the number of pages *this* book actually lost to fallback.
- **A PDF whose page tree pdfminer refuses is repaired instead of silently losing every table.**
  pdfplumber's parser is stricter than MuPDF about damaged page trees / xref, and returns zero
  pages where fitz reads the book fine — which costs 100% of the book's tables while the prose
  converts normally (two references of 464 and 752 pages hit this in a corpus run). When
  pdfplumber parses 0 pages but fitz sees a full book, the PDF is rewritten through fitz
  (`garbage=4, clean=True`) to a temp copy and the table pass retried on that; the repair is
  reported in the book's warnings either way. Both fixes were proven in a 154-book production
  run before landing here.

### Changed
- **Three usage profiles replace the implicit all-or-nothing setup** (docs only). The repo reads
  as one pipeline you either adopt whole or not at all, but the parts stack cleanly:
  **A** converter-only (markdown + `grep`), **B** A plus the note workflow and figures,
  **C** B plus semantic search via the companion
  [vault-search](https://github.com/drpwchen/vault-search) indexer. Both READMEs open with the
  table, and `AGENTS.md` now asks which profile *first* — before any install — and marks every
  later step with the profiles it belongs to. The failure this prevents is an agent helpfully
  installing ollama, an embedding model, and the skills for a user who only ever wanted greppable
  markdown; the same over-eagerness that made the OCR path in 0.3.0 expensive.
- **The Surya-adapter reference now reports measured GPU numbers.** The GPU smoke run finished
  after 0.3.0 was tagged, so [`docs/surya-adapter.md`](docs/surya-adapter.md) now carries it
  beside the CPU run: ~0.15 pages/s end to end and a capped inference server fitting in ~2.3 GB
  VRAM on an 8 GB card, closing [#4](https://github.com/drpwchen/textbook-to-note/issues/4).
  Flagged as an order-of-magnitude anchor — n=5 pages, measured once, on a card shared with
  another job — not a benchmark.

## [0.3.0] — 2026-07-22 — The OCR rung, and telling the truth in the docs

Where 0.2.0 was about table fidelity, this one is about the two places the project was
quietly asking users to trust something that wasn't there: an OCR rung whose central
component was never shipped, and documentation describing behaviour the code did not have.
Both were found the same way — by an outside user's agent following the docs literally and
burning hours on it ([#3](https://github.com/drpwchen/textbook-to-note/issues/3)).

### Added
- **A reference OCR adapter ships** — `converter/surya_adapter.py`, targeting Surya 0.22.x, plus
  [`docs/surya-adapter.md`](docs/surya-adapter.md) ([#4](https://github.com/drpwchen/textbook-to-note/issues/4)).
  `SURYA_ADAPTER` had been a first-class config value, required for `surya_available()`, executed
  as a subprocess — and the file it pointed at existed only on the author's machine, with no
  published interface. An agent following the setup guide reasonably assumed it was part of the
  repo, could not find it, and reverse-engineered one against the removed `surya.ocr` API. The doc
  now publishes the contract (JSON Lines on stdout, `fixture` + `blocks[].text` + **required**
  `blocks[].bbox`, one line per image even when blank, logging on stderr, non-zero exit fails the
  batch) so any engine can take that rung, and states plainly why an adapter written for Surya
  0.17 dies on 0.22: `surya.ocr` is gone, `FoundationPredictor` became `SuryaInferenceManager`,
  and blocks now carry `html` rather than flat text.
- **Documented, tested memory caps for the OCR inference server.** Surya 0.22 serves its VLM
  behind llama.cpp or vllm, and llama.cpp sizes its KV cache as
  `parallel × ctx_per_slot` — defaulting to `8 × 12288 = 98304` tokens for a model whose weights
  are ~1.4 GB. That cache, not the model, is what became a reported 48 GB run for one user. The
  doc ships a launch command with the caps in it; measured peak RSS with them was **3121 MB**.
- **OCR output QC gate** — the OCR rung now fails loud like every other rung. `surya_ocr_pdf()`
  counts unparseable adapter lines, images with no answer, and empty pages, and **raises instead
  of writing markdown** when the empty-page ratio exceeds `T2N_OCR_EMPTY_PAGE_MAX` (0.35) or mean
  characters per page falls below `T2N_OCR_MIN_CHARS_PER_PAGE` (200). Previously a JSON parse
  failure was silently `continue`d and a near-empty book reported success in KB — the shape of a
  real incident where a scanned book produced 20 KB where ~1.6 MB was expected (~25 chars/page).
  Thresholds are calibrated against two real scanned references (689 and 788 pages: empty ratios
  0.054 / 0.003, mean 1791 / 2005 chars per page), so they clear legitimate blank and plate pages
  by roughly an order of magnitude. Per-book counters are surfaced in the batch report.

### Fixed
- **Two-column reading order on OCR'd pages** — the OCR path sorted blocks by `(y0, x0)`, which
  walks across the gutter and back on every band of a two-column page, interleaving the columns.
  Every character is present and individually correct, so no downstream check could see it — the
  same defect the fitz path already carried a dedicated column sort for. Both paths now share one
  `column_order_boxes()` (`T2N_COLUMN_SORT=0` restores the old sort on both). Byte-identity of the
  fitz path across two full books was verified as part of the same re-conversion run used for #5.
- **`DOCLING_DEVICE` defaults to `auto`** instead of a hardcoded `"cuda"`, resolving CUDA → Apple
  Silicon MPS → CPU inside the worker. A CPU-only machine no longer asks Docling for CUDA, and
  `mps` is now reachable at all (previously anything that wasn't the literal `"cuda"` mapped to
  CPU). Resolution logic is unit-tested without torch or a GPU; **the real MPS path is unverified**
  — there is no Apple Silicon in the development environment.
- **pdfplumber page cache is released after each page** ([#5](https://github.com/drpwchen/textbook-to-note/issues/5)).
  `convert_pdf()` opened one `pdfplumber.PDF` for a whole book and reached into `plumber.pages[i]`
  per page. `PDF.pages` holds every materialized `Page` for the object's lifetime, and each `Page`
  caches its `chars` / `edges` / `rects` on first use, so peak memory grew roughly linearly with the
  number of pages the table gate let through — nothing released them until `close()` at the end of
  the book. Every page is read exactly once, so `flush_cache()` + `close()` after its single use
  cannot change output, and **byte-identity was verified** on two books (a 1297-page table-dense
  reference, 627 tables, and a 638-page ordinary one): 23/23 output files identical each, before
  and after. Measured peak RSS: **6899 MB → 803 MB** on the dense book (8.6x), **6209 MB → 133 MB**
  on the ordinary one (46.6x), and the curve flattens instead of climbing to the last page.
  The ordinary book is the more interesting number: the issue predicted table-sparse books would
  barely leak because the gate skips them, but the gate admits far more pages than actually yield
  tables (638 pages, 12 tables extracted, still 6.2 GB), so the leak was never confined to
  table-dense books.

### Changed
- **Documentation audited against the code, and corrected where it disagreed.** Each of these
  was somewhere a reader could act on the docs and get a different result than the repo delivers:
  semantic search was described as if an indexer shipped (it does not — `post_convert.py --index`
  prints `[skip]` and returns success without `INDEXER_SCRIPT`, which is now stated, with the
  companion repo [vault-search](https://github.com/drpwchen/vault-search) named as the thing to
  point it at); `requirements.txt` was said to cover the semantic-search stack (lines 7-16 are all
  comments); the figure-remap contract was documented with a `match_method` key that the
  validator actively *rejects* (the real key is `match_quality`, and `qc_degraded` / `qc_skipped`
  were undocumented); docs used a `textbook-md/` output directory that is really `OUTPUT_DIR`
  (default `./output`); `architecture.md` pointed at a section of itself that does not exist; and
  `scan_fix_negatives.py` read `OLLAMA_VISION_MODEL` while the rest of the repo uses the `T2N_`
  namespace (now `T2N_OLLAMA_VISION_MODEL`, with the old name kept as a fallback so existing
  environments don't silently switch models). Also corrected: the skill claimed a strict figure
  hard_fail exits 2 (it exits 1), and that chapter splitting is not attempted on OCR'd books
  (it is, best-effort).
- **README no longer claims per-page OCR engine selection** (both variants). The detection
  signals are per-page; the routing decision is per-book — one trip of the check sends the whole
  PDF to OCR. Per-page routing is future work and is now labelled as such rather than described
  as shipped.
- **Setup guide no longer provisions OCR up front** (`AGENTS.md`, [#3](https://github.com/drpwchen/textbook-to-note/issues/3)).
  Step 1 asked whether the user had a GPU and pointed at the OCR ladder before a single page had
  been converted, which reads to a coding agent as "install the OCR stack now." It is an exception
  path: OCR routing exists only in the `--batch-dir` code path, and the single-file path never
  invokes it at all. A first-time user's agent installed Surya, hit the missing adapter ([#4](https://github.com/drpwchen/textbook-to-note/issues/4)),
  wrote its own against the removed `surya.ocr` API, chased that into a local VLM inference server,
  and exhausted 48 GB of RAM — on a born-digital PDF that converted correctly with no OCR at all.
  The GPU question is gone from Step 1 and OCR now lives in a new **Step 4.5**, entered only when
  Step 4's output is actually garbled or empty, with a diagnose-before-installing checklist.
- **Related-projects section now links [note-supplement](https://github.com/drpwchen/note-supplement)**
  (both README variants). It covers the direction this pipeline deliberately does not: merging new
  source material into notes that already exist, where the risk is not missing content but silently
  overwriting content the existing note already got right.

## [0.2.0] — 2026-07-21 — Table fidelity

A sustained pass over how the pipeline handles textbook tables, driven by measuring the real
corpus rather than by intuition. Every change defaults to preserving prior output (a kill-switch
restores byte-identical behavior) unless it corrects output that was already wrong.

### Added
- **Cross-page table merge** (`T2N_TABLE_MERGE=1`, default OFF) — stitch a table that ends near
  a page bottom to a geometrically-matching table at the top of the next page; dedupes the
  repeated header, leaves a `<!-- table continues from page N -->` trace.
- **Docling table rung** (`T2N_DOCLING=1`, default OFF) — a layout-model table extractor that
  gets multi-column shape right where `pdfplumber` collapses borderless grids; falls back to
  `pdfplumber` per-page when it finds nothing, so no page is left worse off.
- **Table QC gate** — flags *structural* damage (ragged rows, empty first cell, run-together
  text, single-column collapse, content-retention) with a `<!-- ⚠️ … -->` trace, never
  auto-"fixing" it.
- **Out-of-band review queue** (`T2N_REVIEW_QUEUE=1`, default OFF; recommended ON for clinical
  corpora) — the QC gate sees structure but not **misbinding** (a value merged into the *wrong
  row* of an otherwise clean grid). There is no safe automatic fix, so the high-risk subset
  (continuation-page tables + dosage/threshold tables) is flagged for a bring-your-own-model
  second opinion. In testing ~1 in 6 continuation×dose tables carried a high-severity misbinding
  vs ~0 in a random sample. See [`docs/table-review.md`](docs/table-review.md).
- **Spanned category-header collapse** (`T2N_TABLE_HEADER_COLLAPSE=1`, default ON) — a section
  header broadcast across every column of a wide grid becomes a phantom full-width data row. A
  real data row never repeats one ≥15-char string across ≥3 columns, so the row is re-cast as a
  single header cell — structural only, never moves a value between rows. Hit 130/232 (56%) of
  one dense pharmacology reference's tables.
- **Book-level table-reliability banner** (detection only) — when ≥40% of a book's tables (given
  ≥10) trip a QC flag, a `> [!caution]` banner is hung at the top of the markdown telling the
  downstream model to verify every table against the source PDF; `reliability_flagged` /
  `flag_rate` land in the per-book stats. One reference ran 66%.
- **Whole-book table-failure warning** (`T2N_BOOK_TABLE_CHECK`, default ON) — table loss is
  bimodal (a book extracts fine or loses every table); a loud warning is emitted when a book
  yields 0 tables despite ≥10 captions, or `pdfplumber` parses 0 pages while `fitz` opens fine.

### Fixed
- **Page-frame pseudo-table rejection** (`T2N_TABLE_FRAME_REJECT`, default ON) — page-decoration
  rectangles made `pdfplumber` "find" a whole-page 1-column table that dumps every word into one
  cell (real multi-column tables arrive column-interleaved but caption-and-values intact, reading
  as clean data while the binding is destroyed). Rejected and replaced by a trace comment.
  Measured at 9.9% of extracted tables across 128 books.
- **Two false-positive table detections** — running prose and a navigation strip that the frame
  heuristic misread as tables, narrowed without regressing real rejections.
- **Docling ligature corruption** — repair ligature glyphs against the page's own text layer
  before emitting, so the markdown and the QC retention check both see corrected text.
- **Furniture false-rejections** — a geometry-only running-header/footer band rule, measured for
  its false-kill rate and narrowed.

## [0.1.0] — 2026-07-19 — Initial public release

PDF/EPUB textbook → AI-searchable markdown → structured, fully-cited notes. Five stages:
convert (0-token, silent-failure detection, column-aware reading order), chunk (heading-aware),
retrieve (local LanceDB semantic search with source weighting), write (template-driven,
citation-enforced, non-destructive), extract figures (geometric match + deterministic QC gate).
Bilingual READMEs and note templates; ships as skills an AI agent installs from `AGENTS.md`.

[0.3.1]: https://github.com/drpwchen/textbook-to-note/releases/tag/v0.3.1
[0.3.0]: https://github.com/drpwchen/textbook-to-note/releases/tag/v0.3.0
[0.2.0]: https://github.com/drpwchen/textbook-to-note/releases/tag/v0.2.0
[0.1.0]: https://github.com/drpwchen/textbook-to-note/releases/tag/v0.1.0
