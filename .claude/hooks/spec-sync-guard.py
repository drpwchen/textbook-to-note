#!/usr/bin/env python3
"""PreToolUse guard: block a `git commit` that changes behaviour but not the spec.

Registered on the Bash matcher in `.claude/settings.json`. Reads the hook payload on
stdin, and exits 2 (with guidance on stderr) when the staged changeset touches
implementation code while including nothing under `openspec/` and no `CHANGELOG.md`
entry.

Escape hatch: put `[no-spec]` anywhere in the commit message.

Written in Python rather than the bash+jq idiom so it runs unchanged on Windows, where
`jq` is usually absent. Python 3.10+ is already a hard requirement of this repo.

Design rule: **fail open**. Any unexpected condition — malformed payload, git not on
PATH, not a repo — exits 0. A guard that blocks commits for its own reasons is worse
than no guard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# Staged paths under these prefixes are "behaviour" — changing them should be reflected
# in the spec or, at minimum, in the changelog.
CODE_PREFIXES = (
    "converter/",
    "figures/",
    "citations/",
    "shared/",
    "skills/",
    "workflows/",
    "templates/",
)

# Staged paths that satisfy the guard.
SPEC_PREFIXES = ("openspec/",)
SPEC_FILES = ("CHANGELOG.md",)

BYPASS_TOKEN = "[no-spec]"

MESSAGE = """\
Blocked: this commit changes implementation code but touches nothing under openspec/
and adds no CHANGELOG.md entry.

Staged code files:
{code_list}

Before committing, decide which of these it is:

  1. It changes what the tool does for a user
     -> the behaviour belongs in openspec/. If a change directory is open, update its
        delta spec and tick the matching line in tasks.md. If not, open one:
        cp -r openspec/changes/TEMPLATE openspec/changes/<change-name>
        (openspec/specs/ itself is only edited by the archive step.)

  2. It is a user-visible fix or improvement with no contract change
     -> add the CHANGELOG.md entry now, not later.

  3. It is genuinely internal (refactor, typo, test-only, tooling)
     -> re-run the commit with [no-spec] in the message.
"""


def git(*args: str, cwd: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return out.stdout


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    if "git commit" not in command:
        return 0
    if BYPASS_TOKEN in command:
        return 0

    repo = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    try:
        staged = [p for p in git("diff", "--cached", "--name-only", cwd=repo).splitlines() if p]
    except Exception:
        return 0

    # `git commit -a` stages tracked modifications at commit time, so an empty index is
    # not proof of an empty changeset.
    if not staged and any(f in command.split() for f in ("-a", "-am", "--all")):
        try:
            staged = [p for p in git("diff", "--name-only", cwd=repo).splitlines() if p]
        except Exception:
            return 0

    if not staged:
        return 0

    code = [p for p in staged if p.startswith(CODE_PREFIXES)]
    if not code:
        return 0

    # Tests describe behaviour that already exists; they are not a contract change.
    if all(os.path.basename(p).startswith("test_") for p in code):
        return 0

    spec = [p for p in staged if p.startswith(SPEC_PREFIXES) or p in SPEC_FILES]
    if spec:
        return 0

    code_list = "\n".join(f"  - {p}" for p in code[:12])
    if len(code) > 12:
        code_list += f"\n  - ... and {len(code) - 12} more"
    print(MESSAGE.format(code_list=code_list), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
