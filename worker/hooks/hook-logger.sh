#!/bin/bash
# hook-logger.sh - Shared logging for all hooks
#
# NOTE: This file does NOT call run_hook/set -euo pipefail because:
#   - It defines the error handling that other hooks depend on
#   - It must be sourceable without side effects
#   - Errors here are handled explicitly
#
# Usage: source this file, then call log_hook at every exit
#
# Outputs JSON {"systemMessage": "..."} to stdout which Claude Code displays to user

# Windows PATH bootstrap: Claude Code may spawn hooks with a minimal PATH that
# lacks Git Bash core utilities (dirname, cut, grep, etc.). Ensure standard
# Unix tool directories are present. No-op on macOS/Linux where these are
# already in PATH.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  export PATH="/mingw64/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
fi

# Globals set by plan-validator.sh when LLM is called
VALIDATION_LLM_BACKEND="${VALIDATION_LLM_BACKEND:-}"
VALIDATION_LLM_RESPONSE="${VALIDATION_LLM_RESPONSE:-}"

# Session tag for cross-session debugging (full conversation-scoped session ID)
SESSION_TAG="${CLAUDE_SESSION_ID:-none}"

# Database path (shared across all hooks)
DB_PATH="$HOME/.claude/ironclaude.db"

# Error sideband log (MCP tool errors written here, surfaced by hooks)
MCP_ERROR_LOG="$HOME/.claude/ironclaude-errors.log"

# portable_md5 - Cross-platform MD5 hash (macOS uses md5, Linux/Windows use md5sum)
# Reads from stdin, outputs raw hex hash to stdout
portable_md5() {
  if command -v md5sum &>/dev/null; then
    md5sum | cut -d' ' -f1
  elif command -v md5 &>/dev/null; then
    md5 -q
  else
    # Fallback: use first 8 chars of sha256 if available
    shasum -a 256 2>/dev/null | cut -d' ' -f1 || echo "00000000"
  fi
}

# normalize_path - Convert Windows backslashes to forward slashes
# Claude Code sends backslash paths in JSON on Windows. No-op on macOS/Linux.
# Usage: VAR=$(normalize_path "$VAR")
normalize_path() {
  echo "${1//\\//}"
}

# portable_timeout - Cross-platform command timeout
# Tries: GNU timeout → Homebrew gtimeout → bash background watchdog
# Usage: portable_timeout 60 bash -c 'some command'
portable_timeout() {
  local secs="$1"
  shift
  if command -v timeout &>/dev/null; then
    timeout "$secs" "$@"
  elif command -v gtimeout &>/dev/null; then
    gtimeout "$secs" "$@"
  else
    # Pure bash fallback: run command in background, kill after $secs seconds
    "$@" &
    local cmd_pid=$!
    ( sleep "$secs" && kill "$cmd_pid" 2>/dev/null ) &
    local watchdog_pid=$!
    wait "$cmd_pid" 2>/dev/null
    local rc=$?
    kill "$watchdog_pid" 2>/dev/null
    wait "$watchdog_pid" 2>/dev/null
    return $rc
  fi
}

# is_plan_active WORKFLOW_STAGE
# Returns true (0) when workflow_stage indicates a plan is actively being worked on.
# Hooks should call this instead of comparing workflow_stage directly, so that
# new plan-active states (like "reviewing") are handled in one place.
is_plan_active() {
  local stage="$1"
  [ "$stage" = "executing" ] || [ "$stage" = "reviewing" ]
}

# init_session_id - Resolve session ID from Claude Code's JSON payload
# session_id is a common field in ALL hook events (Claude Code hook contract).
# Call AFTER INPUT=$(cat) in each hook.
init_session_id() {
  CLAUDE_SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
  export CLAUDE_SESSION_ID
  SESSION_TAG="${CLAUDE_SESSION_ID:-none}"
}

# json_escape VALUE
# Escapes a string for safe embedding in a JSON string literal.
# Order: backslash first (prevents double-escaping), then other characters.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"    # \  → \\  (must be first)
  s="${s//\"/\\\"}"    # "  → \"
  s="${s//$'\n'/\\n}"  # LF → \n
  s="${s//$'\r'/\\r}"  # CR → \r
  s="${s//$'\t'/\\t}"  # tab → \t
  printf '%s' "$s"
}

# log_hook HOOK_NAME DECISION [REASON]
# DECISION: "Allowed", "Blocked", "Skipped", etc.
# Outputs JSON systemMessage to stdout
log_hook() {
  local hook="$1"
  local decision="$2"
  local reason="${3:-}"

  # Gate verbose output: only emit Blocked/ERROR messages unless verbose mode is on
  if [[ "$decision" != "Blocked" && "$decision" != "BLOCKED" && "$decision" != "ERROR" ]]; then
    local verbose
    verbose=$(jq -r '.verbose_hook_logs // false' "$HOME/.claude/ironclaude-hooks-config.json" 2>/dev/null || echo "false")
    if [ "$verbose" != "true" ]; then
      return 0
    fi
  fi

  # Build message
  local prefix="✓"
  [[ "$decision" == "Blocked" || "$decision" == "BLOCKED" ]] && prefix="✗"
  [[ "$decision" == "ERROR" ]] && prefix="⚠️"

  local msg="${prefix} [${hook}|${SESSION_TAG}]: ${decision}"
  [ -n "$reason" ] && msg="${msg} - ${reason}"

  # Append LLM info if called
  if [ -n "$VALIDATION_LLM_BACKEND" ]; then
    msg="${msg} (${VALIDATION_LLM_BACKEND})"
  fi

  # JSON-encode message: escape all JSON-special characters
  msg=$(json_escape "$msg")

  echo "{\"systemMessage\": \"${msg}\"}"
}

# log_error HOOK_NAME ERROR_MESSAGE
# For when something goes wrong inside the hook
log_error() {
  local hook="$1"
  local error="$2"

  local msg="⚠️ [${hook}|${SESSION_TAG}]: ERROR - ${error}"
  local msg_escaped
  msg_escaped=$(json_escape "$msg")

  # Output to BOTH systemMessage (for user) AND stderr (for Claude)
  echo "{\"systemMessage\": \"${msg_escaped}\"}"
  echo "$msg" >&2
}

# log_warning HOOK_NAME MESSAGE
# Outputs warning that Claude should address (not a block, but attention required)
log_warning() {
  local hook="${1:-UNKNOWN-HOOK}"
  local message="${2:-Warning}"
  local msg="⚠️ [${hook}|${SESSION_TAG}]: WARNING - ${message}"
  msg=$(json_escape "$msg")

  echo "{\"systemMessage\": \"${msg}\"}"
}

# log_llm_result HOOK_NAME STATUS SUMMARY [RAW_RESPONSE]
# STATUS: "Passed" = clean approval (no raw response logged)
#         "Failed"/"Rejected" = logs full raw LLM response
# Use this for EVERY call_validation_llm result - DRY helper
log_llm_result() {
  local hook="$1"
  local status="$2"
  local summary="$3"
  local raw_response="${4:-}"

  if [ "$status" = "Passed" ]; then
    # Clean approval - simple log, no raw response
    log_hook "$hook" "Allowed" "$summary"
  else
    # Non-approval - include full raw LLM response
    local detail="$summary"
    if [ -n "$raw_response" ]; then
      detail="${summary} | LLM response: ${raw_response}"
    fi
    log_hook "$hook" "$status" "$detail"
  fi
}

# block_pretooluse HOOK_NAME REASON
# Blocks a PreToolUse hook by outputting reason to stderr and exiting 2.
# Claude Code contract: exit 2 = blocking error, stderr = feedback message.
# CRITICAL: Do NOT write to stdout here. When a hook reads stdin (INPUT=$(cat))
# and writes to stdout, Claude Code's pipe handling fails to honor exit 2.
block_pretooluse() {
  local hook="${1:-UNKNOWN-HOOK}"
  local reason="${2:-Blocked by professional mode}"

  # Build display message (same format as log_hook "Blocked")
  local prefix="✗"
  local display_msg="${prefix} [${hook}|${SESSION_TAG}]: Blocked - ${reason}"

  # Append LLM info if called
  if [ -n "$VALIDATION_LLM_BACKEND" ]; then
    display_msg="${display_msg} (${VALIDATION_LLM_BACKEND})"
  fi

  # Output to stderr ONLY — Claude Code reads stderr as feedback on exit 2
  echo "$display_msg" >&2
  exit 2
}

# block_stop HOOK_NAME REASON
# Outputs combined JSON for Stop hook blocking
# Combines systemMessage (for user) with decision (for Claude Code)
block_stop() {
  local hook="${1:-UNKNOWN-HOOK}"
  local reason="${2:-Continue working}"
  local display_msg="✗ [${hook}|${SESSION_TAG}]: Blocked - ${reason}"

  # Combined JSON with systemMessage + decision
  # Includes followup_message for Cursor compatibility (without it, Cursor silently approves)
  jq -n --arg reason "$reason" --arg msg "$display_msg" '{
    "decision": "block",
    "reason": $reason,
    "followup_message": $reason,
    "systemMessage": $msg
  }'

  exit 0  # Stop hooks use exit 0, blocking is via JSON decision field
}

# hard_fail HOOK_NAME REASON
# Outputs a CRITICAL error to stderr and exits 2 (fail-closed).
# Used when DB state cannot be read — never report fabricated values.
# Exit 2 = block the tool call. Fail-closed is safer than fail-open.
hard_fail() {
  local hook="$1"
  local reason="$2"
  echo "CRITICAL [$hook]: $reason" >&2
  exit 2
}

# db_read_or_fail HOOK_NAME QUERY
# Reads from sqlite3 with layered diagnostics. Hard-fails if any check fails.
# Returns query result on stdout. Uses DB_PATH and SESSION_TAG globals.
db_read_or_fail() {
  local hook="$1"
  local query="$2"

  # Layer 1: Prerequisites
  if ! command -v sqlite3 &>/dev/null; then
    hard_fail "$hook" "sqlite3 binary not found in PATH"
  fi
  if [ ! -f "$DB_PATH" ]; then
    hard_fail "$hook" "DB file does not exist: $DB_PATH"
  fi

  # Layer 2: WAL mode check
  local journal
  journal=$(sqlite3 "$DB_PATH" ".timeout 10000" "PRAGMA journal_mode;" 2>&1)
  if [ "$journal" != "wal" ]; then
    hard_fail "$hook" "Journal mode is '$journal', expected 'wal'. DB: $DB_PATH"
  fi

  # Layer 3: Session existence
  local safe_session
  safe_session=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  local row_count
  row_count=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT COUNT(*) FROM sessions WHERE terminal_session='${safe_session}';" 2>&1)
  if [ "$row_count" = "0" ] || [ -z "$row_count" ]; then
    hard_fail "$hook" "Session not found in DB: $SESSION_TAG (count=${row_count:-empty})"
  fi

  # Layer 4: Actual query with stderr captured
  local stderr_file
  stderr_file=$(mktemp /tmp/.claude-db-err-XXXXXX)
  local result
  result=$(sqlite3 "$DB_PATH" ".timeout 10000" "$query" 2>"$stderr_file")
  local stderr_content
  stderr_content=$(cat "$stderr_file" 2>/dev/null)
  rm -f "$stderr_file"

  if [ -z "$result" ]; then
    hard_fail "$hook" "Query returned empty. Check hook logs for details."
  fi

  echo "$result"
}

# db_read HOOK_NAME QUERY [DEFAULT]
# Like db_read_or_fail but returns DEFAULT (empty string) when query returns no rows.
# Use for queries where empty is a valid state (e.g., "is design consumed?").
db_read() {
  local hook="$1"
  local query="$2"
  local default="${3:-}"

  # Layer 1: Prerequisites
  if ! command -v sqlite3 &>/dev/null; then
    hard_fail "$hook" "sqlite3 binary not found in PATH"
  fi
  if [ ! -f "$DB_PATH" ]; then
    hard_fail "$hook" "DB file does not exist: $DB_PATH"
  fi

  # Layer 2: WAL mode check
  local journal
  journal=$(sqlite3 "$DB_PATH" ".timeout 10000" "PRAGMA journal_mode;" 2>&1)
  if [ "$journal" != "wal" ]; then
    hard_fail "$hook" "Journal mode is '$journal', expected 'wal'. DB: $DB_PATH"
  fi

  # Layer 3: Session existence
  local safe_session
  safe_session=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  local row_count
  row_count=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT COUNT(*) FROM sessions WHERE terminal_session='${safe_session}';" 2>&1)
  if [ "$row_count" = "0" ] || [ -z "$row_count" ]; then
    hard_fail "$hook" "Session not found in DB: $SESSION_TAG (count=${row_count:-empty})"
  fi

  # Layer 4: Query with timeout — return default on empty
  local result
  result=$(sqlite3 "$DB_PATH" ".timeout 10000" "$query" 2>/dev/null) || true
  if [ -z "$result" ]; then
    echo "$default"
  else
    echo "$result"
  fi
}

# db_write_or_fail HOOK_NAME QUERY
# Writes to sqlite3 with layered diagnostics. Hard-fails if write affects 0 rows.
# CRITICAL: Write + changes() MUST run in single sqlite3 call (same connection).
# Uses DB_PATH and SESSION_TAG globals.
db_write_or_fail() {
  local hook="$1"
  local query="$2"

  # Layer 1: Prerequisites
  if ! command -v sqlite3 &>/dev/null; then
    hard_fail "$hook" "sqlite3 binary not found in PATH"
  fi
  if [ ! -f "$DB_PATH" ]; then
    hard_fail "$hook" "DB file does not exist: $DB_PATH"
  fi

  # Layer 2: WAL mode check
  local journal
  journal=$(sqlite3 "$DB_PATH" ".timeout 10000" "PRAGMA journal_mode;" 2>&1)
  if [ "$journal" != "wal" ]; then
    hard_fail "$hook" "Journal mode is '$journal', expected 'wal'. DB: $DB_PATH"
  fi

  # Layer 3: Execute write + changes() in SINGLE connection with busy timeout
  local stderr_file
  stderr_file=$(mktemp /tmp/.claude-db-err-XXXXXX)
  local changes
  changes=$(sqlite3 "$DB_PATH" ".timeout 10000" "$query SELECT changes();" 2>"$stderr_file")
  local stderr_content
  stderr_content=$(cat "$stderr_file" 2>/dev/null)
  rm -f "$stderr_file"

  if [ -z "$changes" ] || [ "$changes" = "0" ]; then
    hard_fail "$hook" "Write affected 0 rows. Query: $query | stderr: ${stderr_content:-none} | session: $SESSION_TAG"
  fi
}

# db_audit_log ACTOR ACTION OLD_VALUE NEW_VALUE CONTEXT
# Best-effort audit log writer. Does NOT hard-fail — state write already succeeded.
db_audit_log() {
  local actor="${1//\'/\'\'}"
  local action="${2//\'/\'\'}"
  local old_val="${3:-}"
  old_val="${old_val//\'/\'\'}"
  local new_val="${4:-}"
  new_val="${new_val//\'/\'\'}"
  local context="${5:-}"
  context="${context//\'/\'\'}"
  local safe_session
  safe_session=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  sqlite3 "$DB_PATH" ".timeout 10000" \
    "INSERT INTO audit_log (timestamp, terminal_session, actor, action, old_value, new_value, context) VALUES (datetime('now'), '${safe_session}', '${actor}', '${action}', '${old_val}', '${new_val}', '${context}');" 2>/dev/null || true
}

# surface_mcp_errors - Read and display MCP error sideband log
# Called from UserPromptSubmit hooks (which have visible stdout).
# Atomically moves the file to prevent concurrent read/write issues.
surface_mcp_errors() {
  if [ ! -s "$MCP_ERROR_LOG" ]; then
    return 0
  fi

  # Atomic move to temp file (prevents concurrent write during read)
  local tmp_file
  tmp_file=$(mktemp /tmp/.claude-mcp-errors-XXXXXX)
  if mv "$MCP_ERROR_LOG" "$tmp_file" 2>/dev/null; then
    while IFS= read -r line; do
      if [ -n "$line" ]; then
        log_hook "MCP-ERROR" "Error" "$line"
      fi
    done < "$tmp_file"
    rm -f "$tmp_file"
  fi
}

# _hook_error_handler - Internal function for ERR trap
# Logs stack trace on unexpected failures
_hook_error_handler() {
  local hook="$1"
  local lineno="$2"
  local command="$3"
  local funcstack="${4:-main}"
  local linestack="${5:-0}"

  local trace="Stack trace:"
  local i=0
  for func in $funcstack; do
    local line
    line=$(echo "$linestack" | cut -d' ' -f$((i+1)))
    trace+=" -> ${func}():${line}"
    ((i++)) || true
  done

  log_error "$hook" "Unexpected failure at line $lineno: $command | $trace"
  exit 1
}

# run_hook HOOK_NAME
# Call at start of hook - enables strict mode and sets up error trap
# NOTE: Hooks that need to handle grep failures explicitly should NOT call this
#       and should instead add a comment explaining why.
run_hook() {
  local hook="$1"

  # Enable strict mode
  set -euo pipefail

  # Set up error trap with stack trace
  trap '_hook_error_handler "'"$hook"'" "$LINENO" "$BASH_COMMAND" "${FUNCNAME[*]:-main}" "${BASH_LINENO[*]:-0}"' ERR
}

# read_config_flag FLAG_NAME DEFAULT_VALUE
# Reads a boolean flag from ~/.claude/ironclaude-hooks-config.json
# Returns "true" or "false" (string)
read_config_flag() {
  local flag="$1"
  local default_val="${2:-true}"
  local config="$HOME/.claude/ironclaude-hooks-config.json"
  if [ -f "$config" ] && command -v jq &>/dev/null; then
    local val
    val=$(jq -r ".${flag} // empty" "$config" 2>/dev/null || true)
    if [ "$val" = "true" ] || [ "$val" = "false" ]; then
      echo "$val"
      return
    fi
  fi
  echo "$default_val"
}
