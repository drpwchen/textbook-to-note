# Note-Writing Workflow — Textbook → Complete Note

This is a generic, adaptable workflow for an AI agent to write a complete,
structured, fully-cited note from your own textbook markdown corpus (see
`converter/` and `docs/architecture.md`), then merge it into your existing
notes vault.

## Core invariant: draft blind, merge last

Phases 1–3.5 are **blind to any existing note on the same topic** — the
agent produces an independent draft from the textbook corpus alone, and only
compares against what already exists in Phase 4. If you find the agent
reaching for a vault/notes search before Phase 4, that's a bug: this
workflow exists specifically to prevent it.

**Why blind:** reading the existing note first biases section organization,
headings, and emphasis toward whatever structure happened to exist before —
even if that structure was accidental (built up ad hoc, note-by-note, over
time). Drafting independently from the source material first, and only
reconciling with history afterward, produces a more consistent, less
path-dependent knowledge base.

## Pipeline overview

| Phase | Step | Reads existing note? |
|---|---|---|
| 1 | Topic-type detection → template → textbook (+ optional guideline corpus) search & extract | No |
| 1.5 | **Section skeleton first** — list section structure with one-line placeholders, no expansion | No |
| 2 | *(optional, pluggable)* External evidence/enrichment stage — e.g. a clinical-evidence API, a regulatory/reimbursement database, or any other domain-specific lookup — integrated inline into the relevant sub-sections | No |
| 3 | *(optional, pluggable)* A second domain-specific enrichment stage, if your domain has one (only if the template calls for it) | No |
| 3.5 | **Figure harvest** — pull relevant figures from the textbook corpus via `figure_remap.py extract`, then classify every crop before embedding any (default ON; opt out with a flag) | No |
| 4 | Compare with existing note — **deconstruct & reslot**, never append | Yes |
| 5 | Link suggestion on the final note (optional; default ON) | Yes |

Run the full chain in one continuous pass — don't pause for confirmation
between phases. Only pause where the workflow explicitly calls for a
judgment call (see "Where to pause" below).

## Phase 1: Independent draft from the textbook corpus

### Step 1.1: Topic-type detection → template

Classify the topic and pick the matching note template before writing
anything. A generic classification for a knowledge-note vault might look
like:

| Type | Trigger signal | Template shape |
|---|---|---|
| Concept/entity note | A single well-defined concept, condition, or system | Background → Evaluation/Analysis → Application/Management → Resource |
| Procedure/method note | A method, technique, or process | Principle → Indication → Technique → Interpretation → Pitfalls |
| Reference/atlas note | A visual or positional reference with no single narrative | No fixed template — pick a top-level axis with the user, then free hierarchy underneath |
| Tool/instrument note | A named tool, scale, or instrument | Purpose → Procedure → Scoring/Output → Validity/Reliability |
| Free-structure note | Anything foundational/background that doesn't fit the above | Free structure, commonly Overview → Mechanism → Practical implications |

Adapt the table to your own domain's actual note types — the underlying
principle is: **pick the template from the topic's shape, not from habit**,
and ask the user if a topic could plausibly fit more than one template.
See [`templates/`](../templates/) for a real, in-production set of five
medical-note templates (zh-TW + English) showing this row-to-template
mapping concretely, plus notes on adapting them to another field.

If the topic is a spatial/positional reference with no natural narrative
structure, ask the user up front which top-level axis to organize by (e.g.
by position, by region, by viewing angle) rather than guessing.

### Step 1.2: Textbook (+ guideline) search

Search priority:
1. Semantic search over your converted textbook corpus, if configured (see
   `docs/architecture.md`)
2. Grep over the markdown corpus for exact keywords
3. A broader research/explore pass only as a last resort

If your domain has an authoritative primary-source corpus beyond raw
textbooks — for example, professional-society clinical/technical
guidelines, standards documents, or regulatory rulebooks — search that
corpus alongside the textbooks. These primary sources are often
load-bearing for the "decision algorithm" and "criteria" sections of a note:
they give a graded, authoritative recommendation structure that a textbook
narrative usually doesn't.

**Multiple/conflicting primary sources**: it's common for one topic to have
more than one authoritative guideline. First determine whether the sources
are actually in conflict or just answering different sub-questions (a
surgical-society guideline and a conservative-management guideline on the
same condition, for instance, usually aren't contradicting each other — they
have different scope). If they are genuinely in conflict:
1. Prefer the more recent one, but only when scope and methodology are
   comparable
2. Prefer the one with a stronger evidence-grading methodology
3. Prefer the one whose population/setting best matches the note's context
4. **Do not silently drop the losing guideline** — presenting a real
   disagreement between two authoritative sources is itself high-value
   information; note both positions with their respective grading

**Exclude non-primary sources from the draft.** If your vault or corpus
contains informal materials — shared study notes, presentation decks,
crowd-sourced summaries — these must never be used as a source or citation
for a drafted note, even indirectly. The failure mode this guards against:
an informal source repackages a primary source's numbers with subtle
distortion; if the agent reads the informal version and then "confirms" it
against a textbook, it can end up citing the textbook for a number the
textbook doesn't actually contain. The fix is structural — exclude informal
sources from the search entirely at the tool layer, so the content is never
seen at draft time, rather than trying to catch the mistake after the fact.
If you ever suspect a claim traces back to an informal source, verify it
appears in the primary source verbatim before citing it; if you can't verify
it, drop the claim.

If no primary-source hit after exhausting all search steps, stop and tell
the user rather than falling back to general web search for the primary
draft (an optional web/literature enrichment stage, if you build one, is a
separate opt-in step — not part of the blind draft).

### Step 1.3–1.4: Read, extract, defer figures

Read the relevant sections; skip tables of contents, reference lists, and
indices. Collect figure candidates (fig_id, caption text, PDF page from the
`<!-- REF -->` markers) for the Phase 3.5 harvest, but don't extract any
images yet — Phase 1 is draft-only, no file writes, no figure calls.

## Phase 1.5: Section skeleton first

Before expanding any content, write only the section skeleton — one-line
placeholders per heading. This prevents drift into prose paragraphs and
prevents content ending up in the wrong section.

Example skeleton (concept/entity template):

```markdown
> [!summary] Key points
> - <point 1: one line>
> - <point 2>
> - <point 3-5>

# Background
## Definition
- <one line>
## Context / prevalence
- <one line>
## Mechanism
- <one line>

# Evaluation / Analysis
## Criteria
- <criteria / cut-off / operational definition>
## Assessment methods
### <method 1>
- <one line>
## Classification
- <list; if detailed elsewhere, reference rather than re-write>
## Comparison / differential
- <table: alternatives + key distinguishing points>

# Management / Application
## ⭐ Decision algorithm (domain-optional)
> [!note] Algorithm
> - <decision tree on the axis that actually drives real-world decisions for this topic — fits procedural/clinical/engineering-style topics with a real decision point; skip this sub-section for humanities/history-style topics that don't have one>
## Goals
- <one line>
## Options
### <option 1>
- <one line + optional Evidence sub-bullet>

# Resource
## Reference
### Books
- <Author Ch.X — book title, edition, year>
### Guidelines
- <Society year — full guideline title, society, year>
### Papers
- <Author year — full citation, journal, doi/PMID>
## Related notes
- (Phase 5, optional link-suggest)
```

Rules:
- A third level of headings is encouraged where the topic genuinely needs
  it (sub-types, special cases)
- Each sub-section gets a one-line placeholder only — do not expand yet
- Show the skeleton to the user before expanding — they may want to
  redirect section choices; if there's no response, treat as approved and
  continue

After the skeleton is approved, expand to a complete draft in the
conversation (don't write the file yet).

## Phase 2/3: Optional pluggable enrichment stages

These stages are **optional and domain-specific** — build the ones relevant
to your field, skip the ones that aren't. Examples of what they could be:

- A clinical-evidence API (drug/treatment evidence lookups)
- A regulatory or reimbursement rules database (what's covered, under what
  conditions, in your jurisdiction)
- A standards/specification lookup (engineering codes, compliance rules)
- A literature-search integration (recent papers not yet in any textbook)

Design pattern for any such stage:
1. Compose focused queries per sub-topic/indication/setting in the draft (one
   query per row of whatever matrix you're building)
2. Run queries in parallel where the integration supports it
3. **Verify what comes back** before trusting it — if the tool is an LLM-
   backed service, cross-check its citations against primary sources rather
   than accepting them at face value; if it's a database lookup, the
   response is authoritative by construction and doesn't need the same
   verification step
4. Integrate results **inline** into the relevant sub-sections (a bullet
   gets its evidence/rule directly underneath it) — not only in a separate
   summary table. A summary table, if you build one, should be a
   supplementary quick-reference that doesn't duplicate the inline detail
5. Sanitize placeholders: if the enrichment source ever returns an
   unresolved citation placeholder, either resolve it to a real citation or
   delete the claim — never leave the placeholder in the note
6. Gate on verification verdicts: automatically integrate what verifies
   clean; flag anything that fails verification for the user to decide,
   per-item

Skip these stages entirely for foundational/background topics where an
external evidence stage doesn't apply (e.g. pure anatomy or pure
definitional notes).

## Phase 3.5: Figure harvest (QC-gated)

**Default ON** — only skip with an explicit flag. Pull anatomy,
classification, and imaging figures from the textbook corpus so notes are
visual, not pure text. Every embedded figure passes a QC gate — no figure is
trusted without verification (see `skills/figure-remap/SKILL.md`).

**N/A for EPUB-sourced books.** EPUB has no fixed page layout, so the
`<!-- REF -->` page markers this phase depends on don't exist for
EPUB-sourced material. Skip this phase entirely for such a book and record
`figures: n/a (epub source)` in the note's frontmatter/metadata rather than
leaving the field blank or attempting extraction against a page number that
doesn't correspond to anything.

**Don't skip silently.** If a topic genuinely has no embeddable figures,
state that explicitly ("no qualifying figures after selection filter —
skipped") rather than leaving it unmentioned.

### Selection rules

Prefer to embed:
- Anatomy/structural diagrams
- Classification/grading figures
- Staged imaging comparisons
- Procedural/technique diagrams

Skip:
- Author photos, institutional logos
- Pure flowcharts better rewritten as a callout
- Branded product photography with no educational content

### Extraction

For each candidate figure, resolve the source book directory and PDF path,
then call the single sanctioned entrypoint:

```bash
python {REPO}/figures/figure_remap.py extract \
  --book "{Book}" \
  --fig-id "X-Y" \
  --caption "<full caption text>" \
  --out "path/to/vault/attachments/Fig_X-Y_{BookShort}.jpeg" \
  --pdf "{pdf_path}" \
  --page {1-indexed-pdf-page}
```

Handle the contract's `status` field exactly as documented in
`skills/figure-remap/SKILL.md`: `pass` → embed; `fail` → verify the page
number, then retry or leave a `<!-- TODO -->`; `escalate` → only reachable
with `--no-strict`.

One exception inside `fail`: when `reason` starts with `pregate=`, the crop
was fine and the junk pre-gate judged it to be a chapter banner or a blank.
Drop that figure and move on — no page check, no retry, no TODO. Retrying
just re-crops the same banner.

### Classify every crop before embedding any of it

A `pass` means the crop is the raster the caption owns. It does not mean the
crop is worth embedding: it can be a table, a page banner the pre-gate could
not see, a truncated figure, or a figure whose message is really a list of
criteria. So classify **after all candidates have been extracted and before
the first embed**, in one batch — batching keeps the judgment uniform, and
doing it before any embed means nothing gets written and then retracted.

Give a vision-capable agent the frozen prompt at
[`figures/classify_prompt.txt`](../figures/classify_prompt.txt) verbatim, plus
one record per crop — `{id, image_path, caption}`, where `caption` is the real
caption text read from the source, not the fig_id. It looks at the image and
the caption only (never the filename, book, or figure number) and returns one
JSON object per crop:

```json
{"id": "...", "usable": "yes|no|uncertain", "figure_type": ["..."],
 "crop_quality": "complete|truncated|wrong_page|mostly_text|blank|uncertain",
 "action": "embed|callout|skip|retry", "confidence": "high|low"}
```

Then apply one deterministic **abstention rule** on top of the answer: if
`action` is `embed` but `usable` is not `yes`, or `crop_quality` is
`uncertain`, or `confidence` is `low` → force it to `retry`. Route by the
final action, and account for every crop:

| action | what happens |
|---|---|
| `embed` | embed it (next section) |
| `callout` | do not embed — transcribe the figure's content into a text callout |
| `skip` | drop it; note the fig_id and the type the classifier called it |
| `retry` | **the only case a human/driver looks at the image** — embed, fix `--page` and re-extract, or leave a TODO |

**Use a frontier-tier model for this step.** On a held-out set of 244 crops
with frozen reference labels, this prompt plus the abstention rule let
**1 junk crop of 100** through with Claude Sonnet 5, and **6 of 100** with
Claude Haiku 4.5. Every one of Haiku's misses was a high-confidence perceptual
error — a table called `other`, a chapter banner called `imaging` — with all
four fields self-consistent. The abstention rule keys on the model's own
fields, so it cannot reach that class of error at all: the cheaper tier is not
something you can buy back with more rules. Both figures are measurements of
those two models on that set, not a property of this pipeline; measure your own
before substituting a model.

`figures/classify_prompt.txt` is a **frozen file**. Rewording it changes those
numbers, so keep a labelled crop set of your own and re-measure leak /
false-skip / retry-load before adopting an edit — changing the eyes without
re-checking the prescription is how a silent regression gets in.

### Embedding format

Put the italic caption on the line immediately above the image; embed with
your notes tool's image-embed syntax and an explicit width (never leave an
image widthless). Choose width by content type: fine-detail imaging wider,
schematic/classification figures narrower. Place the figure directly under
the bullet or section it illustrates.

## Phase 4: Compare with existing note — deconstruct & reslot

Skip if you want a clean new draft with no merge. This is the **first**
phase allowed to read an existing note on the topic.

### Branch

**No existing note** → confirm the target location/subfolder with the user
if ambiguous, show the full draft for approval, then write it.

**Existing note found** → the default is **non-destructive**. Before
replacing an existing note, verify the vault is actually under version
control (e.g. `git status` succeeds in the vault root) — version-control
rollback is a checked precondition, not an assumption. If the vault is
under version control, replace-rather-than-append is safe, with version
history/rollback handled by that VCS rather than a manual backup step. If
it is **not** under version control, either write the draft alongside the
original as `<note> (draft).md` rather than overwriting, or get explicit
user confirmation before replacing. Only pause for per-item approval when:
- The existing note is substantial (well over ~50 lines) and contains
  clearly hand-written, non-textbook content (personal observations,
  first-hand experience) that a blind textbook draft has no way to
  reproduce
- More than a couple of existing points can't be slotted into the new
  skeleton
- The existing note has significant images that need re-placement

Otherwise, merge and overwrite directly, then summarize the integration
choices made.

### Deconstruct & reslot (not append)

The existing note's content must be **taken apart and re-classified** before
being merged into the new skeleton — never absorbed as whole paragraphs.
Absorbing whole sections wholesale, when the old note had no clear
subheadings, is exactly the failure mode this guards against: content ends
up stuck in whatever section it happened to land in, rather than the
section it actually belongs to.

1. **Scan every point in the existing note** and tag each one with the
   target section in the new skeleton (e.g. "gait description" → "Evaluation
   > Assessment methods > Physical exam")
2. **Re-insert tagged points** into their matching section of the new
   skeleton
3. **Reconcile content**:
   - Exact duplicate of what the new draft already says → drop the old
     version
   - Old note has it, new draft doesn't → keep the old point, marked as
     coming from the existing note with no source citation
   - Conflict (same topic, different claim) → the textbook-sourced version
     wins, unless the old content is clearly a first-hand/personal
     observation, in which case keep it and label it as such
   - Old note is unstructured prose → rewrite it as nested bullets with
     proper hierarchy
4. **Merge frontmatter/metadata**: keep the original creation date, update
   the modified date, merge alias/tag lists with de-duplication
5. **Show a section-by-section diff** and wait for approval before writing,
   unless your workflow has been explicitly configured for autonomous
   merges

### Orphaned attachment recovery (optional, recommended)

If the existing note (or its source material) references images with
generic, non-descriptive filenames, this is a good moment to clean them up
as part of the merge rather than opening a separate pass later:
1. Confirm the file actually still exists on disk (a generic-named
   reference may be a dead link)
2. Have a vision-capable step inspect each one and report back: what it
   actually shows, whether it matches what the note claims, and a
   suggested descriptive filename
3. **File operations (rename/delete) should be performed by the orchestrating
   step, never by a sub-step that only has inspection access** — this keeps
   destructive filesystem operations under a single, auditable point of
   control
4. Re-caption based on what the image actually shows, not what the old note
   assumed it showed
5. Re-place each image under the section it actually belongs to in the new
   skeleton

## Phase 5: Link suggestion (optional)

Default ON; skip with an explicit flag. After the file is written, run a
link-suggestion pass over the final note to surface unlinked-but-related
notes elsewhere in the vault, and let the user accept/reject each suggested
link.

## Writing-style guidance (generic)

These principles apply regardless of your specific vault's formatting
conventions — adapt the mechanical details (indent style, heading levels)
to your own tool, but keep the underlying discipline:

- **Nested bullets, not prose paragraphs.** A knowledge note should be
  scannable, not read like an essay.
- **Every claim cites a source** (book + chapter, guideline + year, paper +
  citation). If you add a claim from your own general knowledge rather than
  the source material, mark it explicitly as agent-inferred — never present
  it as if the source said it.
- **Tables for comparisons** (differentials, classification systems), not
  bullet lists — a table makes the comparison axis explicit.
- **One canonical location per fact.** If a classification system or
  severity scale is relevant in more than one section, write it once in its
  most natural home and reference it from elsewhere — never retype it. This
  keeps future edits to one place instead of several drifting copies.
- **The "decision algorithm" or "management" section should be
  immediately actionable** — a reader should be able to act from it without
  needing to open the source material. Put the decision tree first, before
  supporting detail.
- **No unresolved citation placeholders.** Before finalizing, check the
  draft doesn't contain any "citation pending" / "metadata unavailable"
  style placeholder text.
- **Read the chapter number off the corpus, never off the topic.** A book
  plus a chapter number is an exact-match string, and a wrong one reads
  perfectly: the surname is right, the number is plausible, the claim is
  true — it just isn't in that chapter, or isn't in that book at all. Two
  ways this goes wrong that human review does not catch:
    - **One name, several books.** `ElMiedany Ch.5` is Psoriatic Arthritis
      in the rheumatology volume and something unrelated in the pediatric
      one. Cite the edition when a name is ambiguous (`Braddom 7e Ch.49`),
      or pin a default in `_chapter_index.defaults.json`.
    - **The converter's file sequence is not the book's chapter number.**
      `ch105_Medical_Complications_of_SCI.md` is chapter 7 of its book.
      Citing "Ch.105" points at content that is right and at a location
      that does not exist on paper.

  Check before you write, and again over the finished note:

  ```bash
  export TEXTBOOK_DIR="<your converted corpus>"   # or pass --textbook-dir to each command
  python {REPO}/citations/textbook_chapter_index.py --textbook-dir "$TEXTBOOK_DIR" --rebuild
  python {REPO}/citations/textbook_ref_lint.py --textbook-dir "$TEXTBOOK_DIR" --refs "ElMiedany Ch.5"
  python {REPO}/citations/textbook_ref_lint.py --textbook-dir "$TEXTBOOK_DIR" "<note.md>"
  ```

  Name the corpus. With neither flag nor env var the commands fall back to
  this repo's own `output/` and refuse outright if that does not exist —
  they never scan the current working directory, which used to produce a
  cheerful 0-book index from wherever the command happened to be run.

  Exit 1 = a reference is provably wrong. **Exit 2 = no corpus to check
  against.** **Exit 3 = unverifiable** (book not converted, or only partly
  converted) — that is not a pass; confirm it yourself before the claim ships.

  A book this repo's converter chapter-split itself is reported as
  *sequence-split*: its `chNN_` numbers are the converter's split counter,
  not the book's printed chapter numbers, so no citation into it can be
  verified until you write the real chapter table into
  `_chapter_index.chapters.json` beside the corpus.

## Self-check before writing the file

1. No informal/non-primary-source content entered the draft
2. Every claim traces to a cited primary source; any agent-added fact is
   marked as inferred
3. No nested bullets containing tables (most notes tools won't render that)
4. Figures were QC-gated (Phase 3.5), not hand-embedded unverified
5. `textbook_ref_lint.py` run over the draft: every chapter reference either
   resolves, or you confirmed the unverifiable ones by hand
6. If your notes tool has an automated format-lint hook, run it and resolve
   every failure before writing — treat it as a mechanical pre-flight check,
   not a substitute for the judgment checks above

## Optional: spaced-repetition review cards

After a note is written and passes the self-check, you can optionally
generate a small set of understanding-based Q&A cards (not verbatim
highlight extraction) for a spaced-repetition system, each answer citing its
source. This is off by default — only do it when asked.
