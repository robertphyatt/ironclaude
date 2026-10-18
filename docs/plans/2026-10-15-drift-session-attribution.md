# Drift Check Session Attribution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the execution drift check count only the files that changed during this session's own shell commands, so that other sessions' work in a shared checkout never blocks `submit_task` or Stop.

**Requirements:** `docs/plans/2026-10-15-drift-session-attribution-design.md` (§Requirements R1–R6, R3b)

**Architecture:** Each non-read-only `Bash`/`exec_command` call by this session opens a window: a snapshot of the dirty paths, keyed by agent lineage (`agent_id`, or `main`) and `tool_use_id`. The command's own PostToolUse closes and deletes it. The same lineage's next shell PreToolUse closes any of that lineage's leftovers (R3b). `submit_task` and Stop checkpoint every open window: they append the delta, re-snapshot and keep the window. Closing appends (dirty after − dirty before) to a ledger for each in-progress `wave_tasks` row. Drift = (ledger ∩ dirty now) − `allowed_files`. The claim-time baseline is retired.

**Tech Stack:** bash hooks, jq, sqlite3, git; bash test suites in `worker/hooks/tests/`.

Loop: full — guard/hook enforcement code shared by Claude and Codex.

## Execution invariants

- Shell state does not persist between steps. Every command uses literal absolute paths.
- The Bash cwd may not be the repo root, so use `git -C /Users/roberthyatt/Code/ironclaude`.
- `docs/` is gitignored. Stage plan or findings artifacts with `git add -f`.
- No `2>/dev/null` on evidence commands. Each suite prints `Results: N pass, M fail` and exits non-zero unless every case passed and the pass count equals `EXPECTED_PASSES`. That makes an empty or partial run fail.
- Hook scripts run under `run_hook` (`set -euo pipefail` + ERR trap, `hook-logger.sh:667-680`). New library code therefore uses `if`/`||` forms for expected non-zero statuses, like the existing `if paths=$(...); then :; else return $?; fi` idiom.
- `LC_ALL=C comm -23` must appear exactly once in `execution-drift-check.sh`, because `test-execution-drift-roots.sh` case 5 counts it.
- Never touch files outside `allowed_files`. The plan changes no other session's files.

### Measured at planning time (2026-10-15)

- `test-execution-drift-check.sh` has `EXPECTED_PASSES=22`, `test-execution-drift-roots.sh` has `=8`, and `test-execution-drift-identity.sh` has `=13`, or 12 on a case-sensitive volume.
- The drift hook is registered once in PreToolUse (`hooks.json:88-96`, matcher `mcp__(plugin_ironclaude_)?state[-_]manager__submit_task`) and once in PostToolUse (`hooks.json:198-206`, matcher `mcp__(plugin_ironclaude_)?state[-_]manager__claim_task`).
- `workspace_input_is_readonly` (`workspace-path-adapter.sh:25-34`) needs `is_readonly_git` and `is_readonly_research_bash` from `bash-readonly-guard.sh:58,98`.
- Nothing outside these files references the drift hook: `grep -rln` over `worker`, `commander/src` and the Makefile found only `get-back-to-work-impl.sh`, `execution-drift-check.sh`, `hooks.json` and the three suites.

---

## Task 1: Attribute drift to this session's shell commands

Executable hook code. TDD: rewrite the three suites first (RED), then the hook and hooks.json (GREEN).

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh` (header :1-27, add a source near :32, replace :216-245, :247-288, :319-379)
- Modify: `worker/hooks/hooks.json` (:95 and :205 matchers)
- Modify: `worker/hooks/get-back-to-work-impl.sh` (comment :487-493 only)
- Test: `worker/hooks/tests/test-execution-drift-check.sh` (full rewrite)
- Test: `worker/hooks/tests/test-execution-drift-roots.sh` (cases 1-7)
- Test: `worker/hooks/tests/test-execution-drift-identity.sh` (cases 1-5 setup)

### Step 1: Rewrite `worker/hooks/tests/test-execution-drift-check.sh` (RED)

Replace the whole file with:

```bash
#!/usr/bin/env bash
# test-execution-drift-check.sh - execution-drift-check.sh session attribution
# (v1.1.16 L2b-1 R5; 2026-10-15). A window is one shell command of this session,
# keyed by agent lineage (agent_id|main) and tool_use_id: PreToolUse
# Bash|exec_command snapshots dirt; its PostToolUse, or the same lineage's next
# PreToolUse, appends the delta to a per-task ledger; submit/Stop checkpoint open
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
EXPECTED_PASSES=30
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

# 12. Codex shape: submit while an exec_command window is still open checkpoints it:
#     the delta so far blocks and the window stays open until its own PostToolUse.
shell_event PreToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
printf 'x\n' > "$REPO/codex-side.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
WIN_AT_SUBMIT=$(open_windows)
shell_event PostToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - codex-side.txt" \
   && [ "$WIN_AT_SUBMIT" = "1" ] && [ "$(open_windows)" = "0" ]; then
  ok "12 submit checkpoints an open exec_command window and keeps it until PostToolUse"
else
  bad "12 submit checkpoints an open exec_command window (rc=$RC at_submit=$WIN_AT_SUBMIT after_post=$(open_windows): $ERR)"
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

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

### Step 2: Update `worker/hooks/tests/test-execution-drift-roots.sh` (RED)

1. Replace line 19 (`BASELINE=...`) with:
   ```bash
   WINDOW_K1="$TMP_HOME/.claude/ironclaude-drift-window-${SESSION}.main.k1.txt"
   ```
2. After the `invoke()` function (ends line 39), add:
   ```bash
   shell_event() {  # EVENT KEY [LC_ALL value] -> exit code (a mutating command, cwd = primary)
     local input
     input=$(jq -cn --arg s "$SESSION" --arg c "$PRIMARY" --arg e "$1" --arg k "$2" \
       '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:"Bash",tool_use_id:$k,tool_input:{command:"touch x"}}')
     printf '%s' "$input" | env LC_ALL="${3:-C}" HOME="$TMP_HOME" bash "$HOOK" >"$TMP_ROOT/out" 2>"$TMP_ROOT/err"
     echo $?
   }
   ```
3. Replace cases 1-4 (lines 103-124) with:
   ```bash
   # 1. F1: the window snapshot is taken from the worktree, not the primary checkout.
   RC=$(shell_event PreToolUse k1)
   if [ "$RC" = "0" ] && [ "$(head -n 1 "$WINDOW_K1" 2>/dev/null)" = "$MANAGED" ] \
      && grep -qxF "Zeta.txt" "$WINDOW_K1" 2>/dev/null && ! grep -qxF "primary-before.txt" "$WINDOW_K1" 2>/dev/null; then
     ok "1 window snapshot comes from the managed worktree"
   else
     bad "1 window snapshot comes from the managed worktree (rc=$RC)"
   fi
   shell_event PostToolUse k1 >/dev/null

   # 2. F1: an edit in the primary checkout during this session's command does not block.
   shell_event PreToolUse k2 >/dev/null
   printf 'o\n' > "$PRIMARY/primary-other.txt"
   shell_event PostToolUse k2 >/dev/null
   RC=$(invoke "$SUBMIT" "PreToolUse")
   [ "$RC" = "0" ] && ok "2 primary-checkout edits do not block" || bad "2 primary-checkout edits do not block (rc=$RC: $(cat "$TMP_ROOT/err"))"

   # 3. F2: an absolute allowed entry under the worktree root does not block.
   shell_event PreToolUse k3 >/dev/null
   mkdir -p "$MANAGED/abs"
   printf 'abs\n' > "$MANAGED/abs/allowed.txt"
   shell_event PostToolUse k3 >/dev/null
   RC=$(invoke "$SUBMIT" "PreToolUse")
   [ "$RC" = "0" ] && ok "3 absolute allowed entry under the root passes" || bad "3 absolute allowed entry under the root passes (rc=$RC: $(cat "$TMP_ROOT/err"))"

   # 4. F3 behavioral: a window opened and closed under a UTF-8 locale, with mixed-case
   #    and non-ASCII dirty paths on both sides, records no false delta.
   shell_event PreToolUse k4 "en_US.UTF-8" >/dev/null
   shell_event PostToolUse k4 "en_US.UTF-8" >/dev/null
   RC=$(invoke "$SUBMIT" "PreToolUse" "en_US.UTF-8")
   [ "$RC" = "0" ] && ok "4 no false drift under LC_ALL=en_US.UTF-8" || bad "4 no false drift under LC_ALL=en_US.UTF-8 (rc=$RC: $(cat "$TMP_ROOT/err"))"
   ```
4. Replace case 6's file creation (line 131 `printf 'quest\n' > "$MANAGED/side-wt.txt"`) with:
   ```bash
   shell_event PreToolUse k6 >/dev/null
   printf 'quest\n' > "$MANAGED/side-wt.txt"
   shell_event PostToolUse k6 >/dev/null
   ```
   Cases 5, 7 and 8 and `EXPECTED_PASSES=8` stay unchanged.

### Step 3: Update `worker/hooks/tests/test-execution-drift-identity.sh` (RED)

1. After the `invoke()` function (ends line 40), add:
   ```bash
   in_window() {  # KEY: run the remaining args as this session's own shell command
     local key="$1" input
     shift
     input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg k "$key" \
       '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:"Bash",tool_use_id:$k,tool_input:{command:"touch x"}}')
     printf '%s' "$input" | env LC_ALL=C HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>&1
     "$@"
     printf '%s' "$input" | jq -c '.hook_event_name = "PostToolUse"' | env LC_ALL=C HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>&1
   }
   ```
2. Replace lines 65-67 (the claim-baseline setup) with:
   ```bash
   # The task is in progress; only this session's own commands (in_window) enter the ledger.
   ```
3. Wrap each case's file change in a window:
   - Case 1, lines 70-71: `in_window i1 bash -c 'mkdir -p "$1/src" && printf "a\n" > "$1/src/a.txt"' _ "$REPO"`
   - Case 2, line 76: `in_window i2 rm -rf "$REPO/gone"`
   - Case 3, lines 82-83: `in_window i3 bash -c 'mkdir -p "$1/case" && printf "c\n" > "$1/case/c.txt"' _ "$REPO"`
   - Case 4, line 91: `in_window i4 bash -c 'printf "side\n" > "$1/side.txt"' _ "$REPO"`
   - Case 5, lines 104-106: `in_window i5 bash -c 'mkdir -p "$1/inside/deep" && printf "internal\n" > "$1/inside/deep/new.txt"' _ "$REPO"`, then `rm -f "$REPO/side.txt"` stays outside the window as written.

   Cases 6-13 and `EXPECTED_PASSES` stay unchanged.

### Step 4: Run the three suites against the unchanged hook (RED)

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh; echo "check=$?"
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh; echo "roots=$?"
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh; echo "identity=$?"
```

Expected: `check=1`, `roots=1`, `identity=1`. The unchanged hook ignores `Bash`/`exec_command` events. The rewritten suites also no longer record a claim baseline, which leaves the old hook failing open on missing baselines. Each suite must finish and exit 1. Record the measured FAIL labels verbatim; there is no predicted list to compare them against. If any suite exits 0, stop and report: its new cases would not be testing the change.

### Step 5: Edit `worker/hooks/execution-drift-check.sh` (GREEN)

1. **Header.** Replace lines 1-27 with:
   ```bash
   #!/bin/bash
   # execution-drift-check.sh - execution side-quest detector (v1.1.16 L2b-1 R5, L2b-1b;
   # session attribution 2026-10-15).
   #
   # Triggers (worker/hooks/hooks.json):
   #   PreToolUse   Bash|exec_command          -> close this agent lineage's leftover
   #                                              windows, then open one (dirty snapshot)
   #                                              unless the command is read-only
   #   PostToolUse  Bash|exec_command          -> close that command's window
   #   PreToolUse   state-manager submit_task  -> checkpoint open windows, block (exit 2) on drift
   #   Library      IC_DRIFT_LIB=1 source      -> _drift_effective_root and
   #                                              ic_drift_stop_violations, used by
   #                                              get-back-to-work-impl.sh at Stop
   #
   # Root: _drift_effective_root resolves the session's effective root exactly as the
   #   write guard does (workspace-path-adapter.sh workspace_bind_effective_root): a
   #   managed worktree (or the primary it owns) when the session has an active
   #   assignment, else the cwd's git toplevel.
   # Attribution: a window is one shell command of THIS session (subagent commands carry
   #   the parent's session_id), keyed by agent lineage (agent_id, else "main") and
   #   tool_use_id. Closing it appends (dirty after - dirty before) to a ledger per
   #   in-progress wave_tasks row. Other sessions' changes made between this session's
   #   commands never enter the ledger. submit_task and Stop checkpoint open windows
   #   (append the delta, re-snapshot, keep the window) so running commands stay tracked.
   # Drift = (ledger paths ∩ paths dirty now in the root; gitignored files never appear)
   #         minus the plan's tasks[].allowed_files: relative entries as-is, absolute
   #         entries under the root made root-relative, other absolute entries dropped.
   #         Exact string match only. A ledger path later committed or reverted drops out.
   # Known gaps (fail open, accepted 2026-10-15): Codex write_stdin input, background
   #   commands that write after their PostToolUse, files written by MCP tools, and (R3b)
   #   two parallel shell commands of one agent lineage: the second's PreToolUse closes
   #   the first's window early. A window with no PostToolUse (a command denied before it
   #   ran, or a failed Claude Bash that fires PostToolUseFailure) stays open until the
   #   same lineage's next shell PreToolUse.
   #
   # Fail-open (no block; the caller logs via log_hook): professional mode not 'on';
   #   stage not executing/reviewing; no cwd or cwd not in a git repo; assignment
   #   evidence unreadable, ambiguous or unbindable (reason in _DRIFT_ROOT_ERROR);
   #   session row, plan_json or its tasks array unreadable.
   # Fail-closed (block): a resolved root and readable plan with non-empty drift, or an
   #   unsupported newline path in current dirt.
   ```
2. **Source the read-only predicates.** After the line `source "$_DRIFT_HOOKS_DIR/workspace-path-adapter.sh" 2>/dev/null || true`, add:
   ```bash
   # is_readonly_git / is_readonly_research_bash for workspace_input_is_readonly. If missing,
   # every shell command counts as mutating (a window opens), which never under-attributes.
   source "$_DRIFT_HOOKS_DIR/bash-readonly-guard.sh" 2>/dev/null || true
   ```
3. **Windows and ledger.**
   - Delete `_drift_baseline_file` (the function starting `_drift_baseline_file() {  # SESSION ROW_ID`, current lines 216-220).
   - Delete `ic_drift_record_baseline` with its two-line comment (current lines 231-245).
   - Leave `_drift_row_id` and its comment unchanged and in place.
   - Insert the block below directly after `_drift_row_id`'s closing `}`.
   ```bash
   _drift_safe() { printf '%s' "$1" | tr -cd 'a-zA-Z0-9_-'; }

   # Window and ledger files. '.' separates fields because _drift_safe never emits it,
   # so a session or lineage glob can never match another session's or lineage's files.
   _drift_window_file() {  # SESSION LINEAGE KEY
     printf '%s/.claude/ironclaude-drift-window-%s.%s.%s.txt' "$HOME" "$(_drift_safe "$1")" "$(_drift_safe "$2")" "$(_drift_safe "$3")"
   }
   _drift_ledger_file() {  # SESSION ROW_ID
     printf '%s/.claude/ironclaude-drift-touched-%s.%s.txt' "$HOME" "$(_drift_safe "$1")" "$2"
   }

   # In-progress wave_tasks row ids of SESSION's current wave, one per line.
   _drift_in_progress_rows() {
     _drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.status='in_progress' AND w.wave_number = s.current_wave ORDER BY w.id;"
   }

   # ic_drift_open_window SESSION LINEAGE KEY ROOT  (ROOT from _drift_effective_root)
   # Status 0: window written or already open. Status 1: skipped (git or write failed).
   # Status 2: current dirt has an unsupported newline path; no window is written.
   # Line 1 of the window file is the root; the rest is the dirty snapshot.
   ic_drift_open_window() {
     local file paths tmp
     file=$(_drift_window_file "$1" "$2" "$3")
     if [ -e "$file" ]; then return 0; fi
     if paths=$(_drift_dirty_paths "$4"); then :; else return $?; fi
     mkdir -p "$(dirname "$file")" || return 1
     tmp="${file}.tmp.$$"
     { printf '%s\n' "$4"; printf '%s\n' "$paths"; } > "$tmp" || { rm -f "$tmp"; return 1; }
     mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
     return 0
   }

   # _drift_close_window_file FILE SESSION MODE
   # Append the window's delta (paths dirty now that were not dirty at its snapshot) to
   # each in-progress row's ledger. MODE=delete removes the window; MODE=checkpoint
   # re-snapshots it and keeps it open for a command that is still running.
   _drift_close_window_file() {
     local file="$1" session="$2" mode="$3" root before now delta row status tmp
     root=$(head -n 1 "$file")
     before=$(tail -n +2 "$file")
     if now=$(_drift_dirty_paths "$root"); then :; else
       status=$?
       if [ "$mode" = "delete" ]; then rm -f "$file"; fi
       return "$status"
     fi
     delta=$(LC_ALL=C comm -13 \
       <(printf '%s\n' "$before" | sed '/^$/d' | LC_ALL=C sort -u) \
       <(printf '%s\n' "$now" | sed '/^$/d' | LC_ALL=C sort -u))
     if [ -n "$delta" ]; then
       for row in $(_drift_in_progress_rows "$session"); do
         printf '%s\n' "$delta" >> "$(_drift_ledger_file "$session" "$row")"
       done
     fi
     if [ "$mode" = "checkpoint" ]; then
       tmp="${file}.tmp.$$"
       if { printf '%s\n' "$root"; printf '%s\n' "$now"; } > "$tmp"; then
         mv "$tmp" "$file" || rm -f "$tmp"
       else
         rm -f "$tmp"
       fi
     else
       rm -f "$file"
     fi
     return 0
   }

   # ic_drift_close_window SESSION LINEAGE KEY: the command's own PostToolUse.
   ic_drift_close_window() {
     local f
     f=$(_drift_window_file "$1" "$2" "$3")
     if [ -f "$f" ]; then _drift_close_window_file "$f" "$1" delete || true; fi
     return 0
   }

   # ic_drift_close_lineage SESSION LINEAGE: close one agent lineage's leftover windows
   # (R3b). Other lineages' windows are never touched.
   ic_drift_close_lineage() {
     local f
     for f in "$HOME/.claude/ironclaude-drift-window-$(_drift_safe "$1").$(_drift_safe "$2")".*.txt; do
       if [ -f "$f" ]; then _drift_close_window_file "$f" "$1" delete || true; fi
     done
     return 0
   }

   # ic_drift_checkpoint_windows SESSION: checkpoint every open window of SESSION
   # (submit_task and Stop), keeping each one open.
   ic_drift_checkpoint_windows() {
     local f
     for f in "$HOME/.claude/ironclaude-drift-window-$(_drift_safe "$1")".*.txt; do
       if [ -f "$f" ]; then _drift_close_window_file "$f" "$1" checkpoint || true; fi
     done
     return 0
   }
   ```
4. **Violations, Stop and message.** Replace `ic_drift_violations`, `ic_drift_stop_violations` and `ic_drift_block_message`, from `# ic_drift_violations SESSION ROOT ROW_ID...` through the closing `}` of `ic_drift_block_message`, with:
   ```bash
   # ic_drift_violations SESSION ROOT ROW_ID...  (ROOT from _drift_effective_root)
   # Prints drift paths (possibly none) with status 0; status 1 means fail-open;
   # status 2 means an unsupported current Git pathname. A row with no ledger adds
   # nothing: no command of this session changed a path while it was in progress.
   ic_drift_violations() {
     local session="$1" root="$2" allowed touched="" row file current
     shift 2
     [ -n "$root" ] || return 1
     if current=$(_drift_dirty_paths "$root"); then :; else return $?; fi
     allowed=$(_drift_allowed_files "$session" "$root") || return 1
     for row in "$@"; do
       file=$(_drift_ledger_file "$session" "$row")
       if [ -f "$file" ]; then touched="${touched}$(cat "$file")"$'\n'; fi
     done
     LC_ALL=C comm -23 \
       <(LC_ALL=C comm -12 \
           <(printf '%s\n' "$current" | sed '/^$/d' | LC_ALL=C sort -u) \
           <(printf '%s\n' "$touched" | sed '/^$/d' | LC_ALL=C sort -u)) \
       <(printf '%s\n' "$allowed" | sed '/^$/d' | LC_ALL=C sort -u)
     [ "$?" = "0" ] && return 0
     return 1
   }

   # ic_drift_stop_violations SESSION ROOT  (ROOT from _drift_effective_root)
   # Stop-time check across every in_progress task of the current wave, after
   # checkpointing the session's open windows. Status 0 with no output when nothing is
   # in progress; status 1 means fail-open.
   ic_drift_stop_violations() {
     local rows
     ic_drift_checkpoint_windows "$1"
     rows=$(_drift_in_progress_rows "$1")
     [ -n "$rows" ] || return 0
     # shellcheck disable=SC2086
     ic_drift_violations "$1" "$2" $rows
   }

   # ic_drift_block_message FILES -> the block text listing each drift path.
   ic_drift_block_message() {
     local list
     list=$(printf '%s\n' "$1" | sed '/^$/d; s/^/  - /')
     printf '%s\n\n%s\n%s\n\n%s' \
       "BLOCKED — EXECUTION DRIFT" \
       "These files changed while this session's own shell commands were running, and none of them is in the plan's allowed_files:" \
       "$list" \
       "If you made the change as a side quest, undo your own change. If the plan needs the file, retreat and amend the plan so it is listed in allowed_files. A file may instead belong to another session that wrote it while your command ran: never delete, revert or edit a file you did not change. It clears by itself once that session commits or reverts it; otherwise report the block to the operator and stop."
   }
   ```
5. **Dispatch.** Replace from `TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)` to the end of the file with:
   ```bash
   TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
   EVENT_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
   TASK_ID=$(printf '%s' "$INPUT" | jq -r '.tool_input.task_id // empty' 2>/dev/null || true)
   HOOK_EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null || true)
   case "$TOOL_NAME" in
     Bash|exec_command) SHORT_TOOL="shell" ;;
     *state_manager__*|*state-manager__*) SHORT_TOOL="${TOOL_NAME##*__}" ;;
     *)
       log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "not a drift tool: ${TOOL_NAME:-none}"
       exit 0
       ;;
   esac

   WORKFLOW=$(_drift_sql "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
   if [ "$WORKFLOW" != "executing" ] && [ "$WORKFLOW" != "reviewing" ]; then
     log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "workflow_stage=${WORKFLOW:-unreadable}"
     exit 0
   fi

   case "$SHORT_TOOL" in
     shell|submit_task) ;;
     *)
       log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "unhandled state-manager tool: ${SHORT_TOOL}"
       exit 0
       ;;
   esac

   if ! _drift_effective_root "$SESSION_TAG" "$EVENT_CWD"; then
     log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: fail-open — ${_DRIFT_ROOT_ERROR}"
     exit 0
   fi

   case "$SHORT_TOOL" in
     shell)
       WINDOW_KEY=$(printf '%s' "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null || true)
       LINEAGE=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || true)
       [ -n "$LINEAGE" ] || LINEAGE="main"
       if [ -z "$WINDOW_KEY" ]; then
         log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "shell event without tool_use_id"
         exit 0
       fi
       if [ "$HOOK_EVENT" = "PostToolUse" ]; then
         ic_drift_close_window "$SESSION_TAG" "$LINEAGE" "$WINDOW_KEY"
         log_hook "EXECUTION-DRIFT-CHECK" "Window" "closed ${LINEAGE}/${WINDOW_KEY}"
         exit 0
       fi
       # This lineage's windows still open here belong to its earlier commands that had
       # no PostToolUse (denied, or a failed Claude Bash). Other lineages are untouched.
       ic_drift_close_lineage "$SESSION_TAG" "$LINEAGE"
       if [ -z "$(_drift_in_progress_rows "$SESSION_TAG")" ]; then
         log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "no in-progress task"
         exit 0
       fi
       SHELL_CMD=$(printf '%s' "$INPUT" | jq -r '(.tool_input.command // .tool_input.cmd // "") | if type == "array" then join(" ") else tostring end' 2>/dev/null || true)
       if type workspace_input_is_readonly >/dev/null 2>&1 \
          && workspace_input_is_readonly command "$TOOL_NAME" "$SHELL_CMD"; then
         log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "read-only command"
         exit 0
       fi
       if ic_drift_open_window "$SESSION_TAG" "$LINEAGE" "$WINDOW_KEY" "$_DRIFT_ROOT"; then
         log_hook "EXECUTION-DRIFT-CHECK" "Window" "opened ${LINEAGE}/${WINDOW_KEY}, root ${_DRIFT_ROOT}"
       else
         log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "window ${LINEAGE}/${WINDOW_KEY} not opened"
       fi
       exit 0
       ;;
     submit_task)
       ic_drift_checkpoint_windows "$SESSION_TAG"
       if ! ROW_ID=$(_drift_row_id "$SESSION_TAG" "$TASK_ID"); then
         log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: no wave_tasks row in the current wave"
         exit 0
       fi
       if DRIFT=$(ic_drift_violations "$SESSION_TAG" "$_DRIFT_ROOT" "$ROW_ID"); then :
       else
         DRIFT_STATUS=$?
         if [ "$DRIFT_STATUS" = "2" ]; then
           block_pretooluse "EXECUTION-DRIFT-CHECK" "$(ic_drift_unsupported_path_message)"
         fi
         log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID}: fail-open (no plan)"
         exit 0
       fi
       if [ -n "$DRIFT" ]; then
         block_pretooluse "EXECUTION-DRIFT-CHECK" "$(ic_drift_block_message "$DRIFT")"
       fi
       log_hook "EXECUTION-DRIFT-CHECK" "Allowed" "task ${TASK_ID}: no drift"
       exit 0
       ;;
   esac
   ```

### Step 6: Edit `worker/hooks/hooks.json` matchers (GREEN)

- Line 95 (PreToolUse drift entry): change the matcher to `"mcp__(plugin_ironclaude_)?state[-_]manager__submit_task|Bash|exec_command"`.
- Line 205 (PostToolUse drift entry): change the matcher to `"Bash|exec_command"`.

### Step 7: Update the Stop-check comment in `worker/hooks/get-back-to-work-impl.sh`

Replace the comment at lines 487-493 with:

```bash
    # Execution drift check (execution-drift-check.sh, R5; session attribution
    # 2026-10-15): while executing, block on files changed by this session's own
    # shell commands during an in-progress task that are outside the plan's
    # allowed_files. ic_drift_stop_violations first checkpoints the session's open
    # windows. The root is the session's effective root (managed worktree or cwd
    # toplevel). It goes through increment_block_counter like every Stop block, so
    # the 3-blocks-in-5-minutes escape hatch above still applies. Fail-open when the
    # root or the plan is unavailable.
```

On line 511, change the log text `"execution drift check fail-open (no plan or baseline)"` to `"execution drift check fail-open (no plan)"`. All other code on lines 494-513 stays unchanged.

### Step 8: Syntax check and GREEN run

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; echo "n1=$?"
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/get-back-to-work-impl.sh; echo "n2=$?"
jq empty /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json; echo "json=$?"
grep -c -F 'LC_ALL=C comm -23' /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh; echo "check=$?"
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh; echo "roots=$?"
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh; echo "identity=$?"
```

Expected:
- `n1=0`, `n2=0`, `json=0`, and the grep count `1`;
- `Results: 30 pass, 0 fail` then `check=0`;
- `Results: 8 pass, 0 fail` then `roots=0`;
- `Results: 13 pass, 0 fail` (12 on a case-sensitive volume) then `identity=0`.

If a case fails, fix the hook (not the assertion) unless the case contradicts this plan's design. In that case, stop and report.

### Step 9: Neighbouring hook suites still pass

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-hooks-json-brain-entries.sh; echo "brain=$?"
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-managed-worktree-guard.sh; echo "mwg=$?"
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-pmguard-client-aware.sh; echo "pmg=$?"
```

Expected: `brain=0`, `mwg=0`, `pmg=0`. These suites read `hooks.json` or share the Bash/exec_command matchers. If one fails, show that the failure existed before this change, without `git stash`: rerun the suite against HEAD's `hooks.json` (`git -C /Users/roberthyatt/Code/ironclaude show HEAD:worker/hooks/hooks.json`) copied into a temp hooks dir. Otherwise fix the regression.

### Step 10: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/hooks.json worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-execution-drift-check.sh worker/hooks/tests/test-execution-drift-roots.sh worker/hooks/tests/test-execution-drift-identity.sh
```

Expected: no output.

---

## Task 2: Measure the per-command cost and record findings

No tests required: this task measures and documents; it changes no code.

**Files:**
- Create: `docs/reviews/2026-10-15-drift-session-attribution-findings.md`

### Step 1: Time `git status` in the large shared checkout (read-only)

```bash
/usr/bin/time -p git -C /Users/roberthyatt/Code/roleplaying-agents status --porcelain=v1 -z --untracked-files=all > /private/tmp/drift-status-timing.out; echo "status=$?"
wc -c /private/tmp/drift-status-timing.out
```

Expected: `status=0`, with `real <seconds>` printed by `time`. Record the value as measured. A window costs two of these. If `real` exceeds 1.0 s, still record it, and flag it in the findings as an operator decision (accept, or narrow the window trigger) before deploy.

### Step 2: Write the findings note

Write `docs/reviews/2026-10-15-drift-session-attribution-findings.md`. It should contain:
- the suite results from Task 1 Step 8 (exact `Results:` lines);
- the RED FAIL labels from Task 1 Step 4;
- the measured `git status` time and byte count from Step 1;
- the accepted known gaps (R3), plus the R3b gap: two parallel shell commands from one agent lineage;
- the residual false-block path and its three ways out (R4);
- what still needs live verification after deploy: a Codex `exec_command` PostToolUse closes its window, and the payload's `tool_name` may be `Bash`. The Codex hooks docs say PostToolUse fires for unified exec with `tool_use_id`. Leftover windows close at the lineage's next PreToolUse;
- failed Claude Bash calls fire `PostToolUseFailure`, which is not registered (the shared `hooks.json` must stay valid for Codex). Their windows close at the lineage's next PreToolUse;
- the deploy note: hooks run from the plugin cache, so the change takes effect only after a redeploy;
- backlog: `linkSubagent` has no caller (`state-manager/src/db.ts:666`); old `ironclaude-drift-baseline-*` files in `~/.claude` are now unused;
- the newline-path block still reads current checkout dirt, so another session's newline-named file can still block. This is pre-existing and recorded only.

### Step 3: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-15-drift-session-attribution-findings.md docs/plans/2026-10-15-drift-session-attribution-design.md docs/plans/2026-10-15-drift-session-attribution.md docs/plans/2026-10-15-drift-session-attribution.plan.json
```

Expected: no output.
