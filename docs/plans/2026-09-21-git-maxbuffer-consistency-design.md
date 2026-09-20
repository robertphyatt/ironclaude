# git maxBuffer consistency + reopen-guard robustness — Design

> **Created:** 2026-09-21
> **Status:** Design Complete
> **Type:** Consistency hardening for the finalize-recovery corpus (folds into unpushed v1.1.11 = `b8e357c`). From the full-corpus Fable end review's 6 observations.
> **Scope mode:** hold.

## Summary

The finalize-recovery corpus applied a 64 MB `GIT_MAX_BUFFER` ceiling to `runGit`/`runGitEnv` and the tell-merged path, but several direct `spawnSync('git', …)` sites still use Node's default 1 MB `maxBuffer`. An inconsistent ceiling is not a ceiling: the exact ENOBUFS class the corpus fixed can still bite through an un-ceilinged site, and a merge-detection site that ENOBUFS-degrades to "not merged" can misclassify a large, legitimately-merged commit — driving a wrong reap or a redundant re-integration. This corrective makes the ceiling universal and folds in four adjacent robustness fixes the same end review surfaced. `obs6` (C2 nulls the carried push disposition on reopen) is INTENDED behavior per the C2 requirement and is explicitly NOT changed.

## Components

### A (obs4 + universal maxBuffer) — every git spawn carries `GIT_MAX_BUFFER`

Audit result (verified against current source). Sites already carrying the ceiling: `git.ts` `runGit`(:67), `runGitEnv`(:80), `patchIdAggregateTellMerged` names(:640)/log(:652)/patch-id(:666); `integration.ts` `cumulativeBinaryEffect`(:319). Sites LACKING it, to fix:
- **git.ts:** `:55` (`git --version`), `:210` (`symbolic-ref …origin/HEAD`), `:566` (`merge-base --is-ancestor`), **`:579` (`diff --no-ext-diff` in `patchId` — large diff, the primary obs4 risk)**, `:582` (`patch-id --stable`), `:610` (`cherry`), **`:693` (`diff --binary` in `reverseApplyTellMerged` — large binary diff, the primary obs4 risk)**, `:703` (`apply --cached --check --reverse`).
- **workspace-service.ts:** **`:1318` (`merge-tree` — off-ref CAS merge, can be large)**, `:1331` (`commit-tree`), `:1342` (`update-ref`). These need `import { GIT_MAX_BUFFER } from './git.js'` (verify the exact existing import specifier/extension in-file).

**Fix:** add `maxBuffer: GIT_MAX_BUFFER` to each site's `spawnSync` options object. Rationale for the tiny-output sites too (`--version`, `symbolic-ref`, `merge-base`, `commit-tree`, `update-ref`): the requirement IS consistency — after this, a reviewer sees every git spawn carry the identical ceiling, with no per-site "is this one safe" judgment left to re-litigate. No behavior change on any bounded-output site; the change is defense-in-depth on the large-output ones.

### B (obs5) — `cumulativeBinaryEffect` error routing + temp-dir cleanup (integration.ts)

Two gaps at `:312-341`: (1) `if (result.error) throw result.error` (`:322`) throws a stderr-pipe ENOBUFS raw instead of through `gitBufferOverflowError` (inconsistent with `runGit`'s clear-error contract); (2) `mkdtempSync`(:313) and `openSync`(:315) sit OUTSIDE the `try` (`:317`), so if `openSync` throws, the `finally`'s `unlinkSync`/`rmdirSync` (inside that try) never run and the mkdtemp dir leaks.

**Fix:** (1) `if (result.error) { const overflow = gitBufferOverflowError(args, result.error); throw overflow ?? result.error; }` (mirror `runGit`; confirm `gitBufferOverflowError` is imported in integration.ts, add to the git.js import if not). (2) Restructure so the dir is always cleaned: keep `const dir = mkdtempSync(...)`, then move `const file`/`openSync` INSIDE a `try` whose `finally` does the best-effort `unlinkSync(file)` + `rmdirSync(dir)` — so an `openSync` throw still rmdir's the (empty) dir. Preserve the existing `wfdOpen` close-guard and the rfd read/hash loop exactly (byte-exact digest unchanged).

### C (obs3) — try/catch the C1 guard's target rev-parse (integration.ts `reopenForEdit`)

`:1952` `runGit(…, ['rev-parse','--verify',`${targetRef(assignment)}^{commit}`])` is not guarded. A target branch deleted after candidate minting throws a raw git error before any mutation. Semantically: an unresolvable target means the integration cannot have "landed on the target", so the row is NOT interrupted-CAS.

**Fix:** wrap the `currentTarget` resolution in try/catch; on throw, treat the target as unresolved → do NOT fire the refusal → fall through to the normal reopen path.

### D (obs2) — gate the C1 refusal on a held integration lock (integration.ts `reopenForEdit`)

The genuine interrupted-CAS shape (the one the crash-recovery branch `:2181-2217` completes via `markIntegrated`) always still HOLDS the integration lock. A reused workspace GUID can carry a STALE leaked candidate ref from a prior lifecycle (a best-effort `update-ref -d` that failed); if the current target happens to equal that stale candidate, the current guard false-refuses a legitimate reopen. Gating the refusal on lock-presence also gives a lockless landed row a forward path (reopen), closing the earlier "lockless row refused by both tools" observation.

**Fix:** before throwing the interrupted-CAS refusal, require a live `integration_locks` row for `(repository_identity, workspace_guid)` (`SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?`). Refuse only when candidate resolves AND `currentTarget === candidate` AND a lock is held. No lock → not a genuine interrupted-CAS → reopen proceeds (it clears the stale candidate ref at `:1998`).

### E (obs1) — refine the C1 refusal message (integration.ts)

With D, the guard fires only on the true lock-held interrupted-CAS. For the mainline (worktree HEAD == candidate) plain reconcile completes it; for the rare HEAD ≠ candidate sub-shape reconcile reports the repair needed. **Fix:** message → `reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen`.

## Data Flow

Unchanged. A adds an options field per call site; B restructures one function's try/finally without changing its digest contract; C adds a catch; D adds one SELECT before an existing throw; E changes a string literal.

## Error Handling

B routes ENOBUFS through the shared clear-error helper and guarantees temp-dir cleanup on every path. C converts a raw git throw into a fall-through. D narrows a refusal (never widens it). Never-discard-work is preserved: D only makes reopen PROCEED where it previously wrongly refused (reopen itself preserves work via its recovery-ref path).

## Testing Strategy

TDD where observable (vitest, `integration-cases.ts` recovery part):
- **D:** seed candidate==target with NO integration lock → `reconcileFinalization(…, reopen_for_edit)` returns `finalization-reopened-for-edit` (proceeds), candidate ref cleared. RED against the current always-refuse guard. The existing lineage-117 C1 test (lock HELD, candidate==target → refuses) must still pass (regression guard for D).
- **C:** seed candidate present + lock held but DELETE the target branch (`update-ref -d refs/heads/main` after minting) → reopen does NOT throw a raw git error; it proceeds (guard skipped on unresolved target). RED against the current un-caught rev-parse.
- **E:** assert the new message substring in D's lock-held refusal test.
- **A, B:** No tests required — A is a defense-in-depth ceiling not observable without a >64 MB diff fixture (and adds no behavior on bounded output); B's ENOBUFS/openSync-failure paths cannot be triggered without fault injection this suite doesn't do. Both rely on the full existing vitest suite passing unchanged (no-regression), and the existing leaked-temp-dir counter (`integration-cases.ts:52-55`) still asserting zero.
- Then rebuild `dist/` and run full vitest (0 failed) + full commander pytest (0 failed).

## Implementation Notes

Local tests only; no version bump (folds into v1.1.11); commit/push operator-gated; no trailers; `dist/` staged with `git add -f`; folds into `b8e357c` (a later amend, operator-gated, PM-off). New lineage — earns its own blind plan review. `obs6` (C2 disposition-null) is INTENDED and unchanged. Docs for this lineage under repo-root `docs/plans/` (the guard's writable base at the current cwd), consistent with the maxbuffer-finalize-recovery docs.
