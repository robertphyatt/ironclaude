#!/bin/bash
# The Codex Brain's restriction hooks are registered at the END of each event in
# the plugin hooks.json (append-only keeps existing Codex trust keys), each behind
# codex-brain-hooks.sh, and every dispatched script ships from the repo.
set -u
WORKER_HOOKS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$WORKER_HOOKS/../.." && pwd)"
HJ="$WORKER_HOOKS/hooks.json"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
D='bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh'
EXPECTED_PRE=$(jq -cn --arg d "$D" '[
  {matcher:"Bash|exec_command", s:"block-push.sh"},
  {matcher:"mcp__orchestrator__.*", s:"wiki-synthesis-enforcer.sh"},
  {matcher:"mcp__orchestrator__.*", s:"attention-sweep-enforcer.sh"},
  {matcher:"Agent|Task|spawn_agent|collaborationspawn_agent", s:"brain-task-gate.sh"},
  {matcher:"Edit|Write|NotebookEdit|MultiEdit|Bash|exec_command|apply_patch", s:"brain-orchestrator-guard.sh"}
] | map({hooks:[{type:"command",command:($d + " " + .s)}], matcher:.matcher})')
EXPECTED_POST=$(jq -cn --arg d "$D" '[
  {matcher:"mcp__orchestrator__get_worker_status", s:"attention-sweep-arm.sh"},
  {matcher:"mcp__orchestrator__update_ledger", s:"block-pin-enforcer.sh"}
] | map({hooks:[{type:"command",command:($d + " " + .s)}], matcher:.matcher})')
EXPECTED_UPS=$(jq -cn --arg d "$D" '[{hooks:[{type:"command",command:($d + " brain-hooks-canary.sh")}]}]')
ACTUAL_PRE=$(jq -c '.hooks.PreToolUse[-5:]' "$HJ")
ACTUAL_POST=$(jq -c '.hooks.PostToolUse[-2:]' "$HJ")
ACTUAL_UPS=$(jq -c '.hooks.UserPromptSubmit[-1:]' "$HJ")
[ "$(jq -S . <<<"$ACTUAL_PRE")" = "$(jq -S . <<<"$EXPECTED_PRE")" ] && pass "PreToolUse ends with the 5 Brain entries" || fail "PreToolUse Brain tail" "$ACTUAL_PRE"
[ "$(jq -S . <<<"$ACTUAL_POST")" = "$(jq -S . <<<"$EXPECTED_POST")" ] && pass "PostToolUse ends with the 2 Brain entries" || fail "PostToolUse Brain tail" "$ACTUAL_POST"
[ "$(jq -S . <<<"$ACTUAL_UPS")" = "$(jq -S . <<<"$EXPECTED_UPS")" ] && pass "UserPromptSubmit ends with the canary" || fail "UserPromptSubmit canary tail" "$ACTUAL_UPS"
G0=$(jq -r '.hooks.PreToolUse[0].hooks[0].command' "$HJ")
[ "$G0" = "$D brain-gated-actions.sh" ] && pass "group 0 dispatches brain-gated-actions" || fail "group 0 command" "$G0"
while IFS= read -r script; do
  if [ -f "$REPO_ROOT/commander/hooks/$script" ] || [ -f "$WORKER_HOOKS/$script" ]; then pass "ships $script"; else fail "ships $script" "not in commander/hooks or worker/hooks"; fi
done < <(jq -r '.hooks[][] .hooks[].command | select(startswith("bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh ")) | split(" ") | last' "$HJ")
if jq -e '[.hooks[][] | select(.matcher == "")] | length == 0' "$HJ" >/dev/null; then pass "no empty matchers"; else fail "no empty matchers"; fi
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
