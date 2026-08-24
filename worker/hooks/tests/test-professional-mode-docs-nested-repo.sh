#!/bin/bash
# test-professional-mode-docs-nested-repo.sh
#
# Task #6: the docs/ whitelist in professional-mode-guard.sh must resolve a
# docs path against the ENCLOSING git repo root so a design/plan write into a
# repo NESTED below the session cwd is allowed — but BOUNDED so it never
# admits a write into an unrelated repo elsewhere on the machine.
#
# Four cases, workflow_stage=brainstorming, professional mode on:
#   1. POSITIVE existing-dir     — nested repo, docs/plans/ already created.
#   2. POSITIVE not-yet-created  — nested repo, docs/plans/ does NOT exist yet
#                                   (proves the ancestor-walk: PreToolUse runs
#                                   before Write creates parent directories).
#   3. NEGATIVE non-docs         — same nested repo, a non-docs path is still
#                                   blocked (proves the widening is scoped to
#                                   docs/* only, not "anything in this repo").
#   4. NEGATIVE outside-repo     — a git repo OUTSIDE the session cwd is still
#                                   blocked (proves the bound: GIT_ROOT must be
#                                   under PROJECT_ROOT, not just any repo).
#
# Modeled on test-managed-worktree-guard.sh's harness conventions (payload/
# run_guard/status_of/assert_eq, mktemp TEST_ROOT/TEST_HOME, the
# ironclaude-hooks-config.json, seeding workflow_stage via sqlite3).

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="$ROOT_DIR/hooks/professional-mode-guard.sh"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$label"; else fail "$label" "expected=$expected actual=$actual"; fi
}

TEST_ROOT=$(mktemp -d)
TEST_ROOT=$(cd "$TEST_ROOT" && pwd -P)
OUTSIDE_ROOT=$(mktemp -d)
OUTSIDE_ROOT=$(cd "$OUTSIDE_ROOT" && pwd -P)
trap 'rm -rf "$TEST_ROOT" "$OUTSIDE_ROOT"' EXIT

TEST_HOME="$TEST_ROOT/home"
CWD="$TEST_ROOT/cwd"
REPO_A="$CWD/repo-a"      # nested repo: docs/plans pre-created (case 1) + non-docs (case 3)
REPO_B="$CWD/repo-b"      # nested repo: nothing but the repo root exists (case 2)
OUTSIDE_REPO="$OUTSIDE_ROOT/other-repo"  # repo OUTSIDE the session cwd (case 4)
SESSION="019fc5e5-fc72-7493-b785-bee8cda62b2b"
STATE_DB="$TEST_HOME/.claude/ironclaude.db"

mkdir -p "$TEST_HOME/.claude" "$CWD" "$REPO_A/docs/plans" "$REPO_B" "$OUTSIDE_REPO"
printf '{"verbose_hook_logs":false}\n' > "$TEST_HOME/.claude/ironclaude-hooks-config.json"

for repo in "$REPO_A" "$REPO_B" "$OUTSIDE_REPO"; do
  git -C "$repo" init -q
  git -C "$repo" config user.email test@example.com
  git -C "$repo" config user.name Test
done

sqlite3 "$STATE_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
  terminal_session TEXT PRIMARY KEY,
  professional_mode TEXT NOT NULL,
  workflow_stage TEXT NOT NULL,
  current_wave INTEGER DEFAULT 1,
  review_pending INTEGER DEFAULT 0,
  review_block_count INTEGER DEFAULT 0
);
CREATE TABLE wave_tasks (
  terminal_session TEXT NOT NULL,
  wave_number INTEGER NOT NULL,
  allowed_files TEXT,
  status TEXT
);
CREATE TABLE registered_designs (
  file_path TEXT,
  terminal_session TEXT,
  consumed INTEGER DEFAULT 0
);
INSERT INTO sessions VALUES ('$SESSION', 'on', 'brainstorming', 1, 0, 0);
SQL

BASH_BIN="${BASH_BIN:-bash}"

run_guard() {
  local payload="$1"
  local output status
  set +e
  output=$(printf '%s' "$payload" | HOME="$TEST_HOME" "$BASH_BIN" "$GUARD" 2>&1)
  status=$?
  set -e
  printf '%s\n%s' "$status" "$output"
}
status_of() { printf '%s' "$1" | sed -n '1p'; }
payload() {
  local tool="$1" tool_input="$2" cwd="$3"
  jq -cn --arg tool "$tool" --argjson input "$tool_input" --arg cwd "$cwd" --arg session "$SESSION" \
    '{tool_name:$tool,tool_input:$input,cwd:$cwd,session_id:$session}'
}

echo '=== case 1: POSITIVE — nested repo, docs/plans/ already exists ==='
TARGET1="$REPO_A/docs/plans/2026-08-11-nested-existing-design.md"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TARGET1" '{file_path:$p,content:"# design\n"}')" "$CWD")")
assert_eq 'nested repo, existing docs/plans dir: ALLOWED' '0' "$(status_of "$RESULT")"

echo '=== case 2: POSITIVE — nested repo, docs/plans/ NOT yet created ==='
TARGET2="$REPO_B/docs/plans/2026-08-11-nested-notyet-design.md"
[ ! -d "$(dirname "$TARGET2")" ] || { fail 'precondition' "docs/plans must not pre-exist for case 2: $(dirname "$TARGET2")"; }
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TARGET2" '{file_path:$p,content:"# design\n"}')" "$CWD")")
assert_eq 'nested repo, not-yet-created docs/plans dir (ancestor walk): ALLOWED' '0' "$(status_of "$RESULT")"

echo '=== case 3: NEGATIVE — nested repo, non-docs path ==='
TARGET3="$REPO_A/src/foo.py"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TARGET3" '{file_path:$p,content:"pass\n"}')" "$CWD")")
assert_eq 'nested repo, non-docs path: BLOCKED' '2' "$(status_of "$RESULT")"

echo '=== case 4: NEGATIVE — repo OUTSIDE the session cwd ==='
TARGET4="$OUTSIDE_REPO/docs/plans/2026-08-11-outside-design.md"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TARGET4" '{file_path:$p,content:"# design\n"}')" "$CWD")")
assert_eq 'repo outside session cwd: BLOCKED (bound holds)' '2' "$(status_of "$RESULT")"

echo
echo "=== SUMMARY: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
