# Unassigned-primary human /commit-and-push (Loop 2) Requirements (operator-approved)

> **Created:** 2026-08-21
> **Source:** operator directive — "/commit-and-push and /push are human controlled." Loop 1 shipped
> the human /push lane (`9c6635c`); this loop adds the composed `/commit-and-push`. Human commits, no
> push (the agent never pushes; a human UserPromptSubmit authorizes it). Scope: selective.

## Approved scope

A **human-only, agent-forge-proof** `/commit-and-push` for a session on its primary checkout with
**zero** managed assignments: commit the staged tree exactly onto the current branch, then push that
new commit to its own `origin` ref via `--force-with-lease`, fast-forward-only, in one human intent,
with NO review/integration envelope. Composes the shipped unassigned commit lane (`createExactCommit`)
and push lane (`pushExactAuthorizedRef`), reusing the SINGLE push site (no new push code).

- **R1 — mint route (hook-intent.ts:48-56).** Widen the zero-assignment route to admit
  `commit-and-push` in addition to `commit`/`push`. Mint-site trust (`:29-36`) UNCHANGED.

- **R2 — combined evidence (git-authority.ts).** New `observeUnassignedCommitAndPushEvidence(path)` →
  a `CommitAndPushEvidence` (commit fields: checkoutMode `primary-unassigned`, canonicalBranch,
  localRef, stagedTree, parentRef `HEAD`, parentOid; remote fields: remoteName `origin`, remoteUrl ==
  `--push` URL else `denyEvidence`, destinationRef = localRef, expectedRemoteOldOid). Ends with a
  **fast-forward proof over the commit's parent**: the pushed commit is created at finalize as a child
  of parentOid, so ff-over-parent ⟹ ff-over-child — `if (expectedRemoteOldOid !== null &&
  !isAncestor(path, expectedRemoteOldOid, parentOid)) throw` the same "must be fast-forward" error.
  (Cannot reuse `assertFastForwardPush(evidence:PushEvidence)` — it keys on `localOid`, which does not
  exist pre-commit.)

- **R3 — relax the unassigned guards + WeakSet (git-authority.ts).** Relax the issue (`:413`) and
  verify (`:451`) `!== 'commit' && !== 'push'` guards to also admit `commit-and-push`; three-way
  branch the evidence (commit/push/commit-and-push); build the authority with `operation:
  input.operation`. Change the verify WeakSet add from `if (operation === 'push')` to
  `if (operation !== 'commit')` so a `commit-and-push` authority is added to
  `usablePushAuthorizations` (mirrors the managed branch `:527`; `pushExactAuthorizedRef` requires it).
  Update the guard message to name commit, push, and commit-and-push.

- **R4 — combined finalizer (integration.ts).** New
  `finalizePrimaryUnassignedCommitAndPush(authority, message, hooks?)`: guard `primary-unassigned &&
  commit-and-push`; `requireMessage`; `createExactCommit(worktreePath, exactCommitEvidence(authority),
  message)` → the new commit; then `pushExactAuthorizedRef(authority)` (its commit-and-push branch:
  skips the integrated-guard for the assignment-less sentinel, revalidates via the Loop-1
  `primary-unassigned` branch, computes the pushed oid from the post-commit worktree via
  `assertPostCommitPushState`, force-with-lease pushes it) inside try/`mutationError` with
  `afterRemoteMutationBeforeResult`; then `remoteRefOid` readback classified against the NEW commit oid:
  `remote===commit` → `{ state:'pushed', integratedCommit: commit }`; `remote===expectedRemoteOldOid
  || null` → "has not proved the exact authorized commit"; else → "ambiguous". No integration lock /
  byte-equality / candidate refs / durable record. **Never-lose-work:** if the push fails, the local
  commit persists and the readback surfaces the failure (same as managed commit-and-push).

- **R5 — dispatch + schema (index.ts).** Extend the `primary-unassigned` dispatch (`:282-286`) to a
  three-way on operation (push → push finalizer, commit-and-push → the new finalizer with `message`,
  else commit finalizer). Make the `commit_and_push` tool schema (`:130`) require only
  `['repository_path', 'message']` (drop mandatory `workspace_guid`; `finalizeDirect` reads it via
  `optionalString` — omitted GUID → unassigned, present GUID → managed).

- **R6 — locked invariants (with negative tests).** UNCHANGED and proven: mint site,
  `requireProviderRoot` (subagent refused, incl. the omitted-guid commit-and-push shape), exact-evidence
  single-use consumption (5-min TTL), single-use force-with-lease, sentinel isolation (a commit intent
  cannot satisfy a commit-and-push verify and vice versa), the commander `cli.js` never pushes. The
  schema widening admits ONLY the unassigned lane (omitted GUID, fail-closed by the zero-assignment
  check) and the managed lane (present GUID) — no third path.

- **R7 — TDD + no regression.** vitest, RED before code. git-authority.test.ts (authority: positive
  commit-and-push issue/verify + WeakSet acceptance, ff-over-parent pos/neg, hook mint + throw-loop
  UPDATE, sentinel isolation), integration-core.test.ts (e2e: exactly one commit child-of-HEAD with
  the staged tree AND the remote moves to it, state 'pushed'; push-fail-after-commit preserves the
  local commit; finalizer rejects push/commit authorities), tool-dispatch.test.ts (schema UPDATE +
  subagent commit-and-push refusal). Managed commit-and-push + Loop-1 push + unassigned commit lanes
  byte-identical. Full suite green; staged set is exactly the touched source + test files.

## Deploy (post-commit, LOCAL)

`npm run build` → refresh `dist/` into the claude + codex caches; commander per-call `cli.js` (no
restart). `dist/` gitignored → commit source-only. `cd` back to the repo root after the build.

## Non-goals

- Managed-worktree "just works" / commander-commit-on-primary / multi-assignment (Loop 3).
  Interactive conflict resolution (Loop 4). Auto-push on any autonomous path. Force-push override.
