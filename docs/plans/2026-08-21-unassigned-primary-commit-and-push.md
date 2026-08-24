# Unassigned-primary human /commit-and-push (Loop 2) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add a human-only `/commit-and-push` lane for an unassigned primary checkout — commit the
staged tree exactly onto the current branch, then push it to its own origin ref ff-only via
force-with-lease, one intent, no envelope.

**Requirements:** docs/plans/2026-08-21-unassigned-primary-commit-and-push-requirements.md

**Design:** docs/plans/2026-08-21-unassigned-primary-commit-and-push-design.md (exact code snippets there)

**Architecture:** Compose the shipped unassigned commit lane (`createExactCommit`) and push lane
(`pushExactAuthorizedRef`). `pushExactAuthorizedRef` already handles `commit-and-push` and (for the
assignment-less sentinel) computes the pushed oid from the post-commit worktree via
`assertPostCommitPushState` — so no new push site. Task 1 = authority + mint + combined evidence.
Task 2 = the combined finalizer + dispatch/schema. Managed lanes untouched.

**Tech Stack:** TypeScript, vitest (`vitest run`, 30s timeout). Tests run against `src`.

**Execution invariants:** Bash cwd persists; repo at `/Users/roberthyatt/Code/ironclaude` (`cd` there —
bare `cd` allowlisted). Absolute paths + `git -C`. `docs/` + `dist/` gitignored (commit source-only).
vitest pass/fail is the RED→GREEN oracle. Run:
`npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test`.
Single-term greps (guard `|` false-positive). Guard bug #5: staging a "-push"/"commit-and-push" doc
path is blocked at execution_complete — the source files stage fine; plan docs go in with PM off.

---

## Task 1: Unassigned-primary commit-and-push authority + mint + combined evidence (TDD)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts:48-56`
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts` (new `observeUnassignedCommitAndPushEvidence`; relax issue `:413` + verify `:451` guards to admit `commit-and-push`; 3-way evidence branch; WeakSet add `!== 'commit'`; guard message)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`

TDD.

**Step 1 (RED — authority tests).** In `git-authority.test.ts` add (mirroring the Loop-1
`issueUnassignedPush`/`verifyUnassignedPush` helpers + `repository(true)` + `unassignedDatabase`):
- **Positive commit-and-push authority:** `repository(true)` + `unassignedDatabase`; stage a file
  (`writeFileSync` + `git add`, do NOT commit — the lane commits); issue+verify `operation:'commit-and-push'`;
  assert `authority.checkoutMode==='primary-unassigned'`, `operation==='commit-and-push'`,
  `workspaceGuid.startsWith('primary:')`. (Proof it entered the push WeakSet is exercised end-to-end in
  Task 2's finalizer test.)
- **ff-over-parent — non-ff refused:** advance origin/main past HEAD (commit locally, push to origin,
  `reset --hard` back) so `expectedRemoteOldOid` is not an ancestor of the current HEAD (= the commit's
  parent) → issuing the commit-and-push intent throws `/fast-forward/`.
- **ff-over-parent — new branch allowed:** on a branch with no remote ref (null), issue+verify succeed.
- **Sentinel isolation (BOTH directions — requirements R6 "and vice versa"):** forward — a `commit`
  intent cannot satisfy a `commit-and-push` verify, and a `push` intent cannot satisfy a
  `commit-and-push` verify; reverse — a `commit-and-push` intent cannot satisfy a `commit` verify
  (`verifyUnassigned`), and a `commit-and-push` intent cannot satisfy a `push` verify
  (`verifyUnassignedPush`). All four throw `requires a matching human intent`. (Generalize the Loop-1
  `issueUnassignedPush`/`verifyUnassignedPush` helpers to take an operation, or add commit-and-push
  twins; each needs `repository(true)` + a staged uncommitted file since c-a-p evidence observes origin
  + write-tree. `consumeMatchingHumanIntent` discriminates on both the `operation` column and the
  canonical-JSON `expected_evidence` byte-match — the reverse negatives pass once written.)
- **Hook mint:** `issueHumanIntentFromHook(hookArgs(root,'commit-and-push'))` (zero assignments,
  `repository(true)`) → `{issued:true, operation:'commit-and-push'}`. **UPDATE the throw-loop** (added
  in Loop 1, currently `['commit-and-push','use-primary-checkout','return-to-managed-worktree']`) to
  remove `commit-and-push`; the remaining two still throw `exactly one active assignment`.

Run: `npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test`
Expected: non-zero (RED) — commit-and-push issue/verify throws `supports commit and push only` today;
the hook throws `exactly one active assignment` today.

**Step 2 (GREEN).** Per design Components 1-3 (exact code there):
- `hook-intent.ts:48-56`: widen to `(operation === 'commit' || operation === 'push' || operation === 'commit-and-push')`.
- `git-authority.ts`: add `observeUnassignedCommitAndPushEvidence` (CommitAndPushEvidence + inline
  ff-over-parent proof via `isAncestor`); relax issue `:413` + verify `:451` guards to
  `!== 'commit' && !== 'push' && !== 'commit-and-push'`; 3-way evidence branch (commit / push /
  commit-and-push); change verify WeakSet add to `if (input.operation !== 'commit')`; update the guard
  message to "supports commit, push, and commit-and-push only".

**Step 3 (GREEN verify).** Run the suite. Expected: `0 fail`.

**Step 4: Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/git-authority.ts worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`

**Step 5 (no-regression):** `git -C /Users/roberthyatt/Code/ironclaude diff --staged --name-only` →
exactly the three Task-1 files.

---

## Task 2: Unassigned-primary commit-and-push finalizer + wiring (TDD)

**Depends on:** Task 1.

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (new `finalizePrimaryUnassignedCommitAndPush`)
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts` (dispatch three-way; commit_and_push schema)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-core.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`

TDD.

**Step 1 (RED).**
- `integration-core.test.ts`: import `finalizePrimaryUnassignedCommitAndPush`; extend
  `issueAndVerifyUnassignedOp`'s operation union to include `'commit-and-push'`. Using
  `setupUnassignedPrimaryWithRemote` (Loop 1) + a staged (uncommitted) file, add:
  - **commit-and-push success:** capture `parentHead` + `remoteBefore`;
    `finalizePrimaryUnassignedCommitAndPush(authority, 'msg')` → `.state==='pushed'`; assert exactly one
    new commit (`HEAD^` === parentHead, `HEAD^{tree}` === the staged tree), and `ls-remote origin main`
    now equals the new HEAD (`result.integratedCommit`).
  - **never-lose-work on push failure:** capture `parentHead` before finalize; advance origin/main
    after verify **using plumbing that does NOT move local HEAD** (the Loop-1 local-commit recipe is
    WRONG here — it moves HEAD/commits the staged tree and makes `createExactCommit` throw the wrong
    error / assert vacuously). Use:
    ```ts
    const intruder = git(state.root, 'commit-tree', 'HEAD^{tree}', '-p', parentHead, '-m', 'origin advanced after verify');
    git(state.root, 'push', 'origin', `${intruder}:refs/heads/main`);
    ```
    Then `finalizePrimaryUnassignedCommitAndPush` throws `/evidence changed or is malformed/` (via the
    `mutationError` prefix on the readback throw — `createExactCommit` SUCCEEDS, then
    `assertRemoteEvidence` breaks the lease before the push runs). Assert **non-vacuously**: the local
    commit persists and IS the lane's — `HEAD^` === parentHead AND `HEAD^{tree}` === the staged tree
    (the tree captured before finalize) — and `ls-remote origin main` is still the intruder (the push
    never ran).
  - **guard:** `finalizePrimaryUnassignedCommitAndPush` throws on a `push` authority (`/commit-and-push/`)
    and `finalizePrimaryUnassignedPush` throws on a `commit-and-push` authority (`/pushes only/`).
- `tool-dispatch.test.ts`: UPDATE the schema it-block — `commit_and_push` required →
  `['repository_path', 'message']` (retitle); keep `push` at `['repository_path']`, `commit` at
  `['repository_path','message']`; `workspace_guid` property still exposed. Extend the subagent-refusal
  test with an omitted-guid `commit_and_push` (`{repository_path, message}`) → `provider-root session`.

Run the suite. Expected: non-zero (RED) — `finalizePrimaryUnassignedCommitAndPush` undefined /
commit_and_push schema still requires workspace_guid.

**Step 2 (GREEN).** Per design Components 4-5:
- `integration.ts`: add `finalizePrimaryUnassignedCommitAndPush(authority, message, hooks?)` — guard
  primary-unassigned+commit-and-push; `createExactCommit(worktreePath, exactCommitEvidence(authority),
  message)` → commit; `pushExactAuthorizedRef(authority)` in try/`mutationError` + hook; `remoteRefOid`
  readback classified vs the new `commit` (pushed / has-not-proved / ambiguous). Exact code in design.
- `index.ts`: import `finalizePrimaryUnassignedCommitAndPush`; make the primary-unassigned dispatch a
  three-way on operation; `commit_and_push` schema `required` → `['repository_path', 'message']`.

**Step 3 (GREEN verify).** Run the suite. Expected: `0 fail`; managed commit-and-push + Loop-1 push +
unassigned commit lanes byte-identical.

**Step 4: Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-core.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`

**Step 5 (no-regression):** `git -C /Users/roberthyatt/Code/ironclaude diff --staged --name-only` →
exactly the 3 Task-1 + 4 Task-2 files = 7 paths, nothing else staged.

---

**Post-execution (documented, NOT plan tasks):** `npm run build` → refresh `dist/` into claude + codex
caches; commander per-call `cli.js`. cd back to repo root. Human commits (PM on); no push. Then Loop 3.
