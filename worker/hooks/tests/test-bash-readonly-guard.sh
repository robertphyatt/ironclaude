#!/bin/bash
# Unit tests for the read-only Bash predicates. DB-free.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/../bash-readonly-guard.sh"

fail=0
allow() { if is_readonly_research_bash "$1"; then :; else echo "FAIL expected ALLOW: $1"; fail=1; fi; }
block() { if is_readonly_research_bash "$1"; then echo "FAIL expected BLOCK: $1"; fail=1; fi; }

# Safe read-only -> ALLOW
allow "find . -name '*.log'"
allow "cat /tmp/ic/daemon.log"
allow "ls -la /tmp"
allow "grep -r pattern ."
allow "rg foo src"
allow "head -n 50 file"
allow "tail -n 100 file"
allow "wc -l file"
allow "find . \\( -name a -o -name b \\)"

# Redirection -> BLOCK
block "cat secret > /etc/x"
block "ls >> out"
block "grep x f 2> err"
block "cat a < b"

# find write/exec actions -> BLOCK
block "find . -delete"
block "find . -name x -exec rm {} +"
block "find . -exec rm {} \\;"
block "find . -execdir rm {} +"
block "find . -fprintf out fmt"
block "find . -fls /tmp/x"
block "find . -ok rm {} \\;"

# Chaining / substitution -> BLOCK
block "ls \$(rm x)"
block "ls \`rm x\`"
block "grep x f | sh"
block "ls && rm x"
block "ls ; rm x"
block "cat <(rm x)"

# Newline injection -> BLOCK
block "ls"$'\n'"rm x"

# Non-allowlisted commands -> BLOCK
block "rm -rf /"
block "git commit -m x"
block "sqlite3 db 'UPDATE x'"
block "sed -i s/a/b/ f"
block "echo hi"

# ── is_readonly_git ──────────────────────────────────────────────────────────
git_allow() { if is_readonly_git "$1"; then :; else echo "FAIL expected ALLOW: $1"; fail=1; fi; }
git_block() { if is_readonly_git "$1"; then echo "FAIL expected BLOCK: $1"; fail=1; fi; }

git_allow "git status"
git_allow "git diff --staged"
git_allow "git log --oneline -5"
# Word-boundary preservation: \b matches before '-', so these are allowed TODAY.
git_allow "git diff-index HEAD"
git_allow "git show-ref"
git_block "git push"
git_block "git commit -m x"
git_block "git add file"
# `-C` normalization: a leading `<tool> -C <path>` pair is stripped before matching.
git_allow "git -C /repo status"

# ── GRD-1: write-capable subcommands must NOT reach the read-only branch ──────
# These reached `exit 0` logged as "read-only git command" before this change.
git_block "git stash"
git_block "git stash drop"
git_block "git stash pop"
git_block "git branch -D main"
git_block "git branch newbranch"
git_block "git tag v9"
git_block "git remote add x http://e"
git_block "git reflog expire --expire=now --all"
# -C must not reopen what the narrowing closed
git_block "git -C /repo stash drop"
# KNOWN read-only form, deliberately fail-closed: `git stash show` is read-only but
# is not in the operator-approved admitted set. Admitting it would widen beyond R1
# without sign-off, so it is refused and recorded as a limitation, not a defect.
git_block "git stash show"

# Read-only FORMS of the same subcommands stay allowed — a narrowing tested only
# with negatives proves nothing about what it still permits.
git_allow "git stash list"
git_allow "git branch"
git_allow "git branch -a"
git_allow "git branch --show-current"
git_allow "git remote"
git_allow "git remote -v"
git_allow "git tag"
git_allow "git tag -l 'v1.*'"
git_allow "git reflog"
git_allow "git reflog show"

# ── is_review_allowed ────────────────────────────────────────────────────────
rev_allow() { if is_review_allowed "$1"; then :; else echo "FAIL expected ALLOW: $1"; fail=1; fi; }
rev_block() { if is_review_allowed "$1"; then echo "FAIL expected BLOCK: $1"; fail=1; fi; }

rev_allow "pytest tests/"
rev_allow "make test"
# Word-boundary preservation: allowed TODAY via make\s+test\b.
rev_allow "make test-unit"
rev_allow "sqlite3 db 'SELECT 1'"
rev_allow "grep -rn foo ."
rev_block "rm -rf /"
rev_block "git push"
# `-C` normalization and the env-prefixed interpreter form this repo requires.
rev_allow "git -C /repo diff --staged"
rev_allow "PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/"

# -C must not become a write-command escape
git_block "git -C /repo push"
git_block "git -C /repo commit -m x"
rev_block "git -C /repo push"
# only a LEADING -C pair is stripped
git_block "echo git -C /repo status"

# ORDERING WITNESS: _has_blocked_metachars MUST run before _strip_dash_c.
# A ';' payload proves nothing (blocked either way). Only a SPACELESS payload
# inside the -C path is falsifying: the sed's [^[:space:]]+ would consume it,
# leaving a clean "git status", so an inverted order would ALLOW this.
git_block 'git -C $(evil) status'
rev_block 'git -C $(evil) diff'

# env prefixes are admitted ONLY to the pytest forms
rev_allow "PYTHONUNBUFFERED=1 pytest -x"
rev_block "FOO=1 sqlite3 db 'UPDATE x'"
rev_block "FOO=1 find . -delete"
rev_block "python -m pip install x"
rev_block ".venv/bin/python -m pip install x"
rev_block "FOO=1 python -m http.server"

# diff, both lists
allow "diff a b"
rev_allow "diff a b"
block "diff a b > out"

# ── bare cd is read-only; chained cd is not (cwd-drift deadlock cure) ──
allow "cd /tmp"
allow "cd /Users/example/Code/ironclaude"
allow "cd"
rev_allow "cd /tmp"
# Chained/compound cd stays BLOCKED (metacharacter check unchanged) — non-widening controls.
block "cd /tmp && ls"
block "cd a ; rm b"
rev_block "cd /tmp && rm x"

if [ "$fail" -eq 0 ]; then echo "ALL PASS"; else echo "FAILURES"; exit 1; fi
