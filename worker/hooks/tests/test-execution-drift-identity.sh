#!/usr/bin/env bash
# test-execution-drift-identity.sh - absolute allowed_files spelled through a symlink
# or a different APFS case map to the effective root by file identity
# (v1.1.16 L2b-1c R1).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$HOOKS_DIR/execution-drift-check.sh"
TMP_ROOT="$(mktemp -d)"
TMP_ROOT="$(cd "$TMP_ROOT" && pwd -P)"
TMP_HOME="$TMP_ROOT/home"
REPO="$TMP_ROOT/repo"
LINK="$TMP_ROOT/link"
CASED="$TMP_ROOT/REPO"
INTERNAL_LINK="$REPO/alias"
ESCAPE="$REPO/escape"
LOOP="$TMP_ROOT/loop"
SESSION="ic-drift-identity-session"
DB="$TMP_HOME/.claude/ironclaude.db"
mkdir -p "$TMP_HOME/.claude" "$REPO/inside" "${REPO}-other"
ln -s "$REPO" "$LINK"
ln -s "$REPO/inside" "$INTERNAL_LINK"
ln -s "${REPO}-other" "$ESCAPE"
ln -s "$LOOP" "$LOOP"
trap 'rm -rf "$TMP_ROOT"; rm -f "/tmp/.claude-block-throttle-${SESSION}" "/tmp/.claude-bypass-counter-${SESSION}"' EXIT

PASSES=0
FAILS=0
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
CLAIM="mcp__plugin_ironclaude_state-manager__claim_task"
SUBMIT="mcp__plugin_ironclaude_state-manager__submit_task"

invoke() {  # TOOL_NAME HOOK_EVENT -> exit code; stdout/stderr in $TMP_ROOT/out, err
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg t "$1" --arg e "$2" \
    '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:$t,tool_input:{task_id:1}}')
  printf '%s' "$input" | env LC_ALL=C HOME="$TMP_HOME" bash "$HOOK" >"$TMP_ROOT/out" 2>"$TMP_ROOT/err"
  echo $?
}
in_window() {  # KEY: run the remaining args as this session's own shell command
  local key="$1" input
  shift
  input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg k "$key" \
    '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:"Bash",tool_use_id:$k,tool_input:{command:"touch x"}}')
  printf '%s' "$input" | env LC_ALL=C HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>&1
  "$@"
  printf '%s' "$input" | jq -c '.hook_event_name = "PostToolUse"' | env LC_ALL=C HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>&1
}

# Repository at a physical path, reachable through a symlink ($LINK) and, on a
# case-insensitive volume, through a different case ($CASED). No workspace DB, so
# the effective root is the cwd's toplevel: $REPO.
git init -q "$REPO"
git -C "$REPO" config user.email t@example.com
git -C "$REPO" config user.name t
mkdir -p "$REPO/gone"
printf 'base\n' > "$REPO/base.txt"
printf 'old\n' > "$REPO/gone/old.txt"
git -C "$REPO" add base.txt gone/old.txt
git -C "$REPO" commit -qm init

CASE_INSENSITIVE=0
if [ -d "$CASED" ] && [ "$CASED" -ef "$REPO" ]; then CASE_INSENSITIVE=1; fi
EXPECTED_PASSES=13
[ "$CASE_INSENSITIVE" = "1" ] || EXPECTED_PASSES=12

PLAN=$(jq -cn --arg l "$LINK" --arg c "$CASED" --arg o "${REPO}-other" --arg i "$INTERNAL_LINK" --arg e "$ESCAPE" \
  '{name:"t",tasks:[{id:1,allowed_files:[($l + "/src/a.txt"),($l + "/gone/old.txt"),($c + "/case/c.txt"),($o + "/side.txt"),($i + "/deep/new.txt"),($e + "/side.txt")]}]}')
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1, plan_name='t', plan_json='${PLAN}' WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'in_progress');"

# The task is in progress; only this session's own commands (in_window) enter the ledger.

# 1. A symlinked spelling of the root excuses a new file in a new directory.
in_window i1 bash -c 'mkdir -p "$1/src" && printf "a\n" > "$1/src/a.txt"' _ "$REPO"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "1 symlinked spelling of the root is allowed" || bad "1 symlinked spelling of the root is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 2. A symlinked spelling of a deleted file whose directory no longer exists.
in_window i2 rm -rf "$REPO/gone"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "2 entry under a removed directory is allowed" || bad "2 entry under a removed directory is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 3. A case-variant spelling of the root (case-insensitive volumes only).
if [ "$CASE_INSENSITIVE" = "1" ]; then
  in_window i3 bash -c 'mkdir -p "$1/case" && printf "c\n" > "$1/case/c.txt"' _ "$REPO"
  RC=$(invoke "$SUBMIT" "PreToolUse")
  [ "$RC" = "0" ] && ok "3 case-variant spelling of the root is allowed" || bad "3 case-variant spelling of the root is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"
else
  printf 'SKIP  3 case-variant spelling (volume is case-sensitive)\n'
fi

# 4. Negative: a sibling-prefix entry does not excuse side.txt.
in_window i4 bash -c 'printf "side\n" > "$1/side.txt"' _ "$REPO"
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - side.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - src/a.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - gone/old.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - case/c.txt"; then
  ok "4 sibling-prefix entry is dropped; side.txt is the only drift"
else
  bad "4 sibling-prefix entry is dropped; side.txt is the only drift (rc=$RC: $ERR)"
fi

# 5. An alias of a directory inside the repository maps to its physical prefix.
in_window i5 bash -c 'mkdir -p "$1/inside/deep" && printf "internal\n" > "$1/inside/deep/new.txt"' _ "$REPO"
rm -f "$REPO/side.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "5 internal directory alias is allowed" || bad "5 internal directory alias is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# Direct identity checks cover the path contract, including absent suffixes and
# a final symlink entry whose target is outside the repository.
export HOME="$TMP_HOME"
IC_DRIFT_LIB=1 source "$HOOK"
MAPPED=$(_drift_rel_by_identity "$INTERNAL_LINK/missing/leaf.txt" "$REPO")
[ "$MAPPED" = 'inside/missing/leaf.txt' ] && ok "6 missing parent suffix is preserved" || bad "6 missing parent suffix is preserved ($MAPPED)"
ln -s "${REPO}-other/side.txt" "$REPO/final-link"
MAPPED=$(_drift_rel_by_identity "$REPO/final-link" "$REPO")
[ "$MAPPED" = 'final-link' ] && ok "7 final symlink entry is preserved" || bad "7 final symlink entry is preserved ($MAPPED)"
MAPPED=$(_drift_rel_by_identity "$LINK/src/a.txt" "$REPO")
[ "$MAPPED" = 'src/a.txt' ] && ok "8 root alias maps the file" || bad "8 root alias maps the file ($MAPPED)"
if _drift_rel_by_identity "${REPO}-other/side.txt" "$REPO" >/dev/null; then
  bad "9 sibling entry is external"
else
  ok "9 sibling entry is external"
fi
MAPPED_ALLOWED=$(_drift_allowed_files "$SESSION" "$REPO")
if _drift_rel_by_identity "$ESCAPE/side.txt" "$REPO" >/dev/null \
   || printf '%s\n' "$MAPPED_ALLOWED" | grep -qxF 'escape/side.txt'; then
  bad "10 internal alias escaping repository is external"
else
  ok "10 internal alias escaping repository is external"
fi

# A looped ancestor is unresolved, not an external path to silently discard.
UNREADABLE_PLAN=$(jq -cn --arg l "$LOOP" '{name:"t",tasks:[{id:1,allowed_files:[($l + "/leaf.txt")]}]}')
sqlite3 "$DB" "UPDATE sessions SET plan_json='${UNREADABLE_PLAN}' WHERE terminal_session='${SESSION}';"
if _drift_allowed_files "$SESSION" "$REPO" >/dev/null; then
  bad "11 unreadable ancestor fails the allowed-file mapping"
else
  ok "11 unreadable ancestor fails the allowed-file mapping"
fi

# Lexical dot segments match scoped-tree's path.resolve behavior. A regular
# file in an ancestor position follows the missing-parent fallback.
MAPPED=$(_drift_rel_by_identity "$REPO/missing/../src/a.txt" "$REPO")
[ "$MAPPED" = 'src/a.txt' ] && ok "12 dot segments normalize before physical resolution" || bad "12 dot segments normalize before physical resolution ($MAPPED)"
printf 'regular\n' > "$REPO/regular-file"
MAPPED=$(_drift_rel_by_identity "$REPO/regular-file/missing/leaf.txt" "$REPO")
[ "$MAPPED" = 'regular-file/missing/leaf.txt' ] && ok "13 non-directory ancestor uses missing-parent fallback" || bad "13 non-directory ancestor uses missing-parent fallback ($MAPPED)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
