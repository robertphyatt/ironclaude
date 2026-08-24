# Unassigned-primary human /push (Loop 1) Requirements (operator-approved)

> **Created:** 2026-08-21 · **rev 2** (post blind-review remediation)
> **Source:** operator directive — "/commit-and-push and /push are human controlled ... this should
> always work, not just primary checkouts." Fable-coached decomposition; operator chose "Loop 0 then
> Loop 1, ff-only, no envelope." This loop = the human-only bare `/push` lane for an unassigned primary
> checkout. `/commit-and-push` is Loop 2. Human commits, no push.

## Approved scope

Open a **human-only, agent-forge-proof** `/push` for a session on its primary checkout with **zero**
managed assignments. It pushes the operator's **current branch** to its own `origin` ref via
`--force-with-lease`, fast-forward-only; no review/integration envelope. Mirrors the Loop 2
unassigned-primary commit lane and **reuses the proven managed push executor** `pushExactAuthorizedRef`.

- **R1 — mint route (hook-intent.ts:48-58).** Widen the zero-assignment route to mint an unassigned
  intent for `push` as well as `commit`. Mint-site trust (`:29-36`) UNCHANGED.

- **R2 — unassigned push authority + executor reuse (git-authority.ts).** New
  `observeUnassignedPushEvidence(path)` (PushEvidence shape; `destinationRef = localRef` = current
  branch; `remoteUrl` == `--push` URL else `denyEvidence`; ends with a LIVE `assertFastForwardPush`).
  Relax the `!== 'commit'` guards at `:379` (issue) and `:411` (verify) to admit `push`; build a
  `checkoutMode:'primary-unassigned'`, `operation:'push'` authority and `usablePushAuthorizations.add`
  it (freeze-then-add ordering). **`revalidateAuthorizedCommitState` (:491-514) gains a
  `primary-unassigned` branch** that re-proves state via `resolveUnassignedPrimaryCheckout` (zero
  active assignments, primary ownership, checked-out branch) instead of `resolveEffectiveCheckout`
  (which requires an assignment). This makes `pushExactAuthorizedRef` usable by the unassigned lane
  with ONE push site, the free live lease/identity re-checks, and structural WeakSet single-use.

- **R3 — fast-forward-only proof (git-authority.ts).** `assertFastForwardPush(worktreePath, evidence)`:
  OK when `expectedRemoteOldOid === null`; else require `isAncestor(expectedRemoteOldOid, localOid)` or
  throw a distinct non-ff error. Evaluated **LIVE at issuance and at verify** (inside
  `observeUnassignedPushEvidence`, which re-observes the live remote each call), NOT on frozen evidence
  at finalize (that would be a can't-fail guard). No override. Push-time ff is guaranteed by the
  verify-time live proof plus `assertRemoteEvidence` (live `ls-remote` == `expectedRemoteOldOid`)
  inside `pushExactAuthorizedRef`.

- **R4 — finalizer (integration.ts).** New `finalizePrimaryUnassignedPush(authority, hooks?)`: guard
  `primary-unassigned && push`; `pushExactAuthorizedRef(authority)` (capture `mutationError`;
  `afterRemoteMutationBeforeResult` hook); `remoteRefOid` readback classification (pushed-only /
  has-not-proved / ambiguous), mirroring `finalizeDirectAuthority`'s push branch (integration.ts:909-937)
  WITHOUT the assignment lookup. **No `assertFastForwardPush` call / no ff import** (ff already proven).
  No integration lock / byte-equality / candidate refs / durable disposition row.

- **R5 — dispatch + schema (index.ts).** `finalizeDirect` (:282-285) branches the primary-unassigned
  case on operation. Push tool schema (:126) requires only `['repository_path']`.

- **R6 — locked invariants (with negative tests).** UNCHANGED and proven: mint site (`:29-36`),
  `requireProviderRoot`, exact-evidence single-use consumption (5-min TTL), single-use force-with-lease,
  commit-vs-push sentinel isolation, wrong-channel + expired-intent refusal. **Commander never
  pushes** — verified by (a) the existing internal-command-surface assertions (`tool-dispatch.test.ts:87-93`,
  `:123-128`: `INTERNAL_COMMAND_NAMES` excludes push/commit-and-push; `dispatchInternalCommand('push')`
  throws) and (b) a NEW behavioral test: `finalizeCommanderLocalCommit` against a repo with a bare
  origin leaves the remote unmoved (`ls-remote` before == after). NOTE: the earlier "import-graph
  assertion that no cli.js path reaches pushExactAuthorizedRef" is DROPPED — it is unsatisfiable
  (cli.ts→integration.ts→pushExactAuthorizedRef is a real import chain); reachability ≠ invocation.
  The push schema widening admits ONLY the unassigned lane (omitted GUID; a session WITH assignments
  omitting the GUID is fail-closed by `resolveUnassignedPrimaryCheckout`'s zero-assignment check) and
  the managed lane (present GUID) — no third path.

- **R7 — TDD + no regression.** vitest, RED before code. Task 1 tests in `git-authority.test.ts`
  (authority layer, incl. the revalidate-branch negatives, non-vacuous positive-push oracle, ff pos/neg,
  hook mint route with the throw-loop UPDATE, wrong-channel/expired negatives). Task 2 tests in
  `integration-core.test.ts` (finalizer e2e + dispatch), `tool-dispatch.test.ts` (schema UPDATE +
  subagent refusal + commander-never-pushes surface), and `integration-cases.ts` (the commander
  no-remote-movement behavioral test — so `integration-cases.ts` is in Task 2's allowed_files).
  Managed push + unassigned commit lanes byte-identical. Full workspace-manager suite green; staged set
  is exactly the touched source + test files.

## Deploy (post-commit, LOCAL)

`npm run build`; refresh `dist/` into the claude + codex caches; commander picks it up via per-call
`cli.js` (no restart). `dist/` gitignored → commit source-only. `cd` back to the repo root after the build.

## Non-goals

- `/commit-and-push` from the unassigned primary (Loop 2). Managed `/push` semantics changes,
  commander-commit-on-primary, multi-assignment (Loop 3). Interactive conflict resolution (Loop 4).
  Auto-push on any autonomous path. Force-push override (raw git with PM off is the escape).
