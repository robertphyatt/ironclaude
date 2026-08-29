# M6 — `/close-out` human verb (verb 4) Design

> **Created:** 2026-08-26
> **Status:** Design Complete (revised after blind Fable plan review + fix advisor: reworked entry model)
> **Scope:** hold (full auto-resolve incl. drain — operator-confirmed 2026-08-26)

## Summary

`/close-out` is the fourth human worktree verb: **integrate the managed worktree's current HEAD into
LOCAL main, then FULLY tear the worktree down** (remove worktree + temp branch, terminal row). Its
sibling `/reconcile` integrates and KEEPS the worktree; close-out is the reconcile that also cleans up.
It is local-only and never pushes.

The governing operator directive is that the worktree verbs must **JUST WORK — auto-resolve, never
kick work back to the operator** with "go push/commit/resolve-the-conflict first." close-out honors
four hard constraints: (1) local-only, never pushes; (2) **never push without an explicit per-action
operator go**; (3) never-lose-work; (4) never bypass review by landing unreviewed bytes on main.

**Entry model (reworked).** A blind review proved the naive "reconcile-shaped authority + recover
inside the finalizer" design unbuildable: a paused rebase detaches HEAD, and the direct-Git authority
path (`resolveEffectiveCheckout`, `observeDirectEvidence`'s `symbolic-ref`,
`revalidateAuthorizedCommitState`) requires an attached branch **at issuance** — so the `/close-out`
intent can't even be minted at prompt time; and an `integrated`+push-pending row (the persistent Case A
state) is excluded by the active-only intent query. The reworked model:
- **Evidence-light close-out intent at issuance.** close-out's `UserPromptSubmit` intent binds only
  operation/repo/guid/session via a **lifecycle-tolerant, guid-based assignment lookup** (the pattern
  the checkout verbs already use — `getWorkspaceAssignment`, workspace-service.ts:404, has no lifecycle
  filter and observes no commit/HEAD evidence). It does NOT observe commit/HEAD evidence at issuance, so
  it mints on `active`, `ready_for_integration` (incl. detached mid-rebase), and `integrated` rows.
  `verifyDirectGitAuthority` observes the actual `ReconcileEvidence` **live at verify** (after any
  recovery re-attaches HEAD) and consumes the intent matching on operation/repo/guid/session — NOT on
  an issuance-time evidence snapshot (which would be stale for a verb whose recovery deliberately moves
  HEAD).
- **Handler-orchestrated rebase recovery (before authority).** The `close_out_worktree` MCP handler,
  BEFORE `verifyDirectGitAuthority`, probes `reconcileFinalization(…, rebaseRecovery:'status')`
  (non-mutating, integration.ts:1395-1403). On `rebase-paused-conflict` it returns a preserve-and-defer
  result (worktree preserved, pending automated M7) — no authority, no operator instruction. On
  `rebase-paused-clean` it invokes the existing **authority-free** `reconcileFinalization(…, 'continue')`
  (:1246) — unmodified, so the reconcile lane stays byte-untouched (D5) — which drives the clean rebase
  to completion with HEAD ATTACHED. Then it verifies the close-out authority live and runs
  `finalizeCloseOut`.

The push-pending auto-resolution rests on a verified fact: a push-pending obligation only exists on a
row already `integrated`, and `integrated` means the candidate commit is ALREADY an ancestor of local
main (integration.ts:657). So close-out tears the worktree down while **carrying the obligation forward
on the terminal `cleaned` row**; the operator's next explicit `/push` drains it. Automatic; no push;
nothing lost.

This is the epic loop after M5; M7 (conflict Q&A) provides the interactive assisted resolution the
preserve-and-defer path awaits.

## Architecture

close-out is a new managed-only direct-Git verb whose intent is **evidence-light at issuance** and whose
MCP handler **orchestrates paused-rebase recovery before authority verification**, then runs a new
`finalizeCloseOut` that ends in teardown-carrying-obligation. The reconcile/commit/commit-and-push/push
lanes stay byte-untouched; `reconcileFinalization` and `releaseFinalized` are reused unmodified. The
schema layer already admits `close-out` (`HumanIntentOperation` types.ts:100; DB CHECK db.ts:197).

## Components

**C1 — Verb wiring + evidence-light close-out intent.** Add `close-out` at each site a managed-only
direct-Git verb needs, but route its intent through an **evidence-light, lifecycle-tolerant** path:
- `worker/hooks/state-activator.sh:54` — the human-verb prompt-match loop (add `close-out`).
- `hook-intent.ts` — a `close-out`-specific branch that resolves the assignment by guid **without the
  active-only lifecycle exclusion** (`hook-intent.ts:44` currently excludes cleaned/integrated/abandoned;
  a close-out lookup admits `active`/`ready_for_integration`/`integrated`), then issues an evidence-light
  intent (operation/repo/guid/session; no commit/HEAD evidence observed). NOT the unassigned lane at :49.
- `git-authority.ts:7` — add `'close-out'` to `DirectGitOperation`.
- `git-authority.ts` — a `close-out` issue path that does NOT call `observeDirectEvidence` (evidence-light),
  and a `verifyDirectGitAuthority` `close-out` arm that observes `ReconcileEvidence` LIVE and consumes the
  intent matching on operation/repo/guid/session (an evidence-light consume rule — the issued intent
  carries no commit evidence to equality-match). Reuse `reconcileEvidence`'s shape/validator for the live
  observation. Extend the push-exclusion (:589) to exclude `'close-out'`. `revalidateAuthorizedCommitState`
  runs at verify time (HEAD attached by then), so its `symbolic-ref` check is satisfied.
- `integration.ts` `FinalizationResult` (:32-44) — add `'closed-out'` to the state union and optional
  `pendingPush?: {candidateCommit; remoteUrl; destinationRef}` and `recovery?: {ref; residualFiles}`.
- **Security note:** evidence-light means the AUTHORITY is bound to the exact worktree/branch/commit
  observed LIVE at verify (not weaker) — issuance simply defers commit observation to verify. Negative
  tests: a `/reconcile` or `/commit` intent on an `integrated` row still refuses (the lifecycle-tolerant
  lookup is `close-out`-scoped only); the evidence-light close-out authority still binds the live worktree
  identity at verify.

**C2 — `close_out_worktree` MCP tool with handler-orchestrated recovery.** Mirror `reconcile_worktree`
(index.ts) at the five sites, but the handler is richer than reconcile's: it (1) probes
`reconcileFinalization({…, rebaseRecovery:'status'})`; (2) on `rebase-paused-conflict` returns a
preserve-and-defer result WITHOUT minting authority; (3) on `rebase-paused-clean` calls
`reconcileFinalization({…, rebaseRecovery:'continue'})` (authority-free) to auto-continue, then falls
through; (4) `verifyDirectGitAuthority({…, operation:'close-out'})`; (5) `finalizeCloseOut(db, authority)`.
Steps 1-3 use the existing agent-callable recovery tool unmodified.

**C3 — `finalizeCloseOut` (integration.ts).** By the time it runs, any paused rebase is already
continued (HEAD attached) or was deferred by the handler. It branches on lifecycle, each ending in
`closeOutRelease` (not `recycleFinalized`):
- **`active`** → `finalizeLocalCommit` → `closeOutRelease`.
- **`ready_for_integration`, no paused rebase (`frozen-no-rebase`)** → `finalizeAttestedCandidate` on the
  frozen candidate (mirror `finalizeReconcile`'s ready branch :1173-1182 up to `recycleFinalized`) →
  `closeOutRelease`.
- **`integrated`** (the persistent Case A state, or a row the handler's `continue` left integrated) →
  reconcileFinalization-style integrated-row proofs (candidate-ref equality, disposition shape, frozen-ref
  proof; reset worktree frozen→candidate if needed — mirror integration.ts:1405-1424) → `closeOutRelease`.
- Guards: `operation==='close-out'`; managed; single-use `WeakSet`; `exactAssignment` (:348, no lifecycle
  check); the live `headOid` pin (HEAD attached at verify). Do NOT modify `reconcileFinalization` or
  `finalizeReconcile`. **Edge:** an `integrated` row whose worktree is already GONE (crash between
  `removeWorktree` and the cleaned transition) — issuance fails at `listWorktrees`; handle with a DB-only
  completion (transition `integrated→cleaned`, carry disposition) returning `closed-out`, or a clear
  terminal message — no worktree op. Decided: DB-only completion (never-lose-work: the integration record
  + refs already durably hold the work).

**C4 — `closeOutRelease` (integration.ts) — the carry/teardown core (unchanged by the rework).**
Duplicates `releaseFinalized`'s proof block (leaving `releaseFinalized` and its :638 push-pending throw
byte-untouched for the Commander `disposeFinalized` caller). Requires an `integrated` row + clean tree +
reachable integration proof; then: **push-pending self-heal + carry** — `decodePushDisposition`; a
read-only `ls-remote` (`remoteRefOid`, a READ never a push); if the remote already has the candidate
`setDisposition(null)`, else record `pendingPush`; `removeWorktree` + `deleteTemporaryBranch` +
`transitionAssignment(integrated→cleaned)` (writes only lifecycle_status — db.ts:316 — so a carried
disposition survives); return `{state:'closed-out', integratedCommit, pendingPush?, recovery?}`.

**C5 — Case B dirty-worktree recovery-ref snapshot.** A dirty worktree at close-out snapshots the full
residual (tracked+untracked, `add -A` scope) to a durable `refs/ironclaude/recovery/<guid>` ref via a
**temporary index in a SCRATCH path OUTSIDE the worktree** (`GIT_INDEX_FILE` under tmpdir, so `add -A`
does not capture the index file itself; no HEAD/index/working-tree move — NOT `rescueAbandon`, which
commits residual onto the branch and would ride into main), BEFORE cleaning. The recovery ref is minted
AND re-verified to resolve BEFORE any `reset --hard HEAD` + `clean -fd`; then integrate the reviewed HEAD.
The result names the ref + residual count loudly (information, not instruction). `reset --hard HEAD` does
not move the commit, so the live headOid pin holds. Uses a `runGitEnv` helper (mirrors `runGit`'s
`spawnSync`, git.ts:28, passing `env`).

**C6 — Push-lane drain sweep (smallest possible touch; hygiene, not correctness).** After a SUCCESSFUL
push (`finalizePrimaryUnassignedPush` :954; the push-only lane :1029), a minimal sweep clears
(`setDisposition(null)`) each `cleaned` row of the same `repository_identity` whose disposition
`(remoteUrl, destinationRef)` matches the just-pushed target AND whose `candidateCommit` is contained in
the pushed oid (`isAncestor`). Hygiene only (the commit is already in local main; a normal `/push`
publishes it regardless; verified: the only reader of a cleaned-row disposition is
`tombstoneTerminalAssignment`, which handles integrated/abandoned, not cleaned). Threading `db` into
`finalizePrimaryUnassignedPush` changes its signature + its single call site (index.ts:294), so **index.ts
is in this task's files**. Must not otherwise alter the proven push lanes (D5).

**C7 — Observability.** Export a pure `pushPendingSummary(disposition)` predicate from integration.ts;
add a `listPreservedWork` surface (WorkspaceService method or inline index.ts query) selecting
`cleaned`/`abandoned` rows whose disposition is push-pending OR `recovery_ref` is non-null, mapped to
`{workspace_guid, kind:'pending-push'|'recovery', destinationRef?|ref?}`, wired to a public tool.

**C8 — No-op re-run & skill.** A `/close-out` with genuinely no assignment for the repo (the
lifecycle-tolerant lookup finds nothing) surfaces at the SKILL layer as a CLEAR, non-error terminal
message ("nothing to close out for this worktree") — never a hard failure, never go-fix-it. New
`worker/skills/close-out/SKILL.md` cloned from `reconcile`. Step 3's success contract names the ACTUAL
reachable terminal states of the reshaped finalizer: `closed-out` (normal, incl. handler-continued and
integrated-carry); `rebase-paused-conflict` and `rebase-recovery-repair-required` (worktree preserved,
pending automated M7 — never handed to the operator). It does NOT claim `cleaned`/`integrated-local`
(finalizeCloseOut never returns them — `closeOutRelease` always returns `closed-out`).

## Data Flow

human `/close-out` → state-activator → hook-intent mints an **evidence-light** close-out intent
(lifecycle-tolerant guid lookup; no commit evidence) → `close_out_worktree` handler:
1. `reconcileFinalization('status')` probe. `rebase-paused-conflict` → return preserve-and-defer (no
   authority). `rebase-paused-clean` → `reconcileFinalization('continue')` (authority-free; HEAD
   re-attached; row now active/integrated/integrated-local).
2. `verifyDirectGitAuthority(operation:'close-out')` — observes `ReconcileEvidence` LIVE (HEAD attached),
   consumes the evidence-light intent by operation/repo/guid/session.
3. `finalizeCloseOut`: Case-B snapshot if dirty → branch on lifecycle (active / ready-frozen / integrated)
   → drive to `integrated` → `closeOutRelease` (ls-remote self-heal; carry push-pending; remove worktree +
   temp branch; `integrated→cleaned`).
4. Result `closed-out` (+ `pendingPush?` + `recovery?`), or a preserve-and-defer state.

Later: the operator's explicit `/push` → C6 drain clears the carried disposition by containment.

## Error Handling

- **Never-lose-work at every seam.** Nothing removed before its content is durably anchored (recovery ref
  minted+re-verified before reset; obligation carried on the terminal row before worktree removal;
  finalization refs never deleted). Any failed proof preserves the worktree and throws.
- **Auto-resolve, never instruct.** Push-pending → carry forward. Dirty → snapshot. Mechanically-recoverable
  paused rebase → the handler auto-continues it (authority-free). A true conflict / content-changing
  resolution → preserve-and-defer to automated M7 — NEVER handed to the operator; only prose guidance on
  genuine ambiguity (standing directive).
- **Entry is total.** The evidence-light intent + handler recovery make `/close-out` enterable on active,
  ready (incl. detached mid-rebase), and integrated rows — no raw identity/missing-intent error reaches
  the operator on a legitimate close-out target.
- **No-op re-run.** No assignment → a clear non-error skill message.
- **Offline-tolerant.** The `ls-remote` self-heal is best-effort; failure carries the obligation and
  proceeds.

## Testing Strategy

vitest, workspace-manager suite (`--testTimeout=30000`). TDD RED→GREEN per task. Coverage: (1) evidence-light
close-out authority mints+verifies on active/ready/integrated rows (and negative: reconcile/commit still
refuse integrated); (2) clean happy path → `closed-out`, worktree removed, main advanced; (3) Case A —
persistent `integrated`+push-pending row (`seedIntegratedPushPending` :147) → integrated branch →
`closeOutRelease` carries the obligation; self-heal clears it when the remote already has the candidate;
intra-call conversion — `active`+integration-pending disposition (mirror :437-444) → markIntegrated
converts → carry; (4) Case B dirty → recovery-ref snapshot, residual off main, teardown completes; (5) R7 —
`rebase-paused-clean` handler auto-continues then tears down; `rebase-paused-conflict` preserve-and-defer
(worktree preserved, no operator instruction); frozen-no-rebase ready → integrates + tears down; (6) C6
drain clears a satisfied carried obligation on a matching `/push`, leaves a non-matching one; (7) integrated
row with worktree gone → DB-only completion; (8) whole-suite regression proves reconcile/commit/
commit-and-push/push/repair byte-unchanged.

## Implementation Notes

- `reconcileFinalization`, `finalizeReconcile`, `releaseFinalized`, `recoverRebaseInProgress` reused
  UNMODIFIED; close-out adds new functions only.
- The evidence-light intent is the one security-sensitive addition — the authority still binds the live
  worktree identity/commit at verify; only issuance defers commit observation. Ground the exact
  issue/consume mechanics in writing-plans Step 1.6 (the checkout-verb `getWorkspaceAssignment` +
  `issueHumanIntent`/`consumeMatchingHumanIntent` path), and cover it with the negative tests above.
- Case-B temp index MUST live outside the worktree (scratch/tmpdir).
- The `integration-pending` machinery + `markIntegrated` stay exactly as-is (M5-confirmed load-bearing).
- Grounding to pin in Step 1.6: the disposition JSON fields (candidateCommit, frozenCommit, remoteName,
  remoteUrl, destinationRef, expectedRemoteOldOid); the push-lane success points; the reconcileFinalization
  integrated-row proof block (:1405-1424) and status/continue paths; removeWorktree/deleteTemporaryBranch/
  worktreeIsClean; the exact evidence-light issue/consume rule.

## Remediation (post-end-review, 2026-08-26)

The tier-up Fable diff review found 2 confirmed critical + 2 important defects; the Fable remediation
advisor (operator-delegated) chose these fixes. This SUPERSEDES the conflicting parts of C3/C5/C7 above.

**R-C1 — never integrate a rejected-rebase resolution (security).** The `close_out_worktree` handler
must CAPTURE `reconcileFinalization('continue')`'s result and proceed to verify+`finalizeCloseOut` ONLY
when its `state ∈ {'cleaned','integrated-local'}` (an allowlist — fails closed on any future state);
any other state (notably `'rebase-recovery-repair-required'`) is returned verbatim as preserve-and-defer.
On a throw from `continue`, re-probe `reconcileFinalization('status')`; if a rebase is still paused,
return a structured `{state:'rebase-paused-conflict', detail}` (M7 hand-off); else rethrow.
**Defense-in-depth:** `finalizeCloseOut`'s `ready_for_integration` branch MUST, before
`finalizeAttestedCandidate`, require the same equality proof `recoverRebaseInProgress` holds
(integration.ts:1499-1501): `isAncestor(targetRef, headOid)` AND
`cumulativeBinaryEffect(base_commit→frozen) === cumulativeBinaryEffect(expectedTarget→headOid)`; on
failure return `{state:'rebase-recovery-repair-required'}`. Close-out's authority is evidence-light (the
human attested no commit), so this equality proof is the ONLY review guarantee — every other
`finalizeAttestedCandidate` caller either checks equality itself or binds a human-observed staged tree.

**R-C2 — preserved work must survive same-session row reuse (data-loss).** `reuseTerminalAssignment`
(db.ts:274) NULLs `disposition`/`recovery_ref` and deletes `integration_records` when the same session
(GUID = session id) reuses its `cleaned` row after a close-out — wiping a carried obligation (Case A
record) and a recovery-ref pointer (Case B). Also `refs/ironclaude/recovery/<guid>` is a FLAT unguarded
ref, so a second close-out clobbers the prior snapshot (Case B byte-loss). Chosen design (advisor option
c, scoped):
- **New additive `preserved_work` table** (columns: `workspace_guid`, `kind` `'pending-push'|'recovery'`,
  `payload` (encoded push disposition JSON / recovery ref name + residualFiles), `created_at`,
  `resolved_at`). Additive migration only — `reuseTerminalAssignment` and the reaper/tombstone lanes stay
  BYTE-UNTOUCHED (their NULLing becomes vacuous for close-out artifacts, which now live in the table).
- **Per-lifecycle immutable recovery refs:** `refs/ironclaude/recovery/<guid>-<snapshotOid>` (flat
  sibling name, content-addressed → idempotent re-snapshot), minted create-only
  (`update-ref <ref> <oid> ''`, tolerating exists-with-same-value). This also SUBSUMES R-I1 (a crash
  between reset and clean, on re-run, mints a SECOND ref; the first full snapshot + its table row survive).
- **Writers:** `closeOutRelease` inserts a `pending-push` row alongside the row-carried disposition;
  `snapshotResidualIfDirty` mints the suffixed ref + inserts a `recovery` row (may still set
  `assignments.recovery_ref` as a convenience pointer). `drainCarriedObligations` also marks matching
  `preserved_work` rows resolved. `listPreservedWork` reads the `preserved_work` table UNION the existing
  abandoned-row query (unresolved rows only).

**R-I2 — auto-heal a recoverable integrated state instead of erroring.** An integrated push-pending row
whose worktree HEAD is at `frozenCommit ≠ candidate` is a state `reconcileFinalization` HEALS
(integration.ts:1627-1633: head==frozen && clean → `reset --hard candidate`). Today the handler's
`'status'` probe returns bare `'integrated'`, skips the heal, and `closeOutRelease` throws at
`actualHead !== integrated_commit`. Extend `closeOutRelease` (NOT routing through full
`reconcileFinalization`, whose integrated branch does an unguarded `remoteRefOid` that throws offline —
Case A must tolerate offline): (1) add the dropped consistency proofs mirroring :1614-1626
(`candidateRef` resolves === `integrated_commit`; when a disposition is present,
`disposition.candidateCommit === integrated_commit` and `freezeRef` resolves === `disposition.frozenCommit`);
(2) self-heal: if `actualHead !== integrated_commit && actualHead === disposition.frozenCommit &&
worktreeIsClean` → `reset --hard integrated_commit`, recompute, proceed. A no-disposition head-mismatch
keeps the existing refusal (genuinely ambiguous, matches the proven lane).

**Cleanups:** `pushPendingSummary` excludes `push-succeeded` (only `push-pending`/`push-failed` are
outstanding); the `finalizeCloseOut` "HEAD changed since issuance" comment → "since verification"
(HEAD is observed at verify, not issuance). `requireProviderRoot` on `listPreservedWork` is optional
(read-only, owner-scoped; the proven `listActiveAssignments` also omits it) — skip.
