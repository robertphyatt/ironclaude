#!/bin/bash
# memory-search-tracker.sh — PostToolUse hook
# Clears memory_search_required when episodic memory is searched, on both clients
# (v1.1.16 L2b-2 R5). Two calls clear it: an episodic-memory MCP search (the Claude
# and Codex tool names measured in docs/plans/2026-10-03-instruction-parity-findings.md),
# or Claude's Agent dispatch of the search-conversations subagent.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "memory-search-tracker"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

case "$TOOL_NAME" in
  mcp__plugin_ironclaude_episodic-memory__search|mcp__episodic_memory__search)
    CLEAR_REASON="episodic-memory MCP search"
    ;;
  Agent)
    # Claude Code sends tool_name="Agent" for subagent dispatches.
    SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || true)
    if [[ "$SUBAGENT_TYPE" != *"search-conversations"* ]]; then
      exit 0
    fi
    CLEAR_REASON="search-conversations dispatched"
    ;;
  *)
    exit 0
    ;;
esac

# Clear the memory_search_required flag
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

db_write_or_fail "memory-search-tracker" \
  "UPDATE sessions SET memory_search_required=0, updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}';"

log_hook "memory-search-tracker" "Cleared" "memory_search_required=0 (${CLEAR_REASON})"
exit 0
