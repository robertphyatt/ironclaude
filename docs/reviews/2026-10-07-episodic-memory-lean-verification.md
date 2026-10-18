# Episodic-memory lean: Task 13 verification (2026-10-07)

## 1. episodic-memory vitest

Command: `worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root worker/mcp-servers/episodic-memory --reporter=verbose`

```
 Test Files  18 passed (18)
      Tests  126 passed (126)
```

Plan's static estimate was 117 (19 pre-existing + 98 new). Actual is 126, a difference of +9. The estimate was static and the plan was extended during execution (one compaction test was added after the estimate). Per-file counts (sum 126):

| File | Tests |
|---|---|
| src/__tests__/embedding-backend.test.ts | 3 |
| src/__tests__/parent-death-exit.test.ts | 2 |
| src/archive-io.test.ts | 7 |
| src/archive-readers.test.ts | 4 |
| src/codex-parser.test.ts | 1 |
| src/codex-paths.test.ts | 2 |
| src/codex-sync.test.ts | 5 |
| src/db.test.ts | 4 |
| src/maintain-cli.test.ts | 7 |
| src/maintain-compact.test.ts | 13 |
| src/maintain-run.test.ts | 7 |
| src/maintain-slim.test.ts | 9 |
| src/parser-voice.test.ts | 5 |
| src/search-provenance.test.ts | 5 |
| src/settings.test.ts | 27 |
| src/summarizer.test.ts | 14 |
| src/sync-lean.test.ts | 4 |
| src/sync-lock.test.ts | 7 |

Pre-existing files: codex-parser 1, codex-sync 5, codex-paths 2, summarizer 6 (now 14), embedding-backend 3, parent-death-exit 2. Static count of the 19 pre-existing cases was 19; summarizer.test.ts now holds 14, i.e. 8 more than its static 6, which accounts for part of the discrepancy against the plan's 98-new figure. Not otherwise itemised here.

## 2. Hook suites

Command: the three-glob loop from plan Step 2. Output:

```
FAIL /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
FAIL /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh
DONE
```

Logs: `/tmp/ic-em-hooks-<suite>.log`.

### test-gbtw-continuation-judge.sh: PRE-EXISTING (rg dependency)
Log ends `results: 77 pass, 107 fail`. Log contains `line 148..152: rg: command not found` (repeated). No `rg` binary on PATH in the non-interactive bash used (`rg` exists only as a shell function in the interactive shell). Confirmation: rerun with a PATH shim `rg -> grep` gives `results: 184 pass, 0 fail`.

### test-last-assistant-text.sh: PRE-EXISTING (rg dependency), no `command not found` in log
Log: `Results: 6 pass, 1 fail`; failing case is test 5 "GBTW reports unavailable Codex evaluation". The output shows `decision: approve` and a systemMessage containing "evaluation unavailable", so the first two conditions pass. The third condition, `rg -qF ... 2>/dev/null` (test line 71), hides stderr, so the log has no "command not found". Confirmation: with the same `rg -> grep` PATH shim, `Results: 7 pass, 0 fail`. Classified as the rg dependency, not a regression.

No hook file is modified in the staged set (`git diff --cached --name-only | grep hooks/` is empty).

## 3. Commander pytest

Command: `cd commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q`

```
FAILED tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd
1 failed, 3760 passed in 440.37s (0:07:20)
```

The one failure is the known pre-existing one (matches the 2026-10-06 baseline of 1 failed, 3760 passed). No other failures.

## 4. Staged set

Command: `git diff --cached --name-only -- worker/mcp-servers/episodic-memory README.md`

Count: 37. Compared against the plan's Step 4 list: missing none, extra none. Paths under `dist/`: 0.
Breakdown: 33 under `src/` (including `src/__fixtures__/`), 3 package/CLI (`package.json`, `cli/episodic-memory.js`, `cli/mcp-server-wrapper.js`), plus `README.md`.
