#!/usr/bin/env bash
# test-execution-drift-check.sh - execution-drift-check.sh session attribution
# (v1.1.16 L2b-1 R5; 2026-10-15). A window is one shell command of this session,
# keyed by agent lineage (agent_id|main) and tool_use_id: PreToolUse
# Bash|exec_command snapshots dirt; its PostToolUse, or the same lineage's next
# PreToolUse, appends the delta to a per-task ledger; submit/Stop discard open
# windows. Drift = (ledger ∩ dirty now) - plan allowed_files. Changes made outside
# this session's commands never count.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$HOOKS_DIR/execution-drift-check.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
REPO="$TMP_ROOT/repo"
SESSION="ic-drift-session"
DB="$TMP_HOME/.claude/ironclaude.db"
LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${SESSION}.1.txt"
THROTTLE="/tmp/.claude-block-throttle-${SESSION}"
mkdir -p "$TMP_HOME/.claude"
rm -f "$THROTTLE"
trap 'rm -rf "$TMP_ROOT"; rm -f "$THROTTLE" "/tmp/.claude-bypass-counter-${SESSION}"' EXIT

PASSES=0
FAILS=0
EXPECTED_PASSES=54
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

invoke() {  # TOOL_NAME HOOK_EVENT -> exit code; stderr in $TMP_ROOT/err
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg t "$1" --arg e "$2" \
    '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:$t,tool_input:{task_id:1},tool_response:{content:[{type:"text",text:"{\"success\":true}"}]}}')
  printf '%s' "$input" | HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>"$TMP_ROOT/err"
  echo $?
}
shell_event() {  # EVENT KEY COMMAND [TOOL_NAME] [AGENT_ID] -> exit code
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg e "$1" --arg k "$2" --arg cmd "$3" \
      --arg t "${4:-Bash}" --arg a "${5:-}" \
    '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:$t,tool_use_id:$k,
      tool_input:(if $t == "exec_command" then {cmd:$cmd} else {command:$cmd} end)}
     | if $a == "" then . else .agent_id = $a end')
  printf '%s' "$input" | HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>"$TMP_ROOT/err"
  echo $?
}
open_windows() { find "$TMP_HOME/.claude" -name 'ironclaude-drift-window-*' | wc -l | tr -d ' '; }
CLAIM="mcp__plugin_ironclaude_state-manager__claim_task"
SUBMIT="mcp__plugin_ironclaude_state-manager__submit_task"

# Repo with committed files, a .gitignore, and dirt that predates the task.
git init -q "$REPO"
printf 'base\n' > "$REPO/pre.txt"
printf 'rename me\n' > "$REPO/rename-old.txt"
printf 'delete me\n' > "$REPO/del-me.txt"
printf 'ignored.log\n' > "$REPO/.gitignore"
git -C "$REPO" add pre.txt rename-old.txt del-me.txt .gitignore
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q -m init
printf 'dirty before claim\n' >> "$REPO/pre.txt"
printf 'untracked before claim\n' > "$REPO/pre-untracked.txt"

# Session: executing, wave 1, task 1 in progress; plan allows src/allowed.txt (task 1) and docs/other.md (task 2).
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1, plan_name='t',
  plan_json='{\"name\":\"t\",\"tasks\":[{\"id\":1,\"allowed_files\":[\"src/allowed.txt\"]},{\"id\":2,\"allowed_files\":[\"docs/other.md\"]}]}'
  WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'in_progress');"
invoke "$CLAIM" "PostToolUse" >/dev/null

# 1. This session's own command: an allowed file, a gitignored file and a
#    further change to an already-dirty file. Only the allowed file enters the
#    ledger, and submit passes.
shell_event PreToolUse w1 "mkdir -p src" >/dev/null
mkdir -p "$REPO/src"
printf 'allowed\n' > "$REPO/src/allowed.txt"
printf 'ignored\n' > "$REPO/ignored.log"
printf 'changed again\n' >> "$REPO/pre.txt"
shell_event PostToolUse w1 "mkdir -p src" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && grep -qxF "src/allowed.txt" "$LEDGER" 2>/dev/null \
   && ! grep -qxF "pre.txt" "$LEDGER" && [ "$(open_windows)" = "0" ]; then
  ok "1 own allowed, gitignored and pre-dirty changes pass; ledger holds the new path only"
else
  bad "1 own allowed, gitignored and pre-dirty changes pass (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi

# 2. Another session's file, written between this session's commands, does not block.
mkdir -p "$REPO/foreign"
printf 'other session\n' > "$REPO/foreign/other-session.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "2 a file changed outside this session's commands does not block" || bad "2 a file changed outside this session's commands does not block (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 3. A read-only command opens no window, so a foreign write during it does not block.
shell_event PreToolUse r1 "cat pre.txt" >/dev/null
WIN_DURING_READ=$(open_windows)
printf 'during read\n' > "$REPO/foreign/during-read.txt"
shell_event PostToolUse r1 "cat pre.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && [ "$WIN_DURING_READ" = "0" ] && ok "3 read-only command opens no window" || bad "3 read-only command opens no window (rc=$RC windows=$WIN_DURING_READ)"

# 4. Professional mode off: no window is opened and submit is not checked.
sqlite3 "$DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='${SESSION}';"
shell_event PreToolUse off1 "touch side/pm-off.txt" >/dev/null
WIN_PM_OFF=$(open_windows)
mkdir -p "$REPO/side"
printf 'pm off\n' > "$REPO/side/pm-off.txt"
shell_event PostToolUse off1 "touch side/pm-off.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && [ "$WIN_PM_OFF" = "0" ] && ok "4 professional mode off opens no window and skips the check" || bad "4 professional mode off (rc=$RC windows=$WIN_PM_OFF)"
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on' WHERE terminal_session='${SESSION}';"

# 5. This session's own side quest blocks; only it is listed, with the new wording.
shell_event PreToolUse w2 "touch side/quest.txt" >/dev/null
printf 'quest\n' > "$REPO/side/quest.txt"
shell_event PostToolUse w2 "touch side/quest.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qF "BLOCKED — EXECUTION DRIFT" \
   && printf '%s' "$ERR" | grep -qxF "  - side/quest.txt" \
   && printf '%s' "$ERR" | grep -qF "never delete, revert or edit a file you did not change" \
   && ! printf '%s' "$ERR" | grep -qxF "  - src/allowed.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - pre.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - pre-untracked.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - ignored.log" \
   && ! printf '%s' "$ERR" | grep -qxF "  - foreign/other-session.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - foreign/during-read.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - side/pm-off.txt"; then
  ok "5 own side quest blocks and is the only path listed"
else
  bad "5 own side quest blocks and is the only path listed (rc=$RC: $ERR)"
fi

# 6. The underscore MCP name form also blocks.
RC=$(invoke "mcp__state_manager__submit_task" "PreToolUse")
[ "$RC" = "2" ] && ok "6 alternate MCP name form blocks" || bad "6 alternate MCP name form blocks (rc=$RC)"

# 7. Stop while executing blocks through increment_block_counter + block_stop.
STOP_IN=$(jq -cn --arg s "$SESSION" --arg c "$REPO" '{session_id:$s,hook_event_name:"Stop",stop_hook_active:false,cwd:$c,last_assistant_message:"Working."}')
STOP_OUT=$(printf '%s' "$STOP_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
if printf '%s' "$STOP_OUT" | jq -cse 'length == 1 and .[0].decision == "block" and (.[0].reason | contains("BLOCKED — EXECUTION DRIFT")) and (.[0].reason | contains("side/quest.txt")) and ((.[0].reason | contains("foreign/other-session.txt")) | not)' >/dev/null 2>&1 \
   && grep -q '^1:' "$THROTTLE" 2>/dev/null; then
  ok "7 Stop blocks on own drift only and increments the block counter"
else
  bad "7 Stop blocks on own drift only and increments the block counter (out: $STOP_OUT)"
fi

# 8-9. hooks.json: each drift entry is registered exactly once and its matcher is bounded.
POST_M=$(jq -r '[.hooks.PostToolUse[] | select(.hooks[0].command | contains("execution-drift-check.sh"))] | if length == 1 then .[0].matcher else "" end' "$HOOKS_DIR/hooks.json")
PRE_M=$(jq -r '[.hooks.PreToolUse[] | select(.hooks[0].command | contains("execution-drift-check.sh"))] | if length == 1 then .[0].matcher else "" end' "$HOOKS_DIR/hooks.json")
if [ -n "$POST_M" ] && matches "$POST_M" "Bash" && matches "$POST_M" "exec_command" \
   && ! matches "$POST_M" "$CLAIM" && ! matches "$POST_M" "$SUBMIT" \
   && ! matches "$POST_M" "Read" && ! matches "$POST_M" "BashOutput"; then
  ok "8 PostToolUse shell matcher is registered once and bounded"
else
  bad "8 PostToolUse shell matcher is registered once and bounded (matcher: $POST_M)"
fi
if [ -n "$PRE_M" ] && matches "$PRE_M" "$SUBMIT" && matches "$PRE_M" "mcp__state_manager__submit_task" \
   && matches "$PRE_M" "Bash" && matches "$PRE_M" "exec_command" \
   && ! matches "$PRE_M" "$CLAIM" && ! matches "$PRE_M" "mcp__other_state-manager__submit_task" \
   && ! matches "$PRE_M" "Read" && ! matches "$PRE_M" "Grep"; then
  ok "9 PreToolUse submit+shell matcher is registered once and bounded"
else
  bad "9 PreToolUse submit+shell matcher is registered once and bounded (matcher: $PRE_M)"
fi

# 10. This session's own deletion of a tracked file blocks.
rm -f "$REPO/side/quest.txt"
shell_event PreToolUse w3 "rm del-me.txt" >/dev/null
rm -f "$REPO/del-me.txt"
shell_event PostToolUse w3 "rm del-me.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - del-me.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - side/quest.txt"; then
  ok "10 own deletion of a tracked file blocks"
else
  bad "10 own deletion of a tracked file blocks (rc=$RC: $ERR)"
fi

# 11. A ledger path that is no longer dirty does not block.
git -C "$REPO" checkout -q -- del-me.txt
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && grep -qxF "del-me.txt" "$LEDGER"; then
  ok "11 a reverted ledger path no longer blocks"
else
  bad "11 a reverted ledger path no longer blocks (rc=$RC: $(cat "$TMP_ROOT/err"))"
fi

# 12. Codex shape: a main-lineage window still open at submit is a leftover; settle
#     discards it (R3c + R3f), so its delta is not counted and the window is gone.
shell_event PreToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
printf 'x\n' > "$REPO/codex-side.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
WIN_AT_SUBMIT=$(open_windows)
shell_event PostToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
if [ "$RC" = "0" ] && [ "$WIN_AT_SUBMIT" = "0" ] && [ "$(open_windows)" = "0" ] \
   && ! grep -qxF "codex-side.txt" "$LEDGER"; then
  ok "12 submit discards a leftover main-lineage window"
else
  bad "12 submit discards a leftover main-lineage window (rc=$RC at_submit=$WIN_AT_SUBMIT after_post=$(open_windows): $ERR)"
fi
rm -f "$REPO/codex-side.txt"

# 13. A subagent-shaped payload (agent_id set, parent session_id) is attributed.
shell_event PreToolUse s1 "printf y > sub-side.txt" Bash agent-1 >/dev/null
printf 'y\n' > "$REPO/sub-side.txt"
shell_event PostToolUse s1 "printf y > sub-side.txt" Bash agent-1 >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
[ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - sub-side.txt" && ok "13 subagent shell write blocks" || bad "13 subagent shell write blocks (rc=$RC: $ERR)"
rm -f "$REPO/sub-side.txt"

# 14-15. A staged rename inside a window changes both Git paths.
RENAME_PLAN=$(jq -cn '{name:"t",tasks:[{id:1,allowed_files:["src/allowed.txt","src/renamed.txt"]}]}')
sqlite3 "$DB" "UPDATE sessions SET plan_json='${RENAME_PLAN}' WHERE terminal_session='${SESSION}';"
shell_event PreToolUse w4 "git mv rename-old.txt src/renamed.txt" >/dev/null
git -C "$REPO" mv rename-old.txt src/renamed.txt
shell_event PostToolUse w4 "git mv rename-old.txt src/renamed.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF '  - rename-old.txt' \
   && ! printf '%s' "$ERR" | grep -qxF '  - src/renamed.txt'; then
  ok "14 rename source blocks when only destination is allowed"
else
  bad "14 rename source blocks when only destination is allowed (rc=$RC: $ERR)"
fi
RENAME_PLAN=$(jq -cn '{name:"t",tasks:[{id:1,allowed_files:["src/allowed.txt","src/renamed.txt","rename-old.txt"]}]}')
sqlite3 "$DB" "UPDATE sessions SET plan_json='${RENAME_PLAN}' WHERE terminal_session='${SESSION}';"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "15 rename passes when both paths are allowed" || bad "15 rename passes when both paths are allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 16. Concurrent subagents: B opening and closing a window never truncates A's,
#     so A's write after B finished is still attributed.
shell_event PreToolUse a1 "printf a > sub-a.txt" Bash agentA >/dev/null
shell_event PreToolUse b1 "printf b > sub-b.txt" Bash agentB >/dev/null
shell_event PostToolUse b1 "printf b > sub-b.txt" Bash agentB >/dev/null
WIN_AFTER_B=$(open_windows)
printf 'a\n' > "$REPO/sub-a.txt"
shell_event PostToolUse a1 "printf a > sub-a.txt" Bash agentA >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && [ "$WIN_AFTER_B" = "1" ] && printf '%s' "$ERR" | grep -qxF "  - sub-a.txt"; then
  ok "16 another lineage's window never truncates a concurrent subagent window"
else
  bad "16 another lineage's window never truncates a concurrent subagent window (rc=$RC after_b=$WIN_AFTER_B: $ERR)"
fi
rm -f "$REPO/sub-a.txt"

# 17. A leftover window (its command was denied, so no PostToolUse) is closed by the
#     same lineage's next PreToolUse, so a later foreign write is not attributed.
shell_event PreToolUse d1 "touch denied.txt" >/dev/null
shell_event PreToolUse r2 "cat pre.txt" >/dev/null
WIN_AFTER_NEXT=$(open_windows)
printf 'late\n' > "$REPO/late-foreign.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$WIN_AFTER_NEXT" = "0" ]; then
  ok "17 the same lineage's next PreToolUse closes its leftover window"
else
  bad "17 the same lineage's next PreToolUse closes its leftover window (rc=$RC windows=$WIN_AFTER_NEXT: $(cat "$TMP_ROOT/err"))"
fi

# 18. Stage not executing/reviewing: no window is opened.
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
shell_event PreToolUse x1 "touch stage.txt" >/dev/null
WIN_STAGE=$(open_windows)
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='${SESSION}';"
[ "$WIN_STAGE" = "0" ] && ok "18 no window outside executing/reviewing" || bad "18 no window outside executing/reviewing (windows=$WIN_STAGE)"

# 19. A shell event without tool_use_id opens no window.
shell_event PreToolUse "" "touch nokey.txt" >/dev/null
WIN_NOKEY=$(open_windows)
[ "$WIN_NOKEY" = "0" ] && ok "19 missing tool_use_id opens no window" || bad "19 missing tool_use_id opens no window (windows=$WIN_NOKEY)"

# 20. Copy's second porcelain record is source metadata, not source deletion.
COPY_PATHS=$(IC_DRIFT_LIB=1 HOOK="$HOOK" REPO="$REPO" bash -c '
  source "$HOOK"
  git() {
    case " $* " in
      *" rev-parse "*) return 0 ;;
      *" status "*) printf "C  copied.txt\\0source.txt\\0" ;;
    esac
  }
  _drift_dirty_paths "$REPO"
')
[ "$COPY_PATHS" = 'copied.txt' ] && ok "20 copy record emits destination only" || bad "20 copy record emits destination only ($COPY_PATHS)"

# A newline in a Git pathname must never become two independently allowed
# line-based entries. Check the actual submit and Stop hooks, with and without a ledger.
LF_PATH=$'src/allowed.txt\nother.txt'
printf 'newline path\n' > "$REPO/$LF_PATH"
LF_PLAN=$(jq -cn '{name:"t",tasks:[{id:1,allowed_files:["src/allowed.txt","other.txt","src/renamed.txt","rename-old.txt"]}]}')
sqlite3 "$DB" "UPDATE sessions SET plan_json='${LF_PLAN}' WHERE terminal_session='${SESSION}';"
assert_unsupported_submit() {
  local label="$1" rc err
  rc=$(invoke "$SUBMIT" "PreToolUse")
  err=$(cat "$TMP_ROOT/err")
  if [ "$rc" = "2" ] && printf '%s' "$err" | grep -qF 'unsupported newline Git path' \
    && ! printf '%s' "$err" | grep -qF 'BLOCKED — EXECUTION DRIFT'; then
    ok "$label"
  else
    bad "$label (rc=$rc: $err)"
  fi
}
assert_unsupported_stop() {
  local label="$1" out
  rm -f "$THROTTLE"
  out=$(printf '%s' "$STOP_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
  if printf '%s' "$out" | jq -cse 'length == 1 and .[0].decision == "block" and (.[0].reason | contains("unsupported newline Git path")) and (.[0].reason | contains("BLOCKED — EXECUTION DRIFT") | not)' >/dev/null 2>&1; then
    ok "$label"
  else
    bad "$label (out: $out)"
  fi
}
assert_unsupported_submit "21 LF path blocks submit despite split allowed entries"
assert_unsupported_stop "22 LF path blocks real Stop with distinct diagnostic"
cp "$LEDGER" "$TMP_ROOT/saved-ledger"
rm "$LEDGER"
assert_unsupported_submit "23 missing ledger cannot bypass LF submit guard"
assert_unsupported_stop "24 missing ledger cannot bypass LF Stop guard"
RC=$(shell_event PreToolUse lf1 "touch lf-window.txt")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ]; then
  ok "25 LF dirt cannot be recorded as a split window snapshot"
else
  bad "25 LF dirt cannot be recorded as a split window snapshot (rc=$RC windows=$(open_windows))"
fi
cp "$TMP_ROOT/saved-ledger" "$LEDGER"
rm "$REPO/$LF_PATH"

# Real Git staged rename records contain both paths. Either LF component is
# unsupported before allowed_files or the ledger can suppress it.
LF_SOURCE=$'lf-source\npart.txt'
printf 'rename content\n' > "$REPO/$LF_SOURCE"
git -C "$REPO" add -- "$LF_SOURCE"
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q -m lf-source -- "$LF_SOURCE"
git -C "$REPO" mv -- "$LF_SOURCE" "renamed-lf-source.txt"
assert_unsupported_submit "26 LF rename source blocks submit"
assert_unsupported_stop "27 LF rename source blocks Stop"
git -C "$REPO" mv -- "renamed-lf-source.txt" "$LF_SOURCE"
git -C "$REPO" reset -q -- "$LF_SOURCE" "renamed-lf-source.txt"
LF_DEST=$'lf-dest\npart.txt'
printf 'rename destination content\n' > "$REPO/clean-source.txt"
git -C "$REPO" add -- clean-source.txt
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q -m clean-source -- clean-source.txt
git -C "$REPO" mv -- clean-source.txt "$LF_DEST"
assert_unsupported_submit "28 LF rename destination blocks submit"
assert_unsupported_stop "29 LF rename destination blocks Stop"

# Git copy source is metadata, while destination is the dirty path.
COPY_RESULT=$(IC_DRIFT_LIB=1 HOOK="$HOOK" REPO="$REPO" bash -c '
  source "$HOOK"
  git() {
    case " $* " in
      *" rev-parse "*) return 0 ;;
      *" status "*) printf "C  copied.txt\\0source\\npart.txt\\0" ;;
    esac
  }
  _drift_dirty_paths "$REPO"
')
COPY_RC=$?
COPY_DEST_RESULT=$(IC_DRIFT_LIB=1 HOOK="$HOOK" REPO="$REPO" bash -c '
  source "$HOOK"
  git() {
    case " $* " in
      *" rev-parse "*) return 0 ;;
      *" status "*) printf "C  copied\\npart.txt\\0source.txt\\0" ;;
    esac
  }
  _drift_dirty_paths "$REPO"
')
COPY_DEST_RC=$?
[ "$COPY_RC" = "0" ] && [ "$COPY_RESULT" = 'copied.txt' ] && [ "$COPY_DEST_RC" = "2" ] && [ -z "$COPY_DEST_RESULT" ] && ok "30 copy ignores LF source metadata and rejects LF destination" || bad "30 copy status (source=$COPY_RC destination=$COPY_DEST_RC)"

# Undo the LF destination rename so windows can open again.
git -C "$REPO" mv -- "$LF_DEST" clean-source.txt
subagent_stop() {  # [AGENT_ID] -> SubagentStop event for this session
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg a "${1:-}" \
    '{session_id:$s,hook_event_name:"SubagentStop",cwd:$c} | if $a == "" then . else .agent_id = $a end')
  printf '%s' "$input" | HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>"$TMP_ROOT/err"
}

# 31. A finished subagent's leftover window (last command denied or failed, so no
#     PostToolUse) is closed at SubagentStop, so a later foreign write never enters the ledger.
shell_event PreToolUse e1 "touch e.txt" Bash agentE >/dev/null
WIN_BEFORE_STOP=$(open_windows)
subagent_stop agentE
WIN_AFTER_STOP=$(open_windows)
printf 'late\n' > "$REPO/late-sub.txt"
invoke "$SUBMIT" "PreToolUse" >/dev/null
if [ "$WIN_BEFORE_STOP" = "1" ] && [ "$WIN_AFTER_STOP" = "0" ] && ! grep -qxF "late-sub.txt" "$LEDGER"; then
  ok "31 SubagentStop closes that subagent's leftover window"
else
  bad "31 SubagentStop closes that subagent's leftover window (before=$WIN_BEFORE_STOP after=$WIN_AFTER_STOP)"
fi

# 32. A SubagentStop without agent_id never sweeps the main lineage's open window.
shell_event PreToolUse m1 "touch m.txt" >/dev/null
subagent_stop ""
WIN_MAIN_KEPT=$(open_windows)
shell_event PostToolUse m1 "touch m.txt" >/dev/null
if [ "$WIN_MAIN_KEPT" = "1" ] && [ "$(open_windows)" = "0" ]; then
  ok "32 SubagentStop without agent_id leaves the main lineage's window"
else
  bad "32 SubagentStop without agent_id leaves the main lineage's window (kept=$WIN_MAIN_KEPT after_post=$(open_windows))"
fi

# 33. A subagent's leftover window (its command failed or was denied) is discarded at
#     submit (R3g): a later foreign write is never charged.
shell_event PreToolUse q33 "touch q33.txt" Bash agentQ >/dev/null
printf 'gap\n' > "$REPO/gap-q33.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ] && ! grep -qxF "gap-q33.txt" "$LEDGER"; then
  ok "33 submit discards a subagent's leftover window"
else
  bad "33 submit discards a subagent's leftover window (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi

# 34. Stop discards an open subagent window without counting it (R3g): no drift block,
#     window gone, and its later PostToolUse finds nothing to close.
shell_event PreToolUse k1 "printf k > sub-k.txt" Bash agentK >/dev/null
printf 'k\n' > "$REPO/sub-k.txt"
rm -f "$THROTTLE"
STOP_OUT34=$(printf '%s' "$STOP_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
WIN_AFTER_STOP34=$(open_windows)
shell_event PostToolUse k1 "printf k > sub-k.txt" Bash agentK >/dev/null
if ! printf '%s' "$STOP_OUT34" | grep -qF "BLOCKED — EXECUTION DRIFT" \
   && [ "$WIN_AFTER_STOP34" = "0" ] && [ "$(open_windows)" = "0" ] \
   && ! grep -qxF "sub-k.txt" "$LEDGER"; then
  ok "34 Stop discards an open subagent window"
else
  bad "34 Stop discards an open subagent window (windows=$WIN_AFTER_STOP34 out: $STOP_OUT34)"
fi
rm -f "$REPO/sub-k.txt"

# 35-36. An unreadable window (missing file, or empty root line) appends nothing (R3d).
#        Run from the dirty test repo so an empty root would list its dirt.
R3D_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-r3d.1.txt"
R3D_WIN="$TMP_HOME/.claude/ironclaude-drift-window-r3d.main.k.txt"
printf '\npre.txt\n' > "$R3D_WIN"
(cd "$REPO" && IC_DRIFT_LIB=1 HOME="$TMP_HOME" HOOK="$HOOK" R3D_WIN="$R3D_WIN" bash -c '
  source "$HOOK"
  _drift_in_progress_rows() { printf "1\n"; }
  _drift_close_window_file "$R3D_WIN" r3d delete
')
[ ! -s "$R3D_LEDGER" ] && ok "35 empty-root window appends nothing" || bad "35 empty-root window appends nothing ($(cat "$R3D_LEDGER"))"
rm -f "$R3D_LEDGER" "$R3D_WIN"
(cd "$REPO" && IC_DRIFT_LIB=1 HOME="$TMP_HOME" HOOK="$HOOK" R3D_WIN="$R3D_WIN" bash -c '
  source "$HOOK"
  _drift_in_progress_rows() { printf "1\n"; }
  _drift_close_window_file "$R3D_WIN" r3d delete
')
[ ! -s "$R3D_LEDGER" ] && ok "36 missing window file appends nothing" || bad "36 missing window file appends nothing ($(cat "$R3D_LEDGER"))"

# 37. A leftover main window is closed by the next shell PreToolUse even outside
#     executing (R3e), so writes after a retreat are never attributed later.
shell_event PreToolUse s37 "touch s37.txt" >/dev/null
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
shell_event PreToolUse r37 "cat pre.txt" >/dev/null
WIN_AFTER_STAGE_PRE=$(open_windows)
printf 'later\n' > "$REPO/late-r3e.txt"
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='${SESSION}';"
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$WIN_AFTER_STAGE_PRE" = "0" ]; then
  ok "37 leftover window closes at the next shell PreToolUse outside executing"
else
  bad "37 leftover window closes at the next shell PreToolUse outside executing (rc=$RC windows=$WIN_AFTER_STAGE_PRE: $(cat "$TMP_ROOT/err"))"
fi

# 38. A command's own PostToolUse closes its window outside executing (R3e).
shell_event PreToolUse p38 "touch p38.txt" >/dev/null
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
shell_event PostToolUse p38 "touch p38.txt" >/dev/null
WIN_AFTER_STAGE_POST=$(open_windows)
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='${SESSION}';"
[ "$WIN_AFTER_STAGE_POST" = "0" ] && ok "38 PostToolUse closes its window outside executing" || bad "38 PostToolUse closes its window outside executing (windows=$WIN_AFTER_STAGE_POST)"

# 39. A write made between a leftover window and its lineage sweep (another session's,
#     after the denied/failed command ended) is discarded, not charged (R3f).
shell_event PreToolUse f39 "touch f39.txt" >/dev/null
printf 'gap\n' > "$REPO/gap-f39.txt"
shell_event PreToolUse r39 "cat pre.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ] && ! grep -qxF "gap-f39.txt" "$LEDGER"; then
  ok "39 lineage sweep discards a leftover window's delta"
else
  bad "39 lineage sweep discards a leftover window's delta (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi

# 40. Same at SubagentStop: a finished subagent's leftover window is discarded (R3f).
shell_event PreToolUse g40 "touch g40.txt" Bash agentG >/dev/null
printf 'gap\n' > "$REPO/gap-g40.txt"
subagent_stop agentG
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ] && ! grep -qxF "gap-g40.txt" "$LEDGER"; then
  ok "40 SubagentStop discards a leftover window's delta"
else
  bad "40 SubagentStop discards a leftover window's delta (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi

# 41. hooks.json registers the drift hook for SubagentStop exactly once.
SUBSTOP_N=$(jq '[.hooks.SubagentStop[] | select(.hooks[0].command | contains("execution-drift-check.sh"))] | length' "$HOOKS_DIR/hooks.json")
[ "$SUBSTOP_N" = "1" ] && ok "41 SubagentStop drift registration present once" || bad "41 SubagentStop drift registration present once (n=$SUBSTOP_N)"

# 42-53. Cross-session exclusion (2026-10-17 hotfix R2-R4; v120-completeness R4d). Another
#        session's recent file-tool write (writer log) or a live session's same-root
#        in-progress ledger path, charged to this session's window, never blocks; this
#        session's own shell side quest, a finished session's ledger, a completed row's
#        ledger, an old record and a record elsewhere still block.
GUARD="$HOOKS_DIR/professional-mode-guard.sh"
OTHER="ic-drift-other"
REPO_PHYS=$(cd "$REPO" && pwd -P)
ROOT_PHYS=$(git -C "$REPO" rev-parse --show-toplevel)
printf '{"session_id":"%s","source":"startup"}' "$OTHER" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming' WHERE terminal_session='${OTHER}';"
other_guard() {  # TOOL_NAME TOOL_INPUT_JSON -> the real guard's exit code for session OTHER
  local input
  input=$(jq -cn --arg s "$OTHER" --arg c "$REPO_PHYS" --arg t "$1" --argjson i "$2" \
    '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:$t,tool_input:$i}')
  printf '%s' "$input" | HOME="$TMP_HOME" bash "$GUARD" >/dev/null 2>"$TMP_ROOT/guard-err"
  echo $?
}
record_writer() {  # SESSION PATH: the library recorder, as the guard calls it
  printf '%s\n' "$2" | IC_DRIFT_LIB=1 HOME="$TMP_HOME" bash -c 'source "$1"; ic_drift_record_writes "$2" "$3"' _ "$HOOK" "$1" "$REPO"
}

# 42. Positive control: this session's own shell write outside allowed_files still blocks.
shell_event PreToolUse w42 "touch own42.txt" >/dev/null
printf 'own\n' > "$REPO/own42.txt"
shell_event PostToolUse w42 "touch own42.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR42=$(cat "$TMP_ROOT/err")
[ "$RC" = "2" ] && printf '%s' "$ERR42" | grep -qxF "  - own42.txt" && ok "42 own shell write outside allowed_files still blocks" || bad "42 own shell write outside allowed_files still blocks (rc=$RC: $ERR42)"

# 43. R4: side-quest and amend guidance stay; the wait-for-another-session line is gone.
if printf '%s' "$ERR42" | grep -qF "If you made the change as a side quest, undo your own change." \
   && printf '%s' "$ERR42" | grep -qF "retreat and amend the plan so it is listed in allowed_files" \
   && printf '%s' "$ERR42" | grep -qF "never delete, revert or edit a file you did not change" \
   && ! printf '%s' "$ERR42" | grep -qF "It clears by itself"; then
  ok "43 block text keeps the side-quest and amend guidance without the wait line"
else
  bad "43 block text keeps the side-quest and amend guidance without the wait line ($ERR42)"
fi
rm -f "$REPO/own42.txt"

# 44. The incident: another session's Claude Write of a design doc, made while this
#     session's command ran, is charged to the ledger but excluded by its writer record.
mkdir -p "$REPO/docs/plans"
shell_event PreToolUse w44 "touch build44" >/dev/null
G44=$(other_guard Write "$(jq -cn --arg p "$REPO_PHYS/docs/plans/x44-design.md" '{file_path:$p,content:"x"}')")
printf 'other design\n' > "$REPO/docs/plans/x44-design.md"
shell_event PostToolUse w44 "touch build44" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$G44" = "0" ] && [ "$RC" = "0" ] && grep -qxF "docs/plans/x44-design.md" "$LEDGER"; then
  ok "44 another session's Write during this session's window does not block"
else
  bad "44 another session's Write during this session's window does not block (guard=$G44 rc=$RC: $(cat "$TMP_ROOT/err"))"
fi
rm -f "$REPO/docs/plans/x44-design.md"

# 45. Another session's Codex apply_patch (every target) during this session's window.
sqlite3 "$DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='${OTHER}';"
P45=$'*** Begin Patch\n*** Add File: p45/a.txt\n+a\n*** Add File: p45b.txt\n+b\n*** End Patch'
shell_event PreToolUse w45 "touch build45" >/dev/null
G45=$(other_guard apply_patch "$(jq -cn --arg c "$P45" '{command:$c}')")
mkdir -p "$REPO/p45"
printf 'a\n' > "$REPO/p45/a.txt"
printf 'b\n' > "$REPO/p45b.txt"
shell_event PostToolUse w45 "touch build45" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$G45" = "0" ] && [ "$RC" = "0" ] && grep -qxF "p45/a.txt" "$LEDGER" && grep -qxF "p45b.txt" "$LEDGER"; then
  ok "45 another session's apply_patch during this session's window does not block"
else
  bad "45 another session's apply_patch during this session's window does not block (guard=$G45 rc=$RC: $(cat "$TMP_ROOT/err"))"
fi
rm -rf "$REPO/p45" "$REPO/p45b.txt"

# 46. A path in a live (executing) session's ledger for this same root does not block.
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='executing', current_wave=1 WHERE terminal_session='${OTHER}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${OTHER}', 1, 1, 'Other task', 'in_progress');"
OTHER_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${OTHER}';")
OTHER_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${OTHER}.${OTHER_ROW}.txt"
printf 'shared46.txt\n' > "$OTHER_LEDGER"
printf '%s\n' "$ROOT_PHYS" > "${OTHER_LEDGER%.txt}.root"
shell_event PreToolUse w46 "touch shared46.txt" >/dev/null
printf 'shared\n' > "$REPO/shared46.txt"
shell_event PostToolUse w46 "touch shared46.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && grep -qxF "shared46.txt" "$LEDGER" && ok "46 a path in another session's same-root ledger does not block" || bad "46 a path in another session's same-root ledger does not block (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/shared46.txt"

# 47. Negative: a live session's ledger for another root never excludes this session's
#     own write.
THIRD="ic-drift-third"
printf '{"session_id":"%s","source":"startup"}' "$THIRD" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1 WHERE terminal_session='${THIRD}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${THIRD}', 1, 1, 'Third task', 'in_progress');"
THIRD_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${THIRD}';")
THIRD_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${THIRD}.${THIRD_ROW}.txt"
printf 'n47.txt\n' > "$THIRD_LEDGER"
printf '/nonexistent/other-repo\n' > "${THIRD_LEDGER%.txt}.root"
shell_event PreToolUse w47 "touch n47.txt" >/dev/null
printf 'n\n' > "$REPO/n47.txt"
shell_event PostToolUse w47 "touch n47.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "2" ] && grep -qxF "  - n47.txt" "$TMP_ROOT/err" && ok "47 another root's ledger never excludes this session's own write" || bad "47 another root's ledger never excludes this session's own write (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/n47.txt"

# 48. Negative: only the NEWEST writer record counts; this session's later record keeps
#     its own write blocking.
record_writer "$OTHER" "o48.txt"
record_writer "$SESSION" "o48.txt"
shell_event PreToolUse w48 "touch o48.txt" >/dev/null
printf 'o\n' > "$REPO/o48.txt"
shell_event PostToolUse w48 "touch o48.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "2" ] && grep -qxF "  - o48.txt" "$TMP_ROOT/err" && ok "48 this session's newer writer record keeps its own write blocking" || bad "48 this session's newer writer record keeps its own write blocking (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/o48.txt"

# 49. A ledger records its root beside it, so (b) can match same-root ledgers only.
[ "$(cat "${LEDGER%.txt}.root" 2>/dev/null)" = "$ROOT_PHYS" ] && ok "49 a ledger records its root beside it" || bad "49 a ledger records its root beside it ($(cat "${LEDGER%.txt}.root" 2>&1))"

# 50. Negative: a finished (idle) session's same-root ledger never excludes.
DONE="ic-drift-done"
printf '{"session_id":"%s","source":"startup"}' "$DONE" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='idle', current_wave=1 WHERE terminal_session='${DONE}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${DONE}', 1, 1, 'Done task', 'completed');"
DONE_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${DONE}';")
DONE_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${DONE}.${DONE_ROW}.txt"
printf 's50.txt\n' > "$DONE_LEDGER"
printf '%s\n' "$ROOT_PHYS" > "${DONE_LEDGER%.txt}.root"
shell_event PreToolUse w50 "touch s50.txt" >/dev/null
printf 's\n' > "$REPO/s50.txt"
shell_event PostToolUse w50 "touch s50.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "2" ] && grep -qxF "  - s50.txt" "$TMP_ROOT/err" && ok "50 a finished session's same-root ledger never excludes this session's own write" || bad "50 a finished session's same-root ledger never excludes this session's own write (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/s50.txt"

# 51. Negative: a writer record older than this session's checked rows never excludes.
printf '%s\t%s\t%s\n' "$(( $(date +%s) - 86400 ))" "$OTHER" "$REPO_PHYS/s51.txt" >> "$TMP_HOME/.claude/ironclaude-drift-writers.tsv"
shell_event PreToolUse w51 "touch s51.txt" >/dev/null
printf 's\n' > "$REPO/s51.txt"
shell_event PostToolUse w51 "touch s51.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "2" ] && grep -qxF "  - s51.txt" "$TMP_ROOT/err" && ok "51 an old writer record never excludes this session's own write" || bad "51 an old writer record never excludes this session's own write (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/s51.txt"

# 52. Negative: a fresh record for a same-basename file in another directory never excludes.
record_writer "$OTHER" "$TMP_ROOT/elsewhere/x52.txt"
shell_event PreToolUse w52 "touch x52.txt" >/dev/null
printf 'x\n' > "$REPO/x52.txt"
shell_event PostToolUse w52 "touch x52.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "2" ] && grep -qxF "  - x52.txt" "$TMP_ROOT/err" && ok "52 a writer record for a same-basename file elsewhere never excludes" || bad "52 a writer record for a same-basename file elsewhere never excludes (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/x52.txt"

# 53. Negative: a completed row's same-root ledger never excludes this session's own
#     write, even while its session stays executing (v120-completeness R4d). Case 46 is
#     the positive control: the same session's in-progress row still excludes.
sqlite3 "$DB" "INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${OTHER}', 2, 1, 'Other finished task', 'completed');"
OTHER_DONE_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${OTHER}';")
OTHER_DONE_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${OTHER}.${OTHER_DONE_ROW}.txt"
printf 's53.txt\n' > "$OTHER_DONE_LEDGER"
printf '%s\n' "$ROOT_PHYS" > "${OTHER_DONE_LEDGER%.txt}.root"
STAGE53=$(sqlite3 "$DB" "SELECT workflow_stage FROM sessions WHERE terminal_session='${OTHER}';")
shell_event PreToolUse w53 "touch s53.txt" >/dev/null
printf 's\n' > "$REPO/s53.txt"
shell_event PostToolUse w53 "touch s53.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$STAGE53" = "executing" ] && [ "$RC" = "2" ] && grep -qxF "  - s53.txt" "$TMP_ROOT/err" && ok "53 a completed row's same-root ledger never excludes this session's own write" || bad "53 a completed row's same-root ledger never excludes this session's own write (stage=$STAGE53 rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/s53.txt"

# 54. A live session's ledger that records this repository through another spelling (a
#     symlinked path) still excludes: roots compare by file identity, not spelling
#     (design 2026-10-18 drift-foreign-session R1). Case 47 is the negative control.
ln -s "$ROOT_PHYS" "$TMP_ROOT/repo-link"
sqlite3 "$DB" "INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${OTHER}', 3, 1, 'Other linked task', 'in_progress');"
OTHER_LINK_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${OTHER}';")
OTHER_LINK_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${OTHER}.${OTHER_LINK_ROW}.txt"
printf 'l54.txt\n' > "$OTHER_LINK_LEDGER"
printf '%s\n' "$TMP_ROOT/repo-link" > "${OTHER_LINK_LEDGER%.txt}.root"
shell_event PreToolUse w54 "touch l54.txt" >/dev/null
printf 'l\n' > "$REPO/l54.txt"
shell_event PostToolUse w54 "touch l54.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && grep -qxF "l54.txt" "$LEDGER" && ok "54 another session's ledger for this repo under another spelling does not block" || bad "54 another session's ledger for this repo under another spelling does not block (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/l54.txt"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
