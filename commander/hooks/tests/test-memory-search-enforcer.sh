#!/bin/bash
# Characterization + fail-closed test for memory-search-enforcer.sh — the PreToolUse
# gate that blocks gated orchestrator actions until episodic memory was searched.
# Uses a FAKE session_id so SESSION_TAG isolates the /tmp/ic flag from any live Brain
# session; arms via the hook's own ARM tool so it does not replicate flag derivation.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # -> commander/
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"                          # -> repo root
HOOK_SRC="$ROOT_DIR/hooks/memory-search-enforcer.sh"
LOGGER_SRC="$REPO_ROOT/worker/hooks/hook-logger.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$label"; else fail "$label" "expected=$expected actual=$actual"; fi
}

TEST_ROOT=$(mktemp -d)
SCRIPT_DIR="$TEST_ROOT/hooks"
mkdir -p "$SCRIPT_DIR"
cp "$HOOK_SRC" "$SCRIPT_DIR/"          # deploy the hook under test
cp "$LOGGER_SRC" "$SCRIPT_DIR/"        # its sourced dependency (deployed by make deploy-hooks in prod)
HOOK="$SCRIPT_DIR/memory-search-enforcer.sh"
SESSION="ic-mse-test-$$-$(date +%s)"   # fake session_id -> isolated SESSION_TAG
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude"
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
trap 'rm -rf "$TEST_ROOT"; rm -f "/tmp/ic/memory-armed-$SESSION"' EXIT

feed() {  # feed TOOL_NAME -> prints the hook exit code
  printf '{"session_id":"%s","tool_name":"%s","tool_input":{}}' "$SESSION" "$1" \
    | bash "$HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}

# (1) unarmed: a gated action is blocked (block_pretooluse -> exit 2)
assert_eq "spawn_worker blocked without memory search" "2" "$(feed mcp__orchestrator__spawn_worker)"

# (2) query tools bypass the gate
assert_eq "get_worker_status allowed (query bypass)" "0" "$(feed mcp__orchestrator__get_worker_status)"

# (3) arm via an episodic memory search
assert_eq "episodic search arms the gate" "0" "$(feed mcp__episodic-memory__search)"

# (4) armed: the gated action is now allowed
assert_eq "spawn_worker allowed after memory search" "0" "$(feed mcp__orchestrator__spawn_worker)"

# (5) fail-closed: with hook-logger.sh absent a gated action blocks, a query passes.
NOLOG_DIR="$TEST_ROOT/nolog"
mkdir -p "$NOLOG_DIR"
cp "$HOOK_SRC" "$NOLOG_DIR/"            # hook only — deliberately NO hook-logger.sh
NOLOG_HOOK="$NOLOG_DIR/memory-search-enforcer.sh"
nofeed() {  # nofeed TOOL -> exit code, against the logger-less hook
  printf '{"session_id":"%s","tool_name":"%s","tool_input":{}}' "$SESSION" "$1" \
    | bash "$NOLOG_HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "no-logger: spawn_worker blocked"      "2" "$(nofeed mcp__orchestrator__spawn_worker)"
assert_eq "no-logger: get_worker_status allowed" "0" "$(nofeed mcp__orchestrator__get_worker_status)"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
