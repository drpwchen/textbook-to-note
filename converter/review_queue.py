"""review_queue.py — deterministic trigger for the out-of-band table-review pass.

## Why this exists

The QC gate in `docling_tables.py` (ragged_row, empty_first_cell, run_together,
multi_value, single_column, content_retention) keys off STRUCTURE and MISSING
content. It cannot see a *misbinding*: a value merged into the wrong row of an
otherwise well-formed table. The grid has the right shape, no empty cells, no
ragged rows, nothing missing — one value is just attached to the wrong label. A
reader (human or an LLM writing notes) then attributes it to the wrong entity.

Worst case measured: on a continuation page, the first data row is an orphan whose
drug name lived on the *previous* page; the extractor fuses that orphan's dose into
the next labelled row, so a corticosteroid's dose is reported as an anxiolytic's.
No deterministic predicate separates a correct binding from a misbound one — the
information needed is semantic. So this is deliberately NOT gated in code.

## What this module does

It flags the HIGH-RISK SUBSET for a second-opinion review pass. It NEVER rejects,
NEVER auto-fixes, and NEVER touches table content — it only annotates. Two triggers,
INDEPENDENT — either one alone flags a table (over-inclusive on purpose; a false flag
costs one review, a missed misbinding ships wrong clinical data):

  1. continuation pages — the second+ page of a table broken across a page break,
     where the orphan-fusion failure lives. Signalled by a "(continued)" / ", Continued"
     marker on the page; by a table the merger stitched across pages (merge path); or by
     page geometry alone — the previous page's table runs to the bottom, this one resumes
     at the top, with the same column count and column x-edges (convert.py's
     `geometric_continuation()`).

     The geometric signal is why a table can be flagged with no caption anywhere: most
     textbooks break a table across a page without printing "(continued)" at all, and a
     categorical table (ASA physical status, NYHA class) carries no dose token to trip
     trigger 2 either — so before it existed, that whole class of table was flagged by
     nothing (issue #14).
  2. dosage / numeric-threshold tables — tables whose cells carry drug-dose or
     cut-off tokens (mg, mL, mg/kg, IU, mEq/L, mmHg, dose ranges). A misbound value
     here is the highest-severity output the pipeline can produce.

A flagged table gets a `<!-- ⚠️ table needs out-of-band review: ... -->` marker in
the markdown (see `format_review_marker`) and is counted in the returned stats so a
batch run can emit a review manifest. The actual second opinion is an EXTERNAL step
documented in `docs/table-review.md` (a fast text model first, a vision model as the
escalation) — this module is only the deterministic trigger that decides *which*
tables enter that queue.

## Evidence

A pilot over the continuation×dose intersection (71 tables) found ~17-20% carried a
high-severity misbinding a downstream LLM would cite as clean data — versus ~0 in a
random table sample. The danger concentrates exactly where both triggers meet, which
is why the queue keys on these two signals. See `docs/table-review.md`.

## Kill-switch

    T2N_REVIEW_QUEUE=1  -> emit review markers + queue entries (default OFF).

Default OFF because it adds annotations to the output (a new capability, following
T2N_TABLE_MERGE's opt-in convention, not T2N_TABLE_FRAME_REJECT's default-ON one which
corrects already-wrong output). Recommended ON for clinical / drug-dosing corpora.
"""
from __future__ import annotations

import os
import re

# Drug-dose and numeric-threshold tokens. A number immediately followed by a
# dose/threshold unit. Ordered longest-first inside each alternation so "mg/kg"
# wins over "mg". Case-insensitive; \b keeps "mg" from matching inside words.
_DOSE_RE = re.compile(
    r"\b\d[\d.,]*\s*(?:"
    r"mg/kg(?:/h)?|mg/m2|mg/d|mg/mL|mcg|µg|mg|"          # mass doses
    r"IU|units?|mmol|mEq/L|"                              # activity / molar
    r"mL|"                                                # volume
    r"mmHg|g/dL|U/L"                                      # thresholds / lab units
    r")\b",
    re.I,
)

# A continuation marker: "(continued)", ", Continued", "cont'd". Over-inclusive by
# design — it fires on both the first page (caption ends "(Continued)") and the
# continuation page (caption ", Continued"); both mean a multi-page table is in play
# and the continuation page's first row is at risk.
_CONTINUATION_RE = re.compile(r"(?i)\b(?:continued|cont[’'`]?d)\b")

# How many dose tokens make a table a "dosage table". Two, so a lone "5 mg" mentioned
# in one prose-like cell does not flag an otherwise non-dose table.
_DOSE_MIN = 2


def review_queue_enabled() -> bool:
    """Kill-switch, default OFF. See module docstring."""
    return os.environ.get("T2N_REVIEW_QUEUE", "0").strip().lower() in ("1", "true", "yes", "on")


def dose_token_count(text: str) -> int:
    """Number of drug-dose / numeric-threshold tokens in `text`."""
    return len(_DOSE_RE.findall(text or ""))


def page_has_continuation_marker(page_text: str) -> bool:
    """True if the page's text carries a table-continuation marker."""
    return bool(_CONTINUATION_RE.search(page_text or ""))


def review_reasons(table_md: str, page_text: str, stitched_continuation: bool = False,
                   geometric_continuation: bool = False) -> list[str]:
    """Return the review triggers this table hits (empty = no review needed).

    `table_md`            the emitted markdown for the table.
    `page_text`           the page's rendered text (for the continuation marker).
    `stitched_continuation`  True when the caller already knows this table was merged
                          across a page break (merge path) — a definitive continuation
                          signal that does not depend on a textual marker.
    `geometric_continuation`  True when the page geometry says this table resumes the
                          previous page's (convert.py's `geometric_continuation()`), which
                          is the only signal available for a table broken across pages with
                          no continuation caption printed anywhere.

    The two triggers are independent: either one alone puts the table in the queue.
    """
    reasons: list[str] = []
    if stitched_continuation or page_has_continuation_marker(page_text):
        reasons.append("continuation-page (orphan first row can be fused into the wrong label)")
    elif geometric_continuation:
        # Named distinctly so a reviewer can tell a caption-driven flag from a
        # geometry-driven one — the latter has no printed "(continued)" to check against.
        reasons.append("continuation-page by geometry, no continuation caption "
                       "(orphan first row can be fused into the wrong label)")
    if dose_token_count(table_md) >= _DOSE_MIN:
        reasons.append("dosage/threshold values (a value on the wrong row = wrong clinical data)")
    return reasons


def format_review_marker(page_no: int, reasons: list[str]) -> str:
    """The markdown annotation for a table entering the review queue. Same ⚠️/HTML-comment
    convention as `format_trace_marker`, but a distinct wording so it is greppable and never
    confused with the structural QC flag: this table is well-formed but its row bindings are
    NOT verifiable by the gate and need a second opinion."""
    why = "; ".join(reasons)
    return (
        f"<!-- ⚠️ table needs out-of-band review ({why}) — the structural QC gate cannot detect a "
        f"value bound to the wrong row; verify this table against PDF page {page_no} (or run the "
        f"second-opinion pass in docs/table-review.md) before citing it as data -->"
    )
