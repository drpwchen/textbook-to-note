# held-book-fix-request

Tier 1. Maintainer standing instruction (2026-11-09): drive to the output contract + validator.
Requested by the maintainer 2026-11-14, reviewing `split-acceptance-gate` before it shipped:
"一本書擋著所有書進來" is wrong — a broken book should be held alone and handed to an AI to fix.

## Why

`split-acceptance-gate` (unpublished 0.8.3) stops the whole ask-your-library hand-off while any
book in the corpus fails validation, because `corpus.py add` takes a whole corpus root. One bad
new book therefore keeps every other new book out of search until a person fixes it, and what
the person gets is a hint list on stdout, not a task.

## What changes

1. A held book's folder moves to `<corpus>/_held/<book>/`. ask-your-library skips `_`-folders,
   so the book is out of the index and the hand-off runs for everything else. The run still
   lists it under `[FAIL]` and exits 1 (after the hand-off).
2. Next to it, `_held/<book>/_fix_request.md`: a self-contained brief an agent can execute —
   the failing checks with the validator's details, the config keys per check, the exact
   commands, the done criterion (validator passes, chapter check no `[WARN]`), and the rules
   (every config entry needs a `reason`; `*_ok` / `*_skip` keys only for a real cause; a book
   whose text itself is broken is re-converted, not papered over with config).
3. The book comes back by itself: a held book is planned for resplit again when its markdown or
   `_split_config.json` is newer than the held manifest; once its report entry is valid, the
   held copy is removed.

## Not doing

- Calling an AI from this repo. The brief is the interface; the maintainer's own wrapper turns
  it into a to-do card (private, outside this repo).
- Holding on a chapter-check `[WARN]` (stays report-only).
- Changing validator checks or the split config format.

## Risk

A held book disappears from search until fixed (before 0.8.3 it was searchable but wrongly
split; in the 0.8.3 draft nothing new was searchable). Removing a held copy must never follow
the figure links inside it: links are unlinked first, then the folder removed. Measured: the
maintainer's corpus has 0 held books after the Weiss fix, so no book moves on upgrade.

## Affected specs

- `openspec/specs/conversion/spec.md` — modified: "An invalid split holds back the hand-off"
  (replaced by "An invalid split is held alone, with a fix request"); "`post_convert.py` keeps
  the corpus in step" (plan rule for held books, hand-off condition).
