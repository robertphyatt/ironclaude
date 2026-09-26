# kill_worker Retry Wording, Guard Robustness and Grader Claim Accuracy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make `kill_worker` say "daemon will retry" only when the daemon will actually retry. Make the terminal guard survive a failing `has_session`. Scope the README and CHANGELOG truncation claims accurately.

**Requirements:** docs/plans/2026-09-26-kill-worker-retry-wording-requirements.md

**Design:** docs/plans/2026-09-26-kill-worker-retry-wording-design.md

**Architecture:** The retry clause is computed from the post-seam registry re-read `kill_worker` already does (`_wr`). The daemon sweeps only `status == 'running'` rows (`worker_registry.get_running_workers`). The terminal guard's liveness check fails toward doing nothing, as `_complete_worker_if_session_dead` does. The docs change is wording only. The seam keeps sole ownership of completion.

**Tech stack:** Python 3.11 and pytest.

## Execution invariants (every command below satisfies these)

- **No persisted shell state.** Shell state does not persist between steps, and every command uses absolute paths or its own `cd` prefix.
- **Bash cwd is `commander/`.** Use `git -C /Users/roberthyatt/Code/ironclaude` for repo-root operations.
- **zsh `nomatch`.** Globs are quoted.
- **`PYTHONUNBUFFERED=1` on every pytest run.**
- **No commits.** Changes are staged only. Never run `git stash`, because the index holds earlier staged v1.1.13 work that must survive.
- **Content anchors, not line numbers.** Line numbers are approximate; each edit names the exact existing code it replaces.

---

## Task 1: Retry clause and guard robustness in `kill_worker`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (`kill_worker`)
- Test: `commander/tests/test_worker_finalize_release.py` (append after class `TestKillWorkerHonestCompletionStatus`)

**Step 1 (RED).** Append to `test_worker_finalize_release.py`:

```python
class TestKillWorkerRetryClause:
    """'daemon will retry' is claimed only when the post-seam registry status is
    'running' — the only status the daemon sweeps (get_running_workers). A
    'failed' worker is never swept; kill_worker is its only integrate path."""

    _CALL_AGAIN = (
        "worker status is 'failed' (not swept by the daemon); "
        "call kill_worker again to retry finalization."
    )

    @staticmethod
    def _kill(release, status):
        tools = _unmanaged_kill_tools(has_session=False, status=status)
        tools._finalize_and_release_worker = MagicMock(return_value=release)
        return tools.kill_worker("w9")

    def test_failed_branch_failed_worker_says_call_again(self):
        result = self._kill(
            {"failure_phase": "probe", "error": "boom", "assignment_preserved": True},
            status="failed",
        )
        assert result["status"].endswith(
            "NOT completed; work preserved; " + self._CALL_AGAIN
        )
        assert "daemon will retry" not in result["status"]

    def test_failed_branch_running_worker_keeps_daemon_retry(self):
        result = self._kill(
            {"failure_phase": "probe", "error": "boom", "assignment_preserved": True},
            status="running",
        )
        assert result["status"].endswith(
            "NOT completed; work preserved; daemon will retry."
        )

    def test_seam_ok_branch_failed_worker_says_call_again(self):
        result = self._kill({"state": "resolved", "detail": "x"}, status="failed")
        assert result["status"] == (
            "Worker w9 session killed; the seam returned state=resolved but did "
            "not complete the worker (status=failed); " + self._CALL_AGAIN
        )
        assert "daemon will retry" not in result["status"]


class TestKillWorkerGuardLivenessFailure:
    def test_has_session_raising_returns_unknown_status_without_kill(self):
        tools = _unmanaged_kill_tools(has_session=False, status="completed")
        tools.tmux.has_session.side_effect = RuntimeError("ssh host unreachable")
        tools._finalize_and_release_worker = MagicMock()

        result = tools.kill_worker("w9")

        assert result["status"] == (
            "Worker w9 is already completed; nothing to finalize "
            "(session state unknown: RuntimeError)."
        )
        tools.tmux.kill_session.assert_not_called()
        tools._finalize_and_release_worker.assert_not_called()
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_finalize_release.py -k "RetryClause or GuardLivenessFailure"
```

Expected (predicted, not yet measured): `3 failed, 1 passed`.
- `test_failed_branch_failed_worker_says_call_again` and `test_seam_ok_branch_failed_worker_says_call_again` fail, because today every non-completed status ends `daemon will retry.`.
- `test_has_session_raising_returns_unknown_status_without_kill` fails with `RuntimeError` propagating out of `kill_worker`.
- `test_failed_branch_running_worker_keeps_daemon_retry` passes today. It is a regression guard: it fails if the fix drops the running case.

**Step 2 (GREEN).** In `orchestrator_mcp.py` `kill_worker`:

(a) In the terminal guard, replace:

```python
            _stale = bool(self.tmux.has_session(session_name, ssh_host=ssh_host))
```

with:

```python
            try:
                _stale = bool(self.tmux.has_session(session_name, ssh_host=ssh_host))
            except Exception as exc:  # noqa: BLE001 — an unreachable host must not raise out of the tool
                # Fail toward doing nothing (mirrors _complete_worker_if_session_dead):
                # kill_session needs the same host, so do not attempt it.
                logger.warning(
                    "kill_worker: %s already %s — session liveness check failed: %s",
                    worker_id, _prior_status, exc,
                )
                return {
                    "status": (
                        f"Worker {worker_id} is already {_prior_status}; nothing to "
                        f"finalize (session state unknown: {type(exc).__name__})."
                    ),
                    "runtime_seconds": None,
                    "remaining_work": self._get_remaining_work_after_kill(worker_id),
                }
```

(b) Directly after the line `_completed = _seam_ok and (_wr or {}).get("status") == "completed"`, add:

```python
        # The daemon sweeps only status == 'running' rows (get_running_workers);
        # claim a daemon retry only when that is true. A 'failed' worker (dead-
        # session detection) is never swept — kill_worker is its only path.
        _post_status = (_wr or {}).get("status") or "unknown"
        _retry_clause = (
            "daemon will retry."
            if _post_status == "running"
            else (
                f"worker status is '{_post_status}' (not swept by the daemon); "
                "call kill_worker again to retry finalization."
            )
        )
```

(c) In the `elif _seam_ok:` branch, replace the final string piece `"daemon will retry."` with `f"{_retry_clause}"`. The preceding pieces, up to and including `(status=...); `, stay unchanged.

(d) In the `else:` (FAILED) branch, replace `"NOT completed; work preserved; daemon will retry."` with `f"NOT completed; work preserved; {_retry_clause}"`.

Run the Step 1 command again. Expected: `4 passed`.

**Step 3: verify no hard-coded retry claim remains.**

```bash
rg -n -F "daemon will retry" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py
```

Expected: exactly one match, the `"daemon will retry."` literal inside `_retry_clause`.

**Step 4: regression over every `kill_worker` caller test.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_finalize_release.py tests/test_orchestrator_mcp.py tests/test_idle_enforcement.py tests/test_kill_worker_log_cap.py
```

Expected: 0 failed. The existing `test_success_dict_but_worker_not_completed_is_not_claimed` exact string (a `running` worker ending `daemon will retry.`) must still pass unchanged.

**Step 5: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_finalize_release.py
```

---

## Task 2: Documentation accuracy, plus the full suite

Depends on Task 1. No tests are required: this task changes documentation only. The full pytest run is the gate.

**Files:**
- Modify: `README.md` (`## What's New in v1.1.13`, the bullet starting `**Grader truncation can no longer happen.**`)
- Modify: `CHANGELOG.md` (`## 1.1.13`: the 8192-floor bullet and the `kill_worker` bullet)

**Step 1: README.** Replace the entire bullet that starts `- **Grader truncation can no longer happen.**` with:

```markdown
- **The Commander's local grader can no longer return truncated JSON.** Every schema it sends now caps its output length in the sampling grammar, and every schema-bound call requests at least 8192 output tokens. That closes the failure mode where a response was cut off mid-object and came back unparseable. The Bash plan-validator hook's schema-bound calls also get the 8192-token floor, but their schemas have no per-field length caps and they stay bounded by the hook's transport time budget.
```

**Step 2: CHANGELOG, 8192-floor bullet.** In the bullet that starts `- **A schema-bound (\`json_schema\` grammar) call now requests at least 8192 output tokens**`, replace:

`so a grammar-bounded object can't be cut off mid-JSON regardless of a low configured cap. Applies to`

with:

`so a length-bounded Commander grader schema can't be cut off mid-JSON regardless of a low configured cap. The floor applies to`

Then append this sentence at the end of the bullet's prose, before the file list in parentheses: ` The per-field length caps apply only to the Commander grader schemas; the plan-validator hook's schemas get the token floor only and remain bounded by the hook's transport budget.`

**Step 3: CHANGELOG, `kill_worker` bullet.** In the bullet that starts `- **\`kill_worker\` no longer re-completes an already-terminal worker`, insert this before its final `(\`orchestrator_mcp.py\`)`: ` When the worker is not completed, the status says "daemon will retry" only if the worker's registry status is still \`running\` (the only status the daemon sweeps); otherwise it names the status and says to call \`kill_worker\` again, because a \`failed\` worker is never swept. If the guard's session-liveness check itself fails (for example, an unreachable remote host), \`kill_worker\` returns "session state unknown" instead of raising.`

**Step 4: verify the wording.**

```bash
rg -n -F "Grader truncation can no longer happen" /Users/roberthyatt/Code/ironclaude/README.md /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: no output.

```bash
rg -n -F "a grammar-bounded object can't be cut off" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: no output.

```bash
rg -n -F "session state unknown" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: one match, in the `## 1.1.13` `kill_worker` bullet.

**Step 5: full pytest.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Use a Bash timeout of 600000. Expected: 0 failed. Record the exact pass line; it should be 3356 + 4 new tests.

**Step 6: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add README.md CHANGELOG.md
```
