#!/bin/bash
# state-activator.sh — UserPromptSubmit hook
# Detects professional mode toggles and logs ALL state changes visible to the user.
# UserPromptSubmit is one of only two hook types whose stdout is displayed to the user.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"

# ─── Single-object, context-only stdout (v1.1.16 L2b-1b R4, L2b-1c R2) ───
# Several writers below (log_hook, log_error, log_warning and the
# warning-queue drain) each print one JSON line. This hook's messages are enforcement
# failures and queued warnings the model must act on, so the body's stdout is buffered
# and, on exit, every text (a systemMessage, a drained additionalContext, or a non-JSON
# line) is newline-joined in buffer order into ONE UserPromptSubmit additionalContext
# object: the one shape both clients deliver to the model. No systemMessage is
# emitted. Nothing is printed when there is nothing to say. This hook emits no
# decisions; the exit code is preserved.
# This trap replaces hook-logger.sh's EXIT trap, so it also removes _IC_DB_VALIDATED.
_ic_sa_flush() {
  local rc=$?
  exec 1>&3 3>&-
  if [ -s "$_IC_SA_OUT" ]; then
    jq -R -s -c '
      [split("\n")[] | select(length > 0) | . as $line | ((fromjson? | objects) // {"__text": $line})
        | (.systemMessage // .hookSpecificOutput.additionalContext? // .__text // (if has("hookSpecificOutput") then empty else tojson end))
        | select(type == "string")]
      | if length == 0 then empty
        else {hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: join("\n")}}
        end
    ' "$_IC_SA_OUT" 2>/dev/null || cat "$_IC_SA_OUT"
  fi
  rm -f "$_IC_SA_OUT" "${_IC_DB_VALIDATED:-}" 2>/dev/null
  exit "$rc"
}
_IC_SA_OUT=$(mktemp "${TMPDIR:-/tmp}/.ic-state-activator-XXXXXX" 2>/dev/null) || _IC_SA_OUT=""
if [ -n "$_IC_SA_OUT" ]; then
  exec 3>&1 1>"$_IC_SA_OUT"
  trap '_ic_sa_flush' EXIT
fi

run_hook "state-activator"

INPUT=$(cat)
init_session_id

# ═══ Pending PPID marker handshake ═══
# MCP wrappers write ironclaude-ppid-pending-{PPID} when no session file exists
# (e.g., after plugin reload). Write the session file so MCP retry loop can bind.
# Claim only this hook's own client's marker ({PPID} = REAL_PPID, derived as
# session-init.sh derives it). Another PID's marker belongs to another session;
# claiming it would stamp this session's id onto that client's PID.
REAL_PPID=$(ic_real_ppid)
marker="$HOME/.claude/ironclaude-ppid-pending-${REAL_PPID}"
if [[ "$REAL_PPID" =~ ^[0-9]+$ ]] && [ -f "$marker" ]; then
  ppid_file="$HOME/.claude/ironclaude-session-${REAL_PPID}.id"
  if [ ! -f "$ppid_file" ]; then
    TMP_PPID=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
    printf '%s' "$SESSION_TAG" > "$TMP_PPID"
    mv "$TMP_PPID" "$ppid_file"
    log_hook "state-activator" "PPID" "wrote session file from pending marker: $ppid_file"
  fi
  rm -f "$marker"
fi

# Deliver warnings queued by earlier hooks (GBTW) as next-turn context on both
# clients. The exit-time flush joins it into this hook's single context object.
ic_drain_warnings "$SESSION_TAG"

USER_PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null || true)

if [ -z "$USER_PROMPT" ]; then
  exit 0
fi

# Exact direct-human Git/workspace commands create a server-held intent through
# the internal workspace-manager CLI. The prompt hook returns no nonce, expiry,
# or evidence to conversation; public MCP consumers re-observe and atomically
# match those fields later.
TRIMMED_PROMPT=$(printf '%s' "$USER_PROMPT" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/(&#x20;|&#32;|&nbsp;|&#160;|&#xa0;|&#xA0;|[[:space:]])+$//')
HOOK_EVENT_NAME=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null || true)
THREAD_SOURCE=$(printf '%s' "$INPUT" | jq -r '.thread_source // empty' 2>/dev/null || true)
EVENT_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
HUMAN_OPERATION=""
HUMAN_CHANNEL=""

if [ "$HOOK_EVENT_NAME" = "UserPromptSubmit" ] && [ "$THREAD_SOURCE" != "subagent" ]; then
  for operation in commit commit-and-push push use-primary-checkout return-to-managed-worktree reconcile close-out confirm-resolution; do
    if [ "$TRIMMED_PROMPT" = "/$operation" ] || [ "$TRIMMED_PROMPT" = "/ironclaude:$operation" ]; then
      HUMAN_OPERATION="$operation"
      HUMAN_CHANNEL="claude-user-prompt"
      break
    fi
    if [ "$TRIMMED_PROMPT" = "\$ironclaude:$operation" ]; then
      HUMAN_OPERATION="$operation"
      HUMAN_CHANNEL="codex-user-prompt"
      break
    fi
    CODEX_COMMAND_LINK_RE='^\[\$ironclaude:'"$operation"'\]\(/[^)[:cntrl:]]*/skills/'"$operation"'/SKILL\.md\)$'
    if [[ "$TRIMMED_PROMPT" =~ $CODEX_COMMAND_LINK_RE ]]; then
      HUMAN_OPERATION="$operation"
      HUMAN_CHANNEL="codex-user-prompt"
      break
    fi
  done
fi

if [ -n "$HUMAN_OPERATION" ]; then
  INTENT_SAFE_SESSION=$(printf '%s' "$SESSION_TAG" | sed "s/'/''/g")
  INTENT_PROFESSIONAL_MODE=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT professional_mode FROM sessions WHERE terminal_session='${INTENT_SAFE_SESSION}';" 2>/dev/null || true)
  WORKSPACE_HOOK_INTENT="${IRONCLAUDE_WORKSPACE_HOOK_INTENT:-}"
  if [ -z "$WORKSPACE_HOOK_INTENT" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    candidate="$CLAUDE_PLUGIN_ROOT/mcp-servers/workspace-manager/dist/hook-intent.js"
    [ -f "$candidate" ] && WORKSPACE_HOOK_INTENT="$candidate"
  fi
  if [ -z "$WORKSPACE_HOOK_INTENT" ]; then
    # Deterministic selection: the active client's plugin cache first, then the
    # highest installed version within it, stopping at the first hit. The
    # previous loop assigned on EVERY match with no break, so the last glob
    # expansion won — with several versions installed a Claude session could
    # execute the Codex-installed bundle, and a stale version could beat the
    # running one. Nothing here validated either.
    intent_roots=()
    case "${IRONCLAUDE_CLIENT:-claude}" in
      codex) intent_roots=(
               "$HOME/.codex/plugins/cache/ironclaude/ironclaude"
               "$HOME/.claude/plugins/cache/ironclaude/ironclaude") ;;
      *)     intent_roots=(
               "$HOME/.claude/plugins/cache/ironclaude/ironclaude"
               "$HOME/.codex/plugins/cache/ironclaude/ironclaude") ;;
    esac
    for intent_root in "${intent_roots[@]}"; do
      [ -d "$intent_root" ] || continue
      while IFS= read -r intent_version; do
        [ -n "$intent_version" ] || continue
        candidate="$intent_root/$intent_version/mcp-servers/workspace-manager/dist/hook-intent.js"
        if [ -f "$candidate" ]; then WORKSPACE_HOOK_INTENT="$candidate"; break; fi
      done < <(ls -1 "$intent_root" 2>/dev/null | sort -Vr)
      [ -n "$WORKSPACE_HOOK_INTENT" ] && break
    done
  fi
  if [ "$INTENT_PROFESSIONAL_MODE" != "on" ]; then
    log_error "state-activator" "Human intent issuance requires professional mode on for this provider root"
  elif [ -z "$EVENT_CWD" ] || [ -z "$WORKSPACE_HOOK_INTENT" ] || [ ! -f "$WORKSPACE_HOOK_INTENT" ]; then
    log_error "state-activator" "Human intent issuance runtime is unavailable"
  else
    ISSUE_PAYLOAD=$(jq -cn \
      --arg operation "$HUMAN_OPERATION" \
      --arg human_channel "$HUMAN_CHANNEL" \
      --arg owner_session_id "$SESSION_TAG" \
      --arg repository_path "$EVENT_CWD" \
      '{operation:$operation,human_channel:$human_channel,owner_session_id:$owner_session_id,repository_path:$repository_path,hook_event_name:"UserPromptSubmit",invocation_source:"human"}')
    if ISSUE_RESULT=$(node "$WORKSPACE_HOOK_INTENT" "$ISSUE_PAYLOAD" 2>&1); then
      log_hook "state-activator" "Allowed" "server-held human intent issued for $HUMAN_OPERATION"
    else
      log_error "state-activator" "Human intent issuance failed: $ISSUE_RESULT"
    fi
  fi
fi

# Detect professional mode toggles
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# Raw SQL is acceptable here: hooks cannot call MCP tools, and user deactivation
# should always be allowed. The MCP set_professional_mode tool validates that Claude
# cannot set 'off', but this is a human action via hook that intentionally bypasses
# Claude-only restrictions.
# Debug: surface what user_prompt contains (gated by verbose logging)
log_hook "state-activator" "Debug" "user_prompt prefix: $(echo "$USER_PROMPT" | head -c 200)"
DEACTIVATE_REQUEST="false"
if echo "$USER_PROMPT" | grep -qiE '^[[:space:]]*/(ironclaude:)?deactivate-professional-mode([[:space:]]|$)'; then
  DEACTIVATE_REQUEST="true"
else
  # Codex invokes plugin skills with a dollar-prefixed name. Keep this route
  # case-sensitive and exact (apart from outer whitespace) so prose, code spans,
  # escaped dollars, and prefix/suffix variants cannot deactivate the session.
  CODEX_DEACTIVATE_LINK_RE='^\[\$ironclaude:deactivate-professional-mode\]\(/[^)[:cntrl:]]*/skills/deactivate-professional-mode/SKILL\.md\)$'
  if [[ "$TRIMMED_PROMPT" =~ $CODEX_DEACTIVATE_LINK_RE ]]; then
    TRIMMED_PROMPT='$ironclaude:deactivate-professional-mode'
  fi
  if [ "$TRIMMED_PROMPT" = '$ironclaude:deactivate-professional-mode' ]; then
    DEACTIVATE_REQUEST="true"
  fi
fi

if [ "$DEACTIVATE_REQUEST" = "true" ]; then
  # Check for active wave_tasks before resetting workflow_stage
  ACTIVE_TASKS=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT COUNT(*) FROM wave_tasks WHERE terminal_session='${SAFE_SESSION}' AND status IN ('pending', 'in_progress', 'submitted');" 2>/dev/null) || ACTIVE_TASKS="0"

  if [ "$ACTIVE_TASKS" -gt 0 ] 2>/dev/null; then
    # Active execution detected: deactivate PM only, preserve workflow_stage
    DEACTIVATE_CHANGES=$(sqlite3 "$DB_PATH" ".timeout 10000" \
      "UPDATE sessions SET professional_mode='off', updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}'; SELECT changes();" 2>/dev/null) || true
    if [ -n "$DEACTIVATE_CHANGES" ] && [ "$DEACTIVATE_CHANGES" != "0" ]; then
      db_audit_log "hook:state-activator" "professional_mode_off" "on" "off" "Active wave_tasks detected (${ACTIVE_TASKS}) — workflow_stage preserved"
      log_hook "state-activator" "Set" "professional-mode-off (workflow_stage preserved: ${ACTIVE_TASKS} active tasks)"
    else
      log_warning "state-activator" "Deactivation UPDATE affected 0 rows (session=${SESSION_TAG}); verification required."
    fi
  else
    # No active execution: safe to reset both PM and workflow_stage
    DEACTIVATE_CHANGES=$(sqlite3 "$DB_PATH" ".timeout 10000" \
      "UPDATE sessions SET professional_mode='off', workflow_stage='idle', updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}'; SELECT changes();" 2>/dev/null) || true
    if [ -n "$DEACTIVATE_CHANGES" ] && [ "$DEACTIVATE_CHANGES" != "0" ]; then
      db_audit_log "hook:state-activator" "professional_mode_off" "on" "off" ""
      log_hook "state-activator" "Set" "professional-mode-off"
    else
      log_warning "state-activator" "Deactivation UPDATE affected 0 rows (session=${SESSION_TAG}); verification required."
    fi
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
