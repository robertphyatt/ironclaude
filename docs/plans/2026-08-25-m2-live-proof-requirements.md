# M2 Live-Proof (/reconcile + /push, v1.1.7 deploy-proof) — Requirements (operator-approved)

> **Created:** 2026-08-25 (revised after grounding falsified the original P-2/R-2 lanes)
> **Status:** Approved
> **Scope mode:** hold. Live-proof of /reconcile + /push against the deployed v1.1.7 dist, proving the
> shipped never-lose-work recovery chain. Operator-approved: "Reconcile+push+NLW core" + T-1 + R6.

## Problem

The v1.1.7 never-lose-work fixes are committed, redeployed, and the client relaunched (MCP now loads
the v1.1.7 repo dist). `/reconcile` was never live-proved, and the committed≠deployed≠loaded trap is
only closed by exercising the deployed build end-to-end. Step-1.6 grounding corrected two design
assumptions: (a) `/push` does NOT complete a preserved obligation and no managed verb resumes it; the
shipped completion is unassigned-primary `/push` + `reconcile_finalization` observe-and-clear; (b) a
push-pending disposition cannot be seeded on an active row — it is reached via the real commit-and-push
dirty-gate path. The candidate resume-push is an explicit v1.1.7 non-goal (CHANGELOG:48-49).

## Requirements

R1. Prove against the **deployed** `worker/mcp-servers/workspace-manager/dist/*.js` via the mint→consume
    methodology, not source.

R2. **Scratch isolation MUST be airtight:** `HOME=$(mktemp -d)` (with a scratch state DB row
    `professional_mode='on'`), scratch `WORKSPACE_MANAGER_DB_PATH`, scratch repos + bare origins. Zero
    real-world contact. The managed assignment MUST be created by the real CLI `allocate` (physical
    worktree required by `verifyDirectGitAuthority`), never pure SQL.

R3. **B-Prime wrapper:** all `git` (and consume `node`) operations run inside ONE driver script at an
    absolute scratchpad path, listed verbatim in the task's `allowed_files`, invoked as `bash <abs-path>`.

R4. Lanes (each falsifiable; each NLW lane traces to the exact deployed transition function):
    - **D-1 deploy-currency:** `hasPushPendingObligation`, `finalizeReconcile`, `integrated-local`,
      `Refusing to tombstone` present in `dist/index.js`.
    - **R-1 reconcile happy:** local main advances to the worktree HEAD, worktree alive, `reconciled`,
      no push.
    - **R-2 reconcile preserves push-pending (v1.1.7 NLW proof):** reach active+integration-pending via
      the commit-and-push dirty-gate (untracked file), then `/reconcile` → `integrated-local`, disposition
      now push-pending and PRESERVED, main advanced. (Seeding push-pending on an active row is invalid —
      markIntegrated rejects it.)
    - **R-3 reconcile requires committed-clean:** dirty worktree refused; main unchanged.
    - **P-1 push happy (managed, ff-only):** origin branch ref ff-advances to local HEAD, `pushed-only`.
    - **P-2a managed /push on preserved guid refused:** mint `/push` (no guid → sentinel), consume with
      the preserved guid → `'Direct Git operation requires a matching human intent'` (the intent cannot
      bind to a terminal row) — the honest record that no managed verb resumes the candidate.
    - **P-2b unassigned-primary /push (no guid):** origin target ref (main) ff-advances to the candidate,
      `pushed-only`.
    - **P-2c reconcile_finalization → cleaned:** observes remote==candidate, clears disposition to NULL,
      recycles; worktree alive.
    - **N-1 identity negatives:** `invocationThreadId !== sessionId` refused for reconcile and push.
    - **T-1 tombstone guard:** `cleanupWorkspace` on the preserved row refused `'Refusing to tombstone'`.

R5. Deliverable: `docs/plans/2026-08-25-m2-live-proof-findings.md` (staged with `git add -f`), structured
    like `docs/plans/2026-08-22-loop3a-live-proof-findings.md`: methodology, deploy-currency, lane table,
    observed behaviors, defect list (empty only if all pass), R6 sequence, bottom line. The doc MUST
    state that the candidate resume-push + durable-anchor teardown are unshipped v1.1.7 non-goals and that
    P-2b/P-2c prove the shipped recovery chain. Real failures are recorded, never suppressed.

R6. Include a documented operator-keystroke end-to-end sequence (`/reconcile` then `/push` on a real
    managed worktree); validated unless the operator reports a failure.

## Non-goals

- No product code changes. Exact symbol/schema grounding is in the plan's Step 1.6.
- The candidate resume-push and durable-anchor teardown (CHANGELOG:48-49) are NOT proved — unshipped.
- The daemon auto-integrate-on-bare-push lane is DEFERRED (needs full daemon/tmux/registry isolation).
- Not re-proving Loop 3a's commit / commit-and-push lanes.
- The "remote advanced past candidate → ambiguous forever" residual is recorded as an observation, not
  laned.
