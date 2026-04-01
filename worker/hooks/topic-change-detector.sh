#!/bin/bash
# topic-change-detector.sh - UserPromptSubmit hook for detecting topic changes
#
# Only calls LLM when:
#   1. Professional mode is active
#   2. A plan is being executed (workflow_stage = 'executing' or 'reviewing')
#
# Reads state directly from SQLite — no state-manager.sh dependency.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  echo '{"systemMessage": "[TOPIC-CHANGE-DETECTOR]: CRITICAL - Failed to load hook-logger.sh!"}'
  echo "CRITICAL: hook-logger.sh failed to load" >&2
fi
run_hook "TOPIC-CHANGE-DETECTOR"

INPUT=$(cat)
init_session_id

# ─── Read session state from SQLite ───
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
ROW=$(db_read_or_fail "TOPIC-CHANGE-DETECTOR" \
  "SELECT professional_mode, workflow_stage, plan_name, current_wave FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
IFS='|' read -r PROF_MODE WORKFLOW PLAN_NAME CURRENT_WAVE <<< "$ROW"
# Fetch plan_json separately — it may contain pipe characters that break IFS parsing
PLAN_JSON=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
  "SELECT plan_json FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true)

# Gate: professional mode must be 'on'
if [ "$PROF_MODE" != "on" ]; then
  log_hook "TOPIC-CHANGE-DETECTOR" "Disabled" "professional mode off"
  exit 0
fi

# Check jq dependency
if ! command -v jq &>/dev/null; then
  log_hook "TOPIC-CHANGE-DETECTOR" "Passed" "jq not installed - install with: brew install jq"
  exit 0
fi

# Source plan-validator for call_validation_llm
. "${SCRIPT_DIR}/plan-validator.sh"

USER_PROMPT=$(echo "$INPUT" | jq -r '.user_prompt // empty' 2>/dev/null || true)

# Gate: must be executing a plan
if ! is_plan_active "$WORKFLOW"; then
  log_hook "TOPIC-CHANGE-DETECTOR" "Passed" "not executing a plan"
  exit 0
fi

# Extract plan context from plan_json
PLAN_SUMMARY=$(echo "$PLAN_JSON" | jq -r '.summary // "unknown"' 2>/dev/null || echo "unknown")
TOTAL_TASKS=$(echo "$PLAN_JSON" | jq -r '.tasks | length // "?"' 2>/dev/null || echo "?")
CURRENT_TASK="${CURRENT_WAVE:-?}"

# Build Haiku prompt
HAIKU_PROMPT="You evaluate if a user message is related to an active implementation plan.

Active plan: ${PLAN_SUMMARY}
Current progress: Wave ${CURRENT_TASK} of ${TOTAL_TASKS} tasks

User message: ${USER_PROMPT}

Is this message:
- Related to the active plan (continuing work, approving, asking about plan tasks, providing feedback)
- Clearly unrelated (asking about different features, files, or subjects not in the plan)

Respond with ONLY valid JSON:
{\"ok\": true} if related or ambiguous
{\"ok\": false, \"reason\": \"brief explanation\"} if clearly unrelated"

# JSON schema for validation response
OK_REASON_SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"},"reason":{"type":"string"}},"required":["ok","reason"]}'

# Call validation LLM (Ollama or Haiku based on config)
RESPONSE=$(call_validation_llm "$HAIKU_PROMPT" "$OK_REASON_SCHEMA") || {
  log_llm_result "TOPIC-CHANGE-DETECTOR" "Failed" "LLM call failed" ""
  exit 0
}

OK=$(echo "$RESPONSE" | jq -r '.ok' 2>/dev/null || true)
REASON=$(echo "$RESPONSE" | jq -r '.reason // empty' 2>/dev/null || true)

if [ -z "$OK" ]; then
  log_llm_result "TOPIC-CHANGE-DETECTOR" "Failed" "LLM parse failed" "$RESPONSE"
  exit 0
fi

if [ "$OK" = "false" ]; then
  jq -n --arg reason "${REASON:-Topic appears unrelated to active plan}" '{
    "systemMessage": ("\u26a0\ufe0f [TOPIC-CHANGE-DETECTOR]: Topic change detected \u2014 " + $reason + ". You MUST invoke the plan-interruption skill BEFORE responding to the user. Call the Skill tool with skill: ironclaude:plan-interruption. Do NOT respond to the user first.")
  }'
else
  log_llm_result "TOPIC-CHANGE-DETECTOR" "Passed" "on-topic"
fi

exit 0
