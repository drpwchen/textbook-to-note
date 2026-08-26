# note-workflow — corpus → a note you can learn from

## Purpose

The algorithm an AI agent follows to turn the converted corpus into a structured,
fully-cited note and merge it into an existing notes vault. Specified in
`workflows/note-writing.md`; agent-facing setup and guardrails in `AGENTS.md`.

Note quality comes from this workflow, not from the model. Most requirements here constrain
*ordering* and *sourcing*, because those are what a capable model will otherwise optimise
away.

## Requirements

### Requirement: The draft is blind to any existing note

The system SHALL complete phases 1 through 3.5 using the textbook corpus alone, and SHALL
first read an existing note on the same topic in phase 4.

#### Scenario: No vault read before phase 4
- **GIVEN** a topic that already has a note in the user's vault
- **WHEN** the workflow runs phases 1–3.5
- **THEN** no search or read of the vault has occurred

#### Scenario: An early vault search is a defect
- **GIVEN** an agent that searches the vault during phase 1
- **WHEN** this is observed
- **THEN** it is reported as a bug against this workflow, not accepted as a shortcut

**Negative constraints**
- The agent MUST NOT use an existing note to decide the new note's section structure. That
  structure is often accidental — built up ad hoc over years — and inheriting it is exactly
  the failure this ordering prevents.

### Requirement: Structure is chosen before content is written

The system SHALL detect the topic type, select the matching template, and emit a section
skeleton with one-line placeholders (phase 1.5) before expanding any section.

#### Scenario: Skeleton precedes expansion
- **GIVEN** a topic mapped to a template
- **WHEN** phase 1.5 completes
- **THEN** the full heading tree exists with placeholders and no section has been expanded

### Requirement: Every claim carries a primary source

The system SHALL cite book plus chapter for each claim, and SHALL explicitly mark any claim
that came from the model's own knowledge.

#### Scenario: Model-added fact is flagged
- **GIVEN** a fact the agent supplies from its own knowledge
- **WHEN** it is written into the draft
- **THEN** it carries an inferred marker distinguishing it from sourced claims

#### Scenario: Chapter references are linted
- **GIVEN** a completed draft containing "Author Ch.N" references
- **WHEN** the self-check runs
- **THEN** `citations/textbook_ref_lint.py` has been run over the draft and every reference
  either resolves or was confirmed by hand

#### Scenario: Conflicting guidelines are both presented
- **GIVEN** two primary sources that disagree on the same point
- **WHEN** the draft covers that point
- **THEN** both positions appear, attributed; the losing one is not dropped

**Negative constraints**
- Non-primary material (co-authored study notes, exam slide decks, informal summaries) MUST
  NOT enter the draft or the citation list.

### Requirement: Merging into an existing note is non-destructive by default

The system SHALL verify the target vault is under version control before replacing an
existing note, and SHALL otherwise write a draft alongside the original.

#### Scenario: Version control is a checked precondition
- **GIVEN** a target vault
- **WHEN** the workflow prepares to replace an existing note
- **THEN** `git status` has been run in the vault root and succeeded

#### Scenario: Unversioned vault gets a side-by-side draft
- **GIVEN** a vault not under version control
- **WHEN** an existing note would be replaced
- **THEN** the output is written as `<note> (draft).md` unless the user explicitly confirms
  replacement

#### Scenario: Substantial hand-written content pauses for approval
- **GIVEN** an existing note well over ~50 lines containing first-hand observations
- **WHEN** the merge reaches it
- **THEN** the workflow pauses for per-item approval before writing

**Negative constraints**
- Existing content MUST be deconstructed and re-slotted into the new skeleton, never
  absorbed as whole paragraphs and never appended to the end.
- The workflow MUST NOT silently overwrite a hand-written note.

### Requirement: Corpus reads are bounded

The system SHALL search the corpus first and read only a bounded window around each hit,
because a single converted book can exceed one million tokens.

#### Scenario: Read follows a search hit
- **GIVEN** a question about a topic in the corpus
- **WHEN** the agent consults a book
- **THEN** it greps or semantic-searches first and reads at most ~150 lines around the hit

#### Scenario: Whole-file read is refused
- **GIVEN** a converted `full_text.md`
- **WHEN** the agent considers reading it entirely
- **THEN** it does not — reading it whole defeats the purpose of converting to a searchable
  corpus

**Negative constraints**
- Frontier-vision figure escalation MUST be per-figure opt-in and capped at a small fixed
  number per note. Prefer a `<!-- TODO: figure -->` placeholder over a third escalation on
  the same note.
- A set of figures MUST NOT be batch-escalated to frontier vision at once.

### Requirement: The workflow runs to completion without per-phase confirmation

The system SHALL run phases 1 through 5 in one continuous pass, pausing only where the
workflow explicitly calls for a judgment call.

#### Scenario: No pause between phases
- **GIVEN** a note-writing run with no ambiguity
- **WHEN** phase 1 completes
- **THEN** phase 1.5 begins without asking the user to confirm

### Requirement: The agent never touches the filesystem outside its sandbox

The system SHALL confine writes to this repo and the user's configured vault/attachments
directory, and SHALL leave destructive operations to the orchestrating agent.

#### Scenario: Out-of-scope delete requires confirmation
- **GIVEN** a file outside the repo and the configured vault
- **WHEN** a step would delete or move it
- **THEN** it does not proceed without explicit user confirmation

**Negative constraints**
- An automated sub-step MUST NOT run a recursive delete or force move against the user's
  attachments folder.
- A step MUST NOT guess at the interface of a script or path that does not exist in the
  repo; it says so instead.

### Requirement: The chapter index never scans an unnamed directory

The system SHALL resolve its corpus root from `--textbook-dir`, then `TEXTBOOK_DIR`, then
`{repo}/output` if that directory exists, and SHALL fail rather than scan the current working
directory.

#### Scenario: No corpus named, repo output exists
- **GIVEN** neither `--textbook-dir` nor `TEXTBOOK_DIR` is set, and `{repo}/output` is a directory
- **WHEN** `textbook_chapter_index.py --rebuild` runs from any working directory
- **THEN** the printed summary line names `{repo}/output` as the scanned root

#### Scenario: No corpus named and no repo output
- **GIVEN** neither `--textbook-dir` nor `TEXTBOOK_DIR` is set and `{repo}/output` does not exist
- **WHEN** `textbook_chapter_index.py --rebuild` or `textbook_ref_lint.py` runs
- **THEN** it exits 2 with a message naming both `--textbook-dir` and `TEXTBOOK_DIR`, and writes
  no index file

**Negative constraints**
- The resolved root MUST NOT be the process working directory unless the user named it.
- A failed resolution MUST NOT write, truncate, or delete `_chapter_index.json`.

### Requirement: A rebuild that recognises no book is refused

The system SHALL refuse to write an index in which no directory was recognised as a converted
book (every entry `strategy: "none"`), because that is what scanning the wrong directory looks
like.

#### Scenario: Wrong directory scanned
- **GIVEN** a root whose subdirectories are not converted books
- **WHEN** `--rebuild` runs
- **THEN** it exits 2, prints `REFUSED` with the scanned root and the count of directories
  examined, and no index file is written

#### Scenario: A genuinely unmappable corpus can still be indexed
- **GIVEN** the same root and `--allow-empty`
- **WHEN** `--rebuild` runs
- **THEN** the index is written and the exit code is 0

#### Scenario: An unreadable subdirectory does not abort the scan
- **GIVEN** a root containing a subdirectory that raises `PermissionError` on listing (a Windows
  user-profile junction is the common case)
- **WHEN** `--rebuild` runs
- **THEN** that directory is skipped, the remaining books are indexed, the index is written, and
  the summary line reports the skipped count

**Negative constraints**
- A single unreadable directory MUST NOT raise out of `build()`.
- The refusal MUST NOT be silenced by tuning what counts as "recognised". `--allow-empty` is the
  only override.
- A book recognised as `seq` counts as recognised: a corpus converted entirely by this repo's own
  converter must still index.

### Requirement: A sequence-split book is named as such, never guessed at

The system SHALL record a book whose markdown files are named `chNN_Title.md` (the converter's
own split naming, where `NN` is a file sequence and not the printed chapter number) as
`strategy: "seq"` with its sequence→title table, and SHALL report a citation into it as
UNVERIFIABLE naming the file that sequence number points at.

The trigger is at least 3 files matching `^ch(\d+)[_ ](.+)\.md$` with a non-zero number, tried
only after every chapter-number strategy has failed.

#### Scenario: Converter-split book is distinguished from a structureless one
- **GIVEN** a book directory holding `ch01_Introduction.md`, `ch02_Methods.md`, `ch03_Results.md`
- **WHEN** the index is rebuilt
- **THEN** that book's `strategy` is `"seq"`, its `chapters` is empty, and its `sequence` maps
  `"2"` to `"Methods"`

#### Scenario: Citing into a sequence-split book says what to do about it
- **GIVEN** an index in which `Alpha_Handbook_7e_2021` has `strategy: "seq"`
- **WHEN** `Alpha Ch.2` is linted
- **THEN** the verdict is UNVERIFIABLE and the detail names the book, states it was split by the
  converter's file sequence, and names `_chapter_index.chapters.json`

#### Scenario: A pinned chapter table makes the book checkable
- **GIVEN** `_chapter_index.chapters.json` containing `{"Alpha_Handbook_7e_2021": {"2": "Middles"}}`
- **WHEN** the index is rebuilt and `Alpha Ch.2` is linted
- **THEN** that book's `strategy` is `"pinned"` and the verdict is OK with the title `Middles`

**Negative constraints**
- The sequence number MUST NOT be recorded as, or reported as, a chapter number. `chapters` stays
  empty for a `seq` book.
- A `seq` book MUST NOT produce an OK, BAD_CHAPTER, or FILE_INDEX verdict. The only chapter fact
  available is the one the user pins.
- `_chapter_index.chapters.json` MUST NOT be written or modified by any code path.
- Strategy A (`^Ch(\d+)[_ ](.+)\.md$`) MUST stay case-sensitive, so the converter's lowercase
  `chNN` output cannot be read as a chapter number.
