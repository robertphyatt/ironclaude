# Unassigned-primary human /push (Loop 1) Design

> **Created:** 2026-08-21
> **Status:** Design Complete (rev 2 — post blind-review remediation)
> **Scope mode:** selective
> **Epic context:** Loop 1 of the human-controlled commit/push epic (Fable-coached). Operator
> approved "Loop 0 then Loop 1, ff-only, no envelope." Prereqs shipped: Loop 0 migration NULL-fix
> (`40b20dc`), cwd-drift cure (`eaa294e`). Loop 2 (`9276569`) built the unassigned-primary commit lane;
> push was held back. This loop = the human-only `/push` lane for an unassigned primary checkout.
> Loop 2 = `/commit-and-push`; Loop 3 = managed-worktree gaps; Loop 4 = interactive conflict flow.
>
> **rev 2:** the blind Fable plan review + fix advisor found that the rev-1 premise "reuse
> `pushExactAuthorizedRef` generically" was false — it calls `revalidateAuthorizedCommitState` →
> `resolveEffectiveCheckout` → `getAssignment(sentinel)`, which throws for the assignment-less
> `primary:<repo>` sentinel. The fix (advisor-preferred over a dedicated executor): give
> `revalidateAuthorizedCommitState` a `primary-unassigned` branch that re-proves state via
> `resolveUnassignedPrimaryCheckout`. The reuse approach + WeakSet single-use + single push site all
> stand. Also: the finalize-time ff re-check was a can't-fail guard on frozen evidence (removed;
> ff holds at issuance/verify live + `assertRemoteEvidence` lease); R6's import-graph claim was
> unsatisfiable (reworded).

## Summary

A session on its primary checkout with **zero** managed assignments can `/commit` (Loop 2) but cannot
`/push` — three gates refuse it: the hook won't mint a push intent without exactly one assignment
(`hook-intent.ts:48-58`), the unassigned authority lane is commit-only (`git-authority.ts:379`,
`:411`), and there is no unassigned push finalizer. Loop 1 opens a **human-only, agent-forge-proof**
`/push`: it pushes the operator's **current branch** to its own `origin` ref via `--force-with-lease`,
**fast-forward-only**; no review/integration envelope (the operator is on their own branch, authorized
by their own human intent).

The human-only guarantee is a 4-part chain this loop does NOT touch: the UserPromptSubmit mint site
(`hook-intent.ts:29-36`, `invocation_source==='human'`), prompt-time evidence pinning, byte-exact
single-use consumption (5-min TTL), and the single-use force-with-lease. The zero-assignment /
commit-only gates are lane-routing, not authorization — this widens which operation a human intent may
name, never who may mint one.

## Architecture

Mirror the unassigned-primary commit lane for push, and **reuse the proven managed push executor**
`pushExactAuthorizedRef` (git-authority.ts:517-543). The executor is made usable by the unassigned
lane with a single targeted change: `revalidateAuthorizedCommitState` (git-authority.ts:491-514) —
which `pushExactAuthorizedRef` calls at :528 — gains a `primary-unassigned` branch that re-proves
state via `resolveUnassignedPrimaryCheckout` (zero active assignments, primary ownership, checked-out
branch) instead of `resolveEffectiveCheckout` (which requires an assignment row). This keeps ONE push
site, keeps the free live re-checks (`assertPushState`/`assertRemoteEvidence`: remote-URL identity +
`ls-remote`==`expectedRemoteOldOid` lease), and keeps structural single-use (the `usablePushAuthorizations`
WeakSet). The managed `finalizeDirectAuthority` push branch (its `checkoutMode==='managed'` gate) is
NOT touched — the unassigned push routes through a new `finalizePrimaryUnassignedPush`.

## Components

1. **`hook-intent.ts:48-58`** — widen the zero-assignment route: mint an unassigned intent for `push`
   as well as `commit`: `requestedGuid === undefined && (operation === 'commit' || operation === 'push')`,
   issuing with `operation` (not hard-coded `'commit'`). Mint-site trust (`:29-36`) UNCHANGED.

2. **`git-authority.ts` — `revalidateAuthorizedCommitState` (:491-514) gains a `primary-unassigned`
   branch** (the rev-2 enabler). At the top, before the existing `resolveEffectiveCheckout` path:
   ```ts
   if (authority.checkoutMode === 'primary-unassigned') {
     const unassigned = resolveUnassignedPrimaryCheckout(db, authority.worktreePath, authority.providerRootSessionId);
     if (unassigned.path !== authority.worktreePath) throw new Error('Direct Git authority effective checkout changed');
   } else {
     // existing resolveEffectiveCheckout(...) + mode/path check (:494-505)
   }
   // existing shared branch + localRef live check (:506-513) unchanged
   ```
   This re-proves at push time: zero active assignments (plant an assignment between verify and push →
   throws), primary not owned by another session, branch checked out. Branch-guarded on
   `checkoutMode`, so managed/primary authorities are unaffected.

3. **`git-authority.ts` — new `observeUnassignedPushEvidence(path)`** (sibling of
   `observeUnassignedCommitEvidence`; a `PushEvidence`: `checkoutMode:'primary-unassigned'`,
   `canonicalBranch`, `localRef=refs/heads/<branch>`, `localOid`, `remoteName:'origin'`, `remoteUrl`
   (== `--push` URL else `denyEvidence()`), `destinationRef = localRef` (current branch — no
   assignment/integration target), `expectedRemoteOldOid = remoteOldOid(path,'origin',localRef)`).
   Ends by calling `assertFastForwardPush(path, evidence)` — the ff proof, evaluated **live** here
   (before the evidence is frozen).

4. **`git-authority.ts` — new exported `assertFastForwardPush(worktreePath, evidence)`** (the one new
   safety mechanism): OK when `expectedRemoteOldOid === null` (new branch, empty lease); else require
   `isAncestor(worktreePath, expectedRemoteOldOid, localOid)` (git.ts:293) — else throw
   `'Unassigned-primary push must be fast-forward; non-fast-forward to a shared branch is refused'`.
   Called **only at issuance/verify** inside `observeUnassignedPushEvidence`, which runs on LIVE remote
   state each time (issuance in the hook, and again in `verifyDirectGitAuthority` immediately before
   finalize). It is NOT re-called on frozen evidence at finalize (that would be a can't-fail guard).

5. **`git-authority.ts:378-390` (`issueDirectGitHumanIntent` unassigned) and `:410-437`
   (`verifyDirectGitAuthority` unassigned)** — relax the `!== 'commit'` guards to also admit `push`;
   branch the evidence (`observeUnassignedPushEvidence` for push, else commit); issue/consume/build the
   authority with `operation: input.operation`. In verify, after freezing evidence + authority and
   `authorityDatabases.set(...)`, add `if (input.operation === 'push') usablePushAuthorizations.add(authority)`
   (mirror the managed ordering at :480-483 — freeze first, then add). Update the stale commit-only
   comment (:281-283) and generalize `resolveUnassignedPrimaryCheckout`'s "commit" error strings to
   "direct-Git".

6. **`integration.ts` — new `finalizePrimaryUnassignedPush(authority, hooks?)`**: guard
   `checkoutMode==='primary-unassigned' && operation==='push'`; call `pushExactAuthorizedRef(authority)`
   (inside try, capturing `mutationError`; `hooks?.afterRemoteMutationBeforeResult?.()` after);
   `remoteRefOid` readback classification mirroring `finalizeDirectAuthority`'s push branch
   (integration.ts:925-936): `remote===localOid` → `{state:'pushed-only'}`;
   `remote===expectedRemoteOldOid || null` → "has not proved the exact authorized commit"; else →
   "ambiguous". **No `assertFastForwardPush` call, no `PushEvidence`/ff import** — ff is already proven
   at verify + by `assertRemoteEvidence` inside `pushExactAuthorizedRef`. No integration lock /
   byte-equality / candidate refs / durable disposition row.

7. **`index.ts` — dispatch + schema.** `finalizeDirect` (:282-285): branch the `primary-unassigned`
   case on operation — `push` → `finalizePrimaryUnassignedPush(authority)`, else
   `finalizePrimaryUnassignedCommit(authority, message)` (import the new fn). Push tool schema (:126):
   require only `['repository_path']` (drop mandatory `workspace_guid`; `finalizeDirect` already reads
   it via `optionalString`, so omitted GUID → unassigned branch, present GUID → managed branch).

## Data Flow

Human `/push` on primary (0 assignments) → UserPromptSubmit hook (`invocation_source:'human'`) →
`issueHumanIntentFromHook` zero-assignment push route → `issueDirectGitHumanIntent` (unassigned) →
`observeUnassignedPushEvidence` (+live ff-proof) → server-held `push` intent (sentinel guid, 5-min
TTL). Then the `/push` skill calls workspace-manager `push` (no `workspace_guid`) → `finalizeDirect` →
`requireProviderRoot` → `verifyDirectGitAuthority` (unassigned push: re-observe live +ff-proof,
exact-match consume, WeakSet add) → `finalizePrimaryUnassignedPush` → `pushExactAuthorizedRef`
(revalidate via the new primary-unassigned branch → `assertPushState`/`assertRemoteEvidence` live lease
→ force-with-lease) → `remoteRefOid` readback.

## Error Handling

- **Non-fast-forward to a shared branch** → refused LIVE at issuance and at verify (`assertFastForwardPush`
  in `observeUnassignedPushEvidence`). No override.
- **Remote moved after verify** → `assertRemoteEvidence` inside `pushExactAuthorizedRef` throws
  `'Direct Git authority evidence changed or is malformed'` (caught as `mutationError`, surfaced via the
  readback classification). (This — not the removed frozen-evidence re-check — is what catches a
  post-verify remote advance.)
- **Assignment planted / primary taken over between verify and push** → the new
  `revalidateAuthorizedCommitState` primary-unassigned branch throws (`'zero active assignments'` /
  `'owned by another session'`).
- **No `origin` / detached HEAD** → `remote get-url` / `resolveUnassignedPrimaryCheckout` throw.
- **Ambiguous remote readback** → surface, never blind-retry; a successful push voids the lease so a
  duplicate attempt fail-closes on the remote-evidence mismatch.
- **Agent / subagent / non-UserPromptSubmit** → refused at the mint site + `requireProviderRoot`.

## Testing Strategy

TDD, vitest (`vitest run`, 30s timeout configured), RED before code.
- **git-authority.test.ts (authority layer):** positive unassigned push authority with a
  **non-vacuous oracle** (commit a NEW commit ahead of the pushed `main` so local ≠ remote pre-push;
  call `pushExactAuthorizedRef` plainly — ANY throw fails; assert the remote MOVES to the new oid);
  ff-proof non-ff refused at issuance (remote ahead) and new-branch (null) allowed; the new
  `revalidateAuthorizedCommitState` branch negatives (plant an active assignment between verify and
  push → `'zero active assignments'`; plant a foreign `primary_checkout_owners` row → `'owned by
  another session'`); commit-vs-push sentinel isolation (uses `repository(true)`); wrong-channel and
  expired-intent negatives (design promised them; `createHumanIntent` with a past `expiresAt`); hook
  mint route — `issueHumanIntentFromHook(hookArgs(root,'push'))` → `{issued:true, operation:'push'}`,
  and **UPDATE** the existing zero-assignment throw-loop (`:729-738`, and its "commit only" comment) to
  drop `'push'` (the other three ops still throw `'exactly one active assignment'`); do NOT weaken the
  `workspace_guid`-supplied refusal (`:746`).
- **integration-core.test.ts (finalizer + e2e):** extend `setupUnassignedPrimary` with a bare `origin`;
  pushed-only success + `ls-remote` readback; remote-advanced-after-verify → throw contains
  `'evidence changed or is malformed'` (NOT `'must be fast-forward'`); ambiguous via the
  `afterRemoteMutationBeforeResult` hook; dispatch routes primary-unassigned push → the new finalizer,
  commit → the commit finalizer.
- **tool-dispatch.test.ts:** UPDATE the existing schema it-block (`:154-164`) — push `required` →
  `['repository_path']`, `commit_and_push` unchanged; the subagent-refuses-unassigned-push negative
  (extend `:220-244` with the omitted-guid `{repository_path}` shape → provider-root refusal); the
  **commander-never-pushes** behavioral test (a `finalizeCommanderLocalCommit` against a repo with a
  bare origin → `ls-remote` before == after) plus the existing internal-command-surface assertions
  (`:87-93`, `:123-128`).
- No-regression: managed push + unassigned commit lanes byte-identical; full suite green.

## Implementation Notes

- **Deploy (post-commit, LOCAL):** `npm run build` → refresh `dist/` into the claude cache + codex
  cache; commander picks it up via per-call `cli.js` (no restart). `dist/` gitignored → commit is
  source-only. `cd` back to the repo root after the build.
- **allowed_files:** Task 1 = hook-intent.ts, git-authority.ts (incl. the revalidate branch — in this
  task's set), git-authority.test.ts. Task 2 = integration.ts, index.ts, integration-core.test.ts,
  tool-dispatch.test.ts, **and integration-cases.ts** (the commander no-remote-movement test lives in
  the shared finalization harness).
- **Landmines:** never reintroduce a `workspace_guid` FK; keep the push evidence shape stable; the
  `|`-in-quoted-regex guard false-positive (single-term greps); force-with-lease is not ff-only (hence
  the issuance ff-proof + the lease).
- **Non-goals:** `/commit-and-push` (Loop 2); managed `/push` semantics, commander-commit-on-primary,
  multi-assignment (Loop 3); interactive conflict resolution (Loop 4); auto-push on any autonomous path.
