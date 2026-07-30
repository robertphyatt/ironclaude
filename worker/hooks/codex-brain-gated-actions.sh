#!/bin/bash
# Codex Brain parity for BrainClient._tool_guard_logic's stateful MCP action gate.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "codex-brain-gated-actions"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(jq -r '.tool_name // empty' <<<"$INPUT")
TOOL_INPUT=$(jq -c '.tool_input // {}' <<<"$INPUT")
EVENT_CWD=$(jq -r '.cwd // empty' <<<"$INPUT")

if [ "${IC_ROLE:-}" != "brain" ] || [ "${IRONCLAUDE_CLIENT:-}" != "codex" ]; then
  exit 0
fi

KIND=""
case "$TOOL_NAME" in
  mcp__episodic?memory__*|\
  mcp__plugin_ironclaude_episodic?memory__*)
    KIND="memory"
    ;;
  mcp__plugin_ironclaude_orchestrator__wiki_query|\
  mcp__orchestrator__wiki_query)
    KIND="wiki"
    ;;
  mcp__plugin_ironclaude_orchestrator__get_operator_messages|\
  mcp__orchestrator__get_operator_messages)
    KIND="slack"
    ;;
  mcp__plugin_ironclaude_orchestrator__update_ledger|\
  mcp__orchestrator__update_ledger)
    KIND="ledger"
    ;;
  mcp__plugin_ironclaude_orchestrator__game_launch|\
  mcp__plugin_ironclaude_orchestrator__game_screenshot|\
  mcp__plugin_ironclaude_orchestrator__game_click|\
  mcp__plugin_ironclaude_orchestrator__game_type|\
  mcp__plugin_ironclaude_orchestrator__game_key|\
  mcp__plugin_ironclaude_orchestrator__game_kill|\
  mcp__orchestrator__game_launch|\
  mcp__orchestrator__game_screenshot|\
  mcp__orchestrator__game_click|\
  mcp__orchestrator__game_type|\
  mcp__orchestrator__game_key|\
  mcp__orchestrator__game_kill)
    block_pretooluse "codex-brain-gated-actions" \
      "Game tools cannot be used directly by the brain. Use spawn_worker for game operations."
    ;;
  mcp__plugin_ironclaude_orchestrator__spawn_worker|\
  mcp__plugin_ironclaude_orchestrator__spawn_workers|\
  mcp__plugin_ironclaude_orchestrator__approve_plan|\
  mcp__plugin_ironclaude_orchestrator__reject_plan|\
  mcp__plugin_ironclaude_orchestrator__send_to_worker|\
  mcp__plugin_ironclaude_orchestrator__kill_worker|\
  mcp__orchestrator__spawn_worker|\
  mcp__orchestrator__spawn_workers|\
  mcp__orchestrator__approve_plan|\
  mcp__orchestrator__reject_plan|\
  mcp__orchestrator__send_to_worker|\
  mcp__orchestrator__kill_worker|\
  mcp__plugin_ironclaude_ollama__pull_model|\
  mcp__plugin_ironclaude_ollama__remove_model|\
  mcp__plugin_ironclaude_ollama__create_model|\
  mcp__ollama__pull_model|\
  mcp__ollama__remove_model|\
  mcp__ollama__create_model)
    KIND="action"
    ;;
  *)
    exit 0
    ;;
esac

GATE_SESSION="${IRONCLAUDE_BRAIN_GATE_SESSION:-}"
case "$GATE_SESSION" in
  ""|*[!A-Za-z0-9._-]*)
    block_pretooluse "codex-brain-gated-actions" \
      "Codex Brain gate session identity is missing or invalid."
    ;;
esac

STATE_DIR="/tmp/ic/codex-brain-gate/$GATE_SESSION"
LOCK_DIR="$STATE_DIR/.lock"
if ! mkdir -p "$STATE_DIR"; then
  block_pretooluse "codex-brain-gated-actions" \
    "Codex Brain gate state directory is unavailable."
fi

acquire_gate_lock() {
  local attempt lock_pid
  for ((attempt = 0; attempt < 50; attempt++)); do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      printf '%s\n' "$$" >"$LOCK_DIR/pid"
      return 0
    fi
    if [ -f "$LOCK_DIR/pid" ]; then
      lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
      if [[ "$lock_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
        rm -rf "$LOCK_DIR"
        continue
      fi
    fi
    sleep 0.01
  done
  return 1
}

if ! acquire_gate_lock; then
  block_pretooluse "codex-brain-gated-actions" \
    "Codex Brain gate state is busy; action blocked without consuming prerequisites."
fi

release_gate_lock() {
  rm -rf "$LOCK_DIR"
}
trap release_gate_lock EXIT

case "$KIND" in
  memory)
    touch "$STATE_DIR/memory-armed"
    exit 0
    ;;
  wiki)
    touch "$STATE_DIR/wiki-queried"
    exit 0
    ;;
  slack)
    HOURS_BACK=$(jq -r '.hours_back // 0' <<<"$TOOL_INPUT")
    if [[ "$HOURS_BACK" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
       awk -v hours="$HOURS_BACK" 'BEGIN { exit !(hours >= 48) }'; then
      touch "$STATE_DIR/lookback-slack"
    fi
    exit 0
    ;;
  ledger)
    touch "$STATE_DIR/lookback-ledger"
    exit 0
    ;;
esac

MISSING=()
[ -f "$STATE_DIR/lookback-slack" ] || MISSING+=("Slack lookback (≥48h)")
[ -f "$STATE_DIR/lookback-ledger" ] || MISSING+=("ledger update")
if [ "${#MISSING[@]}" -gt 0 ]; then
  MISSING_TEXT=$(IFS=", "; echo "${MISSING[*]}")
  block_pretooluse "codex-brain-gated-actions" \
    "Required before acting: $MISSING_TEXT. Complete startup lookback first."
fi

MISSING=()
[ -f "$STATE_DIR/memory-armed" ] || MISSING+=("episodic memory search")
[ -f "$STATE_DIR/wiki-queried" ] || MISSING+=("wiki query")
if [ "${#MISSING[@]}" -gt 0 ]; then
  MISSING_TEXT=$(IFS=", "; echo "${MISSING[*]}")
  block_pretooluse "codex-brain-gated-actions" \
    "Required before acting: $MISSING_TEXT. What would the Operator do?"
fi

ledger_stale_age() {
  if ! command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  python3 - "$EVENT_CWD" "$HOME/.claude/ironclaude-hooks-config.json" <<'PY'
import json
import os
import sys
import time
from datetime import datetime

try:
    cwd, config_path = sys.argv[1:3]
    if not cwd:
        raise SystemExit(0)
    tasks_path = os.path.join(cwd, "wiki", "tasks.md")
    if not os.path.exists(tasks_path):
        raise SystemExit(0)
    with open(tasks_path, encoding="utf-8") as handle:
        content = handle.read()
    if '"status": "in_progress"' not in content:
        raise SystemExit(0)

    threshold_minutes = 30
    task_threshold_hours = 4
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        threshold_minutes = int(config.get("ledger_staleness_threshold_minutes", 30))
        task_threshold_hours = int(config.get("task_staleness_threshold_hours", 4))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass

    age_minutes = int((time.time() - os.path.getmtime(tasks_path)) / 60)
    if age_minutes > threshold_minutes:
        print(age_minutes)
        raise SystemExit(0)

    parts = content.split("## Data")
    if len(parts) < 2:
        raise SystemExit(0)
    fence = parts[1].find("```")
    if fence == -1:
        raise SystemExit(0)
    after = parts[1][fence + 3:]
    newline = after.find("\n")
    if newline == -1:
        raise SystemExit(0)
    json_text = after[newline + 1:]
    end = json_text.find("```")
    if end == -1:
        raise SystemExit(0)
    data = json.loads(json_text[:end].strip())
    now = time.time()
    for task in data.get("tasks", []):
        if task.get("status") != "in_progress":
            continue
        status_set_at = task.get("status_set_at")
        if not status_set_at:
            continue
        changed_at = datetime.fromisoformat(status_set_at.replace("Z", "+00:00"))
        task_age_hours = (now - changed_at.timestamp()) / 3600
        if task_age_hours > task_threshold_hours:
            print(int(task_age_hours * 60))
            break
except Exception:
    pass
PY
}

STALE_AGE=$(ledger_stale_age)
if [ -n "$STALE_AGE" ]; then
  block_pretooluse "codex-brain-gated-actions" \
    "Ledger stale (${STALE_AGE}m without update). Call update_ledger to sync current state."
fi

rm -f "$STATE_DIR/memory-armed" "$STATE_DIR/wiki-queried"
exit 0
