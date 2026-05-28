#!/bin/bash
# wiki-synthesis-enforcer.sh — PreToolUse hook
# Warns when wiki_query not called before gated actions.
# Blocks update_directive_status(completed) unless wiki_write was called first.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "wiki-synthesis-enforcer"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Flag file paths (session-scoped)
FLAG_DIR="/tmp/ic"
WIKI_QUERIED_FLAG="$FLAG_DIR/wiki-queried-$SESSION_TAG"
WIKI_WRITTEN_FLAG="$FLAG_DIR/wiki-written-$SESSION_TAG"

# ARM wiki-queried: wiki_query was called
if [[ "$TOOL_NAME" == "mcp__orchestrator__wiki_query" ]]; then
  mkdir -p "$FLAG_DIR"
  touch "$WIKI_QUERIED_FLAG"
  log_hook "wiki-synthesis-enforcer" "Allowed" "wiki_query — gate armed"
  exit 0
fi

# ARM wiki-written: wiki_write was called
if [[ "$TOOL_NAME" == "mcp__orchestrator__wiki_write" ]]; then
  mkdir -p "$FLAG_DIR"
  touch "$WIKI_WRITTEN_FLAG"
  log_hook "wiki-synthesis-enforcer" "Allowed" "wiki_write — synthesis recorded"
  exit 0
fi

# BLOCK: update_directive_status(completed) requires prior wiki_write
if [[ "$TOOL_NAME" == "mcp__orchestrator__update_directive_status" ]]; then
  STATUS=$(echo "$INPUT" | jq -r '.tool_input.status // empty' 2>/dev/null || true)
  if [[ "$STATUS" == "completed" ]]; then
    if [ ! -f "$WIKI_WRITTEN_FLAG" ]; then
      block_pretooluse "wiki-synthesis-enforcer" \
        "Write wiki pages synthesizing what was learned before completing this directive."
    fi
    rm -f "$WIKI_WRITTEN_FLAG"
  fi
  log_hook "wiki-synthesis-enforcer" "Allowed" "update_directive_status($STATUS) — passing through"
  exit 0
fi

# WARN: gated actions should be preceded by wiki_query
case "$TOOL_NAME" in
  mcp__orchestrator__spawn_worker|\
  mcp__orchestrator__spawn_workers|\
  mcp__orchestrator__approve_plan|\
  mcp__orchestrator__reject_plan|\
  mcp__orchestrator__send_to_worker|\
  mcp__orchestrator__kill_worker)
    if [ ! -f "$WIKI_QUERIED_FLAG" ]; then
      log_warning "wiki-synthesis-enforcer" \
        "Query the wiki before dispatching workers. What knowledge is relevant to this decision?"
    fi
    rm -f "$WIKI_QUERIED_FLAG"
    log_hook "wiki-synthesis-enforcer" "Allowed" "$TOOL_NAME — wiki query check passed"
    exit 0
    ;;
esac

# Default: allow (hook is additive enforcement, not deny-by-default)
log_hook "wiki-synthesis-enforcer" "Allowed" "tool not gated"
exit 0
