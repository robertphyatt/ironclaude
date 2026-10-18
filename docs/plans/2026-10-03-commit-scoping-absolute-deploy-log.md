# Commit Scoping for Absolute allowed_files — Verification and Deploy Log (v1.1.16 L2c)

> **Date:** 2026-10-04
> **Plan:** `docs/plans/2026-10-03-commit-scoping-absolute.md`, Task 2
> **Requirements:** R3 (verify and redeploy), R4 (final Fable gate, then `/commit`)

## Task 1 results (graded B, minor findings only)

**RED run.** `scoped-tree.test.ts` gave:

```
Tests  6 failed | 6 passed (12)
```

**GREEN run** (re-run independently by the orchestrator):

```
Test Files  1 passed (1)
      Tests  12 passed (12)
```

The case-variant test ran rather than being skipped, because APFS is case-insensitive.

**Bundle check.** `grep -c repoRelativeFromAbsolute` on `dist/hook-intent.js`, `dist/cli.js` and `dist/index.js` returned `0` for each file before `npm run build` (exit 1) and `2` for each after it (exit 0).

**Full workspace-manager suite:**

```
Test Files  12 passed (12)
Tests  494 passed | 1 skipped (495)
Errors  1 error
```

The one error is the known `Timeout calling "onTaskUpdate"` RPC timeout from `integration-core.test.ts`, which is already in the backlog. No test failed.

## Step 1: Commander suite

```
3460 passed in 396.00s (0:06:35)
exit=0
```

## Steps 2–3: Codex plugin cache

```
"installedPath": "/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837",
```

`diff -rq` of `worker/` against the Codex cache printed nothing (exit 0).

## Step 4: Claude plugin cache. Correction: the stale copy does have a runtime effect

```
Files /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js and /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/mcp-servers/workspace-manager/dist/index.js differ
```

The plan said this stale copy has no runtime effect. That holds for Claude Code's own MCP servers: the state-manager wrapper log shows 873 launches from the repo `worker/` and none from the 1.1.15 cache.

It is **wrong for Commander**. `commander/src/ironclaude/workspace_client.py:94-123` (`_local_discovery_evidence`) finds the workspace-manager CLI under `~/<provider cache dir>/plugins/cache/ironclaude/ironclaude/<version>/` for each worker provider. As a result:

- Commander-run **Codex** workers get the fix, because the Codex cache was refreshed in Steps 2–3.
- Commander-run **Claude** workers keep using the stale 1.1.15 Claude-cache `cli.js` until that cache is refreshed.

This is recorded in the backlog together with the earlier report that the Claude plugin cache was missing an npm dependency (`project_plugin_cache_missing_npm_dep`). Refreshing that cache is the operator's decision.

## Orchestrator action

Commander is restarted after this task. This is not a plan step.

## Final gate and order (R4)

1. Run the final Fable end review of the full staged L2c diff. It must come back CLEAN. Any material finding gets fixed and the review runs again.
2. Only then does the operator commit through `/commit`. The agent does not commit, and nobody pushes.

This loop's `allowed_files` are all repo-relative, so its `/commit` does not exercise the absolute-entry handling. The unit tests cover that handling, and L3 will exercise it in practice.
