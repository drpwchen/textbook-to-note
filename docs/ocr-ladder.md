# OCR Ladder

Principle: minimize vision-model tokens. Extract text locally first; only
send an image to a hosted LLM as the absolute last resort.

## Choosing your hardware tier

The zero-setup `fitz`-only default (step 1 below) is the **right** starting
point for born-digital ebooks — most personal libraries are mostly
born-digital, and `fitz` alone handles them at essentially no cost. The rest
of the ladder (steps 2-5) is **opt-in**, only needed once you actually have
scanned books to process. Which of those opt-in steps are practical depends
on your hardware:

**Surya ≥ 0.20 changed what this table means.** OCR is no longer a pip-install
plus a PyTorch batch size; a single ~650M-parameter VLM does OCR, layout and
tables together, behind an OpenAI-compatible **inference server** — vllm on
NVIDIA (shipped as a Docker image), llama.cpp everywhere else. So the tier
question is now "which server can I stand up, and how big a KV cache does it
want", not "how large a batch fits". Setup commands, the interface contract and
the memory caps are in [`surya-adapter.md`](surya-adapter.md); this table is
only for sizing. The Embedding column was written for `bge-m3`; ask-your-library now
defaults to `qwen3-embedding:0.6b` for corpora, whose fit on these tiers has not been measured here.

| Tier | Text (born-digital) | OCR (scanned) | Vision QC model (ollama) | Embedding (semantic index) | Skip / caveats |
|---|---|---|---|---|---|
| No GPU (CPU-only) | fitz — full speed, this is your whole pipeline | llama.cpp backend works but is ~0.01 pg/s on a dense page (measured, see below) — a handful of pages, never a book. Send rare scanned pages to frontier vision or skip. | Skip local vision QC → rely on deterministic QC only | bge-m3 on CPU OK for a small library, else grep-only | Accept: scanned books aren't locally OCR-able. The engine will *run*; it will not *finish*. |
| Apple Silicon 8 GB | fitz | llama.cpp Metal build (`brew install llama.cpp`). Cap `SURYA_INFERENCE_PARALLEL=1`; weights ~1.5 GB but the default KV cache is far larger | minicpm-v:8b Q4 is tight in 8 GB unified; prefer a smaller VLM (e.g. llava-phi3, moondream), and never concurrent with the OCR server | bge-m3 via ollama Metal | Avoid PaddleOCR-VL (poor Metal support) |
| Apple Silicon 16 GB+ | fitz | llama.cpp Metal, `SURYA_INFERENCE_PARALLEL=2-4` | minicpm-v:8b comfortable | bge-m3 | PaddleOCR still Mac-weak — leave it off |
| NVIDIA 8 GB | fitz | llama.cpp CUDA build with `-ngl 99`, `--parallel 2 --ctx-size 24576`. vllm technically fits but leaves nothing for anything else | minicpm-v:8b Q4 (~6 GB) — load sequentially, never concurrent with the OCR server | bge-m3 | Don't hold OCR + vision + embed resident at once. On Windows, vllm means WSL2 + Docker; use llama.cpp instead. |
| NVIDIA 16 GB+ | fitz | vllm via Docker + NVIDIA Container Toolkit — the intended production route | minicpm-v:8b at higher precision, or qwen2.5-vl:7b | bge-m3 (can stay resident) | Everything concurrent; reference tier |

Speed anchors to set expectations before committing a whole library to a
method: `fitz` text extraction runs ≫50 pages/s; a `pdfplumber` table pass runs
2-10 pages/s; a local VLM QC pass runs ~3-8 s/figure. For Surya 0.22
specifically, the one measured point we have is **CPU** (llama.cpp, no GPU
offload, one slot, a dense two-column synthetic page at 300 DPI):
**~0.01 pages/s — 100 s/page**. That is the number that matters for the
CPU-only row: a 500-page book would take ~14 hours. GPU throughput is
hardware-dependent and is not measured here; treat the pre-0.20 "1-4 pages/s"
figure as no longer applicable, since it described a different architecture.

**Image resolution.** Surya 0.22 wants images ≤ ~2048 px wide (96-192 DPI for
US Letter). `surya_ocr_pdf()` renders at 300 DPI (~2550 px), so the shipped
adapter downscales and maps bboxes back to original pixels. An adapter of your
own must do the same or the model sees inputs it was not tuned for.

## The ladder

| Priority | Method | Cost | Notes |
|---|---|---|---|
| 1 | `fitz` text extraction (or `markitdown`) | 0 | Default path for born-digital PDFs |
| 2 | Local OCR engine A (GPU, e.g. Surya) | 0 | Default OCR engine — CJK + Latin. Invoked as a subprocess through the adapter contract in [`surya-adapter.md`](surya-adapter.md), so any engine can take this rung. |
| 3 | Local OCR engine B (fallback, e.g. PaddleOCR-VL) | 0 | Used when engine A's output looks wrong; tends to run away on dense tables, so it's fallback-only, not default |
| 4 | Local vision model via a local inference server (e.g. ollama running a small vision model) | 0 (no hosted LLM tokens) | Weak but free; good for a rough bounding-box suggestion or a sanity check |
| 5 | Frontier-model vision read (Claude, GPT, etc.) | High | Max ~20 pages per request; last resort only |

`tesseract` is deliberately excluded from this ladder: for CJK text in
particular, its output quality was too poor to be usable, and every project
that tried it ended up needing a better engine anyway. Skip straight to a
modern OCR model (step 2).

## The table rung (orthogonal to the OCR ladder)

The ladder above is about *reading text off a page*. Extracting **table
structure** is a separate problem, and it bites even on born-digital pages the
OCR ladder never touches: `pdfplumber` finds tables by ruling lines, so it
silently drops borderless / shaded-row tables and collapses some multi-column
tables into one column (values survive but the row↔column binding is
destroyed — worse than a missing table, because it reads as clean data).

`T2N_DOCLING=1` (default **off**) adds [Docling](https://github.com/DS4SD/docling)
as an alternative table source. It is invoked **only on pages the existing
table gate already flags** — never the whole book — and only replaces
`pdfplumber` for a page when it actually returns a table; otherwise that page
falls back to `pdfplumber`, whose known collapse modes stay guarded by the
page-frame and page-furniture rules. **The fitz text stream is never handed to
Docling** — it reorders page content, so only its *tables* are used, never its
reading order.

Docling runs as a persistent worker in its own venv over a line-delimited JSON
protocol (model cold start ~4–12 s, reused across the whole batch), and its
tables pass through the same flag-only QC gate as everything else
(content-retention, ragged-row, empty-first-cell, run-together, multi-value,
single-column) plus an oracle-gated ligature repair (`T2N_LIGATURE_REPAIR`,
default on) that fixes glyph-drop corruption (`speciic` → `specific`) only when
the source page's own text layer confirms the correct spelling. It is MIT and
**not** in `requirements.txt` — install it separately and point
`DOCLING_VENV_PY` at its interpreter.

`DOCLING_DEVICE` picks the accelerator, and defaults to `auto`: CUDA if torch
sees it, else Apple-Silicon MPS, else CPU. Set it explicitly (`cuda` | `mps` |
`cpu`) only to override that probe — e.g. forcing `cpu` on a box whose GPU is
already saturated by an OCR run. The resolution happens inside the worker,
which is the only process with torch importable; the chosen device is printed
to stderr at worker startup.

General rule: **any page with an image gets OCR'd even if it already has a
text layer.** A native text layer is a cross-check, never a reason to skip
OCR — see the silent-failure detection below for why a text layer can lie.

Always run a cheap `fitz` quick-scan first (character count + image count per
page) to decide which path a given page needs, instead of committing the
whole document to one method.

Step 5 (frontier vision) is only justified for: figures/charts that need
layout understanding beyond OCR, or when steps 1–4 have all failed on a
given page. Every time step 4 or 5 is used, log a one-line note of *why*
steps 1–3 failed for that page — this makes it possible to notice if a
particular publisher/format pattern needs a permanent fix upstream instead of
a per-document escape hatch.

## `fitz` silent-failure detection (mandatory)

`fitz` (or any PDF text-layer extractor) can return text that *looks*
plausible while silently dropping content — vector-drawn glyphs, Type 3
fonts, or CID-encoded fonts with no `ToUnicode` map all produce this failure
mode. A page can report thousands of characters extracted and still be
functionally garbage. The quick-scan step must surface quality signals, not
just "did extraction return non-empty text" — and every check below is pure
script (PDF-library API calls + regex), costing 0 LLM tokens:

1. **Font risk flags** — inspect the page's font list. Flag pages using
   `Type 3` fonts, `Identity-H` encoding with no `ToUnicode` CMap, or subset
   fonts missing a character map. A flagged page's fitz text is not
   trustworthy — route it to OCR regardless of how much text it returned.
2. **Character-density anomaly** — for a text-heavy page, if the extracted
   character count is low (e.g. `< 100`) while the page's vector-drawing
   coverage is high, the "text" is likely drawn glyph outlines, not a real
   text layer. Route to OCR.
3. **Domain pattern miss** — the caller supplies an expected regex for the
   document type (e.g. numbered list markers, lettered options, section
   numbering). A page with zero matches for a pattern that adjacent pages
   satisfy is suspect — route to OCR.
4. **Sampling cross-check** — when any of the above trips, render one or two
   sample pages and ask a small local vision model for a rough character
   count. If it diverges from fitz's count by more than ~30%, re-OCR the
   whole document. This step still costs 0 hosted-LLM tokens (local model
   only).

The quick-scan should emit, per page: `char_count`, `font_risk`,
`pattern_hits`, and a final `verdict` of either `trust_fitz` or `force_ocr`.
Downstream code should always read the `verdict` field — never decide based
on raw extracted text length alone.

Note the gap between that design and what ships: these signals are computed
**per page**, but `--batch-dir` routing is decided **per book** — one trip of
the check sends the whole PDF to OCR. Per-page routing (mixing fitz and OCR
output inside one book) is future work.

## OCR output can fail silently too

The ladder's whole premise is that a step can succeed loudly and be wrong, so
the OCR rung gets the same treatment as the fitz rung. `surya_ocr_pdf()` counts
unparseable adapter output lines, images the adapter never answered for, and
pages that came back empty; after assembly it **raises** rather than writing a
markdown file if the empty-page ratio or the mean characters per page falls
outside `T2N_OCR_EMPTY_PAGE_MAX` (0.35) / `T2N_OCR_MIN_CHARS_PER_PAGE` (200).
The failure this exists to catch: a scanned book that once produced a 20 KB
`full_text.md` where ~1.6 MB was expected, and reported success in KB. Both
thresholds and their calibration are documented in
[`surya-adapter.md`](surya-adapter.md).

## Practical guidance

- Run OCR through an isolated Python environment per engine (these tools
  have finicky, sometimes conflicting dependency stacks — GPU driver
  bindings especially).
- On Windows, any OCR engine invoked as a subprocess should be read back in
  bytes mode and decoded explicitly as UTF-8 with error replacement — do not
  rely on the platform's default text-mode decoding, which can crash on
  legitimate UTF-8 subprocess output.
- Batch conversions should auto-route flagged pages to OCR rather than
  requiring a human to notice a bad conversion after the fact; single-file
  conversions can leave the decision to the caller.
