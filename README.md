# textbook-to-note

[![CI](https://github.com/drpwchen/textbook-to-note/actions/workflows/ci.yml/badge.svg)](https://github.com/drpwchen/textbook-to-note/actions/workflows/ci.yml)

Turn your own PDF textbooks into an AI-searchable knowledge base and structured, fully-cited notes — figures included. A local-first pipeline that spends (almost) zero LLM tokens on the heavy lifting and reserves the frontier model for the one thing it's uniquely good at: synthesizing a note you can actually learn from.

[繁體中文說明 → README.zh-TW.md](README.zh-TW.md)

## Why I built this

I've loved taking notes since med school, and over the years I've built up thousands of them — but I can no longer keep every one at the same quality by hand. In an age of information overload, trustworthy high-quality sources become the precious thing, and textbooks are among the best; yet my specialty alone has 40+ designated reference books, with a single concept scattered across chapters in many of them. Reading them all cover to cover just isn't realistic. LLMs are great at long context, but you can't dump hundreds of books on one at once — so the real unlock is pairing good search and a database with teaching the AI my own note-making process, so it produces grounded, structured notes I only have to absorb.

![A note template on the left, the note the pipeline produced from it on the right](docs/assets/template-vs-note.png)

*Left: one of the templates in [`templates/`](templates/). Right: a real note in my vault, written against it — every claim traceable, every section where I expect it.*

## Which path are you on?

Not everyone wants the whole pipeline, and the parts stack in one direction — each profile
below is the one above it plus one more thing. Pick the smallest one that solves your
problem; you can move up later without redoing anything.

| Profile | What you get | What you set up |
|---|---|---|
| **A · Converter only** | Your PDFs/EPUBs as clean, greppable markdown with page markers, tables, and figure-reference markers. Search with `grep`. | `pip install -r requirements.txt`, then `converter/convert.py`. Nothing else — no GPU, no ollama, no index. |
| **B · A + the note workflow** | An AI writes structured, per-claim-cited notes from that corpus, with figures extracted through a QC gate. | A, plus the two skills in [`skills/`](skills/) and [`workflows/note-writing.md`](workflows/note-writing.md). |
| **C · B + semantic search** | Cross-book retrieval by meaning rather than keyword — worth it once "which book was that in?" stops being obvious. | B, plus an indexer: the companion repo [vault-search](https://github.com/drpwchen/vault-search), a local embedding model, and `INDEXER_SCRIPT`. |

**This repo ships no indexer.** Profile C's semantic search comes from vault-search (or any
indexer exposing the same `--incremental` / `--book <name>` CLI); `post_convert.py --index`
is only the hook that calls it. Profile A is a fully supported end state, not a degraded one —
for a few dozen books, grep over the converted markdown is genuinely enough.

If you're having an AI set this up, tell it which profile you want; [`AGENTS.md`](AGENTS.md)
asks this first and then skips the steps your profile doesn't need.

## Why this is hard (and why naive approaches fail)

Feeding a raw PDF to a frontier model seems simple until you hit the real problems:

- **Cost & latency** — a 600-page book is 1-2M tokens. Re-reading it for every question is untenable.
- **Silent data loss** — scanned pages and broken font encodings produce garbage that the model quietly skips. You never learn your note is missing half the chapter.
- **Scrambled text** — most textbooks are two-column. Default PDF text extraction interleaves the columns, and a naive "is this garbled?" check *passes* the shuffled output because the characters themselves are fine.
- **Figures vanish** — the anatomy diagram, the classification table, the treatment algorithm: often the most valuable part of a chapter, and text extraction drops all of it.
- **Ungrounded output** — an AI writing notes from memory hallucinates. Every claim needs to trace back to a source.

This pipeline treats each of those as a distinct engineering problem with a deliberate solution, organized as five stages.

## Philosophy

- **Local-first, token-frugal** — the expensive AI is reserved for synthesis, never mechanical page-by-page reading.
- **Deterministic gates over AI vibes** — every figure crop and OCR page passes rule-based QC before an AI is allowed to judge it, and thresholds are *never* tuned just to make a failing case pass.
- **Citations or it didn't happen** — every claim traces to book + chapter; AI-inferred additions are flagged.

## The five stages

### 1 · Convert — PDF/EPUB → clean markdown, 0 tokens

PyMuPDF text extraction at ~130 ms/page. Two non-obvious pieces:

- **Silent-failure detection** — a native text layer can *lie* (CID/Identity-H fonts, PUA codepoints). We score glyph-garble ratio, character density, and font risk to catch pages that "extracted fine" but didn't, and route only those to OCR.
- **Column-aware reading order** — line-level column clustering reconstructs true reading order on two-column layouts, with an exact-fallback path so single-column pages are byte-identical to the trivial extraction. (`T2N_COLUMN_SORT=0` to disable.)
- **Table pass, gated** — `pdfplumber`'s table detection is the slowest part of conversion. We gate it behind a cheap `fitz` pre-check (ruling-line signature incl. three-line tables, plus multilingual "Table"/表 keywords), so table-sparse books convert **~3.4× faster** without missing tables. (`T2N_TABLE_GATE=0` to disable.)
- **Cross-page table merge** — textbook tables often run past a page break. Opt in with `T2N_TABLE_MERGE=1` to stitch a table that ends near a page bottom to a geometrically-matching table at the top of the next page (same column count / x-edges, no intervening heading), deduping a repeated header row and leaving a `<!-- table continues from page N -->` trace comment. Same exact-fallback discipline: default OFF ⇒ byte-identical output.
- **Page-frame pseudo-table rejection** — a page's decoration (a content frame plus the rule under the running header) gives `pdfplumber` enough intersecting edges to "find" a table covering the whole page body, with 1 column and every word on the page dumped into a single cell. That output is worse than a missing table: a real multi-column table arrives with its columns interleaved line by line, carrying its caption and every value, so it reads as clean citable data while the row↔column binding is destroyed. Candidates of one column whose largest cell exceeds 500 characters, or whose bbox alone covers half the page, are dropped and replaced by a `<!-- ⚠️ page-frame pseudo-table rejected on page N -->` comment — the page's own prose already carries the text. Measured at **9.9% of extracted tables across 128 books**; on a hand-read sample of 34 pages from 10 books, 28 were genuine defects and the 6 legitimate boxed lists lost nothing (100% of their tokens are present in the page prose). Default **ON** — this corrects wrong output; `T2N_TABLE_FRAME_REJECT=0` restores the old behaviour.
- **Out-of-band review queue for misbinding** — the QC gate catches *structural* damage, but it cannot see a **misbinding**: a value merged into the **wrong row** of an otherwise clean grid (a corticosteroid dose fused into the next drug's row on a table's continuation page, a lab value's interpretation on the wrong condition). Nothing is structurally off, so the gate passes it — and it reads as clean, citable data. There's no safe automatic fix (the right value-to-row assignment is exactly what's in doubt), so instead of guessing we **flag the high-risk subset for a second opinion**: continuation-page tables (where orphan-row fusion lives) and dosage/threshold tables (`mg`, `mL`, `mg/kg`, `IU`, dose ranges — a wrong value here is the worst output the tool can produce). Each gets a `<!-- ⚠️ table needs out-of-band review … verify against PDF page N -->` marker and a queue entry; the second opinion is a bring-your-own-model pass (fast text model first, vision as escalation). In testing, ~**1 in 6** continuation×dose tables carried a high-severity misbinding versus ~0 in a random table sample — the danger concentrates exactly where both triggers meet. Opt in with `T2N_REVIEW_QUEUE=1` (default OFF; recommended ON for clinical corpora). Details, the failure mode, and a hazard-primed verifier prompt: [`docs/table-review.md`](docs/table-review.md).
- **Spanned category-header collapse** — dense drug×attribute grids (rehab pharmacology references are the worst case) carry section headers like "Corticosteroids: Used to reduce inflammation." that the extractor broadcasts across *every* column, producing a phantom full-width data row that shifts the alignment of the real rows. A genuine data row never repeats one sentence-length string across all its columns, so the signature is unambiguous: any row whose ≥3 non-empty cells are the identical ≥15-char string is re-cast as a single header cell. Structural only — it never moves a value between rows, so it cannot create a misbinding. Measured on one book: **130 of 232 tables (56%)** carry the pattern. Default **ON** — corrects wrong output; `T2N_TABLE_HEADER_COLLAPSE=0` restores byte-identical output.
- **Whole-book table-failure warning** — table loss is bimodal: a book either extracts tables fine or silently loses every one. If `pdfplumber` parses 0 pages while `fitz` opens the file, or a book yields 0 tables despite ≥10 table captions, the conversion report and the markdown itself get a loud warning instead of nothing at all. Detection only — extraction is unchanged. Fires on 22 of 34 zero-table books in our corpus and on none of the 226 that extract tables. **Partial loss is caught too**: a book that recovers some tables but fewer than `BOOK_PARTIAL_TABLE_RATIO` (0.20) per caption is warned about as well — measured over 171 converted books the median tables:captions ratio is 0.99 and 42 books sit below 0.20, including large references the zero-rule never flagged. (`T2N_BOOK_TABLE_CHECK=0` to disable.)
- **Book-level table-reliability banner** — some books are just hostile to table extraction: when a large fraction of a book's tables trip a structural QC flag, the individual `⚠️` markers don't convey that the *whole book*'s tables are unreliable. The QC gate sees structure, not value-on-wrong-row misbinding, so a high flag rate is a proxy for "don't trust any table here without the PDF". The trigger is **content loss**, not any QC flag: measured across a 6-book pilot, any-flag rates cluster at 39-64% for every dense clinical book, so they separate nothing, while content-retention (text on the page that reached no cell) runs 40/27/17/12/2/0%. If ≥`BOOK_CONTENT_LOSS_RATE` (25%) of a book's tables (given ≥10 of them) lost content, a `> [!caution]` banner is hung at the top of the markdown telling the downstream LLM to verify every table against the source PDF, and `reliability_flagged` / `content_loss_rate` / `flag_rate` land in the per-book stats. Detection only.

Scanned and font-broken books fall through an **OCR ladder** — Surya → PaddleOCR-VL → local vision model → frontier vision as the true last resort. The *detection* signals are per-page (character density, font-risk flags, domain-pattern miss); the *routing* decision, as shipped, is **per book**: if a PDF trips the check during a `--batch-dir` run, the whole file goes to OCR rather than a page at a time. Per-page routing is future work, so don't read the ladder as already mixing engines inside one book. A **reference OCR adapter ships** (`converter/surya_adapter.py`, targeting Surya 0.22.x) behind a documented interface, so a different engine can take that rung without touching the converter — see [`docs/surya-adapter.md`](docs/surya-adapter.md), which also carries the server memory caps you want set *before* your first run. See [`docs/ocr-ladder.md`](docs/ocr-ladder.md) for the ladder itself, including a **hardware-tier model-selection table** (no-GPU / Apple Silicon / NVIDIA 8GB / 16GB+) so you pick an engine and ollama model that actually fit your VRAM instead of OOMing mid-book.

### 2 · Chunk — into semantically searchable units

A table of contents is too coarse (one topic spans many chapters); manual tags don't scale and can't be granular enough. The answer is **semantic embedding**. But chunking is a design decision, not a fixed window: split too small and you lose context, too large and you dilute meaning. We chunk **by heading structure** and carry each chunk's parent-section context, so every retrieved unit is a self-contained concept with its provenance attached.

### 3 · Retrieve — find the right book among dozens

Cross-book search runs on the same engine as its sibling project [**vault-search**](https://github.com/drpwchen/vault-search) — local LanceDB + `bge-m3` embeddings, nothing leaves your machine. On top of plain similarity we add **source weighting**: boost your most-trusted references (exam-designated texts, official society textbooks) and down-weight by edition age, so on any topic the AI reaches for the source *you* trust first.

### 4 · Write — a note-writing algorithm, not a prompt

Note quality comes from the workflow, not the model:

- **Blind draft first** — the AI researches the topic fully from the textbook corpus *before* looking at any existing note, so an old note's structure and content can't bias the new draft. Merge comes last.
- **Template-driven extraction** — each topic type has a fixed template. This is load-bearing: it tells the AI exactly what to hunt for in the source, and it means every note has the same predictable shape so *you* read faster. Sections like a leading Summary and a Management-algorithm block exist specifically to aid comprehension, not just to hold data. The exact templates used daily are included under [`templates/`](templates/), in both the original 繁體中文 and an English translation.
- **Cite or it didn't happen** — every claim carries book + chapter; anything the AI adds from its own knowledge is explicitly flagged as inferred.
- **Non-destructive merge** — replacing an existing note is gated on your vault being under version control; otherwise it writes a draft beside the original. It will not silently overwrite your hand-written notes.

See [`workflows/note-writing.md`](workflows/note-writing.md).

### 5 · Extract figures — the hard one

Every book lays figures out differently, so there's no single crop rule. We use a **general geometric-matching method** (a caption owns the nearest assignable raster) behind a **deterministic QC gate**: whitespace-fill, text-bleed, and OCR-long-line checks all run *before* any AI is allowed to judge the crop — and the gate hard-fails rather than guessing, so a wrong page yields a refusal, not a wrong figure. Everything runs locally, token-frugally.

A crop that passes the gate then meets a second, separate question: *is this a figure at all?* A **deterministic junk pre-gate** (`figures/pregate.py`) kills chapter-title banners and blank crops on page metadata alone — no model, no per-book tuning — and tells the caller to skip that figure rather than retry it. Its calibration requirement is zero false kills on good figures, because a killed figure has no second chance; measured 0 on both of its evaluation sets.

When a specific book extracts wrong, you fix *that book's* logic once, and every later extraction from it is correct. This stage has been through many iterations and is still **experimental** — it doesn't yet handle every book, and improvement PRs are very welcome. See [`figures/CALIBRATION.md`](figures/CALIBRATION.md).

### Bonus · Pluggable evidence enrichment

The note workflow has optional hook points to enrich a draft from external sources — a clinical-evidence API, a regulations/coverage database, a literature search. They live outside this repo to bound its scope; the workflow doc marks exactly where they slot in so you can wire in your own domain's sources.

The clinical-evidence hook I use daily is published separately as [**openevidence-tools**](https://github.com/drpwchen/openevidence-tools) — an OpenEvidence ask tool paired with a verifier that checks the returned citations. It takes [htlin222/openevidence-mcp](https://github.com/htlin222/openevidence-mcp) as its reference and inspiration; it is not a fork. **Always run the verify step** — cited sources can be wrong.

## Designed to be deployed by an AI

You're probably here to have *your* AI set this up — that's the intended path:

> Point Claude Code (or any capable coding agent) at this repo and say:
> **"Read AGENTS.md and set this up for me."**

[`AGENTS.md`](AGENTS.md) is written for the agent: dependency install, configuration, converting a first book, installing the two Claude Code skills, running the note workflow, plus token guardrails so a naive agent doesn't burn a million tokens reading a whole book into context.

## Repo layout

```
converter/    PDF/EPUB → markdown (convert.py — silent-failure + column-sort + table-gate)
figures/      figure extraction + deterministic QC gate + junk pre-gate (figure_remap.py entrypoint)
citations/    chapter-reference lint — proves every "Author Ch.N" points at a real chapter
skills/       drop-in Claude Code skill definitions (textbook-to-md, figure-remap)
workflows/    the note-writing algorithm (adapt to your own note system)
docs/         architecture, OCR ladder + hardware tiers, OCR adapter contract
examples/     a sample output note showing the target format
shared/       env-driven configuration (config.py)
```

## Requirements

- Python 3.10+, `pip install -r requirements.txt`
- **CPU-only is a first-class path** for born-digital PDFs (the common case) — no GPU needed
- Optional, for scanned books and figure QC: an NVIDIA GPU or Apple Silicon + [Surya OCR](https://github.com/VikParuchuri/surya), [ollama](https://ollama.com) with a small vision model and `bge-m3` for embeddings — all local, nothing leaves your machine. See the hardware-tier table in [`docs/ocr-ladder.md`](docs/ocr-ladder.md).
- Tested on Windows 11 and macOS; Windows-specific gotchas are handled in code (cp950 subprocess decoding, atomic-ish path ops)

## Bring your own books

This tool ships **no textbook content**. It operates on PDFs you already own — purchased ebooks, institutional-access downloads, open-licensed texts ([OpenStax](https://openstax.org)), or scans of your own paper books where your local law permits. Respect your books' licenses.

## Related

- [**vault-search**](https://github.com/drpwchen/vault-search) — the local semantic-search engine stage 3 builds on.
- [**openevidence-tools**](https://github.com/drpwchen/openevidence-tools) — the OpenEvidence ask + verify pair that plugs into the Bonus stage.
- [**note-supplement**](https://github.com/drpwchen/note-supplement) — the other direction: merges new source material into notes you *already* wrote, with conflict detection and tier-gated writes.

## License

MIT © Po-Wei Chen ([drpwchen](https://github.com/drpwchen))

### A note on dependency licenses

This project's own code is MIT. Two dependencies carry stronger terms you should be aware of if
you redistribute or offer this as a network service:

- **PyMuPDF** (`fitz`), a required dependency, is **AGPL-3.0** (or a commercial license from
  Artifex). It is the core PDF reader and is not optional. If you build a closed-source product
  or a hosted service on top of this tool, evaluate that obligation for yourself.
- **Docling**, the optional table engine (`T2N_DOCLING=1`), is MIT end-to-end (`docling`,
  `docling-core`, `docling-ibm-models`, `docling-parse`). It is **not** installed by default and
  is not in `requirements.txt`; it runs in its own virtual environment and is reached by
  subprocess, so it is a pluggable tool rather than a linked dependency.

This is disclosure, not legal advice — licenses change, so verify against the versions you
actually install.

---

## 🌱 Start here if you're new to AI agents ／ AI agent 新手起點

This tool is one piece of my personal AI workflow. If you want to learn how to use AI agents like Claude Code from zero (no programming background needed), I wrote a beginner series (in Traditional Chinese):

這個工具是我個人 AI 工作流的一部分。想從零開始學怎麼用 Claude Code 這類 AI agent（不需要程式背景），可以從我的入門系列開始：

1. [從零開始：安裝、看懂 GitHub、跑起你的第一個工具](https://drpwchen.com/posts/getting-started/)
2. [怎麼跟 AI agent 講話：心法、元技能與規則檔](https://drpwchen.com/posts/talking-to-agents/)
3. [自動化流程不是設計出來的，是長出來的](https://drpwchen.com/posts/growing-your-workflow/)

Full map of my tools and posts ／ 所有工具與文章的全貌 → [drpwchen.com/map](https://drpwchen.com/map/)

## Support 支持

覺得這個工具有幫助嗎？歡迎[請我喝飲料](https://drpwchen.com/support/) 🧋
If this tool helped you, you can [buy me a drink](https://drpwchen.com/en/support/).
