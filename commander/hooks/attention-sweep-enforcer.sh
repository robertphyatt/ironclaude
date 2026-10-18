#!/bin/bash
# attention-sweep-enforcer.sh — PreToolUse hook
# Blocks gated worker actions unless a full-fleet get_worker_status() call
# successfully returned within the last 3 minutes. The flag is armed PostToolUse
# by attention-sweep-arm.sh — only a confirmed successful response arms the gate.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "attention-sweep-enforcer"

INPUT=$(cat)
init_session_id

# Fail-closed on TOOL_NAME parse: can't route without knowing the tool name.
# Do NOT use || true here — if jq fails, block with a clear error.
TOOL_NAME=""
if ! TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null); then
  block_pretooluse "attention-sweep-enforcer" \
    "Hook parse error — jq failed to parse hook input. Check jq installation."
fi

# Flag file path (session-scoped, shared with attention-sweep-arm.sh)
FLAG_DIR="/tmp/ic"
SWEEP_FLAG="$FLAG_DIR/sweep-armed-$SESSION_TAG"
SWEEP_TTL=3

# get_worker_status: pass through — arming is handled PostToolUse by attention-sweep-arm.sh
if [[ "$TOOL_NAME" == "mcp__orchestrator__get_worker_status" ]]; then
  log_hook "attention-sweep-enforcer" "Allowed" "get_worker_status — arm handled PostToolUse"
  exit 0
fi

# PASS-THROUGH: query/read tools don't require a sweep
if [[ "$TOOL_NAME" == mcp__orchestrator__get_* ]]; then
  log_hook "attention-sweep-enforcer" "Allowed" "query tool bypass"
  exit 0
fi

# GATED action tools: require a recent full-fleet sweep
case "$TOOL_NAME" in
  mcp__orchestrator__send_to_worker|\
  mcp__orchestrator__send_keys_to_worker|\
  mcp__orchestrator__approve_plan|\
  mcp__orchestrator__reject_plan|\
  mcp__orchestrator__kill_worker|\
  mcp__orchestrator__spawn_worker|\
  mcp__orchestrator__spawn_workers)
    # No sweep at all
    if [ ! -f "$SWEEP_FLAG" ]; then
      block_pretooluse "attention-sweep-enforcer" \
        "Attention Sweep required — call get_worker_status() for all workers before acting on one."
    fi
    # Sweep too old (TTL expired)
    STALE=$(find "$SWEEP_FLAG" -mmin "+${SWEEP_TTL}" 2>/dev/null || true)
    if [ -n "$STALE" ]; then
      rm -f "$SWEEP_FLAG"
      block_pretooluse "attention-sweep-enforcer" \
        "Attention Sweep expired (>${SWEEP_TTL}m) — call get_worker_status() for all workers to re-arm."
    fi
    # Sweep fresh — allow (flag retained for subsequent actions within TTL)
    log_hook "attention-sweep-enforcer" "Allowed" "$TOOL_NAME — sweep check passed"
    exit 0
    ;;
esac

# Default: allow (hook is additive enforcement, not deny-by-default)
log_hook "attention-sweep-enforcer" "Allowed" "tool not gated"
exit 0
