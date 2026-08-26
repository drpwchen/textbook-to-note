# figure-extraction — crops, and the gate that refuses them

## Purpose

Pull a named figure out of a textbook PDF and hand back either a crop that provably shows
that figure, or a refusal. Never a plausible wrong figure. Entry point:
`figures/figure_remap.py`; gate engine `figures/figure_qc_gate.py`; junk pre-gate
`figures/pregate.py`.

This stage is **experimental** — it does not yet handle every book. That status is part of
the contract, not a caveat to be quietly dropped.

## Requirements

### Requirement: The QC gate is pure computation

The system SHALL decide pass/fail on a crop using deterministic checks only, so the same
crop always receives the same verdict.

The chain is two checks: whitespace-fill (content bbox / image bbox ≥
`WHITESPACE_FILL_MIN`, 0.80) and text-bleed (at most `TEXT_BLEED_CHAR_MAX`, 50, prose
characters inside the region, where a prose line is at least `PROSE_LINE_MIN`, 35,
characters; an overlay label within `LABEL_REACH`, 36 pt, of the figure box counts as part
of the figure).

#### Scenario: Same crop, same verdict
- **GIVEN** one crop file and one caption
- **WHEN** the gate is run on it twice
- **THEN** the two verdicts are identical

#### Scenario: Crop with body text bleeding in fails
- **GIVEN** a crop whose region contains 120 characters of prose beyond the caption
- **WHEN** the gate runs
- **THEN** the verdict is fail and the reason names text-bleed

**Negative constraints**
- No model MUST participate in the QC chain. A model-backed check gave the same crop a pass
  on one run and a fail on the next (issue #16), which is why such checks were removed
  outright.
- Thresholds MUST NOT be tuned to make a specific failing case pass. Fix the book's
  geometry logic instead.

### Requirement: The gate refuses rather than guesses

The system SHALL hard-fail when it cannot place a figure, and SHALL NOT emit a
best-available crop as if it were the requested figure.

#### Scenario: Wrong page yields a refusal
- **GIVEN** a request for a figure whose caption is not found on the given page
- **WHEN** extraction runs
- **THEN** no crop is embedded and the caller receives a failure, not a different figure

#### Scenario: Retry is bounded
- **GIVEN** a crop that fails the gate
- **WHEN** guided retries are attempted
- **THEN** at most `RETRY_LIMIT` (2) retries run before the request escalates or fails

### Requirement: A deterministic pre-gate kills non-figures

The system SHALL kill chapter-title banners and blank crops on page metadata alone, before
the QC gate, and SHALL tell the caller to skip that figure rather than retry it.

Frozen thresholds (`figures/pregate.py`): blank when pixel std < 3.0; banner when the crop
starts within 0.02 of page height from the top, ends by 0.36, spans at least 0.95 of page
width, and the page carries fewer than 40 vector paths.

#### Scenario: Chapter-title banner is killed, not retried
- **GIVEN** a full-width crop at the top of a chapter-opening page with 8 vector paths
- **WHEN** the pre-gate runs
- **THEN** the action is `kill` and the caller skips the figure

#### Scenario: Vector-heavy figure is never killed
- **GIVEN** a crop on a page with 200 vector paths
- **WHEN** the pre-gate runs
- **THEN** the action is `pass`

#### Scenario: Missing dependencies do not kill
- **GIVEN** PIL/numpy are unavailable so `px_std` cannot be computed
- **WHEN** the pre-gate runs
- **THEN** the missing feature is treated as "no evidence to kill on" and the crop passes
  to the QC gate

**Negative constraints**
- The pre-gate's calibration requirement is **zero false kills on good figures**, because a
  killed figure gets no second chance. A change that raises kill rate at the cost of any
  false kill MUST NOT ship.
- The pre-gate MUST NOT require per-book tuning.

### Requirement: The gate CLI is not a public entry point

The system SHALL block direct CLI use of `figure_qc_gate.py gate`, so the workflow's
classification step cannot be bypassed by convenience.

#### Scenario: Direct gate CLI is refused
- **GIVEN** `FIGURE_REMAP_ALLOW_GATE` is unset
- **WHEN** `python figures/figure_qc_gate.py gate ...` is run
- **THEN** the process exits 2 and stderr names `figure_remap.py extract` as the entry point

#### Scenario: Engine debugging remains possible
- **GIVEN** `FIGURE_REMAP_ALLOW_GATE=1`
- **WHEN** the same command runs
- **THEN** the gate executes and exits 0 on pass, 2 on fail

**Negative constraints**
- This guard MUST be documented as blocking the convenient path, not as making bypass
  impossible — Python cannot prevent `import figure_qc_gate; gate()`.

### Requirement: Passing the gate does not authorise embedding

The system SHALL treat "is this crop structurally sound?" and "is this crop worth
embedding?" as separate questions, and SHALL require a frontier-tier model to read every
crop before any of them is embedded.

#### Scenario: Every crop is classified before any embed
- **GIVEN** six QC-passed crops harvested for one note
- **WHEN** the note-writing workflow reaches the embed step
- **THEN** all six have been read and classified before the first is embedded

**Negative constraints**
- A cheaper model MUST NOT be substituted for the classification step. On a held-out set of
  244 crops, weaker tiers let junk through at materially higher rates.
- Figure extraction and its QC gate MUST NOT require a local vision model. Since v0.7.1 the
  extraction path is pure computation.

### Requirement: Per-book breakage is fixed once, in that book's logic

The system SHALL localise a book-specific extraction failure to that book's logic, so every
later extraction from it is correct.

#### Scenario: A book's crop rule is corrected
- **GIVEN** a book whose figures extract with the caption inside the crop
- **WHEN** that book's logic is corrected and recorded in `figures/CALIBRATION.md`
- **THEN** subsequent extractions from that book pass the gate without changing any global
  threshold
