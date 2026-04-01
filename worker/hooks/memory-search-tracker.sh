#!/bin/bash
# memory-search-tracker.sh — PostToolUse hook for Agent tool
# Detects when search-conversations agent is dispatched and clears
# the memory_search_required flag.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "memory-search-tracker"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Only process Agent tool (Claude Code sends tool_name="Agent" in PostToolUse events)
if [ "$TOOL_NAME" != "Agent" ]; then
  exit 0
fi

# Check if subagent_type contains "search-conversations"
SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || true)

if [[ "$SUBAGENT_TYPE" != *"search-conversations"* ]]; then
  exit 0
fi

# Clear the memory_search_required flag
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

db_write_or_fail "memory-search-tracker" \
  "UPDATE sessions SET memory_search_required=0, updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}';"

log_hook "memory-search-tracker" "Cleared" "memory_search_required=0 (search-conversations dispatched)"
exit 0
