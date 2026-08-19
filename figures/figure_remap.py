"""figure_remap.py — PUBLIC ENTRYPOINT (contract layer) for the figure-remap pipeline.

This is the ONLY sanctioned way a workflow (textbook-to-note, note-supplement,
note-cleanup, …) asks for a figure. Workflows MUST call `extract()` / the
`extract` CLI here and MUST NOT call `figure_qc_gate.py` (the implementation)
directly.

Why a contract layer:
  - The workflow should see a stable, minimal contract — never the gate's
    internal shape (attempts/bbox/fallback ladder). That keeps logging uniform
    and lets policy (strict-by-default, fallback rules) be enforced in ONE place
    instead of being re-decided at every call site.
  - Implementation (geometric_match, strict mode, fallback policy, QC chain)
    lives behind this boundary in figure_qc_gate.py and may change freely as long
    as this contract holds.

Contract returned to the workflow (and ONLY this):
  {
    "status": "pass" | "fail" | "escalate",
    "match_quality": "exact" | "uncertain" | "failed",  # NOT the impl method —
                    # 'exact' = deterministic geometric match; 'uncertain' = a
                    # fallback crop (only reachable with strict=False); 'failed' =
                    # no crop. Workflows branch on this, never on the engine method.
    "hard_fail": bool,            # strict deterministic miss (never a wrong raster)
    "file": "<path>" | None,      # set only when status == pass
    "fig_id": "<normalized>",     # canonical dot id actually matched on
    "reason": "<text>",           # set on fail/escalate
    "qc_degraded": bool,          # true when a QC check had to skip (PIL/numpy
                                   # missing, no PDF context, ollama unreachable)
                                   # and defaulted to pass instead of a real verdict
    "qc_skipped": ["<check>", ...]  # which check(s) skipped; [] when qc_degraded is False
  }

The precise engine method (geometric_match / raw_xref / ollama_retry / existing /
FAIL) is recorded ONLY in the per-book QC log — it is deliberately NOT in the
contract, so callers cannot grow a dependency on the fallback ladder's existence.

  status semantics:
    pass     → `file` is a QC-passed crop; embed it.
    fail     → deterministic miss (strict hard_fail). NOT a wrong figure — a
               correct refusal. Caller fixes --page, escalates to vision,
               or leaves a TODO.
             → EXCEPT when reason starts with "pregate=": the crop extracted
               and passed QC fine, but the metadata pre-gate (pregate.py)
               identified it as a non-figure (chapter-banner geometry / blank
               crop). SKIP this fig_id — do not retry, do not fix --page, do
               not leave a retry TODO. hard_fail is False for this case.
    escalate → only reachable in non-strict mode; gate exhausted its ladder and
               wants a frontier-model vision read (Read page → re-call with a bbox).

Default policy: strict=True (deterministic contract). Pass strict=False only to
deliberately opt into the best-effort fallback ladder (raw_xref / VLM), which can
produce a plausible-but-wrong crop — discouraged for note-writing.

CLI:
  python figure_remap.py extract --book B --fig-id X.Y --caption "..." \
      --out path --pdf file.pdf --page N [--no-strict] [--existing p]
  exit 0 = pass · 1 = fail/hard_fail · 2 = escalate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The implementation lives in the sibling module; import it rather than shell out.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import figure_qc_gate as _impl  # noqa: E402

_VALID_SOURCES = frozenset({"auto", "pdf", "existing"})


def _resolve_book_figure(book: str, fig_id: str) -> Path | None:
    """Locate a pre-extracted figure under the textbook-md convention:
      <BOOKS_MD_BASE>/<book>/figures/Fig_<dash_id>.{jpeg,png,jpg}

    The batch extractor produced these from the same deterministic
    caption-anchor logic the live extractor uses, so reusing a QC-passing
    pre-extract is structurally equivalent to re-running geometric match —
    and it sidesteps the failure mode where geometric_match silently claims
    a text-strip raster as the figure's owned raster on the requested page
    (observed in production on a multi-column layout: a caption-adjacent
    text block was closer to the target caption than the actual raster).
    """
    if not book or not fig_id:
        return None
    norm = _impl.normalize_fig_id(fig_id)
    dash_id = norm.replace(".", "-")
    book_dir = _impl.TEXTBOOK_MD_BASE / book / "figures"
    for ext in (".jpeg", ".png", ".jpg"):
        candidate = book_dir / f"Fig_{dash_id}{ext}"
        if candidate.exists():
            return candidate
    return None

# Multi-panel caption detector. Strict geometric_match assumes one caption owns
# one raster; composite multi-panel figures (caption references (A) (B) (C) ...)
# violate that ownership model — the panels are subsemantic regions of a single
# layout, not independently owned rasters. This is a capability mismatch, not a
# bug to retry. When detected, we route to relaxed mode up front (no wasted
# strict attempt). Require ≥2 distinct panel refs so single-panel captions that
# happen to mention "(A)" don't trip it.
_MULTIPANEL_PANEL_RE = re.compile(r'\(\s*([A-Ha-h])\s*\)|\bpanel\s+([A-Ha-h])\b')


def _detect_multipanel_caption(caption: str) -> bool:
    panels = set()
    for m in _MULTIPANEL_PANEL_RE.finditer(caption or ""):
        letter = (m.group(1) or m.group(2) or "").upper()
        if letter:
            panels.add(letter)
    return len(panels) >= 2

# Executable spec for the contract. _validate() runs on EVERY extract() return
# (single exit point), so all branches — pass / hard_fail / escalate / no-pdf /
# generic-fail — are guaranteed to conform, including the ones test fixtures don't
# exercise. Any key not in CONTRACT_KEYS is rejected, which structurally forbids
# leaking the engine axis (match_method/attempts/bbox/escalate/pass).
CONTRACT_KEYS = frozenset({"status", "match_quality", "hard_fail",
                           "file", "fig_id", "reason",
                           "qc_degraded", "qc_skipped"})
_STATUS = frozenset({"pass", "fail", "escalate"})
_QUALITY = frozenset({"exact", "uncertain", "failed"})


def _validate(c: dict) -> dict:
    """Raise on any contract violation; return c unchanged when valid."""
    extra = set(c) - CONTRACT_KEYS
    missing = CONTRACT_KEYS - set(c)
    if extra or missing:
        raise RuntimeError(
            f"figure-remap contract violation: extra={extra} missing={missing}")
    if c["status"] not in _STATUS:
        raise RuntimeError(f"contract violation: bad status {c['status']!r}")
    if c["match_quality"] not in _QUALITY:
        raise RuntimeError(
            f"contract violation: bad match_quality {c['match_quality']!r}")
    if not isinstance(c["hard_fail"], bool):
        raise RuntimeError("contract violation: hard_fail must be bool")
    if not isinstance(c["qc_degraded"], bool):
        raise RuntimeError("contract violation: qc_degraded must be bool")
    if not isinstance(c["qc_skipped"], list):
        raise RuntimeError("contract violation: qc_skipped must be a list")
    return c


def _pregate_kill(pdf: str | None, page_idx: int | None, raw: dict) -> str | None:
    """Run the metadata pre-gate on a gate-passing crop. Returns the kill reason
    ("banner" / "blank") or None. Needs the winning attempt's bbox; without pdf,
    page, or bbox (existing-file fast path, scanned caption_anchor backend,
    mocked gate) it abstains. Fail-open
    by design: pregate is a junk filter, never a correctness gate — an exception
    here must never block an otherwise QC-passed extraction."""
    try:
        if pdf is None or page_idx is None:
            return None
        bbox = None
        for a in reversed(raw.get("attempts", [])):
            if a.get("pass") and a.get("bbox"):
                bbox = a["bbox"]
                break
        if bbox is None:
            return None
        from pregate import compute_features, verdict
        feat = compute_features(pdf, page_idx, bbox, raw.get("file"))
        action, reason = verdict(feat)
        return reason if action == "kill" else None
    except Exception:
        return None


def extract(book: str, fig_id: str, caption: str, out: str | Path,
            pdf: str | None = None, page: int | None = None,
            existing: str | Path | None = None,
            strict: bool = True,
            source: str = "auto") -> dict:
    """Public contract entrypoint. `page` is 1-indexed (CLI convention).

    Returns the minimal contract dict documented in the module docstring — never
    the gate's internal result shape.

    `source` selects where the figure comes from:
      - "auto" (default): try the pre-extracted file under
        <book>/figures/Fig_<id>.{jpeg,png,jpg} as an authoritative cached
        result; fall through to PDF (strict geometric_match + ±1 sweep) on
        miss or QC fail. Explicit `existing` overrides the auto-resolved path.
      - "pdf": skip the existing fast-path entirely — always re-extract from
        PDF (legacy behaviour; use when you want to validate a code change or
        suspect the pre-extracted file is contaminated).
      - "existing": only the existing file is honored; if missing or QC fail,
        hard_fail (use for batch-trust mode after you've validated extraction
        offline).
    """
    if source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {_VALID_SOURCES}, got {source!r}")

    out_path = Path(out)
    page_idx = (page - 1) if page else None
    existing_path = Path(existing) if existing else None

    # source=auto with no explicit --existing: try the book-convention path.
    # source=pdf: ignore even a passed --existing (force re-extract).
    if source == "pdf":
        existing_path = None
    elif source == "auto" and existing_path is None:
        existing_path = _resolve_book_figure(book, fig_id)

    # Existing-file fast path. Runs BEFORE strict geometric_match because a
    # QC-passing pre-extract is at least as deterministic as recomputing — the
    # batch extractor uses the same caption-anchor logic. Skipping this is
    # what let geometric_match silently claim the wrong raster in the
    # production incident referenced above.
    if source in ("auto", "existing") and existing_path and existing_path.exists():
        # bbox=None: pre-extract isn't tied to a known page bbox, so text_bleed
        # check is skipped — whitespace + contamination still apply.
        ok, reasons = _impl.run_local_qc(existing_path, caption, pdf, page_idx, None)
        degraded, skipped = _impl.qc_degradation(reasons)
        if ok:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if existing_path != out_path:
                out_path.write_bytes(existing_path.read_bytes())
            _impl.log_qc(book, True, ["existing_authoritative:" + ";".join(reasons)],
                         method="existing_authoritative")
            fig_norm = _impl.normalize_fig_id(fig_id or "")
            return _validate({"status": "pass", "match_quality": "exact",
                              "hard_fail": False, "file": str(out_path),
                              "fig_id": fig_norm,
                              "reason": f"source=existing_authoritative ({existing_path.name})",
                              "qc_degraded": degraded, "qc_skipped": skipped})
        if source == "existing":
            fig_norm = _impl.normalize_fig_id(fig_id or "")
            return _validate({"status": "fail", "match_quality": "failed",
                              "hard_fail": True, "file": None, "fig_id": fig_norm,
                              "reason": f"source=existing QC fail: {'; '.join(reasons)}",
                              "qc_degraded": degraded, "qc_skipped": skipped})
        # source=auto + existing QC fail → fall through to PDF path.
    elif source == "existing":
        fig_norm = _impl.normalize_fig_id(fig_id or "")
        return _validate({"status": "fail", "match_quality": "failed",
                          "hard_fail": True, "file": None, "fig_id": fig_norm,
                          "reason": "source=existing but no pre-extracted file found",
                          "qc_degraded": False, "qc_skipped": []})

    # Policy-level routing: multi-panel captions break strict ownership model.
    # Auto-relax (no special-case fallback — this is correct routing, not retry).
    policy_reason = ""
    if strict and _detect_multipanel_caption(caption):
        strict = False
        policy_reason = "multipanel_caption_relaxed"

    raw = _impl.gate(
        book=book, fig_id=fig_id, caption=caption, out_path=out_path,
        existing_path=existing_path, pdf_path=pdf, page_idx=page_idx,
        strict_geometric_only=strict,
    )

    # Neighbor-page sweep on strict hard_fail. Caption/figure cross-page offsets
    # are common (some publishers' layouts put a caption on page N while the
    # raster sits on N±1). A single-page strict match can't recover this; ±1
    # sweep covers the vast majority of cases without expanding scope enough
    # to risk grabbing the wrong figure. Skip `existing` for neighbors — the
    # fast-path image is tied to the original page assertion.
    neighbor_tries: list[tuple[int, str]] = []
    matched_page_idx = page_idx  # page the winning gate run actually used
    if (strict and page_idx is not None and pdf is not None
            and not raw.get("pass") and raw.get("hard_fail")):
        for delta in (-1, 1):
            np_idx = page_idx + delta
            if np_idx < 0:
                continue
            alt = _impl.gate(
                book=book, fig_id=fig_id, caption=caption, out_path=out_path,
                existing_path=None, pdf_path=pdf, page_idx=np_idx,
                strict_geometric_only=strict,
            )
            neighbor_tries.append((np_idx + 1, "pass" if alt.get("pass") else "fail"))
            if alt.get("pass"):
                raw = alt
                matched_page_idx = np_idx
                break
        if not raw.get("pass") and neighbor_tries:
            base = raw.get("reason") or "strict deterministic miss"
            raw["reason"] = f"{base}; neighbor sweep tried pages {neighbor_tries}"

    fig_norm = raw.get("fig_id_normalized") or _impl.normalize_fig_id(fig_id or "")
    qc_degraded = bool(raw.get("qc_degraded", False))
    qc_skipped = list(raw.get("qc_skipped", []))
    if raw.get("pass"):
        # Metadata pre-gate (pregate.py): deterministic kill of chapter-banner
        # geometry and blank crops before anything downstream sees the crop.
        # Calibrated with zero embed/callout false kills (see pregate.py);
        # fail-open, so it can never turn into a new source of missing figures
        # through its own errors.
        kill = _pregate_kill(pdf, matched_page_idx, raw)
        if kill:
            try:  # the crop is a regenerable build artifact — direct delete ok
                Path(raw["file"]).unlink()
            except Exception:
                pass
            _impl.log_qc(book, False, [f"PREGATE_KILL:{kill}"], method="pregate")
            contract = {"status": "fail", "match_quality": "failed",
                        "hard_fail": False, "file": None, "fig_id": fig_norm,
                        "reason": (f"pregate={kill}: crop is not an embeddable "
                                   f"figure — skip this fig_id, do not retry"),
                        "qc_degraded": qc_degraded, "qc_skipped": qc_skipped}
            if policy_reason:
                contract["reason"] += f"; policy={policy_reason}"
            return _validate(contract)
        # exact = any deterministic backend (geometric_match OR caption_anchor,
        # both produce ambiguity-safe crops from a deterministic anchor).
        # Anything else is a heuristic fallback crop (only reachable with
        # strict=False) → 'uncertain'.
        method = raw.get("match_method")
        quality = "exact" if method in ("geometric_match", "caption_anchor") else "uncertain"
        contract = {"status": "pass", "match_quality": quality, "hard_fail": False,
                    "file": raw.get("file"), "fig_id": fig_norm, "reason": "",
                    "qc_degraded": qc_degraded, "qc_skipped": qc_skipped}
    elif raw.get("hard_fail"):
        contract = {"status": "fail", "match_quality": "failed", "hard_fail": True,
                    "file": None, "fig_id": fig_norm,
                    "reason": raw.get("reason", "strict deterministic miss"),
                    "qc_degraded": qc_degraded, "qc_skipped": qc_skipped}
    elif raw.get("escalate"):
        contract = {"status": "escalate", "match_quality": "failed", "hard_fail": False,
                    "file": None, "fig_id": fig_norm,
                    "reason": raw.get("err") or "gate exhausted; needs frontier-model vision bbox",
                    "qc_degraded": qc_degraded, "qc_skipped": qc_skipped}
    else:
        contract = {"status": "fail", "match_quality": "failed", "hard_fail": False,
                    "file": None, "fig_id": fig_norm,
                    "reason": raw.get("err", "extraction failed"),
                    "qc_degraded": qc_degraded, "qc_skipped": qc_skipped}

    # Surface policy routing in the reason field for audit trail. Workflows can
    # ignore; per-book QC log retains full provenance.
    if policy_reason:
        existing = contract["reason"]
        contract["reason"] = f"{existing}; policy={policy_reason}" if existing else f"policy={policy_reason}"

    return _validate(contract)  # single exit: every branch is shape-checked


def extract_page_scanned(book: str, pdf: str, page: int, out_dir: str | Path,
                         fig_id_to_name: dict[str, str] | None = None,
                         dpi: int = 200) -> list[dict]:
    """Performance helper for scanned-PDF batch extraction on one page.

    NOT part of the public contract — this is a render-cache-aware loop that
    calls `extract()` once per fig_id discovered (or requested) on the page.
    Each returned dict is a full single-figure contract (validated through
    `_validate`). Workflows can replace this with N `extract()` calls without
    any behavioral difference — only speed.

    Args:
      fig_id_to_name: if provided, only extract these fig_ids and use the
        given output filenames. If None, discover all caption fig_ids on the
        page via figure_scanned.analyze_page() and use auto-generated names.

    Returns one contract dict per fig_id processed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    page_idx = page - 1

    # Discover captions if no explicit list given
    if fig_id_to_name is None:
        import figure_scanned as _scan  # noqa: E402
        scratch_out = out_dir / "_probe.png"
        derived = _scan.analyze_page(pdf, page_idx, scratch_out, dpi=dpi)
        fig_id_to_name = {
            next(iter(c.aliases)): f"fig_{next(iter(c.aliases)).replace('.', '-')}.png"
            for c in derived.captions
        }

    results: list[dict] = []
    for fig_id, fname in fig_id_to_name.items():
        out = out_dir / fname
        # Use empty caption — caption-anchor uses caption-text only for
        # anatomy/sono threshold heuristic; if unknown, default tighten works.
        results.append(extract(book=book, fig_id=fig_id, caption="",
                               out=out, pdf=pdf, page=page, strict=True))
    return results


def main():
    ap = argparse.ArgumentParser(
        description="figure-remap public entrypoint (contract layer)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract", help="extract one figure (contract output)")
    p.add_argument("--book", required=True)
    p.add_argument("--fig-id", required=True)
    p.add_argument("--caption", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--pdf", default=None)
    p.add_argument("--page", type=int, default=None, help="1-indexed")
    p.add_argument("--existing", default=None)
    p.add_argument("--source", choices=sorted(_VALID_SOURCES), default="auto",
                   help="auto (default): use <book>/figures/Fig_<id>.* as "
                        "authoritative cache, fall through to PDF on miss/QC "
                        "fail. pdf: skip the cache, always re-extract. "
                        "existing: cache-only, hard_fail if missing.")
    # strict is the default contract; --no-strict opts into the fallback ladder.
    p.add_argument("--no-strict", dest="strict", action="store_false",
                   help="opt into best-effort fallback (raw_xref/VLM); discouraged")
    p.set_defaults(strict=True)

    pp = sub.add_parser("extract-page",
                        help="batch helper: extract ALL figures on one scanned page "
                             "(performance shortcut, NOT a contract; behaves as N "
                             "independent extract() calls)")
    pp.add_argument("--book", required=True)
    pp.add_argument("--pdf", required=True)
    pp.add_argument("--page", type=int, required=True, help="1-indexed")
    pp.add_argument("--out-dir", required=True)
    pp.add_argument("--fig-ids", default=None,
                    help="comma-separated fig_ids to extract; if omitted, "
                         "discovers all captions on the page")
    pp.add_argument("--name-template", default="fig_{fig_id}.png",
                    help="output filename template; {fig_id} is replaced")

    args = ap.parse_args()
    if args.cmd == "extract":
        result = extract(book=args.book, fig_id=args.fig_id, caption=args.caption,
                         out=args.out, pdf=args.pdf, page=args.page,
                         existing=args.existing, strict=args.strict,
                         source=args.source)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit({"pass": 0, "fail": 1, "escalate": 2}[result["status"]])

    if args.cmd == "extract-page":
        if args.fig_ids:
            name_map = {fid.strip(): args.name_template.format(fig_id=fid.strip().replace(".", "-").replace("/", "-"))
                        for fid in args.fig_ids.split(",")}
        else:
            name_map = None
        results = extract_page_scanned(
            book=args.book, pdf=args.pdf, page=args.page,
            out_dir=args.out_dir, fig_id_to_name=name_map,
        )
        # JSONL — one line per fig_id
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        # Exit 0 if any passed, else 1 (overall summary)
        sys.exit(0 if any(r["status"] == "pass" for r in results) else 1)


if __name__ == "__main__":
    main()
