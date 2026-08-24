#!/usr/bin/env bash
set -euo pipefail

# Verifies skill-state-bridge.sh sets inherit_review from the FROM-stage when the
# brainstorming skill invocation raw-UPDATEs workflow_stage (the dominant live path
# into brainstorming, which bypasses executeWorkflowTransition). See C2b.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INIT_HOOK="${SCRIPT_DIR}/../session-init.sh"
BRIDGE_HOOK="${SCRIPT_DIR}/../skill-state-bridge.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="${TMP_ROOT}/home"
SESSION="skill-bridge-inherit-review-session"
DB_PATH="${TMP_HOME}/.claude/ironclaude.db"

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "${TMP_HOME}/.claude"

# Bootstrap the DB + a session row (real schema, incl. inherit_review).
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$INIT_HOOK" >/dev/null

invoke_brainstorming() {
  printf '{"session_id":"%s","tool_name":"Skill","tool_input":{"skill":"brainstorming"}}' "$SESSION" \
    | HOME="$TMP_HOME" bash "$BRIDGE_HOOK" >/dev/null 2>&1 || true
}

read_inherit() {
  sqlite3 "$DB_PATH" "SELECT inherit_review FROM sessions WHERE terminal_session='${SESSION}';"
}

fail() { printf 'FAIL: %s\n' "$1"; exit 1; }

# Case 1: mid-effort (executing) -> brainstorming => inherit_review = 1
sqlite3 "$DB_PATH" "UPDATE sessions SET workflow_stage='executing', inherit_review=0 WHERE terminal_session='${SESSION}';"
invoke_brainstorming
[ "$(read_inherit)" = "1" ] || fail "executing -> brainstorming should set inherit_review=1, got '$(read_inherit)'"

# Case 2: terminal (execution_complete) -> brainstorming => inherit_review = 0
# Pre-set to 1 so a passing assertion proves the hook actively wrote 0 (not a default).
sqlite3 "$DB_PATH" "UPDATE sessions SET workflow_stage='execution_complete', inherit_review=1 WHERE terminal_session='${SESSION}';"
invoke_brainstorming
[ "$(read_inherit)" = "0" ] || fail "execution_complete -> brainstorming should set inherit_review=0, got '$(read_inherit)'"

printf 'PASS skill-state-bridge sets inherit_review from FROM-stage on brainstorming entry\n'
