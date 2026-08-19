"""pregate.py — deterministic metadata pre-gate for extracted figure crops.

Kills obvious non-figures (chapter title banners, blank crops) BEFORE anything
downstream — a classifier, a note writer, a human reviewer — ever sees them,
using only what fitz knows at crop time: bbox geometry, char density,
raster/vector coverage. A kill means "skip this crop entirely — do not embed,
do not retry"; anything uncertain passes through untouched.

Why a separate stage rather than one more QC check: `figure_qc_gate` asks "is
this crop the figure the caption names?" — a correctness question, where a
wrong answer is a wrong figure in a note. This asks "is this crop a figure at
all?" — a junk question, where a wrong answer is a missing figure. The two
have opposite failure costs and opposite tuning pressure, so they get separate
thresholds and separate calibration data.

Calibration (internal figure-classification evaluation, 2026-08-19; thresholds
fitted on a dev set of n=301 crops with frozen reference labels, then measured
unchanged on a later held-out set of n=244):

  - hard requirement: zero kills on crops whose reference label says embed or
    callout. A killed good figure has no downstream rescue, so a false kill is
    strictly worse than a passed banner. Measured: 0 on both sets.
  - banner rule margin: killed text banners had n_drawings <= 18 on dev and
    <= 19 on the held-out set; the embed/callout figures that this geometry
    would otherwise have caught had n_drawings >= 72. The threshold of 40 sits
    in that empty band rather than next to either side.
  - banner recall: 42/45 on the dev set, 50/50 on the held-out set.
  - known cost: crops that are a damaged figure sitting under a full-width
    chapter banner get killed too (6 per set). Those fig_ids go un-embedded,
    which is the accepted trade for the zero-false-kill requirement.

A TABLE kill rule (find_tables coverage + char density) was tried and
REJECTED. fitz `find_tables` fires on flowcharts and designed figures: on the
held-out set every threshold combination killed crops labelled embed/callout,
three of them flowcharts, and no metadata feature separated those from real
tables. Raster-image tables are invisible to `find_tables` anyway
(image_cover 1.0, table cover 0.0), so a rule tuned to catch them cannot even
see the case it was meant for. Tables stay a job for the downstream
classifier — on the held-out set that classifier let 1 junk crop of 100
through, and the one table it missed was exactly such a raster-image table.
Do not resurrect the table rule without a NEW separating feature, calibrated
on dev.

Do NOT tune these thresholds against fresh acceptance data — refitting on the
set you then report numbers from turns a measurement into a claim. Recalibrate
on dev only.

Public API:
  compute_features(pdf_path, page_index0, bbox, image_path=None) -> dict
  verdict(features) -> (action, reason)   # action in {"pass", "kill"}
  check(pdf_path, page_1idx, bbox, image_path=None) -> dict  # CLI-shaped

CLI:
  python pregate.py --pdf X.pdf --page 119 --bbox x0,y0,x1,y1 [--image crop.jpeg]
"""
from __future__ import annotations

import json
from pathlib import Path

THRESHOLDS = {
    "blank_std": 3.0,        # frozen: pixel std below this is a blank crop
    "banner_fy0_max": 0.02,
    "banner_fy1_max": 0.36,
    "banner_w_min": 0.95,
    "banner_ndraw_max": 40,  # >= this many vector paths = real vector figure
}


def compute_features(pdf_path: str, page_index0: int, bbox,
                     image_path: str | None = None) -> dict:
    """Metadata features for one crop. bbox in PDF points, page_index0 0-based.

    Every optional feature degrades to a neutral value rather than raising:
    a page whose drawings or images cannot be read yields 0, and px_std stays
    None when PIL/numpy are missing or the crop file is unreadable. `verdict`
    treats None/0 as "no evidence to kill on".
    """
    import fitz
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index0]
        pw, ph = page.rect.width, page.rect.height
        r = fitz.Rect(bbox)
        area = max(r.get_area(), 1e-6)
        covers = []
        try:
            for t in page.find_tables().tables:
                inter = fitz.Rect(r)
                inter.intersect(fitz.Rect(t.bbox))
                covers.append(inter.get_area() / area if not inter.is_empty else 0.0)
        except Exception:
            pass
        txt = page.get_text("text", clip=r)
        chars = sum(1 for c in txt if not c.isspace())
        img_cover = 0.0
        try:
            for im in page.get_image_info():
                bb = im.get("bbox")
                if not bb:
                    continue
                inter = fitz.Rect(r)
                inter.intersect(fitz.Rect(bb))
                if not inter.is_empty:
                    img_cover += inter.get_area() / area
        except Exception:
            pass
        n_draw = 0
        try:
            for d in page.get_drawings():
                db = d.get("rect")
                if db is None:
                    continue
                inter = fitz.Rect(r)
                inter.intersect(fitz.Rect(db))
                if not inter.is_empty and inter.get_area() > 0:
                    n_draw += 1
        except Exception:
            pass
        feat = {
            "fy0": r.y0 / ph, "fy1": r.y1 / ph,
            "w_frac": r.width / pw, "h_frac": r.height / ph,
            "table_cover_max": max(covers) if covers else 0.0,
            "char_density": chars / (area / 1000.0),
            "image_cover": min(img_cover, 1.0),
            "n_drawings": n_draw,
            "px_std": None,
        }
    finally:
        doc.close()
    if image_path:
        try:
            from PIL import Image
            import numpy as np
            im = Image.open(image_path).convert("L")
            feat["px_std"] = float(np.asarray(im, dtype=float).std())
        except Exception:
            pass
    return feat


def verdict(f: dict) -> tuple[str, str | None]:
    """(action, reason). action "kill" = discard crop; "pass" = let it through.

    `table_cover_max`, `char_density` and `image_cover` are computed but
    deliberately unused — they are the descriptive record of the rejected
    table rule, kept so a future recalibration starts from measured features
    instead of re-deriving them.
    """
    t = THRESHOLDS
    std = f.get("px_std")
    if std is not None and std < t["blank_std"]:
        return "kill", "blank"
    if (f.get("fy0") is not None
            and f["fy0"] <= t["banner_fy0_max"]
            and f["fy1"] <= t["banner_fy1_max"]
            and f["w_frac"] >= t["banner_w_min"]
            and f["n_drawings"] < t["banner_ndraw_max"]):
        return "kill", "banner"
    return "pass", None


def check(pdf_path: str, page_1idx: int, bbox,
          image_path: str | None = None) -> dict:
    feat = compute_features(pdf_path, page_1idx - 1, bbox, image_path)
    action, reason = verdict(feat)
    return {"action": action, "reason": reason, "features": feat}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="deterministic metadata pre-gate")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True, help="1-indexed page")
    ap.add_argument("--bbox", required=True, help="x0,y0,x1,y1 in PDF points")
    ap.add_argument("--image", help="crop image file (enables blank check)")
    a = ap.parse_args()
    bbox = tuple(float(x) for x in a.bbox.split(","))
    print(json.dumps(check(a.pdf, a.page, bbox, a.image), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
