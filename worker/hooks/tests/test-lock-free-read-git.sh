#!/bin/bash
# Read-only git in IronClaude hooks never writes the index, so a hook killed mid-run cannot
# leave a stale .git/index.lock (design 2026-10-18 lock-free-read-git R2).
set -u
HOOKS="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { if grep -qF -- "$2" "$HOOKS/$1"; then ok "$1 uses: $2"; else bad "$1 missing: $2"; fi; }

has execution-drift-check.sh 'git -C "$root" --no-optional-locks status --porcelain=v1 -z --untracked-files=all'
has subagent-drift-detector.sh 'git --no-optional-locks status --porcelain'

# Behavior: --no-optional-locks leaves a stat-dirty index unwritten; plain status (control) rewrites it.
R=$(mktemp -d); trap 'rm -rf "$R"' EXIT
git -C "$R" init -q
printf 'a\n' > "$R/a"
git -C "$R" add a
git -C "$R" -c user.email=t@example.invalid -c user.name=t commit -q -m init
mtime() { python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "$R/.git/index"; }
touch -t 202001010000 "$R/a"
m0=$(mtime)
git -C "$R" --no-optional-locks status --porcelain >/dev/null
m1=$(mtime)
if [ "$m0" = "$m1" ]; then ok "--no-optional-locks status leaves the index unwritten"; else bad "--no-optional-locks status rewrote the index"; fi
git -C "$R" status --porcelain >/dev/null
m2=$(mtime)
if [ "$m1" != "$m2" ]; then ok "control: plain status rewrites the index"; else bad "control: plain status did not rewrite the index"; fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
