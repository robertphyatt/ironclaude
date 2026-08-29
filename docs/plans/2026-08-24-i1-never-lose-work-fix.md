# I-1 Never-Lose-Work Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop two active finalizer branches from silently destroying a push-pending obligation, add a structural backstop, and ship it as v1.1.7.

**Requirements:** docs/plans/2026-08-24-i1-never-lose-work-fix-requirements.md

**Design:** docs/plans/2026-08-24-i1-never-lose-work-fix-design.md

**Architecture:** Approach A — `finalizeReconcile`'s active branch gains the same `decodePushDisposition` guard its repair branch already has; `finalizeDirectAuthority`'s `/commit` branch delegates to the vetted `finishLocalIntegration` helper. Option C — `recycleFinalized` and `releaseFinalized` throw when the fresh row still carries a push-pending disposition, making never-lose-work structural.

**Tech Stack:** TypeScript, vitest (`worker/mcp-servers/workspace-manager`), Python (`commander/tests`).

**Test harness note:** `src/__tests__/integration-cases.ts` is NOT a collected test file — it exports `registerFinalizationTests(part)`, run via `integration-core.test.ts` (`part='core'`, the region ~:269-1511) and `integration-recovery.test.ts`. New tests go inside that helper's `describe` blocks; targeted `-t` runs must name `src/__tests__/integration-core.test.ts`. The file-local `git(cwd, ...args)` helper (~:37) returns trimmed stdout.

**Execution invariants (blind-reviewer contract):** Shell state does not persist between steps; use absolute paths. Execution cwd is `commander/`; vitest steps `cd` to the workspace-manager package absolutely. `docs/` is gitignored (`git add -f`). Every new test asserts a value that flips when its guard is deleted. No `expected:` below is a predicted count — each is a TDD RED/GREEN state.

---

## Task 1: Active-branch preservation fix + falsifiable tests

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (`finalizeReconcile` active branch ~1144-1148; `finalizeDirectAuthority` `/commit` branch ~1070-1073)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Step 1: Write the reconcile active-branch RED test.**

Add inside the `describe('reconcile')` block (~:315-413; setup template :316-320, assertion template :401-411). Commit first (reconcile requires a clean tree and integrates the existing HEAD), then seed the disposition, then issue authority (`reconcileEvidence` reads live HEAD at mint — the commit MUST precede issuance):

```ts
it('preserves a push-pending disposition through an ACTIVE reconcile instead of recycling', () => {
  const s = setup(true);
  git(s.assignment.worktree_path, 'commit', '-m', 'work to reconcile');
  const head = git(s.assignment.worktree_path, 'rev-parse', 'HEAD');
  s.database.prepare('UPDATE assignments SET disposition = ? WHERE workspace_guid = ?').run(
    JSON.stringify({
      phase: 'integration-pending', frozenCommit: head, remoteName: 'origin',
      remoteUrl: 'file:///unused-in-active-preserve', destinationRef: 'refs/heads/main',
      expectedRemoteOldOid: null,
    }),
    s.assignment.workspace_guid,
  );
  expect(s.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
    .get(s.assignment.workspace_guid)).toMatchObject({
      lifecycle_status: 'active', disposition: expect.stringContaining('integration-pending'),
    });
  const authority = issueAndVerify(s.database, s.assignment, 'reconcile', reconcileEvidence(s.assignment));

  const result = finalizeReconcile(s.database, authority);

  expect(result.state).toBe('integrated-local');
  expect(existsSync(s.assignment.worktree_path)).toBe(true);
  expect(s.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
    .get(s.assignment.workspace_guid)).toMatchObject({
      lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
    });
  expect(s.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
    .get(s.assignment.workspace_guid)).toBeTruthy();
});
```

Do NOT advance `root` main in this test (it would trip the merge-base gate at ~:753-756).

**Step 2: Write the `/commit` active-branch RED test.**

Same block. No pre-commit — `createExactCommit` commits the staged tree; the seeded `frozenCommit` is inert on the `commit` path:

```ts
it('preserves a push-pending disposition through an ACTIVE /commit instead of recycling', () => {
  const s = setup(true);
  const head = git(s.assignment.worktree_path, 'rev-parse', 'HEAD');
  s.database.prepare('UPDATE assignments SET disposition = ? WHERE workspace_guid = ?').run(
    JSON.stringify({
      phase: 'integration-pending', frozenCommit: head, remoteName: 'origin',
      remoteUrl: 'file:///unused-in-active-preserve', destinationRef: 'refs/heads/main',
      expectedRemoteOldOid: null,
    }),
    s.assignment.workspace_guid,
  );
  const authority = issueAndVerify(s.database, s.assignment, 'commit', commitEvidence(s.assignment));

  const result = finalizeDirectAuthority(s.database, authority, 'reviewed commit');

  expect(result.state).toBe('integrated-local');
  expect(existsSync(s.assignment.worktree_path)).toBe(true);
  expect(s.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
    .get(s.assignment.workspace_guid)).toMatchObject({
      lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
    });
});
```

Adapt `setup`/`issueAndVerify`/`reconcileEvidence`/`commitEvidence`/`git` to their live signatures if they differ.

**Step 3: Run the two RED tests — verify they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts -t "ACTIVE"
```

Expected: both new tests run and FAIL — reconcile returns `state: 'reconciled'`, `/commit` returns `state: 'cleaned'`, each with `disposition` NULL and the `integration_records` row deleted (the unguarded recycle). A "No test files found" error means the wrong target — fix the path, not the test.

**Step 4: Implement the reconcile active-branch guard.**

`finalizeReconcile` active branch currently:
```ts
  if (exact.assignment.lifecycle_status === 'active') {
    const local = finalizeLocalCommit(db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, headOid);
    recycleFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'reconciled', integratedCommit: local.integratedCommit };
  }
```
Insert the guard (identical to the repair branch below it):
```ts
  if (exact.assignment.lifecycle_status === 'active') {
    const local = finalizeLocalCommit(db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, headOid);
    if (decodePushDisposition(local.assignment.disposition)) {
      return { state: 'integrated-local', integratedCommit: local.integratedCommit, pushError: 'Remote has not proved the exact integrated candidate' };
    }
    recycleFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'reconciled', integratedCommit: local.integratedCommit };
  }
```

**Step 5: Implement the `/commit` active-branch delegation.**

`finalizeDirectAuthority` `/commit` branch currently:
```ts
  if (authority.operation === 'commit') {
    recycleFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'cleaned', integratedCommit: local.integratedCommit };
  }
```
Replace with:
```ts
  if (authority.operation === 'commit') {
    return finishLocalIntegration(db, local);
  }
```

**Step 6: Run the two tests — verify GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts -t "ACTIVE"
```

Expected: both PASS (`integrated-local`, disposition `push-pending` preserved, worktree present, `integration_records` row present).

**Step 7: Run the full workspace-manager suite — verify no regression.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: all tests pass.

**Step 8: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```

---

## Task 2: Option C structural backstop + falsifiable test

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (`export` `releaseFinalized` at ~:617; backstop in `recycleFinalized` after its re-read guard ~:574-576; backstop in `releaseFinalized` after its re-read guard ~:621-623)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Depends on:** Task 1.

**Step 1: Export `releaseFinalized` and import it in the test file.**

- In `integration.ts` change `function releaseFinalized(` (~:617) to `export function releaseFinalized(`.
- In `integration-cases.ts` add `releaseFinalized` to the `../integration.js` import list (~:22-29), alongside `recycleFinalized`.

**Step 2: Write the backstop RED test.**

Add in the core region (neighbor: the recycle-refusal test ~:500-508), using the existing `seedIntegratedPushPending(s)` helper (~:146-169; requires `setup(true)` — it reads `origin`). Pass `s.root` and `s.assignment` (both functions re-read by `workspace_guid`):

```ts
it('refuses to recycle or release an integrated row that still carries a push-pending obligation', () => {
  const s = setup(true);
  seedIntegratedPushPending(s);
  expect(() => recycleFinalized(s.database, s.root, s.assignment)).toThrow('push-pending obligation');
  expect(() => releaseFinalized(s.database, s.root, s.assignment)).toThrow('push-pending obligation');
  expect(existsSync(s.assignment.worktree_path)).toBe(true);
  expect(s.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
    .get(s.assignment.workspace_guid)).toMatchObject({
      lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
    });
  expect(s.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
    .get(s.assignment.workspace_guid)).toBeTruthy();
});
```

**Step 3: Run the RED test — verify it fails** (no throw today; `recycleFinalized` recycles and nulls the disposition).

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts -t "push-pending obligation"
```

Expected: FAIL — `recycleFinalized` returns without throwing.

**Step 4: Add the backstop to `recycleFinalized`.**

After the integrated-row guard (`if (!current || current.lifecycle_status !== 'integrated' || !current.integrated_commit) throw ...`), before `const integratedCommit = current.integrated_commit;`, insert:
```ts
  if (decodePushDisposition(current.disposition)) {
    throw new Error('Refusing to discard a push-pending obligation; resolve or push it first');
  }
```

**Step 5: Add the backstop to `releaseFinalized`.**

After its integrated-row guard (`if (!current || ... ) throw 'Release requires a durable integrated assignment...'`), before the `worktreeIsClean` check, insert the identical block:
```ts
  if (decodePushDisposition(current.disposition)) {
    throw new Error('Refusing to discard a push-pending obligation; resolve or push it first');
  }
```

**Step 6: Run the test — verify GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts -t "push-pending obligation"
```

Expected: PASS (both throw; row `integrated` + `push-pending`, `integration_records` present, worktree survives).

**Step 7: Run the full suite — verify no legitimate caller trips the backstop.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: all pass. Every correct recycle caller nulls or guards the disposition first. A failure here is a real defect to surface, not a test to loosen.

**Step 8: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```

---

## Task 3: O-2 comment accuracy + O-3/O-4 message clarity

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts` (comment ~:1114-1118)
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (`finalizeReconcile` refusals ~:1137, ~:1160)

**Depends on:** Task 2.

**No tests required:** comment-only and refusal-message-only. Neither target message is asserted (verified: `grep` of `src/__tests__/` for both strings is empty). `git-authority.ts` is untouched by Tasks 1-2, so its line numbers are stable through execution.

**Step 1: Fix the O-2 stale line references.**

Read `git-authority.ts` and confirm three current lines: single-use push gate (`throw new Error('Direct Git push authority is single-use')`) at `:640`; operation guard (`if (authority.operation === 'commit') throw ...`) at `:639`; verify-time exclusion (`if (input.operation !== 'commit' && input.operation !== 'reconcile') usablePushAuthorizations.add(...)`) at `:589`. Verify each by reading it, then rewrite the comment at `git-authority.test.ts:1114-1118` to:
```ts
      // This exact message fires only at the single-use gate (:640) — a reconcile
      // authority reaches it because the operation guard above (:639) rejects only
      // 'commit' (it does NOT special-case 'reconcile') and the authority was
      // excluded from usablePushAuthorizations at verify-time (:589). A bare
      // .toThrow() would also pass if :639 threw its own, differently-worded error
      // — pin the exact text to catch that.
```

**Step 2: Tighten the two reconcile refusal messages (O-3/O-4).**

Replace (~:1137):
```ts
  if (authority.checkoutMode !== 'managed') throw new Error('Reconcile is only valid for a managed worktree');
```
with:
```ts
  if (authority.checkoutMode !== 'managed') throw new Error('Reconcile is only valid for a managed worktree; there is no primary or unassigned reconcile lane');
```
and replace (~:1160):
```ts
  throw new Error('Reconcile requires an active or ready-for-integration managed assignment; run reconcile_finalization to recover');
```
with:
```ts
  throw new Error('Reconcile needs an active or paused-for-integration managed assignment; if a prior finalize is frozen, run reconcile_finalization first, then re-run /reconcile');
```

**Step 3: Verify the suite still passes.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: all tests pass.

**Step 4: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts worker/mcp-servers/workspace-manager/src/integration.ts
```

---

## Task 4: Release v1.1.7

**Files:**
- Modify: `commander/pyproject.toml`
- Modify: `worker/.claude-plugin/plugin.json`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify: `worker/mcp-servers/workspace-manager/package.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

**Depends on:** Task 3.

**No tests required for the version edits** (config); `test_version_consistency.py` verifies. CHANGELOG/README are documentation.

**Step 1: Bump the five version sources from `1.1.6` to `1.1.7`.**

- `commander/pyproject.toml`: `version = "1.1.7"`
- `worker/.claude-plugin/plugin.json`: `"version": "1.1.7"`
- `worker/.codex-plugin/plugin.json`: `"version": "1.1.7+codex.<UTC YYYYMMDDHHMMSS>"` — keep the `+codex.<ts>` cachebuster; set a fresh `<ts>`. The release part (`1.1.7`) is what the test checks.
- `worker/mcp-servers/workspace-manager/package.json`: `"version": "1.1.7"`
- `.claude-plugin/marketplace.json`: `plugins[0].version` → `"1.1.7"`

**Step 2: Verify version consistency.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && python -m pytest tests/test_version_consistency.py -q
```

Expected: passes (`test_version_sources_match` green; the codex cachebuster release parses to `1.1.7`).

**Step 3: Add the CHANGELOG entry.**

Insert a new `## 1.1.7: …` section at the top of `CHANGELOG.md` (above `## 1.1.6`): the never-lose-work fix at the two active finalizer branches, the structural `recycleFinalized`/`releaseFinalized` backstop, and the O-2/O-3/O-4 clarity fixes. State the accepted consequence (a preserved obligation leaves the worktree integrated-but-not-recycled until `/push` or `reconcile_finalization` resolves it).

**Step 4: Add the README "What's New in v1.1.7" section** above the v1.1.6 one: reconcile and `/commit` preserve an unfulfilled push obligation instead of silently dropping it, with a structural backstop that refuses to discard one.

**Step 5: Run the full workspace-manager suite once more.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: all pass.

**Step 6: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/pyproject.toml worker/.claude-plugin/plugin.json worker/.codex-plugin/plugin.json worker/mcp-servers/workspace-manager/package.json .claude-plugin/marketplace.json CHANGELOG.md README.md
```
