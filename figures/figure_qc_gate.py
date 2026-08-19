"""figure_qc_gate.py — Per-figure extraction + QC gate (internal engine).

Extraction (gate, in order; first success wins):
  0. geometric_match_bbox — direction-agnostic caption↔image matching (primary,
     deterministic, no VLM). Auto-detects whether captions sit above/below.
  1. existing-file QC      — reuse a prior crop if it passes.
  2. raw page candidates   — largest embedded rasters, decoration-filtered.
  3. ollama bbox retry x2  — local vision model suggests a bbox (weak; last resort).
  4. escalate              — {escalate:true} so the caller can fall back to a
                              frontier-model vision read.

QC chain on each candidate — two checks, both pure computation, both block:
  1. whitespace_borders  — content fills >=80% of frame
  2. text_bleed (fitz)   — body-text chars overlapping bbox < threshold
No model participates in the QC chain (issue #16: a model-backed check gave
the same crop different verdicts on repeat runs, so nothing a model says can
gate — and a number that changes between runs is not worth logging per crop
either). The local-vision helpers kept in this file (qc_text_contamination,
qc_caption_match, ollama_suggest_bbox) are LEGACY: only the non-strict
fallback ladder reaches them; the strict default never calls ollama at all.
Crop-level judgment lives in the workflow's frontier-model classification
step (workflows/note-writing.md, "classify every crop"), not here.

Modes:
  --check <img>                              run local QC only (no PDF context)
  --gate <book> --page N --caption "..." \\   full gate: QC existing | extract whole page |
        --fig-id X --out <path>               vision-guided retry x2 | escalate flag
        [--existing <path>] [--pdf <path>]

QC fail → existing file is DELETED.
After 2 retries → return {pass:false, escalate:true} so the caller can fall back
to a frontier-model vision read.

Updates figure_qc_log.json on every check. Auto-suggests purge when fail_rate > 30% and checked >= 20.

Configuration (env vars, see shared/config.py for BOOKS_DIR / OUTPUT_DIR):
  T2N_FIGURE_MD_BASE    — root of the converted-markdown library searched for the
                          pre-extracted crop <root>/<book>/figures/
                          (falls back to TEXTBOOK_DIR, then OUTPUT_DIR)
  T2N_OLLAMA_HOST       — ollama base URL (default http://127.0.0.1:11434)
  T2N_OLLAMA_VISION_MODEL — vision model tag (default minicpm-v:8b)
  T2N_QC_LOG_PATH       — QC log file path (default <OUTPUT_DIR>/figure_qc_log.json)
  T2N_QC_WHITESPACE_MIN — whitespace-fill QC threshold (default 0.80)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
# The pre-extracted-crop fast path lives in the *converted markdown* library,
# one subdir per book, each with a figures/ folder — i.e. under OUTPUT_DIR, which
# is where convert.py writes. (BOOKS_DIR is the *source PDF* folder; it holds no
# markdown and no figures/, so it cannot be this root.) T2N_FIGURE_MD_BASE
# overrides it for a corpus whose markdown lives apart from fresh conversions.
# TEXTBOOK_DIR is honoured in between: citations/ already uses that name for this
# same "converted markdown corpus", so a corpus that is already pointed at once
# does not have to be pointed at twice under a second name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import DASH_CHARS
try:
    from shared.config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = Path("./output")

TEXTBOOK_MD_BASE = Path(os.environ.get("T2N_FIGURE_MD_BASE")
                        or os.environ.get("TEXTBOOK_DIR")
                        or OUTPUT_DIR)

# ─── Figure-specific config (env-driven, not shared across the repo) ─────────
OLLAMA = os.environ.get("T2N_OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("T2N_OLLAMA_VISION_MODEL", "minicpm-v:8b")  # vision model; use /api/generate (some vision models 500 on /api/chat with images)

QC_LOG_PATH = Path(os.environ.get("T2N_QC_LOG_PATH", str(OUTPUT_DIR / "figure_qc_log.json")))

# Vision-call decoding is pinned to greedy (temperature 0 + top_k 1 + fixed
# seed). Measured on 25 crops x 5 runs, sampling alone made the OCR long-line
# count differ between runs on 16 of 24 cleanly measured crops; the pin removes
# that, but 10 of 25 crops still shifted on the FIRST call against a crop — the
# only call an extraction ever makes — which is why no vision call gates, or
# runs, in the QC chain any more (issue #16). The pin stays for the legacy
# non-strict ladder's bbox suggestion, which does feed a crop attempt.
VISION_SEED = int(os.environ.get("T2N_OLLAMA_SEED", "0"))


def _vision_options(num_predict: int) -> dict:
    """Decoding options for every vision call in this file (greedy, seeded)."""
    return {"num_predict": num_predict, "temperature": 0,
            "top_k": 1, "top_p": 1.0, "seed": VISION_SEED}

# ─── Tunable thresholds ───────────────────────────────────────────────────────
WHITESPACE_FILL_MIN = float(os.environ.get("T2N_QC_WHITESPACE_MIN", "0.80"))  # content bbox / image bbox must be ≥ this
TEXT_BLEED_CHAR_MAX = 50         # fitz PROSE-chars-in-region threshold. Recalibrated
                                 # 100→50 when counting switched from all-text to
                                 # prose-only (labels excluded): legit figures now score
                                 # ~0, so a stricter bar catches even a half-line of a
                                 # clipped caption/body sentence. (labels wrap to short
                                 # lines, so they rarely misclassify as prose.)
LONG_LINE_CHARS = 30
LONG_LINE_COUNT_MAX = 7          # OCR long-line count <= this (legacy diagnostic only)
PURGE_FAIL_RATE = 0.30           # fail rate > this AND
PURGE_CHECKED_MIN = 20           # checked >= this → suggest purge
RETRY_LIMIT = 2                  # vision-guided crop retries before escalation
# Structural (NOT gate thresholds): distinguish in-figure labels from prose bleed,
# and let extraction swallow overlay labels that sit outside the raster rect.
PROSE_LINE_MIN = 35              # a text line >= this many chars = prose (caption/body);
                                 # shorter scattered lines = figure labels (axis nums, anatomy/EMG)
LABEL_REACH = 36                 # pt: overlay label within this of the figure box is part of it


# ─── Local QC checks (0 LLM tokens) ───────────────────────────────────────────

def qc_whitespace_borders(img_path: Path) -> tuple[bool, str]:
    """Reject if image has wasted whitespace padding (content < 80% of frame)."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return (True, "skip:pillow_or_numpy_missing")
    try:
        im = Image.open(img_path).convert("L")
        arr = np.array(im)
        # near-white = >240; content = anything else
        content_mask = arr < 240
        if not content_mask.any():
            return (False, "all_whitespace_image")
        rows = np.any(content_mask, axis=1)
        cols = np.any(content_mask, axis=0)
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        content_area = (rmax - rmin + 1) * (cmax - cmin + 1)
        total_area = arr.shape[0] * arr.shape[1]
        fill = content_area / total_area
        if fill < WHITESPACE_FILL_MIN:
            return (False, f"whitespace_padding fill={fill:.2f}<{WHITESPACE_FILL_MIN}")
        return (True, f"fill={fill:.2f}")
    except Exception as e:
        return (True, f"skip:{e}")


def qc_text_bleed(pdf_path: str | None, page_idx: int | None,
                  bbox: tuple | None) -> tuple[bool, str]:
    """fitz-based: count body text chars overlapping the figure bbox."""
    if not pdf_path or page_idx is None or not bbox:
        return (True, "skip:no_pdf_context")
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_idx]
        # Only PROSE (caption / body paragraphs) counts as bleed — that is the real
        # failure mode (crop swallowed a sentence). In-figure labels (axis numbers,
        # anatomy/EMG/legend text) are legitimate figure content and must NOT count,
        # else label-rich figures (graphs, labelled anatomy) false-fail. Discriminate
        # by line length: prose has long lines; labels are short scattered fragments.
        chars = 0          # prose chars (the gated failure mode)
        label_chars = 0    # in-figure labels (informational only)
        for b in page.get_text("blocks"):
            bb = b[:4]
            text = b[4] if len(b) > 4 else ""
            block_type = b[6] if len(b) > 6 else 0
            if block_type != 0:
                continue
            x0 = max(bb[0], bbox[0]); y0 = max(bb[1], bbox[1])
            x1 = min(bb[2], bbox[2]); y1 = min(bb[3], bbox[3])
            if x1 <= x0 or y1 <= y0:
                continue
            ax = (bb[2] - bb[0]) * (bb[3] - bb[1])
            if ax <= 0:
                continue
            ix = (x1 - x0) * (y1 - y0)
            if ix / ax <= 0.5:
                continue
            n = len(text.strip())
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            is_prose = (CAPTION_RE.match(text.lstrip()) is not None
                        or any(len(ln) >= PROSE_LINE_MIN for ln in lines))
            if is_prose:
                chars += n
            else:
                label_chars += n
        doc.close()
        if chars >= TEXT_BLEED_CHAR_MAX:
            return (False, f"text_bleed prose_chars={chars}>={TEXT_BLEED_CHAR_MAX} (label_chars={label_chars} ignored)")
        return (True, f"prose_chars={chars} label_chars={label_chars}")
    except Exception as e:
        return (True, f"skip:{e}")


def qc_text_contamination(img_path: Path) -> tuple[bool, str]:
    """LEGACY (0.7.1) — count long body-text lines in the crop via local OCR.

    No longer called by run_local_qc: the count is not reproducible between
    runs of the same crop, so it can neither gate nor serve as a per-crop log
    signal (issue #16). Kept only as an opt-in diagnostic for callers outside
    the QC chain; the returned bool says whether the count is over
    LONG_LINE_COUNT_MAX — never gate on it.

    Tesseract is deliberately avoided for CJK OCR (unreliable diacritics/spacing);
    the local vision model is reused via a plain transcription prompt.
    """
    if not _ollama_alive():
        return (True, "skip:ollama_unreachable")
    try:
        from PIL import Image
        im = Image.open(img_path)
        if im.width > 2000:
            ratio = 2000 / im.width
            im = im.resize((2000, int(im.height * ratio)))
        if im.mode != "RGB":
            im = im.convert("RGB")
        from io import BytesIO
        buf = BytesIO()
        im.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        prompt = (
            "Transcribe ALL text visible in this image, verbatim, one line of "
            "text per output line. Do not describe the image, do not add "
            "commentary or numbering — output only the transcribed text lines. "
            "If there is no text, output nothing."
        )
        resp = _ollama_post("/api/generate", {
            "model": MODEL,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": _vision_options(1500),
        })
        text = (resp.get("response") or "").strip()
        long_lines = sum(1 for ln in text.splitlines()
                         if len(ln.strip()) >= LONG_LINE_CHARS)
        if long_lines > LONG_LINE_COUNT_MAX:
            return (False, f"text_contamination long_lines={long_lines}>{LONG_LINE_COUNT_MAX}")
        return (True, f"long_lines={long_lines}")
    except Exception as e:
        return (True, f"skip:ollama_err:{e}")


# ─── Local vision-model checks (0 frontier-model tokens, ~3-8s each) ──────────

def _ollama_alive(timeout=2) -> bool:
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


def _ollama_post(path: str, payload: dict, timeout=300):
    req = urllib.request.Request(
        OLLAMA + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _img_b64_rgb(path) -> str:
    """Encode an image as base64 PNG, forced to RGB.

    Some local vision models return HTTP 500 on CMYK or grayscale (L-mode)
    inputs — and figures extracted from print PDFs are often CMYK (color
    plates) or L (grayscale radiographs). Convert to RGB before sending.
    """
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(path)
        if im.mode != "RGB":
            im = im.convert("RGB")
        buf = BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # PIL unavailable / unreadable → best-effort raw bytes
        return base64.b64encode(Path(path).read_bytes()).decode()


def qc_caption_match(img_path: Path, caption: str) -> tuple[bool, str]:
    """LEGACY (0.7.1) — yes/no: does the image content match the caption?

    No longer called by run_local_qc (issue #16 — the local vision model's
    judgment is not reproducible enough to be worth a per-crop call). Kept as
    an opt-in diagnostic only. Uses /api/generate (the vision model may return
    HTTP 500 on /api/chat with images).
    """
    if not caption:
        return (True, "skip:no_caption")
    if not _ollama_alive():
        return (True, "skip:ollama_unreachable")
    img_b64 = _img_b64_rgb(img_path)
    prompt = (
        "You are checking if a figure matches its caption.\n"
        f"CAPTION: {caption[:500]}\n\n"
        "Examine the image. Does the visual content match what the caption describes?\n"
        "Consider: imaging modality (X-ray/MRI/CT/US/clinical photo/diagram/chart), "
        "anatomical region, depicted subject. Ignore minor differences.\n"
        "Reply with EXACTLY one word: YES or NO. Then a colon and a brief reason (≤15 words)."
    )
    try:
        resp = _ollama_post("/api/generate", {
            "model": MODEL,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": _vision_options(1200),
        })
        out = (resp.get("response") or "").strip()
        first = out.split(":", 1)[0].strip().upper()
        if first.startswith("YES"):
            return (True, f"caption_match:{out[:120]}")
        if first.startswith("NO"):
            return (False, f"caption_mismatch:{out[:120]}")
        return (True, f"skip:ambiguous:{out[:120]}")
    except Exception as e:
        return (True, f"skip:ollama_err:{e}")


def ollama_suggest_bbox(pdf_path: str, page_idx: int, caption: str) -> tuple | None:
    """LEGACY — render whole page → ask the local vision model for a bbox of
    the matching figure as a fraction of the page.

    Only the non-strict fallback ladder calls this (strict mode, the default,
    never does). Its suggestion is a proposal only — any crop it produces must
    still pass the computed QC checks. Returns (x0, y0, x1, y1) in PDF
    coordinates, or None.
    """
    if not _ollama_alive():
        return None
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_idx]
        pw, ph = page.rect.width, page.rect.height
        # Render at 1.5x for clarity, encode as PNG
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        png_bytes = pix.tobytes("png")
        doc.close()
        img_b64 = base64.b64encode(png_bytes).decode()
        prompt = (
            "This is a textbook page. Find the figure matching this caption:\n"
            f"CAPTION: {caption[:400]}\n\n"
            "Return ONLY a JSON object with the figure's bounding box as fractions of "
            "page size (0.0-1.0):\n"
            '{"x0": 0.10, "y0": 0.05, "x1": 0.55, "y1": 0.45}\n'
            "x0,y0 = top-left; x1,y1 = bottom-right. No prose, JSON only."
        )
        resp = _ollama_post("/api/generate", {
            "model": MODEL,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": _vision_options(1500),
        })
        out = (resp.get("response") or "").strip()
        m = re.search(r"\{[^{}]*\}", out)
        if not m:
            return None
        data = json.loads(m.group(0))
        x0 = float(data["x0"]) * pw
        y0 = float(data["y0"]) * ph
        x1 = float(data["x1"]) * pw
        y1 = float(data["y1"]) * ph
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)
    except Exception:
        return None


# ─── Extraction helpers ───────────────────────────────────────────────────────

def is_decoration(bb, pw, ph) -> bool:
    if not bb or len(bb) != 4:
        return False
    x0, y0, x1, y1 = bb
    h = y1 - y0; w = x1 - x0
    if not (0 < h < 60):
        return False
    if w < 0.9 * pw:
        return False
    return y1 < 50 or y0 < 0 or y0 > ph - 50


def extract_raw_candidates(pdf_path: str, page_idx: int) -> list[dict]:
    """Return all non-decoration raw images on a page, largest first."""
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    pw, ph = page.rect.width, page.rect.height
    raws = list(page.get_image_info(xrefs=True))
    out = []
    for im in raws:
        bb = im.get("bbox")
        if not bb or is_decoration(bb, pw, ph):
            continue
        area = (bb[2] - bb[0]) * (bb[3] - bb[1])
        out.append({"xref": im["xref"], "bbox": bb, "area": area})
    doc.close()
    out.sort(key=lambda d: -d["area"])
    return out


# Dash variants seen in figure captions across books. CAPTION_RE accepts any
# (with optional spaces); normalize_fig_id folds them all (plus period) to a
# canonical dot-separated id so '64-5' / '64–5' / '30 — 44' / '64.5' all compare
# equal. Root-cause fix for en-dash misses that would otherwise force fallback.
# The set itself lives in shared/config.py so it stays in lockstep with the
# converter's reference detection (#10).
_DASHES = DASH_CHARS
_DASH_DOT = _DASHES + "."  # dash variants or dot, for neighbour checks below
# Captures: chapter digit(s) + dash/period + page-numeric portion + optional sub-letter.
# The sub-letter is required to disambiguate multi-panel figures (e.g. Fig 4-1A
# vs 4-1D on the same page). The numeric portion accepts OCR-mangled chars
# `l`/`I`/`O`/`S` because scanned-derived captions frequently substitute these
# for `1`/`0`/`5`; normalize_fig_id() collapses them deterministically.
CAPTION_RE = re.compile(
    r"\s*(?:FIG(?:URE)?\.?|Figure|Fig\.?)\s*"
    r"(\d+\s*[" + _DASHES + r"\.]\s*[lISO\d]+[A-Za-z]?)",
    re.IGNORECASE,
)


def normalize_fig_id(raw: str) -> str:
    """Canonicalize a figure id to dot-separated form, dash-variant agnostic.

    Folds:
      - dash variants (ASCII hyphen / figure / en / em / horizontal bar) → '.'
      - Class A OCR substitutions: lowercase `l` / uppercase `I` → '1';
        capital `S` → '5'; capital `O` → '0' (each only inside the numeric
        portion, so the trailing sub-letter A/B/C/D is preserved).
      - whitespace → removed
      - case → lower
    e.g. '4-1D', '4–1D', '4-lD', '4.1d' all canonicalize to '4.1d'.
    """
    s = raw.strip()
    # Class A OCR substitutions (positional: only when neighbour is digit/sep)
    out = []
    for i, ch in enumerate(s):
        if ch in ("l", "I"):
            prev_ok = out and (out[-1].isdigit() or out[-1] in _DASH_DOT)
            next_ok = i + 1 < len(s) and (s[i + 1].isdigit() or s[i + 1] in _DASH_DOT)
            if prev_ok or next_ok:
                out.append("1")
                continue
        if ch == "S" and out and (out[-1].isdigit() or out[-1] in _DASH_DOT):
            out.append("5")
            continue
        if ch == "O" and out and (out[-1].isdigit() or out[-1] in _DASH_DOT):
            out.append("0")
            continue
        out.append(ch)
    s = "".join(out)
    for d in _DASHES:
        s = s.replace(d, ".")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def geometric_match_bbox(pdf_path: str, page_idx: int, fig_id: str) -> tuple | None:
    """Direction-agnostic caption↔image matching (deterministic, no VLM).

    Some books print captions ABOVE figures, others BELOW. Rather than assume a
    direction, auto-detect per page: for each direction assign every raster to its
    nearest caption in that direction, then keep the direction whose captions
    collectively own the MOST images (tie-break: smaller total gap). Return the
    union bbox of the raster(s) owned by the target fig_id's caption. Caption TEXT
    is excluded from the bbox (the note adds its own caption).

    Multi-panel figures whose panels are separate rasters are handled: every
    raster nearest the same caption is unioned.
    """
    import fitz
    PAD = 6
    TOL = 30  # allow caption to sit slightly inside the image's top/bottom edge
    target = normalize_fig_id(fig_id)
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_idx]
        pw, ph = page.rect.width, page.rect.height
        captions = []
        label_spans = []   # short lines = figure overlay labels (axis nums, anatomy/EMG)
        prose_blocks = []  # body / caption paragraphs = hard figure boundaries
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            blk_lines = []
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                line_txt = "".join(s.get("text", "") for s in spans)
                blk_lines.append(line_txt.strip())
                # Caption detection: search the FULL joined line, not just spans[0],
                # and tolerate a short leader prefix (≤10 chars). Some books prepend
                # a square-bullet glyph that the PDF text extractor decodes as a
                # stray letter before "Figure X-Y", which made a plain
                # `match(spans[0])` miss the caption entirely. Limiting the regex
                # offset to ≤10 chars and the line length to ≤200 chars rejects
                # inline prose references like "…see Figure 3-5 below…".
                m = CAPTION_RE.search(line_txt)
                if m and m.start() <= 10 and len(line_txt) <= 200:
                    line_bb = line.get("bbox") or spans[0].get("bbox")
                    if line_bb:
                        captions.append({"fig": normalize_fig_id(m.group(1)),
                                         "top": line_bb[1], "bot": line_bb[3]})
                    continue
                ltext = line_txt.strip()
                if ltext and len(ltext) <= 30 and not CAPTION_RE.match(ltext):
                    bxs = [s.get("bbox") for s in spans if s.get("bbox")]
                    if bxs:
                        label_spans.append((min(b[0] for b in bxs), min(b[1] for b in bxs),
                                            max(b[2] for b in bxs), max(b[3] for b in bxs)))
            # whole-block classification: a paragraph with a long line or a caption
            # line is PROSE → it bounds the figure (label-expansion must not cross it)
            joined = [l for l in blk_lines if l]
            if joined and (CAPTION_RE.match(joined[0]) or any(len(l) >= PROSE_LINE_MIN for l in joined)):
                bb = block.get("bbox")
                if bb:
                    prose_blocks.append(tuple(bb))
        images = [im["bbox"] for im in page.get_image_info(xrefs=True)
                  if im.get("bbox") and not is_decoration(im["bbox"], pw, ph)]
        doc.close()
        if not captions or not images:
            return None

        def assign(direction):
            owner, gap = {}, 0.0
            for bb in images:
                itop, ibot = bb[1], bb[3]
                best = None
                for ci, c in enumerate(captions):
                    if direction == "above":
                        if c["top"] > itop + TOL:
                            continue
                        d = itop - c["top"]
                    else:  # below
                        if c["bot"] < ibot - TOL:
                            continue
                        d = c["bot"] - ibot
                    if d < -TOL:
                        continue
                    if best is None or abs(d) < abs(best[0]):
                        best = (d, ci)
                if best is not None:
                    owner.setdefault(best[1], []).append(bb)
                    gap += abs(best[0])
            return owner, gap

        a_owner, a_gap = assign("above")
        b_owner, b_gap = assign("below")
        if (len(a_owner), -a_gap) >= (len(b_owner), -b_gap):
            owner, direction = a_owner, "above"
        else:
            owner, direction = b_owner, "below"

        tgt, tgt_caps = [], []
        for ci, c in enumerate(captions):
            if c["fig"] == target and ci in owner:
                tgt.extend(owner[ci])
                tgt_caps.append(c)
        if not tgt:
            return None
        # raster union (the figure's image panels)
        rx0 = min(b[0] for b in tgt); ry0 = min(b[1] for b in tgt)
        rx1 = max(b[2] for b in tgt); ry1 = max(b[3] for b in tgt)
        # caption-clip band: never cross the owning caption (it sits on one side)
        if direction == "above":
            cap_y0 = max(c["bot"] for c in tgt_caps) + 2; cap_y1 = ph
        else:
            cap_y0 = 0.0; cap_y1 = min(c["top"] for c in tgt_caps) - 2
        # tighten the OPEN side (away from own caption) to the nearest neighbouring
        # figure / caption, so label-expansion can't chain into the figure next to
        # this one (regression guard: stacked figures in one column).
        TOLN = 4
        other_imgs = [im for im in images if im not in tgt]
        other_caps = [c for c in captions if c not in tgt_caps]
        def _hoverlap(b):  # block horizontally overlaps the figure's image column
            return b[2] > rx0 and b[0] < rx1
        if direction == "above":  # open side is below ry1
            for ob in other_imgs:
                if ob[1] >= ry1 - TOLN:
                    cap_y1 = min(cap_y1, ob[1] - 2)
            for c in other_caps:
                if c["top"] >= ry1 - TOLN:
                    cap_y1 = min(cap_y1, c["top"] - 2)
            for pb in prose_blocks:
                if pb[1] >= ry1 - TOLN and _hoverlap(pb):
                    cap_y1 = min(cap_y1, pb[1] - 2)
        else:                     # open side is above ry0
            for ob in other_imgs:
                if ob[3] <= ry0 + TOLN:
                    cap_y0 = max(cap_y0, ob[3] + 2)
            for c in other_caps:
                if c["bot"] <= ry0 + TOLN:
                    cap_y0 = max(cap_y0, c["bot"] + 2)
            for pb in prose_blocks:
                if pb[3] <= ry0 + TOLN and _hoverlap(pb):
                    cap_y0 = max(cap_y0, pb[3] + 2)
        # column band: clamp single-column figures to their half so label-expansion
        # can't grab the adjacent column; full-width figures keep the whole page.
        mid = pw / 2.0
        if rx0 < mid < rx1:
            col_x0, col_x1 = 0.0, pw
        elif (rx0 + rx1) / 2.0 < mid:
            col_x0, col_x1 = 0.0, mid
        else:
            col_x0, col_x1 = mid, pw
        # Swallow overlay labels (axis numbers, anatomy/EMG/legend) that sit OUTSIDE
        # the raster rect — within LABEL_REACH, in-column, on the figure side of the
        # caption. Raw-xref extraction loses these; this is the root-cause fix for
        # "labels got cut". Fixed-point so label chains (e.g. stacked axis ticks +
        # axis title) are reached transitively.
        ex0, ey0, ex1, ey1 = rx0, ry0, rx1, ry1
        for _ in range(4):
            changed = False
            for (lx0, ly0, lx1, ly1) in label_spans:
                if lx1 <= col_x0 or lx0 >= col_x1:        # other column
                    continue
                if ly1 <= cap_y0 or ly0 >= cap_y1:        # past the caption
                    continue
                if (lx0 <= ex1 + LABEL_REACH and lx1 >= ex0 - LABEL_REACH
                        and ly0 <= ey1 + LABEL_REACH and ly1 >= ey0 - LABEL_REACH):
                    n0x, n0y = min(ex0, lx0), min(ey0, ly0)
                    n1x, n1y = max(ex1, lx1), max(ey1, ly1)
                    if (n0x, n0y, n1x, n1y) != (ex0, ey0, ex1, ey1):
                        ex0, ey0, ex1, ey1 = n0x, n0y, n1x, n1y
                        changed = True
            if not changed:
                break
        # final box: pad, clipped to caption band, column, and page
        x0 = max(col_x0, ex0 - PAD)
        x1 = min(col_x1, ex1 + PAD)
        y0 = max(cap_y0, ey0 - PAD, 0.0)
        y1 = min(cap_y1, ey1 + PAD, ph)
        return (x0, y0, x1, y1)
    except Exception:
        return None


def xref_is_ink_separation(pdf_path: str, xref: int) -> bool:
    """Return True if image xref uses /DeviceN or /Separation colorspace.
    Raw stream extraction (extract_xref_image) will INVERT colors in this case
    because ink-amount semantics (1.0 = max ink = dark) are misread as
    brightness (1.0 = white). Caller must use render_bbox instead.
    """
    import fitz
    try:
        doc = fitz.open(pdf_path)
        info = doc.xref_object(xref)
        cs_match = re.search(r"/ColorSpace\s+(\d+)\s+0\s+R", info or "")
        if cs_match:
            cs_obj = doc.xref_object(int(cs_match.group(1))) or ""
        else:
            cs_obj = info or ""
        doc.close()
        return ("/DeviceN" in cs_obj) or ("/Separation" in cs_obj)
    except Exception:
        return False


def render_bbox(pdf_path: str, page_idx: int, bbox: tuple, out_path: Path,
                zoom: float = 2.0) -> bool:
    """Render a region of the page to PNG/JPEG."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_idx]
        clip = fitz.Rect(*bbox)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() in (".jpg", ".jpeg"):
            pix.save(str(out_path), jpg_quality=88)
        else:
            pix.save(str(out_path))
        doc.close()
        return True
    except Exception:
        return False


def extract_xref_image(pdf_path: str, xref: int, out_path: Path) -> bool:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        info = doc.extract_image(xref)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(info["image"])
        doc.close()
        return True
    except Exception:
        return False


# ─── QC log (per-book fail rate) ──────────────────────────────────────────────

def log_qc(book: str, passed: bool, reasons: list[str],
           method: str | None = None) -> dict:
    """Append result to figure_qc_log.json. Returns updated entry + purge flag.

    `method` records which extractor produced the result (geometric_match |
    raw_xref | ollama_retry | existing | FAIL) so the per-book log shows whether
    extractions are running deterministically or leaning on fallback.
    """
    QC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = {}
    if QC_LOG_PATH.exists():
        try:
            log = json.loads(QC_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            log = {}
    e = log.setdefault(book, {"checked": 0, "failed": 0, "fail_reasons": []})
    e["checked"] += 1
    if method:
        m = e.setdefault("methods", {})
        m[method] = m.get(method, 0) + 1
    if not passed:
        e["failed"] += 1
        e["fail_reasons"] = (e.get("fail_reasons", []) + reasons)[-20:]
    e["fail_rate"] = round(e["failed"] / max(1, e["checked"]), 3)
    e["last_seen"] = date.today().isoformat()
    QC_LOG_PATH.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    purge_recommended = (
        e["checked"] >= PURGE_CHECKED_MIN
        and e["fail_rate"] > PURGE_FAIL_RATE
    )
    return {**e, "purge_recommended": purge_recommended}


# ─── Orchestrator ─────────────────────────────────────────────────────────────

# Checks whose "skip" means the QC verdict is not a real verdict — the check's
# dependency was unavailable (PIL/numpy missing, no PDF context) and it
# defaulted to pass. Exactly the two checks the QC chain runs; nothing else
# can weaken the gate by not running.
_DEGRADE_CHECKS = {"whitespace", "text_bleed"}


def qc_degradation(reasons: list[str]) -> tuple[bool, list[str]]:
    """Derive (qc_degraded, qc_skipped) from a run_local_qc reasons list.

    Each reason is "<check_name>:<message>"; a check counts as skipped when
    its message starts with "skip:" (see _DEGRADE_CHECKS for which checks
    count toward degradation).
    """
    skipped = []
    for r in reasons:
        name, sep, rest = r.partition(":")
        if name in _DEGRADE_CHECKS and sep and rest.startswith("skip:"):
            skipped.append(name)
    return (bool(skipped), skipped)


def run_local_qc(img_path: Path, caption: str | None,
                 pdf_path: str | None = None,
                 page_idx: int | None = None,
                 bbox: tuple | None = None) -> tuple[bool, list[str]]:
    """Run full QC chain. Returns (pass, reasons-list).

    Two checks run, both BLOCK, and both are pure computation: whitespace fill
    and fitz text bleed. No model is called (issue #16). The OCR long-line
    count used to block here — but the count is not reproducible between runs
    of the same crop, so the same crop passed on one run and failed on the
    next; greedy decoding did not fix it (only the FIRST call on a crop
    drifts, computed against whatever the previous image left in the server's
    cache, and that first call is the only one an extraction ever makes). A
    check like that cannot gate, and a per-crop number that changes between
    runs is not worth 2 s of GPU to log either, so it was removed outright.

    Removing it costs nothing on the path that matters: qc_text_bleed reads
    the PDF's own text layer and catches the same failure mode (a crop that
    swallowed a sentence) with better evidence; the scanned backend never
    calls this chain at all (figure_scanned.py runs its own whitespace-only
    QC); and whether a crop is *worth embedding* is judged downstream by the
    workflow's frontier-model classification step, which reads every crop.

    Correctness is secured upstream by geometric_match_bbox producing the right
    crop. `caption` is kept in the signature for callers; it no longer
    triggers any check here.
    """
    reasons = []
    ok, msg = qc_whitespace_borders(img_path)
    reasons.append(f"whitespace:{msg}")
    if not ok:
        return (False, reasons)
    ok, msg = qc_text_bleed(pdf_path, page_idx, bbox)
    reasons.append(f"text_bleed:{msg}")
    if not ok:
        return (False, reasons)
    return (True, reasons)


def gate(book: str, fig_id: str, caption: str, out_path: Path,
         existing_path: Path | None = None,
         pdf_path: str | None = None,
         page_idx: int | None = None,
         existing_bbox: tuple | None = None,
         strict_geometric_only: bool = False) -> dict:
    """Full gate flow (deterministic geometry first, VLM only as deep fallback):
      0. **geometric_match_bbox** — direction-agnostic caption↔image matching:
         auto-detect whether captions sit above/below, assign each raster to its
         nearest caption, crop the raster(s) owned by fig_id's caption (union for
         multi-panel). No VLM, no caption-direction assumption.
      1. Existing file QC — reuse a prior crop if it passes. If it fails → DELETE.
      2. Raw page candidates — area-largest first, decoration filtered.
      3. Vision-guided crop retry (up to RETRY_LIMIT) — render whole page,
         ask the local vision model for bbox JSON (weak; last resort).
      4. Escalate to a frontier-model vision read (caller responsibility).

    strict_geometric_only=True → DETERMINISTIC CONTRACT: only step 0 is on the
    execution path. No existing-file reuse, no raw_xref, no VLM bbox, no size-
    based pick. A fig_id that cannot be deterministically matched returns
    {pass:False, hard_fail:True, reason:...} rather than silently resolving to a
    wrong raster. Steps 1-4 functions remain defined but are never called here.

    Every returned dict also carries qc_degraded (bool) and qc_skipped (list of
    check names): true/non-empty when a QC check in the deciding run_local_qc
    call had to skip for lack of a dependency (PIL/numpy, PDF context, ollama)
    and defaulted to pass rather than giving a real verdict — see
    qc_degradation(). False/[] when every check that ran gave a real verdict.
    """
    fig_norm = normalize_fig_id(fig_id) if fig_id else ""
    attempts = []
    have_pdf = bool(pdf_path) and page_idx is not None
    qc_skipped_acc: list[str] = []

    def _track_qc(reasons: list[str]) -> None:
        for s in qc_degradation(reasons)[1]:
            if s not in qc_skipped_acc:
                qc_skipped_acc.append(s)

    # Step -1: backend selection (capability-based). Scanned PDFs route to the
    # caption-anchor backend (figure_scanned.py); born-digital continues into
    # the existing geometric_match flow below. Backend decision happens BEFORE
    # extraction, not inside it.
    if have_pdf and fig_id:
        try:
            import figure_scanned as _scan
            import fitz as _fitz
            _doc = _fitz.open(pdf_path)
            _page = _doc[page_idx]
            _decision = _scan._select_backend(_page)
            _doc.close()
            if _decision.backend == "caption_anchor":
                result = _scan.extract_scanned(
                    pdf_path, page_idx, fig_id, caption, out_path,
                )
                method = result.get("match_method", "caption_anchor")
                ok = result.get("pass", False)
                log_qc(book, ok,
                       [result.get("reason", "")] if not ok else ["scanned:pass"],
                       method=method if ok else "FAIL")
                if ok:
                    return {"pass": True, "file": result["file"],
                            "attempts": [{"src": "caption_anchor", "pass": True,
                                          "backend_confidence": _decision.confidence}],
                            "escalate": False,
                            "match_method": "caption_anchor",
                            "fig_id_normalized": result["fig_id_normalized"],
                            "qc_degraded": False, "qc_skipped": []}
                # Scanned backend failure semantics (policy vs content):
                #   hard_fail = content semantics (caption_anchor refused).
                #     Strict OR non-strict, the page's structure says
                #     caption_anchor is the right backend → respect the refusal,
                #     never fall through to born-digital geometric.
                #   strict_geometric_only is a born-digital-path policy flag;
                #     it does NOT apply once we've routed to caption_anchor.
                # Therefore: only check result.hard_fail here, never strict.
                if result.get("hard_fail"):
                    return {"pass": False, "file": None,
                            "attempts": [{"src": "caption_anchor", "pass": False,
                                          "reasons": [result.get("reason", "")],
                                          "backend_confidence": _decision.confidence}],
                            "escalate": False, "hard_fail": True,
                            "match_method": "FAIL",
                            "fig_id_normalized": fig_norm,
                            "reason": result.get("reason", "scanned hard_fail"),
                            "qc_degraded": False, "qc_skipped": []}
                # Non-hard scanned failure (e.g., QC reject without an
                # identity/geometry guard firing) → escalate for human
                # review. Still no fall-through to born-digital path.
                return {"pass": False, "file": None,
                        "attempts": [{"src": "caption_anchor", "pass": False,
                                      "reasons": [result.get("reason", "")]}],
                        "escalate": True,
                        "err": result.get("reason", "scanned extraction failed"),
                        "qc_degraded": False, "qc_skipped": []}
        except Exception as _e:
            # Backend-selection error must NOT silently fall through to
            # geometric_match on a scanned page (that always returns wrong
            # bbox). Surface the error.
            attempts.append({"src": "backend_select",
                             "pass": False, "reasons": [f"select_err:{_e}"]})

    # Step 0: geometric direction-agnostic caption↔image match (primary, no VLM)
    if have_pdf and fig_id:
        geo_bbox = geometric_match_bbox(pdf_path, page_idx, fig_id)
        if geo_bbox:
            tmp = out_path.with_name(f"{out_path.stem}.geo{out_path.suffix}")
            if render_bbox(pdf_path, page_idx, geo_bbox, tmp):
                ok, reasons = run_local_qc(tmp, caption, pdf_path, page_idx,
                                           geo_bbox)
                _track_qc(reasons)
                attempts.append({"src": "geometric_match", "pass": ok,
                                 "reasons": reasons, "bbox": geo_bbox})
                log_qc(book, ok, reasons, method="geometric_match")
                if ok:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp.replace(out_path)
                    if existing_path and existing_path != out_path and existing_path.exists():
                        try:
                            existing_path.unlink()
                            attempts[-1]["existing_superseded"] = True
                        except Exception:
                            pass
                    return {"pass": True, "file": str(out_path),
                            "attempts": attempts, "escalate": False,
                            "match_method": "geometric_match",
                            "fig_id_normalized": fig_norm,
                            "qc_degraded": bool(qc_skipped_acc),
                            "qc_skipped": list(qc_skipped_acc)}
                try:
                    tmp.unlink()
                except Exception:
                    pass

    # Deterministic contract: geometric_match is the ONLY permitted extractor.
    # If it did not produce a QC-passing crop, HARD-FAIL with a reason — never
    # fall through to raw_xref / VLM / existing-file (those are the paths that
    # silently produced wrong rasters). Steps 1-4 below are skipped entirely.
    if strict_geometric_only:
        if attempts and attempts[0].get("src") == "geometric_match":
            reason = ("geometric_match crop failed QC: "
                      + "; ".join(attempts[0].get("reasons", [])))
        elif not have_pdf:
            reason = "no pdf/page context for geometric match"
        else:
            reason = (f"no geometric caption↔raster match for fig_id={fig_norm} "
                      f"on page (caption absent or unowned raster)")
        log_qc(book, False, [f"STRICT_HARD_FAIL:{reason}"], method="FAIL")
        return {"pass": False, "file": None, "attempts": attempts,
                "escalate": False, "hard_fail": True,
                "match_method": "FAIL", "fig_id_normalized": fig_norm,
                "reason": reason,
                "qc_degraded": bool(qc_skipped_acc),
                "qc_skipped": list(qc_skipped_acc)}

    # Step 1: existing file (fallback when geometric match failed/unavailable)
    if existing_path and existing_path.exists():
        ok, reasons = run_local_qc(
            existing_path, caption, pdf_path, page_idx, existing_bbox
        )
        _track_qc(reasons)
        attempts.append({"src": "existing", "pass": ok, "reasons": reasons})
        log_qc(book, ok, reasons, method="existing")
        if ok:
            if existing_path != out_path:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(existing_path.read_bytes())
            return {"pass": True, "file": str(out_path), "attempts": attempts,
                    "escalate": False, "match_method": "existing",
                    "fig_id_normalized": fig_norm,
                    "qc_degraded": bool(qc_skipped_acc),
                    "qc_skipped": list(qc_skipped_acc)}
        # QC fail → delete existing
        try:
            existing_path.unlink()
            attempts[-1]["deleted"] = True
        except Exception as e:
            attempts[-1]["delete_err"] = str(e)

    if not have_pdf:
        return {"pass": False, "file": None, "attempts": attempts,
                "escalate": True, "err": "no_pdf_for_extract",
                "qc_degraded": bool(qc_skipped_acc),
                "qc_skipped": list(qc_skipped_acc)}

    # Step 2: raw page candidates
    cands = extract_raw_candidates(pdf_path, page_idx)
    for i, c in enumerate(cands[:4]):  # cap at 4 candidates per page
        tmp = out_path.with_name(f"{out_path.stem}.cand{i}{out_path.suffix}")
        # Ink-separation guard: raw stream extraction inverts /DeviceN or
        # /Separation images. Render the bbox region instead so PDF color
        # transforms (/Decode arrays) are applied.
        if xref_is_ink_separation(pdf_path, c["xref"]):
            if not render_bbox(pdf_path, page_idx, c["bbox"], tmp):
                continue
        else:
            if not extract_xref_image(pdf_path, c["xref"], tmp):
                continue
        ok, reasons = run_local_qc(tmp, caption, pdf_path, page_idx, c["bbox"])
        _track_qc(reasons)
        attempts.append({"src": f"raw_xref_{c['xref']}", "pass": ok,
                         "reasons": reasons, "bbox": c["bbox"]})
        log_qc(book, ok, reasons, method="raw_xref")
        if ok:
            tmp.replace(out_path)
            return {"pass": True, "file": str(out_path), "attempts": attempts,
                    "escalate": False, "match_method": "raw_xref",
                    "fig_id_normalized": fig_norm,
                    "qc_degraded": bool(qc_skipped_acc),
                    "qc_skipped": list(qc_skipped_acc)}
        try:
            tmp.unlink()
        except Exception:
            pass

    # Step 3: vision-guided retry
    for retry in range(RETRY_LIMIT):
        bbox = ollama_suggest_bbox(pdf_path, page_idx, caption)
        if not bbox:
            attempts.append({"src": f"ollama_retry_{retry}", "pass": False,
                             "reasons": ["ollama_no_bbox"]})
            continue
        tmp = out_path.with_name(f"{out_path.stem}.retry{retry}{out_path.suffix}")
        if not render_bbox(pdf_path, page_idx, bbox, tmp):
            attempts.append({"src": f"ollama_retry_{retry}", "pass": False,
                             "reasons": ["render_failed"], "bbox": bbox})
            continue
        ok, reasons = run_local_qc(tmp, caption, pdf_path, page_idx, bbox)
        _track_qc(reasons)
        attempts.append({"src": f"ollama_retry_{retry}", "pass": ok,
                         "reasons": reasons, "bbox": bbox})
        log_qc(book, ok, reasons, method="ollama_retry")
        if ok:
            tmp.replace(out_path)
            return {"pass": True, "file": str(out_path), "attempts": attempts,
                    "escalate": False, "match_method": "ollama_retry",
                    "fig_id_normalized": fig_norm,
                    "qc_degraded": bool(qc_skipped_acc),
                    "qc_skipped": list(qc_skipped_acc)}
        try:
            tmp.unlink()
        except Exception:
            pass

    # Step 4: escalate
    return {"pass": False, "file": None, "attempts": attempts, "escalate": True,
            "qc_degraded": bool(qc_skipped_acc),
            "qc_skipped": list(qc_skipped_acc)}


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Run QC on a single image file")
    p_check.add_argument("--file", required=True, type=Path)
    p_check.add_argument("--caption", default="")
    p_check.add_argument("--pdf", default=None)
    p_check.add_argument("--page", type=int, default=None, help="1-indexed")
    p_check.add_argument("--bbox", default=None,
                         help="x0,y0,x1,y1 in PDF coords")
    p_check.add_argument("--book", default=None, help="log under this book")

    p_gate = sub.add_parser("gate", help="Full gate: QC | extract | retry")
    p_gate.add_argument("--book", required=True)
    p_gate.add_argument("--fig-id", required=True)
    p_gate.add_argument("--caption", required=True)
    p_gate.add_argument("--out", required=True, type=Path)
    p_gate.add_argument("--existing", type=Path, default=None)
    p_gate.add_argument("--pdf", default=None)
    p_gate.add_argument("--page", type=int, default=None, help="1-indexed")
    p_gate.add_argument("--bbox", default=None,
                        help="existing bbox: x0,y0,x1,y1")
    p_gate.add_argument("--strict-geometric-only", "--strict_geometric_only",
                        dest="strict_geometric_only", action="store_true",
                        help="deterministic mode: geometric_match ONLY; hard-fail "
                             "(no raw_xref / VLM / existing fallback)")

    p_log = sub.add_parser("log-show", help="Show QC log + purge candidates")
    p_log.add_argument("--book", default=None)

    args = ap.parse_args()

    if args.cmd == "check":
        bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
        page_idx = args.page - 1 if args.page else None
        ok, reasons = run_local_qc(args.file, args.caption, args.pdf, page_idx, bbox)
        if args.book:
            entry = log_qc(args.book, ok, reasons)
            print(json.dumps({"pass": ok, "reasons": reasons, "log": entry},
                             indent=2, ensure_ascii=False))
        else:
            print(json.dumps({"pass": ok, "reasons": reasons},
                             indent=2, ensure_ascii=False))
        sys.exit(0 if ok else 1)

    if args.cmd == "gate":
        # Contract guard: the gate is the INTERNAL engine. The public entrypoint
        # figure_remap.py reaches it by importing gate() as a function, never via
        # this CLI — so blocking the CLI path forbids workflow bypass without
        # affecting the entrypoint. NOTE (honest limitation): Python cannot truly
        # prevent `import figure_qc_gate; gate()`; this blocks the convenient path
        # and makes any bypass explicit/detectable, not impossible.
        import os
        if os.environ.get("FIGURE_REMAP_ALLOW_GATE") != "1":
            print(json.dumps(
                {"error": "figure_qc_gate.py is the internal engine. "
                          "Use: figure_remap.py extract  "
                          "(engine debugging only: FIGURE_REMAP_ALLOW_GATE=1)"},
                ensure_ascii=False), file=sys.stderr)
            sys.exit(2)
        bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
        page_idx = args.page - 1 if args.page else None
        result = gate(args.book, args.fig_id, args.caption, args.out,
                      existing_path=args.existing, pdf_path=args.pdf,
                      page_idx=page_idx, existing_bbox=bbox,
                      strict_geometric_only=args.strict_geometric_only)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0 if result["pass"] else 2)

    if args.cmd == "log-show":
        if not QC_LOG_PATH.exists():
            print("{}")
            return
        log = json.loads(QC_LOG_PATH.read_text(encoding="utf-8"))
        if args.book:
            log = {args.book: log.get(args.book, {})}
        out = {}
        for book, e in log.items():
            purge = (e.get("checked", 0) >= PURGE_CHECKED_MIN
                     and e.get("fail_rate", 0) > PURGE_FAIL_RATE)
            out[book] = {**e, "purge_recommended": purge}
        print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
