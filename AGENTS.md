# AGENTS.md — Instructions for your AI coding agent

You are helping your user set up `textbook-to-note`: a local-first pipeline
that converts their own PDF/EPUB textbooks into searchable markdown, then
into structured, fully-cited notes in their personal knowledge vault
(Obsidian, Logseq, or a plain markdown folder). This repository is designed
to be deployed **by you**, an AI coding agent, working directly with the
user rather than requiring them to hand-run every script themselves.

Read this file fully before doing anything. Then work through the steps
below in order, checking in with the user at the marked decision points.

## What you're setting up

```
converter/    — PDF/EPUB → markdown conversion (0 LLM tokens)
figures/      — on-demand figure extraction with QC gating
skills/       — two Claude Code skill definitions (drop-in to ~/.claude/skills/)
workflows/    — the note-writing workflow specification
templates/    — real production note templates (zh-TW + English) for the topic-type
                table in workflows/note-writing.md Step 1.1 (profiles B/C)
docs/         — architecture + OCR-ladder reference docs
examples/     — one example output note showing the target format
shared/       — shared config (paths, env var names)
requirements.txt
```

Read `docs/architecture.md` first for the full picture. Leave
`docs/ocr-ladder.md` alone until Step 4.5 tells you to open it — it is a
reference for a failure case most users never hit, not part of setup.

## Step 1: Which profile is the user setting up?

**Ask this before anything else**, and do not start installing until you have
an answer. The profiles stack — each is the one above it plus one more thing —
and the wrong assumption here costs the user an install they never needed.

| Profile | The user wants | Steps you run |
|---|---|---|
| **A · Converter only** | Their books as clean, greppable markdown. `grep` is their whole search story. | 1.1 → 2 → 3 → 4 (→ 4.5 only if output is broken). **Then stop.** |
| **B · A + note workflow** | An AI writing structured, cited notes from that corpus, with figures. | A's steps, then 6 → 7 → 8. |
| **C · B + semantic search** | Cross-book retrieval by meaning, not keyword. | B's steps, plus 5. |

If the user doesn't know, **default to A** and say why: it is a complete,
supported end state, it installs in one command, and B and C can be added
later without redoing any conversion. Profile A also means Step 5 (indexing),
Step 6 (skills) and Step 7 (note workflow) are simply not yours to run — do
not "set them up anyway to be helpful", and do not install ollama or an
embedding model for a profile-A user.

State the chosen profile back to the user in one line before you continue, so
a wrong guess gets corrected now rather than three installs later.

### Step 1.1: Understand the user's situation

Ask, or infer from context:
- Where do their textbook PDFs/EPUBs already live? (a folder, a cloud-synced
  drive, etc.)
- **Profiles B/C only** — what notes tool do they use, and where is their
  vault/notes folder? A profile-A user has no vault in this pipeline; the
  converted markdown is the deliverable.
- **Profile C only** — semantic search (LanceDB + a local embedding model via
  ollama) pays off once there are more than a handful of books. **This repo
  ships no indexer** — the companion repo ask-your-library (formerly *vault-search*) indexes the corpus
  folder this repo produces (Step 5); grep works with no extra install. Details: [`docs/setup-details.md`](docs/setup-details.md#step-11--profile-c-the-semantic-search-index).

**Do not ask about GPUs here, and do not install any OCR engine yet.** OCR is
a reactive exception path in this pipeline, not a prerequisite — see Step 4.5.
Born-digital PDFs (bought ebooks, publisher downloads — the common case) go
through `fitz` alone and never touch it.

## Step 2: Install dependencies

```bash
pip install -r requirements.txt
```

(`pip install -e .` from the clone does the same and also puts `t2n-convert`,
`t2n-post-convert` and `t2n-split-validate` on `PATH`.)
`requirements.txt` covers **only** the core converter. EPUB needs the `pandoc`
CLI; `requests`, `lancedb` and the OCR (Surya) venv are separate installs, and
it never gives the user semantic search. Details:
[`docs/setup-details.md`](docs/setup-details.md#step-2--what-requirementstxt-does-and-does-not-cover).

## Step 3: Configure paths

Configuration is environment-variable driven with repo-relative defaults —
`shared/config.py` documents every variable and works with zero setup:
drop books in `./books`, get markdown in `./output`.

Set env vars only where the defaults don't fit: `BOOKS_DIR` (default `./books`),
`OUTPUT_DIR` (default `./output`; keep it outside the notes vault), and — only
when a later step sends you there — `SURYA_VENV_PY` + `SURYA_ADAPTER` (skip
until Step 4.5), `VAULT_SEARCH_DIR` (Profile C, Step 5), and the figure
locations. Full list: [`docs/setup-details.md`](docs/setup-details.md#step-3--environment-variables).

Run `python shared/config.py` to print the resolved configuration and
confirm it before converting anything. Never hardcode paths into scripts or
skill files — always go through `shared/config.py` or env vars, so the repo
stays portable across machines and users.

## Step 4: Convert a first book (smoke test)

Pick one book the user cares about and convert it end to end, narrating what
you're doing:

```bash
python converter/convert.py "path/to/one.pdf" --book-label "Author Title — Ch.1"
```

Check the output markdown for:
- Readable, non-garbled text
- `<!-- page N -->` markers present
- Any `<!-- REF: Fig. X.Y → ... -->` markers where the source mentions
  figures

**For clinical / drug-dosing corpora, set `T2N_REVIEW_QUEUE=1`.** Extracted
tables can carry a *misbinding* — a value on the wrong row (a dose fused into
the wrong drug on a continuation page) — that is structurally invisible to the
QC gate and reads as clean, citable data. The flag marks the high-risk subset
(continuation-page + dosage/threshold tables) with a
`<!-- ⚠️ table needs out-of-band review … -->` comment for a second-opinion
pass. Before citing any flagged table as data, verify it against the PDF (or
run the review pass). See [`docs/table-review.md`](docs/table-review.md).

Once the smoke test looks right, convert the rest of the user's priority
books:

```bash
python converter/convert.py --batch-dir "path/to/their/textbook/folder"
```

This can take a while for a large library — run it as a background job and
report progress, don't block the conversation on it. Don't run two batch
conversions against the same `OUTPUT_DIR` concurrently — progress tracking
is last-writer-wins, so overlapping runs will corrupt each other's progress
state.

## Step 4.5: OCR — only if Step 4 actually came out wrong

**Skip this entire step unless the Step 4 output is genuinely garbled or
mostly empty.** If the markdown is readable, the pipeline is working as
designed and there is nothing here for you to install. Installing an OCR
engine "to be safe" is the single most expensive mistake you can make during
setup: it pulls in a heavyweight ML stack plus a multi-GB model download, and
has cost a user hours and tens of GB of RAM on a book that converted perfectly
without it.

Routing to OCR exists **only** in the `--batch-dir` path (scan-only PDF, silent
`fitz` failure, or `--force-surya`); the single-file path never invokes OCR.
Diagnose first (can you select text on a bad page? garbled font encoding vs a
true scan), then size the engine to the hardware via
[`docs/ocr-ladder.md`](docs/ocr-ladder.md) and set it up per
[`docs/surya-adapter.md`](docs/surya-adapter.md). The full procedure — diagnosis
steps, engine setup, the `--force` re-run and when to add `--force-surya` — is in
[`docs/setup-details.md`](docs/setup-details.md#step-45--ocr-routing-diagnosis-engine-setup-re-run).
**Read it before installing anything.**

## Step 5: (Profile C only) Hand the corpus to ask-your-library

Skip this step entirely for profiles A and B — they need no index, no
embedding model and no ask-your-library. This repo does no indexing: it produces
a corpus folder, and the companion repo
[ask-your-library](https://github.com/drpwchen/ask-your-library) makes that folder
searchable. The sequence:

```bash
python converter/convert.py --batch-dir <BOOKS_DIR>                      # Step 4: books → OUTPUT_DIR
python converter/convert.py --resplit <OUTPUT_DIR> --out <CORPUS_ROOT> --pdf-root <BOOKS_DIR>
python <ask-your-library>/server/corpus.py add <CORPUS_ROOT>                 # run from ask-your-library's env
```

`--resplit` is not optional here: it writes the per-book `_manifest.json` and
the root `corpus.json` (format `t2n-corpus/1`,
[spec](https://github.com/drpwchen/ask-your-library/blob/main/docs/corpus-format.md))
that `corpus.py` checks. `corpus.py add` validates, indexes, audits and
prints the env line to set. `python converter/post_convert.py --index
--corpus <CORPUS_ROOT>` does the last two lines incrementally (only new or
re-converted books are re-split) and runs `corpus.py add` when
`VAULT_SEARCH_DIR` points at the ask-your-library checkout; with nothing set it
prints `[skip]` and the command, so a green run does *not* mean an index
exists. Guidelines, prerequisites and verification:
[`docs/setup-details.md`](docs/setup-details.md#step-5--hand-the-corpus-to-ask-your-library).

## Step 6: (Profiles B/C) Install the two Claude Code skills

```bash
mkdir -p ~/.claude/skills
cp -r skills/textbook-to-md ~/.claude/skills/
cp -r skills/figure-remap ~/.claude/skills/
```

Then replace every `{REPO}` placeholder in each copied `SKILL.md` with the
absolute path of this clone. Details:
[`docs/setup-details.md`](docs/setup-details.md#step-6--placeholder-substitution-after-copying-the-skills).

## Step 7: (Profiles B/C) Run the note-writing workflow

`workflows/note-writing.md` is the full specification; adapt its phases 2/3
to the user's actual enrichment tools. Walk through `examples/example-note.md`
with the user before the first real note, never hand-crop figures (use the
`figure-remap` skill), and merge by deconstruct-and-reslot, not append. The
step-by-step list: [`docs/setup-details.md`](docs/setup-details.md#step-7--running-the-note-writing-workflow).

## Step 8: (Profiles B/C, optional but recommended) Verify the note-format hook

An optional pre-write format-lint hook (this repo ships none); what it should
catch is in [`docs/setup-details.md`](docs/setup-details.md#step-8--the-note-format-hook) and in
`workflows/note-writing.md`'s self-check section.

## Token guardrails

These apply any time you're working with the converted corpus, not just
during initial setup:

- When consulting the converted corpus, **always grep or semantic-search
  first**, then `Read` only a bounded window (roughly ≤150 lines) around the
  hit. Never `Read` an entire `full_text.md` or a whole chapter file into
  context — a single book can be well over 1M tokens, and reading it whole
  defeats the entire point of converting to a searchable corpus.
- Frontier-vision figure escalation (ladder step 5 in `docs/ocr-ladder.md`)
  is per-figure opt-in and capped — at most a small fixed number of
  escalations per note. Prefer leaving a `<!-- TODO: figure -->` placeholder
  over a third escalation on the same note, and never batch-escalate a set
  of figures to frontier vision at once.

## Ongoing use

Once set up, the day-to-day loop is:
1. New textbook arrives → `converter/convert.py` (or batch-dir for several
   at once)
2. User wants a note on a topic → run the note-writing workflow
3. Note needs a figure mid-conversation, outside the full workflow → call
   `figure_remap.py extract` directly per `skills/figure-remap/SKILL.md`

## Guardrails — read before touching the filesystem

- Never delete or move files outside this repo and the user's configured
  vault/attachments directory without explicit confirmation.
- Never use raw destructive file operations (recursive delete, force move)
  on the user's attachments folder from within an automated sub-step —
  destructive operations belong to the orchestrating agent only, with the
  user able to see and approve what's being removed.
- Treat the user's source PDFs as read-only source-of-truth; all pipeline
  output should be a fresh copy, never an in-place edit of the original.
- If a step in this file references a script or path that doesn't exist yet
  in this repo, say so explicitly rather than guessing at its interface.
