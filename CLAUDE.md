# CLAUDE.md — which audience are you?

This repo has two kinds of AI reader, and they need different files.

**You are setting this tool up for a user** (they said "read AGENTS.md and set this up") →
read [`AGENTS.md`](AGENTS.md). Nothing on this page applies to you.

**You are changing this repo** (fixing a bug, adding a behaviour, cutting a release) → read
[`openspec/README.md`](openspec/README.md) first, then continue here.

---

## Working on this repo

### Spec before code

`openspec/specs/` states what this tool does. When a request changes that:

1. Write the delta spec first — `openspec/changes/<name>/` (copy `openspec/changes/TEMPLATE`).
2. Get the maintainer's line-by-line approval on it.
3. Then implement.

**When the requirement shifts mid-implementation, go back and change the delta spec before
touching code again.** A spec that gets updated after the fact is a changelog, and the whole
point is to have the argument before the code exists rather than after.

`openspec/specs/` itself is edited only by the archive step, never to describe work you are
about to do.

### The commit guard

`.claude/hooks/spec-sync-guard.py` blocks a `git commit` that stages `converter/`,
`figures/`, `citations/`, `shared/`, `skills/`, `workflows/`, or `templates/` while staging
nothing under `openspec/` and no `CHANGELOG.md` entry. Test-only changes are exempt.

If a commit is genuinely internal, re-run it with `[no-spec]` in the message. Do not disable
the hook.

### Release discipline (this repo is public)

Every user-visible change ships as: `CHANGELOG.md` entry + commit + version tag. Never
overwrite a released tree wholesale.

Stage explicit paths. Never `git add -A` or `git add .`.

### Two rules that override convenience

- **Never tune a QC threshold to make a failing case pass.** Fix that book's geometry logic
  instead. This applies to `figures/figure_qc_gate.py`, `figures/pregate.py`, and every
  `T2N_TABLE_*` constant.
- **A new default-ON behaviour needs a corpus measurement** showing it corrects wrong
  output. Additive behaviours ship as opt-in flags, default OFF, byte-identical when unset.
