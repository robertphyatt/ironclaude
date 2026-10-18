# Drift-Check Review Fixes Implementation Plan (v1.1.16 L2b-1b)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the four defects in the L2b-1 drift check and in `state-activator.sh`:
- the drift check resolves the session's effective root the way the write guard does;
- it matches absolute `allowed_files` entries;
- it runs `comm` in the C locale;
- `state-activator.sh` emits at most one JSON object per run.

**Requirements:** `docs/plans/2026-10-03-drift-check-review-fixes-requirements.md`

**Design:** `docs/plans/2026-10-03-drift-check-review-fixes-design.md`

**Architecture:**
- **One root resolver (R1).** `execution-drift-check.sh` sources `workspace-path-adapter.sh` and gains `_drift_effective_root SESSION CWD`. The function reproduces the inputs that `workspace_prepare_input` derives and calls the adapter's `workspace_bind_effective_root`. Each caller resolves the root once, in its own shell, and passes it to the baseline, the submit check and the Stop check, so all three share one root and the fail-open reason can be logged.
- **Absolute entries (R2).** `_drift_allowed_files` converts absolute entries under the root to repo-relative form and drops other absolute entries.
- **Locale (R3).** `comm` runs as `LC_ALL=C comm -23`.
- **One object (R4).** `state-activator.sh` buffers its stdout and, on exit, merges every buffered line into one JSON object.

**Tech Stack:** Bash hooks, jq, sqlite3, git; shell tests in `worker/hooks/tests`, run by `make test-hooks`; Commander pytest suite.

**Loop classification:** full loop. It changes guard and hook enforcement code.

## Execution invariants

- **No persistent shell state.** Each step is its own invocation and uses literal absolute paths.
- **Bash cwd is `commander/`.** Staging uses `git -C /Users/roberthyatt/Code/ironclaude`.
- **zsh `nomatch`.** No command uses an unquoted glob.
- **No waiting.** Foreground `sleep` is blocked, and no step waits.
- **`docs/` is gitignored.** The deploy log needs `git add -f`.
- **Empty versus failed.** Evidence commands keep stderr. Tests print `PASS`/`FAIL` per assertion and a `Results:` line, and exit non-zero on any failure.
- **Measured baselines (working tree with L2b-1 staged):**
  - `worker/hooks/` holds 31 `*.sh` files, counted from `ls`.
  - `diff worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json` prints nothing.
  - `diff -rq worker <codex cache> -x node_modules -x __pycache__ -x .DS_Store` prints nothing.
  - `grep -c -F 'LC_ALL=C comm -23' worker/hooks/execution-drift-check.sh` gives 0. The file has `comm -23` at line 113, with no locale prefix.
  - **Not measured:** the hook-suite and Commander-suite pass counts. Test execution is blocked in planning, so both are expected only as "exit 0, 0 failed".
- **Stop output stays one JSON object.** `_drift_effective_root` writes nothing to stdout. The adapter's heartbeat log goes to a sideband file (`workspace-path-adapter.sh:386-389`).
- **Stop blocks keep the escape hatch.** A Stop drift block still goes through `increment_block_counter` and then `block_stop`.
- **Exact matching.** Allowed paths are compared as exact strings, never as patterns.
- **`hooks.json` is not changed in this loop.** No Codex hook trust key changes.

## Grounded calling contract (R1)

`workspace_prepare_input` (`workspace-path-adapter.sh:395-492`) calls `workspace_bind_effective_root workspace_db repository_identity event_cwd safe_session` (`:341`) only after deriving these inputs:
- **`event_cwd`:** `workspace_canonical_dir` of the hook `cwd` (`:428-433`).
- **`repository_identity`:** `workspace_canonical_dir` of `git -C event_cwd rev-parse --git-common-dir`. A relative result is resolved against `event_cwd` (`:439-451`).
- **`workspace_db`:** `${WORKSPACE_MANAGER_DB_PATH:-$HOME/.claude/ironclaude-workspaces.db}`. An absent file means no assignment (`:463-464`).
- **`safe_session`:** `workspace_sql_quote "$SESSION_TAG"` (`:465`).
- **Assignment count:** rows for this repository identity and session, excluding `integrated`, `abandoned` and `cleaned`. A count of 0 means no assignment, 1 means bind, and anything else is ambiguous (`:466-476`).

What the bind function relies on and what it changes:
- **Needs:** `workspace_sql_quote` (`:118`), `workspace_canonical_dir` (`:122`), `workspace_adapter_fail` (`:15`) and the global `SESSION_TAG` (`:374`). It does not need `bash-readonly-guard.sh`; only `workspace_input_is_readonly` uses that.
- **Sets:** `WORKSPACE_EFFECTIVE_ROOT`, `WORKSPACE_PRIMARY_ROOT` and, on failure, `WORKSPACE_ADAPTER_ERROR`.
- **Side effect:** when this session owns `primary_checkout_owners`, it renews `acquired_at`, the owner heartbeat, and appends to `~/.claude/ironclaude-worktree-heartbeat.log` (`:372-391`). The same renewal happens on every guarded file operation, so it is acceptable here.

Sourcing the adapter has no side effects at the top level: lines 5-13 only assign empty `WORKSPACE_*` globals.

## Fail-open and fail-closed choices

| Condition | Result |
|---|---|
| professional mode not `on`; stage not executing/reviewing | skip, as in L2b-1 |
| hook `cwd` missing, or not in a git repository | fail open, logged |
| workspace DB file absent, or 0 assignment rows | root = `git rev-parse --show-toplevel` of `cwd`; this is the L2b-1 behavior |
| adapter not loaded; cwd or common dir not canonical; assignment table unreadable; more than one row | fail open, logged with the reason |
| one row, but `workspace_bind_effective_root` fails (frozen, missing, wrong branch, etc.) | fail open, logged as `managed-worktree binding failed: <WORKSPACE_ADAPTER_ERROR>` |
| plan or baseline unreadable | fail open, as in L2b-1 |
| root resolved, plan and baseline readable, drift non-empty | **fail closed**: `submit_task` exits 2; Stop calls `increment_block_counter` then `block_stop` |

**Known limitation.** Absolute plan entries that name the *primary* checkout do not match while the session's effective root is a managed worktree. They are dropped as "outside the root". The write guard behaves the same way: it strips only `WORKSPACE_EFFECTIVE_ROOT` (`professional-mode-guard.sh:766-767`).

---

## Task 1: Effective root, absolute allowed_files and locale-pinned comm (R1, R2, R3)

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh`. Replace the whole file (209 lines at present).
- Modify: `worker/hooks/get-back-to-work-impl.sh:468-484`, the Stop drift block.
- Create: `worker/hooks/tests/test-execution-drift-roots.sh`.

**Step 1: Write the test (RED)**

Create `worker/hooks/tests/test-execution-drift-roots.sh`:
```bash
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
BASELINE="$TMP_HOME/.claude/ironclaude-drift-baseline-${SESSION}-1.txt"
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

# 1. F1: the baseline is taken from the worktree, not the primary checkout.
RC=$(invoke "$CLAIM" "PostToolUse")
if [ "$RC" = "0" ] && grep -qxF "Zeta.txt" "$BASELINE" 2>/dev/null && ! grep -qxF "primary-before.txt" "$BASELINE" 2>/dev/null; then
  ok "1 baseline comes from the managed worktree"
else
  bad "1 baseline comes from the managed worktree (rc=$RC)"
fi

# 2. F1: another session's edit in the primary checkout does not block.
printf 'o\n' > "$PRIMARY/primary-other.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "2 primary-checkout edits do not block" || bad "2 primary-checkout edits do not block (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 3. F2: an absolute allowed entry under the worktree root does not block.
mkdir -p "$MANAGED/abs"
printf 'abs\n' > "$MANAGED/abs/allowed.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "3 absolute allowed entry under the root passes" || bad "3 absolute allowed entry under the root passes (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 4. F3 behavioral: a UTF-8 locale with mixed-case and non-ASCII baseline paths gives no false drift.
RC=$(invoke "$SUBMIT" "PreToolUse" "en_US.UTF-8")
[ "$RC" = "0" ] && ok "4 no false drift under LC_ALL=en_US.UTF-8" || bad "4 no false drift under LC_ALL=en_US.UTF-8 (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 5. F3 static: comm is invoked under LC_ALL=C.
[ "$(grep -c -F 'LC_ALL=C comm -23' "$HOOK")" = "1" ] && ok "5 comm runs under LC_ALL=C" || bad "5 comm runs under LC_ALL=C"

# 6. F1/F2: a new file in the worktree blocks and is the only path listed; the
#    out-of-root absolute entries (/side-wt.txt, <primary>/side-wt.txt) do not excuse it.
printf 'quest\n' > "$MANAGED/side-wt.txt"
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
```

**Step 2: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh
```
Expected: exit 1. At the current tree the baseline and the check read the primary checkout, so these cases fail:
- `FAIL  1 baseline comes from the managed worktree`
- `FAIL  2 primary-checkout edits do not block`
- `FAIL  3 absolute allowed entry under the root passes`
- `FAIL  5 comm runs under LC_ALL=C`
- `FAIL  6 …`, because `primary-other.txt` is listed
- `FAIL  7 …`
- `FAIL  8 …`, because there is no binding message

Case 4 is not a reliable RED on this host: macOS's `en_US.UTF-8` collation was not measured to differ from C order. Case 5 is the locale check that can fail on every host.

**Step 3: Replace `worker/hooks/execution-drift-check.sh`**

Write the file with exactly this content:
```bash
#!/bin/bash
# execution-drift-check.sh - execution side-quest detector (v1.1.16 L2b-1 R5, L2b-1b).
#
# Triggers (worker/hooks/hooks.json):
#   PostToolUse  state-manager claim_task   -> record the dirty-file baseline
#   PreToolUse   state-manager submit_task  -> block (exit 2) on drift
#   Library      IC_DRIFT_LIB=1 source      -> _drift_effective_root and
#                                              ic_drift_stop_violations, used by
#                                              get-back-to-work-impl.sh at Stop
#
# Root: _drift_effective_root resolves the session's effective root exactly as the
#   write guard does (workspace-path-adapter.sh workspace_bind_effective_root): a
#   managed worktree (or the primary it owns) when the session has an active
#   assignment, else the cwd's git toplevel. Callers resolve it once and pass it to
#   the baseline and the check, so both read the same checkout.
# Drift = paths dirty now in that root (git status; gitignored files never appear)
#         minus paths dirty when the task was claimed (the baseline)
#         minus the plan's tasks[].allowed_files: relative entries as-is, absolute
#         entries under the root made root-relative, other absolute entries dropped.
#         Exact string match only.
#
# Fail-open (no block; the caller logs via log_hook): professional mode not 'on';
#   stage not executing/reviewing; no cwd or cwd not in a git repo; assignment
#   evidence unreadable, ambiguous or unbindable (reason in _DRIFT_ROOT_ERROR);
#   session row, plan_json or its tasks array unreadable; no baseline for the task.
# Fail-closed (block): a resolved root, readable baseline and plan, non-empty drift.
# Baselines are write-once per wave_tasks row id.

_DRIFT_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The write guard's effective-root resolver. Sourcing only assigns empty WORKSPACE_*
# globals. If it is missing, sessions with workspace evidence fail open.
source "$_DRIFT_HOOKS_DIR/workspace-path-adapter.sh" 2>/dev/null || true

_DRIFT_ROOT=""
_DRIFT_ROOT_ERROR=""

_drift_sql() {
  local db="$HOME/.claude/ironclaude.db"
  [ -f "$db" ] || return 0
  sqlite3 -cmd ".timeout 10000" "$db" "$1" 2>/dev/null || true
}

_drift_q() { printf '%s' "${1:-}" | sed "s/'/''/g"; }

# _drift_effective_root SESSION CWD
# Status 0: _DRIFT_ROOT holds the root. Status 1: fail-open, reason in _DRIFT_ROOT_ERROR.
# Call it directly (never in $(...)) so the caller can log the reason. Writes
# nothing to stdout. May renew the primary-checkout owner heartbeat (as every
# guarded file operation does).
_drift_effective_root() {
  local session="$1" cwd="${2:-}" top event_cwd common repository_identity workspace_db safe_session row_count
  _DRIFT_ROOT=""
  _DRIFT_ROOT_ERROR=""
  if [ -z "$cwd" ] || [ ! -d "$cwd" ]; then
    _DRIFT_ROOT_ERROR="hook cwd is missing"
    return 1
  fi
  if ! top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || [ -z "$top" ]; then
    _DRIFT_ROOT_ERROR="hook cwd is not in a git repository"
    return 1
  fi
  workspace_db="${WORKSPACE_MANAGER_DB_PATH:-$HOME/.claude/ironclaude-workspaces.db}"
  if [ ! -f "$workspace_db" ]; then
    _DRIFT_ROOT="$top"
    return 0
  fi
  if ! type workspace_bind_effective_root >/dev/null 2>&1; then
    _DRIFT_ROOT_ERROR="workspace-path-adapter.sh is not loaded"
    return 1
  fi
  if ! event_cwd=$(workspace_canonical_dir "$cwd"); then
    _DRIFT_ROOT_ERROR="hook cwd is not a canonical directory"
    return 1
  fi
  if ! common=$(git -C "$event_cwd" rev-parse --git-common-dir 2>/dev/null); then
    _DRIFT_ROOT_ERROR="cannot resolve the git common directory"
    return 1
  fi
  case "$common" in /*) ;; *) common="$event_cwd/$common" ;; esac
  if ! repository_identity=$(workspace_canonical_dir "$common"); then
    _DRIFT_ROOT_ERROR="git common directory is not canonical"
    return 1
  fi
  safe_session=$(workspace_sql_quote "$session")
  if ! row_count=$(sqlite3 "$workspace_db" ".timeout 10000" \
      "SELECT COUNT(*) FROM assignments WHERE repository_identity='$(workspace_sql_quote "$repository_identity")' AND owner_session_id='$safe_session' AND lifecycle_status NOT IN ('integrated','abandoned','cleaned');" 2>/dev/null); then
    _DRIFT_ROOT_ERROR="cannot read managed-worktree assignment"
    return 1
  fi
  if [ "$row_count" = "0" ]; then
    _DRIFT_ROOT="$top"
    return 0
  fi
  if [ "$row_count" != "1" ]; then
    _DRIFT_ROOT_ERROR="ambiguous managed-worktree assignment"
    return 1
  fi
  # workspace_bind_effective_root compares the primary owner to $SESSION_TAG.
  local SESSION_TAG="$session"
  if ! workspace_bind_effective_root "$workspace_db" "$repository_identity" "$event_cwd" "$safe_session"; then
    _DRIFT_ROOT_ERROR="managed-worktree binding failed: ${WORKSPACE_ADAPTER_ERROR:-unknown}"
    return 1
  fi
  _DRIFT_ROOT="$WORKSPACE_EFFECTIVE_ROOT"
  return 0
}

# Repo-relative dirty paths, one per line, sorted. --untracked-files=all lists
# files inside new directories individually; -z avoids path quoting. A rename or
# copy record is followed by its source path, which is skipped.
_drift_dirty_paths() {
  local root="$1"
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || return 1
  git -C "$root" status --porcelain=v1 -z --untracked-files=all 2>/dev/null | {
    rec=""
    skip_next=0
    while IFS= read -r -d '' rec; do
      if [ "$skip_next" = "1" ]; then
        skip_next=0
        continue
      fi
      case "${rec:0:2}" in
        R?|C?|?R|?C) skip_next=1 ;;
      esac
      printf '%s\n' "${rec:3}"
    done
  } | LC_ALL=C sort -u
}

# _drift_allowed_files SESSION ROOT
# The plan's tasks[].allowed_files in root-relative form, sorted. Absolute entries
# under ROOT lose the "ROOT/" prefix; other absolute entries are dropped (they can
# never equal a git status path); relative entries pass through. Status 1 when no
# plan or no tasks array.
_drift_allowed_files() {
  local plan out root="$2" entry
  plan=$(_drift_sql "SELECT plan_json FROM sessions WHERE terminal_session='$(_drift_q "$1")';")
  [ -n "$plan" ] || return 1
  out=$(printf '%s' "$plan" | jq -r 'if (.tasks | type) == "array" then [.tasks[] | (.allowed_files // [])[] | strings] | .[] else error("plan has no tasks array") end' 2>/dev/null) || return 1
  printf '%s\n' "$out" | while IFS= read -r entry; do
    case "$entry" in
      '') ;;
      "$root"/*) printf '%s\n' "${entry#"$root"/}" ;;
      /*) ;;
      *) printf '%s\n' "$entry" ;;
    esac
  done | sed '/^$/d' | LC_ALL=C sort -u
}

_drift_baseline_file() {  # SESSION ROW_ID
  local safe
  safe=$(printf '%s' "$1" | tr -cd 'a-zA-Z0-9_-')
  printf '%s/.claude/ironclaude-drift-baseline-%s-%s.txt' "$HOME" "$safe" "$2"
}

# Newest wave_tasks row id for SESSION's TASK_ID in the current wave.
_drift_row_id() {  # SESSION TASK_ID
  local id
  [[ "${2:-}" =~ ^[0-9]+$ ]] || return 1
  id=$(_drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.task_id=$2 AND w.wave_number = s.current_wave ORDER BY w.id DESC LIMIT 1;")
  [ -n "$id" ] || return 1
  printf '%s' "$id"
}

# ic_drift_record_baseline SESSION TASK_ID ROOT  (ROOT from _drift_effective_root)
# Status 0: baseline written or already present. Status 1: skipped.
ic_drift_record_baseline() {
  local row file root="${3:-}" paths tmp
  [ -n "$root" ] || return 1
  row=$(_drift_row_id "$1" "$2") || return 1
  file=$(_drift_baseline_file "$1" "$row")
  [ -e "$file" ] && return 0
  paths=$(_drift_dirty_paths "$root") || return 1
  mkdir -p "$(dirname "$file")" || return 1
  tmp="${file}.tmp.$$"
  printf '%s\n' "$paths" > "$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
  return 0
}

# ic_drift_violations SESSION ROOT ROW_ID...  (ROOT from _drift_effective_root)
# Prints drift paths (possibly none) with status 0; status 1 means fail-open.
ic_drift_violations() {
  local session="$1" root="$2" allowed baseline="" row file current
  shift 2
  [ -n "$root" ] || return 1
  allowed=$(_drift_allowed_files "$session" "$root") || return 1
  for row in "$@"; do
    file=$(_drift_baseline_file "$session" "$row")
    [ -f "$file" ] || return 1
    baseline="${baseline}$(cat "$file")"$'\n'
  done
  current=$(_drift_dirty_paths "$root") || return 1
  LC_ALL=C comm -23 \
    <(printf '%s\n' "$current" | sed '/^$/d' | LC_ALL=C sort -u) \
    <(printf '%s\n%s\n' "$baseline" "$allowed" | sed '/^$/d' | LC_ALL=C sort -u)
}

# ic_drift_stop_violations SESSION ROOT  (ROOT from _drift_effective_root)
# Stop-time check across every in_progress task of the current wave. Status 0 with
# no output when nothing is in progress; status 1 means fail-open.
ic_drift_stop_violations() {
  local rows
  rows=$(_drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.status='in_progress' AND w.wave_number = s.current_wave ORDER BY w.id;")
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
    "These files changed after the current task was claimed, and none of them is in the plan's allowed_files:" \
    "$list" \
    "Remove them if they are a side quest. If the plan needs them, retreat and amend the plan so they are listed in allowed_files."
}

# Library mode: define the functions only.
if [ "${IC_DRIFT_LIB:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

SCRIPT_DIR="$_DRIFT_HOOKS_DIR"
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "EXECUTION-DRIFT-CHECK"

INPUT=$(cat)
init_session_id

# Professional-mode gate (idiom from plan-execution-reminder.sh). A plain read,
# not db_read_or_fail: its hard_fail exits 2 and would block claim/submit.
SAFE_SESSION=$(_drift_q "$SESSION_TAG")
PROF_MODE=$(_drift_sql "SELECT professional_mode FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
if [ "$PROF_MODE" != "on" ]; then
  log_hook "EXECUTION-DRIFT-CHECK" "Disabled" "professional mode ${PROF_MODE:-unreadable}"
  exit 0
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
EVENT_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
TASK_ID=$(printf '%s' "$INPUT" | jq -r '.tool_input.task_id // empty' 2>/dev/null || true)
case "$TOOL_NAME" in
  *state_manager__*|*state-manager__*) ;;
  *)
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "not a state-manager tool: ${TOOL_NAME:-none}"
    exit 0
    ;;
esac
SHORT_TOOL="${TOOL_NAME##*__}"

WORKFLOW=$(_drift_sql "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
if [ "$WORKFLOW" != "executing" ] && [ "$WORKFLOW" != "reviewing" ]; then
  log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "workflow_stage=${WORKFLOW:-unreadable}"
  exit 0
fi

case "$SHORT_TOOL" in
  claim_task|submit_task) ;;
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
  claim_task)
    if ic_drift_record_baseline "$SESSION_TAG" "$TASK_ID" "$_DRIFT_ROOT"; then
      log_hook "EXECUTION-DRIFT-CHECK" "Baseline" "task ${TASK_ID}: baseline present (write-once), root ${_DRIFT_ROOT}"
    else
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: no baseline (no wave_tasks row or git failed)"
    fi
    exit 0
    ;;
  submit_task)
    if ! ROW_ID=$(_drift_row_id "$SESSION_TAG" "$TASK_ID"); then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: no wave_tasks row in the current wave"
      exit 0
    fi
    if ! DRIFT=$(ic_drift_violations "$SESSION_TAG" "$_DRIFT_ROOT" "$ROW_ID"); then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID}: fail-open (no plan or baseline)"
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

**Step 4: Resolve the root in the Stop caller**

In `worker/hooks/get-back-to-work-impl.sh`, replace this text (lines 468-484):
```bash
    # Execution drift check (execution-drift-check.sh, R5): while executing, block on
    # files changed since the in-progress task's claim that are outside the plan's
    # allowed_files. It goes through increment_block_counter like every Stop block,
    # so the 3-blocks-in-5-minutes escape hatch above still applies. Fail-open when
    # git, the plan, or a baseline is unavailable.
    if [ "$WORKFLOW_STAGE" = "executing" ] && [ "$IS_SUBAGENT" = "false" ] \
       && type ic_drift_stop_violations >/dev/null 2>&1; then
        _ic_drift_cwd=$(printf '%s' "$EVENT" | jq -r '.cwd // empty' 2>/dev/null || true)
        if _ic_drift_files=$(ic_drift_stop_violations "$SESSION_TAG" "$_ic_drift_cwd"); then
            if [ -n "$_ic_drift_files" ]; then
                increment_block_counter
                block_stop "GET-BACK-TO-WORK" "$(ic_drift_block_message "$_ic_drift_files")"
            fi
        else
            log_hook "GET-BACK-TO-WORK" "Skipped" "execution drift check fail-open (no git repo, plan, or baseline)"
        fi
    fi
```
with this text:
```bash
    # Execution drift check (execution-drift-check.sh, R5): while executing, block on
    # files changed since the in-progress task's claim that are outside the plan's
    # allowed_files. The root is the session's effective root (managed worktree or
    # cwd toplevel), resolved by the same function the claim baseline used. It goes
    # through increment_block_counter like every Stop block, so the
    # 3-blocks-in-5-minutes escape hatch above still applies. Fail-open when the
    # root, the plan, or a baseline is unavailable.
    if [ "$WORKFLOW_STAGE" = "executing" ] && [ "$IS_SUBAGENT" = "false" ] \
       && type ic_drift_stop_violations >/dev/null 2>&1 \
       && type _drift_effective_root >/dev/null 2>&1; then
        _ic_drift_cwd=$(printf '%s' "$EVENT" | jq -r '.cwd // empty' 2>/dev/null || true)
        if ! _drift_effective_root "$SESSION_TAG" "$_ic_drift_cwd"; then
            log_hook "GET-BACK-TO-WORK" "Skipped" "execution drift check fail-open: ${_DRIFT_ROOT_ERROR}"
        elif _ic_drift_files=$(ic_drift_stop_violations "$SESSION_TAG" "$_DRIFT_ROOT"); then
            if [ -n "$_ic_drift_files" ]; then
                increment_block_counter
                block_stop "GET-BACK-TO-WORK" "$(ic_drift_block_message "$_ic_drift_files")"
            fi
        else
            log_hook "GET-BACK-TO-WORK" "Skipped" "execution drift check fail-open (no plan or baseline)"
        fi
    fi
```

**Step 5: Run the tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh
```
Expected: `Results: 8 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
```
Expected: `Results: 9 pass, 0 fail`, exit 0. That suite has no workspace DB, so the root falls back to the cwd toplevel, as in L2b-1.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
```
Expected: last line `ALL WRAPPER TESTS PASSED`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-managed-worktree-guard.sh
```
Expected: exit 0. The adapter is unchanged; this confirms the drift check's sourcing did not alter it.

**Step 6: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-execution-drift-roots.sh
```
Expected: no output, exit 0.

---

## Task 2: `state-activator.sh` emits at most one JSON object (R4)

**Files:**
- Modify: `worker/hooks/state-activator.sh`, after `run_hook "state-activator"` (line 7).
- Create: `worker/hooks/tests/test-state-activator-single-object.sh`.

**Grounding:**
- `state-activator.sh` never emits a `decision`. Every stdout write comes from:
  - `log_hook` (`:28`, `:125`, `:140`, `:168`, `:178`, `:211`, `:214`), plus the `log_hook` calls inside `surface_mcp_errors` (`:34`);
  - `log_error` (`:114`, `:116`, `:127`);
  - `log_warning` (`:170`, `:180`);
  - `ic_drain_warnings` (`:38`).
- Each of these prints one single-line JSON object.
- The script's only exit is `exit 0` (`:43`, `:228`), plus the `run_hook` ERR trap, which calls `log_error` and then `exit 1` (`hook-logger.sh:555-556`).
- `hook-logger.sh:37` installs an EXIT trap that removes `_IC_DB_VALIDATED`. The new EXIT trap replaces it, so it removes that file itself.

**Step 1: Write the test (RED)**

Create `worker/hooks/tests/test-state-activator-single-object.sh`:
```bash
#!/usr/bin/env bash
# test-state-activator-single-object.sh - state-activator.sh prints at most one JSON
# object per run, merging systemMessage texts with the warning drain (v1.1.16 L2b-1b R4).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-sa-single-object"
PASSES=0
FAILS=0
EXPECTED_PASSES=4
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

run_prompt() {  # PROMPT -> stdout; exit code in $TMP_ROOT/rc
  jq -cn --arg s "$SESSION" --arg p "$1" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:$p}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/state-activator.sh" 2>/dev/null
  echo $? > "$TMP_ROOT/rc"
}

# A queued warning plus a forced log_error: "/commit" with professional mode not on
# reaches state-activator.sh's "Human intent issuance requires professional mode on" log_error.
HOME="$TMP_HOME" bash -c 'source "$1/hook-logger.sh"; ic_queue_warning "$2" "W-F4 queued warning"' _ "$HOOKS_DIR" "$SESSION"
OUT=$(run_prompt "/commit")
RC=$(cat "$TMP_ROOT/rc")

# 1. Exactly one parseable JSON object.
printf '%s' "$OUT" | jq -cse 'length == 1' >/dev/null 2>&1 && ok "1 exactly one JSON object" || bad "1 exactly one JSON object (got: $OUT)"

# 2. It carries both texts: the log_error in systemMessage, the warning in additionalContext.
if printf '%s' "$OUT" | jq -cse 'length == 1
    and (.[0].systemMessage | contains("Human intent issuance requires professional mode on"))
    and (.[0].hookSpecificOutput.hookEventName == "UserPromptSubmit")
    and (.[0].hookSpecificOutput.additionalContext | contains("W-F4 queued warning"))' >/dev/null 2>&1; then
  ok "2 merged object holds the error and the queued warning"
else
  bad "2 merged object holds the error and the queued warning (got: $OUT)"
fi

# 3. The exit code is preserved.
[ "$RC" = "0" ] && ok "3 exit code 0 preserved" || bad "3 exit code 0 preserved (rc=$RC)"

# 4. Nothing to say -> no output at all.
EMPTY=$(run_prompt "hello")
[ -z "$EMPTY" ] && ok "4 empty buffer prints nothing" || bad "4 empty buffer prints nothing (got: $EMPTY)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh
```
Expected: exit 1, with these failures:
- `FAIL  1 exactly one JSON object`
- `FAIL  2 merged object holds the error and the queued warning`

At the current tree, stdout holds two objects: the drain at `:38`, then `log_error` at `:114`. Cases 3 and 4 pass at the current tree.

**Step 3: Buffer and merge stdout in `state-activator.sh`**

Replace this text (lines 5-7):
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "state-activator"
```
with this text:
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"

# ─── Single-object stdout (v1.1.16 L2b-1b R4) ───
# Several writers below (log_hook, log_error, log_warning, surface_mcp_errors and the
# warning-queue drain) each print one JSON line. A client parses hook stdout as ONE
# object, so the body's stdout is buffered and merged on exit: every systemMessage
# (or non-JSON line) is newline-joined into one systemMessage, and the drain's
# additionalContext becomes hookSpecificOutput. Nothing is printed when there is
# nothing to say. This hook emits no decisions; the exit code is preserved.
# This trap replaces hook-logger.sh's EXIT trap, so it also removes _IC_DB_VALIDATED.
_ic_sa_flush() {
  local rc=$?
  exec 1>&3 3>&-
  if [ -s "$_IC_SA_OUT" ]; then
    jq -R -s -c '
      [split("\n")[] | select(length > 0) | . as $line | ((fromjson? | objects) // {"__text": $line})] as $items
      | [$items[] | (.systemMessage // .__text // (if has("hookSpecificOutput") then empty else tojson end)) | select(type == "string")] as $sys
      | [$items[] | (.hookSpecificOutput.additionalContext? // empty) | select(type == "string")] as $ctx
      | if ($sys | length) == 0 and ($ctx | length) == 0 then empty
        else (if ($sys | length) > 0 then {systemMessage: ($sys | join("\n"))} else {} end)
           + (if ($ctx | length) > 0 then {hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: ($ctx | join("\n"))}} else {} end)
        end
    ' "$_IC_SA_OUT" 2>/dev/null || cat "$_IC_SA_OUT"
  fi
  rm -f "$_IC_SA_OUT" "${_IC_DB_VALIDATED:-}" 2>/dev/null
  exit "$rc"
}
_IC_SA_OUT=$(mktemp "${TMPDIR:-/tmp}/.ic-state-activator-XXXXXX" 2>/dev/null) || _IC_SA_OUT=""
if [ -n "$_IC_SA_OUT" ]; then
  exec 3>&1 1>"$_IC_SA_OUT"
  trap '_ic_sa_flush' EXIT
fi

run_hook "state-activator"
```

Then replace the drain comment:
```bash
# Deliver warnings queued by earlier hooks (GBTW) as next-turn context on both
# clients. Prints one JSON object, or nothing when the queue is empty.
```
with:
```bash
# Deliver warnings queued by earlier hooks (GBTW) as next-turn context on both
# clients. The exit-time merge folds it into this hook's single output object.
```

**Step 4: Run the tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh
```
Expected: `Results: 4 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-warning-queue.sh
```
Expected: `Results: 7 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```
Expected: exit 0. Its `assert_contains` checks look for message text, such as "Human intent issuance runtime is unavailable" and "verification required". That text is still present inside the merged `systemMessage`.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh
```
Expected: exit 0.

**Step 5: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/state-activator.sh worker/hooks/tests/test-state-activator-single-object.sh
```
Expected: no output, exit 0.

---

## Task 3: Full verification and redeploy (R5)

Depends on Tasks 1 and 2.

**Files:**
- Create: `docs/plans/2026-10-03-drift-check-review-fixes-deploy-log.md`.
- `make deploy-hooks` and `codex plugin add` also write runtime copies outside the repo: `~/.claude/ironclaude-hooks`, `~/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks` and the Codex cache. These are generated install output, not edits.

No new tests: this task runs the suites and deploys.

**Step 1: Run every hook suite**

Run:
```bash
make -C /Users/roberthyatt/Code/ironclaude test-hooks
```
Expected: exit 0, with the two new suites' headers printed. Pass counts were not measured during planning.

**Step 2: Run the Commander suite**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: `0 failed`, exit 0. The count was not measured during planning.

**Step 3: Deploy hooks**

Run:
```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```
Expected output includes:
```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```
The count is 31 `*.sh` files at planning time; this loop adds no hook script.

**Step 4: Check Claude's plugin `hooks.json`**

Run:
```bash
diff /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json
```
Expected: no output, exit 0. `hooks.json` is in no task's `allowed_files`, so this loop does not change it, and this diff printed nothing during planning. Skip the copy and record "`hooks.json` unchanged — copy skipped" in the deploy log.

If the diff unexpectedly prints differences, record them in the deploy log and run:
```bash
cp /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json
```
Then re-run the diff and expect no output.

**Step 5: Refresh the Codex plugin cache**

Run:
```bash
codex plugin add ironclaude@ironclaude --json
```
Expected: exit 0, with JSON `installedPath` `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`.

**Step 6: Verify the Codex cache matches `worker/`**

Run:
```bash
diff -rq /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837 -x node_modules -x __pycache__ -x .DS_Store
```
Expected: no output, exit 0.

**Step 7: Write and stage the deploy log**

Create `docs/plans/2026-10-03-drift-check-review-fixes-deploy-log.md` with:
- the quoted output of Steps 1–6;
- a note that `hooks.json` is unchanged, so no Codex hook trust key changes;
- a note that, per R6, the operator commits L2b-1 and L2b-1b together, and the four new or changed Codex hooks are re-trusted only after that commit.

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-drift-check-review-fixes-deploy-log.md
```
Expected: no output, exit 0.
