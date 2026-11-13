# The OCR adapter interface (and the Surya 0.22 reference implementation)

`converter/convert.py` never imports an OCR engine. When a book routes to the OCR rung it
runs one subprocess per batch of pages:

```
<SURYA_VENV_PY> <SURYA_ADAPTER> p0001.png p0002.png ...      # up to 20 images per call
```

That boundary exists because OCR stacks have heavy, mutually hostile dependency trees (GPU
driver bindings especially) and must not be able to break the converter. It also means the
engine is **pluggable**: anything that satisfies the contract below can be `SURYA_ADAPTER`,
including a stub used in tests. `converter/surya_adapter.py` is the reference implementation,
written against **surya-ocr 0.22.1**.

## The contract

**stdout carries JSON Lines and nothing else** — one object per input image, in input order:

```json
{"fixture": "p0011.png", "blocks": [{"text": "...", "bbox": [x0, y0, x1, y1]}]}
```

| Field | Requirement |
|---|---|
| `fixture` | The image filename exactly as passed. The caller parses the page index out of the `pNNNN` stem, so a renamed or reordered fixture silently assigns text to the wrong page. |
| `blocks[].text` | Plain text. Empty / whitespace-only blocks are dropped by the caller. |
| `blocks[].bbox` | `[x0, y0, x1, y1]` in **original image pixels**, top-left origin. **Required.** The caller uses it to rebuild reading order (see below). If your engine downscales internally, scale the boxes back before emitting them. |

Rules that are not about field shape and matter just as much:

- **One line per input image, even for a blank page** (`"blocks": []`). Silence is how the
  caller detects a dropped page; a page that legitimately has no text must say so.
- **All logging goes to stderr.** Anything printed to stdout that is not a JSON object
  corrupts the stream. The caller counts unparseable lines and reports them, but it cannot
  recover the page's text.
- **Exit non-zero on any failure.** The caller raises for the whole batch. A partial success
  reported as success is the failure mode this rung is most vulnerable to.

### Why bbox is not decorative

The caller reconstructs reading order from geometry: a column split first (all of the left
column, then the right, banded by full-width blocks), falling back to a plain top-to-bottom
sort when the page is single-column or the split is ambiguous. Without bboxes every block
lands at the origin and the page comes out in whatever order the engine happened to emit —
which on a two-column textbook page interleaves the columns line by line. The characters are
all present and individually correct, so **no downstream check catches it**; it is the same
defect the fitz path carries a dedicated column sort to avoid. `T2N_COLUMN_SORT=0` disables
the column split on both paths.

The OCR split is at the page center by default. `T2N_OCR_GUTTER=1` (default off) first looks
for a measured gutter — the vertical strip in the middle half of the page that the fewest
narrow blocks cross — for boxed layouts whose gutter sits well off center (one measured page:
38% of the width). If none qualifies, the page-center split applies as before.

### Output QC (the caller's side)

`surya_ocr_pdf()` counts, per book: unparseable stdout lines, images the adapter never
answered for, and pages that came out empty. After assembly it checks two ratios and
**raises** rather than writing a markdown file if either trips:

| Env | Default | Meaning |
|---|---|---|
| `T2N_OCR_EMPTY_PAGE_MAX` | `0.35` | Maximum fraction of pages with no text |
| `T2N_OCR_MIN_CHARS_PER_PAGE` | `200` | Minimum mean characters per page |

The defaults are calibrated on real scanned books converted through this path (two clinical
references, 689 and 788 pages): empty-page ratios `0.054` and `0.003`, mean chars/page `1791`
and `2005`. Scanned books legitimately contain blank, plate and figure-only pages, so the
gate sits well clear of them — roughly 6x above the worse observed empty ratio and 9x below
the lower observed density. The failure it exists to catch is the opposite shape: a scanned
book that once produced a 20 KB `full_text.md` where ~1.6 MB was expected (~25 chars/page)
and reported success.

## Reference implementation: Surya 0.22.x

### Why an adapter written for Surya 0.17 fails on 0.22

Surya ≥ 0.20 is a ground-up rework. A single ~650M-parameter VLM now does OCR, layout and
table structure together, served behind an OpenAI-compatible **inference server** rather than
being called in-process. `surya.ocr` no longer exists and `FoundationPredictor` was replaced
by `SuryaInferenceManager`, so an old adapter dies at import with
`No module named 'surya.ocr'`. The output shape changed too: blocks carry `html` (math as
`<math>`, tables as `<table>`) instead of flat text, which is why the adapter has an
HTML-to-text stage — `<br>` and block tags become newlines, `<table>` rows become
pipe-separated lines, `<math>` keeps its inner text.

Licensing: Surya's code is Apache-2.0; the **weights** are modified OpenRAIL-M, free for
personal use and for organizations under a revenue threshold. Check upstream before using
this rung commercially.

### Install (separate venv — never share the converter's)

```bash
uv venv /path/to/surya22-venv --python 3.12
uv pip install --python /path/to/surya22-venv/bin/python "surya-ocr>=0.22.1,<0.23"

export SURYA_VENV_PY=/path/to/surya22-venv/bin/python     # Scripts/python.exe on Windows
export SURYA_ADAPTER=/path/to/textbook-to-note/converter/surya_adapter.py
```

### Choosing a serving backend

`SuryaInferenceManager` picks a backend automatically: **NVIDIA GPU present → vllm**,
otherwise → **llama.cpp**. That autodetect is a trap on a Windows box with an NVIDIA card,
because vllm ships as a Docker image and on Windows that means WSL2 + NVIDIA Container
Toolkit. Set the backend explicitly.

| Route | When | Cost |
|---|---|---|
| `SURYA_INFERENCE_BACKEND=vllm` | Linux with an NVIDIA GPU, Docker + NVIDIA Container Toolkit already working | Highest throughput. On Windows it means standing up WSL2 + Docker first. |
| `SURYA_INFERENCE_BACKEND=llamacpp` | Everything else — Windows, macOS/Apple Silicon, CPU-only | Needs a `llama-server` binary on `PATH` (or `LLAMA_CPP_BINARY`). GGUF weights (~1.5 GB) download on first use. |

For llama.cpp on Windows, take a prebuilt release from
[ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/releases) — the
`llama-*-bin-win-cuda-*-x64.zip` build plus the matching `cudart-*` archive unpacked into the
same folder — and point `LLAMA_CPP_BINARY` at `llama-server.exe`. macOS: `brew install
llama.cpp`.

### The memory cap — read this before starting a server

**This is the single most expensive mistake available on this rung.** The weights are only
~1.5 GB, but llama.cpp sizes its KV cache from
`max(16384, SURYA_INFERENCE_PARALLEL × SURYA_INFERENCE_CTX_PER_SLOT)`, and Surya's defaults
are `8 × 12288 = 98304` tokens. That cache, not the model, is what turned into a reported
**48 GB** run on a single-page test. Cap it explicitly:

| Env | Default | Set it to |
|---|---|---|
| `SURYA_INFERENCE_PARALLEL` | 8 | `2` on an 8 GB GPU; `1` on CPU |
| `SURYA_INFERENCE_CTX_SIZE` | derived (98304) | `24576` with parallel 2; `12288` with parallel 1 |
| `SURYA_INFERENCE_KEEP_ALIVE` | false | `true` when you want the server to outlive one batch |

Per-slot context below ~12288 truncates page output, so lower `--parallel` rather than
`--ctx-size` when you need to shrink further.

### Run one persistent server (strongly recommended)

The caller invokes the adapter **once per 20 pages**. If each invocation spawns its own
server, every 20 pages pays a full model load. Start one server, export its URL, and the
adapter attaches instead of spawning:

```bash
# 1. start the server yourself, with the caps in the command line
llama-server -m ~/.cache/huggingface/hub/.../surya-2.gguf \
             --mmproj ~/.cache/huggingface/hub/.../surya-2-mmproj.gguf \
             -ngl 99 --host 127.0.0.1 --port 8555 \
             --parallel 2 --ctx-size 24576 \
             --alias datalab-to/surya-ocr-2 --jinja

# 2. point everything at it
export SURYA_INFERENCE_BACKEND=llamacpp
export SURYA_INFERENCE_URL=http://127.0.0.1:8555/v1
export SURYA_INFERENCE_PARALLEL=2
```

`--alias datalab-to/surya-ocr-2` is **required**: Surya compares the served model id against
its expected checkpoint and refuses to attach on a mismatch. Use `-ngl 0` to force CPU.

Then re-run the affected book through the only path that routes to OCR:

```bash
python converter/convert.py --batch-dir "folder/with/that/book" --force
```

### Image resolution

Surya 0.22 recommends images no wider than ~2048 px (96–192 DPI for a US-letter page);
`surya_ocr_pdf()` renders at 300 DPI (~2550 px). The adapter therefore downscales anything
wider than `T2N_SURYA_MAX_WIDTH` (default 2048) with LANCZOS and scales every bbox back to
original-image pixels, so the caller's geometry stays in the coordinate space of the PNG it
actually rendered.

## Measured on the reference setup

Windows 11, llama.cpp `b10082`, surya-ocr 0.22.1, `-ngl 0 --parallel 1 --ctx-size 12288`
(**CPU only** — the machine's GPU was occupied by another job), 5-page synthetic scan-style
render at 300 DPI downscaled to 2048 px, run end to end through
`convert.py --batch-dir --force-surya`:

| | |
|---|---|
| Wall clock | 500 s for 5 pages ⇒ **~0.010 pages/s**, ~100 s/page |
| `llama-server` peak RSS | **3121 MB** (weights ~1.4 GB + one 12288-token slot) |
| Same server, default params would have been | `8 × 12288 = 98304` tokens of KV cache — the 48 GB shape |
| Output | 6.2 KB, 0 empty pages, 1235 chars/page, 0 parse failures, 0 missing lines |
| Two-column page | left column emitted whole, then the right — not interleaved |
| Table on a ruled page | recovered as pipe-separated rows via the `<table>` → text path |

Two things this run establishes and one it does not. It establishes that the contract works
end to end (page alignment, bbox-driven column order, the QC counters) and that a capped
server stays around 3 GB rather than tens of GB. It does **not** establish GPU throughput —
see the GPU run below for that. Do not read 0.01 pages/s as "Surya is slow"; read it as
"CPU is not a route for whole books", which is what the hardware-tier table in
[`ocr-ladder.md`](ocr-ladder.md) already says.

### The same smoke on GPU

Same machine, same 5-page book, `llama-server -ngl 99 --parallel 2 --ctx-size 24576`
on an RTX 3070 Ti (8 GB), adapter attached via `SURYA_INFERENCE_URL`:

| | |
|---|---|
| Wall clock | 33.3 s for 5 pages ⇒ **~0.15 pages/s** end to end (includes adapter startup + server attach) |
| GPU memory | whole-card peak **3635 MB**, of which ~1.3 GB was an unrelated concurrent job ⇒ the capped server fits in **~2.3 GB** VRAM |
| Output | byte count and QC counters identical to the CPU run; two-column ordering check passed programmatically |

Caveats to carry with these numbers: n = 5 pages of a synthetic book, measured once, on a
card that was also hosting another process — treat 0.15 pg/s as an order-of-magnitude
anchor (whole-book throughput will differ; per-call adapter startup amortizes over larger
batches), not a benchmark. The VRAM number is the useful one: with the documented caps,
an 8 GB card runs the server with room to spare.

A caveat about the two-column check, because it changes how you build a test page: Surya's
layout stage segments columns from *visual* density. A synthetic page with six widely spaced
lines per column was read as six full-width lines and the columns were concatenated inside a
single block — no downstream sort can recover that. The same page redrawn at realistic body
density (24 tight lines per column) segmented perfectly. If you are validating an adapter,
use a page that looks like a real one.
