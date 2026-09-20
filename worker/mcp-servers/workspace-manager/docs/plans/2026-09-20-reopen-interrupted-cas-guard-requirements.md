# reopen_for_edit interrupted-CAS guard — Requirements

> **Created:** 2026-09-20
> **Status:** Operator-approved (scope: "MATERIAL + O2 + O4")
> **Source:** Final Fable adversarial end review of the finalize-recovery corpus.

## Authority

Operator directive: after the final Fable end review, fix the corrective scope **"MATERIAL + O2 + O4"** in a new lineage. O3 and O5 are explicitly backlog (out of scope). Standing constraints apply: no commit trailers; no push/deploy without an explicit per-action go; local tests only; new lineage earns its own blind plan review.

## Requirements

**R1 (MATERIAL, from the verified end-review finding).** `reopenForEdit` (`worker/mcp-servers/workspace-manager/src/integration.ts:1936`) MUST refuse an interrupted-CAS row — one where the candidate ref resolves AND the current target ref already equals the candidate (the crash-recovery state `reconcileFinalization` completes via `markIntegrated` at `integration.ts:2181-2217`). The refusal MUST happen BEFORE any mutation (no ref/lock deletion, no lifecycle change), and MUST throw a clear error directing the caller to plain reconcile:
`reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile to complete it, not reopen`.
- **Verification:** a vitest case built from the `advancedWithoutRecord` seed (`integration-cases.ts:1963`) asserting the throw AND that the candidate ref, freeze ref, and `integration_locks` row all survive and lifecycle stays `ready_for_integration`. It MUST be RED against the current no-guard handler.

**R2 (O2).** `reopenForEdit` MUST null `assignments.disposition` for the workspace inside its existing reopen transaction, so a carried `integration-pending` disposition cannot later pair with a re-minted freeze ref and throw "push refs differ".
- **Verification:** a vitest case seeding a `frozen-no-rebase` ready row with a carried disposition; after `reopen_for_edit`, `assignments.disposition` is NULL and state is `finalization-reopened-for-edit`. RED against the current handler.

**R3 (O4).** `reopen_for_edit` MUST be listed in the two places the Brain reads the recovery-action set: `recover_worker_integration`'s docstring (`commander/src/ironclaude/orchestrator_mcp.py:4174`) and the MCP tool description (`:7387`). Doc/prompt only; no test.

## Non-goals (backlog)

- O3: `countFinalizeDiffTempDirs` cross-fork flake (per-pid prefix).
- O5: `events` table unindexed on `worker_id`.
- O1: missed re-alert after a marker-less self-integrate.
- v1.1.11 F1 / observations follow-up.

## Success criteria

R1+R2 vitest cases RED→GREEN; `dist/` rebuilt; full vitest 0 failed; full commander pytest 0 failed; R3 present in both locations. Nothing committed or pushed without an explicit operator go.
