# Drift-Check Review Fixes — Verification and Deploy Log (v1.1.16 L2b-1b)

> **Date:** 2026-10-03
> **Plan:** `docs/plans/2026-10-03-drift-check-review-fixes.md`, Task 3
> **Requirements:** R5 (verify and redeploy), R6 (commit and re-trust order)

## Task results before deploy

| Task | Change | Review grade | Regression test |
|---|---|---|---|
| 1 | The drift check resolves the effective root through `workspace_bind_effective_root`, accepts absolute `allowed_files` under that root, and runs `LC_ALL=C comm -23` | B (minor only) | `test-execution-drift-roots.sh`: RED 0/8, GREEN 8/8 |
| 2 | `state-activator.sh` buffers stdout and emits at most one JSON object | B (minor only) | `test-state-activator-single-object.sh`: RED 2/4, GREEN 4/4 |

## Step 1: hook suites

`make -C /Users/roberthyatt/Code/ironclaude test-hooks` exited 0. A search for a non-zero fail count found none. The two new suites' results:

```
--- worker/hooks/tests/test-execution-drift-roots.sh ---
Results: 8 pass, 0 fail
--- worker/hooks/tests/test-state-activator-single-object.sh ---
Results: 4 pass, 0 fail
```

## Step 2: Commander suite

`PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q` (in `commander/`) passed:

```
3457 passed in 386.77s (0:06:26)
exit=0
```

## Step 3: deploy hooks

```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```

`cmp` reported that the deployed `state-activator.sh` and `execution-drift-check.sh` are identical to the repo copies.

## Step 4: the Claude plugin's hooks.json

`diff worker/hooks/hooks.json <installPath>/hooks/hooks.json` printed nothing and exited 0. **hooks.json is unchanged, so the copy was skipped.**

## Step 5: Codex plugin cache

`codex plugin add ironclaude@ironclaude --json` exited 0:

```
"version": "1.1.15+codex.20260930172837",
"installedPath": "/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837"
```

## Step 6: the Codex cache matches worker/

`diff -rq worker <codex cache> -x node_modules -x __pycache__ -x .DS_Store` printed nothing and exited 0.

## Commit and re-trust order (R6)

- **Codex trust keys are unchanged.** `hooks.json` did not change in this loop, so the hook entries Codex trusts are the same as after the L2b-1 deploy.
- **Commit next.** L2b-1 and L2b-1b are staged together. The operator commits them as one change: `/commit`, no trailers, no push.
- **Re-trust after the commit.** Only then does the operator re-trust the four Codex hooks changed in L2b-1:
  - `pre_tool_use:2`
  - `pre_tool_use:3`
  - `pre_tool_use:6`
  - `post_tool_use:6`
- **Installed is not loaded.** Running clients keep their loaded copies. Claude picks up the hooks at the next session start. Codex needs a full quit and relaunch.
