# Unassigned-primary human /commit-and-push (Loop 2) Design

> **Created:** 2026-08-21
> **Status:** Design Complete
> **Scope mode:** selective
> **Epic context:** Loop 2 of the human-controlled commit/push epic. Composes the two lanes already
> shipped: the unassigned-primary COMMIT lane (Loop 2-commit, `9276569`) and the unassigned-primary
> PUSH lane (Loop 1, `9c6635c`). Operator: "/commit-and-push is human controlled." Loop 3 =
> managed-worktree "just works"; Loop 4 = interactive conflict resolution.

## Summary

A human on their primary checkout with **zero** managed assignments can `/commit` and `/push`
separately, but not `/commit-and-push` as one command. Loop 2 adds a **human-only, agent-forge-proof**
`/commit-and-push`: it commits the staged tree exactly onto the current branch (the Loop-2-commit
core), then pushes that new commit to its own `origin` ref via `--force-with-lease`,
fast-forward-only — one human intent, no review/integration envelope. If the commit lands but the
push fails, the local commit is preserved (never-lose-work) and the human re-pushes; this matches the
managed commit-and-push semantics.

The human-only guarantee is the unchanged 4-part chain (mint site `hook-intent.ts:29-36`
`invocation_source==='human'`; prompt-time evidence pinning; byte-exact single-use consumption,
5-min TTL; single-use force-with-lease). The zero-assignment/operation gates are lane-routing, not
authorization — this widens which operation a human intent may name, never who may mint one.

## Architecture

Reuse everything. The key source facts (verified against current code):
- `pushExactAuthorizedRef` (git-authority.ts:578-604) **already handles `commit-and-push`**: it hard-
  throws only for `operation==='commit'` (:579); its integrated-candidate guard (:581-587) fires only
  when `getAssignment(workspaceGuid).lifecycle_status==='integrated'` — and the unassigned sentinel
  `primary:<repo>` has NO assignment row, so `getAssignment` → undefined → the guard is skipped. It
  then revalidates (the Loop-1 `primary-unassigned` branch handles it) and, for commit-and-push,
  computes the pushed oid from the **post-commit** worktree via `assertPostCommitPushState`
  (:222-243) — which validates the current HEAD is a single-parent commit with parent==`parentOid`,
  tree==`stagedTree`, on-branch, then runs live `assertRemoteEvidence`. That is exactly the state
  `createExactCommit` leaves. → the combined lane pushes the just-created commit through the SAME push
  site, no new push code.
- So the combined finalizer = `createExactCommit(...)` (the Loop-2-commit core) then
  `pushExactAuthorizedRef(authority)` (the shared executor) + a readback classification.

## Components

1. **`hook-intent.ts:48-56`** — widen the zero-assignment route once more:
   `requestedGuid === undefined && (operation === 'commit' || operation === 'push' || operation === 'commit-and-push')`.
   Mint-site trust (`:29-36`) UNCHANGED. This is the final operation admitted to the unassigned lane.

2. **`git-authority.ts` — new `observeUnassignedCommitAndPushEvidence(path)`** → a `CommitAndPushEvidence`
   (mirrors managed `observeDirectEvidence`'s commit-and-push shape, sentinel-style): commit fields
   `checkoutMode:'primary-unassigned'`, `canonicalBranch`, `localRef=refs/heads/<branch>`,
   `stagedTree=write-tree`, `parentRef:'HEAD'`, `parentOid=rev-parse HEAD^{commit}`; remote fields
   `remoteName:'origin'`, `remoteUrl` (== `--push` URL else `denyEvidence`), `destinationRef=localRef`,
   `expectedRemoteOldOid=remoteOldOid(...)`. Then the **ff-proof over the commit's parent**: the pushed
   commit is created at finalize as a child of `parentOid`, so ff-over-parent ⟹ ff-over-child —
   `if (expectedRemoteOldOid !== null && !isAncestor(path, expectedRemoteOldOid, parentOid)) throw`
   the same `must be fast-forward` error. (Cannot reuse `assertFastForwardPush(evidence:PushEvidence)`
   directly — it keys on `evidence.localOid`, which does not exist for a not-yet-created commit; the
   inline parent-based check is the correct proof and adds one `isAncestor` call, not a push site.)

3. **`git-authority.ts` — relax the unassigned issue (:413) and verify (:451) guards** from
   `!== 'commit' && !== 'push'` to also admit `commit-and-push`; branch the evidence three ways
   (`commit`→commit, `push`→push, `commit-and-push`→the new combined observer); issue/consume/build the
   authority with `operation: input.operation`. Change the verify WeakSet add from
   `if (input.operation === 'push')` to `if (input.operation !== 'commit')` so a `commit-and-push`
   authority is also added to `usablePushAuthorizations` (mirrors the managed branch's `:527`;
   `pushExactAuthorizedRef` requires it). Generalize the "commit and push only" guard message to
   "commit, push, and commit-and-push only".

4. **`integration.ts` — new `finalizePrimaryUnassignedCommitAndPush(authority, message, hooks?)`**:
   guard `checkoutMode==='primary-unassigned' && operation==='commit-and-push'`; `requireMessage`;
   `const commit = createExactCommit(authority.worktreePath, exactCommitEvidence(authority), message)`
   (the exact-commit core — `exactCommitEvidence` accepts commit-and-push evidence since it only
   rejects `operation==='push'`); then `pushExactAuthorizedRef(authority)` inside try/`mutationError`
   with `hooks?.afterRemoteMutationBeforeResult?.()`; then `remoteRefOid` readback classified against
   the **new commit oid**: `remote === commit` → `{ state: 'pushed', integratedCommit: commit }`;
   `remote === expectedRemoteOldOid || null` → "has not proved the exact authorized commit"; else →
   "ambiguous". No integration lock / byte-equality / candidate refs / durable record. If the push
   fails, the local commit persists (never-lose-work) and the readback surfaces the failure.

5. **`index.ts`** — dispatch: extend the `primary-unassigned` branch (:282-286) to a three-way on
   operation — `push` → `finalizePrimaryUnassignedPush`, `commit-and-push` →
   `finalizePrimaryUnassignedCommitAndPush(authority, message)`, else
   `finalizePrimaryUnassignedCommit(authority, message)` (import the new fn). Make the
   `commit_and_push` tool schema (:130) require only `['repository_path', 'message']` (drop mandatory
   `workspace_guid`; `finalizeDirect` reads it via `optionalString`, so omitted GUID → unassigned,
   present GUID → managed).

## Data Flow

Human `/commit-and-push` on primary (0 assignments) → UserPromptSubmit hook (`invocation_source:'human'`)
→ zero-assignment route mints an unassigned `commit-and-push` intent (evidence from
`observeUnassignedCommitAndPushEvidence` incl. live ff-proof over parent) → the skill calls
workspace-manager `commit_and_push` (no `workspace_guid`) → `finalizeDirect` → `requireProviderRoot`
→ `verifyDirectGitAuthority` (unassigned: re-observe live, exact-match consume, WeakSet add) →
`finalizePrimaryUnassignedCommitAndPush` → `createExactCommit` (C_new, child of parentOid) →
`pushExactAuthorizedRef` (skips integrated-guard, revalidates via the unassigned branch,
`assertPostCommitPushState` validates C_new + live lease, force-with-lease pushes C_new) → readback.

## Error Handling

- **Non-ff at issuance** (remote not an ancestor of the commit's parent) → refused live in the observer.
- **Remote moved after verify** → `assertRemoteEvidence` inside `pushExactAuthorizedRef` throws
  `evidence changed or is malformed`; the local commit already landed and is preserved; readback
  surfaces it.
- **Assignment / foreign primary owner appears after verify** → the revalidate `primary-unassigned`
  branch throws (`zero active assignments` / `owned by another session`) — but note this fires AFTER
  `createExactCommit` (the local commit persists; nothing is pushed).
- **Push fails for any reason** → local commit preserved; readback → "has not proved" / "ambiguous".
- **Agent / subagent / non-UserPromptSubmit** → mint site + `requireProviderRoot`.

## Testing Strategy

TDD, vitest (`vitest run`, 30s timeout), RED before code.
- **git-authority.test.ts:** positive unassigned commit-and-push authority issues+verifies
  (checkoutMode primary-unassigned, operation commit-and-push, sentinel guid; the authority is in the
  push WeakSet — `pushExactAuthorizedRef` accepts it); ff-proof over parent refused when origin is
  ahead of HEAD; new-branch (null) allowed; hook mint route `{issued:true, operation:'commit-and-push'}`
  and **UPDATE** the zero-assignment throw-loop (remove `commit-and-push`; the remaining
  `use-primary-checkout`/`return-to-managed-worktree` still throw); commit-and-push sentinel isolation
  (a commit intent cannot satisfy a commit-and-push verify and vice versa).
- **integration-core.test.ts:** end-to-end with a bare origin — `finalizePrimaryUnassignedCommitAndPush`
  creates exactly one commit (child of prior HEAD, staged tree) AND pushes it (`ls-remote` moves to the
  new commit), `state:'pushed'`; a push failure after commit (remote advanced after verify) throws
  `evidence changed or is malformed` AND leaves the local commit in place (never-lose-work); the
  finalizer rejects a `push` or `commit` authority (guard). 
- **tool-dispatch.test.ts:** UPDATE the schema test — `commit_and_push` required →
  `['repository_path', 'message']`; the subagent omitted-guid `commit_and_push` refusal
  (`provider-root session`).
- No-regression: managed commit-and-push, the Loop-1 push lane, and the unassigned commit lane
  byte-identical; full suite green.

## Implementation Notes

- **Deploy (post-commit, LOCAL):** `npm run build` → refresh `dist/` into the claude + codex caches;
  commander per-call `cli.js` (no restart). `dist/` gitignored → commit source-only. cd back to repo
  root after the build.
- **Landmines:** never reintroduce a `workspace_guid` FK; keep the CommitAndPushEvidence shape stable
  (a shape change makes old intents unconsumable); the `|`-in-quoted-regex guard false-positive
  (single-term greps); guard bug #5 ("push"/"commit-and-push" in a doc filename blocks `git add` at
  execution_complete — stage plan docs with PM off).
- **Non-goals:** managed-worktree "just works" / commander-commit-on-primary / multi-assignment
  (Loop 3); interactive conflict resolution (Loop 4); auto-push on any autonomous path; force-push
  override (raw git with PM off is the escape).
