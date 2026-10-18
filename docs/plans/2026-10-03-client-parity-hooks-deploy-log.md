# Client Parity Hooks: Deploy Log (Task 8)

Date: 2026-10-03

## Step 1: make test-hooks (exit 0)

```
Results: 8 passed, 0 failed
ALL CODEX BRAIN GATE TESTS PASSED
Results: 3 passed, 0 failed
Results: 42 passed, 0 failed
Results: 324 passed, 0 failed
Results: 17 passed, 0 failed
ALL WRAPPER TESTS PASSED
Results: 13 pass, 0 fail
Results: 6 passed, 0 failed
Results: 9 pass, 0 fail
Results: 11 pass, 0 fail
Results: 94 passed, 0 failed
Results: 8 passed, 0 failed
Results: 7 pass, 0 fail
Results: 150 passed, 0 failed
Results: 6 pass, 0 fail
Results: 4 passed, 0 failed
Results: 42 passed, 0 failed
Results: 3 pass, 0 fail
Results: 3 pass, 0 fail
Results: 28 pass, 0 fail
Results: 9 pass, 0 fail
Results: 7 pass, 0 fail
Results: 44 pass, 0 fail
(no FAIL lines; final line: 8 passed, 0 failed)
```

## Step 2: Commander pytest (exit 0)

```
3457 passed in 390.35s (0:06:30)
```

## Step 3: make deploy-hooks (exit 0)

```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```

## Step 3b: installPath and hooks.json install

```
27:        "installPath": "/Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15",
cp hooks.json: exit 0
diff worker/hooks/hooks.json vs installed copy: (no output) exit 0
```

## Step 4: codex plugin add ironclaude@ironclaude --json (exit 0)

```
{
  "pluginId": "ironclaude@ironclaude",
  "name": "ironclaude",
  "marketplaceName": "ironclaude",
  "version": "1.1.15+codex.20260930172837",
  "installedPath": "/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837",
  "authPolicy": "ON_INSTALL"
}
```

## Step 5: diff -rq worker vs Codex cache (exit 0)

```
(no output)
```

## Operator notes

- **Codex trust.** Settings > Hooks will need re-trust for these changed or new entries: `pre_tool_use:2:0` (plan-task-context matcher), `pre_tool_use:3:0` (guard matcher), `pre_tool_use:6:0` (new) and `post_tool_use:6:0` (new). No `post_tool_use:4:0` change (classification (b) did not apply).
- **Claude.** Claude loads the new hooks.json at its next session start. Script changes are live after Step 3.
