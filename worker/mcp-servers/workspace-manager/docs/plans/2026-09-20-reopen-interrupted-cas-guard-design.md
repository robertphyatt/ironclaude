# reopen_for_edit — interrupted-CAS guard + disposition/discoverability fixes — Design

> **Created:** 2026-09-20
> **Status:** Design Complete
> **Type:** Corrective for the final Fable end review of the finalize-recovery corpus (1 verified MATERIAL + 2 folded observations). New lineage.
> **Scope mode:** hold.

## Summary

The final adversarial end review of the finalize-recovery corpus verified one MATERIAL defect in the new `reopen_for_edit` reconcile mode plus two observations the operator folded in. `reopen_for_edit` is the sanctioned forward path the daemon surface and the 6d wizard now steer operators to on repeated finalize failures — so it must not itself strand a row. Invariant unchanged: never discard work; reopen must always leave a sanctioned forward path.

## Components

### C1 (MATERIAL) — refuse `reopen_for_edit` on an interrupted-CAS row (`worker/mcp-servers/workspace-manager/src/integration.ts`)

`reopenForEdit` (`:1936`) admits any `frozen-no-rebase` `ready_for_integration` row. That includes the **interrupted-CAS** shape (a crash between the target CAS and `markIntegrated`): the candidate ref resolves, the target ref already equals the candidate, and the integration lock is still held — exactly the state `reconcileFinalization`'s crash-recovery branch (`:2181-2217`) completes via `markIntegrated` using the candidate + lock proof. On that row, reopen deletes the candidate ref, freeze ref, and the lock and flips lifecycle to `active` — destroying the recovery inputs. The work is not lost (already on main via the CAS, and reopen also mints a recovery ref for HEAD=candidate), but the assignment is stranded: plain reconcile then throws `No ready finalization is available` (`:2086`, lifecycle is `active`), and a re-finalize deterministically fails the cumulative-effect equality (main already holds patch-identical commits) — re-entering the "failing repeatedly → try reopen_for_edit" loop the daemon (`main.py`) and 6d wizard now recommend, because neither distinguishes failure class.

**Fix:** at the TOP of `reopenForEdit`, before any mutation, detect the interrupted-CAS row and REFUSE. Resolve the candidate ref (`candidateRef(guid)`, best-effort `rev-parse --verify`); if it resolves AND the current target ref (`rev-parse --verify <targetRef>^{commit}`) equals the candidate, `throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile to complete it, not reopen')`. This routes the operator/Brain to the correct plain-reconcile crash path, preserving the lock/candidate/freeze proof. Mirror how `reconcileFinalization` resolves `candidate`/`ref` (`:2181-2182`).

### C2 (O2) — null `assignments.disposition` on reopen (`integration.ts`)

`reopenForEdit` returns a row to `active` but does not clear `assignments.disposition`. A carried `integration-pending` disposition (set at `:1219` before the freeze, deliberately carried on active rows) then pairs with the freeze ref reopen leaves the worker to re-mint on its next `/commit`; `markIntegrated` (`:679-681`) carries the stale disposition into push-pending, and the integrated-row reconcile (`:2057-2058`) / close-out (`:1385-1387`) later throw "push refs differ". **Fix:** in reopen's `db.transaction`, also `UPDATE assignments SET disposition = NULL` for the workspace (reopen returns the row to a clean editable state, so any carried push obligation is void). A test asserting `disposition` is NULL after reopen on a row seeded with a carried disposition.

### C3 (O4) — list `reopen_for_edit` where the Brain reads the actions (`commander/src/ironclaude/orchestrator_mcp.py`)

`_RECOVERY_ACTIONS` (`:3968`) already accepts `reopen_for_edit`, but `recover_worker_integration`'s docstring (`:4174`) and the MCP tool description the Brain reads (`:7387`) still enumerate only `status`/`continue`/`abort`/`rerebase`/`restore_frozen`. **Fix:** add `reopen_for_edit` (with a one-clause description: returns a stuck reviewed row to editable `active`, preserving work) to both. Doc/prompt only; no test (behavioral).

## Data Flow

Unchanged; C1 only adds a refusal branch, C2 adds a `disposition = NULL` to reopen's existing transaction, C3 is documentation.

## Error Handling

C1 refuses BEFORE any mutation (no partial state). C2 is inside reopen's existing transaction (atomic with the lifecycle transition). Never-discard-work is upheld in all reopen paths (the C1 refusal preserves the interrupted-CAS proof rather than deleting it).

## Testing Strategy

TDD (C1/C2 vitest `integration-cases.ts`; C3 no test):
- **C1:** build the interrupted-CAS row from the existing `advancedWithoutRecord` seed (`integration-cases.ts:1963`); `reconcileFinalization(..., rebaseRecovery:'reopen_for_edit')` → `toThrow('integration already landed on the target')`; assert the candidate ref, freeze ref, and `integration_locks` row all SURVIVE (not deleted) and lifecycle stays `ready_for_integration`. RED against the current no-guard handler.
- **C2:** seed a `frozen-no-rebase` ready row with a carried `integration-pending` disposition; `reopen_for_edit` → assignment `disposition` is NULL, state `finalization-reopened-for-edit`. RED against the current handler (disposition unchanged).
- Then rebuild `dist/` and run full vitest (0 failed) + full commander pytest (0 failed).

## Implementation Notes

Local tests only; no version bump; commit/push operator-gated; no trailers; `dist/` staged with `git add -f`. New lineage — earns its own blind plan review. NOTE: this design doc is being authored at `worker/mcp-servers/workspace-manager/docs/plans/` because the professional-mode-guard's writable docs base tracks the current Bash cwd (workspace-manager, from the prior dist rebuild) and `cd` is blocked in brainstorming; the plan/requirements docs and `design_file`/`requirements_file` references will be relocated to `commander/docs/plans/` (the MCP resolution root) during the executing stage where `cd` is permitted, matching the earlier corrective-loop docs. `allowed_files` are git-root-relative regardless. Out of scope (backlog): O3 (`countFinalizeDiffTempDirs` cross-fork flake — per-pid prefix), O5 (`events` unindexed on `worker_id` — add an index), O1 (missed re-alert after a marker-less self-integrate), and the v1.1.11 F1/observations follow-up.
