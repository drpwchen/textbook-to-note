# Calibrating figure-remap for a new book

`figure_remap.py extract` is a deterministic document-grounding tool, not a
one-size-fits-all cropper. Every book has its own caption format, scan
quality, and layout quirks. This guide walks through calibrating a new book
before you trust the extractor on it.

## 1. Is it scanned or born-digital?

`figure_qc_gate.gate()` auto-selects a backend per page via
`figure_scanned._select_backend()`, but it helps to know which regime you're
in before debugging a miss:

| Signature | Backend | Notes |
|---|---|---|
| ≥2 separable rasters, no full-page background image, native fonts | `geometric_match_bbox` | Pure born-digital PDF — the common case for recent publisher-typeset books. |
| 1 small raster + many text blocks + embedded fonts | `geometric_match_bbox` | Text-heavy page with an inline figure; fonts raise confidence but never flip the decision. |
| Near-full-page raster **plus** smaller embedded rasters, OCR-overlay text | `caption_anchor` | "Hybrid scan" — a scanned page with digitized figures overlaid, common when an older edition was re-scanned and OCR'd. The OCR-overlay text will fail geometric's text-bleed QC even though the smaller rasters look "assignable," so route to caption_anchor regardless. |
| Near-full-page raster only, sparse/no OCR text | `caption_anchor` | Pure scan, e.g. a photocopied or camera-scanned older edition. |

Quick check: open a representative page in a PDF viewer. If you can
select/copy body text cleanly, it's likely born-digital. If selecting text
gives garbage or nothing, it's scanned (even if it has an OCR layer somewhere
else in the book).

## 2. Calibrate the caption regex against this book

Both backends key off `CAPTION_RE` (in `figure_qc_gate.py` and
`figure_scanned.py`) — a regex over `FIG(URE)?/Fig./Figure` + separator +
numeric id + optional sub-letter. Before extracting anything:

1. Grep one converted chapter's markdown for the figure-caption pattern the
   book actually uses, e.g. `grep -oE '(?:FIGURE|Fig\.?)\s*\S+' chapter.md`.
2. Note the separator variant: hyphen (`4-1`), period (`4.1`), en-dash
   (`4–1`), em-dash, or a plain space. `normalize_fig_id()` folds all of
   these to a canonical dotted form, so `--fig-id` can be given in whatever
   form is convenient — it doesn't need to match the book's raw punctuation.
3. Note the sub-letter convention for multi-panel figures: `4-1A` vs
   `4.1 (a)` vs `4.1, A`. If the sub-letter is embedded as a separate token
   ("Fig. 4.1, panel A") rather than glued to the number, the regex may need
   a per-book variant — check `CAPTION_RE` in both scripts.
4. Check for OCR letter/digit confusion if the book is scanned: lowercase
   `l` / uppercase `I` read as `1`, capital `S` as `5`, capital `O` as `0`.
   `normalize_fig_id()` already folds these (Class A substitutions); if you
   see a *different* confusion pattern (Class B — e.g. `B`↔`8`, `D`↔`0` in
   the sub-letter position), that needs manual disambiguation — the L1 guard
   in `figure_scanned.py` will hard-fail with
   `L1_target_matches_multiple_captions` rather than guess.

## 3. Caption direction

Some books print the caption **above** the figure; others **below**. Both
backends auto-detect this — `geometric_match_bbox` per-page (assigns rasters
to whichever direction claims the most images), `figure_scanned._infer_direction`
page-globally (Phase 1: majority vote by caption Y-position). You don't need
to configure this, but if extraction consistently grabs the wrong region for
a book, check whether it mixes both conventions (rare, but seen in edited
multi-author volumes) — that needs a per-page override, not a global one.

## 4. Multi-panel figures

Composite figures with sub-panels (`(A)/(B)/(C)/(D)`) break the "one caption
owns one raster" assumption that strict geometric matching relies on.
`figure_remap.extract()` detects ≥2 distinct panel references in the caption
text and auto-relaxes to non-strict mode for that call — no manual flag
needed. Two things to know when calibrating:

- **Vector overlay loss**: raster/xref cropping often loses panel-letter
  labels, arrows, and guide lines that were drawn as vector overlays, not
  baked into the raster. The crop is geometrically correct but can look
  incomplete. Write the surrounding note prose to map each panel letter to
  its content, so the note doesn't depend on overlay labels that may not
  have survived extraction.
- **Uncertainty is real, not just cosmetic**: `match_quality: uncertain`
  collapses three different failure modes (which raster is correct, whether
  overlay labels survived, how tight the crop is). Treat any `uncertain`
  result as "needs a human glance," not "good enough."

## 5. Common QC failure modes and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `whitespace_padding` fail | Crop includes a lot of margin/background | Usually resolves itself once `geometric_match_bbox` finds the right raster union; if it recurs, check whether the book's figures have unusually large internal whitespace (e.g. graphs with big axis margins) — may need `WHITESPACE_FILL_MIN` tuned down for this book. |
| `text_bleed` fail on a born-digital page | Crop swallowed part of a caption or body paragraph | Check if the book's caption sits closer to the figure than `CAPTION_RE`'s clip logic assumes — `geometric_match_bbox` clips to the **caption band** so this should be rare; if persistent, the caption text may not be matching `CAPTION_RE` at all (falls through to raw-xref, which doesn't clip against captions). |
| `text_contamination` (OCR long-lines) fail on a labeled diagram/graph | Figure has a lot of legitimate in-figure text (axis labels, legends) that the local vision model's OCR pass mistakes for bleed-in prose | This is a known false-positive mode for label-dense figures (EMG traces, anatomical atlases with many labels). `PROSE_LINE_MIN` / `LONG_LINE_COUNT_MAX` in `figure_qc_gate.py` are tuned to tolerate typical label density; if a book's figures are unusually label-dense, consider whether the fail rate justifies a per-book override rather than a global threshold change (global threshold changes risk masking real bleed-in on other books). |
| `L1_no_caption_match` (scanned) | Target fig_id's caption uses a different separator/format than expected, or OCR mangled the fig_id in a way Class A substitution doesn't cover | Re-check step 2 above; if it's a Class B OCR confusion, the id needs manual correction — the guard is deliberately conservative (hard_fail, not a guess). |
| `L1_target_matches_multiple_captions` (scanned) | Two captions on the page normalize to the same fig_id | Usually a genuine ambiguity in the source (e.g. a sub-panel labeled inconsistently) — verify by reading the page render at `<out>.fail.json`'s `render_path`. |
| Extraction grabs the *adjacent* figure instead | Stacked figures in one column, or a caption sits closer to the wrong raster | `geometric_match_bbox` tightens the "open side" of the crop against neighboring images/captions/prose blocks specifically to prevent this; if it still happens, the two figures may be closer together than `LABEL_REACH`'s padding — inspect the specific page. |
| Extraction misses entirely (hard_fail) on a page you know is correct | Caption/figure are on different pages (common cross-page offset) | `figure_remap.extract()` already auto-sweeps ±1 neighbor pages on strict hard_fail — check whether the true offset is larger than 1 page; if so, pass the correct `--page` directly rather than relying on the sweep. |

## 6. Decision order when calibrating a new book

1. Convert a chapter, grep for the caption pattern (step 2).
2. Pick 3-5 figures spanning: a simple single-panel figure, a multi-panel
   figure, a label-dense figure (graph/diagram), and one near a column
   break or adjacent figure.
3. Run `figure_remap.py extract --source pdf` (bypass any cache) on each,
   in strict mode (the default) first.
4. For any hard_fail, read the `reason` field — it tells you which
   deterministic guard refused, not just "it didn't work."
5. Only fall back to `--no-strict` for genuinely ambiguous cases (e.g. a
   figure the deterministic backends structurally can't handle) — treat it
   as a last resort per figure, not a book-wide setting.
6. If you're running the legacy batch tool (`batch_remap.py`) across many
   chapters at once, `qc_metrics.py`'s verdict (green/yellow/red) plus
   `diagnostic_dump()` will show you sample missed captions — use that to
   spot systematic format mismatches instead of debugging figure-by-figure.

## QC thresholds

Thresholds like `WHITESPACE_FILL_MIN` are env-configurable (e.g.
`T2N_QC_WHITESPACE_MIN`) so you can loosen them for special content types —
line-art-heavy books with large legitimate internal whitespace, for
instance. But **never tune a threshold just to make one failing figure
pass** — a threshold change is global and risks masking a real QC failure on
every other book. If a single figure fails, re-extract it (check page
offset, caption match, backend selection per the sections above) instead of
loosening the gate. Only change the env value when you've calibrated it
against a whole book's figure set and confirmed the looser threshold doesn't
let genuine failures through.

## The calibration output is a list to copy from, not a rule to apply

Everything above produces *exact-match strings*: a book id, a fig_id, a caption.
Once you have them, copy them — don't regenerate them.

- `--caption` is pasted **verbatim** from the converted markdown. Don't retype
  it, don't normalize the spacing, don't translate it.
- `--book` is the directory name under your corpus root, verbatim.
- If the fig_id you need isn't in the manifest or the grep output, **stop and
  say so**. Do not construct one from the pattern you just calibrated — a
  plausible-but-wrong fig_id is precisely what makes geometric matching claim
  the *neighbouring* figure, and the result passes QC because it is a perfectly
  good crop of the wrong thing.
- The path you embed in a note is the `file` field the contract returned, again
  verbatim — not the `--out` you asked for. The `auto` fast path can hand back
  the book's pre-extracted `.jpeg` where your template said `.png`.

The failure mode all four share: a name *derived* from the real one reads
correct in human review, because it was derived correctly-looking. Only a
machine comparing against the source of truth catches it. That machine is:

```bash
python figures/figure_embed_lint.py --notes-dir {NOTES_DIR} path/to/notes/
```

It reports MISSING (a guessed filename, or an embed written for an extract that
failed) and CASE MISMATCH (opens on Windows/macOS, breaks on git and Linux).
Exit 3 means it couldn't find the notes dir — unverifiable, which is not a pass.

## Placeholder examples used above

Where this guide says "an older edition scanned to PDF" or "a label-dense
diagram," substitute your own book's actual quirks once you've calibrated —
e.g. "Book A (scanned 1990s edition, hybrid scan + embedded photos)" or
"Book B (born-digital, heavy multi-panel arthroscopy figures)." Keep a
per-book note (append it to this file) once you've worked out the quirks, so
you don't re-derive them next time.
