#!/bin/bash
# startup-lookback-enforcer.sh — PreToolUse hook
# Blocks gated orchestrator actions until Slack lookback (≥48h) and ledger update
# are complete. Flags persist for the session (not consumed after gated tool passes).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "startup-lookback-enforcer"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Flag file paths (session-scoped, persistent)
FLAG_DIR="/tmp/ic"
FLAG_SLACK="$FLAG_DIR/lookback-slack-$SESSION_TAG"
FLAG_LEDGER="$FLAG_DIR/lookback-ledger-$SESSION_TAG"

# ARM: Slack lookback with hours_back >= 48
if [[ "$TOOL_NAME" == "mcp__orchestrator__get_operator_messages" ]]; then
  HOURS_BACK=$(echo "$INPUT" | jq -r '.tool_input.hours_back // 0' 2>/dev/null || echo 0)
  if [ "$HOURS_BACK" -ge 48 ] 2>/dev/null; then
    mkdir -p "$FLAG_DIR"
    touch "$FLAG_SLACK"
    log_hook "startup-lookback-enforcer" "Allowed" "slack lookback (${HOURS_BACK}h) — armed"
  else
    log_hook "startup-lookback-enforcer" "Allowed" "get_operator_messages (${HOURS_BACK}h < 48) — not armed"
  fi
  exit 0
fi

# ARM: ledger update
if [[ "$TOOL_NAME" == "mcp__orchestrator__update_ledger" ]]; then
  mkdir -p "$FLAG_DIR"
  touch "$FLAG_LEDGER"
  log_hook "startup-lookback-enforcer" "Allowed" "ledger update — armed"
  exit 0
fi

# QUERY bypass: get_* tools don't require lookback
if [[ "$TOOL_NAME" == mcp__orchestrator__get_* ]]; then
  log_hook "startup-lookback-enforcer" "Allowed" "query tool bypass"
  exit 0
fi

# GATED action tools: require BOTH flag files (persistent — no delete after pass)
case "$TOOL_NAME" in
  mcp__orchestrator__spawn_worker|\
  mcp__orchestrator__spawn_workers|\
  mcp__orchestrator__approve_plan|\
  mcp__orchestrator__reject_plan|\
  mcp__orchestrator__send_to_worker|\
  mcp__orchestrator__kill_worker|\
  AskUserQuestion)
    MISSING=""
    if [ ! -f "$FLAG_SLACK" ]; then
      MISSING="Slack lookback (≥48h)"
    fi
    if [ ! -f "$FLAG_LEDGER" ]; then
      if [ -n "$MISSING" ]; then
        MISSING="$MISSING, ledger update"
      else
        MISSING="ledger update"
      fi
    fi
    if [ -n "$MISSING" ]; then
      block_pretooluse "startup-lookback-enforcer" \
        "Complete startup lookback first: ${MISSING}. Read Slack (≥48h) and update task ledger."
    fi
    # Do NOT delete flags — persistent for session (unlike memory-search-enforcer)
    log_hook "startup-lookback-enforcer" "Allowed" "$TOOL_NAME — lookback check passed"
    exit 0
    ;;
esac

# Default: allow (hook is additive enforcement, not deny-by-default)
log_hook "startup-lookback-enforcer" "Allowed" "tool not gated"
exit 0
