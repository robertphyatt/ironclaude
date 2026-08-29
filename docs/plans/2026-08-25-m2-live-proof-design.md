# M2 — Live-Proof of /reconcile + /push (v1.1.7 deploy-proof) Design

> **Created:** 2026-08-25
> **Status:** Design Complete (revised after Step-1.6 grounding + Fable verification)

## Summary

Loop 3a (2026-08-22) live-proved commit / commit-and-push / push, but NOT `/reconcile`. M2 closes that
gap and proves the v1.1.7 never-lose-work (NLW) recovery chain end-to-end against the now-relaunched
**v1.1.7** deployed dist, doubling as deploy-proof for `6d8c6e6`. It reuses Loop 3a's mint→consume
scratch-isolated methodology. Every lane traces to the exact deployed transition function; no lane
simulates unshipped behavior. The candidate **resume-push** and durable-anchor teardown are documented
v1.1.7 **non-goals** (CHANGELOG.md:48-49) and are NOT proved — instead M2 proves the recovery path that
DOES ship: preserve (reconcile) → publish (unassigned-primary /push) → observe-and-clear
(reconcile_finalization). Deliverable is a findings doc; no product code changes.

## Architecture

One B-Prime driver script at an absolute scratchpad path (listed verbatim in `allowed_files`), invoked
`bash <abs-path>` (keyword-free — the professional-mode-guard raw-matches the absolute path at :784
before the git-keyword matcher at :818, so child git/node ops are invisible to the PreToolUse hook).
Pattern from the committed `worker/hooks/tests/test-git-authority-activation.sh`.

**Scratch isolation:** `HOME=$(mktemp -d)`; a scratch state DB at `$HOME/.claude/ironclaude.db` with a
`sessions` row `professional_mode='on'` (intents require it; state-activator.sh:109); a scratch
`WORKSPACE_MANAGER_DB_PATH`; working repos + **bare** origins created with plain git. Zero real-world
contact.

**Managed assignment is created by the REAL CLI, not hand-seeded SQL:**
`WORKSPACE_MANAGER_DB_PATH=<scratch> node dist/cli.js allocate '{repository_path,workspace_guid,owner_session_id,integration_target:"main"}'`
— this produces a schema-valid row AND the physical git worktree that `verifyDirectGitAuthority` requires
(git-authority.ts:274-282: worktree_path must be `<primary>/.ironclaude/worktrees/<guid>`, branch
`ironclaude/<guid>`, and a real `git worktree`). Use the returned `worktree_path`.

**Mint:** pipe `{prompt,session_id,cwd,hook_event_name:"UserPromptSubmit",thread_source:"user"}` to the
deployed `state-activator.sh` with `HOME` + `WORKSPACE_MANAGER_DB_PATH` + `IRONCLAUDE_WORKSPACE_HOOK_INTENT`
set. A `/reconcile`, `/commit-and-push`, `/push` slash matches the `claude-user-prompt` channel; a
`thread_source:"subagent"` mints 0 intents.

**Consume:** `node --input-type=module` importing `createPublicToolDependencies` (dist/index.js) +
`initCliDb` (dist/cli.js); `deps = createPublicToolDependencies(initCliDb(), {client:'claude', sessionId:SID, invocationThreadId:SID, source:'ppid_file'})`;
call `deps.finalizeDirect('push'|'commit-and-push', args)`, `deps.reconcileWorktree(args)`,
`deps.reconcileFinalization(args)`. (`client` MUST be `'claude'` to match the mint channel — Loop 3a C1.)
There is NO `deps.push`.

## Components / Lanes

**D-1 — Deploy-currency.** grep NLW symbols in `dist/index.js`: `hasPushPendingObligation` (confirmed
=2), `finalizeReconcile`, the `integrated-local` literal, and the tombstone guard string
`Refusing to tombstone`. Non-zero counts prove the deployed dist is v1.1.7 (no skew).

**R-1 — reconcile happy path.** allocate a managed worktree; commit a change in it → mint `/reconcile`
→ `deps.reconcileWorktree` → assert local `main` advanced to the worktree HEAD, worktree alive, result
`state:'reconciled'`, no push.

**R-2 — reconcile PRESERVES a push-pending obligation (the v1.1.7 NLW proof; genuinely reachable).**
Precondition is **active + integration-pending**, reached WITHOUT SQL seeding: in the managed worktree,
stage a change AND leave an untracked file present → mint+consume `/commit-and-push` → assert it fails
the dirty gate (`finalizeLocalCommit` throws `'Managed worktree has uncommitted changes; ...'`,
integration.ts:844-846) AFTER `createExactCommit` + `setDisposition('integration-pending')` ran, so the
row is now active + integration-pending (setup assertion). Remove the untracked file. mint `/reconcile`
→ `deps.reconcileWorktree` → assert `state:'integrated-local'`, the disposition is now **push-pending
and PRESERVED** (markIntegrated upgrades integration-pending→push-pending, integration.ts:553-554; the
v1.1.7 active branch preserves it instead of recycling), local main advanced to the candidate, worktree
alive. Falsifiable: pre-v1.1.7 the reconcile active branch recycled unconditionally (CHANGELOG:15-21).
(Note: seeding push-pending directly on an active row would throw `'malformed'` at markIntegrated:550-551
— that is why the reachable dirty-gate path is used.)

**R-3 — reconcile requires a committed, clean worktree.** Uncommitted change present → mint `/reconcile`
→ assert refused with the exact dirty-tree error; local main unchanged.

**P-1 — push happy path (managed, ff-only).** A managed worktree whose branch is ahead of its own origin
ref → mint `/push` → `deps.finalizeDirect('push',...)` → assert the origin **branch** ref
(`refs/heads/ironclaude/<guid>`, git-authority.ts:437-439) ff-advances to the local HEAD, result
`state:'pushed-only'`. No commit created.

**P-2a — no managed verb resumes a preserved candidate (gap record, negative).** From R-2's preserved
`integrated` (terminal) row, `mint /push` with NO guid (the deployed mint issues the `primary:<identity>`
sentinel) → `consume push` WITH `workspace_guid=<GUID>` → assert refused
`'Direct Git operation requires a matching human intent'` (git-authority.ts:576). The intent cannot bind
to a terminal row (`hook-intent.ts:41-46` selects only non-terminal assignments), so a managed push over
the preserved row cannot even be *authorized* — strictly upstream of, and stronger than, the
`finalizeDirectAuthority` guard `'Push-only authority does not designate active managed workspace'`
(integration.ts:1001-1003), which is defense-in-depth and black-box-unreachable via the deployed mint
(covered statically by the D-1 deploy-currency grep). This lane IS the honest record that no managed verb
resumes a preserved obligation.

**P-2b — shipped completion, publish half.** From the primary checkout with the preserved row as its
only assignment, mint `/push` with **NO** `workspace_guid` (unassigned-primary lane;
`resolveUnassignedPrimaryCheckout` counts only non-terminal rows as active, git-authority.ts:308-313) →
`finalizePrimaryUnassignedPush` → assert origin's **target** ref (main) ff-advances to the candidate
(local main tip == candidate after R-2), result `state:'pushed-only'` (integration.ts:947).

**P-2c — shipped completion, observe-and-clear half.** mint the reconcile-finalization intent → consume
`deps.reconcileFinalization` → assert it observes `remote == candidate` (integration.ts:1414), clears
the disposition to NULL (:1424), recycles → `state:'cleaned'`, worktree alive. This is the shipped
recovery terminal state.

**N-1 — identity negatives.** Consume with `invocationThreadId !== sessionId` → assert reconcile and
push refused `'Direct human authority can be consumed only by the provider-root session'`
(index.ts:227-231).

**T-1 — tombstone teardown guard (v1.1.7 third-primitive, deployed).** Attempt `cleanupWorkspace` on the
preserved `integrated` push-pending row (before P-2b/P-2c resolve it) → assert refused
`'Refusing to tombstone ...'` (workspace-service.ts:730-732; deployed dist/index.js:15464-15465).
Corrects a stale memory backlog entry with live evidence.

**R6 — operator keystroke-confirm.** A documented sequence the operator runs by hand on a real managed
worktree (`/reconcile` then `/push`); treated as validated unless a failure is reported.

## Data Flow

driver sets scratch HOME + state DB (`professional_mode='on'`) + WS DB → `allocate` a real managed
worktree → per lane: mint intent via deployed `state-activator.sh` → consume via `node` import of
deployed dist → inspect git/DB state → assert. All git ops inside the driver (B-Prime). Lanes that
share the preserved obligation (R-2 → T-1 → P-2a → P-2b → P-2c) run in that order on one prepared row.

## Error Handling

- Every lane assertion failure is a RECORDED finding; the findings-doc defect list is empty only if all
  lanes pass. No silent pass, no suppression.
- Consume-time exceptions (missing intent, dirty gate, identity refusal, tombstone refusal) are captured
  and asserted against the EXACT expected error string, never swallowed.
- Scratch teardown runs AFTER the findings doc quotes results (evidence outlives cleanup).

## Testing Strategy

This IS the test — a live proof against the DEPLOYED dist, not source. Each lane asserts a concrete
git/DB state a broken deployed lane would not produce (main advanced to the exact candidate; disposition
push-pending vs nulled; origin ref == candidate; exact refusal strings; `cleaned` terminal). Each NLW
lane traces to the single deployed transition function it exercises. No lane simulates the unshipped
resume-push.

## Implementation Notes

- No product code changes. Deliverable: `docs/plans/2026-08-25-m2-live-proof-findings.md` (staged with
  `git add -f`; `docs/` is gitignored). The findings doc MUST state the resume-push + durable-anchor
  teardown are unshipped v1.1.7 non-goals (CHANGELOG:48-49) and that P-2b/P-2c prove the shipped chain.
- Consume surface (verified): `deps.finalizeDirect('push'|'commit-and-push', args)`,
  `deps.reconcileWorktree(args)`, `deps.reconcileFinalization(args)`.
- Loop 3a review gotchas carried forward: C1 consume `client:'claude'`; C2 B-Prime driver for the git
  guard; I1 `initCliDb` from `dist/cli.js`, `createPublicToolDependencies` from `dist/index.js`.
- Known residual (record as observation, do NOT lane): a remote advanced PAST the candidate makes
  `reconcile_finalization` return "ambiguous; preserving push-pending" indefinitely
  (integration.ts:1419-1421) — part of the deferred resume-push loop.
- Deferred non-goal (same as Loop 3a): the daemon auto-integrate-on-bare-push lane (needs full
  daemon/tmux/registry isolation).
