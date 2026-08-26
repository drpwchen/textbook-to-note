# chapter-index-root-and-sequence

## Why

Two reports from an outside user (#17, #15) describe the same end symptom — `textbook_ref_lint.py`
returns UNVERIFIABLE (exit 3) for citations into books that are cleanly converted on disk — from
two independent causes.

**Cause 1 — the corpus is never reached (#17).** `citations/textbook_chapter_index.py:36` and
`citations/textbook_ref_lint.py:242` both resolve their root to `Path(".")` when neither
`--textbook-dir` nor `TEXTBOOK_DIR` is given, and `workflows/note-writing.md:465` documents
exactly that bare invocation. Two measured outcomes:

- Run from the repo root, `--rebuild` scans `citations/`, `converter/`, `docs/`, … as if each
  were a book, writes a 0-book-mapped index, and prints `OK`. Every later lint returns
  UNVERIFIABLE for every citation into every book, with nothing anywhere saying the wrong tree
  was scanned.
- Run from `~`, `build()`'s unguarded `d.iterdir()` hits the `Application Data` junction and the
  rebuild dies with `PermissionError: [WinError 5]`, writing nothing.

**Cause 2 — the converter's own output is unmappable, and the tool cannot say why (#15).**
`converter/convert.py:1764,1804` names split chapters `ch{ch_num:02d}_{safe_title}.md` where
`ch_num = idx + 1` is a **sequence counter over detected split points** (level-1 *and* level-2
bookmarks, or `detect_chapters()` hits) — not the book's printed chapter number. No strategy in
`textbook_chapter_index.py` matches that filename shape, so every book converted through
`--batch-dir` records `strategy: "none"` and every citation into it is UNVERIFIABLE with the
message "no machine-readable chapter structure (sliced by page or by section)" — which is both
inaccurate (the book *is* chapter-split) and actionless.

#15 proposes reading `chNN` as the chapter number. That is rejected: `ch_num` is a file
sequence, so it would produce a confidently wrong chapter table for any book whose bookmarks
include sub-sections — the exact failure `textbook_ref_lint.py`'s own `FILE_INDEX` verdict was
written to catch, and a direct violation of the index's "refuses to guess" contract.

## What changes

- A run that does not name a corpus resolves to `{repo}/output` when that directory exists, and
  otherwise fails with a message naming both ways to set it. Bare cwd is never scanned.
- `--rebuild` refuses to write an index in which no book mapped, and exits non-zero saying which
  root it scanned. `--allow-empty` overrides.
- An unreadable subdirectory is skipped and counted in the summary instead of aborting the run.
- A book whose files carry the converter's `chNN_Title.md` sequence naming is recorded as
  `strategy: "seq"` with its sequence→title table, distinct from `strategy: "none"`. A citation
  into such a book is still UNVERIFIABLE, but now says the book is sequence-split, names the file
  that sequence number points at, and names the file that fixes it.
- A new optional, human-written `_chapter_index.chapters.json` pins a book's real chapter table.
  A pinned book resolves normally.
- `workflows/note-writing.md`'s self-check block shows `--textbook-dir` explicitly.

## Not doing

- Not reading `chNN` as a chapter number. See Why.
- Not auto-populating `_chapter_index.chapters.json`. Like `_chapter_index.defaults.json` beside
  it, deciding a book's real chapter numbers is a judgment call and stays human-written.
- Not changing `converter/convert.py`'s naming or adding a chapter manifest at split time. That
  would only help books converted after the change and is a separate proposal.
- Not touching strategy A (`Ch12_Title.md`, capital `Ch`), which stays case-sensitive so a
  hand-organised corpus keeps its meaning and the converter's lowercase output cannot fall into
  it.
- Not changing any verdict other than the wording and specificity of UNVERIFIABLE for `seq`
  books. No citation that resolves today stops resolving.

## Risk

The root-default change is behaviour-visible: a user who deliberately ran from inside their
corpus directory with no flag now gets `{repo}/output` instead of cwd. That is the intended
correction — the silent-cwd scan is the bug — and the failure is loud (a named root in the
summary line, a refusal when nothing maps), not silent.

`strategy: "seq"` adds a value that existing consumers do not know. `textbook_ref_lint.py` is the
only consumer and is updated in the same change; a stale `_chapter_index.json` from before this
change contains no `seq` entries and still loads.

No default-ON extraction behaviour changes, so no corpus measurement is required.

## Affected specs

- `openspec/specs/note-workflow/spec.md` — added (three requirements)
