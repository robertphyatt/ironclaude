#!/usr/bin/env bash
# test-pmguard-codex-primary.sh - 2026-10-17 drift hotfix R7: in a primary checkout (no
# managed-worktree assignment) native Codex apply_patch and exec_command reach the same
# stage and allowed_files gates as MultiEdit and Bash. HOME is a temp dir throughout.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GUARD="$HOOKS_DIR/professional-mode-guard.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
REPO="$TMP_ROOT/repo"
DB="$TMP_HOME/.claude/ironclaude.db"
LOG="$TMP_HOME/.claude/ironclaude-drift-writers.tsv"
S="ic-codex-primary"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
unset IC_DRIFT_WRITERS_LOG IC_DRIFT_WRITERS_MAX_LINES

PASSES=0
FAILS=0
EXPECTED_PASSES=7
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

git init -q "$REPO"
printf 'base\n' > "$REPO/base.txt"
mkdir -p "$REPO/src"
printf 'allowed\n' > "$REPO/src/allowed.txt"
git -C "$REPO" add base.txt src/allowed.txt
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q -m init
REPO_PHYS=$(cd "$REPO" && pwd -P)
printf '{"session_id":"%s","source":"startup"}' "$S" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming', current_wave=1 WHERE terminal_session='${S}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status, allowed_files) VALUES ('${S}', 1, 1, 'Task one', 'in_progress', '[\"src/allowed.txt\",\"src/p6.txt\"]');"
stage() { sqlite3 "$DB" "UPDATE sessions SET workflow_stage='$1' WHERE terminal_session='${S}';"; }
guard() {  # TOOL_NAME TOOL_INPUT_JSON -> exit code; stdout in $TMP_ROOT/out, stderr in $TMP_ROOT/err
  local input
  input=$(jq -cn --arg s "$S" --arg c "$REPO_PHYS" --arg t "$1" --argjson i "$2" \
    '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:$t,tool_input:$i}')
  printf '%s' "$input" | HOME="$TMP_HOME" bash "$GUARD" >"$TMP_ROOT/out" 2>"$TMP_ROOT/err"
  echo $?
}
patch_input() { jq -cn --arg c "$1" '{command:$c}'; }
count() {  # PATH -> this session's well-formed writer records for PATH
  [ -f "$LOG" ] || { echo 0; return; }
  awk -F '\t' -v s="$S" -v p="$1" 'NF == 3 && $1 ~ /^[0-9]+$/ && $2 == s && $3 == p { n++ } END { print n + 0 }' "$LOG"
}

# C1. Brainstorming: an apply_patch of a source file is blocked like any write tool.
stage brainstorming
RC=$(guard apply_patch "$(patch_input $'*** Begin Patch\n*** Add File: src/x.txt\n+x\n*** End Patch')")
[ "$RC" = "2" ] && grep -qF "WRITE TOOLS NOT ALLOWED" "$TMP_ROOT/err" && ok "C1 brainstorming apply_patch of a source file is blocked" || bad "C1 brainstorming apply_patch of a source file is blocked (rc=$RC: $(cat "$TMP_ROOT/err"))"

# C2. Executing: a patch whose SECOND target is outside allowed_files is blocked.
stage executing
RC=$(guard apply_patch "$(patch_input $'*** Begin Patch\n*** Update File: src/allowed.txt\n@@\n-allowed\n+changed\n*** Add File: src/outside.txt\n+o\n*** End Patch')")
[ "$RC" = "2" ] && grep -qF "FILE NOT IN PLAN" "$TMP_ROOT/err" && grep -qF "src/outside.txt" "$TMP_ROOT/err" && ok "C2 executing apply_patch with a second target outside allowed_files is blocked" || bad "C2 executing apply_patch with a second target outside allowed_files is blocked (rc=$RC: $(cat "$TMP_ROOT/err"))"

# C3. Positive control: an all-docs patch in brainstorming stays allowed.
stage brainstorming
RC=$(guard apply_patch "$(patch_input $'*** Begin Patch\n*** Add File: docs/notes.md\n+n\n*** End Patch')")
[ "$RC" = "0" ] && ! grep -qF "BLOCKED" "$TMP_ROOT/err" && ok "C3 brainstorming all-docs apply_patch stays allowed" || bad "C3 brainstorming all-docs apply_patch stays allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# C4. Brainstorming: a mutating exec_command is blocked like Bash.
RC=$(guard exec_command '{"cmd":"touch src/x.txt"}')
[ "$RC" = "2" ] && grep -qF "WRITE TOOLS NOT ALLOWED" "$TMP_ROOT/err" && ok "C4 brainstorming mutating exec_command is blocked" || bad "C4 brainstorming mutating exec_command is blocked (rc=$RC: $(cat "$TMP_ROOT/err"))"

# C5. Positive control: read-only research exec_command stays allowed in brainstorming.
RC=$(guard exec_command '{"cmd":"cat base.txt"}')
[ "$RC" = "0" ] && ! grep -qF "BLOCKED" "$TMP_ROOT/err" && ok "C5 brainstorming read-only exec_command stays allowed" || bad "C5 brainstorming read-only exec_command stays allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# C6. Positive control: executing, every target allowed -> allowed, and the patch is not
#     rewritten in a primary checkout (no updatedInput on stdout).
stage executing
RC=$(guard apply_patch "$(patch_input $'*** Begin Patch\n*** Update File: src/allowed.txt\n@@\n-allowed\n+changed\n*** Add File: src/p6.txt\n+p\n*** End Patch')")
[ "$RC" = "0" ] && [ ! -s "$TMP_ROOT/out" ] && ok "C6 executing in-plan apply_patch is allowed and not rewritten" || bad "C6 executing in-plan apply_patch is allowed and not rewritten (rc=$RC out=$(cat "$TMP_ROOT/out"): $(cat "$TMP_ROOT/err"))"

# C7. The writer log records every target of that allowed patch (Task 2 recorder).
N1=$(count "$REPO_PHYS/src/allowed.txt")
N2=$(count "$REPO_PHYS/src/p6.txt")
[ "$N1$N2" = "11" ] && ok "C7 the writer log records every target of an allowed patch" || bad "C7 the writer log records every target of an allowed patch (counts=$N1$N2)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
