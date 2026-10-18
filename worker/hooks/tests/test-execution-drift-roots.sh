#!/usr/bin/env bash
# test-execution-drift-roots.sh - drift check review fixes (v1.1.16 L2b-1b).
# F1: the drift check uses the session's effective root (managed worktree), not cwd.
# F2: absolute allowed_files entries under the root match; others never do.
# F3: comm runs under LC_ALL=C (behavioral case + static check).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$HOOKS_DIR/execution-drift-check.sh"
TMP_ROOT="$(mktemp -d)"
TMP_ROOT="$(cd "$TMP_ROOT" && pwd -P)"
TMP_HOME="$TMP_ROOT/home"
PRIMARY="$TMP_ROOT/primary"
SESSION="ic-drift-roots-session"
GUID="ic-drift-roots-guid"
MANAGED="$PRIMARY/.ironclaude/worktrees/$GUID"
DB="$TMP_HOME/.claude/ironclaude.db"
WDB="$TMP_HOME/.claude/ironclaude-workspaces.db"
WINDOW_K1="$TMP_HOME/.claude/ironclaude-drift-window-${SESSION}.main.k1.txt"
THROTTLE="/tmp/.claude-block-throttle-${SESSION}"
mkdir -p "$TMP_HOME/.claude" "$PRIMARY"
rm -f "$THROTTLE"
trap 'rm -rf "$TMP_ROOT"; rm -f "$THROTTLE" "/tmp/.claude-bypass-counter-${SESSION}"' EXIT

PASSES=0
FAILS=0
EXPECTED_PASSES=8
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
CLAIM="mcp__plugin_ironclaude_state-manager__claim_task"
SUBMIT="mcp__plugin_ironclaude_state-manager__submit_task"

invoke() {  # TOOL_NAME HOOK_EVENT [LC_ALL value] -> exit code; stdout/stderr in $TMP_ROOT/out, err
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$PRIMARY" --arg t "$1" --arg e "$2" \
    '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:$t,tool_input:{task_id:1}}')
  printf '%s' "$input" | env LC_ALL="${3:-C}" HOME="$TMP_HOME" bash "$HOOK" >"$TMP_ROOT/out" 2>"$TMP_ROOT/err"
  echo $?
}
shell_event() {  # EVENT KEY [LC_ALL value] -> exit code (a mutating command, cwd = primary)
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$PRIMARY" --arg e "$1" --arg k "$2" \
    '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:"Bash",tool_use_id:$k,tool_input:{command:"touch x"}}')
  printf '%s' "$input" | env LC_ALL="${3:-C}" HOME="$TMP_HOME" bash "$HOOK" >"$TMP_ROOT/out" 2>"$TMP_ROOT/err"
  echo $?
}

# Primary checkout with a managed worktree (layout and schema from test-managed-worktree-guard.sh).
git init -q "$PRIMARY"
git -C "$PRIMARY" config user.email t@example.com
git -C "$PRIMARY" config user.name t
printf '.ironclaude/\n' > "$PRIMARY/.gitignore"
printf 'base\n' > "$PRIMARY/base.txt"
git -C "$PRIMARY" add .gitignore base.txt
git -C "$PRIMARY" commit -qm init
BASE=$(git -C "$PRIMARY" rev-parse HEAD)
REPOSITORY_IDENTITY=$(cd "$PRIMARY/.git" && pwd -P)
mkdir -p "$PRIMARY/.ironclaude/worktrees"
git -C "$PRIMARY" worktree add -qb "ironclaude/$GUID" "$MANAGED" "$BASE"

sqlite3 "$WDB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE assignments (
  workspace_guid TEXT PRIMARY KEY,
  repository_identity TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  branch TEXT NOT NULL,
  base_commit TEXT NOT NULL,
  current_head TEXT NOT NULL,
  owner_session_id TEXT,
  worker_id TEXT,
  lifecycle_status TEXT NOT NULL,
  integration_target TEXT NOT NULL,
  integrated_commit TEXT,
  recovery_ref TEXT,
  disposition TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE primary_checkout_owners (
  repository_identity TEXT PRIMARY KEY,
  workspace_guid TEXT NOT NULL,
  owner_session_id TEXT NOT NULL,
  acquired_at TEXT DEFAULT (datetime('now'))
);
INSERT INTO assignments (
  workspace_guid, repository_identity, worktree_path, branch, base_commit,
  current_head, owner_session_id, lifecycle_status, integration_target
) VALUES (
  '$GUID', '$REPOSITORY_IDENTITY', '$MANAGED', 'ironclaude/$GUID', '$BASE',
  '$BASE', '$SESSION', 'active', 'refs/heads/main'
);
SQL

# Plan allowed_files: a relative entry, an absolute entry under the worktree root,
# and two absolute entries that must never match (outside the root).
PLAN=$(jq -cn --arg m "$MANAGED" --arg p "$PRIMARY" \
  '{name:"t",tasks:[{id:1,allowed_files:["src/allowed.txt",($m + "/abs/allowed.txt"),"/side-wt.txt",($p + "/side-wt.txt")]}]}')
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1, plan_name='t', plan_json='${PLAN}' WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'in_progress');"

# Dirt before the claim: mixed-case and non-ASCII names in the worktree, one file in the primary.
printf 'z\n' > "$MANAGED/Zeta.txt"
printf 'a\n' > "$MANAGED/alpha.txt"
printf 'e\n' > "$MANAGED/Ébauche.txt"
printf 'b\n' > "$MANAGED/ábc.txt"
printf 'p\n' > "$PRIMARY/primary-before.txt"

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

# 5. F3 static: every comm invocation runs under LC_ALL=C, and there is at least one.
N_COMM=$(grep -c -E '(^|[^[:alnum:]_])comm -' "$HOOK")
N_COMM_C=$(grep -c -F 'LC_ALL=C comm -' "$HOOK")
[ "$N_COMM" -ge 1 ] && [ "$N_COMM" = "$N_COMM_C" ] && ok "5 comm runs under LC_ALL=C" || bad "5 comm runs under LC_ALL=C (comm=$N_COMM under_C=$N_COMM_C)"

# 6. F1/F2: a new file in the worktree blocks and is the only path listed; the
#    out-of-root absolute entries (/side-wt.txt, <primary>/side-wt.txt) do not excuse it.
shell_event PreToolUse k6 >/dev/null
printf 'quest\n' > "$MANAGED/side-wt.txt"
shell_event PostToolUse k6 >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - side-wt.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - primary-other.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - abs/allowed.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - Zeta.txt"; then
  ok "6 new worktree file blocks and is the only drift"
else
  bad "6 new worktree file blocks and is the only drift (rc=$RC: $ERR)"
fi

# 7. F1 at Stop: GBTW resolves the same root and blocks once, through the block counter.
STOP_IN=$(jq -cn --arg s "$SESSION" --arg c "$PRIMARY" '{session_id:$s,hook_event_name:"Stop",stop_hook_active:false,cwd:$c,last_assistant_message:"Working."}')
STOP_OUT=$(printf '%s' "$STOP_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
if printf '%s' "$STOP_OUT" | jq -cse 'length == 1 and .[0].decision == "block" and (.[0].reason | contains("side-wt.txt")) and ((.[0].reason | contains("primary-other.txt")) | not)' >/dev/null 2>&1 \
   && grep -q '^1:' "$THROTTLE" 2>/dev/null; then
  ok "7 Stop blocks on worktree drift only and increments the block counter"
else
  bad "7 Stop blocks on worktree drift only and increments the block counter (out: $STOP_OUT)"
fi

# 8. A frozen assignment fails open, and the reason is logged.
printf '{"verbose_hook_logs":true}\n' > "$TMP_HOME/.claude/ironclaude-hooks-config.json"
sqlite3 "$WDB" "UPDATE assignments SET lifecycle_status='paused' WHERE workspace_guid='$GUID';"
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && grep -qF "managed-worktree binding failed" "$TMP_ROOT/out"; then
  ok "8 broken assignment fails open with a logged reason"
else
  bad "8 broken assignment fails open with a logged reason (rc=$RC: $(cat "$TMP_ROOT/out"))"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
