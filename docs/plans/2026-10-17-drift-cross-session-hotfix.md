# Drift Check Cross-Session Hotfix Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop the execution drift check from blocking a session on files another live session wrote. The guard records the writer of every file-tool write it allows. The drift check drops a path whose newest writer record is another session's recent write, or that a live session's same-root ledger lists. The session's own shell side quests still block. Also close the primary-checkout gap that let Codex `apply_patch` and `exec_command` skip the stage and allowed_files gates.

**Requirements:** `docs/plans/2026-10-17-drift-cross-session-hotfix-design.md` (R1-R7, including the R2 refinements).

**Architecture:**
- **Writer log (R1).** `execution-drift-check.sh` gains the library function `ic_drift_record_writes SESSION BASE`. It reads target paths on stdin and appends `epoch<TAB>session<TAB>path` to `${IC_DRIFT_WRITERS_LOG:-$HOME/.claude/ironclaude-drift-writers.tsv}`:
  - `session` is `_drift_safe(SESSION)`.
  - `path` is absolute, made from BASE when relative, with its longest existing ancestor resolved by `cd -P`. PreToolUse runs before Write creates parent directories, so the ancestor walk is needed.
  - When the log passes `IC_DRIFT_WRITERS_MAX_LINES` (default `_DRIFT_WRITERS_MAX_DEFAULT=10000`), it keeps its newest half.
  - It runs in a `set +eu` subshell, never prints, and always returns 0.
- **Recording site (R1).** Every allow path in the guard exits through `workspace_allow` (`workspace-path-adapter.sh:539-545`), and only the guard calls it. That includes PM off (:416), the docs/ path (:609), the executing access check (:871), the "tool not handled" fall-through (:879) and the others. `workspace_allow` now calls the guard's `ic_guard_record_writer` in an isolated subshell before it emits the decision. The recorder filters by the ORIGINAL `.tool_name`:
  - `Edit|Write|MultiEdit|NotebookEdit` take their targets from `WORKSPACE_TARGET_FILES`, else from `file_path`/`notebook_path`.
  - `apply_patch` takes its targets from `WORKSPACE_TARGET_FILES`, else from the patch's `*** Add|Update|Delete File:` and `*** Move to:` lines, relative to `PROJECT_ROOT`.

  It pipes them to `ic_drift_record_writes` in a child `bash` with `IC_DRIFT_LIB=1`, so sourcing the drift library never resets the guard's `WORKSPACE_*` globals. `hooks.json` is unchanged.
- **Exclusion (R2, R3).** `ic_drift_violations` computes drift as before, then removes `_drift_foreign_paths SESSION ROOT CANDIDATES ROW_ID...` from it:
  - **(a) A recent writer of another session.** The newest writer-log record for the file belongs to another session, and its epoch is at or after `since`. `since` is the earliest `strftime('%s', created_at)` of this session's checked `wave_tasks` rows. With no `since`, rule (a) excludes nothing. Records are matched to candidates by basename, then mapped with `_drift_rel_by_identity`, so case and symlink spellings agree, and a record outside the root never matches.
  - **(b) A live session's same-root ledger.** Another session's ledger `ironclaude-drift-touched-<other>.<row>.txt` lists the path, and all of these hold:
    - its root file `<ledger>.root` equals this root;
    - `<row>` is numeric;
    - that `wave_tasks` row belongs to `<other>`;
    - `<other>`'s `workflow_stage` is `executing` or `reviewing`. `professional_mode` is not checked.

  `_drift_close_window_file` writes the `.root` file beside each ledger. A ledger without one is ignored.
- **Message (R4).** The block text keeps the side-quest, amend-plan and "never delete, revert or edit a file you did not change" guidance. It drops "It clears by itself once that session commits or reverts it".

- **Codex in a primary checkout (R7).** With no managed-worktree assignment, the guard maps `apply_patch` to `MultiEdit` and `exec_command` to `Bash` before its write gates. Every patch target is resolved by the adapter's `workspace_rewrite_patch` against the cwd, without rewriting the patch, and checked by the existing docs and allowed_files gates. Before this, both names fell through to the allow at :879. The recorder reads the same `WORKSPACE_TARGET_FILES`.

**Accepted residuals (R3):**
- Another live session's shell write during an overlapping window lands in both ledgers and is excluded for both (fail open).
- A session left in executing/reviewing keeps its ledgers live until its next `create_plan` or reset.
- A finished session's shell write during an overlapping window blocks, as it did before the fix.
- This session's shell may rewrite a file whose newest record is another session's recent file-tool write (fail open).
- A record can be lost to a concurrent trim (fail closed: the path may block).

**Tech Stack:** bash 3.2 (macOS `/bin/bash`; `hooks.json` runs `bash`), jq, sqlite3, git, BSD awk/sed.

**Execution invariants:**
- Shell state does not persist between steps, so every step uses absolute paths. The repo root is `/Users/roberthyatt/Code/ironclaude`; git runs as `git -C /Users/roberthyatt/Code/ironclaude`.
- zsh has `nomatch`, so quote globs passed to `find`/`rg`. No foreground `sleep`.
- Each run writes its own log under `/private/tmp/claude-502/dh/`. Evidence commands never use `2>/dev/null`.
- Hook tests run as `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/<file>.sh`. They must never write to the real `~/.claude`: every hook invocation gets a temporary `HOME`, as the existing drift tests do.
- Never edit `~/.claude/ironclaude-hooks-config.json` or `~/.codex/config.toml`. Task 1 only reads `config.toml`.
- `worker/hooks/hooks.json` stays byte-identical, so Codex sees no hook-list change.
- `docs/` is gitignored, so `docs/plans` files are staged with `git add -f`.
- `worker/.codex-plugin/plugin.json` is modified and unstaged at plan time. It is not part of this plan; never stage it.
- Fail-open under errexit: `run_hook` sets `set -euo pipefail` and an ERR trap in both hooks. The recorder runs only inside `( … ) || :` or a child `bash`. Every new `grep`/`awk` in the drift library ends in `|| true` or sits in an `if`. A recorder failure must never drop the guard's `updatedInput` JSON.
- RED output uses the drift-test style `FAIL  <label>` (two spaces), and a pass-count mismatch makes rc=1.
- Deploy is out of plan: the operator commits, then runs `make deploy-hooks` (design R6). No push.

---

## Task 1: Codex hook-trust measurement and baselines (no source change)

No tests required: these are measurements only. **Files:** Create `docs/plans/2026-10-17-drift-cross-session-hotfix-findings.md`.

**Step 1: Scratch set-up and trust probe**

```bash
mkdir -p /private/tmp/claude-502/dh && cat > /private/tmp/claude-502/dh/trust-probe.sh <<'SH'
#!/bin/bash
# Which input does each Codex hook trusted_hash cover? Read-only: never writes config.toml.
set -u
REPO=/Users/roberthyatt/Code/ironclaude
H="$REPO/worker/hooks/hooks.json"
CFG="$HOME/.codex/config.toml"
CACHE_ROOT="$HOME/.codex/plugins/cache/ironclaude/ironclaude"
NEWEST=$(ls -t "$CACHE_ROOT" | head -n 1)
CACHE="$CACHE_ROOT/$NEWEST/hooks"
STABLE="$HOME/.claude/ironclaude-hooks"
sha() { shasum -a 256 | cut -d' ' -f1; }
echo "cache_version=$NEWEST"
if diff -q "$H" "$CACHE/hooks.json" >/dev/null; then echo "hooks_json_repo_vs_cache=identical"; else echo "hooks_json_repo_vs_cache=DIFFERENT"; fi
for spec in pre_tool_use:PreToolUse:3 pre_tool_use:PreToolUse:6 post_tool_use:PostToolUse:6 subagent_stop:SubagentStop:1; do
  key="${spec%%:*}"; rest="${spec#*:}"; ev="${rest%%:*}"; ix="${rest#*:}"
  trusted=$(grep -A1 -F "[hooks.state.\"ironclaude@ironclaude:hooks/hooks.json:$key:$ix:0\"]" "$CFG" | sed -n 's/^trusted_hash = "sha256:\(.*\)"$/\1/p')
  cmd=$(jq -r --arg e "$ev" --argjson i "$ix" '.hooks[$e][$i].hooks[0].command' "$H")
  script="${cmd##*/}"; script="${script%% *}"
  echo "== $key:$ix:0 command=$cmd"
  echo "trusted=$trusted"
  cand() { if [ "$2" = "$trusted" ]; then echo "$1=$2 MATCH"; else echo "$1=$2"; fi; }
  cand script_repo "$(sha < "$REPO/worker/hooks/$script")"
  cand script_stable "$(sha < "$STABLE/$script")"
  cand script_cache "$(sha < "$CACHE/$script")"
  cand cmd_raw "$(printf '%s' "$cmd" | sha)"
  cand cmd_lf "$(printf '%s\n' "$cmd" | sha)"
  cand cmd_home "$(printf '%s' "${cmd//\$HOME/$HOME}" | sha)"
  cand handler_json "$(jq -cj --arg e "$ev" --argjson i "$ix" '.hooks[$e][$i].hooks[0]' "$H" | sha)"
  cand group_json "$(jq -cj --arg e "$ev" --argjson i "$ix" '.hooks[$e][$i]' "$H" | sha)"
done
SH
ls /private/tmp/claude-502/dh
```

Expected: `ls` lists `trust-probe.sh`.

**Step 2: Trusted hashes and group indexes**

```bash
grep -n -A1 -F -e 'hooks/hooks.json:pre_tool_use:3:0"]' -e 'hooks/hooks.json:pre_tool_use:6:0"]' -e 'hooks/hooks.json:post_tool_use:6:0"]' -e 'hooks/hooks.json:subagent_stop:1:0"]' /Users/roberthyatt/.codex/config.toml > /private/tmp/claude-502/dh/t1-trusted.log 2>&1; echo "grep=$?"; cat /private/tmp/claude-502/dh/t1-trusted.log; jq -r '.hooks | to_entries[] | select(.key == "PreToolUse" or .key == "PostToolUse" or .key == "SubagentStop") | .key as $e | .value | to_entries[] | "\($e) \(.key) \(.value.hooks[0].command)"' /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json > /private/tmp/claude-502/dh/t1-groups.log 2>&1; echo "jq=$?"; rg -n -e 'professional-mode-guard' -e 'execution-drift-check' /private/tmp/claude-502/dh/t1-groups.log
```

Expected:
- `grep=0`, with four header lines each followed by a `trusted_hash` line. The plan-time values are:
  - `pre_tool_use:3:0` 49ec3280…;
  - `pre_tool_use:6:0` 747d3b90…;
  - `post_tool_use:6:0` 27182980…;
  - `subagent_stop:1:0` 9a32b1a1….
- `jq=0`, and rg prints exactly 4 lines:
  - `PreToolUse 3 … professional-mode-guard.sh`;
  - `PreToolUse 6 … execution-drift-check.sh`;
  - `PostToolUse 6 … execution-drift-check.sh`;
  - `SubagentStop 1 … execution-drift-check.sh`.

If an index differs, STOP and report: the probe's group list would be wrong.

**Step 3: Which input the trust hash covers**

```bash
bash /private/tmp/claude-502/dh/trust-probe.sh > /private/tmp/claude-502/dh/t1-probe.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/dh/t1-probe.log
```

Expected:
- `probe=0` and a `cache_version=` line.
- `hooks_json_repo_vs_cache=` printed.
- 4 sections. Each has `trusted=` equal to its Step 2 value and 8 candidate lines.

Record, per section, which candidate line ends in `MATCH`, or `none`. No STOP rule. Interpretation for the findings:
- A `script_*` MATCH means the hash covers script bytes, so this hotfix's edits to the guard and the drift check make Codex re-prompt after deploy.
- A `cmd_*`, `handler_json` or `group_json` MATCH means script edits do not change trust.
- `none` everywhere means undetermined; the owed post-deploy check (Task 5) settles it.

**Step 4: Baselines (staged set, hooks suite)**

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status > /private/tmp/claude-502/dh/t1-staged.log 2>&1; echo "staged=$?"; cat /private/tmp/claude-502/dh/t1-staged.log; for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/test-guard-security.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh; do bash "$t" > "/private/tmp/claude-502/dh/t1-hooks-$(basename "$t").log" 2>&1; rc=$?; echo "$(basename "$t") rc=$rc"; done
```

Expected:
- `staged=0`. Record the staged list as the pre-plan staged set; it may be empty.
- 45 `rc=` lines: 42 `tests/test-*.sh` plus the 3 named files. Record every `rc≠0` file as the baseline failing set; it is expected to be empty.

**Step 5: Record and stage**

Write the findings section "Task 1 (before change)":
- the Step 2 and Step 3 logs verbatim;
- the per-section MATCH result and its interpretation;
- the Step 4 staged set and baseline failing set.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-drift-cross-session-hotfix-findings.md
```

Expected: no output.

---

## Task 2: Writer log recorded by the guard (R1, R5) (depends on 1)

**Files:**
- Create `worker/hooks/tests/test-execution-drift-writers.sh`.
- Modify `worker/hooks/execution-drift-check.sh`: the header Library lines 12-14, plus new functions after `_drift_in_progress_rows` (line 256).
- Modify `worker/hooks/professional-mode-guard.sh`: a new function after `has_command_input_key` (ends line 106).
- Modify `worker/hooks/workspace-path-adapter.sh`: `workspace_allow`, lines 539-545.

**Step 1: Writer-log tests (RED)**

Create `worker/hooks/tests/test-execution-drift-writers.sh`:

```bash
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
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-writers.sh > /private/tmp/claude-502/dh/t2-red.log 2>&1; echo "rc=$?"; rg -n -e '^FAIL' -e '^Results' /private/tmp/claude-502/dh/t2-red.log
```

Expected: `rc=1`, and `Results: 3 pass, 7 fail`. FAIL is exactly these 7:
- `W1 a Claude Write allowed by the guard records its writer`
- `W2 a Codex apply_patch records every target`
- `W3 Claude and Codex records share one form`
- `W5 a relative target records its physical absolute path`
- `W6 the log keeps every record up to its cap (default cap 10000)`
- `W7 past its cap the log keeps its newest half`
- `W8 an allowed executing Write records its writer`

W4, W9 and W10 pass already. They are controls that fail if the recorder ever blocks, or records before the block decision or for a read.

**Step 2: Recorder, guard wiring, allow hook (GREEN)**

In `execution-drift-check.sh`, replace the header lines

```
#   Library      IC_DRIFT_LIB=1 source      -> _drift_effective_root and
#                                              ic_drift_stop_violations, used by
#                                              get-back-to-work-impl.sh at Stop
```

with

```
#   Library      IC_DRIFT_LIB=1 source      -> _drift_effective_root and
#                                              ic_drift_stop_violations, used by
#                                              get-back-to-work-impl.sh at Stop;
#                                              ic_drift_record_writes, used by
#                                              professional-mode-guard.sh (writer log)
```

Directly after the `_drift_in_progress_rows` function (its closing `}` at line 256), insert:

```bash

# Writer log (2026-10-17 cross-session hotfix R1). professional-mode-guard.sh appends
# "epoch<TAB>session<TAB>path" for every file-tool write it allows (Claude Edit, Write,
# MultiEdit, NotebookEdit; Codex apply_patch, every target), so ic_drift_violations can
# tell another session's file from this session's drift.
_DRIFT_WRITERS_MAX_DEFAULT=10000
_drift_writers_log() { printf '%s' "${IC_DRIFT_WRITERS_LOG:-$HOME/.claude/ironclaude-drift-writers.tsv}"; }

# _drift_physical_path PATH BASE -> PATH made absolute against BASE, with its longest
# existing ancestor resolved physically (PreToolUse runs before Write creates parents).
_drift_physical_path() {
  local p="$1" dir suffix phys
  case "$p" in /*) ;; *) p="$2/$p" ;; esac
  suffix=$(basename -- "$p")
  dir=$(dirname -- "$p")
  while ! phys=$(cd -P -- "$dir" 2>/dev/null && pwd -P); do
    [ "$dir" = "/" ] && return 1
    suffix="$(basename -- "$dir")/$suffix"
    dir=$(dirname -- "$dir")
  done
  if [ "$phys" = "/" ]; then printf '/%s\n' "$suffix"; else printf '%s/%s\n' "$phys" "$suffix"; fi
}

# ic_drift_record_writes SESSION BASE: append one record per target path on stdin (one
# per line; relative paths resolve against BASE). Fail-open: it never prints, never
# fails, and skips a path holding a tab or CR. Past IC_DRIFT_WRITERS_MAX_LINES lines
# (default _DRIFT_WRITERS_MAX_DEFAULT) the log keeps its newest half. A concurrent trim
# can lose a record; that fails closed (the path may block again).
ic_drift_record_writes() {
  (
    set +eu
    session=$(_drift_safe "$1")
    [ -n "$session" ] || exit 0
    log=$(_drift_writers_log)
    mkdir -p "$(dirname -- "$log")" || exit 0
    now=$(date +%s)
    while IFS= read -r target; do
      [ -n "$target" ] || continue
      case "$target" in *$'\t'*|*$'\r'*) continue ;; esac
      abs=$(_drift_physical_path "$target" "$2") || continue
      printf '%s\t%s\t%s\n' "$now" "$session" "$abs" >> "$log" || exit 0
    done
    max="${IC_DRIFT_WRITERS_MAX_LINES:-$_DRIFT_WRITERS_MAX_DEFAULT}"
    [[ "$max" =~ ^[1-9][0-9]*$ ]] || max="$_DRIFT_WRITERS_MAX_DEFAULT"
    lines=$(wc -l < "$log" | tr -d ' ')
    if [ "${lines:-0}" -gt "$max" ]; then
      tmp="$log.tmp.$$"
      if tail -n "$((max / 2))" "$log" > "$tmp"; then mv -f "$tmp" "$log"; else rm -f "$tmp"; fi
    fi
  ) >/dev/null 2>&1
  return 0
}
```

In `professional-mode-guard.sh`, directly after the closing `}` of `has_command_input_key` (line 106), insert:

```bash

# Drift writer log (2026-10-17 cross-session hotfix R1): workspace_allow calls this, in a
# subshell, for every decision this guard allows. A file-tool write records its writer
# so execution-drift-check.sh never charges another session's file to this session. The
# ORIGINAL tool name decides (the adapter renames apply_patch to MultiEdit). Targets:
# the adapter's resolved WORKSPACE_TARGET_FILES when set, else file_path/notebook_path,
# else every Add/Update/Delete/Move target of a Codex patch (relative to the cwd). The
# drift library is sourced in a child bash, so its sourcing never resets WORKSPACE_*.
ic_guard_record_writer() {
  local original targets
  original=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || return 0
  targets="${WORKSPACE_TARGET_FILES:-}"
  case "$original" in
    Edit|Write|MultiEdit|NotebookEdit)
      [ -n "$targets" ] || targets=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null) || return 0
      ;;
    apply_patch)
      [ -n "$targets" ] || targets=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null \
        | sed -n -E -e 's/^\*\*\* (Add|Update|Delete) File: //p' -e 's/^\*\*\* Move to: //p') || return 0
      ;;
    *) return 0 ;;
  esac
  [ -n "$targets" ] || return 0
  printf '%s\n' "$targets" | IC_DRIFT_LIB=1 bash -c 'source "$1/execution-drift-check.sh" && ic_drift_record_writes "$2" "$3"' \
    _ "$SCRIPT_DIR" "$SESSION_TAG" "$PROJECT_ROOT"
}
```

In `workspace-path-adapter.sh`, replace `workspace_allow` (lines 539-545) with:

```bash
workspace_allow() {
  # professional-mode-guard.sh records the writer of an allowed file-tool write (drift
  # writer log). Isolated: it never prints, never fails the allow, never drops the
  # updatedInput below.
  if type ic_guard_record_writer >/dev/null 2>&1; then
    ( ic_guard_record_writer ) >/dev/null 2>&1 || :
  fi
  if [ -n "$WORKSPACE_UPDATED_INPUT" ]; then
    jq -cn --argjson updated "$WORKSPACE_UPDATED_INPUT" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:$updated}}'
  fi
  exit 0
}
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-writers.sh > /private/tmp/claude-502/dh/t2-green.log 2>&1; echo "writers=$?"; rg -n '^FAIL' /private/tmp/claude-502/dh/t2-green.log; tail -n 1 /private/tmp/claude-502/dh/t2-green.log; for t in test-managed-worktree-guard.sh test-pmguard-client-aware.sh test-pmguard-enterplanmode.sh test-plan-task-context-apply-patch.sh test-professional-mode-docs-nested-repo.sh test-execution-drift-check.sh test-execution-drift-identity.sh test-execution-drift-roots.sh; do bash "/Users/roberthyatt/Code/ironclaude/worker/hooks/tests/$t" > "/private/tmp/claude-502/dh/t2-$t.log" 2>&1; rc=$?; echo "$t rc=$rc"; done; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-guard-security.sh > /private/tmp/claude-502/dh/t2-test-guard-security.sh.log 2>&1; echo "test-guard-security.sh rc=$?"
```

Expected:
- `writers=0`, no FAIL lines, and `Results: 10 pass, 0 fail`.
- Each of the 9 other files has the same rc as in the Task 1 Step 4 baseline (rc=0 there means rc=0 here).
- A new failure in `test-managed-worktree-guard.sh` means the recorder disturbed the `updatedInput` JSON: STOP and report.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/professional-mode-guard.sh worker/hooks/workspace-path-adapter.sh worker/hooks/tests/test-execution-drift-writers.sh
```

Expected: no output.

---

## Task 3: Exclude other sessions' files; block text (R2, R3, R4, R5) (depends on 2)

**Files:**
- Modify `worker/hooks/execution-drift-check.sh`:
  - header lines 20-34;
  - `_drift_close_window_file` (lines 293-297);
  - `ic_drift_violations` (lines 335-356);
  - `ic_drift_block_message` (line 379);
  - a new `_drift_foreign_paths` directly before `ic_drift_violations`.

  Line numbers are pre-Task-2; locate by the quoted text.
- Modify `worker/hooks/tests/test-execution-drift-check.sh`: line 26, plus new cases before the final `printf 'Results: …'` (line 506).
- Modify `worker/hooks/tests/test-execution-drift-roots.sh`: case 5 (lines 142-143). The new foreign-path subtraction adds a second `LC_ALL=C comm -23`, so the old exact count of one no longer states the check's intent.

**Step 1: Tests (RED)**

Change `EXPECTED_PASSES=41` to `EXPECTED_PASSES=52`. Directly before the line `printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"`, insert:

```bash
# 42-52. Cross-session exclusion (2026-10-17 hotfix R2-R4). Another session's recent
#        file-tool write (writer log) or a live session's same-root ledger path, charged
#        to this session's window, never blocks; this session's own shell side quest,
#        a finished session's ledger, an old record and a record elsewhere still block.
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

```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/dh/t3-red.log 2>&1; echo "rc=$?"; rg -n -e '^FAIL' -e '^Results' /private/tmp/claude-502/dh/t3-red.log
```

Expected: `rc=1`, and `Results: 47 pass, 5 fail`. FAIL is exactly these 5:
- `43 block text keeps the side-quest and amend guidance without the wait line`
- `44 another session's Write during this session's window does not block`
- `45 another session's apply_patch during this session's window does not block`
- `46 a path in another session's same-root ledger does not block`
- `49 a ledger records its root beside it`

Cases 42, 47, 48, 50, 51 and 52 pass already. They are the positive control and the bounds on the exclusion: another root, a newer own record, a finished session, an old record, and a record elsewhere.

**Step 2: Exclusion, ledger root, block text, header (GREEN)**

In the header, directly after the line `#         Exact string match only. A ledger path later committed or reverted drops out.`, insert:

```
# Cross-session exclusion (2026-10-17 hotfix R2): before reporting, drift drops a path
#   (a) whose newest writer-log record (ic_drift_record_writes, fed by
#   professional-mode-guard.sh for every allowed file-tool write) is another session's,
#   with an epoch at or after the earliest created_at of the checked rows (no created_at:
#   (a) drops nothing), or (b) that a live session's same-root ledger lists: the ledger's
#   <ledger>.root equals this root (a ledger without one is ignored) and its wave_tasks
#   row belongs to a session in executing or reviewing (professional_mode not checked).
# Accepted residuals (R3): another live session's shell write during an overlapping window
#   lands in both ledgers and is excluded for both (fail open); a session left in
#   executing/reviewing keeps its ledgers live until its next create_plan or reset; a
#   finished session's shell write during an overlapping window blocks (pre-fix behavior);
#   this session's shell rewrite of a file whose newest record is another session's recent
#   write (fail open); a record lost to a concurrent trim (the path may block).
```

In `_drift_close_window_file`, change `local file="$1" session="$2" root before now delta row status content` to `local file="$1" session="$2" root before now delta row status content ledger`, and replace

```bash
    for row in $(_drift_in_progress_rows "$session"); do
      printf '%s\n' "$delta" >> "$(_drift_ledger_file "$session" "$row")"
    done
```

with

```bash
    for row in $(_drift_in_progress_rows "$session"); do
      ledger=$(_drift_ledger_file "$session" "$row")
      printf '%s\n' "$delta" >> "$ledger"
      # The root beside the ledger lets another session match only same-root paths (R2b).
      printf '%s\n' "$root" > "${ledger%.txt}.root"
    done
```

Replace the comment block and body of `ic_drift_violations` (from `# ic_drift_violations SESSION ROOT ROW_ID...` through its closing `}`) with:

```bash
# _drift_foreign_paths SESSION ROOT CANDIDATES ROW_ID... -> the CANDIDATES (root-relative,
# one per line) that another live session wrote (R2):
#   (a) the newest writer-log record for the file is another session's, with an epoch at
#       or after "since", the earliest created_at of SESSION's ROW_IDs (no since: none);
#   (b) another session's ledger <other>.<row>.txt lists it, its <ledger>.root equals
#       ROOT, and wave_tasks row <row> belongs to <other> while <other> is executing or
#       reviewing (professional_mode is not checked).
# Writer records match by identity (_drift_rel_by_identity), so case and symlink
# spellings agree and a record outside ROOT never matches. Prints nothing on any error
# (no exclusion); always status 0.
_drift_foreign_paths() {
  local session="$1" root="$2" candidates="$3" safe log rows since entry rel f name other row live
  shift 3
  safe=$(_drift_safe "$session")
  [ -n "$candidates" ] || return 0
  rows=$(printf '%s,' "$@")
  rows="${rows%,}"
  since=""
  if [[ "$rows" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    since=$(_drift_sql "SELECT MIN(CAST(strftime('%s',created_at) AS INTEGER)) FROM wave_tasks WHERE terminal_session='$(_drift_q "$session")' AND id IN ($rows);")
  fi
  log=$(_drift_writers_log)
  if [[ "$since" =~ ^[0-9]+$ ]] && [ -f "$log" ]; then
    awk -F '\t' 'NF == 3 { s[$3] = $2; t[$3] = $1 } END { for (p in s) printf "%s\t%s\t%s\n", s[p], t[p], p }' "$log" 2>/dev/null \
      | DRIFT_NAMES="$(printf '%s\n' "$candidates" | sed 's#.*/##')" awk -F '\t' -v me="$safe" -v since="$since" '
          BEGIN { n = split(ENVIRON["DRIFT_NAMES"], a, "\n"); for (i = 1; i <= n; i++) if (a[i] != "") want[a[i]] = 1 }
          $1 != me && ($2 + 0) >= (since + 0) { b = $3; sub(/.*\//, "", b); if (b in want) print $3 }' 2>/dev/null \
      | while IFS= read -r entry; do
          rel=$(_drift_rel_by_identity "$entry" "$root") || continue
          if grep -qxF -- "$rel" <<< "$candidates"; then printf '%s\n' "$rel"; fi
        done || true
  fi
  for f in "$HOME/.claude/ironclaude-drift-touched-"*.txt; do
    [ -f "$f" ] || continue
    name=${f##*-drift-touched-}; name=${name%.txt}; other=${name%.*}; row=${name##*.}
    [[ $row =~ ^[0-9]+$ ]] || continue
    [ "$other" != "$safe" ] || continue
    [ "$(cat "${f%.txt}.root" 2>/dev/null)" = "$root" ] || continue
    live=$(_drift_sql "SELECT 1 FROM wave_tasks w JOIN sessions s ON s.terminal_session=w.terminal_session WHERE w.id=$row AND w.terminal_session='$(_drift_q "$other")' AND s.workflow_stage IN ('executing','reviewing') LIMIT 1;")
    [ "$live" = "1" ] || continue
    printf '%s\n' "$candidates" | awk 'NR == FNR { if ($0 != "") want[$0] = 1; next } ($0 in want)' "$f" - 2>/dev/null || true
  done
  return 0
}

# ic_drift_violations SESSION ROOT ROW_ID...  (ROOT from _drift_effective_root)
# Prints drift paths (possibly none) with status 0; status 1 means fail-open;
# status 2 means an unsupported current Git pathname. A row with no ledger adds
# nothing: no command of this session changed a path while it was in progress.
# Paths another live session wrote are dropped (_drift_foreign_paths, R2).
ic_drift_violations() {
  local session="$1" root="$2" allowed touched="" row file current drift foreign
  shift 2
  [ -n "$root" ] || return 1
  if current=$(_drift_dirty_paths "$root"); then :; else return $?; fi
  allowed=$(_drift_allowed_files "$session" "$root") || return 1
  for row in "$@"; do
    file=$(_drift_ledger_file "$session" "$row")
    if [ -f "$file" ]; then touched="${touched}$(cat "$file")"$'\n'; fi
  done
  drift=$(LC_ALL=C comm -23 \
    <(LC_ALL=C comm -12 \
        <(printf '%s\n' "$current" | sed '/^$/d' | LC_ALL=C sort -u) \
        <(printf '%s\n' "$touched" | sed '/^$/d' | LC_ALL=C sort -u)) \
    <(printf '%s\n' "$allowed" | sed '/^$/d' | LC_ALL=C sort -u)) || return 1
  [ -n "$drift" ] || return 0
  foreign=$(_drift_foreign_paths "$session" "$root" "$drift" "$@")
  LC_ALL=C comm -23 \
    <(printf '%s\n' "$drift") \
    <(printf '%s\n' "$foreign" | sed '/^$/d' | LC_ALL=C sort -u) || return 1
  return 0
}
```

In `ic_drift_block_message`, replace the last `printf` argument (the line starting `"If you made the change as a side quest`) with:

```bash
    "If you made the change as a side quest, undo your own change. If the plan needs the file, retreat and amend the plan so it is listed in allowed_files. Files another live session recently wrote with a file tool, or lists in its own drift ledger, are already left out of this list. If a listed file is still not yours, never delete, revert or edit a file you did not change: report the block to the operator and stop."
```

In `worker/hooks/tests/test-execution-drift-roots.sh`, replace case 5 (the comment line `# 5. F3 static: comm is invoked under LC_ALL=C.` and the line after it) with:

```bash
# 5. F3 static: every comm invocation runs under LC_ALL=C, and there is at least one.
N_COMM=$(grep -c -E '(^|[^[:alnum:]_])comm -' "$HOOK")
N_COMM_C=$(grep -c -F 'LC_ALL=C comm -' "$HOOK")
[ "$N_COMM" -ge 1 ] && [ "$N_COMM" = "$N_COMM_C" ] && ok "5 comm runs under LC_ALL=C" || bad "5 comm runs under LC_ALL=C (comm=$N_COMM under_C=$N_COMM_C)"
```

After the GREEN edits the hook has 4 `comm` lines (357, 465, 466, 472 at plan time), all under `LC_ALL=C`, so N_COMM=4 and N_COMM_C=4. The check fails if any `comm` call loses `LC_ALL=C` (N_COMM_C < N_COMM) or if no `comm` call remains.

```bash
for t in test-execution-drift-check.sh test-execution-drift-identity.sh test-execution-drift-roots.sh test-execution-drift-writers.sh; do bash "/Users/roberthyatt/Code/ironclaude/worker/hooks/tests/$t" > "/private/tmp/claude-502/dh/t3-$t.log" 2>&1; rc=$?; echo "$t rc=$rc"; rg -n '^FAIL' "/private/tmp/claude-502/dh/t3-$t.log"; tail -n 1 "/private/tmp/claude-502/dh/t3-$t.log"; done
```

Expected: all 4 have rc=0 and no FAIL lines:
- `test-execution-drift-check.sh`: `Results: 52 pass, 0 fail`.
- `test-execution-drift-identity.sh`: `Results: 13 pass, 0 fail`, or 12 on a case-sensitive volume, where its `EXPECTED_PASSES` is 12.
- `test-execution-drift-roots.sh`: `Results: 8 pass, 0 fail`.
- `test-execution-drift-writers.sh`: `Results: 10 pass, 0 fail`.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh worker/hooks/tests/test-execution-drift-roots.sh
```

Expected: no output.

---

## Task 4: Codex apply_patch and exec_command gated in a primary checkout (R7) (depends on 3)

**Source trace (the bypass).** With no managed-worktree assignment, `workspace_prepare_input` returns at `workspace-path-adapter.sh:464` (no workspace DB) or `:472` (no row). That is before its `command)` arm (:519, `WORKSPACE_GUARD_TOOL_NAME='Bash'`) and its `patch)` arm (:529, `'MultiEdit'`). The guard re-reads `TOOL_NAME` at :440, and :442 changes nothing, so the name stays `apply_patch` or `exec_command`.

Neither name is in any write gate:
- :505 (workflow read) and :623 (stage/access) list Edit/Write/MultiEdit/Bash/NotebookEdit;
- :550 (docs) and :752 (allowed_files) list the file tools;
- :813 (git commit/push) and :833 (review_pending) list `Bash` or the same set.

Both names fall through to "tool not handled" (:879) and are allowed in any stage, outside allowed_files. **exec_command has the same gap**, so it is fixed here too, mapped to `Bash`.

**Fix.** Directly after `FILE_PATH=$(normalize_path "$FILE_PATH")` (guard line 444), map the native names whether or not an assignment exists:
- An `apply_patch` with no adapter targets has every target resolved by the adapter's own `workspace_rewrite_patch` against `PROJECT_ROOT`, the physical cwd. This only sets `WORKSPACE_TARGET_FILES`; the patch is not rewritten and no `updatedInput` is emitted.
- A malformed patch, or a target that escapes the cwd (`..`, a symlinked directory, an absolute path outside it), is blocked.

The docs gate (:550) and allowed_files loop (:752) already iterate `WORKSPACE_TARGET_FILES`, so every target is checked. The Task 2 recorder reads the same `WORKSPACE_TARGET_FILES`, so it records exactly the targets that were checked.

**Files:**
- Modify `worker/hooks/professional-mode-guard.sh`: insert after line 444 (pre-Task-2 numbering; locate by text).
- Create `worker/hooks/tests/test-pmguard-codex-primary.sh`.

**Step 1: Tests (RED)**

Create `worker/hooks/tests/test-pmguard-codex-primary.sh`:

```bash
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
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-pmguard-codex-primary.sh > /private/tmp/claude-502/dh/t4-red.log 2>&1; echo "rc=$?"; rg -n -e '^FAIL' -e '^Results' /private/tmp/claude-502/dh/t4-red.log
```

Expected: `rc=1`, and `Results: 3 pass, 4 fail`. FAIL is exactly these 4:
- `C1 brainstorming apply_patch of a source file is blocked`
- `C2 executing apply_patch with a second target outside allowed_files is blocked`
- `C4 brainstorming mutating exec_command is blocked`
- `C7 the writer log records every target of an allowed patch` (counts=21)

C1, C2 and C4 are allowed today through :879. C7 fails in RED because the C2 patch, allowed via :879, also recorded `src/allowed.txt`. In GREEN, C2 is blocked before `workspace_allow`, so only C6 records. C3, C5 and C6 are positive controls and pass.

**Step 2: Map the native Codex names (GREEN)**

In `professional-mode-guard.sh`, directly after the line `FILE_PATH=$(normalize_path "$FILE_PATH")` that follows `if [ -n "$WORKSPACE_GUARD_FILE_PATH" ]; then FILE_PATH="$WORKSPACE_GUARD_FILE_PATH"; fi`, insert:

```bash

# 2026-10-17 drift hotfix R7: with no managed-worktree assignment the adapter returns before
# it maps native Codex names, and every write gate below matches only Claude names, so
# apply_patch and exec_command fell through to "tool not handled" (allowed in any stage,
# outside allowed_files). Map them here whether or not an assignment exists. A patch with no
# adapter targets has EVERY target resolved by the adapter's own parser against the cwd;
# only WORKSPACE_TARGET_FILES is set, the patch is not rewritten (no updatedInput).
if [ "$TOOL_NAME" = "exec_command" ]; then
  TOOL_NAME="Bash"
elif [ "$TOOL_NAME" = "apply_patch" ]; then
  TOOL_NAME="MultiEdit"
  if [ -z "$WORKSPACE_TARGET_FILES" ]; then
    _R7_PATCH=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
    if ! workspace_rewrite_patch "$_R7_PATCH" "$PROJECT_ROOT"; then
      block_pretooluse "professional-mode-guard" "BLOCKED — UNCHECKABLE CODEX PATCH

The apply_patch input is malformed, or a target leaves the working directory (${PROJECT_ROOT}) through '..', a symlinked directory, or an absolute path outside it. The guard cannot check its targets against the workflow stage and allowed_files.

Use paths relative to the working directory, or absolute paths inside it."
    fi
  fi
  FILE_PATH=$(printf '%s\n' "$WORKSPACE_TARGET_FILES" | sed -n '1p')
fi
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-pmguard-codex-primary.sh > /private/tmp/claude-502/dh/t4-green.log 2>&1; echo "codex_primary=$?"; rg -n '^FAIL' /private/tmp/claude-502/dh/t4-green.log; tail -n 1 /private/tmp/claude-502/dh/t4-green.log; for t in test-managed-worktree-guard.sh test-pmguard-client-aware.sh test-pmguard-enterplanmode.sh test-plan-task-context-apply-patch.sh test-professional-mode-docs-nested-repo.sh test-execution-drift-check.sh test-execution-drift-writers.sh; do bash "/Users/roberthyatt/Code/ironclaude/worker/hooks/tests/$t" > "/private/tmp/claude-502/dh/t4-$t.log" 2>&1; rc=$?; echo "$t rc=$rc"; done; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-guard-security.sh > /private/tmp/claude-502/dh/t4-test-guard-security.sh.log 2>&1; echo "test-guard-security.sh rc=$?"
```

Expected:
- `codex_primary=0`, no FAIL lines, and `Results: 7 pass, 0 fail`.
- Each of the 8 other files has its Task 1 Step 4 baseline rc. `test-execution-drift-writers.sh`, which is new, has rc=0.
- A new `test-managed-worktree-guard.sh` or `test-guard-security.sh` failure means the mapping changed the assignment or undecided paths: STOP and report.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/professional-mode-guard.sh worker/hooks/tests/test-pmguard-codex-primary.sh
```

Expected: no output.

---

## Task 5: Full hooks suite, invariants, findings and staging (depends on 4)

**Files:** Modify `docs/plans/2026-10-17-drift-cross-session-hotfix-findings.md`. Stage the design, this plan, its `.plan.json` and the findings.

**Step 1: Full hooks suite**

```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/test-guard-security.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh; do bash "$t" > "/private/tmp/claude-502/dh/t5-hooks-$(basename "$t").log" 2>&1; rc=$?; echo "$(basename "$t") rc=$rc"; done
```

Expected:
- 47 `rc=` lines: the 45 Task 1 Step 4 files plus `test-execution-drift-writers.sh` and `test-pmguard-codex-primary.sh`.
- All are rc=0, or the non-zero set is exactly the Task 1 Step 4 baseline failing set.

**Step 2: hooks.json unchanged; the real ~/.claude untouched**

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --exit-code HEAD -- worker/hooks/hooks.json; echo "hooks_json=$?"; ls -la /Users/roberthyatt/.claude/ironclaude-drift-writers.tsv; echo "real_log=$?"; find /Users/roberthyatt/.claude -maxdepth 1 -name 'ironclaude-drift-touched-ic-*'; echo "find=$?"
```

Expected:
- `hooks_json=0` with no diff output.
- `ls` reports `No such file or directory` with `real_log=1`. The deployed hooks predate this change, so only a test leak could create the file.
- `find` prints nothing, with `find=0`.

If the real log exists, STOP and report the test that leaked it.

**Step 3: Findings and staging**

Append "After change" to the findings:
- the Task 2, 3 and 4 RED and GREEN summaries;
- the Step 1 rc list beside the Task 1 baseline, and the Step 2 output;
- an "Owed after deploy (operator-assisted)" section with this procedure, to run after the operator commits and runs `make deploy-hooks`:
  1. Open a new Codex session in `/Users/roberthyatt/Code/roleplaying-agents`. Record whether a "Hooks need review" prompt appears, and set that against the Task 1 Step 3 MATCH result.
  2. In any session, make one allowed file-tool write. Confirm that `~/.claude/ironclaude-drift-writers.tsv` gains one `epoch<TAB>session<TAB>path` line.
  3. Report both results to the operator.

Also record the R7 fix (Task 4). Codex `apply_patch` and `exec_command` with no managed-worktree assignment used to skip the stage gate and the allowed_files check (`professional-mode-guard.sh:879`, "tool not handled"). Note the behaviour change this causes in a primary checkout:
- Codex mutating shell commands are now blocked outside executing, as Bash is.
- A patch target that is an absolute path outside the cwd is now blocked.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-drift-cross-session-hotfix-design.md docs/plans/2026-10-17-drift-cross-session-hotfix.md docs/plans/2026-10-17-drift-cross-session-hotfix.plan.json docs/plans/2026-10-17-drift-cross-session-hotfix-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status
```

Expected:
- `M` for `worker/hooks/execution-drift-check.sh`, `worker/hooks/professional-mode-guard.sh`, `worker/hooks/workspace-path-adapter.sh`, `worker/hooks/tests/test-execution-drift-check.sh` and `worker/hooks/tests/test-execution-drift-roots.sh`.
- `A` for `worker/hooks/tests/test-execution-drift-writers.sh`, `worker/hooks/tests/test-pmguard-codex-primary.sh` and the 4 `docs/plans/2026-10-17-drift-cross-session-hotfix*` docs.
- Any other entries are exactly the Task 1 Step 4 pre-plan staged set.
- `worker/.codex-plugin/plugin.json` and `worker/hooks/hooks.json` are absent.
