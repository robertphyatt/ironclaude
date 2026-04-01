#!/bin/bash
# mcp-state-logger.sh — PostToolUse hook
# Logs workflow_stage transitions triggered by MCP state-manager tools.
# Fires after: mark_design_ready, mark_plan_ready, mark_brainstorming,
#              mark_executing, create_plan, start_execution, retreat
#
# Best-effort: if DB read fails, emits a warning rather than hard-failing.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "mcp-state-logger"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Extract the short tool name (last segment after __)
SHORT_TOOL="${TOOL_NAME##*__}"

# Read current workflow_stage from DB (best-effort, no hard-fail)
STAGE=""
if [ -n "$SESSION_TAG" ] && [ "$SESSION_TAG" != "none" ] && [ -f "$DB_PATH" ]; then
  SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  STAGE=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}' LIMIT 1;" \
    2>/dev/null || true)
fi

if [ -z "$STAGE" ]; then
  log_warning "mcp-state-logger" "Could not read workflow_stage after $SHORT_TOOL"
  exit 0
fi

log_hook "mcp-state-logger" "Transition" "tool=$SHORT_TOOL → workflow_stage=$STAGE"
exit 0
