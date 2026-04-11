#!/bin/bash
# session-init.sh — SessionStart hook
# Registers session via direct sqlite3 write.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "session-init"

INPUT=$(cat)

# Parse source field for PPID write adjudication (startup vs resume)
RAW_SOURCE=$(echo "$INPUT" | jq -r '.source // empty' 2>/dev/null || true)

init_session_id

PROJECT_HASH=$(echo "$PWD" | portable_md5)

# Ensure session row exists via direct sqlite3
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
SAFE_PROJECT_HASH=$(echo "$PROJECT_HASH" | sed "s/'/''/g")
ROWS_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sessions WHERE terminal_session = '${SAFE_SESSION}';" 2>/dev/null || echo "0")
sqlite3 "$DB_PATH" "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode, workflow_stage, project_hash, updated_at)
  VALUES ('${SAFE_SESSION}', 'undecided', 'idle', '${SAFE_PROJECT_HASH}', datetime('now'));" 2>/dev/null || true

# Log if we just created a new session (fallback INSERT succeeded)
if [ "$ROWS_BEFORE" = "0" ]; then
  log_hook "session-init" "State" "workflow_stage: null -> idle, professional_mode: null -> undecided (fallback INSERT)"
fi

log_hook "session-init" "Allowed" "Session registered"

# ═══ Stable hook directory ═══
# Copy hooks to version-independent location so existing sessions survive plugin upgrades.
STABLE_DIR="$HOME/.claude/ironclaude-hooks"
HOOK_SRC_DIR="$SCRIPT_DIR"

if [ -d "$HOOK_SRC_DIR" ]; then
  mkdir -p "$STABLE_DIR"
  # Copy all .sh files (hooks + sourced helpers like hook-logger.sh, plan-validator.sh)
  cp -f "$HOOK_SRC_DIR"/*.sh "$STABLE_DIR/" 2>/dev/null || true
  # Preserve executable permissions
  chmod +x "$STABLE_DIR"/*.sh 2>/dev/null || true
  log_hook "session-init" "Stable" "hooks copied to $STABLE_DIR"
fi

# ═══ Absolute statusline path ═══
# Write fully resolved path — no tilde, no symlink — so Claude Code can always find it.
SETTINGS_JSON="$HOME/.claude/settings.json"
VERSION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATUSLINE_PATH="$VERSION_DIR/hooks/statusline.sh"

if [ -f "$SETTINGS_JSON" ] && command -v jq &>/dev/null; then
  CURRENT_CMD=$(jq -r '.statusLine.command // empty' "$SETTINGS_JSON" 2>/dev/null || true)
  if [ -z "$CURRENT_CMD" ]; then
    # INSERT: No statusLine yet — add full object (type + command required by Claude Code)
    TMP_SETTINGS=$(mktemp)
    if jq --arg cmd "$STATUSLINE_PATH" '.statusLine = {"type": "command", "command": $cmd}' "$SETTINGS_JSON" > "$TMP_SETTINGS" 2>/dev/null && [ -s "$TMP_SETTINGS" ]; then
      mv "$TMP_SETTINGS" "$SETTINGS_JSON"
      log_hook "session-init" "Settings" "statusline inserted: $STATUSLINE_PATH"
    else
      rm -f "$TMP_SETTINGS"
    fi
  elif [ "$CURRENT_CMD" != "$STATUSLINE_PATH" ]; then
    # UPDATE: statusLine exists but points to stale path — patch command only
    TMP_SETTINGS=$(mktemp)
    if jq --arg cmd "$STATUSLINE_PATH" '.statusLine.command = $cmd' "$SETTINGS_JSON" > "$TMP_SETTINGS" 2>/dev/null && [ -s "$TMP_SETTINGS" ]; then
      mv "$TMP_SETTINGS" "$SETTINGS_JSON"
      log_hook "session-init" "Settings" "statusline path updated to $STATUSLINE_PATH"
    else
      rm -f "$TMP_SETTINGS"
    fi
  fi
fi

# ═══ Pre-commit hook install ═══
# Auto-install version-check pre-commit hook from scripts/pre-commit
REPO_ROOT="$PWD"
PRE_COMMIT_SRC="$REPO_ROOT/scripts/pre-commit"
PRE_COMMIT_DST="$REPO_ROOT/.git/hooks/pre-commit"

if [ -f "$PRE_COMMIT_SRC" ] && [ -d "$REPO_ROOT/.git/hooks" ]; then
  if ! cmp -s "$PRE_COMMIT_SRC" "$PRE_COMMIT_DST" 2>/dev/null; then
    cp -f "$PRE_COMMIT_SRC" "$PRE_COMMIT_DST"
    chmod +x "$PRE_COMMIT_DST"
    log_hook "session-init" "Git" "pre-commit hook installed"
  fi
fi

# ═══ Stale state cache cleanup ═══
# Remove state cache files older than 24 hours
find "$HOME/.claude" -name "ironclaude-state-cache-*.json" -mtime +0 -delete 2>/dev/null || true
rm -f /tmp/.claude-block-throttle-* /tmp/.claude-bypass-counter-* 2>/dev/null || true

# ═══ Proactive maintenance: old log files ═══
# Remove worker log files, .done markers, and .brain_contact files older than 7 days.
# These accumulate from tmux pipe-pane logging and grow unbounded without cleanup.
find /tmp/ic-logs -type f \( -name "*.log" -o -name "*.done" -o -name "*.brain_contact" \) -mtime +7 -delete 2>/dev/null || true

# ═══ Proactive maintenance: stale decision files ═══
# Decision files are normally deleted after reading (protocol.py:read_pending_decisions).
# These are crash orphans — leftover when the daemon crashes mid-read.
find /tmp/ic/brain-decisions -name "*.json" -mtime +1 -delete 2>/dev/null || true

# ═══ NOTE: Episodic memory is NEVER cleaned ═══
# ~/.config/superpowers/conversation-archive/ and conversation-index/ are the user's
# persistent knowledge base. They grow over time but must NOT be pruned or rotated.

# ═══ Stale PPID session file cleanup (MCP-only) ═══
# PPID files are used ONLY by MCP servers for session binding.
# Hooks use the JSON payload session_id directly (see hook-logger.sh).
# Remove PPID files from dead Claude Code processes.
# On MSYS2/Git Bash, files are keyed by Windows PIDs (kill -0 only works with
# MSYS PIDs), so use tasklist for liveness checks on Windows.
for f in "$HOME/.claude"/ironclaude-session-*.id; do
  [ -f "$f" ] || continue
  stale_pid=$(basename "$f" | sed 's/ironclaude-session-//;s/\.id//')
  if [ -f /proc/$$/winpid ]; then
    # Windows: files keyed by Windows PIDs — use tasklist for liveness
    if ! MSYS_NO_PATHCONV=1 tasklist /FI "PID eq $stale_pid" /NH 2>/dev/null | grep -q "$stale_pid"; then
      rm -f "$f"
    fi
  else
    # Unix: files keyed by Unix PIDs — use kill -0
    if ! kill -0 "$stale_pid" 2>/dev/null; then
      rm -f "$f"
    fi
  fi
done

# ═══ Stale HTTP artifact cleanup ═══
# Remove .hook-port and .hook-token files from v1.0.6 (HTTP server eliminated in v1.0.7)
rm -f "$HOME/.claude/.hook-port" "$HOME/.claude/.hook-token" 2>/dev/null || true

# ═══ PPID session file (MCP-only) ═══
# Write session ID to PPID-keyed file so MCP servers can resolve their session.
# MCP servers read this via process.ppid — both share the claude process as parent.
# NOTE: Hooks do NOT use this file. They read session_id from the JSON payload.
#
# On MSYS2/Git Bash, $PPID=1 because Claude Code isn't an MSYS process.
# Claude Code spawns bash through an intermediate bash layer
# (bash → bash → claude.exe), so we walk up the process tree skipping
# bash.exe intermediaries to find claude.exe's PID. This matches what
# Node.js process.ppid returns in the MCP wrapper.
#
# Uses PowerShell Get-CimInstance (always available on Windows 10/11).
# wmic was removed on Windows 11 24H2+.
if [ -f /proc/$$/winpid ]; then
  BASH_WINPID=$(cat /proc/$$/winpid)
  # Guard: validate BASH_WINPID is an integer before interpolating into PowerShell/wmic
  if [[ ! "$BASH_WINPID" =~ ^[0-9]+$ ]]; then
    REAL_PPID="$PPID"
  else
    # Walk up process tree, skipping bash.exe intermediaries, to find Claude Code PID.
    # Single PowerShell invocation to minimize startup latency (~1-2s).
    REAL_PPID=$(powershell.exe -NoProfile -Command '
      $cur = '"$BASH_WINPID"'
      do {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur)
        if (-not $proc) { break }
        $cur = $proc.ParentProcessId
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur)
      } while ($parent -and $parent.Name -eq "bash.exe")
      Write-Output $cur
    ' 2>/dev/null | tr -d '\r\n') || true
    if [ -z "$REAL_PPID" ]; then
      # Fallback: try wmic for older Windows (pre-24H2) where PowerShell may lack Get-CimInstance
      REAL_PPID=$(MSYS_NO_PATHCONV=1 wmic process where "ProcessId=$BASH_WINPID" get ParentProcessId /value 2>/dev/null | sed -n 's/ParentProcessId=//p' | tr -d '\r\n') || true
    fi
    REAL_PPID="${REAL_PPID:-$PPID}"
  fi
else
  REAL_PPID="$PPID"
fi

# ═══ Capture-then-adjudicate PPID file write ═══
# Claude Code fires two concurrent SessionStart events on resume:
#   - source="startup" with an ephemeral process session ID
#   - source="resume"  with the stable conversation session ID
# Both race to write this PPID file. The resume event is always correct.
# Startup defers 1s to let resume go first; only writes on fresh sessions.
PPID_FILE="$HOME/.claude/ironclaude-session-${REAL_PPID}.id"
STAGE_DIR="$HOME/.claude/.ppid-stage-${REAL_PPID}"
mkdir -p "$STAGE_DIR"
printf '%s' "$SESSION_TAG" > "$STAGE_DIR/${RAW_SOURCE:-unknown}"

if [ "$RAW_SOURCE" = "resume" ]; then
  # Resume is always the conversation session — write PPID file immediately
  TMP_PPID_FILE=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
  printf '%s' "$SESSION_TAG" > "$TMP_PPID_FILE"
  mv "$TMP_PPID_FILE" "$PPID_FILE"
  log_hook "session-init" "PPID" "session file written (resume): $PPID_FILE"
elif [ "$RAW_SOURCE" = "startup" ]; then
  # Write immediately so MCP tools can bind during the 1-second resume-wait window.
  TMP_PPID_FILE=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
  printf '%s' "$SESSION_TAG" > "$TMP_PPID_FILE"
  mv "$TMP_PPID_FILE" "$PPID_FILE"
  log_hook "session-init" "PPID" "session file written early (startup): $PPID_FILE"
  sleep 1
  if [ -f "$STAGE_DIR/resume" ]; then
    log_hook "session-init" "PPID" "resume event superseded startup write"
  fi
  rm -rf "$STAGE_DIR"
else
  # Unknown source — write unconditionally (backward compat)
  TMP_PPID_FILE=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
  printf '%s' "$SESSION_TAG" > "$TMP_PPID_FILE"
  mv "$TMP_PPID_FILE" "$PPID_FILE"
  log_hook "session-init" "PPID" "session file written (unknown source): $PPID_FILE"
fi

# ═══ MCP error sideband cleanup ═══
# Truncate error log on session start so stale errors don't surface in new sessions
: > "$MCP_ERROR_LOG" 2>/dev/null || true

# ═══ Post-resume state snapshot ═══
# If session is mid-workflow or has review_pending, emit state snapshot as system message before Claude's first token.
# Reads SQLite directly — MCP is not available at session-init time.
export SAFE_SESSION
WORKFLOW_STAGE=$(sqlite3 "$DB_PATH" \
  "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "")
REVIEW_PENDING=$(sqlite3 "$DB_PATH" \
  "SELECT review_pending FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
export REVIEW_PENDING

if { [ -n "$WORKFLOW_STAGE" ] && [ "$WORKFLOW_STAGE" != "idle" ]; } || [ "$REVIEW_PENDING" = "1" ]; then
  SNAPSHOT=$(python3 - <<'PYEOF' 2>/dev/null
import json, subprocess, os, sys, re

db_path = os.path.expanduser("~/.claude/ironclaude.db")
session = os.environ.get("SAFE_SESSION", "")
if not session:
    sys.exit(0)
if not re.match(r'^[a-zA-Z0-9_-]+$', session):
    sys.exit(0)

def query(sql):
    r = subprocess.run(["sqlite3", db_path, sql], capture_output=True, text=True)
    return r.stdout.strip()

prof = query(f"SELECT professional_mode FROM sessions WHERE terminal_session='{session}';")
stage = query(f"SELECT workflow_stage FROM sessions WHERE terminal_session='{session}';")
plan_name = query(f"SELECT plan_name FROM sessions WHERE terminal_session='{session}';")
plan_json_str = query(f"SELECT plan_json FROM sessions WHERE terminal_session='{session}';")
current_wave = query(f"SELECT current_wave FROM sessions WHERE terminal_session='{session}';")
total_waves = query(f"SELECT MAX(wave_number) FROM wave_tasks WHERE terminal_session='{session}';")
tasks_raw = query(f"SELECT task_id, task_name, status FROM wave_tasks WHERE terminal_session='{session}' ORDER BY wave_number, task_id;")
review_pending = os.environ.get("REVIEW_PENDING", "0")

plan_goal = ""
if plan_json_str:
    try:
        plan_goal = json.loads(plan_json_str).get("goal", "")
    except Exception:
        pass

lines = ["[ironclaude] Session state:"]
lines.append(f"  Professional mode: {prof.upper()}")
lines.append(f"  Workflow stage: {stage}")
if plan_name:
    plan_line = f"  Plan: {plan_name}"
    if plan_goal:
        plan_line += f" \u2014 {plan_goal}"
    lines.append(plan_line)
if current_wave and total_waves:
    lines.append(f"  Wave: {current_wave} of {total_waves}")
if tasks_raw:
    lines.append("  Tasks:")
    symbols = {"review_passed": "\u2713", "in_progress": "\u2192", "submitted": "\u23f3"}
    for row in tasks_raw.split("\n"):
        parts = row.split("|")
        if len(parts) >= 3:
            task_id, task_name, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
            sym = symbols.get(status, "\u00b7")
            lines.append(f"    {sym} Task {task_id}: {task_name} ({status})")

# Prepend review gate warning if active — tells worker to call code-review skill on compaction recovery
if review_pending == "1":
    warning = [
        "\u26a0\ufe0f  WORKFLOW STATE RECOVERY:",
        f"  - workflow_stage: {stage}",
        "  - review_pending: true",
    ]
    if current_wave:
        warning.append(f"  - current_wave: {current_wave}")
    warning.append('  \u2192 Call skill "ironclaude:code-review" to clear the review gate before any Bash/Edit.')
    warning.append("")
    lines = warning + lines

content = "\n".join(lines)
print(json.dumps({"type": "system", "content": content}))
PYEOF
  )

  if [ -n "$SNAPSHOT" ]; then
    echo "$SNAPSHOT"
    log_hook "session-init" "Snapshot" "emitted state snapshot (stage=$WORKFLOW_STAGE, review_pending=$REVIEW_PENDING)"
  else
    # Python failed — emit minimal warning so Claude knows to call get_resume_state
    echo '{"type":"system","content":"[ironclaude] State read failed. Call get_resume_state to check status manually."}'
    log_hook "session-init" "Snapshot" "WARN: python3 snapshot failed, emitted fallback"
  fi
fi

exit 0
