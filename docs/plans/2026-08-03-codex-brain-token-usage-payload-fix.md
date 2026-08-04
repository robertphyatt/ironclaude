# Codex Brain Token-Usage Payload Parity Fix Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task in subagent-sequential mode with Terra workers.

**Goal:** Restore existing Commander heartbeat token accounting for Codex Brain by reading the cumulative breakdown in the installed app-server payload.

**Requirements:** `docs/plans/2026-08-03-codex-brain-token-usage-payload-fix-requirements.md`

**Design:** `docs/plans/2026-08-03-codex-brain-token-usage-payload-fix-design.md`

**Architecture:** Keep event handling and heartbeat presentation unchanged. At the provider-neutral `get_token_usage()` adapter boundary, select dictionary-valued `tokenUsage.total` when present and retain the current flat payload as a defensive fallback.

**Tech Stack:** Python 3.11+, pytest, Codex app-server JSON-RPC, Commander SIGHUP restart path.

## Execution Invariants

- Shell state does not persist between steps; commands use literal paths and do not depend on prior exports.
- Execution cwd is `commander/`; Git commands use `git -C /Users/roberthyatt/Code/ironclaude`.
- Quote shell globs; zsh `nomatch` must not decide a result.
- Do not use foreground `sleep`; run a later verification step instead.
- `docs/` is ignored; stage plan artifacts with `git add -f`.
- Empty evidence is not success. Verification commands print named counts and exit nonzero when invariants fail.
- No command hard-codes a discovered PID as its proof; each process check enumerates current state.
- Each check names its broken state: the RED assertion fails on outer-object parsing, tests fail on regression, and process counts fail on missing or duplicate runtimes.

---

## Task 1: Normalize cumulative Codex token usage with protocol-shaped TDD

**Files:**

- Modify: `commander/tests/test_codex_brain_client.py`
- Modify: `commander/src/ironclaude/codex_brain_client.py`

### Step 1: Preserve flat fallback coverage and add the installed protocol shape

Keep the existing `test_real_token_usage_event_populates_usage` test unchanged because it verifies the required defensive flat fallback. Add this second method to `TestTokenUsageEventName` in `commander/tests/test_codex_brain_client.py`:

```python
    def test_real_token_usage_event_populates_cumulative_usage(self):
        client = CodexBrainClient()
        client._handle_event({
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {
                    "last": {
                        "cachedInputTokens": 2,
                        "inputTokens": 11,
                        "outputTokens": 7,
                        "reasoningOutputTokens": 3,
                        "totalTokens": 18,
                    },
                    "total": {
                        "cachedInputTokens": 20,
                        "inputTokens": 110,
                        "outputTokens": 70,
                        "reasoningOutputTokens": 30,
                        "totalTokens": 191,
                    },
                    "modelContextWindow": 200000,
                },
            },
        })

        usage = client.get_token_usage()

        assert usage is not None
        assert usage["input_tokens"] == 110
        assert usage["output_tokens"] == 70
        assert usage["total_tokens"] == 191
        assert usage["cost_usd"] == 0.0
        assert usage["seconds_since_last_activity"] is not None
```

The distinct `last` and `total` values ensure the test fails if the parser uses last-turn usage or the outer wrapper. The deliberately non-additive `totalTokens` value is valid under the installed schema and proves the getter honors the explicit field instead of always deriving `inputTokens + outputTokens`.

### Step 2: Run the protocol-shaped test and prove RED

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_codex_brain_client.py::TestTokenUsageEventName::test_real_token_usage_event_populates_cumulative_usage -q
```

Expected: exit 1; assertion reports `0 == 110`. If it passes before the production change or fails for a different reason, stop and report because the planned causal proof is invalid.

### Step 3: Select the cumulative breakdown in `get_token_usage()`

In `commander/src/ironclaude/codex_brain_client.py`, replace `get_token_usage()` with:

```python
    def get_token_usage(self) -> dict | None:
        usage = self._token_usage
        if not usage:
            return None
        breakdown = usage.get("total")
        if not isinstance(breakdown, dict):
            breakdown = usage
        input_tokens = breakdown.get("inputTokens", breakdown.get("input_tokens", 0)) or 0
        output_tokens = breakdown.get("outputTokens", breakdown.get("output_tokens", 0)) or 0
        total = breakdown.get("totalTokens", breakdown.get("total_tokens")) or (input_tokens + output_tokens)
        age = time.time() - self._last_activity if self._last_activity > 0 else None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
            # Codex usage is subscription-billed; no per-turn dollar figure is reported.
            "cost_usd": usage.get("costUsd", usage.get("cost_usd", 0.0)) or 0.0,
            "seconds_since_last_activity": age,
        }
```

Do not change event dispatch, notification formatting, provider selection, or any other file.

### Step 4: Prove GREEN with the focused client suite

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_codex_brain_client.py -q
```

Expected: exit 0; all tests in `test_codex_brain_client.py` pass. This catches a parser regression but does not by itself prove heartbeat presentation.

### Step 5: Prove heartbeat formatting remains unchanged

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_notifications.py -q
```

Expected: exit 0; all notification tests pass. This catches accidental changes to zero-token activity wording and nonzero formatting.

### Step 6: Stage only Task 1 source and test changes

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/codex_brain_client.py commander/tests/test_codex_brain_client.py
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only | /usr/bin/python3 -c 'import sys; paths=[line.strip() for line in sys.stdin if line.strip()]; required={"commander/src/ironclaude/codex_brain_client.py","commander/tests/test_codex_brain_client.py"}; workflow={"docs/plans/2026-08-03-codex-brain-token-usage-payload-fix-design.md","docs/plans/2026-08-03-codex-brain-token-usage-payload-fix-requirements.md","docs/plans/2026-08-03-codex-brain-token-usage-payload-fix.md","docs/plans/2026-08-03-codex-brain-token-usage-payload-fix.plan.json"}; unexpected=set(paths)-(required|workflow); missing=required-set(paths); print("STAGED_PATHS"); print("\n".join(paths)); print("MISSING_TASK_PATHS="+("none" if not missing else ",".join(sorted(missing)))); print("UNEXPECTED_PATHS="+("none" if not unexpected else ",".join(sorted(unexpected)))); raise SystemExit(0 if not missing and not unexpected else 1)'
```

Expected: exit 0; output lists the four workflow artifacts and two Task 1 paths, then `MISSING_TASK_PATHS=none` and `UNEXPECTED_PATHS=none`. The command fails on a missing Task 1 path or any unrelated staged path. Professional mode blocks commit.

---

## Task 2: Run full verification and activate the repaired Commander

**Files:**

- Read-only verification authority: `commander/src/ironclaude/codex_brain_client.py`
- Read-only verification authority: `commander/tests/test_codex_brain_client.py`

No file edits are permitted in Task 2. Any failure requiring a change must return through the fix-first review path to Task 1's bounded files.

### Step 1: Run the full Commander suite

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests -q
```

Expected: exit 0; the complete Commander suite passes. This catches interaction regressions outside the focused adapter and formatter tests.

### Step 2: Verify the pre-restart runtime identity

Run:

```bash
ps -axo pid=,ppid=,command= | /usr/bin/python3 -c 'import sys; rows=[]; [rows.append((int(parts[0]),int(parts[1]),parts[2])) for line in sys.stdin for parts in [line.strip().split(None,2)] if len(parts)==3]; daemons=[row for row in rows if "/commander/.venv/bin/python -m ironclaude.main" in row[2] and "python3 -c" not in row[2]]; parents={pid:ppid for pid,ppid,_ in rows}; descends=lambda pid,ancestor: any(parent==ancestor for parent in iter(lambda p=[pid]: (p.__setitem__(0,parents.get(p[0],-1)) or p[0]),-1)); owned=[] if len(daemons)!=1 else [row for row in rows if row[2].startswith("codex app-server") and descends(row[0],daemons[0][0])]; print(f"COMMANDER_DAEMONS={len(daemons)}"); print(f"COMMANDER_OWNED_CODEX_APP_SERVERS={len(owned)}"); print("\n".join(str(row) for row in daemons+owned)); raise SystemExit(0 if len(daemons)==1 and len(owned)==1 else 1)'
```

Expected: exit 0 with `COMMANDER_DAEMONS=1` and `COMMANDER_OWNED_CODEX_APP_SERVERS=1`, followed by the enumerated processes. Unrelated app-server processes neither satisfy nor invalidate this ownership check.

### Step 3: Send the existing bounded restart signal

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/ironclaude restart
```

Expected: exit 0 with `Restart signal sent to daemon PID <enumerated PID>`. The daemon's existing SIGHUP handler uses `execvp`, loading current source without introducing a new restart mechanism.

### Step 4: Verify the post-restart runtime identity

Run the same enumerating process command from Step 2 in a later tool invocation.

Expected: exit 0 with `COMMANDER_DAEMONS=1` and `COMMANDER_OWNED_CODEX_APP_SERVERS=1`. A missing owned app-server means restart initialization is incomplete; re-run this read-only check once after other verification work, not through foreground `sleep` and not through another restart.

### Step 5: Inspect bounded restart evidence

Run:

```bash
/usr/bin/tail -n 160 /tmp/ironclaude-daemon.log
```

Expected: recent output contains `Daemon restart requested via SIGHUP`, a fresh `Brain SDK client started`, and `IronClaude Commander daemon starting.` without a subsequent startup traceback or duplicate-daemon exit. This fails on restart-path or initialization errors.

### Step 6: Report live token-counter acceptance honestly

Perform one post-verification read of the next naturally available Commander heartbeat after a completed Codex Brain turn. Expected: nonzero cumulative input/output/total values. If no completed turn and heartbeat are naturally available in that one observation, report live acceptance as pending; do not poll indefinitely, create synthetic provider work, add logging, change Slack behavior, restart again, or alter scope.

No staging step is required because Task 2 must not edit files.
