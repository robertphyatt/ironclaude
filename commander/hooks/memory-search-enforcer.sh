#!/bin/bash
# memory-search-enforcer.sh — PreToolUse hook
# Blocks orchestrator action tools unless episodic memory was searched first.
# Arms when mcp__episodic-memory__* is called; disarms after each gated action.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "memory-search-enforcer"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Operator name: env var, fallback to "Operator"
OPERATOR="${OPERATOR_NAME:-Operator}"

# Flag file path (session-scoped)
FLAG_DIR="/tmp/ic"
FLAG_FILE="$FLAG_DIR/memory-armed-$SESSION_TAG"

# ARM: episodic memory search arms the gate
if [[ "$TOOL_NAME" == mcp__episodic-memory__* ]]; then
  mkdir -p "$FLAG_DIR"
  touch "$FLAG_FILE"
  log_hook "memory-search-enforcer" "Allowed" "memory search — armed"
  exit 0
fi

# QUERY bypass: get_* tools don't require memory search
if [[ "$TOOL_NAME" == mcp__orchestrator__get_* ]]; then
  log_hook "memory-search-enforcer" "Allowed" "query tool bypass"
  exit 0
fi

# GATED action tools: require prior memory search
case "$TOOL_NAME" in
  mcp__orchestrator__spawn_worker|\
  mcp__orchestrator__spawn_workers|\
  mcp__orchestrator__approve_plan|\
  mcp__orchestrator__reject_plan|\
  mcp__orchestrator__send_to_worker|\
  mcp__orchestrator__kill_worker|\
  AskUserQuestion)
    if [ ! -f "$FLAG_FILE" ]; then
      block_pretooluse "memory-search-enforcer" \
        "Search episodic memory first. What would ${OPERATOR} do?"
    fi
    rm -f "$FLAG_FILE"
    log_hook "memory-search-enforcer" "Allowed" "$TOOL_NAME — memory check passed"
    exit 0
    ;;
esac

# Default: allow (hook is additive enforcement, not deny-by-default)
log_hook "memory-search-enforcer" "Allowed" "tool not gated"
exit 0
