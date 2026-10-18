#!/usr/bin/env bash
# test-execution-drift-writers.sh - drift writer log (2026-10-17 cross-session hotfix R1):
# professional-mode-guard.sh records epoch<TAB>session<TAB>physical path for every
# file-tool write it allows (Claude Write/Edit/MultiEdit/NotebookEdit, Codex apply_patch),
# fail-open, keeping its newest half past a line cap. HOME is a temp dir throughout.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GUARD="$HOOKS_DIR/professional-mode-guard.sh"
HOOK="$HOOKS_DIR/execution-drift-check.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
REPO="$TMP_ROOT/repo"
DB="$TMP_HOME/.claude/ironclaude.db"
LOG="$TMP_HOME/.claude/ironclaude-drift-writers.tsv"
CLAUDE_S="ic-writers-claude"
CODEX_S="ic-writers-codex"
EXEC_S="ic-writers-exec"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
unset IC_DRIFT_WRITERS_LOG IC_DRIFT_WRITERS_MAX_LINES

PASSES=0
FAILS=0
EXPECTED_PASSES=10
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

git init -q "$REPO"
printf 'base\n' > "$REPO/base.txt"
git -C "$REPO" add base.txt
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q -m init
REPO_PHYS=$(cd "$REPO" && pwd -P)
mkdir -p "$REPO/src"
printf 'allowed\n' > "$REPO/src/allowed.txt"

for s in "$CLAUDE_S" "$CODEX_S" "$EXEC_S"; do
  printf '{"session_id":"%s","source":"startup"}' "$s" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
done
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming' WHERE terminal_session='${CLAUDE_S}';
UPDATE sessions SET professional_mode='off' WHERE terminal_session='${CODEX_S}';
UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1 WHERE terminal_session='${EXEC_S}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status, allowed_files) VALUES ('${EXEC_S}', 1, 1, 'Task one', 'in_progress', '[\"src/allowed.txt\"]');"

guard() {  # SESSION TOOL_NAME TOOL_INPUT_JSON [VAR=value...] -> exit code; stderr in $TMP_ROOT/err
  local input s="$1" t="$2" i="$3"
  shift 3
  input=$(jq -cn --arg s "$s" --arg c "$REPO_PHYS" --arg t "$t" --argjson i "$i" \
    '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:$t,tool_input:$i}')
  printf '%s' "$input" | env HOME="$TMP_HOME" "$@" bash "$GUARD" >/dev/null 2>"$TMP_ROOT/err"
  echo $?
}
count() {  # SESSION PATH [LOG] -> well-formed records of SESSION for PATH
  local log="${3:-$LOG}"
  [ -f "$log" ] || { echo 0; return; }
  awk -F '\t' -v s="$1" -v p="$2" 'NF == 3 && $1 ~ /^[0-9]+$/ && $2 == s && $3 == p { n++ } END { print n + 0 }' "$log"
}
lines() { if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi; }
record() {  # SESSION BASE PATH [VAR=value...]: drive the library recorder directly
  local s="$1" b="$2" p="$3"
  shift 3
  printf '%s\n' "$p" | env IC_DRIFT_LIB=1 HOME="$TMP_HOME" "$@" bash -c 'source "$1"; ic_drift_record_writes "$2" "$3"' _ "$HOOK" "$s" "$b"
}

# W1. A Claude Write allowed by the real guard (a brainstorming design doc, the
#     2026-10-17 incident shape) records epoch, session and physical absolute path.
RC=$(guard "$CLAUDE_S" Write "$(jq -cn --arg p "$REPO_PHYS/docs/plans/w1-design.md" '{file_path:$p,content:"x"}')")
N=$(count "$CLAUDE_S" "$REPO_PHYS/docs/plans/w1-design.md")
[ "$RC" = "0" ] && [ "$N" = "1" ] && ok "W1 a Claude Write allowed by the guard records its writer" || bad "W1 a Claude Write allowed by the guard records its writer (rc=$RC n=$N: $(cat "$TMP_ROOT/err"))"

# W2. A Codex apply_patch (no managed worktree) records every target, relative to cwd,
#     including one under a directory that does not exist yet.
PATCH=$'*** Begin Patch\n*** Add File: src/w2-a.txt\n+a\n*** Add File: new-dir/w2-b.txt\n+b\n*** Update File: base.txt\n@@\n-base\n+changed\n*** End Patch'
RC=$(guard "$CODEX_S" apply_patch "$(jq -cn --arg c "$PATCH" '{command:$c}')")
NA=$(count "$CODEX_S" "$REPO_PHYS/src/w2-a.txt")
NB=$(count "$CODEX_S" "$REPO_PHYS/new-dir/w2-b.txt")
NC=$(count "$CODEX_S" "$REPO_PHYS/base.txt")
[ "$RC" = "0" ] && [ "$NA$NB$NC" = "111" ] && ok "W2 a Codex apply_patch records every target" || bad "W2 a Codex apply_patch records every target (rc=$RC counts=$NA$NB$NC: $(cat "$TMP_ROOT/err"))"

# W3. Claude and Codex records share one form: numeric epoch, safe session, absolute path.
FORM=$( [ -f "$LOG" ] && awk -F '\t' -v a="$CLAUDE_S" -v b="$CODEX_S" '
  $2 == a || $2 == b { if (!(NF == 3 && $1 ~ /^[0-9]+$/ && $3 ~ /^\//)) badf++ }
  $2 == a { na++ } $2 == b { nb++ }
  END { print (badf + 0) " " (na + 0) " " (nb + 0) }' "$LOG")
[ "$FORM" = "0 1 3" ] && ok "W3 Claude and Codex records share one form" || bad "W3 Claude and Codex records share one form (bad/claude/codex=${FORM:-no log})"

# W4. A writer log that cannot be written never blocks the write (fail open).
printf 'not a directory\n' > "$TMP_ROOT/blocker"
RC=$(guard "$CLAUDE_S" Write "$(jq -cn --arg p "$REPO_PHYS/docs/plans/w4-design.md" '{file_path:$p,content:"x"}')" IC_DRIFT_WRITERS_LOG="$TMP_ROOT/blocker/log.tsv")
[ "$RC" = "0" ] && ! grep -qF "BLOCKED" "$TMP_ROOT/err" && ok "W4 an unwritable writer log never blocks the write" || bad "W4 an unwritable writer log never blocks the write (rc=$RC: $(cat "$TMP_ROOT/err"))"

# W5. A relative target under a missing directory, with a symlinked (non-physical)
#     base, records its physical absolute path.
record w5-session "$REPO" "src/w5-dir/w5.txt"
N=$(count w5-session "$REPO_PHYS/src/w5-dir/w5.txt")
[ "$N" = "1" ] && ok "W5 a relative target records its physical absolute path" || bad "W5 a relative target records its physical absolute path (n=$N)"

# W6-W7. The log keeps every record up to its cap, then keeps its newest half.
TRIM_LOG="$TMP_ROOT/trim.tsv"
for n in 1 2 3 4 5 6 7 8 9 10; do
  record trim "$REPO" "t$n.txt" IC_DRIFT_WRITERS_LOG="$TRIM_LOG" IC_DRIFT_WRITERS_MAX_LINES=10
done
AT_CAP=$(lines "$TRIM_LOG")
if [ "$AT_CAP" = "10" ] && grep -qx '_DRIFT_WRITERS_MAX_DEFAULT=10000' "$HOOK"; then
  ok "W6 the log keeps every record up to its cap (default cap 10000)"
else
  bad "W6 the log keeps every record up to its cap (default cap 10000) (lines=$AT_CAP)"
fi
record trim "$REPO" "t11.txt" IC_DRIFT_WRITERS_LOG="$TRIM_LOG" IC_DRIFT_WRITERS_MAX_LINES=10
AFTER=$(lines "$TRIM_LOG")
FIRST=$( [ -f "$TRIM_LOG" ] && head -n 1 "$TRIM_LOG" | cut -f3)
LAST=$( [ -f "$TRIM_LOG" ] && tail -n 1 "$TRIM_LOG" | cut -f3)
if [ "$AFTER" = "5" ] && [ "$FIRST" = "$REPO_PHYS/t7.txt" ] && [ "$LAST" = "$REPO_PHYS/t11.txt" ]; then
  ok "W7 past its cap the log keeps its newest half"
else
  bad "W7 past its cap the log keeps its newest half (lines=$AFTER first=$FIRST last=$LAST)"
fi

# W8. An allowed in-plan Write while executing (the access-check allow) records.
RC=$(guard "$EXEC_S" Write "$(jq -cn --arg p "$REPO_PHYS/src/allowed.txt" '{file_path:$p,content:"x"}')")
N=$(count "$EXEC_S" "$REPO_PHYS/src/allowed.txt")
[ "$RC" = "0" ] && [ "$N" = "1" ] && ok "W8 an allowed executing Write records its writer" || bad "W8 an allowed executing Write records its writer (rc=$RC n=$N: $(cat "$TMP_ROOT/err"))"

# W9. A blocked Write records nothing.
RC=$(guard "$EXEC_S" Write "$(jq -cn --arg p "$REPO_PHYS/src/forbidden.txt" '{file_path:$p,content:"x"}')")
N=$(count "$EXEC_S" "$REPO_PHYS/src/forbidden.txt")
[ "$RC" = "2" ] && grep -qF "FILE NOT IN PLAN" "$TMP_ROOT/err" && [ "$N" = "0" ] && ok "W9 a blocked Write records nothing" || bad "W9 a blocked Write records nothing (rc=$RC n=$N)"

# W10. A Read is not a write and records nothing.
BEFORE=$(lines "$LOG")
RC=$(guard "$EXEC_S" Read "$(jq -cn --arg p "$REPO_PHYS/base.txt" '{file_path:$p}')")
[ "$RC" = "0" ] && [ "$(lines "$LOG")" = "$BEFORE" ] && ok "W10 a Read records nothing" || bad "W10 a Read records nothing (rc=$RC before=$BEFORE after=$(lines "$LOG"))"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
