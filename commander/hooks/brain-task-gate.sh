#!/bin/bash
# brain-task-gate.sh — PreToolUse hook (registered with matcher "Agent|Task").
#
# R5a: the daemon Brain must not wrap its own gated actions in a general-purpose
# subagent (202 such calls observed) — it should act directly (call the
# orchestrator/MCP tools itself). Allow ONLY the ironclaude:search-conversations
# subagent; block every other subagent dispatch. Fail-closed: an absent/empty
# subagent_type is blocked.
#
# Matches BOTH tool names because the emitted name depends on the CLI: the daemon
# Brain runs the SDK's BUNDLED claude (older, emits tool_name "Task"); a newer PATH
# CLI emits "Agent". Matching both is future-proof against an SDK-bundle upgrade.
#
# DB-FREE by design: a Brain session has no row in the state-manager `sessions`
# table, so any db_read_or_fail (as in subagent-circuit-breaker.sh) would raise a
# DATABASE ERROR and block EVERY dispatch. This gate reads only the tool input.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  # Fail-closed: enforcement infra is missing. Still block the gated case (an
  # Agent/Task subagent that isn't search-conversations); allow everything else.
  _IN=$(cat)
  _TN=$(echo "$_IN" | jq -r '.tool_name // empty' 2>/dev/null || true)
  if [[ "$_TN" == "Agent" || "$_TN" == "Task" ]]; then
    _SUB=$(echo "$_IN" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || true)
    if [[ "$_SUB" != "ironclaude:search-conversations" ]]; then
      echo "✗ [brain-task-gate]: Blocked - IronClaude hook-logger.sh missing; enforcement cannot run. Run 'make deploy-hooks', then retry." >&2
      exit 2
    fi
  fi
  exit 0
fi
run_hook "brain-task-gate"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Only gate subagent dispatches; everything else passes untouched.
if [[ "$TOOL_NAME" != "Agent" && "$TOOL_NAME" != "Task" ]]; then
  exit 0
fi

SUBAGENT=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || true)

if [[ "$SUBAGENT" == "ironclaude:search-conversations" ]]; then
  log_hook "brain-task-gate" "Allowed" "search-conversations subagent"
  exit 0
fi

block_pretooluse "brain-task-gate" "BLOCKED — do not wrap gated actions in a general-purpose subagent.

Act directly: call the orchestrator/MCP tools yourself instead of dispatching a '${SUBAGENT:-<none>}' subagent. Only 'ironclaude:search-conversations' is permitted as a subagent."
