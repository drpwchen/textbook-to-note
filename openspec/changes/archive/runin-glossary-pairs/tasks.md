# Tasks — runin-glossary-pairs

## Implementation

- [x] `runin_paired_text()` + `T2N_RUNIN_PAIRS` in `converter/convert.py`, called by
      `render_page_text()` only when `column_sorted_text()` returned None
      → Requirement: Run-in glossary entries can be rejoined on the text-layer path (opt-in)

## Verification

- [x] `converter/test_runin_pairs.py` (synthetic PDFs built in the test + the bundled sample)
      covers every scenario; added to CI (needs only fitz)
- [x] Mutation check: removing the third-cell guard, the bold rule, the separation check, the
      continuation lines, the blank-line separator, rotated-line retention, the wiring, setting
      `RUNIN_MIN_PAIRS` to 1 or the default to ON — each fails the test (9/9 killed)
- [x] Byte-identical when unset: `render_page_text()` of 0.8.1 vs this change agree on all
      15,515 pages below; full `convert.py` of the sample, 0.8.1 vs this change unset: `cmp` equal
- [x] Corpus measurement (flag unset vs `=1`, every page, CPU only, no Docling)
- [x] Full suite green

### Measurement (2026-11-14)

25 born-digital PDFs: the bundled sample + 24 of the maintainer's books (17 whose markdown has a Glossary /
Key Terms heading, the rest ordinary single- and two-column text). The last 7 in the list
were added after the rule was final (held out).

| Book | Pages | Changed | Correction | Regression |
|---|---|---|---|---|
| OpenStax A&P Ch1 (sample) | 33 | 3 (p28–30, 70 entries) | 3 | 0 |
| Lundy-Ekman, Behrens, Bellew, Whittle, Cook, Brookshire, Shipley, Starkey, NSCA ×2, Neumann, Ciccone, Groher, Rondinelli, Frontera, Cuccurullo, Blumenfeld | 11,941 | 0 | 0 | 0 |
| Held out: Thow, Pilat, Baessler, Magee, Rankin, Trescot | 3,220 | 0 | 0 | 0 |
| Held out: Niebauer | 321 | 1 (p242, 6 entries) | 1 | 0 |
| **Total** | **15,515** | **4** | **4** | **0** |

Niebauer p242 is a figure of `Week N:` run-in heads whose stream is scrambled; each head now
sits with the line on its own baseline (all six checked against the geometry). The figure's
other lines keep their (scrambled) stream order, as before. On every changed page the
characters are the same before and after (no line dropped or duplicated).

The first draft (no bold rule, no third-cell guard) also changed Lundy-Ekman p87 (figure panel
letters paired with labels) and Whittle p66 (chart axis labels paired with a caption): two
regressions, which are why a pair needs a bold term and a non-bold definition start. Both pages
are unchanged by the final rule.

## Ship

- [x] `CHANGELOG.md` entry (0.8.2)
- [ ] Version tag — by the lead at release
- [x] Archive: fold delta into `openspec/specs/`, move this directory to `changes/archive/`
