#!/bin/bash
# subagent-drift-detector.sh - SubagentStop hook
# When a subagent finishes, logs git status and cleans up subagent link.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  exit 0
fi

# _sad_last_assistant_text TRANSCRIPT_PATH
# Claude-transcript-only extraction, kept for test-sad-antipattern.sh. Delegates to
# the shared fallback in hook-logger.sh. The hook body uses ic_last_assistant_text,
# which prefers the SubagentStop input's last_assistant_message.
_sad_last_assistant_text() {
    _ic_transcript_last_assistant_text "${1:-}"
}

# Test-mode shim: sourcing with SAD_TEST_MODE=1 exposes helpers without
# running the hook body (mirrors GBTW_TEST_MODE in get-back-to-work-claude.sh).
if [ "${SAD_TEST_MODE:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

run_hook "SUBAGENT-DRIFT-DETECTOR"

INPUT=$(cat)
init_session_id

# Extract child session from SubagentStop input
CHILD_SESSION=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

if [ -n "$CHILD_SESSION" ]; then
  SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  SAFE_CHILD=$(echo "$CHILD_SESSION" | sed "s/'/''/g")

  # Only set review_pending during plan execution — not during brainstorming/research (H11 fix)
  WORKFLOW=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true)
  if [ "$WORKFLOW" = "executing" ]; then
    # --no-optional-locks: read without rewriting the index (design 2026-10-18 lock-free-read-git).
    CHANGES=$(git --no-optional-locks status --porcelain 2>/dev/null || true)
    if [ -n "$CHANGES" ]; then
      log_hook "SUBAGENT-DRIFT-DETECTOR" "Detected" "git changes present after subagent — review_pending NOT set (submit_task is sole authority)"
    else
      log_hook "SUBAGENT-DRIFT-DETECTOR" "Skipped" "review_pending not set — no code changes (read-only subagent)"
    fi
  else
    log_hook "SUBAGENT-DRIFT-DETECTOR" "Skipped" "review_pending not set — workflow_stage=${WORKFLOW:-unknown} (not executing)"
  fi

  # Clean up subagent link (best-effort)
  sqlite3 "$DB_PATH" ".timeout 10000" \
    "DELETE FROM subagent_sessions WHERE child_session='${SAFE_CHILD}';" 2>/dev/null || true

  db_audit_log "hook:subagent-drift-detector" "subagent_completed" "" "$CHILD_SESSION" ""

  # Anti-pattern block (round-2 Important 4/5 + Minor 6): runs AFTER cleanup +
  # audit log so subagent_sessions row and audit trail persist on block. Gates
  # on same 5 stages as GBTW predicate (executing / reviewing / brainstorming /
  # plan_ready / final_plan_prep). Uses SAD's own $INPUT variable (not
  # INPUT_JSON) and block_stop's 2-arg signature.
  case "$WORKFLOW" in
    executing|reviewing|brainstorming|plan_ready|final_plan_prep)
      LAST_TEXT=$(ic_last_assistant_text "$INPUT")
      if [ -n "$LAST_TEXT" ] && [ "$(_ic_is_antipattern_proposal "$LAST_TEXT")" = "true" ]; then
        block_stop "SUBAGENT-DRIFT-DETECTOR" "Subagent proposed a checkpoint / query-offload anti-pattern. Continue the work; do not re-propose the pause. See ironclaude:workflow-durability."
      fi
      ;;
  esac

  log_hook "SUBAGENT-DRIFT-DETECTOR" "Notified" "child=${CHILD_SESSION}"
else
  log_hook "SUBAGENT-DRIFT-DETECTOR" "Skipped" "no child session in input"
fi

exit 0
