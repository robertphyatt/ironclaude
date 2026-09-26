# Seam Completes a Dead Worker Whose Assignment Is Already Resolved — Design

> **Created:** 2026-09-26
> **Status:** Design Complete
> **Scope mode:** reduction
> **Requirements:** docs/plans/2026-09-26-resolved-assignment-completion-requirements.md

## Summary

The v1.1.12 WARNING log (deployed 2026-09-26) exposed why worker `gm-core-regen-1500-r2`
was never completed:

```
abandon-rescue for gm-core-regen-1500-r2: abandon failed: workspace-manager abandon exited with exit 1: Only unresolved managed worktrees can be abandoned
```

Its workspace assignment is already `cleaned`: the orphan was preserved and reaped
earlier. Its Commander `workers` row, though, is still `running` with a dead tmux session.
The sequence repeats on every daemon tick:

1. The terminal seam probes finalization status.
2. The probe returns `not-ready`, because `reconcileFinalization`'s `status` branch
   (`integration.ts:2061-2070`) reports every lifecycle other than `integrated` and
   `ready_for_integration` as `not-ready`.
3. The router falls through to the review gates. They fail, so the seam calls
   `_abandon_rescue_worker`.
4. `abandonWorkspace` (`workspace-service.ts:674-679`) throws for any terminal lifecycle
   (`nonterminal` = not `integrated` / `abandoned` / `cleaned`) other than `abandoned`,
   which it returns as a no-op.
5. The outcome is `failure_phase=abandon`, and the worker is never completed.

When an assignment is already `cleaned` or `abandoned`, its work was already integrated or
preserved to a recovery ref. There is nothing left to rescue.

## Architecture (approach A — lifecycle-aware probe; operator-chosen)

1. **workspace-manager** (`integration.ts`): the non-mutating `status` probe returns
   `{ state: 'resolved', detail: 'Assignment lifecycle is <cleaned|abandoned>; nothing left
   to finalize or rescue.' }` for a `cleaned` or `abandoned` assignment. `'resolved'` joins
   the `FinalizationResult.state` union. `integrated` and `ready_for_integration` behave as
   before. Every other lifecycle is active and still returns `not-ready`, so `not-ready`
   means exactly "active, not ready".
2. **Seam router** (`orchestrator_mcp.py` `_finalize_and_release_worker`, the probe-first
   router at :3686): a new `elif state == "resolved":` branch, placed beside the
   `integrated` branch. It calls `self._complete_worker_if_session_dead(worker_id,
   ssh_host)` and returns `status`. There is no cleanup call, because the row is already
   resolved. Finalize and abandon are never reached.
3. **Safety:**
   - `_complete_worker_if_session_dead` completes the worker only when its tmux session is
     confirmed dead. A missing or raising liveness check means "not completed".
   - The router already downgrades a terminal request to non-terminal when the session is
     alive.
   - The seam still owns all completion; the daemon adds no `update_worker_status` call.
   - `_abandon_rescue_worker`'s own defense-in-depth probe (`:3933`) still refuses
     anything other than `not-ready`, so it can never abandon a resolved row.

## Components

- `worker/mcp-servers/workspace-manager/src/integration.ts`: the `FinalizationResult.state`
  union (:44-50) and the `status` branch (:2061-2070).
- `commander/src/ironclaude/orchestrator_mcp.py`: the router `resolved` branch (next to
  :3688-3695).
- `CHANGELOG.md` `## [Unreleased]`: a new entry, targeting v1.1.13 (v1.1.12 is pushed and
  tagged).
- A dist rebuild. Deploy by refreshing the plugin-cache dist and restarting Commander.

## Testing Strategy

- **vitest** (`src/__tests__/integration-cases.ts`, next to the `status` probe tests
  around :2702):
  - a `cleaned` row: `setup(false)`, then `UPDATE assignments SET lifecycle_status =
    'cleaned'`, returns `state: 'resolved'` with a detail naming `cleaned`;
  - an `abandoned` row returns `resolved` in the same way.

  Both fail today, because the probe returns `not-ready`. The existing active-row test
  still returns `not-ready`.
- **pytest** (`tests/test_worker_finalize_release.py`, `TestProbeFirstRouter`, mirroring
  `test_probe_integrated_*`):
  - probe `resolved` with a dead session: the worker is completed
    (`update_worker_status("w1", "completed")`), finalize and abandon are not called, and
    the output state is `resolved`;
  - probe `resolved` with a live session: the worker is NOT completed, and finalize and
    abandon are not called.

  The dead-session test fails today, because the router sends `resolved` to its
  unknown-state branch as `failure_phase=probe`.

## Implementation Notes

- The Brain-facing `recover_worker_integration` status passthrough may now report
  `resolved`. That is informational and needs no rule change, since no Brain rule
  references `not-ready`.
- Out of scope: stale repo paths (#2), the shared alert gate, "consecutive" semantics.
