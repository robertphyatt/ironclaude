# Parity Output and Path Fixes Implementation Plan (v1.1.16 L2b-1c)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Map absolute `allowed_files` entries to the drift root by file identity, make `state-activator.sh` emit context only, and stop gating MCP sideband errors. Then verify and redeploy.

**Requirements:** `docs/plans/2026-10-03-parity-output-path-fixes-requirements.md`

**Design:** `docs/plans/2026-10-03-parity-output-path-fixes-design.md`

**Loop:** full. This loop changes hook enforcement code.

**Architecture:**
- `execution-drift-check.sh` gains `_drift_rel_by_identity`. It walks from an absolute entry's longest existing ancestor up to `/`, comparing each directory to the root with `[ -ef ]`.
- `state-activator.sh`'s exit-time flush joins every buffered text into one `UserPromptSubmit` `additionalContext` object.
- `hook-logger.sh:614` logs MCP errors at level `ERROR`.

**Tech Stack:** bash (3.2-compatible), jq, sqlite3, git.

## Execution invariants

- Shell state does not persist between steps. Use literal absolute paths.
- Use `git -C /Users/roberthyatt/Code/ironclaude` for git commands.
- zsh has `nomatch`, so quote any glob.
- `docs/` is gitignored. Plan artifacts need `git add -f`.
- An `expected:` labeled "predicted" was not measured at planning time. This is because Bash is limited to read-only commands outside execution. The executor reports the actual value. If the actual value contradicts the prediction, the executor stops and reports; it does not adapt.
- Measured at planning time (2026-10-03, during the L2b-1b runs and reads):
  - `test-execution-drift-check.sh`: 9 pass.
  - `test-execution-drift-roots.sh`: 8 pass.
  - `test-warning-queue.sh`: 7 pass.
  - `test-professional-mode-deactivation.sh`: 28 pass.
  - `test-git-authority-activation.sh`: 94 passed.
  - `deploy-hooks`: 31 hooks.
  - Plugin `hooks.json` diff: empty.
  - Codex cache `diff -rq`: empty.
  - Commander suite: 3457 passed, about 390 s. Pass `timeout` 600000.
- Only one of the suites that run `state-activator` asserts on `systemMessage`: `test-state-activator-single-object.sh`. Checked with `grep -n -e systemMessage` across `test-professional-mode-deactivation.sh`, `test-git-authority-activation.sh` and `pm-deactivation-audit.test.ts`; none of them match. `test-warning-queue.sh` case 4 asserts `length == 1` plus `additionalContext` text, which still holds under the new shape.
- Provenance for R3: `git log -S 'log_hook "MCP-ERROR" "Error"'` and `git log -S "Gate verbose output: only emit Blocked/ERROR"` both return `3287f3a Initial commit` (and `09eedd3`). The two were never reconciled. The comments say "only emit Blocked/ERROR" and "Read and display MCP error sideband log", so the lowercase level was not a deliberate choice.

---

## Task 1: Identity-based mapping of absolute allowed_files (R1)

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh:130-148` (`_drift_allowed_files` and its comment)
- Create: `worker/hooks/tests/test-execution-drift-identity.sh`

**Step 1 (RED): create `worker/hooks/tests/test-execution-drift-identity.sh`**

```bash
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
SESSION="ic-drift-identity-session"
DB="$TMP_HOME/.claude/ironclaude.db"
mkdir -p "$TMP_HOME/.claude" "$REPO" "${REPO}-other"
ln -s "$REPO" "$LINK"
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
EXPECTED_PASSES=4
[ "$CASE_INSENSITIVE" = "1" ] || EXPECTED_PASSES=3

PLAN=$(jq -cn --arg l "$LINK" --arg c "$CASED" --arg o "${REPO}-other" \
  '{name:"t",tasks:[{id:1,allowed_files:[($l + "/src/a.txt"),($l + "/gone/old.txt"),($c + "/case/c.txt"),($o + "/side.txt")]}]}')
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1, plan_name='t', plan_json='${PLAN}' WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'in_progress');"

# Clean baseline at claim.
RC=$(invoke "$CLAIM" "PostToolUse")
[ "$RC" = "0" ] || bad "setup: claim baseline (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 1. A symlinked spelling of the root excuses a new file in a new directory.
mkdir -p "$REPO/src"
printf 'a\n' > "$REPO/src/a.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "1 symlinked spelling of the root is allowed" || bad "1 symlinked spelling of the root is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 2. A symlinked spelling of a deleted file whose directory no longer exists.
rm -rf "$REPO/gone"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "2 entry under a removed directory is allowed" || bad "2 entry under a removed directory is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"

# 3. A case-variant spelling of the root (case-insensitive volumes only).
if [ "$CASE_INSENSITIVE" = "1" ]; then
  mkdir -p "$REPO/case"
  printf 'c\n' > "$REPO/case/c.txt"
  RC=$(invoke "$SUBMIT" "PreToolUse")
  [ "$RC" = "0" ] && ok "3 case-variant spelling of the root is allowed" || bad "3 case-variant spelling of the root is allowed (rc=$RC: $(cat "$TMP_ROOT/err"))"
else
  printf 'SKIP  3 case-variant spelling (volume is case-sensitive)\n'
fi

# 4. Negative: a sibling-prefix entry ($REPO-other/side.txt) does not excuse
#    side.txt, which is then the only drift listed.
printf 'side\n' > "$REPO/side.txt"
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

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: run it and see it fail**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh
```

Expected (predicted): exit 1, with FAIL lines for cases 1, 2, 4 and, on a case-insensitive volume, 3.
- Today `/*` drops every absolute entry not spelled under `$REPO/`.
- That makes `src/a.txt`, `gone/old.txt` and `case/c.txt` drift, so each submit exits 2.
- Case 4 fails because those paths are listed alongside `side.txt`.

**Step 3: in `worker/hooks/execution-drift-check.sh`, replace lines 130-148 (the `_drift_allowed_files` comment and function) with:**

```bash
# _drift_rel_by_identity ENTRY ROOT
# ENTRY is absolute and not spelled under "ROOT/". Walk from ENTRY's longest existing
# ancestor up to "/"; the first directory that is the same file as ROOT ([ -ef ]:
# same device and inode) makes the rest of ENTRY the repo-relative path. This maps
# symlinked and case-variant spellings that the write guard accepts verbatim.
# Status 1 (entry dropped) when no ancestor is ROOT.
_drift_rel_by_identity() {
  local entry="$1" root="$2" dir
  dir=$(dirname -- "$entry")
  while [ "$dir" != "/" ] && [ ! -d "$dir" ]; do
    dir=$(dirname -- "$dir")
  done
  while [ "$dir" != "/" ]; do
    if [ "$dir" -ef "$root" ]; then
      printf '%s\n' "${entry#"$dir"/}"
      return 0
    fi
    dir=$(dirname -- "$dir")
  done
  return 1
}

# _drift_allowed_files SESSION ROOT
# The plan's tasks[].allowed_files in root-relative form, sorted. Absolute entries
# under ROOT lose the "ROOT/" prefix; other absolute entries map by file identity
# (_drift_rel_by_identity) or are dropped; relative entries pass through. Status 1
# when no plan or no tasks array.
_drift_allowed_files() {
  local plan out root="$2" entry
  plan=$(_drift_sql "SELECT plan_json FROM sessions WHERE terminal_session='$(_drift_q "$1")';")
  [ -n "$plan" ] || return 1
  out=$(printf '%s' "$plan" | jq -r 'if (.tasks | type) == "array" then [.tasks[] | (.allowed_files // [])[] | strings] | .[] else error("plan has no tasks array") end' 2>/dev/null) || return 1
  printf '%s\n' "$out" | while IFS= read -r entry; do
    case "$entry" in
      '') ;;
      "$root"/*) printf '%s\n' "${entry#"$root"/}" ;;
      /*) _drift_rel_by_identity "$entry" "$root" || true ;;
      *) printf '%s\n' "$entry" ;;
    esac
  done | sed '/^$/d' | LC_ALL=C sort -u
}
```

**Step 4: run the new test**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh
```

Expected: on this Mac (APFS, case-insensitive by default; predicted) `Results: 4 pass, 0 fail`, exit 0. On a case-sensitive volume the output is `SKIP  3 …` and `Results: 3 pass, 0 fail`, exit 0.

**Step 5: run the existing drift suites**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh
```

Expected: `Results: 8 pass, 0 fail`, exit 0. Its out-of-root negatives `/side-wt.txt` and `<primary>/side-wt.txt` must stay dropped. `<primary>` is a parent of the worktree root, not the root, so the walk passes over it.

**Step 6:**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
```

Expected: `Results: 9 pass, 0 fail`, exit 0.

**Step 7: stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-identity.sh
```

Expected: no output, exit 0.

---

## Task 2: Context-only state-activator output and the MCP error level (R2, R3)

**Files:**
- Modify: `worker/hooks/state-activator.sh:8-32` (the header comment and `_ic_sa_flush`) and `:68-69` (the drain comment)
- Modify: `worker/hooks/hook-logger.sh:614`
- Modify: `worker/hooks/tests/test-state-activator-single-object.sh` (full replacement)

**Step 1 (RED): replace `worker/hooks/tests/test-state-activator-single-object.sh` with:**

```bash
#!/usr/bin/env bash
# test-state-activator-single-object.sh - state-activator.sh prints at most one JSON
# object per run, and only as UserPromptSubmit additionalContext: systemMessage texts,
# MCP sideband errors and the warning drain all reach the model on both clients
# (v1.1.16 L2b-1b R4, L2b-1c R2/R3).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'chmod 755 "$TMP_HOME/.claude" 2>/dev/null; rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-sa-single-object"
PASSES=0
FAILS=0
EXPECTED_PASSES=6
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
# Exactly one object, whose only key is a UserPromptSubmit hookSpecificOutput.
CTX_ONLY='length == 1 and (.[0] | keys) == ["hookSpecificOutput"] and .[0].hookSpecificOutput.hookEventName == "UserPromptSubmit"'

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

# 1. Exactly one object, context only (no systemMessage key).
printf '%s' "$OUT" | jq -cse "$CTX_ONLY" >/dev/null 2>&1 && ok "1 exactly one context-only object" || bad "1 exactly one context-only object (got: $OUT)"

# 2. The context carries both texts: the log_error and the queued warning.
if printf '%s' "$OUT" | jq -cse "$CTX_ONLY
    and (.[0].hookSpecificOutput.additionalContext | contains(\"Human intent issuance requires professional mode on\"))
    and (.[0].hookSpecificOutput.additionalContext | contains(\"W-F4 queued warning\"))" >/dev/null 2>&1; then
  ok "2 context holds the error and the queued warning"
else
  bad "2 context holds the error and the queued warning (got: $OUT)"
fi

# 3. The exit code is preserved.
[ "$RC" = "0" ] && ok "3 exit code 0 preserved" || bad "3 exit code 0 preserved (rc=$RC)"

# 4. Nothing to say -> no output at all.
EMPTY=$(run_prompt "hello")
[ -z "$EMPTY" ] && ok "4 empty buffer prints nothing" || bad "4 empty buffer prints nothing (got: $EMPTY)"

# 5. R3: an MCP sideband error reaches the context with verbose logging off
#    (no ironclaude-hooks-config.json exists in this HOME).
printf 'MCP-LINE-77 sideband failure\n' > "$TMP_HOME/.claude/ironclaude-errors.log"
MCP_OUT=$(run_prompt "hello")
if printf '%s' "$MCP_OUT" | jq -cse "$CTX_ONLY and (.[0].hookSpecificOutput.additionalContext | contains(\"MCP-LINE-77 sideband failure\"))" >/dev/null 2>&1; then
  ok "5 MCP sideband error reaches context without verbose logging"
else
  bad "5 MCP sideband error reaches context without verbose logging (got: $MCP_OUT)"
fi

# 6. ERR path: a pending PPID marker in a read-only ~/.claude makes the unguarded
#    mktemp at state-activator.sh:57 fail under run_hook's ERR trap, which logs and
#    exits 1. The flush keeps exit code 1 and delivers the failure as context.
touch "$TMP_HOME/.claude/ironclaude-ppid-pending-424242"
chmod 555 "$TMP_HOME/.claude"
ERR_OUT=$(run_prompt "hello")
ERR_RC=$(cat "$TMP_ROOT/rc")
chmod 755 "$TMP_HOME/.claude"
if [ "$ERR_RC" = "1" ] && printf '%s' "$ERR_OUT" | jq -cse "$CTX_ONLY and (.[0].hookSpecificOutput.additionalContext | contains(\"Unexpected failure\"))" >/dev/null 2>&1; then
  ok "6 ERR path exits 1 with the failure in context"
else
  bad "6 ERR path exits 1 with the failure in context (rc=$ERR_RC, got: $ERR_OUT)"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: run it and see it fail**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh
```

Expected (predicted): exit 1, `Results: 2 pass, 4 fail`.
- Cases 1, 2 and 6 FAIL: today's merged object has a `systemMessage` key.
- Case 5 FAILs: the `"Error"` level is gated, so nothing is printed.
- Cases 3 and 4 PASS.

**Step 3: in `worker/hooks/state-activator.sh`, replace the block from the line `# ─── Single-object stdout (v1.1.16 L2b-1b R4) ───` through the closing `}` of `_ic_sa_flush` (lines 8-32) with:**

```bash
# ─── Single-object, context-only stdout (v1.1.16 L2b-1b R4, L2b-1c R2) ───
# Several writers below (log_hook, log_error, log_warning, surface_mcp_errors and the
# warning-queue drain) each print one JSON line. This hook's messages are enforcement
# failures and queued warnings the model must act on, so the body's stdout is buffered
# and, on exit, every text (a systemMessage, a drained additionalContext, or a non-JSON
# line) is newline-joined in buffer order into ONE UserPromptSubmit additionalContext
# object: the one shape both clients deliver to the model. No systemMessage is
# emitted. Nothing is printed when there is nothing to say. This hook emits no
# decisions; the exit code is preserved.
# This trap replaces hook-logger.sh's EXIT trap, so it also removes _IC_DB_VALIDATED.
_ic_sa_flush() {
  local rc=$?
  exec 1>&3 3>&-
  if [ -s "$_IC_SA_OUT" ]; then
    jq -R -s -c '
      [split("\n")[] | select(length > 0) | . as $line | ((fromjson? | objects) // {"__text": $line})
        | (.systemMessage // .hookSpecificOutput.additionalContext? // .__text // (if has("hookSpecificOutput") then empty else tojson end))
        | select(type == "string")]
      | if length == 0 then empty
        else {hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: join("\n")}}
        end
    ' "$_IC_SA_OUT" 2>/dev/null || cat "$_IC_SA_OUT"
  fi
  rm -f "$_IC_SA_OUT" "${_IC_DB_VALIDATED:-}" 2>/dev/null
  exit "$rc"
}
```

Lines 33-37 (`_IC_SA_OUT=$(mktemp …)` through `fi`) stay unchanged.

**Step 4: in `worker/hooks/state-activator.sh`, replace the drain comment.** The current comment is:

```bash
# Deliver warnings queued by earlier hooks (GBTW) as next-turn context on both
# clients. The exit-time merge folds it into this hook's single output object.
```

Replace it with:

```bash
# Deliver warnings queued by earlier hooks (GBTW) as next-turn context on both
# clients. The exit-time flush joins it into this hook's single context object.
```

**Step 5: in `worker/hooks/hook-logger.sh:614`, change**

```bash
        log_hook "MCP-ERROR" "Error" "$line"
```

to

```bash
        log_hook "MCP-ERROR" "ERROR" "$line"
```

**Step 6: run the new test**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh
```

Expected: `Results: 6 pass, 0 fail`, exit 0.

**Step 7:**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-warning-queue.sh
```

Expected: `Results: 7 pass, 0 fail`, exit 0.

**Step 8:**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```

Expected: `Results: 28 pass, 0 fail`, exit 0.

**Step 9:**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh
```

Expected: `Results: 94 passed, 0 failed`, exit 0.

**Step 10: stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/state-activator.sh worker/hooks/hook-logger.sh worker/hooks/tests/test-state-activator-single-object.sh
```

Expected: no output, exit 0.

---

## Task 3: Full verification and redeploy (R5)

**Files:**
- Create: `docs/plans/2026-10-03-parity-output-path-fixes-deploy-log.md`

**No tests required:** this task runs the existing suites and deploys. `deploy-hooks` and `codex plugin add` write generated runtime copies outside the repo; these are install output, not edits.

**Step 1:**

```bash
make -C /Users/roberthyatt/Code/ironclaude test-hooks
```

Expected: exit 0. The headers for `test-execution-drift-identity.sh` and `test-state-activator-single-object.sh` are printed, and no non-zero fail count appears. The total count was not measured at planning.

**Step 2:** run with `timeout` 600000:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: `3457 passed`, exit 0. This loop changes no Python.

**Step 3:**

```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```

Expected: the output includes both of these lines:
- `Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks`
- `Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks`

**Step 4:**

```bash
diff /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json
```

Expected: no output, exit 0, so record "hooks.json unchanged — copy skipped". If the diff shows a difference, record it, run `cp /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json`, and diff again.

**Step 5:**

```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: exit 0, and the JSON `installedPath` is `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`.

**Step 6:**

```bash
diff -rq /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837 -x node_modules -x __pycache__ -x .DS_Store
```

Expected: no output, exit 0.

**Step 7:** write `docs/plans/2026-10-03-parity-output-path-fixes-deploy-log.md`. It should contain:
- the quoted outputs of Steps 1–6;
- "hooks.json unchanged";
- the R6 note: the operator commits L2b-1, L2b-1b and L2b-1c together, then re-trusts the 4 Codex hooks (`pre_tool_use:2`, `pre_tool_use:3`, `pre_tool_use:6`, `post_tool_use:6`).

**Step 8:**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-parity-output-path-fixes-deploy-log.md
```

Expected: no output, exit 0.
