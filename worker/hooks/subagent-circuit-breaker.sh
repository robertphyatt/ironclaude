#!/bin/bash
# subagent-circuit-breaker.sh - Circuit breaker for subagent dispatch failures
#
# Registered for:
#   PostToolUse on Agent/TaskOutput — detect context-limit failures
#   PreToolUse on Agent             — block dispatches when breaker is tripped
#
# Mode detection: presence of tool_output distinguishes PostToolUse from PreToolUse.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "SUBAGENT-CIRCUIT-BREAKER"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Detect mode: tool_output present = PostToolUse, absent = PreToolUse
HAS_OUTPUT=$(echo "$INPUT" | jq -r 'has("tool_output")' 2>/dev/null || echo "false")

if [ "$HAS_OUTPUT" = "true" ]; then
  # ─── PostToolUse: DETECT context-limit failures ───
  TOOL_OUTPUT=$(echo "$INPUT" | jq -r '.tool_output // empty' 2>/dev/null || true)

  if echo "$TOOL_OUTPUT" | grep -qi "context limit\|context window"; then
    SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
    db_write_or_fail "SUBAGENT-CIRCUIT-BREAKER" \
      "UPDATE sessions SET circuit_breaker=1, updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}';"
    db_audit_log "hook:subagent-circuit-breaker" "circuit_breaker_tripped" "0" "1" ""
    log_hook "SUBAGENT-CIRCUIT-BREAKER" "TRIPPED" "Subagent hit context limit"
  fi

  exit 0
else
  # ─── PreToolUse: BLOCK Task dispatch when breaker is tripped ───
  CB=$(db_read_or_fail "SUBAGENT-CIRCUIT-BREAKER" \
    "SELECT circuit_breaker FROM sessions WHERE terminal_session='$(echo "$SESSION_TAG" | sed "s/'/''/g")';") || {
    block_pretooluse "SUBAGENT-CIRCUIT-BREAKER" "BLOCKED — DATABASE ERROR

Cannot read circuit breaker state from the database. This is a temporary error.

Try dispatching the Task again. If this persists, report the error to the user."
  }

  if [ "$CB" = "1" ] && [ "$TOOL_NAME" = "Agent" ]; then
    block_pretooluse "SUBAGENT-CIRCUIT-BREAKER" "BLOCKED — SUBAGENT CONTEXT LIMIT HIT

A previous subagent ran out of context. Dispatching more subagents is blocked.

You MUST switch to inline execution:
1. Execute remaining tasks directly in this session (no subagents)
2. Follow the plan steps exactly as written

Do NOT dispatch more subagents. Execute inline instead."
  fi

  exit 0
fi
