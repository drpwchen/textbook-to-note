#!/usr/bin/env bash
# Behaviour tests for spec-sync-guard's ==git commit-msg entry point==.
#
# These run real `git commit` calls inside a throwaway repo, using the real guard and
# the real hook file — not a simplified copy. That distinction is the whole point: a
# sibling repo learned on 2026-08-20 that a guard can pass every synthetic-payload test
# and still never fire, because the wiring was never connected. Only a real commit
# proves the wiring.
#
# Run: bash .claude/hooks/test_spec_guard_git.sh
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PASS=0
FAIL=0

check() {  # check <expected exit> <label> <actual exit>
    if [ "$1" = "$3" ]; then
        echo "  PASS  [$1] $2"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  [expected $1, got $3] $2"
        FAIL=$((FAIL + 1))
    fi
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git init -q "$TMP/repo"
cd "$TMP/repo"
git config user.email t@t; git config user.name t
git config commit.gpgsign false

mkdir -p .claude/hooks .githooks openspec/changes
cp "$REPO_ROOT/.claude/hooks/spec-sync-guard.py" .claude/hooks/
cp "$REPO_ROOT/.githooks/commit-msg" .githooks/
chmod +x .githooks/commit-msg
git config core.hooksPath .githooks

mkdir -p converter figures tests
echo "seed" > README.md
git add README.md
git commit -qm "seed [no-spec]"

commit_exit() {  # commit_exit <message> -> prints exit code
    git commit -qm "$1" >/dev/null 2>&1
    echo $?
}

echo "git commit-msg entry point"

# 1 — implementation code, no spec -> blocked
echo "x = 1" > converter/thing.py
git add converter/thing.py
check 1 "converter/ change with no spec -> blocked" "$(commit_exit 'add thing')"

# 2 — same change, [no-spec] in the message -> allowed
check 0 "same change with [no-spec] -> allowed" "$(commit_exit 'add thing [no-spec]')"

# 3 — code plus openspec/ -> allowed
echo "y = 2" > figures/other.py
mkdir -p openspec/changes/demo
echo "# delta" > openspec/changes/demo/spec.md
git add figures/other.py openspec/changes/demo/spec.md
check 0 "code plus openspec/ -> allowed" "$(commit_exit 'code plus spec')"

# 4 — code plus CHANGELOG.md (this repo's alternative satisfier) -> allowed
echo "z = 3" > converter/third.py
echo "## [Unreleased]" > CHANGELOG.md
git add converter/third.py CHANGELOG.md
check 0 "code plus CHANGELOG.md -> allowed" "$(commit_exit 'code plus changelog')"

# 5 — tests only -> allowed (a test is not a behaviour contract)
echo "assert True" > tests/test_x.py
git add tests/test_x.py
check 0 "tests only -> allowed" "$(commit_exit 'tests only')"

# 6 — README only (not a code prefix) -> allowed
echo "docs" >> README.md
git add README.md
check 0 "README only -> allowed" "$(commit_exit 'readme')"

# 7 — `git commit -a` stages at commit time; must still be caught
echo "w = 4" >> converter/thing.py
git commit -qam "sneak in via -a" >/dev/null 2>&1
check 1 "git commit -a on converter/ with no spec -> blocked" "$?"

# 8 — --no-verify is git's own escape hatch; make sure nothing wedges it
git commit -qam "bypass" --no-verify >/dev/null 2>&1
check 0 "--no-verify -> allowed" "$?"

# 10 — this repo's own rule: code-prefixed paths are exempt only when they are ALL
#      test_ files. `tests/` is not a code prefix here, so test 5 above never
#      exercised it — this one does.
echo "assert True" > converter/test_helper.py
git add converter/test_helper.py
check 0 "only test_ files under a code prefix -> allowed" "$(commit_exit 'test helper')"

# 11 — one real file alongside a test_ file breaks the "all" condition -> blocked
echo "assert True" > converter/test_other.py
echo "u = 6" > converter/real.py
git add converter/test_other.py converter/real.py
check 1 "test_ file mixed with a real file -> blocked" "$(commit_exit 'mixed')"

# 12 — LAST, because it deletes the guard: guard missing -> fail open. A guard that blocks commits because it is itself
#     broken is worse than no guard.
rm .claude/hooks/spec-sync-guard.py
echo "v = 5" > converter/fourth.py
git add converter/fourth.py
check 0 "guard file absent -> fail open" "$(commit_exit 'guard missing')"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
