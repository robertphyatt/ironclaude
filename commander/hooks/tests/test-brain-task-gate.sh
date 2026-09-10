#!/bin/bash
# R5a: test for brain-task-gate.sh — the PreToolUse hook that stops the daemon Brain
# wrapping gated actions in general-purpose subagents. Matches BOTH tool names the
# SDK may emit for a subagent dispatch (Task from the bundled CLI, Agent from the
# newer PATH CLI). Runs with a fresh HOME (no ironclaude.db) — the allow cases
# passing proves the gate is DB-FREE (a db_read_or_fail would error on the missing DB).
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # -> commander/
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"                          # -> repo root
HOOK_SRC="$ROOT_DIR/hooks/brain-task-gate.sh"
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
cp "$HOOK_SRC" "$SCRIPT_DIR/"
cp "$LOGGER_SRC" "$SCRIPT_DIR/"
HOOK="$SCRIPT_DIR/brain-task-gate.sh"
SESSION="ic-r5a-test-$$-$(date +%s)"
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude"       # deliberately NO ironclaude.db -> proves the gate is DB-free
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
trap 'rm -rf "$TEST_ROOT"' EXIT

feed() {  # feed TOOL_NAME SUBAGENT_TYPE ("OMIT" -> omit the field) -> prints exit code
  local tool="$1" sub="${2:-}"
  if [ "$sub" = "OMIT" ]; then
    printf '{"session_id":"%s","tool_name":"%s","tool_input":{}}' "$SESSION" "$tool" \
      | bash "$HOOK" >/dev/null 2>&1
  else
    printf '{"session_id":"%s","tool_name":"%s","tool_input":{"subagent_type":"%s"}}' "$SESSION" "$tool" "$sub" \
      | bash "$HOOK" >/dev/null 2>&1
  fi
  printf '%s' "$?"
}

for tool in Task Agent; do
  assert_eq "$tool general-purpose blocked"       "2" "$(feed "$tool" general-purpose)"
  assert_eq "$tool search-conversations allowed"  "0" "$(feed "$tool" ironclaude:search-conversations)"
  assert_eq "$tool missing subagent_type blocked" "2" "$(feed "$tool" OMIT)"
done

# a non-subagent tool is not gated at all
assert_eq "non-subagent tool (Bash) allowed" "0" "$(feed Bash OMIT)"

# Fail-closed: with hook-logger.sh absent the gated case still blocks and the
# allowed cases still pass (selective, not a blunt block-everything).
NOLOG_DIR="$TEST_ROOT/nolog"
mkdir -p "$NOLOG_DIR"
cp "$HOOK_SRC" "$NOLOG_DIR/"          # hook only — deliberately NO hook-logger.sh
NOLOG_HOOK="$NOLOG_DIR/brain-task-gate.sh"
nofeed() {  # nofeed TOOL SUBAGENT -> exit code, against the logger-less hook
  printf '{"session_id":"%s","tool_name":"%s","tool_input":{"subagent_type":"%s"}}' "$SESSION" "$1" "$2" \
    | bash "$NOLOG_HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "no-logger: Agent general-purpose blocked"      "2" "$(nofeed Agent general-purpose)"
assert_eq "no-logger: Agent search-conversations allowed" "0" "$(nofeed Agent ironclaude:search-conversations)"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
