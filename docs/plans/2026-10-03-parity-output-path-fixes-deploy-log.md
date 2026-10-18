# Parity Output and Path Fixes — Verification and Deploy Log (v1.1.16 L2b-1c)

> **Date:** 2026-10-03
> **Plan:** `docs/plans/2026-10-03-parity-output-path-fixes.md`, Task 3
> **Requirements:** R5 (verify and redeploy), R6 (commit and re-trust order)

## Task results before deploy

| Task | Change | Review grade | Regression test |
|---|---|---|---|
| 1 | Absolute `allowed_files` entries map to the drift root by file identity (`_drift_rel_by_identity`, `[ -ef ]` walk) | B (minor only) | `test-execution-drift-identity.sh`: RED 0/4, GREEN 4/4 (case-insensitive volume, so case 3 ran) |
| 2 | `state-activator.sh` emits one context-only `UserPromptSubmit` object and never a `systemMessage`; `surface_mcp_errors` logs at `ERROR` | B (minor only) | `test-state-activator-single-object.sh`: RED 2/6, GREEN 6/6 |

## Step 1: hook suites

`make -C /Users/roberthyatt/Code/ironclaude test-hooks` exited 0, and a search for any non-zero fail count found none. The two suites changed in this loop reported:

```
--- worker/hooks/tests/test-execution-drift-identity.sh ---
Results: 4 pass, 0 fail
--- worker/hooks/tests/test-state-activator-single-object.sh ---
Results: 6 pass, 0 fail
```

## Step 2: Commander suite

```
3457 passed in 423.07s (0:07:03)
exit=0
```

## Step 3: deploy hooks

```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```

`cmp` confirmed that the deployed `state-activator.sh`, `hook-logger.sh` and `execution-drift-check.sh` are identical to the repo copies.

## Step 4: the Claude plugin's hooks.json

The diff against `<installPath>/hooks/hooks.json` printed nothing and exited 0. **hooks.json is unchanged, so I skipped the copy.**

## Step 5: Codex plugin cache

`codex plugin add ironclaude@ironclaude --json` exited 0:

```
"version": "1.1.15+codex.20260930172837",
"installedPath": "/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837"
```

## Step 6: Codex cache matches worker/

`diff -rq` printed nothing and exited 0.

## Commit and re-trust order (R6)

- **Trust keys:** `hooks.json` is unchanged, so the Codex hook trust keys are the same as after the L2b-1 deploy.
- **Commit:** L2b-1, L2b-1b and L2b-1c are staged together. The operator commits them as one change with `/commit`, without trailers and without a push.
- **Re-trust:** only after that commit, the operator re-trusts the four Codex hooks: `pre_tool_use:2`, `pre_tool_use:3`, `pre_tool_use:6` and `post_tool_use:6`.
- **Restart:** running clients keep the copies they loaded. Claude picks up the change at the next session start; Codex needs a full quit and relaunch.
