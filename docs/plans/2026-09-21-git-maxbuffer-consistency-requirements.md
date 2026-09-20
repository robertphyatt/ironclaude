# git maxBuffer consistency + reopen-guard robustness — Requirements

> **Created:** 2026-09-21
> **Status:** Operator-approved (scope: "All 5 as designed")
> **Source:** Full-corpus Fable end review of `b8e357c` (6 observations).

## Authority

Operator directive: "Activate pm and fix all those" + "we need to be consistent" — fix all five genuine end-review observations; `obs6` (C2 disposition-null) is intended and must NOT be reverted. Confirmed scope: "All 5 as designed" (universal maxBuffer, not targeted). Standing constraints: local tests only; no commit trailers; no push/deploy without explicit per-action go; folds into unpushed v1.1.11 `b8e357c`; no version bump; new lineage earns its own blind plan review.

## Requirements

**R-A (obs4, universal maxBuffer).** EVERY `spawnSync('git', …)` in the workspace-manager TypeScript MUST pass `maxBuffer: GIT_MAX_BUFFER`. The sites currently lacking it MUST be updated: git.ts `:55`, `:210`, `:566`, `:579`, `:582`, `:610`, `:693`, `:703`; workspace-service.ts `:1318`, `:1331`, `:1342` (importing `GIT_MAX_BUFFER` from `./git.js`). No behavior change on bounded-output sites; the ceiling closes the ENOBUFS class on the large-diff sites (`:579`, `:693`, `:1318`). Verification: a source check that no `spawnSync('git'` in git.ts / workspace-service.ts lacks `maxBuffer` (grep-based), plus the full suite unchanged.

**R-B (obs5).** `cumulativeBinaryEffect` (integration.ts) MUST route `result.error` through `gitBufferOverflowError` (as `runGit` does), and MUST clean up the `mkdtemp` dir even if `openSync` throws (no leaked temp dir on that failure path). The sha256 digest contract (byte-exact equality across the 8 call sites) MUST be preserved unchanged.

**R-C (obs3).** `reopenForEdit`'s target-ref resolution (`targetRef(assignment)^{commit}`) MUST NOT throw a raw git error when the target ref is unresolvable; an unresolvable target means the row is not interrupted-CAS, so the guard MUST be skipped and reopen proceed. Verification: a vitest case (candidate + lock present, target branch deleted) where reopen proceeds instead of throwing a raw git error. RED before the fix.

**R-D (obs2).** The C1 interrupted-CAS refusal MUST additionally require a held `integration_locks` row for `(repository_identity, workspace_guid)` — the actual crash-recovery precondition. A reused-GUID stale-leaked candidate (no lock) with an unmoved target MUST NOT be refused (reopen proceeds and clears the stale candidate). Verification: a vitest case (candidate==target, NO lock) where reopen proceeds; the existing lineage-117 C1 test (lock HELD, candidate==target → refuses) MUST still pass. RED before the fix.

**R-E (obs1).** The C1 refusal message MUST NOT over-promise that plain reconcile completes the integration for every sub-shape; it MUST direct the operator to reconcile as the correct next step ("run reconcile — it will finish the integration or report the repair needed — do not reopen"). Verification: assert the message substring in R-D's lock-held refusal test.

## Non-goals

- `obs6` (C2 disposition-null on reopen): intended per the C2 requirement — NOT changed.
- Commander (Python) git subprocess calls: out of scope (no Node maxBuffer concept; `GIT_MAX_BUFFER` is a TS constant).
- Test-file `spawnSync` sites: out of scope.

## Success criteria

R-A/R-B applied (universal ceiling; obs5 routing + cleanup); R-C/R-D/R-E vitest RED→GREEN; the existing lineage-117 C1 test and the leaked-temp-dir counter still pass; `dist/` rebuilt; full vitest 0 failed; full commander pytest 0 failed. Nothing committed/pushed without an explicit operator go.
