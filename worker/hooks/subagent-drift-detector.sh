#!/bin/bash
# subagent-drift-detector.sh - SubagentStop hook
# When a subagent finishes, sets review_pending=1 and cleans up subagent link.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "SUBAGENT-DRIFT-DETECTOR"

INPUT=$(cat)
init_session_id

# Extract child session from SubagentStop input
CHILD_SESSION=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

if [ -n "$CHILD_SESSION" ]; then
  SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  SAFE_CHILD=$(echo "$CHILD_SESSION" | sed "s/'/''/g")

  # Only set review_pending during plan execution — not during brainstorming/research (H11 fix)
  WORKFLOW=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true)
  if [ "$WORKFLOW" = "executing" ]; then
    CHANGES=$(git status --porcelain 2>/dev/null || true)
    if [ -n "$CHANGES" ]; then
      db_write_or_fail "SUBAGENT-DRIFT-DETECTOR" \
        "UPDATE sessions SET review_pending=1, updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}';"
    else
      log_hook "SUBAGENT-DRIFT-DETECTOR" "Skipped" "review_pending not set — no code changes (read-only subagent)"
    fi
  else
    log_hook "SUBAGENT-DRIFT-DETECTOR" "Skipped" "review_pending not set — workflow_stage=${WORKFLOW:-unknown} (not executing)"
  fi

  # Clean up subagent link (best-effort)
  sqlite3 "$DB_PATH" ".timeout 10000" \
    "DELETE FROM subagent_sessions WHERE child_session='${SAFE_CHILD}';" 2>/dev/null || true

  db_audit_log "hook:subagent-drift-detector" "subagent_completed" "" "$CHILD_SESSION" ""
  log_hook "SUBAGENT-DRIFT-DETECTOR" "Notified" "child=${CHILD_SESSION}"
else
  log_hook "SUBAGENT-DRIFT-DETECTOR" "Skipped" "no child session in input"
fi

exit 0
