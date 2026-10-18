#!/bin/bash
# attention-sweep-arm.sh — PostToolUse hook
# Arms the sweep gate ONLY when get_worker_status(all) returns a valid fleet list.
# Arming happens PostToolUse (not PreToolUse) so we can verify the response
# before granting sweep authority. A failed get_worker_status must NOT arm the gate.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "attention-sweep-arm"

INPUT=$(cat)
init_session_id

# Flag file path (session-scoped, shared with attention-sweep-enforcer.sh)
FLAG_DIR="/tmp/ic"
SWEEP_FLAG="$FLAG_DIR/sweep-armed-$SESSION_TAG"

# Only relevant for get_worker_status — registered with precise matcher,
# but guard defensively in case matcher config changes.
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
if [[ "$TOOL_NAME" != "mcp__orchestrator__get_worker_status" ]]; then
  log_hook "attention-sweep-arm" "Allowed" "not get_worker_status — pass through"
  exit 0
fi

# Single-worker queries do not arm the gate
WORKER_ID=$(echo "$INPUT" | jq -r '.tool_input.worker_id // empty' 2>/dev/null || true)
if [ -n "$WORKER_ID" ]; then
  log_hook "attention-sweep-arm" "Allowed" "get_worker_status(single) — sweep NOT armed"
  exit 0
fi

# Arm the sweep flag for all-workers calls.
mkdir -p "$FLAG_DIR"
touch "$SWEEP_FLAG"
log_hook "attention-sweep-arm" "Allowed" "get_worker_status(all) — sweep gate armed"

exit 0
