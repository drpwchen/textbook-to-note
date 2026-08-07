---
name: figure-remap
description: Extract single figures from PDF textbooks on-demand with built-in QC verification. Primary use: when writing/supplementing a note that needs an embedded figure (anatomy, classification, algorithm, imaging). Each call checks an existing fast-path crop, re-extracts from PDF if missing/wrong, retries with local-vision guidance, and escalates to a frontier-model vision read only if all else fails. Also contains legacy batch tools for whole-book re-extraction. Trigger: "extract figure", "figure for note", or proactively when a note's TODO/REF references a figure not yet embedded.
---

# figure-remap — deterministic document-grounding system

> This skill calls scripts in your clone of the textbook-to-note repo. At
> install time, replace `{REPO}` below with the absolute path of the clone.

## Architecture (read this before implementation details)

figure-remap is a **multi-backend deterministic document-grounding system**,
not a "crop utility." The system has six explicit layers:

```
┌─ Contract ──────────────────────────────────────────┐
│  figure_remap.extract() → {status, match_quality,   │
│  hard_fail, file, fig_id, reason}                   │
├─ Policy ─────────────────────────────────────────────┤
│  strict = deterministic + ambiguity-intolerant      │
│  L1 / L2 / L3 hard-fail hierarchy                   │
├─ Backend selection ─────────────────────────────────┤
│  Capability-based: _select_backend()                │
│    "geometric"  | "caption_anchor" | [future]       │
├─ Backends ──────────────────────────────────────────┤
│  geometric_match_bbox()  — born-digital rasters     │
│  caption_anchor_bbox()   — scanned / hybrid scans   │
│  [future] layout-aware / table-aware                │
├─ Analysis & cache ──────────────────────────────────┤
│  PageDerived (per-page, sha1-keyed, policy-versioned)│
│  _render_cache/<sha1>_pg<N>_<dpi>dpi.png            │
├─ Debug artifacts ───────────────────────────────────┤
│  <out>.fail.json on every scanned-mode hard_fail    │
└─────────────────────────────────────────────────────┘
```

Future backends plug in at the Backend layer with their own capability check
and a function returning bbox + ambiguity flags — nothing above changes.

==Architectural freeze rule==: do not add per-book heuristics or
`if scanned/elif born-digital` branches into the gate function. New behavior
goes into a new backend plus a new capability check in `_select_backend`.

## Backends — when each is used

`_select_backend()` routes pages by structural capability, not by document
type:

| Category | Signature | Backend |
|---|---|---|
| Pure born-digital | ≥2 separable rasters, no full-page background, native fonts | `geometric` (0.95 confidence) |
| Born-digital text page | 1 small raster + many text blocks + embedded fonts | `geometric` (0.75 confidence) |
| **Hybrid scan** (near-full-page raster + embedded figure rasters + OCR overlay) | e.g. a scanned reference book with an OCR text layer laid over the scan | `caption_anchor` (0.92 confidence) |
| Pure scanned | Near-full-page raster only, no embedded figures, sparse OCR text | `caption_anchor` (0.9 confidence) |

The hybrid-scan category is the one that surprises naive backend selection:
the PDF library reports multiple "assignable" rasters (the embedded figure
photos), but the surrounding OCR-overlay text contaminates the geometric
backend's text-bleed QC check. Detecting the page-background raster and
routing to caption-anchor is the fix.

## Scanned/hybrid backend — caption_anchor

Module: `figure_scanned.py`. Algorithm:

1. Render the page at 200 dpi to a cache file (keyed by PDF hash + page +
   dpi).
2. Walk the page's text-with-position data for `Fig X-Y` caption text
   bounding boxes (with an inline-reference filter — must start at a line
   beginning, not mid-sentence after a word).
3. Apply Class A OCR normalization (silent, 1-to-1): common OCR
   confusions (`l`/`I` → `1`, `S` → `5`, `O` → `0`) only inside the numeric
   portion of the fig_id.
4. Build a caption-match object per caption with an alias set.
5. Look up the target fig_id across all caption alias sets:
   - 0 matches → `L1_no_caption_match` hard_fail
   - ≥2 matches → `L1_target_matches_multiple_captions` hard_fail
6. Infer page-global caption direction (caption-below is the layout
   default).
7. Compute the figure region: the area opposite the caption, bounded by the
   previous/next caption in the same column.
8. Crop the rendered page; run an ink-density trim to tighten the bbox.
9. Scanned QC: whitespace-fill check only (text-bleed is meaningless on
   scanned PDFs — the OCR overlay covers the whole page).
10. On hard_fail: write a sibling `.fail.json` debug artifact with policy
    version, captions, columns, backend reasoning.

Known Phase-1 limitations (deferred):
- Class B OCR ambiguity (e.g. `4-28` vs `4-2B`) currently fails
  `L1_no_caption_match` for OCR-mangled targets.
- L2 ownership-overlap guard, L3 direction-quality guard not yet built.
- Direction inference is page-global, not per-column.

## `fail.json` schema

Every scanned-mode hard_fail writes a sibling `<out>.fail.json` debug
artifact, with a versioned schema. Stable fields:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | bumps only on breaking schema changes |
| `policy_version` | string | policy that produced this fail |
| `fig_id_target` | string | the normalized fig_id the caller asked for |
| `fail_reason` | string | reason code (e.g., `L1_no_caption_match`) |
| `page_idx` | int | 0-indexed page |
| `pdf_sha1` | string | first 12 hex chars of PDF hash |
| `backend_selected` | string | `"geometric"` or `"caption_anchor"` |
| `backend_confidence` | float | 0..1 |
| `backend_reasons` | list[string] | one-line tokens explaining the choice |
| `captions` | list[object] | each: `{raw, aliases, ambiguity, bbox}` |
| `columns` | list[[x0,x1]] | column boundaries in PDF points |
| `direction` | string \| null | `"above"` \| `"below"` \| null |
| `render_path` | string | path to the cached rendered page image |

Downstream consumers must check `schema_version` and refuse to parse
unrecognized majors.

## Performance helper — `extract-page` for scanned books

When a workflow needs all figures on one scanned page, use the batch CLI
subcommand instead of N independent `extract` calls:

```bash
python {REPO}/figures/figure_remap.py extract-page \
  --book "BookID_PartI" \
  --pdf "<path>.pdf" --page N --out-dir "attachments/topic/" \
  --fig-ids "4-1A,4-1B,4-1C,4-1D" \
  --name-template "Fig_{fig_id}_BookShort.png"
```

==This is NOT a contract addition== — `extract-page` is a render-cache-aware
loop that calls `extract()` once per fig_id. Each emitted JSON line is a full
single-figure contract dict; workflows can substitute N independent `extract`
calls without behavior change — only speed differs.

## Entrypoint contract (call THIS, not the gate)

There is ONE sanctioned entrypoint for every figure request —
`figure_remap.py extract`. Workflows MUST go through it and MUST NOT call
the internal gate script directly. The entrypoint is the abstraction
boundary: it returns a stable minimal contract and hides the implementation
(matching method, strict mode, fallback policy, QC ladder), so logging stays
uniform and policy is enforced in one place.

```bash
python {REPO}/figures/figure_remap.py extract \
  --book "{BookID}" \
  --fig-id "X.Y" \
  --caption "{caption text from textbook md}" \
  --out "attachments/Fig_X-Y_{BookShort}_{topic}.png" \
  --pdf "{path/to/textbook.pdf}" \
  --page N \
  [--source {auto|pdf|existing}]   # default auto (see "Source modes" below)
  [--existing "{book_dir}/figures/Fig_X-Y.{png,jpeg}"] \
  [--no-strict]      # opt OUT of deterministic mode into the fallback ladder (discouraged)
```

### Source modes

`--source` selects where the figure comes from. Default is `auto` — callers
don't need to think about a pre-extracted fast path; the entrypoint resolves
it from the book convention `<OUTPUT_DIR>/<book>/figures/Fig_<id>.*`
automatically.

| `--source` | Behaviour | When to use |
|---|---|---|
| `auto` (default) | (1) Try book-convention pre-extract; QC-gate it. (2) On miss/QC fail → fall through to strict geometric match + ±1 page sweep. | Normal note-writing. |
| `pdf` | Skip the existing fast path entirely; ignore even an explicit `--existing`. Always re-extract from PDF. | Validating a code change, suspect a pre-extract is contaminated. |
| `existing` | Cache-only: hard_fail if the convention path is missing or QC-fails. No PDF fallback. | Trust-the-batch mode after offline validation, or when the PDF is unavailable. |

Contract returned (and ONLY this — never the gate's internal shape):
```json
{ "status": "pass|fail|escalate", "match_quality": "exact|uncertain|failed",
  "hard_fail": true|false, "file": "<path|null>", "fig_id": "<normalized>", "reason": "<text>",
  "qc_degraded": true|false, "qc_skipped": ["<check name>", "..."] }
```
Those eight keys, no more and no fewer — `_validate()` in
`figures/figure_remap.py` raises on any extra or missing key.
- `qc_degraded` / `qc_skipped`: some QC checks could not run (e.g. the source
  page render was unavailable), so a `pass` here is weaker than a fully-gated
  one. Treat a degraded pass as "embed, but eyeball it".
- `match_quality`: `exact` = deterministic geometric match · `uncertain` = a
  fallback crop (only with `--no-strict`) · `failed` = no crop. ==Branch on
  this, never on the engine method.== The precise method is logged only in a
  local QC log, deliberately kept out of the contract.
- `status:pass` (exit 0) → `file` is a QC-passed crop; embed it.
  ==**Embed the `file` string verbatim — never the `--out` you asked for, never
  a name rebuilt from the `Fig_{id}_{Book}` convention.**== `file` is the
  authoritative path and it legitimately differs from `--out`: the `auto` fast
  path returns the book's pre-extracted image, so a template saying `.png` can
  come back `.jpeg`. A rebuilt name is *derived from* the real one, so it reads
  correct in review and only a machine catches it. Prove it:
  ```bash
  python {REPO}/figures/figure_embed_lint.py --notes-dir {NOTES_DIR} "<note.md>"
  ```
  exit 0 clean · 1 = MISSING (a guessed filename, or an embed written for an
  extract that actually failed) or CASE MISMATCH (opens on Windows/macOS,
  breaks on git and Linux) · 3 = notes dir not found = **unverifiable, not a
  pass**.
- `status:fail` (exit 1) → deterministic miss / hard_fail. ==NOT a wrong
  figure — a correct refusal.== Fix `--page`, escalate to vision, or leave a
  `<!-- TODO -->`.
- `status:escalate` (exit 2) → non-strict only; read the page render, then
  re-call with `--bbox`.

**Default is strict (deterministic).** `--no-strict` re-enables the
legacy-compatibility fallback ladder, which can yield a plausible-but-wrong
crop — avoid for note-writing.

> ⚠️ A pass guarantees the **right raster for the fig_id**, not that the
> figure depicts what you assume. Still read the real caption text before
> trusting its content.

## Policy version

**figure-remap policy (current)**
- Deterministic mode (geometric match) is the DEFAULT and MUST NOT be
  bypassed without an explicit `--no-strict`.
- The fallback ladder (raw-xref pick / local-vision guidance / size-pick) is
  **legacy compatibility only** — never the default path.
- Workflows call `figure_remap.py extract` ONLY; the internal gate script is
  private.
- The contract exposes `match_quality`, not the engine method.
- ==Future changes MUST NOT re-introduce silent fallback (default→
  heuristic)== without bumping the policy version. A bug-fix patch must not
  quietly flip the default. Enforcement: the gate's CLI is guarded, and a
  regression test suite (`{REPO}/figures/test_contract.py`) runs after any gate
  edit.
- `extract` auto-sweeps ±1 neighbor pages on strict hard_fail before
  returning fail. Still deterministic (geometric match only, no vision
  model); just widens the search window by 2 pages. See "Page resilience"
  below.
- Multi-panel caption auto-relax: when a caption contains ≥2 distinct panel
  references (`(A)(B)…` or `panel X`), strict mode is overridden to relaxed
  up front — see "Multi-panel figures" below. This is policy-level routing
  (a capability mismatch, not a silent fallback): strict geometric match's
  "caption owns exactly one raster" assumption is invalid for composite
  layouts, so retrying strict can only waste attempts.

## Multi-panel figures — capability mismatch

Composite figures with sub-panels (multi-location diagrams, staged
classification figures, surgical step sequences) break a core assumption of
strict mode and require ==different handling at three levels==: routing,
semantic completeness, and uncertainty reporting.

### 1. Routing (automated)

Strict geometric matching assumes ==one caption owns one raster==.
Multi-panel captions reference sub-regions (`(A)/(B)/(C)/(D)/(E)`) of a
composite layout — those panels are not independently owned rasters. This
is not an implementation bug; it's a representation mismatch.

Detection triggers on ≥2 distinct `(letter)` or `panel <letter>` references
(a single `(A)` is allowed through — common in non-panel prose). When
triggered, `extract` overrides `strict=False` and records
`policy=multipanel_caption_relaxed` in the contract's `reason` field.

### 2. Vector overlay loss (caller awareness)

==Multi-panel figures extracted via raster/xref cropping often lose vector
overlays== — panel letter labels, arrows, dotted guides, annotation lines.
The crop is technically correct (right page region, right raster) but
==semantically incomplete==: it can be hard to map a sub-region back to its
caption text.

Caller doctrine: when embedding a multi-panel figure in a note, write the
surrounding description as self-contained — map each panel letter to its
content in the prose, so readers can interpret the figure even when overlay
labels were lost in extraction.

### 3. Uncertainty taxonomy (observation)

`match_quality: uncertain` today collapses three distinct failure modes:
identity uncertainty (vision-model fallback selected the raster, not
deterministically verified), semantic-completeness uncertainty (overlay
labels lost), and geometry uncertainty (crop bbox loose). Caller doctrine:
when `match_quality:uncertain` is returned, prose-annotate the embed (e.g.
"vision-extracted, recommend visual confirmation") rather than relying on
machine-readable fields.

## Implementation: internal gate script (do not call from workflows)

`figure_remap.extract()` delegates to the internal gate implementation,
which handles the ladder + QC in one call (first success wins):
0. **geometric_match** (primary, deterministic, no vision model) —
   direction-agnostic caption↔image matching: auto-detects whether captions
   sit above/below the figure, assigns each embedded raster to its nearest
   caption, crops the raster(s) owned by the target fig_id (union for
   multi-panel).
1. **Existing file** — reuse a prior crop if it passes QC (if `--existing`)
2. **Raw page candidates** — largest embedded rasters, decoration-filtered
3. **Local-vision-guided retry** (×2) — a small local vision model suggests
   a bbox (weak; last resort before escalation)
4. **Escalate** — exit code `2` → read the page render, estimate bbox,
   re-call the gate with `--bbox`

QC on each candidate: deterministic checks **block** (whitespace fill
≥80%, text-bleed <100 chars, OCR long-lines ≤7); the local vision model's
caption match is **advisory only** (logged, never blocks — too noisy to
gate on).

Exit codes: `0` ok (saved to `--out`) · `2` escalate to frontier vision ·
other → log + skip.

## Deterministic mode — no-fallback strict mode

The best-effort ladder is *heuristic routing*: when geometric matching
misses, it falls through to raw-xref pick / local-vision-guided crop, which
can silently produce a **plausible-but-wrong** crop (e.g. grabbing the
adjacent figure's raster on a multi-figure page, or any raster when the
caller passes a wrong `--page`). The entrypoint therefore defaults to the
**deterministic contract**; the fallback ladder is opt-in via `--no-strict`.

Behavior under strict mode:
- **geometric_match is the ONLY extractor on the execution path.** Raw-xref
  pick, vision-guided bbox, existing-file reuse, and size-based picks are
  skipped entirely.
- If geometric_match cannot deterministically own a raster for the
  normalized fig_id → **HARD_FAIL**, surfaced on the entrypoint contract as
  `{"status":"fail", "match_quality":"failed", "hard_fail":true, "file":null,
  "reason":...}` with exit `1`. It never substitutes a different raster.
  (Exit `2` is `status:escalate`, which strict mode never returns.)
- Internally the gate tracks a `match_method` (`geometric_match` | `FAIL`)
  and the per-book QC log records a method counter — but that key is
  deliberately *not* on the contract above; branch on `match_quality`.

When strict HARD_FAILs, the caller decides: fix `--page`, or escalate to
vision (read the page render → `--bbox`), or leave a `<!-- TODO -->`. A
HARD_FAIL is the *correct* outcome — it converts a silent mislabel into an
explicit miss.

## Page resilience: ±1 neighbor sweep

Caption/figure cross-page offsets are common: a caption may be on the page
the markdown lists while the raster sits on N±1 (frequent in multi-column
layouts where a figure spans a column break). A single-page strict match
can't recover this on its own.

`figure_remap.py extract` handles this transparently:
1. Try `--page N` (strict geometric_match)
2. If hard_fail: silently retry `N-1`, then `N+1` (still strict, still
   geometric_match only)
3. First pass wins; `reason` annotates which neighbor matched
4. If all three fail → return `status:fail` with `reason` listing the
   neighbor attempts

==This is NOT a fallback ladder.== Every attempt is still deterministic
geometric matching; no vision model, no raw-xref pick. The contract is
unchanged.

`--existing` is intentionally skipped for neighbor attempts: the fast-path
image is keyed to the original page assertion, so reusing it on a different
page would short-circuit the real geometric retry.

When a true hard_fail returns after the sweep, the caller's next move is:
1. ==Re-check `--page`== — the caption may live on a different page than the
   markdown says
2. ==Cross-book search== (workflow-level, not script-level): search the same
   concept across your other priority reference books and re-issue
   `extract` against the best alternative
3. ==Vision escalate== (`--no-strict`) — last resort
4. Only if all the above fail → leave `<!-- TODO -->` per the convention
   below

**fig_id is dash-variant agnostic**: normalization folds hyphen, en-dash,
em-dash, figure-dash, horizontal-bar and period to a canonical dotted id, so
`64-5` / `64–5` / `Fig 64.5` / `30 — 44` all compare equal.

## Per-book calibration (do this BEFORE calling extract)

Caption format varies per book — calibrate first to set the correct
`--fig-id`:

- Grep one chapter's markdown for a figure-caption pattern (e.g.
  `(?:FIGURE|Fig\.?)\s*\S+`) to see how captions are written
- Inspect `figures/figures_manifest.json`'s `fig_id` field if present
- Inspect existing `figures/Fig_*` filenames

==**The calibration output is a list to copy from, not a rule to apply.**==
`--book`, `--fig-id` and `--caption` are exact-match strings: paste `--caption`
**verbatim from the converted markdown** (don't retype it, don't tidy the
spacing, don't translate it) and take `--book` verbatim from the directory name
under your corpus root. If the fig_id you need isn't in the manifest or the grep
output, ==say so and stop — do not construct one from the pattern==; a
plausible-but-wrong fig_id is what makes geometric matching claim the
neighbouring figure.

Common variants:
- Caption marker: `FIGURE` / `Figure` / `Fig.` / `Fig` / `FIG.`
- Separator: `-` / `.` / en-dash / em-dash
- Sub-letter: `5.42a` / `5-42A` / `5.42 (a)` / `5.42, A`
- Page offset: PDF page ≠ printed page (varies per book — check front
  matter)

## Layout B books (no pre-extracted figures)

Some books have only an empty `figures/` — the fast path is skipped, and the
gate goes straight to re-extract. For these, supply `--pdf` + `--page`
directly; do not pass `--existing`.

## QC criteria (built into the gate)

Every figure passes through QC before being saved:
- Content type matches caption modality (diagram / imaging / photo / chart)
- Multi-part figures (A/B/C) include all panels
- No bleed-in from adjacent figures
- Text labels readable (not a blurred thumbnail)

If QC fails on the existing fast-path crop → automatic re-extract. If QC
fails on re-extract → local-vision-guided retry with bbox. If QC fails after
retries → escalate (exit 2) for a frontier-model read.

## TODO comment convention in notes

When a figure can't be extracted now (PDF missing, caption ambiguous),
leave:

```markdown
<!-- TODO: insert Fig X.Y from {Book} ({reason}) — render PDF page N + crop -->
```

==TODO is the LAST step, not the first.== Before writing this comment the
caller MUST have:
1. Confirmed the page number against the textbook markdown (grep the
   caption text)
2. Let the ±1 neighbor sweep run (automatic in `extract`)
3. Tried at least one cross-book alternative for the same concept — the
   same figure/photo/classification often appears in more than one
   reference book
4. Optionally used `--no-strict` for a frontier-vision escalation

Do not imply a batch run will "fill this in later" — each agent extracts
its own figures on demand.

==A failed extract gets a TODO comment, never an embed.== Writing
`![[Fig_X-Y_Book.png]]` for a figure that never passed QC produces a note that
looks complete and renders a broken link — exactly what `figure_embed_lint.py`
exists to catch. Run the lint on every note you touched before reporting done.


## Legacy: batch re-extraction (rarely used now)

The original batch workflow is preserved for cases where the core extractor
is materially changed and you want to validate the fix across the whole
corpus:

```bash
python {REPO}/figures/batch_remap.py --list
python {REPO}/figures/batch_remap.py --book NAME --dry
python {REPO}/figures/batch_remap.py --book NAME --apply
```

This runs caption-based extraction on every chapter, computes QC metrics
(green/yellow/red), and visual-checks a few random samples per book via a
local vision model. See `{REPO}/figures/CALIBRATION.md` for accumulated
per-book quirks.

**Do not run batch as part of normal note-writing** — too slow, and the
on-demand gate is more reliable for single figures because it can fall back
to local-vision-guided bbox retry, which batch mode doesn't try.

> Historically some setups also had a separate legacy caption-based batch
> extractor (`extract_figures.py`). It is not shipped in this repo —
> `{REPO}/figures/batch_remap.py` degrades gracefully without it (falls
> back to the gate's own extraction path per figure).

## Files

Everything referenced above lives under `{REPO}/figures/`:

- `figure_remap.py` — sanctioned entrypoint (`extract`, `extract-page`)
- `figure_qc_gate.py` — primary internal implementation: single-figure
  on-demand extraction + QC gate
- `figure_scanned.py` — scanned/hybrid `caption_anchor` backend
- `qc_metrics.py` — batch QC metric computation
- `visual_check.py` — local-vision-model sample verification (used inside
  the gate)
- `visual_check_batch.py` — batch-mode visual-check runner
- `batch_remap.py` — legacy: whole-book batch re-extraction
- `test_contract.py` — regression test suite for the entrypoint contract
- `CALIBRATION.md` — per-book quirks accumulated from batch runs (still
  useful for setting `--fig-id` format expectations)

## Key constraints

- **0 hosted-LLM tokens for QC**: only a local vision model via a local
  inference server. Frontier-model vision is used only on escalation (exit
  code 2).
- **No destructive edits**: source PDFs are the source of truth. `--out`
  writes a fresh copy to your notes' attachments folder; the original
  `figures/` (if present) is left untouched.
- **Idempotent**: re-running extract on the same figure produces the same
  output.
- **TODO trail**: any failed extraction leaves a `<!-- TODO: ... -->`
  comment in the note rather than silently skipping.
