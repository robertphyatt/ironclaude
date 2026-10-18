#!/bin/bash
# block-pin-enforcer.sh — PostToolUse hook on mcp__orchestrator__update_ledger
# When update_ledger is called with any blocked tasks, injects a reminder into
# Brain's context to post and pin a decision-format escalation message if not done.
#
# Exit 0 always — PostToolUse hooks cannot block the completed tool call.
# Output JSON with systemMessage key — Claude Code displays this in Brain's context.

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
if [[ "$TOOL_NAME" != "mcp__orchestrator__update_ledger" ]]; then
  exit 0
fi

# Extract comma-separated list of IDs/descriptions for blocked tasks
BLOCKED_LIST=$(echo "$INPUT" | \
  jq -r '[.tool_input.tasks[]? | select(.status == "blocked") | (.id // .description // "unknown")] | join(", ")' \
  2>/dev/null || true)

if [ -z "$BLOCKED_LIST" ]; then
  exit 0
fi

jq -n --arg list "$BLOCKED_LIST" \
  '{systemMessage: "⚠️ [block-pin-enforcer]: BLOCKED TASK PIN REQUIRED — Tasks blocked: \($list). If you have NOT yet called post_message + pin_message for each blocked task, do so immediately (see workflow.md Decision Format: options/pros-cons/recommendation/prediction). Robert will not see these blockers without a pinned message."}'

exit 0
