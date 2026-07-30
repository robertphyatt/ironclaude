#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$SCRIPT_DIR/codex-brain-gated-actions.sh"
HOOKS_JSON="$SCRIPT_DIR/hooks.json"
TOKEN="codex-brain-gate-test-$$"
STATE_DIR="/tmp/ic/codex-brain-gate/$TOKEN"
TMP_DIR=$(mktemp -d /tmp/ironclaude-codex-brain-gate-test.XXXXXX)
PASS=0
FAIL=0

cleanup() {
  rm -rf "$STATE_DIR" "$TMP_DIR"
}
trap cleanup EXIT

if [ ! -f "$HOOK" ]; then
  echo "FAIL: production hook missing: $HOOK" >&2
  exit 1
fi

run_case() {
  local tool_name="$1"
  local tool_input="${2-}"
  [ -n "$tool_input" ] || tool_input='{}'
  local client="${3:-codex}"
  local role="${4:-brain}"
  local token="${5-$TOKEN}"
  local cwd="${6:-$TMP_DIR}"
  local output_file="$TMP_DIR/output"
  local error_file="$TMP_DIR/error"
  local payload
  payload=$(jq -cn \
    --arg tool_name "$tool_name" \
    --argjson tool_input "$tool_input" \
    --arg cwd "$cwd" \
    '{session_id:"codex-brain-gate-test",tool_name:$tool_name,tool_input:$tool_input,cwd:$cwd}')
  IC_ROLE="$role" \
  IRONCLAUDE_CLIENT="$client" \
  IRONCLAUDE_BRAIN_GATE_SESSION="$token" \
    bash "$HOOK" >"$output_file" 2>"$error_file" <<<"$payload"
  return $?
}

assert_rc() {
  local name="$1"
  local expected="$2"
  shift 2
  "$@"
  local actual=$?
  if [ "$actual" -eq "$expected" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: $name expected rc=$expected actual=$actual" >&2
    [ -s "$TMP_DIR/error" ] && cat "$TMP_DIR/error" >&2
  fi
}

arm_startup() {
  run_case "mcp__plugin_ironclaude_orchestrator__get_operator_messages" '{"hours_back":48}'
  run_case "mcp__plugin_ironclaude_orchestrator__update_ledger" '{}'
}

arm_action() {
  run_case "mcp__plugin_ironclaude_episodic-memory__search" '{"query":"parity"}'
  run_case "mcp__plugin_ironclaude_orchestrator__wiki_query" '{"keywords":"parity"}'
}

rm -rf "$STATE_DIR"
mkdir -p "$TMP_DIR/wiki"

assert_rc "Claude client inactive" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}' "claude" "brain"
assert_rc "worker role inactive" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}' "codex" "worker"
assert_rc "unrelated tool inactive" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__get_task_ledger" '{}'
assert_rc "near-miss action inactive" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker_extra" '{}'
assert_rc "unrelated provider prefix inactive" 0 run_case \
  "mcp__other_orchestrator__spawn_worker" '{}'
assert_rc "local near-miss action inactive" 0 run_case \
  "mcp__orchestrator__spawn_worker_extra" '{}'
assert_rc "recognized tool missing gate session blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}' "codex" "brain" ""

assert_rc "gated action needs startup" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
assert_rc "47-hour lookback does not arm" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__get_operator_messages" '{"hours_back":47}'
assert_rc "47-hour lookback still blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'

assert_rc "48-hour lookback arms" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__get_operator_messages" '{"hours_back":48}'
assert_rc "ledger update arms" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__update_ledger" '{}'
assert_rc "memory missing blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
assert_rc "memory arms" 0 run_case \
  "mcp__plugin_ironclaude_episodic-memory__search" '{"query":"parity"}'
assert_rc "wiki missing blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
assert_rc "wiki arms" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__wiki_query" '{"keywords":"parity"}'
assert_rc "complete gate allows" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
assert_rc "one-action pair consumed" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'

GATED_ACTIONS=(
  mcp__plugin_ironclaude_orchestrator__spawn_worker
  mcp__plugin_ironclaude_orchestrator__spawn_workers
  mcp__plugin_ironclaude_orchestrator__approve_plan
  mcp__plugin_ironclaude_orchestrator__reject_plan
  mcp__plugin_ironclaude_orchestrator__send_to_worker
  mcp__plugin_ironclaude_orchestrator__kill_worker
  mcp__plugin_ironclaude_ollama__pull_model
  mcp__plugin_ironclaude_ollama__remove_model
  mcp__plugin_ironclaude_ollama__create_model
)

for action in "${GATED_ACTIONS[@]}"; do
  arm_action
  assert_rc "$action gated and allowed" 0 run_case "$action" '{}'
done

LOCAL_ORCHESTRATOR_PREFIX="mcp__orchestrator__"

rm -rf "$STATE_DIR"
assert_rc "local prefix action needs startup" 2 run_case \
  "${LOCAL_ORCHESTRATOR_PREFIX}spawn_worker" '{}'
assert_rc "local prefix 48-hour lookback arms" 0 run_case \
  "${LOCAL_ORCHESTRATOR_PREFIX}get_operator_messages" '{"hours_back":48}'
assert_rc "local prefix ledger update arms" 0 run_case \
  "${LOCAL_ORCHESTRATOR_PREFIX}update_ledger" '{}'
assert_rc "local prefix action needs memory" 2 run_case \
  "${LOCAL_ORCHESTRATOR_PREFIX}spawn_worker" '{}'
assert_rc "local prefix memory arms" 0 run_case \
  "mcp__plugin_ironclaude_episodic-memory__search" '{"query":"parity"}'
assert_rc "local prefix action needs wiki" 2 run_case \
  "${LOCAL_ORCHESTRATOR_PREFIX}spawn_worker" '{}'
assert_rc "local prefix wiki arms" 0 run_case \
  "${LOCAL_ORCHESTRATOR_PREFIX}wiki_query" '{"keywords":"parity"}'

LOCAL_GATED_ACTIONS=(
  spawn_worker
  spawn_workers
  approve_plan
  reject_plan
  send_to_worker
  kill_worker
)
for action in "${LOCAL_GATED_ACTIONS[@]}"; do
  run_case "mcp__plugin_ironclaude_episodic-memory__search" '{"query":"parity"}'
  run_case "${LOCAL_ORCHESTRATOR_PREFIX}wiki_query" '{"keywords":"parity"}'
  assert_rc "local $action gated and allowed" 0 run_case \
    "${LOCAL_ORCHESTRATOR_PREFIX}${action}" '{}'
  assert_rc "local $action consumes one-action arms" 2 run_case \
    "${LOCAL_ORCHESTRATOR_PREFIX}${action}" '{}'
done

# Bare-prefix ollama tools. The `0` alone proves nothing — an UNGATED name also exits 0 via
# the `*)` fall-through — so the `2` on the repeat call, proving the one-action arm was
# consumed, is what distinguishes a gated tool from one the case never matched.
OLLAMA_LOCAL_ACTIONS=(
  mcp__ollama__pull_model
  mcp__ollama__remove_model
  mcp__ollama__create_model
)
for action in "${OLLAMA_LOCAL_ACTIONS[@]}"; do
  arm_action
  assert_rc "local $action gated and allowed" 0 run_case "$action" '{}'
  assert_rc "local $action consumes one-action arms" 2 run_case "$action" '{}'
done

# Each attested memory-tool form must arm the memory gate. Asserting rc=0 on the memory
# call itself would prove nothing — an UNRECOGNIZED tool also returns 0. Only the follow-on
# gated action discriminates: 0 if memory armed, 2 if the form was never recognized.
MEMORY_FORMS=(
  mcp__plugin_ironclaude_episodic-memory__search
  mcp__plugin_ironclaude_episodic_memory__search
  mcp__episodic-memory__search
  mcp__episodic_memory__search
)
for form in "${MEMORY_FORMS[@]}"; do
  rm -rf "$STATE_DIR"
  run_case "mcp__plugin_ironclaude_orchestrator__get_operator_messages" '{"hours_back":48}'
  run_case "mcp__plugin_ironclaude_orchestrator__update_ledger" '{}'
  run_case "$form" '{"query":"parity"}'
  run_case "mcp__plugin_ironclaude_orchestrator__wiki_query" '{"keywords":"parity"}'
  assert_rc "memory form $form arms the gate" 0 run_case \
    "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
done

# Negative control: the provider boundary must hold.
rm -rf "$STATE_DIR"
run_case "mcp__plugin_ironclaude_orchestrator__get_operator_messages" '{"hours_back":48}'
run_case "mcp__plugin_ironclaude_orchestrator__update_ledger" '{}'
run_case "mcp__other_episodic-memory__search" '{"query":"parity"}'
run_case "mcp__plugin_ironclaude_orchestrator__wiki_query" '{"keywords":"parity"}'
assert_rc "foreign provider memory form does NOT arm the gate" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'

GAME_TOOLS=(game_launch game_screenshot game_click game_type game_key game_kill)
GAME_PREFIXES=(
  "mcp__plugin_ironclaude_orchestrator__"
  "mcp__orchestrator__"
)
for prefix in "${GAME_PREFIXES[@]}"; do
  for tool in "${GAME_TOOLS[@]}"; do
    assert_rc "$prefix$tool denied" 2 run_case "${prefix}${tool}" '{}'
    if grep -Fq \
      "Game tools cannot be used directly by the brain. Use spawn_worker for game operations." \
      "$TMP_DIR/error"; then
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
      echo "FAIL: $prefix$tool missing Claude game-denial guidance" >&2
    fi
  done
done

arm_startup
arm_action
mkdir -p "$STATE_DIR/.lock"
printf '%s\n' "$$" >"$STATE_DIR/.lock/pid"
assert_rc "live lock contention blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
rm -rf "$STATE_DIR/.lock"

cat >"$TMP_DIR/wiki/tasks.md" <<'EOF'
## Data

```json
{"tasks": [{"id": "t1", "status": "in_progress", "status_set_at": "2000-01-01T00:00:00+00:00"}]}
```
EOF
arm_action
assert_rc "stale per-task timestamp blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
cat >"$TMP_DIR/wiki/tasks.md" <<'EOF'
## Data

```json
{"tasks": [{"id": "t1", "status": "in_progress", "status_set_at": "2999-01-01T00:00:00+00:00"}]}
```
EOF
assert_rc "stale rejection preserves memory/wiki" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'

cat >"$TMP_DIR/wiki/tasks.md" <<'EOF'
"status": "in_progress"
EOF
python3 - "$TMP_DIR/wiki/tasks.md" <<'PY'
import os
import sys
import time
path = sys.argv[1]
old = time.time() - 35 * 60
os.utime(path, (old, old))
PY
arm_action
assert_rc "stale ledger mtime blocks" 2 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'

printf '%s\n' '{"tasks":malformed}' >"$TMP_DIR/wiki/tasks.md"
arm_action
assert_rc "malformed ledger fails open like Claude" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'
rm -f "$TMP_DIR/wiki/tasks.md"
arm_action
assert_rc "missing ledger fails open like Claude" 0 run_case \
  "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}'

arm_action
run_case "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}' >"$TMP_DIR/one.log" 2>&1 &
FIRST_PID=$!
run_case "mcp__plugin_ironclaude_orchestrator__spawn_worker" '{}' >"$TMP_DIR/two.log" 2>&1 &
SECOND_PID=$!
wait "$FIRST_PID"
FIRST_RC=$?
wait "$SECOND_PID"
SECOND_RC=$?
if { [ "$FIRST_RC" -eq 0 ] && [ "$SECOND_RC" -eq 2 ]; } ||
   { [ "$FIRST_RC" -eq 2 ] && [ "$SECOND_RC" -eq 0 ]; }; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: concurrent one-action consumption expected rc set {0,2}, got {$FIRST_RC,$SECOND_RC}" >&2
fi

EXPECTED_MATCHER='mcp__(plugin_ironclaude_)?episodic[-_]memory__.*|mcp__plugin_ironclaude_orchestrator__(wiki_query|get_operator_messages|update_ledger|spawn_worker|spawn_workers|approve_plan|reject_plan|send_to_worker|kill_worker|game_(launch|screenshot|click|type|key|kill))|mcp__orchestrator__(wiki_query|get_operator_messages|update_ledger|spawn_worker|spawn_workers|approve_plan|reject_plan|send_to_worker|kill_worker|game_(launch|screenshot|click|type|key|kill))|mcp__plugin_ironclaude_ollama__(pull_model|remove_model|create_model)|mcp__ollama__(pull_model|remove_model|create_model)'
ACTUAL_MATCHER=$(jq -r \
  '.hooks.PreToolUse[] | select(.hooks[].command | contains("codex-brain-gated-actions.sh")) | .matcher' \
  "$HOOKS_JSON")
if [ "$ACTUAL_MATCHER" = "$EXPECTED_MATCHER" ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: exact hook matcher missing" >&2
fi

# The pin above selects the PreToolUse entry only, so the PostToolUse state-manager matcher
# is structurally invisible to it. Without this second pin, that matcher's edit is unverified.
EXPECTED_POST_MATCHER='mcp__(plugin_ironclaude_)?state[-_]manager__(mark_design_ready|mark_plan_ready|mark_brainstorming|mark_executing|mark_debugging|create_plan|start_execution|retreat)'
ACTUAL_POST_MATCHER=$(jq -r \
  '.hooks.PostToolUse[] | select(.hooks[].command | contains("mcp-state-logger.sh")) | .matcher' \
  "$HOOKS_JSON")
if [ "$ACTUAL_POST_MATCHER" = "$EXPECTED_POST_MATCHER" ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: exact PostToolUse state-manager matcher missing" >&2
fi

if jq empty "$HOOKS_JSON"; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: hooks.json is invalid" >&2
fi

if [ "$FAIL" -ne 0 ]; then
  echo "$FAIL CODEX BRAIN GATE TESTS FAILED ($PASS passed)" >&2
  exit 1
fi

echo "ALL CODEX BRAIN GATE TESTS PASSED"
