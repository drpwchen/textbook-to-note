"""LEGACY — visual sanity check via local ollama (minicpm-v:8b). 0 Claude tokens.

Only the legacy whole-book batch tools (batch_remap.py, visual_check_batch.py)
use this. The note-writing workflow does not: every crop is read and classified
by a frontier-tier model before embedding (workflows/note-writing.md,
"classify every crop"), which supersedes this sampling check.

For a sample of N figures from a freshly-extracted book, ask minicpm-v to
describe the image, then compare loosely against the caption text. If the
description's modality / anatomy keywords disagree with the caption, mark
as a potential mismatch.
"""
import base64
import json
import os
import random
import re
import urllib.request
from pathlib import Path

OLLAMA = os.environ.get("T2N_OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = "minicpm-v:8b"  # vision model on /api/generate; replaced gemma4:e4b (thinking model returned empty on /api/generate)

# Modality keyword groups — overlap = compatible, no overlap = suspicious
MODALITY_TAGS = {
    "xray": ["radiograph", "x-ray", "ap view", "anteroposterior", "lateral", "dunn"],
    "mri": ["mri", "magnetic resonance", "t1", "t2", "fat-suppressed", "weighted"],
    "ct": ["ct", "computed tomography", "3d reconstruction", "axial slice"],
    "us": ["ultrasound", "sonographic", "doppler"],
    "arthroscopy": ["arthroscopic", "intraoperative", "endoscopic"],
    "clinical": ["clinical photo", "patient", "physical exam", "maneuver", "demonstrates"],
    "diagram": ["diagram", "illustration", "schematic"],
}


def _post(path, payload, timeout=300):
    req = urllib.request.Request(
        OLLAMA + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _img_b64_rgb(path: Path) -> str:
    """base64 PNG forced to RGB — minicpm-v:8b returns HTTP 500 on CMYK / L-mode."""
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
        return base64.b64encode(Path(path).read_bytes()).decode()


def describe_image(png_path: Path) -> str:
    img_b64 = _img_b64_rgb(png_path)
    prompt = (
        "Describe this medical figure in 1-2 sentences. Focus on: "
        "imaging modality (X-ray / MRI / CT / ultrasound / arthroscopy / "
        "clinical photo / diagram), anatomical region, key visible findings. "
        "Be concrete. No preamble."
    )
    resp = _post("/api/generate", {
        "model": MODEL,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "options": {"num_predict": 800, "temperature": 0.2},
    })
    return resp.get("response", "").strip()


def tag_modalities(text: str) -> set:
    tl = (text or "").lower()
    tags = set()
    for tag, kws in MODALITY_TAGS.items():
        if any(kw in tl for kw in kws):
            tags.add(tag)
    return tags


def _ollama_alive(timeout=2) -> bool:
    """Short ping to avoid hanging the batch when ollama is down or busy."""
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


def sample_check(figures: list, fig_dir: Path, n: int = 3) -> dict:
    """Pick `n` figures from the manifest and verify each via minicpm-v. Returns
    {checked: [...], passes, fails, suspicious: [details]}.
    """
    if not figures:
        return {"checked": [], "passes": 0, "fails": 0, "suspicious": []}
    if not _ollama_alive():
        return {"checked": [], "passes": 0, "fails": 0, "suspicious": [],
                "skipped": "ollama_unreachable"}

    # Stratified sample: prefer one single-image, one multi-part, one late-chapter
    singles = [f for f in figures if "region_clip" not in (f.get("render", ""))]
    multi   = [f for f in figures if "region_clip" in (f.get("render", ""))]
    pool = []
    if singles:
        pool.append(random.choice(singles))
    if multi:
        pool.append(random.choice(multi))
    # Late-chapter pick
    if figures:
        try:
            late = max(figures, key=lambda f: float(f["fig_id"]))
            if late not in pool:
                pool.append(late)
        except Exception:
            pass
    while len(pool) < n and len(pool) < len(figures):
        c = random.choice(figures)
        if c not in pool:
            pool.append(c)

    results = {"checked": [], "passes": 0, "fails": 0, "suspicious": []}
    for f in pool[:n]:
        path = fig_dir / f["file"]
        if not path.exists():
            continue
        try:
            desc = describe_image(path)
        except Exception as e:
            desc = f"[ERROR] {e}"
        cap_tags = tag_modalities(f.get("caption", ""))
        desc_tags = tag_modalities(desc)
        compatible = bool(cap_tags & desc_tags) or not cap_tags or not desc_tags
        item = {
            "file": f["file"],
            "fig_id": f["fig_id"],
            "caption": f.get("caption", "")[:120],
            "description": desc[:200],
            "caption_tags": sorted(cap_tags),
            "desc_tags": sorted(desc_tags),
            "compatible": compatible,
        }
        results["checked"].append(item)
        if compatible:
            results["passes"] += 1
        else:
            results["fails"] += 1
            results["suspicious"].append(item)
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: visual_check.py <fig_dir>")
        sys.exit(1)
    fig_dir = Path(sys.argv[1])
    manifest = fig_dir / "figures_manifest.json"
    data = json.load(open(manifest, encoding="utf-8"))
    figs = data.get("figures", [])
    out = sample_check(figs, fig_dir, n=3)
    print(json.dumps(out, indent=2, ensure_ascii=False))
