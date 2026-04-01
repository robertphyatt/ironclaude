#!/bin/bash
# task-completion-validator.sh - PostToolUse hook (thin MCP client)
#
# Triggers when: Skill tool completes with skill="*code-review*" and args contain "--task-boundary"
# Action: LLM validates work matches expected task, then posts code-review-passed event to MCP
#
# State reads: sqlite3 (direct DB reads)
# State writes: sqlite3 via db_write_or_fail()
#
# Input: JSON via stdin with tool_name, tool_input, tool_response
# Output: JSON systemMessage to stdout, or nothing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "TASK-COMPLETION-VALIDATOR"

INPUT=$(cat)
init_session_id

# =============================================================================
# TOOL FILTER: Only process Skill tool
# =============================================================================

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

if [ "$TOOL_NAME" != "Skill" ]; then
  exit 0
fi

# Check if this is a code-review skill call with --task-boundary
SKILL_NAME=$(echo "$INPUT" | jq -r '.tool_input.skill // empty' 2>/dev/null || true)
SKILL_ARGS=$(echo "$INPUT" | jq -r '.tool_input.args // empty' 2>/dev/null || true)

if [[ "$SKILL_NAME" != *"code-review"* ]]; then
  exit 0
fi

if [[ "$SKILL_ARGS" != *"--task-boundary"* ]]; then
  log_hook "TASK-COMPLETION-VALIDATOR" "Passed" "code-review without --task-boundary, skipping"
  exit 0
fi

# =============================================================================
# READ CURRENT TASK FROM DB
# =============================================================================

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

CURRENT_TASK=""
TASK_NAME=""
TASK_DESC=""
if [ -f "$DB_PATH" ]; then
  CURRENT_TASK=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
    "SELECT task_id FROM wave_tasks WHERE terminal_session = '${SAFE_SESSION}' AND status = 'in_progress' LIMIT 1;" 2>/dev/null || true)
  if [ -n "$CURRENT_TASK" ]; then
    SAFE_CURRENT_TASK=$(echo "$CURRENT_TASK" | sed "s/'/''/g")
    TASK_NAME=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
      "SELECT task_name FROM wave_tasks WHERE terminal_session = '${SAFE_SESSION}' AND task_id = '${SAFE_CURRENT_TASK}' LIMIT 1;" 2>/dev/null || true)
    TASK_DESC=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
      "SELECT description FROM wave_tasks WHERE terminal_session = '${SAFE_SESSION}' AND task_id = '${SAFE_CURRENT_TASK}' LIMIT 1;" 2>/dev/null || true)
  fi
fi

if [ -z "$CURRENT_TASK" ]; then
  log_hook "TASK-COMPLETION-VALIDATOR" "Passed" "no in_progress task found"
  exit 0
fi

# =============================================================================
# LLM VALIDATION (if plan-validator.sh available)
# =============================================================================

if [ -f "${SCRIPT_DIR}/plan-validator.sh" ]; then
  # Source plan-validator for call_validation_llm
  . "${SCRIPT_DIR}/plan-validator.sh"

  if type call_validation_llm &>/dev/null; then
    HAIKU_PROMPT="You evaluate if a completed task matches what was expected in the plan.

Current task ID: ${CURRENT_TASK}
Task name: ${TASK_NAME:-unknown}

Expected task description:
${TASK_DESC:-No description available}

A code review was just completed for this task. Based on the task description, does the work appear to be for the correct task?

Respond with ONLY valid JSON:
{\"ok\": true, \"reason\": \"brief explanation\"} if work matches expected task
{\"ok\": false, \"reason\": \"brief explanation\"} if work appears to be for a different task"

    OK_REASON_SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"},"reason":{"type":"string"}},"required":["ok","reason"]}'

    RESPONSE=$(call_validation_llm "$HAIKU_PROMPT" "$OK_REASON_SCHEMA") || {
      log_llm_result "TASK-COMPLETION-VALIDATOR" "Passed" "LLM call failed, task match assumed" ""
      exit 0
    }

    OK=$(echo "$RESPONSE" | jq -r '.ok' 2>/dev/null || true)
    REASON=$(echo "$RESPONSE" | jq -r '.reason // empty' 2>/dev/null || true)

    if [ -z "$OK" ]; then
      log_llm_result "TASK-COMPLETION-VALIDATOR" "Passed" "LLM parse failed, task match assumed" "$RESPONSE"
      exit 0
    fi

    if [ "$OK" = "false" ]; then
      # Block advancement, warn user (review_pending stays 1, MCP is not notified)
      jq -n --arg reason "${REASON:-Work does not match expected task}" '{
        "systemMessage": ("[TASK-COMPLETION-VALIDATOR]: Task mismatch detected -- " + $reason + ". Review and confirm before continuing. Type \"proceed anyway\" to override.")
      }'
      log_llm_result "TASK-COMPLETION-VALIDATOR" "Blocked" "task mismatch: ${REASON}" "$RESPONSE"
      exit 0
    fi
  fi
fi

# =============================================================================
# APPROVED: Task-description match confirmed (info only — GBTW advances tasks)
# =============================================================================

log_hook "TASK-COMPLETION-VALIDATOR" "Matched" "task ${CURRENT_TASK} confirmed as correct task (advancement handled by GBTW)"

exit 0
