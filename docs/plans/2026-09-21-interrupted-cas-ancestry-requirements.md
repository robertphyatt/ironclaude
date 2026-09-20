# Interrupted-CAS ancestry recovery + cleanups — Requirements

> **Created:** 2026-09-21
> **Status:** Operator-approved (scope: "Fix now" + "I1 + cleanups 1-4")
> **Source:** Fresh full-corpus adversarial review of v1.1.11 `8ed4807` (1 MATERIAL) + over-engineering review (PROPORTIONATE + 5 SAFE items).

## Authority

Operator directives: "Fix now (corrective loop)" for I1; over-engineering review requested and returned PROPORTIONATE; "I1 + cleanups 1-4" for scope. Folds into unpushed v1.1.11 `8ed4807`. Standing constraints: local tests only; no trailers; no push/deploy without explicit go; no version bump; new lineage earns its own blind plan review.

## Requirements

**R1 (I1, MATERIAL).** Interrupted-CAS recovery MUST detect "already landed" by **ancestry** (candidate reachable from the current target), not exact equality — aligning the code with its own comment ("Target reachability is sufficient durable proof", `integration.ts:2204-2205`).
- reconcile crash branch: refuse only when the candidate did NOT land (`!isAncestor(candidate, currentTarget)`); when it landed but the target advanced past it, complete the integration (`markIntegrated` + `recycle`) using the lock's `expectedTarget` for the effect proof, and SKIP the primary-checkout verify/repair (valid only when `currentTarget === candidate`; the advancing operation owns primary/ref consistency).
- `reopenForEdit` guard: refuse when the candidate landed (`isAncestor(candidate, targetRef)`) AND the lock is held; otherwise proceed. This subsumes the lineage-118 unresolvable-target try/catch (`isAncestor` returns false for an unresolvable ref).
- The row MUST NOT be stranded: a landed-then-advanced row completes as integrated via reconcile; the guard routes there rather than reopening.
- **Verification:** vitest — a landed-then-advanced seed where plain reconcile COMPLETES (RED against `===`), and where `reopen_for_edit` REFUSES preserving refs+lock (RED against the equality guard). Existing exact-target reconcile-completes and reopen-refuses cases MUST still pass.

**R2-R5 (cleanups, all SAFE, behavior-preserving).**
- **R2 (cleanup 1):** collapse `main.py`'s `_commit_failure_alerted` + `_commit_reopen_processed` into one `_finalize_marker_seen[worker_id]` episode key. Daemon marker tests port; alert-once-per-episode + re-arm-on-new-marker behavior preserved.
- **R3 (cleanup 2):** delete the `test_daemon.py` test that `del`s the marker attrs; give the `__new__`-built fixtures in `test_idle_worker_ttl.py` the new attr.
- **R4 (cleanup 3):** delete the two tautology `git.test.ts` cases (constant-equality + source-grep-for-literal); keep the >1 MB round-trip falsifiers.
- **R5 (cleanup 4):** extract a `persistRecoveryRef` helper for the recovery-ref mint duplicated between `snapshotResidualIfDirty` and `reopenForEdit`'s clean-diverged-HEAD branch.

## Non-goals

- Over-engineering review item 5 (the `gitBufferOverflowError` call on cumulativeBinaryEffect's fd-stdout spawn): kept as-is (harmless, consistent).
- No change to the exact-target recovery path, the effect-proof semantics, or `markIntegrated`.

## Success criteria

R1 vitest RED→GREEN (landed-then-advanced completes; reopen refuses); R2-R5 applied with the daemon/existing suites still green; `dist/` rebuilt; full vitest 0 failed; full commander pytest 0 failed. Nothing committed/pushed without an explicit operator go.
