# Automatic bidirectional worker handoff native validation continuation

Created: 2026-10-06
Loop: full
Scope: hold
Predicted answer: Yes — run required review (delegated)

Goal: Continue unfinished managed worker work on fresh alternate native provider after confirmed usage exhaustion while preserving worktree and PM progress.
Requirements: docs/plans/2026-10-05-worker-provider-handoff-requirements.md
Design: docs/plans/2026-10-06-worker-provider-handoff-continuation-design.md
Original staged work and execution history are preserved; this plan completes native validation and cumulative verification in the same authorized effort.

## Task 1: Complete native recovery and resumable controller validation

Dependencies: none

R3/R5/R7/R8/R9. Preserve latest native availability after an initial Codex usage error with no cumulative baseline, without clearing quota for stale heartbeat or quoted output. Controller revalidation must consume authenticated in-process/registry parser progress bound to exact source/native UUID/client/generation/cwd/file identity, revalidate append boundaries and latest state, and persist bounded progress across budget exhaustion. Caller-supplied status or reader state is not a substitute for trusted parser provenance. At stop boundary, newer availability or unknown evidence prevents termination and successor launch. All existing assignment holds, exclusive effect owners, native identity/process proof, PM history and human-authority boundaries remain in force.

Allowed files:

- commander/src/ironclaude/worker_quota.py
- commander/src/ironclaude/worker_handoff.py
- commander/src/ironclaude/worker_registry.py
- commander/src/ironclaude/main.py
- commander/tests/test_worker_quota.py
- commander/tests/test_worker_handoff.py
- commander/tests/test_worker_handoff_daemon.py

Steps:

1. RED: add real native-log sequences with initial error before token count then a new completed successful turn and matching quota scope; negative stale-heartbeat, partial output, abort/error, wrong turn/scope, replacement/truncated file controls. Controller bounded scanning starts from exact trusted cached parser progress, advances across repeated budgets/restart, and reaches EOF; forged caller state cannot authorize exhaustion or stop. Include paused/pre-stop validation state with source still alive. Run focused selection and record actual failures.

   ```bash
   python3 -m pytest -c /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_quota.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_daemon.py
   ```

2. GREEN: implement provider-native success proof when cumulative baseline unavailable using fresh bound turn, genuine output and clean completion plus applicable availability scope; never treat unknown baseline as zero or old positive cumulative total as fresh output. Preserve persisted reader-state compatibility conservatively. Implement trusted validation cache/progress across daemon/controller/registry paths without weakening snapshot, monotonic cursor, SHA-256 append-boundary/native identity or exclusive effect claims. Unknown/budget progress remains non-authoritative until current EOF.

3. Run focused verification and exact original required handoff selection; inspect full exit and warnings/errors.

   ```bash
   python3 -m pytest -c /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_quota.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_daemon.py
   ```

4. Run original required handoff Python suites.

   ```bash
   python3 -m pytest -c /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_registry.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_quota.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_pm_handoff.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_process.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_daemon.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_workspace_handoff_authority.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_provider_router.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_registry.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_lifecycle_logging.py
   ```

5. Parent performs task-boundary code review and testing-theatre analysis; stage only allowed source/tests.

## Task 2: Verify and review cumulative staged handoff implementation

Dependencies: 1

R1-R9. Review entire cumulative staged implementation against unchanged operator authority and original design plus continuation design. Retain baseline storage success both directions with and without Commander tasks, unavailable holds and separate fault cases. Run original required checks, report broader Vitest RPC error honestly and distinguish source tests from deployment/live proof. Remove nonbehavioral trailing blank line from original human plan if present without changing original machine plan/history. No commit, push, deploy, application restart or live worker operation.

Allowed files:

- worker/mcp-servers/workspace-manager/src/db.ts
- worker/mcp-servers/workspace-manager/src/handoff.ts
- worker/mcp-servers/workspace-manager/src/workspace-service.ts
- worker/mcp-servers/workspace-manager/src/integration.ts
- worker/mcp-servers/workspace-manager/src/__tests__/handoff.test.ts
- worker/mcp-servers/workspace-manager/src/cli.ts
- worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts
- commander/src/ironclaude/workspace_client.py
- commander/src/ironclaude/workspace_handoff_authority.py
- commander/tests/test_workspace_client.py
- commander/tests/test_workspace_handoff_authority.py
- commander/src/ironclaude/db.py
- commander/src/ironclaude/worker_registry.py
- commander/src/ironclaude/worker_quota.py
- commander/src/ironclaude/provider_router.py
- commander/src/ironclaude/provider_state.py
- commander/tests/test_worker_handoff_registry.py
- commander/tests/test_worker_quota.py
- commander/tests/test_worker_registry.py
- commander/tests/test_provider_router.py
- commander/tests/test_provider_state.py
- commander/src/ironclaude/worker_pm_handoff.py
- commander/tests/test_worker_pm_handoff.py
- commander/src/ironclaude/worker_handoff.py
- commander/src/ironclaude/worker_handoff_process.py
- commander/src/ironclaude/orchestrator_mcp.py
- commander/src/ironclaude/tmux_manager.py
- commander/tests/test_worker_handoff.py
- commander/tests/test_worker_handoff_process.py
- commander/tests/test_orchestrator_mcp.py
- commander/tests/test_tmux_manager.py
- commander/src/ironclaude/main.py
- commander/tests/test_worker_handoff_daemon.py
- commander/tests/test_lifecycle_logging.py
- README.md
- CODEX_SETUP.md
- docs/plans/2026-10-06-worker-provider-handoff-continuation-design.md
- docs/plans/2026-10-06-worker-provider-handoff-continuation.md
- docs/plans/2026-10-06-worker-provider-handoff-continuation.plan.json
- docs/plans/2026-10-05-worker-provider-handoff.md

Steps:

1. Run complete required Python handoff selection on frozen source.

   ```bash
   python3 -m pytest -c /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_registry.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_quota.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_pm_handoff.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_process.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_handoff_daemon.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_workspace_handoff_authority.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_provider_router.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_registry.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_lifecycle_logging.py
   ```

2. Run focused workspace handoff/CLI selection and explicit TypeScript compiler. Broader previously attempted five-file result remains a disclosed validation limit; repeat only for new relevant changes or a concrete diagnostic justification.

   ```bash
   npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- src/__tests__/handoff.test.ts src/__tests__/cli.test.ts
   ```

3. Explicit TypeScript noEmit check.

   ```bash
   /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit --project /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json
   ```

4. Parent completes cumulative testing-theatre/spec/code review and fresh report-only adversarial code review as justified; repairs requirement blockers through active workflow, records grade, stages exact cumulative union and verifies completed state. Preserve source-only boundary and report actual checks/limits.
