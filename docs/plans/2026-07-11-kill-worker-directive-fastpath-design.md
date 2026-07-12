# kill_worker Directive Fast-Path Design

> **Created:** 2026-07-11
> **Status:** Design Complete

## Summary

`kill_worker` (`commander/src/ironclaude/orchestrator_mcp.py`) always invokes a claude-opus grader to evaluate whether a worker completed its objective before allowing the kill. The grader reads a capped log tail and calls out to an Opus session; when that call is slow, it can hit the 600s `GRADER_TIMEOUT_SECONDS` ceiling and fail via the double-timeout grade-F fallback, blocking the kill entirely. This has repeatedly blocked cleanup (d1310, d1321, d1322) in cases where the worker's directive was already marked `completed` in the `directives` table before `kill_worker` was called — the grader call was redundant in those cases, since completion was already confirmed by other means.

The directive that requested this fix (d1325) assumed `kill_worker` could resolve a `directive_id` from `worker_id` via the worker registry. That lookup path does not exist: the `workers` table has no `directive_id` column, and `spawn_worker` receives `directive_id` only to run a spawn-time drift check — it is never persisted. `worker_id` also does not reliably encode a directive number; two of the four worker-registration code paths (`adopt_session`, `resume_session`) have no `directive_id` concept at all. A schema migration to persist `directive_id` on the `workers` table would also violate this fix's single-file constraint (`orchestrator_mcp.py` only) and would not retroactively help the three already-blocked directives, whose workers were registered with no `directive_id` stored.

The fix instead adds an optional `directive_id: int | None = None` parameter to `kill_worker`, supplied explicitly by the caller at kill time. This is fully within `orchestrator_mcp.py`, requires no schema change, and is immediately usable for d1310/d1321/d1322 since the caller already knows their directive numbers.

## Architecture

Both `kill_worker` entry points in `orchestrator_mcp.py` — the `OrchestratorTools.kill_worker` class method (~line 3492) and the `@mcp.tool()` wrapper (~line 4219) — gain a fourth parameter, `directive_id: int | None = None`, and the wrapper passes it straight through unchanged (matching the existing pass-through pattern for `original_objective`/`evidence`).

Inside the class method, a new fast-path check runs before the existing `if original_objective and evidence:` grader block (which stays otherwise unmodified). If `directive_id` is provided, the method queries `directives.status` for that id. If the status is `'completed'`, the method logs that the fast-path fired and skips straight to the existing kill+registry-update+event-log+return tail — the same code that today runs after a successful grade. If `directive_id` is absent, the row doesn't exist, or the status is anything other than `'completed'`, control falls through to the existing two-way branch (grade if objective+evidence given, else warn-and-skip) exactly as it behaves today. The fast-path takes priority over the grader check — if `directive_id` resolves to `'completed'`, the grader is skipped even if `original_objective`/`evidence` were also passed.

This makes the fast-path opt-in: callers (the Brain or a human) must explicitly pass `directive_id` for it to fire. `system_prompt.md`/`workflow.md` are out of scope for this fix, so no automated caller starts passing it as a side effect of this change — the fix makes the fast-path available, not automatic.

## Components

All changes in `commander/src/ironclaude/orchestrator_mcp.py`:

1. **`kill_worker` class method signature**: add `directive_id: int | None = None`.
2. **New fast-path check**, placed before the `if original_objective and evidence:` block:
   ```python
   directive_completed = False
   if directive_id is not None:
       try:
           row = self._db.execute(
               "SELECT status FROM directives WHERE id = ?", (directive_id,)
           ).fetchone()
           directive_completed = bool(row and row["status"] == "completed")
       except Exception as exc:
           logger.warning(f"directive_id lookup failed for {directive_id}: {exc}")
   ```
3. **Branch restructure**: the existing `if original_objective and evidence: ... else: ...` becomes a three-way `if directive_completed: ... elif original_objective and evidence: ... else: ...`, where the new first branch logs `f"kill_worker fast-path: directive {directive_id} already completed — skipping grader"` and does nothing else (falls through to the shared kill/cleanup tail below the whole conditional, same as the other two branches do today).
4. **MCP wrapper**: add matching `directive_id: int | None = None` parameter, pass through to `tools.kill_worker(...)`, and update the docstring to mention it (mirroring how `spawn_worker`'s docstring documents its own `directive_id` param).

No changes to `_call_grader`, `GRADER_TIMEOUT_SECONDS`, `GRADER_LOG_MAX_LINES`, or any file outside `orchestrator_mcp.py`.

## Data Flow

Caller supplies `worker_id` and, optionally, `directive_id` → `kill_worker` resolves `ssh_host`/`session_name`/`remote_log_dir` as it does today (unchanged) → if `directive_id` given, a single `SELECT status FROM directives WHERE id = ?` query determines `directive_completed` → three-way branch selects fast-path / grader / warn-skip → all three paths converge on the existing shared tail: kill the tmux session, mark the worker `completed` in the registry, compute runtime, log the `WORKER_KILLED` event, run the existing post-kill remaining-work sweep, and return the existing `{status, runtime_seconds, remaining_work}` shape unchanged.

## Error Handling

The `directives.status` lookup is wrapped in `try/except Exception`, matching the existing fail-open pattern already used in this method for `read_log_tail` failures and the `_get_remaining_work_after_kill` sweep. Any lookup failure (bad `directive_id`, DB error) simply leaves `directive_completed = False` and the method falls through to its current behavior — no new error is surfaced to the caller, and no existing failure mode changes.

## Testing Strategy

Two required tests, added alongside the existing `TestKillWorker`/`TestInlineGraderEnforcement` classes in `commander/tests/test_orchestrator_mcp.py` (where `kill_worker`'s `OrchestratorTools`/tmux/`_call_grader` fixtures already live — `test_worker_registry.py` only covers `WorkerRegistry`'s own table wrappers and has no `kill_worker` coverage today):

1. **Fast-path fires**: insert a directive row with `status='completed'` (raw `INSERT INTO directives (..., status) VALUES (..., 'completed')`, following the pattern already used elsewhere in the file), register a worker, mock `tools._call_grader`, call `kill_worker(worker_id, directive_id=<id>)` with no `original_objective`/`evidence`, and assert `_call_grader.assert_not_called()` plus the worker ends up `status == 'completed'` in the registry.
2. **Fast-path doesn't fire when not completed**: insert a directive with `status='in_progress'` (or `'confirmed'`), call `kill_worker(worker_id, directive_id=<id>, original_objective=..., evidence=...)` with `_call_grader` mocked to approve, and assert `_call_grader.assert_called_once()` — unchanged behavior.

Success criteria: `cd commander && python -m pytest tests/ -x` passes in full (existing suite untouched); the two new tests pass; no test file outside `commander/tests/` is touched.

## Implementation Notes

- The fast-path is opt-in per call, not automatic — a future reader should not assume this change alone fixes any in-flight or automated `kill_worker` invocation. Only calls that explicitly pass `directive_id` benefit.
- `_db` is the same `sqlite3.Connection` object used by `WorkerRegistry` (both wrap `init_db(db_path)`'s connection), so the new query uses the identical access pattern (`self._db.execute(...)`) already used elsewhere in this file (e.g. `update_directive_status`, `spawn_worker`'s drift check).
- Daemon restart after landing this fix is a deployment step, not a design concern — it belongs in the implementation plan (writing-plans), not here.
- Two approaches were considered and rejected: (a) resolving `directive_id` from a `workers.directive_id` column persisted at spawn time — infeasible within the single-file constraint and wouldn't help the three already-running blocked directives; (b) parsing `directive_id` out of `worker_id` via a naming convention (e.g. `d1310-...`) — confirmed there is no enforced convention anywhere in the codebase; `adopt_session` and `resume_session` worker registrations have no directive linkage at all, so this would silently misfire for a meaningful fraction of workers.
