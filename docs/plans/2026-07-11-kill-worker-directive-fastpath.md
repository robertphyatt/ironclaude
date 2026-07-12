# kill_worker Directive Fast-Path Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional `directive_id` parameter to `kill_worker` so it can skip the Opus grader entirely when the directive is already marked `completed`, eliminating grader-timeout blockage for the common already-confirmed-completion case.

**Architecture:** `kill_worker` (class method + MCP tool wrapper, both in `orchestrator_mcp.py`) gains `directive_id: int | None = None`. Before the existing grader branch, a new `directive_completed` check runs a single `SELECT status FROM directives WHERE id = ?` (wrapped in try/except, fail-open) and, if the status is `'completed'`, the existing two-way `if original_objective and evidence: ... else: ...` grader branch becomes a three-way branch with the fast-path taking priority. All three branches converge on the unchanged kill/registry-update/event-log/return tail.

**Tech Stack:** Python 3, sqlite3, pytest.

---

## Task 1: Add directive fast-path to kill_worker

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:3492-3586` (class method) and `:4218-4224` (MCP tool wrapper)
- Test: `commander/tests/test_orchestrator_mcp.py` (add to `class TestInlineGraderEnforcement:`, after `test_kill_without_evidence_skips_grader` at line 2393, before `test_spawn_grader_failure_blocks_spawn` at line 2395)

**Step 1: Write the two required tests (RED)**

Insert these two methods into `class TestInlineGraderEnforcement:` in `commander/tests/test_orchestrator_mcp.py`, immediately after `test_kill_without_evidence_skips_grader` (ends at line 2393) and before `test_spawn_grader_failure_blocks_spawn` (starts at line 2395):

```python
    def test_kill_worker_fast_path_skips_grader_when_directive_completed(self, tools, registry, mock_tmux, db_conn):
        """kill_worker skips the grader entirely when directive_id resolves to status='completed',
        even when objective/evidence are also provided — the fast-path takes priority over the
        grader (per design). original_objective/evidence must be non-empty here so that a BROKEN
        fast-path would fall into the elif and actually call the grader, making this test capable
        of failing — a call with empty objective/evidence would hit the same no-op `else` branch
        regardless of whether the fast-path logic works, and could never catch a regression."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        cursor = db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1.0', 'do work', 'Implement feature X', 'completed')"
        )
        db_conn.commit()
        directive_id = cursor.lastrowid
        tools._call_grader = MagicMock()

        result = tools.kill_worker(
            "w1", original_objective="Build X", evidence="git diff shows changes",
            directive_id=directive_id,
        )

        tools._call_grader.assert_not_called()
        assert isinstance(result, dict)
        assert registry.get_worker("w1")["status"] == "completed"
        mock_tmux.kill_session.assert_called_once()

    def test_kill_worker_directive_not_completed_still_calls_grader(self, tools, registry, mock_tmux, db_conn):
        """kill_worker still grades normally when directive_id resolves to a non-completed status."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        cursor = db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1.0', 'do work', 'Implement feature X', 'in_progress')"
        )
        db_conn.commit()
        directive_id = cursor.lastrowid
        tools._call_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "Verified"
        })

        result = tools.kill_worker(
            "w1", original_objective="Build X", evidence="git diff shows changes",
            directive_id=directive_id,
        )

        tools._call_grader.assert_called_once()
        assert isinstance(result, dict)
        mock_tmux.kill_session.assert_called_once()
```

**Step 2: Run the new tests, verify they fail (RED)**

Run:
```bash
cd commander && python -m pytest tests/test_orchestrator_mcp.py -k "test_kill_worker_fast_path_skips_grader_when_directive_completed or test_kill_worker_directive_not_completed_still_calls_grader" -v
```

Expected: both FAIL with `TypeError: kill_worker() got an unexpected keyword argument 'directive_id'` — `directive_id` doesn't exist on `kill_worker` yet.

**Step 3: Implement the fast-path in the class method (GREEN, part 1)**

In `commander/src/ironclaude/orchestrator_mcp.py`, replace the `kill_worker` class method (lines 3492-3586) with:

```python
    def kill_worker(self, worker_id: str, original_objective: str = "", evidence: str = "", directive_id: int | None = None) -> str | dict:
        """Kill a worker's tmux session and mark it completed."""
        _kw = self.registry.get_worker(worker_id)
        if _kw and _kw.get("machine"):
            self._ensure_ssh_manager()
        ssh_host = self._resolve_ssh_host(worker_id)
        session_name = f"ic-{worker_id}"
        remote_log_dir = None
        if ssh_host:
            machine = self._ssh_manager.get_machine(_kw["machine"]) if self._ssh_manager and _kw else None
            remote_log_dir = machine.log_dir if machine else None

        directive_completed = False
        if directive_id is not None:
            try:
                row = self._db.execute(
                    "SELECT status FROM directives WHERE id = ?", (directive_id,)
                ).fetchone()
                directive_completed = bool(row and row["status"] == "completed")
            except Exception as exc:
                logger.warning(f"directive_id lookup failed for {directive_id}: {exc}")

        # Inline grader enforcement — MCP grades automatically
        if directive_completed:
            logger.info(f"kill_worker fast-path: directive {directive_id} already completed — skipping grader")
        elif original_objective and evidence:
            avatar_skill = _load_avatar_skill()
            system_prompt = f"""{avatar_skill}

You are grading a kill_worker decision. The worker's recent log is provided in the user
message as a capped excerpt — evaluate log evidence from that excerpt, not by re-reading
the log file. You may still use Read and Bash to investigate other evidence (diffs, test
output). When ready, output ONLY a valid JSON object on a single line:
{{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "specific feedback"}}

Grading criteria:
- A: All success criteria verified with concrete evidence (diffs, timestamps, test results). approved=true
- B: Most criteria verified, minor items can be deferred. approved=true
- C: Some criteria unverified — trusted self-assessment instead of checking. approved=false
- D: Worker claimed done but evidence shows incomplete. approved=false
- F: Work clearly not done. approved=false"""

            try:
                log_tail = self.tmux.read_log_tail(
                    session_name, lines=self.GRADER_LOG_MAX_LINES,
                    ssh_host=ssh_host, remote_log_dir=remote_log_dir,
                )
            except Exception as exc:  # a log-read failure must not abort the kill+grade
                log_tail = f"(worker log unavailable — {type(exc).__name__}: {exc})"
            user_prompt = f"""Evaluate this kill_worker decision:

worker_id: {worker_id}
original_objective: {original_objective}
evidence provided: {evidence}

Has the worker genuinely completed its objective based on the evidence?

--- WORKER LOG (last {self.GRADER_LOG_MAX_LINES} lines) ---
{log_tail}"""

            grade_result = self._call_grader(system_prompt, user_prompt)
            _kw_repo = (_kw or {}).get("repo")  # already fetched at method entry
            _opus_tool_calls = self._parse_tool_calls_from_delta(self._last_grader_delta)
            self._fire_shadow_thread("kill_worker", worker_id, _kw_repo, grade_result, _opus_tool_calls, system_prompt, user_prompt)
            if not grade_result["approved"]:
                # Track failure for retry escalation
                fail_base = re.sub(r'[-_]?\d*[a-z]?$', '', worker_id)
                if fail_base:
                    self._track_failed_base(fail_base)
                    logger.info(f"Tracked failure base '{fail_base}' for retry escalation")
                return {
                    "error": f"Kill rejected by grader (grade {grade_result['grade']}). {grade_result['feedback']}",
                    "action": "send worker back to finish, then try again with updated evidence",
                }
            logger.info(f"Grader approved kill for '{worker_id}' (grade {grade_result['grade']})")
        else:
            logger.warning(f"kill_worker called without objective/evidence for '{worker_id}' — skipping grader")

        pane_pid = self.tmux.list_pane_pid(session_name, ssh_host=ssh_host)
        self.tmux.kill_session(session_name, ssh_host=ssh_host)
        self.registry.update_worker_status(worker_id, "completed")
        _wr = self.registry.get_worker(worker_id)
        _runtime = None
        if _wr:
            try:
                _created = datetime.fromisoformat(_wr["spawned_at"])
                if _created.tzinfo is None:
                    _created = _created.replace(tzinfo=timezone.utc)
                _runtime = round(time.time() - _created.timestamp(), 1)
            except (ValueError, TypeError):
                pass
        log_worker_event(
            "WORKER_KILLED",
            worker_id=worker_id,
            pane_pid=pane_pid,
            had_evidence=bool(original_objective and evidence),
            kill_reason=evidence[:200] if evidence else None,
            runtime_seconds=_runtime,
        )
        self.registry.log_event("worker_finished", worker_id=worker_id)
        # Post-kill sweep: query remaining work for Brain visibility
        remaining_work = self._get_remaining_work_after_kill(worker_id)
        return {
            "status": f"Worker {worker_id} killed and marked completed.",
            "runtime_seconds": _runtime,
            "remaining_work": remaining_work,
        }
```

Only the additions are: the `directive_id` parameter, the `directive_completed` lookup block, and changing `if original_objective and evidence:` to `elif original_objective and evidence:` with the new `if directive_completed:` branch above it. Everything else is byte-for-byte identical to the current method.

**Step 4: Update the MCP tool wrapper (GREEN, part 2)**

In `commander/src/ironclaude/orchestrator_mcp.py`, replace lines 4218-4224:

```python
    @mcp.tool()
    def kill_worker(worker_id: str, original_objective: str = "", evidence: str = "") -> str:
        """Kill a worker's tmux session and mark it completed. Use after reviewing worker log."""
        result = tools.kill_worker(worker_id, original_objective, evidence)
        if isinstance(result, dict):
            return json.dumps(result)
        return result
```

with:

```python
    @mcp.tool()
    def kill_worker(worker_id: str, original_objective: str = "", evidence: str = "", directive_id: int | None = None) -> str:
        """Kill a worker's tmux session and mark it completed. Use after reviewing worker log.

        directive_id: Optional id of the directive this worker was fulfilling. When set and
            that directive's status is 'completed', the grader is skipped and the kill is
            approved immediately. Otherwise grading proceeds as before.
        """
        result = tools.kill_worker(worker_id, original_objective, evidence, directive_id)
        if isinstance(result, dict):
            return json.dumps(result)
        return result
```

**Step 5: Run the new tests, verify they pass (GREEN)**

Run:
```bash
cd commander && python -m pytest tests/test_orchestrator_mcp.py -k "test_kill_worker_fast_path_skips_grader_when_directive_completed or test_kill_worker_directive_not_completed_still_calls_grader" -v
```

Expected: both PASS.

**Step 6: Run the full test suite**

Run:
```bash
cd commander && python -m pytest tests/ -x
```

Expected: all tests pass, no regressions (existing `TestKillWorker` and `TestInlineGraderEnforcement` tests — none of which pass `directive_id` — must still pass unchanged, since `directive_id` defaults to `None` and `directive_completed` defaults to `False`).

**Step 7: Stage changes**

Run:
```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

Expected: both files staged (professional mode blocks commit).

---

## Post-Execution: Manual MCP Server Restart (required, not automatable by this session)

The daemon's MCP server subprocess loads `orchestrator_mcp.py` at process start and does not hot-reload edits. There is no shell-level restart mechanism for it — no Makefile target, no restart script, no launchd plist (verified: `commander/Makefile` has only `install`/`test`/`run`/`stop`/`follow-run`; `stop` sends `SIGTERM`, not a reload signal). The only way to load this change is the `restart_mcp` MCP tool already defined in this file (`orchestrator_mcp.py:3852-3867`, registered at `:4231-4238`), which `os.execvp`s a fresh interpreter in place, preserving the MCP stdio connection.

**This Claude Code session does not have the orchestrator MCP server connected** (it only has `state-manager`/`episodic-memory`/etc.), so it cannot call `restart_mcp` itself, regardless of execution mode chosen below.

**Required manual step after this plan's task is committed:** call the `restart_mcp` MCP tool from a session that does have the orchestrator MCP server connected (e.g. a Brain session), or, if unavailable, the heavier `restart_daemon` tool (SIGHUP to the daemon at `/tmp/ic-daemon.pid`, which cascades to the MCP subprocess). Do not consider this fix live until one of these has been called and `kill_worker` has been exercised once with `directive_id` set.

## Implementation Notes

- This is a single cohesive change to one file plus its test file — no dependency chain, no parallelizable sub-tasks.
- `directive_id` is opt-in: existing/automated callers that don't pass it get byte-for-byte unchanged behavior.
