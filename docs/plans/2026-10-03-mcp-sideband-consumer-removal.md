# MCP Sideband Consumer Removal Implementation Plan (v1.1.16 L2b-1d)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop delivering MCP sideband errors into any session's context. To do that, remove the hook-side consumer (`surface_mcp_errors`) and its call from `state-activator.sh`, then verify and redeploy.

**Requirements:** `docs/plans/2026-10-03-mcp-sideband-consumer-removal-requirements.md`

**Design:** `docs/plans/2026-10-03-mcp-sideband-consumer-removal-design.md`

**Loop:** full. This loop changes hook code.

**Architecture:**
- **No TypeScript change.** This is a hooks-only removal.
- **Writer kept.** The state-manager writer (`index.ts:31-41`, `:150`) still appends to `~/.claude/ironclaude-errors.log`.
- **Truncation kept.** `session-init.sh:406` still truncates the log at each session start.
- **Result.** The log becomes an operator debug file that nothing delivers to a model.

**Tech Stack:** bash, jq, sqlite3.

## Execution invariants

- Shell state does not persist between steps, so use literal absolute paths.
- Use `git -C /Users/roberthyatt/Code/ironclaude` for every git command.
- zsh has `nomatch`, so quote any glob.
- `docs/` is gitignored, so plan artifacts need `git add -f`.
- An `expected:` marked "predicted" was traced but not measured, because Bash was limited to read-only commands while planning. If the actual result contradicts it, the executor stops and reports rather than adapting.

**Measured at planning time** (2026-10-03, L2b-1c runs):

| Check | Result |
|---|---|
| `test-state-activator-single-object.sh` | 6 pass |
| `test-warning-queue.sh` | 7 pass |
| `test-professional-mode-deactivation.sh` | 28 pass |
| `test-git-authority-activation.sh` | 94 passed |
| Commander suite | 3457 passed (≈ 420 s; pass `timeout` 600000) |
| `deploy-hooks` | 31 hooks |
| `hooks.json` diff | empty |
| Codex cache `diff -rq` | empty |

**Every reference to `surface_mcp_errors`** (from `grep -rn surface_mcp_errors` over `worker/`, `commander/src` and `commander/tests`):
- `worker/hooks/state-activator.sh:9` (comment)
- `worker/hooks/state-activator.sh:67` (the call)
- `worker/hooks/hook-logger.sh:600` (comment)
- `worker/hooks/hook-logger.sh:603` (the definition)

No test calls it.

**Every reference to the log path** (from `grep -rn -e ironclaude-errors -e ERROR_LOG_PATH -e MCP_ERROR_LOG`), all kept:
- `index.ts:31,37` (the writer)
- `hook-logger.sh:32` (the variable)
- `session-init.sh:406` (truncation)

**Final gate (requirement R5).** Not a plan task: it runs after execution completes. A Fable end review of the full combined L2b-1, L2b-1b, L2b-1c and L2b-1d staged diff must come back CLEAN before the operator commits. If it reports material findings, they are fixed and the review runs again.

---

## Task 1: Remove the consumer (R1–R3)

**Files:**
- Modify: `worker/hooks/state-activator.sh:9` (header comment) and `:66-68` (the call and its comment)
- Modify: `worker/hooks/hook-logger.sh:31` (variable comment) and `:600-620` (the function and the blank line after it)
- Modify: `worker/hooks/tests/test-state-activator-single-object.sh:2-5` (header) and `:53-61` (case 5)

**Step 1 (RED): edit the test file `worker/hooks/tests/test-state-activator-single-object.sh`.**

Replace lines 2-5:

```bash
# test-state-activator-single-object.sh - state-activator.sh prints at most one JSON
# object per run, and only as UserPromptSubmit additionalContext: systemMessage texts,
# MCP sideband errors and the warning drain all reach the model on both clients
# (v1.1.16 L2b-1b R4, L2b-1c R2/R3).
```

with:

```bash
# test-state-activator-single-object.sh - state-activator.sh prints at most one JSON
# object per run, and only as UserPromptSubmit additionalContext: systemMessage texts
# and the warning drain reach the model on both clients; the MCP error sideband is
# not delivered (v1.1.16 L2b-1b R4, L2b-1c R2, L2b-1d).
```

Then replace case 5, which is the block from `# 5. R3: an MCP sideband error reaches the context with verbose logging off` through its closing `fi`:

```bash
# 5. R3: an MCP sideband error reaches the context with verbose logging off
#    (no ironclaude-hooks-config.json exists in this HOME).
printf 'MCP-LINE-77 sideband failure\n' > "$TMP_HOME/.claude/ironclaude-errors.log"
MCP_OUT=$(run_prompt "hello")
if printf '%s' "$MCP_OUT" | jq -cse "$CTX_ONLY and (.[0].hookSpecificOutput.additionalContext | contains(\"MCP-LINE-77 sideband failure\"))" >/dev/null 2>&1; then
  ok "5 MCP sideband error reaches context without verbose logging"
else
  bad "5 MCP sideband error reaches context without verbose logging (got: $MCP_OUT)"
fi
```

with:

```bash
# 5. L2b-1d: the MCP error sideband is not delivered. ironclaude-errors.log is global
#    across sessions and every line was already returned to the calling model as its
#    tool result, so a line there produces no output and stays in the file for the
#    operator (session-init.sh truncates it at session start).
printf 'MCP-LINE-77 sideband failure\n' > "$TMP_HOME/.claude/ironclaude-errors.log"
MCP_OUT=$(run_prompt "hello")
if [ -z "$MCP_OUT" ] && grep -qxF "MCP-LINE-77 sideband failure" "$TMP_HOME/.claude/ironclaude-errors.log"; then
  ok "5 MCP sideband is not delivered and the log is left for the operator"
else
  bad "5 MCP sideband is not delivered and the log is left for the operator (got: $MCP_OUT)"
fi
```

**Step 2: run it and see it fail.**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh
```

Expected (predicted): exit 1, `Results: 5 pass, 1 fail`. Only case 5 fails, for two reasons:
- Today `surface_mcp_errors` emits the line, so `MCP_OUT` is not empty.
- It moves the log away, so the grep finds nothing.

**Step 3: edit `worker/hooks/state-activator.sh`.**

Change line 9 from:

```bash
# Several writers below (log_hook, log_error, log_warning, surface_mcp_errors and the
```

to:

```bash
# Several writers below (log_hook, log_error, log_warning and the
```

Delete these two lines and the blank line after them (`:66-68`):

```bash
# Surface any MCP tool errors from the sideband log
surface_mcp_errors

```

**Step 4: edit `worker/hooks/hook-logger.sh`.**

Change line 31 from:

```bash
# Error sideband log (MCP tool errors written here, surfaced by hooks)
```

to:

```bash
# Error sideband log: the state-manager appends MCP tool errors here for the operator to
# read; no hook delivers it to a model (each error is already the tool result).
# session-init.sh truncates it at session start.
```

Then delete lines 600-620. That is the `# surface_mcp_errors - Read and display MCP error sideband log` comment, the whole `surface_mcp_errors()` function through its closing `}`, and the one blank line after it. Keep the `# _hook_error_handler` block, which follows.

**Step 5: confirm no reference remains.**

```bash
grep -rn surface_mcp_errors /Users/roberthyatt/Code/ironclaude/worker
```

Expected: no output and exit 1. Exit 1 here means grep found no match; an error would be exit 2.

**Step 6: rerun the test.**

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

**Step 9: stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/state-activator.sh worker/hooks/hook-logger.sh worker/hooks/tests/test-state-activator-single-object.sh
```

Expected: no output, exit 0.

---

## Task 2: Full verification and redeploy (R4)

**Files:**
- Create: `docs/plans/2026-10-03-mcp-sideband-consumer-removal-deploy-log.md`

**No tests required.** This task runs the existing suites and deploys. `deploy-hooks` and `codex plugin add` write generated runtime copies outside the repo; these are install output, not edits.

**Step 1: run every hook suite.**

```bash
make -C /Users/roberthyatt/Code/ironclaude test-hooks
```

Expected:
- Exit 0.
- The header for `test-state-activator-single-object.sh` is printed with `Results: 6 pass, 0 fail`.
- No non-zero fail count appears.

**Step 2: run the Commander suite, with `timeout` 600000.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: `3457 passed`, exit 0.

**Step 3: deploy the hooks.**

```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```

Expected: the output includes both of these lines:
- `Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks`
- `Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks`

**Step 4: compare the Claude plugin's `hooks.json`.**

```bash
diff /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json
```

Expected: no output, exit 0. Record "hooks.json unchanged — copy skipped".

If there is a difference:
1. Record it.
2. Copy the file: `cp /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json`.
3. Run the diff again.

**Step 5: install into Codex.**

```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: exit 0, and the JSON `installedPath` is `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`.

**Step 6: compare the Codex cache with the repo.**

```bash
diff -rq /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837 -x node_modules -x __pycache__ -x .DS_Store
```

Expected: no output, exit 0.

**Step 7: write the deploy log** at `docs/plans/2026-10-03-mcp-sideband-consumer-removal-deploy-log.md`. It should contain:
- the quoted outputs of Steps 1-6;
- "hooks.json unchanged";
- the R5 note, which sets this order:
  1. a final Fable end review of the full combined L2b-1, L2b-1b, L2b-1c and L2b-1d diff must pass;
  2. then the operator commits all four together;
  3. then the operator re-trusts the four Codex hooks: `pre_tool_use:2`, `pre_tool_use:3`, `pre_tool_use:6` and `post_tool_use:6`.

**Step 8: stage the deploy log.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-mcp-sideband-consumer-removal-deploy-log.md
```

Expected: no output, exit 0.
