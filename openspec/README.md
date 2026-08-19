# openspec/ — the behaviour contract for this repo

This directory is the **authoritative statement of what this tool does**. Code implements
it; docs explain it; this describes it in a form a machine and a human can both check.

It follows the [OpenSpec](https://github.com/Fission-AI/OpenSpec) file convention. **No
OpenSpec CLI is required** — this repo uses the file format only, driven by a skill and a
hook (see "Tooling" below).

## Layout

```
openspec/
  specs/<domain>/spec.md        current behaviour — the single source of truth
  changes/<change-name>/        one in-flight change
    proposal.md                 why, what, and an explicit "not doing" list
    tasks.md                    the implementation checklist
    specs/<domain>/spec.md      the DELTA only (ADDED / MODIFIED / REMOVED)
  changes/archive/<change-name>/ completed changes, kept for history
```

## The one rule that makes this work

**`specs/` is only edited by the archive step.** While a change is in flight, its
behaviour lives in `changes/<name>/specs/` as a delta. When the change ships, the delta is
folded into `specs/` and the change directory moves to `changes/archive/`.

If you edit `specs/` directly to describe work you are *about to do*, this whole directory
degrades into stale documentation. Don't.

## Writing a requirement

Format:

```markdown
### Requirement: <one capability, stated as SHALL>
The system SHALL ...

#### Scenario: <what situation>
- **GIVEN** <starting state>
- **WHEN** <the trigger>
- **THEN** <an observable result>
```

Hard rules (these are what the `spec-review` skill checks):

1. **One requirement, one thing.** If it needs an "and", it is probably two requirements.
2. **THEN must be observable.** "handled correctly" / "works properly" fail review.
   "exit code 2", "the markdown contains `<!-- ⚠️ ... -->`", "the crop file is not
   written" pass.
3. **Every requirement has a failure scenario.** What happens when the input is bad.
4. **Negative constraints are mandatory.** Write what must *not* happen — this is where an
   AI implementer improvises, and where regressions get through.
5. **Thresholds are numbers.** Ratios, caps, timeouts, retry counts — written out, and
   matching the constant in the code.

## Delta format

Inside `changes/<name>/specs/<domain>/spec.md`, mark each requirement with its operation:

```markdown
## ADDED Requirements
### Requirement: ...

## MODIFIED Requirements
### Requirement: ...      (restate the requirement in full, post-change)

## REMOVED Requirements
### Requirement: ...      (state why it is going away)
```

A delta restates the whole requirement, never a diff fragment — the archive step is then a
copy, not a merge.

## The loop

1. Discuss the change.
2. Write `changes/<name>/proposal.md` + delta spec + `tasks.md`.
3. **The maintainer reads the spec line by line and approves it.** This is not optional and
   not delegable to a model — it is the step the whole method exists for.
4. Implement against the delta. Tests come from the scenarios.
5. Archive: fold the delta into `specs/`, move the change to `changes/archive/`.

## Tooling

- **`/spec-review <change-name>`** — a user-level Claude Code skill that runs the eight
  checks above against a change directory and reports pass/fail per item with suggested
  replacement text. It never edits files.
- **`.claude/hooks/spec-sync-guard.py`** — a `PreToolUse` hook that blocks a `git commit`
  whose staged files touch implementation code but include nothing under `openspec/` and no
  `CHANGELOG.md` entry. Escape hatch: put `[no-spec]` in the commit message.

## Provenance of the initial specs

The specs under `specs/` were extracted on 2026-08-19 from this repo's own
`README.md`, `AGENTS.md`, `docs/`, `CHANGELOG.md`, and the constants in
`converter/convert.py`, `figures/*.py`, and `shared/config.py`. They describe behaviour
that already shipped as of **v0.7.1**.

They are a faithful transcription of the documented contract, not a line-by-line audit of
every code path. Where a spec and the code disagree, **that is a finding** — open a change
to resolve it in whichever direction is correct, rather than silently editing the spec.
