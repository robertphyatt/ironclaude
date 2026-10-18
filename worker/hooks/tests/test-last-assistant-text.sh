#!/usr/bin/env bash
# test-last-assistant-text.sh - ic_last_assistant_text (hook-logger.sh) and its
# callers: GBTW grading input and subagent-drift-detector.sh (v1.1.16 L2b-1 R1).
# Codex-shaped Stop input carries last_assistant_message and a rollout path that
# is not Claude JSONL; Claude-shaped input may carry either source.
set -u
export IRONCLAUDE_LOCAL_BACKOFF_DIR=/dev/null  # local-box backoff off (not a directory): each call tests its own chain; nothing touches the real ~/.ironclaude
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"

PASSES=0
FAILS=0
EXPECTED_PASSES=7
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

# shellcheck disable=SC1091
source "$HOOKS_DIR/hook-logger.sh"
trap 'rm -rf "$TMP_ROOT"; rm -f /tmp/.claude-block-throttle-ic-lat-e2e /tmp/.claude-bypass-counter-ic-lat-e2e' EXIT

if ! type ic_last_assistant_text >/dev/null 2>&1; then
  bad "ic_last_assistant_text is not defined in hook-logger.sh"
  printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
  exit 1
fi

# Claude transcript fixture: one assistant response (requestId req_1) with a text block.
CLAUDE_T="$TMP_ROOT/claude.jsonl"
jq -cn '{type:"assistant",requestId:"req_1",message:{content:[{type:"text",text:"Claude transcript text"}]}}' > "$CLAUDE_T"

# 1. Codex-shaped: the field is used; the rollout path is missing.
CODEX_IN=$(jq -cn --arg t "$TMP_ROOT/missing-rollout.jsonl" \
  '{session_id:"x",hook_event_name:"Stop",last_assistant_message:"Codex final text",transcript_path:$t,stop_hook_active:false}')
[ "$(ic_last_assistant_text "$CODEX_IN")" = "Codex final text" ] \
  && ok "1 codex last_assistant_message" || bad "1 codex last_assistant_message"

# 2. Claude-shaped with both sources: the field wins.
BOTH_IN=$(jq -cn --arg t "$CLAUDE_T" '{last_assistant_message:"Field text",transcript_path:$t}')
[ "$(ic_last_assistant_text "$BOTH_IN")" = "Field text" ] \
  && ok "2 field preferred over transcript" || bad "2 field preferred over transcript"

# 3. Claude-shaped without the field: transcript fallback.
T_ONLY=$(jq -cn --arg t "$CLAUDE_T" '{transcript_path:$t}')
[ "$(ic_last_assistant_text "$T_ONLY")" = "Claude transcript text" ] \
  && ok "3 transcript fallback" || bad "3 transcript fallback"

# 4. Neither source: empty.
NONE_IN=$(jq -cn --arg t "$TMP_ROOT/none.jsonl" '{transcript_path:$t}')
[ -z "$(ic_last_assistant_text "$NONE_IN")" ] \
  && ok "4 both absent -> empty" || bad "4 both absent -> empty"

# 5. GBTW end-to-end with Codex-shaped input. An unreachable Ollama endpoint makes
#    every grading call unavailable. The turn still approves, reports unavailable
#    evaluation honestly, and queues a next-turn warning. The missing transcript
#    must not short-circuit grading.
E2E_HOME="$TMP_ROOT/home"
mkdir -p "$E2E_HOME/.claude"
# The validator chain falls back to the Haiku-class model; keep this case hermetic.
mkdir -p "$TMP_ROOT/bin"
printf '#!/bin/bash\nexit 1\n' > "$TMP_ROOT/bin/claude"
printf '#!/bin/bash\nexit 1\n' > "$TMP_ROOT/bin/codex"
chmod +x "$TMP_ROOT/bin/claude" "$TMP_ROOT/bin/codex"
printf '{"session_id":"ic-lat-e2e","source":"startup"}' | HOME="$E2E_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$E2E_HOME/.claude/ironclaude.db" \
  "UPDATE sessions SET professional_mode='on', workflow_stage='idle' WHERE terminal_session='ic-lat-e2e';"
printf '%s' '{"validation_backend":"ollama","ollama":{"url":"http://127.0.0.1:9","model":"none"},"connect_timeout_seconds":1,"hook_validation_budget_seconds":2}' \
  > "$E2E_HOME/.claude/ironclaude-hooks-config.json"
E2E_IN=$(printf '%s' "$CODEX_IN" | jq -c '.session_id = "ic-lat-e2e"')
E2E_OUT=$(printf '%s' "$E2E_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE -u TRON_WORKER_ID -u IC_OLLAMA_CONFIG_PATH \
  HOME="$E2E_HOME" PATH="$TMP_ROOT/bin:$PATH" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
E2E_MSG=$(printf '%s' "$E2E_OUT" | jq -r '.systemMessage // empty' 2>/dev/null)
E2E_DECISION=$(printf '%s' "$E2E_OUT" | jq -r '.decision // empty' 2>/dev/null)
E2E_WARNING="$E2E_HOME/.claude/ironclaude-warnings-ic-lat-e2e.queue"
if [ "$E2E_DECISION" = "approve" ] \
  && [[ "$E2E_MSG" == *"evaluation unavailable"* ]] \
  && grep -qF 'evaluation unavailable' "$E2E_WARNING" 2>/dev/null; then
  ok "5 GBTW reports unavailable Codex evaluation"
else
  bad "5 GBTW reports unavailable Codex evaluation (got: $E2E_OUT)"
fi

# 6. subagent-drift-detector, Codex-shaped: an anti-pattern in last_assistant_message
#    blocks with no transcript. At HEAD it reads only transcript_path and does not block.
printf '{"session_id":"ic-lat-sad","source":"startup"}' | HOME="$E2E_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$E2E_HOME/.claude/ironclaude.db" \
  "UPDATE sessions SET professional_mode='on', workflow_stage='executing' WHERE terminal_session='ic-lat-sad';"
SAD_IN=$(jq -cn --arg t "$TMP_ROOT/missing-rollout.jsonl" \
  '{session_id:"ic-lat-sad",hook_event_name:"SubagentStop",last_assistant_message:"Shall we checkpoint here?",transcript_path:$t}')
SAD_OUT=$(printf '%s' "$SAD_IN" | HOME="$E2E_HOME" bash "$HOOKS_DIR/subagent-drift-detector.sh" 2>/dev/null)
[ "$(printf '%s' "$SAD_OUT" | jq -r '.decision // empty' 2>/dev/null)" = "block" ] \
  && ok "6 drift detector blocks on Codex-shaped anti-pattern" \
  || bad "6 drift detector blocks on Codex-shaped anti-pattern (got: $SAD_OUT)"

# 7. Negative control: clean text does not block.
SAD_OK=$(printf '%s' "$SAD_IN" | jq -c '.last_assistant_message = "Grade: A. Ready to proceed."' \
  | HOME="$E2E_HOME" bash "$HOOKS_DIR/subagent-drift-detector.sh" 2>/dev/null)
[ -z "$(printf '%s' "$SAD_OK" | jq -r '.decision // empty' 2>/dev/null)" ] \
  && ok "7 clean subagent text does not block" || bad "7 clean subagent text does not block (got: $SAD_OK)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
