#!/bin/bash
# state-activator.sh — UserPromptSubmit hook
# Detects professional mode toggles and logs ALL state changes visible to the user.
# UserPromptSubmit is one of only two hook types whose stdout is displayed to the user.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "state-activator"

INPUT=$(cat)
init_session_id

# ═══ Pending PPID marker handshake ═══
# MCP wrappers write ironclaude-ppid-pending-{PPID} when no session file exists
# (e.g., after plugin reload). Write the session file so MCP retry loop can bind.
for marker in "$HOME/.claude"/ironclaude-ppid-pending-*; do
  [ -f "$marker" ] || continue
  ppid_val=$(basename "$marker" | sed 's/ironclaude-ppid-pending-//')
  # Validate ppid_val is numeric (defense against unexpected filenames)
  if ! [[ "$ppid_val" =~ ^[0-9]+$ ]]; then
    rm -f "$marker"
    continue
  fi
  ppid_file="$HOME/.claude/ironclaude-session-${ppid_val}.id"
  if [ ! -f "$ppid_file" ]; then
    TMP_PPID=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
    printf '%s' "$SESSION_TAG" > "$TMP_PPID"
    mv "$TMP_PPID" "$ppid_file"
    log_hook "state-activator" "PPID" "wrote session file from pending marker: $ppid_file"
  fi
  rm -f "$marker"
done

# Surface any MCP tool errors from the sideband log
surface_mcp_errors

USER_PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null || true)

if [ -z "$USER_PROMPT" ]; then
  exit 0
fi

# Detect professional mode toggles
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# Raw SQL is acceptable here: hooks cannot call MCP tools, and user deactivation
# should always be allowed. The MCP set_professional_mode tool validates that Claude
# cannot set 'off', but this is a human action via hook that intentionally bypasses
# Claude-only restrictions.
# Debug: surface what user_prompt contains (gated by verbose logging)
log_hook "state-activator" "Debug" "user_prompt prefix: $(echo "$USER_PROMPT" | head -c 200)"
if echo "$USER_PROMPT" | grep -qiE '(^|[/: ])deactivate-professional-mode'; then
  # Soft write: deactivation should never block the user's message
  DEACTIVATE_CHANGES=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "UPDATE sessions SET professional_mode='off', workflow_stage='idle', updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}'; SELECT changes();" 2>/dev/null) || true
  if [ -n "$DEACTIVATE_CHANGES" ] && [ "$DEACTIVATE_CHANGES" != "0" ]; then
    db_audit_log "hook:state-activator" "professional_mode_off" "on" "off" ""
    log_hook "state-activator" "Set" "professional-mode-off"
  else
    log_warning "state-activator" "Deactivation UPDATE affected 0 rows (session=${SESSION_TAG}). Skill will provide sqlite fallback."
  fi
fi

# ═══ State change detection ═══
# Query MCP for current state and log any changes since last check.
# This is the ONLY reliable way to surface state changes because
# UserPromptSubmit is one of only two hook types with visible stdout.
STATE_CACHE="$HOME/.claude/ironclaude-state-cache-${SESSION_TAG}.json"

CURRENT_PROF=""
CURRENT_WORKFLOW=""

# Query current session state from SQLite (best-effort — this is a logging hook, not a gate)
if [ -f "$DB_PATH" ] && command -v sqlite3 &>/dev/null; then
  IFS='|' read -r CURRENT_PROF CURRENT_WORKFLOW <<< "$(sqlite3 "$DB_PATH" \
    "SELECT professional_mode, workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true)"
fi

# Compare against cached state
if [ -n "$CURRENT_PROF" ] || [ -n "$CURRENT_WORKFLOW" ]; then
  PREV_PROF=""
  PREV_WORKFLOW=""
  if [ -f "$STATE_CACHE" ] && command -v jq &>/dev/null; then
    PREV_PROF=$(jq -r '.professional_mode // empty' "$STATE_CACHE" 2>/dev/null || true)
    PREV_WORKFLOW=$(jq -r '.workflow_stage // empty' "$STATE_CACHE" 2>/dev/null || true)
  fi

  # Log differences
  if [ -n "$CURRENT_PROF" ] && [ "$CURRENT_PROF" != "$PREV_PROF" ] && [ -n "$PREV_PROF" ]; then
    log_hook "STATE-CHANGE" "State" "professional_mode: ${PREV_PROF} -> ${CURRENT_PROF}"
  fi
  if [ -n "$CURRENT_WORKFLOW" ] && [ "$CURRENT_WORKFLOW" != "$PREV_WORKFLOW" ] && [ -n "$PREV_WORKFLOW" ]; then
    log_hook "STATE-CHANGE" "State" "workflow_stage: ${PREV_WORKFLOW} -> ${CURRENT_WORKFLOW}"
  fi

  # Update cache
  if command -v jq &>/dev/null; then
    jq -n \
      --arg pm "${CURRENT_PROF:-unknown}" \
      --arg ws "${CURRENT_WORKFLOW:-unknown}" \
      --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      '{professional_mode: $pm, workflow_stage: $ws, timestamp: $ts}' \
      > "$STATE_CACHE" 2>/dev/null || true
  fi
fi

exit 0
