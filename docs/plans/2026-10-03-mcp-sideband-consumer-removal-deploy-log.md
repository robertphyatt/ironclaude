# MCP Sideband Consumer Removal — Verification and Deploy Log (v1.1.16 L2b-1d)

> **Date:** 2026-10-03
> **Plan:** `docs/plans/2026-10-03-mcp-sideband-consumer-removal.md`, Task 2
> **Requirements:** R4 (verify and redeploy), R5 (final Fable gate, then commit)

## Task 1 result

Task 1 removed `surface_mcp_errors` and its call from `state-activator.sh`, and kept `MCP_ERROR_LOG` with an updated comment. The writer in `index.ts` and the truncation in `session-init.sh:406` are unchanged.

- **Review grade:** A.
- **`test-state-activator-single-object.sh`:** RED gave 5 pass, 1 fail (case 5 printed the sideband line). GREEN gave 6 pass, 0 fail.
- **References:** `grep -rn surface_mcp_errors worker` printed nothing and exited 1, so no references remain.

## Step 1: hook suites

`make -C /Users/roberthyatt/Code/ironclaude test-hooks` exited 0. Searching the output for a non-zero fail count matched only a section heading at line 609, which is not a result.

```
--- worker/hooks/tests/test-state-activator-single-object.sh ---
Results: 6 pass, 0 fail
```

## Step 2: Commander suite

```
3457 passed in 420.11s (0:07:00)
exit=0
```

## Step 3: deploy hooks

```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```

`cmp` confirmed that the deployed `state-activator.sh` and `hook-logger.sh` match the repository copies.

## Step 4: Claude plugin hooks.json

The diff printed nothing and exited 0. **hooks.json is unchanged, so the copy was skipped.**

## Step 5: Codex plugin cache

```
"version": "1.1.15+codex.20260930172837",
"installedPath": "/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837"
```

The command exited 0.

## Step 6: Codex cache matches worker/

`diff -rq` printed nothing and exited 0.

## Commit gate and order (R5)

1. **Final Fable end review.** A Fable end review of the full combined staged diff (L2b-1, L2b-1b, L2b-1c and L2b-1d) must come back CLEAN. If it reports material findings, fix them and run the review again.
2. **Commit.** Only then does the operator commit all four with `/commit`, with no trailers and no push.
3. **Re-trust the Codex hooks.** After the commit, the operator re-trusts the four Codex hooks: `pre_tool_use:2`, `pre_tool_use:3`, `pre_tool_use:6` and `post_tool_use:6`. Because `hooks.json` is unchanged, the trust keys are the same as after the L2b-1 deploy.
4. **Restart the clients.** Running clients keep the code they loaded. Claude picks up the change at the next session start. Codex needs a full quit and relaunch.
