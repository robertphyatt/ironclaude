# Unassigned-primary human /push (Loop 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add a human-only `/push` lane for an unassigned primary checkout — push the current branch to
its own `origin` ref via force-with-lease, fast-forward-only, no review/integration envelope.

**Requirements:** docs/plans/2026-08-21-unassigned-primary-push-requirements.md

**Design:** docs/plans/2026-08-21-unassigned-primary-push-design.md  (rev 2 — exact code snippets live there)

**Architecture:** Reuse the managed push executor `pushExactAuthorizedRef` by teaching
`revalidateAuthorizedCommitState` a `primary-unassigned` branch (re-prove via
`resolveUnassignedPrimaryCheckout`, not `resolveEffectiveCheckout`). Task 1 = authority + mint layer +
that revalidate branch. Task 2 = the finalizer + wiring. The managed push path and its
`checkoutMode==='managed'` gate are untouched.

**Tech Stack:** TypeScript, better-sqlite3, vitest (`vitest run`, 30s timeout). Tests run against `src`.

**Execution invariants:** Bash cwd persists; repo at `/Users/roberthyatt/Code/ironclaude` (cd there —
bare `cd` is now allowlisted). Absolute paths + `git -C`. `docs/` gitignored; `dist/` gitignored
(commit source-only). vitest pass/fail is the measured RED→GREEN oracle. Run:
`npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test`.
Single-term greps (the guard's `|`-in-regex false-positive).

---

## Task 1: Unassigned-primary push authority + mint route + revalidate branch (TDD)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts:48-58`
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts` (new `observeUnassignedPushEvidence`; new exported `assertFastForwardPush`; `isAncestor` import; the `revalidateAuthorizedCommitState` primary-unassigned branch; relax `:379` + `:411`; WeakSet add; comment/error cleanup)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`

TDD. Tests first (RED), implement (GREEN), stage.

**Step 1 (RED — authority-layer tests).** In `git-authority.test.ts` add (mirroring
`unassignedDatabase`/`issueUnassigned`/`verifyUnassigned` :561-636 and `repository(withRemote)`):
- **Positive push authority (NON-VACUOUS oracle):** `repository(true)` (pushes `main` to a bare origin)
  + `unassignedDatabase`; make a NEW local commit ahead of origin (so `localOid !== remote`); issue+verify
  `operation:'push'`; assert `authority.checkoutMode==='primary-unassigned'`, `operation==='push'`,
  `workspaceGuid.startsWith('primary:')`; PRE-assert `ls-remote origin <localRef>` != local HEAD; call
  `pushExactAuthorizedRef(authority)` PLAINLY (any throw fails the test); POST-assert `ls-remote` now
  equals the new local HEAD (proves the push moved the remote — this is what the vacuous rev-1 oracle missed).
- **ff-proof — non-ff refused:** advance the bare origin's `main` past local so `expectedRemoteOldOid`
  is not an ancestor of local HEAD → issuing the unassigned `push` intent throws `'must be fast-forward'`.
- **ff-proof — new branch allowed:** a branch with no remote ref (expectedRemoteOldOid null) → issue+verify succeed.
- **Revalidate-branch negatives (prove the new branch exists + re-checks live):** (i) plant an active
  assignment for the session between verify and `pushExactAuthorizedRef` → the push throws
  `'zero active assignments'`; (ii) plant a foreign `primary_checkout_owners` row → throws
  `'owned by another session'`.
- **Sentinel isolation:** issue a `commit` unassigned intent, verify as `push` → throws
  `'requires a matching human intent'` (use `repository(true)` so verify-as-push's `remote get-url` succeeds).
- **Wrong-channel + expired-intent negatives:** `createHumanIntent` with a mismatched `humanChannel`
  and (separately) a past `expiresAt` → verify throws `'requires a matching human intent'`.
- **Hook mint route:** `issueHumanIntentFromHook(db, hookArgs(root,'push'))` (zero assignments) →
  `{issued:true, operation:'push'}`. **UPDATE the existing throw-loop test (`:729-738`, and its
  "commit only" comment):** remove `'push'` from the loop (now succeeds); the other three ops still
  throw `'exactly one active assignment'`. Do NOT weaken the `workspace_guid`-supplied refusal (`:746`).
  (The subagent-refusal negative moves to Task 2's tool-dispatch tests — `requireProviderRoot` is
  index.ts-local, unreachable here.)

Run: `npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test`
Expected: non-zero (RED) — the push-authority/ff/revalidate/hook positives fail (unassigned lane is
commit-only today; revalidate has no primary-unassigned branch; hook throws for push). Retained negatives pass.

**Step 2 (GREEN — implement).** Per design Components 1-5 (exact code there):
- `hook-intent.ts:48-58`: widen to `(operation === 'commit' || operation === 'push')`, issue with `operation`.
- `git-authority.ts`: add `isAncestor` to the `./git.js` import; add exported `assertFastForwardPush`
  and `observeUnassignedPushEvidence` (design Component 3-4); relax `issueDirectGitHumanIntent` (:379)
  and `verifyDirectGitAuthority` (:411) to admit push (branch evidence on operation, `operation:input.operation`,
  freeze-then-`usablePushAuthorizations.add` on push); update comment :281-283 + `resolveUnassignedPrimaryCheckout`
  error strings.
- **`revalidateAuthorizedCommitState` (:491-514)** — add the primary-unassigned branch:
  ```ts
  let checkoutPath: string;
  if (authority.checkoutMode === 'primary-unassigned') {
    const unassigned = resolveUnassignedPrimaryCheckout(db, authority.worktreePath, authority.providerRootSessionId);
    if (unassigned.path !== authority.worktreePath) throw new Error('Direct Git authority effective checkout changed');
    checkoutPath = unassigned.path;
  } else {
    const checkout = resolveEffectiveCheckout(db, { repositoryPath: authority.worktreePath, workspaceGuid: authority.workspaceGuid, providerRootSessionId: authority.providerRootSessionId, humanChannel: 'internal-revalidation', operation: authority.operation, expectedEvidence: authority.evidence, nonce: 'internal-revalidation' }, authority.workspaceGuid);
    if (checkout.mode !== authority.checkoutMode || checkout.path !== authority.worktreePath) throw new Error('Direct Git authority effective checkout changed');
    checkoutPath = checkout.path;
  }
  ```
  Then the existing `try { branch = symbolic-ref ...; rev-parse localRef; if branch !== canonicalBranch denyEvidence } catch {...}` uses `checkoutPath` instead of `checkout.path`.

**Step 3 (GREEN — verify).** Run the suite. Expected: `0 fail`.

**Step 4: Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/git-authority.ts worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`

**Step 5 (no-regression):** `git -C /Users/roberthyatt/Code/ironclaude diff --staged --name-only` →
exactly the three Task-1 files, nothing else.

---

## Task 2: Unassigned-primary push finalizer + wiring (TDD)

**Depends on:** Task 1.

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (new `finalizePrimaryUnassignedPush`)
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts:126` (schema), `:282-285` (dispatch)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-core.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

TDD.

**Step 1 (RED — finalizer + wiring tests).**
- `integration-core.test.ts`: `setupUnassignedPrimaryWithRemote` (setupUnassignedPrimary + bare origin);
  extend `issueAndVerifyUnassigned` to take an operation. Add: (a) **pushed-only** — ff local commit →
  `finalizePrimaryUnassignedPush(authority)` → `.state==='pushed-only'` + `ls-remote origin <localRef>`
  equals local HEAD; (b) **remote-advanced-after-verify** → the throw message contains
  `'evidence changed or is malformed'` (NOT `'must be fast-forward'`); (c) **ambiguous** via
  `afterRemoteMutationBeforeResult` hook (mirror integration-cases.ts:548-585); (d) **dispatch** —
  primary-unassigned+push routes to `finalizePrimaryUnassignedPush`, primary-unassigned+commit to
  `finalizePrimaryUnassignedCommit`.
- `tool-dispatch.test.ts`: **UPDATE** the existing it-block at `:154-164` (retitle) — push `required` →
  `['repository_path']`, `commit_and_push` still `['repository_path','workspace_guid','message']`,
  `properties` still exposes `workspace_guid`. **Add** the subagent-refuses-unassigned-push negative
  (extend `:220-244` with the omitted-guid shape `{repository_path}` → provider-root refusal). Keep the
  internal-command-surface assertions (`:87-93`, `:123-128`).
- `integration-cases.ts`: **commander-never-pushes behavioral test** — `finalizeCommanderLocalCommit`
  against a repo with a bare origin → `ls-remote origin` before == after (remote never moves).

Run the suite. Expected: non-zero (RED) — `finalizePrimaryUnassignedPush` undefined / push schema still
requires workspace_guid / existing schema it-block now wrong.

**Step 2 (GREEN — implement).** Per design Components 6-7:
- `integration.ts`: add `finalizePrimaryUnassignedPush(authority, hooks?)` — guard
  `primary-unassigned && push`; `pushExactAuthorizedRef(authority)` (try/`mutationError` +
  `afterRemoteMutationBeforeResult`); `remoteRefOid` readback (pushed-only / has-not-proved / ambiguous).
  **NO `assertFastForwardPush` call, NO `PushEvidence`/ff import.**
  ```ts
  export function finalizePrimaryUnassignedPush(authority: AuthorizedDirectGitOperation, hooks?: FinalizationHooks): FinalizationResult {
    if (authority.checkoutMode !== 'primary-unassigned') throw new Error('Not an unassigned-primary authority');
    if (authority.operation !== 'push') throw new Error('Unassigned-primary push lane pushes only');
    const evidence = authority.evidence as { localOid: string; remoteUrl: string; destinationRef: string; expectedRemoteOldOid: string | null };
    let mutationError: string | undefined;
    try { pushExactAuthorizedRef(authority); hooks?.afterRemoteMutationBeforeResult?.(); }
    catch (error) { mutationError = error instanceof Error ? error.message : String(error); }
    let remote: string | null;
    try { remote = remoteRefOid(authority.worktreePath, evidence.remoteUrl, evidence.destinationRef); }
    catch (error) { const d = error instanceof Error ? error.message : String(error); throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary push remote readback failed: ${d}`); }
    if (remote === evidence.localOid) return { state: 'pushed-only' };
    if (remote === evidence.expectedRemoteOldOid || remote === null) throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary push remote has not proved the exact authorized commit`);
    throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary push remote outcome is ambiguous`);
  }
  ```
- `index.ts`: import `finalizePrimaryUnassignedPush`; dispatch (:282-285) branch on operation; push
  schema (:126) `required` → `['repository_path']`.

**Step 3 (GREEN — verify).** Run the suite. Expected: `0 fail`; managed push + unassigned commit
byte-identical.

**Step 4: Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-core.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Step 5 (no-regression):** `git -C /Users/roberthyatt/Code/ironclaude diff --staged --name-only` →
exactly the 3 Task-1 + 5 Task-2 files = 8 paths, nothing else.

---

**Post-execution (documented, NOT plan tasks):** `npm run build` → refresh `dist/` into claude + codex
caches; commander picks it up via per-call `cli.js`. cd back to repo root. Human commits (PM on); no
push. Then Loop 2 (`/commit-and-push`).
