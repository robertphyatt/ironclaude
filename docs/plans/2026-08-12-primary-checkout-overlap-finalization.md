# Primary Checkout Overlap-Based Finalization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Let reviewed worker integration and proven managed-worktree cleanup
proceed under unrelated live primary ownership while preserving direct-primary
exclusivity and every concrete safety gate.

**Requirements:** `docs/plans/2026-08-12-primary-checkout-overlap-finalization-requirements.md`

**Architecture:** Remove repository-wide ownership predicates from integration,
proven workspace cleanup, and Commander reaper liveness. Direct-primary
exclusivity remains in `acquirePrimaryCheckoutOwnership` and checkout-intent
issuance; all path-overlap, ref, identity, reachability, liveness, lock, and
recovery safeguards remain intact.

**Tech Stack:** TypeScript, Vitest, Git worktrees and refs, SQLite, esbuild, Claude Code plugin CLI, Codex plugin CLI.

## Execution invariants

- Each shell step starts fresh. Use literal absolute paths; never depend on an earlier shell variable.
- Execution Bash starts in `commander/`. Every Git command uses `git -C /Users/roberthyatt/Code/ironclaude`.
- Quote globs under zsh. Do not suppress evidence-command stderr or truncate completeness searches.
- `docs/` is ignored; plan artifacts use `git add -f`.
- Preserve the already-staged communication effort. Do not reset, stash, unstage, or sweep unrelated files into this effort.
- Use RED before GREEN. A newly added success case must fail against the current blanket fence before source removal.
- Keep exact technical values, errors, refs, paths, and machine contracts unchanged except for removing the obsolete fence error.
- Commander remains unable to push. Direct commit/push authority remains human-gated. This effort changes neither authority path.
- Do not bump release `1.1.6`; replace only the Codex cachebuster suffix.
- Reinstallation is the final deployment operation. Reuse `/private/tmp/restart_codex.py` with SHA-256 `d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f`.
- After the final `codex plugin add` shell command, allow the armed helper to restart Codex, reopen this same task, and use only MCP state/runtime checks until task submission.
- No commit or push occurs in this plan.

---

## Task 1: Remove workspace-manager ownership fences with behavioral coverage

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts`

**Current staged baseline:** Integration/live-owner, normal finalization overlap,
status, push-only, release, repair-channel, interrupted-CAS, and direct cleanup
coverage below is already staged. `integration.ts` and `workspace-service.ts`
already omit their repository-wide ownership fences. Preserve these bytes and
their captured RED evidence; do not expect removed fence errors from the current
source. Remaining work adds authority-routing and post-CAS overlap coverage,
then completes those two source seams.

### Recorded staged baseline: live-owner and primary-change fixtures

Add these helpers beside the existing finalization fixtures in `integration-cases.ts`:

```ts
  function bindOtherLivePrimaryOwner(state: ReturnType<typeof setup>) {
    const primaryOwner = new WorkspaceService(state.database)
      .ensureSessionWorktree({ repositoryPath: state.root, ownerSessionId: OTHER });
    acquirePrimaryCheckoutOwnership(state.database, {
      repositoryIdentity: primaryOwner.repository_identity,
      workspaceGuid: primaryOwner.workspace_guid,
      ownerSessionId: OTHER,
    });
    return primaryOwner;
  }

  type PrimaryChangeKind = 'staged' | 'unstaged' | 'untracked';

  function seedPrimaryChange(
    state: ReturnType<typeof setup>,
    kind: PrimaryChangeKind,
    overlap: boolean,
  ) {
    const changedPath = overlap
      ? (kind === 'untracked' ? 'work.txt' : 'README.md')
      : (kind === 'untracked' ? 'operator-notes.txt' : 'README.md');
    if (overlap && kind !== 'untracked') {
      writeFileSync(join(state.assignment.worktree_path, 'README.md'), 'approved worker edit\n');
      git(state.assignment.worktree_path, 'add', 'README.md');
    }
    writeFileSync(join(state.root, changedPath), `operator ${kind} edit\n`);
    if (kind === 'staged') git(state.root, 'add', changedPath);
    return {
      changedPath,
      beforeHash: git(state.root, 'hash-object', join(state.root, changedPath)),
      beforeIndex: git(state.root, 'ls-files', '-s', '--', changedPath),
      beforeStatus: git(state.root, 'status', '--porcelain=v1', '--untracked-files=all', '--', changedPath),
    };
  }
```

These use the existing imported `WorkspaceService`,
`acquirePrimaryCheckoutOwnership`, file helpers, and `git()` fixture. Do not
modify `db.ts` or `git.ts`.

### Recorded staged baseline: fence expectations and integration matrix

Replace the test that says a live owner fences finalization with two success cases:

```ts
  it('finalizes direct and Commander managed work while another live session owns the primary checkout', () => {
    const commander = setup(false);
    const commanderOwner = bindOtherLivePrimaryOwner(commander);
    expect(finalizeCommanderLocalCommit(
      commander.database,
      commanderInput(commander.root, commander.assignment, 'Commander under live owner'),
    ).state).toBe('cleaned');
    expect(commander.database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(commander.assignment.repository_identity)).toMatchObject({
      workspace_guid: commanderOwner.workspace_guid,
      owner_session_id: OTHER,
    });

    const direct = setup(false);
    const authority = issueAndVerify(
      direct.database, direct.assignment, 'commit', commitEvidence(direct.assignment),
    );
    const directOwner = bindOtherLivePrimaryOwner(direct);
    expect(finalizeDirectAuthority(direct.database, authority, 'direct under live owner').state)
      .toBe('cleaned');
    expect(direct.database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(direct.assignment.repository_identity)).toMatchObject({
      workspace_guid: directOwner.workspace_guid,
      owner_session_id: OTHER,
    });
  });

  it('does not reap a stale primary owner as a side effect of finalization', () => {
    const state = setup(false);
    const stale = new WorkspaceService(state.database)
      .ensureSessionWorktree({ repositoryPath: state.root, ownerSessionId: OTHER });
    state.database.prepare("UPDATE assignments SET lifecycle_status = 'abandoned' WHERE workspace_guid = ?")
      .run(stale.workspace_guid);
    state.database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(stale.repository_identity, stale.workspace_guid, OTHER);
    expect(finalizeCommanderLocalCommit(
      state.database, commanderInput(state.root, state.assignment, 'ignore stale ownership'),
    ).state).toBe('cleaned');
    expect(state.database.prepare(
      'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(state.assignment.repository_identity)).toMatchObject({ workspace_guid: stale.workspace_guid });
  });
```

Replace the push-only fence test with a successful push-only case under
`bindOtherLivePrimaryOwner(state)`. Require `state === 'pushed-only'`, remote
read-back equal to `evidence.localOid`, unchanged assignment lifecycle, and the
other owner's row unchanged.

Add a non-mutating status case:

```ts
  it('reports reconciliation status under a live primary owner without mutating ownership', () => {
    const state = setup(false);
    const owner = bindOtherLivePrimaryOwner(state);
    const before = state.database.prepare(
      'SELECT * FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(state.assignment.repository_identity);
    expect(reconcileFinalization(state.database, {
      repositoryPath: state.root,
      workspaceGuid: state.assignment.workspace_guid,
      providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    })).toEqual({ state: 'not-ready' });
    expect(state.database.prepare(
      'SELECT * FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(state.assignment.repository_identity)).toEqual(before);
    expect(before).toMatchObject({ workspace_guid: owner.workspace_guid, owner_session_id: OTHER });
  });
```

In the existing release-disposition test, bind another live owner before the
`dispose: 'release'` call. Preserve the existing worktree-removal and lifecycle
assertions and add an assertion that the other owner's row remains.

In the existing interrupted-CAS recovery test, bind another live owner after the
simulated crash and before `reconcileFinalization`. Preserve its existing
candidate, lock-release, and cleanup assertions and add an unchanged-owner-row
assertion.

Add the other-owner fixture to existing case 1 and to all three repair-channel
states. Replace current single case-2 and case-3 tests with this complete matrix:

```ts
  it.each(['staged', 'unstaged', 'untracked'] as const)(
    'case 2: carries forward with non-overlapping %s primary work under a live owner',
    (kind) => {
      const state = setup(false);
      const owner = bindOtherLivePrimaryOwner(state);
      const local = seedPrimaryChange(state, kind, false);
      const result = finalizeCommanderLocalCommit(
        state.database, commanderInput(state.root, state.assignment, `non-overlap ${kind}`),
      );
      expect(result.state).toBe('cleaned');
      expect(git(state.root, 'rev-parse', 'HEAD')).toBe(result.integratedCommit);
      expect(git(state.root, 'hash-object', join(state.root, local.changedPath))).toBe(local.beforeHash);
      expect(git(state.root, 'ls-files', '-s', '--', local.changedPath)).toBe(local.beforeIndex);
      expect(git(state.root, 'status', '--porcelain=v1', '--untracked-files=all', '--', local.changedPath))
        .toBe(local.beforeStatus);
      expect(readFileSync(join(state.root, 'work.txt'), 'utf8')).toBe('approved\n');
      expect(state.database.prepare(
        'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
      ).get(state.assignment.repository_identity)).toMatchObject({ workspace_guid: owner.workspace_guid });
    },
  );

  it.each(['staged', 'unstaged', 'untracked'] as const)(
    'case 3: refuses before CAS for overlapping %s primary work under a live owner',
    (kind) => {
      const state = setup(false);
      const mainTip = git(state.root, 'rev-parse', 'refs/heads/main');
      const owner = bindOtherLivePrimaryOwner(state);
      const local = seedPrimaryChange(state, kind, true);
      expect(() => finalizeCommanderLocalCommit(
        state.database, commanderInput(state.root, state.assignment, `overlap ${kind}`),
      )).toThrow(
        `Finalization primary checkout has local changes overlapping the carried-forward integration; `
        + `preserving worktree. Overlapping paths: ${local.changedPath}`,
      );
      expect(git(state.root, 'rev-parse', 'refs/heads/main')).toBe(mainTip);
      expect(git(state.root, 'hash-object', join(state.root, local.changedPath))).toBe(local.beforeHash);
      expect(git(state.root, 'ls-files', '-s', '--', local.changedPath)).toBe(local.beforeIndex);
      expect(git(state.root, 'status', '--porcelain=v1', '--untracked-files=all', '--', local.changedPath))
        .toBe(local.beforeStatus);
      expect(existsSync(state.assignment.worktree_path)).toBe(true);
      expect(state.database.prepare(
        'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
      ).get(state.assignment.repository_identity)).toMatchObject({ workspace_guid: owner.workspace_guid });
    },
);
```

Add this real-Git lifecycle case to `workspace-service.test.ts` beside the
existing integrated-cleanup proof test:

```ts
  it('cleans a proven integrated worktree while another session owns the primary checkout', () => {
    const root = repository();
    const database = initDb(join(root, 'integrated-under-other-owner.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const other = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OTHER_OWNER });
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const targetRef = 'refs/heads/main';
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ? WHERE workspace_guid = ?
    `).run(integratedCommit, assignment.workspace_guid);
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef,
      integratedCommit,
    });
    git(root, 'merge', '--ff-only', integratedCommit);
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(other.repository_identity, other.workspace_guid, OTHER_OWNER);
    writeFileSync(join(root, 'README.md'), 'operator staged\n');
    git(root, 'add', 'README.md');
    writeFileSync(join(root, 'README.md'), 'operator unstaged\n');
    const primaryHash = git(root, 'hash-object', join(root, 'README.md'));
    const primaryIndex = git(root, 'ls-files', '-s', '--', 'README.md');
    const primaryBefore = git(root, 'status', '--porcelain=v1', '--untracked-files=all');

    expect(manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    }).lifecycle_status).toBe('cleaned');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');
    expect(git(root, 'hash-object', join(root, 'README.md'))).toBe(primaryHash);
    expect(git(root, 'ls-files', '-s', '--', 'README.md')).toBe(primaryIndex);
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe(primaryBefore);
    expect(database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(assignment.repository_identity)).toMatchObject({
      workspace_guid: other.workspace_guid,
      owner_session_id: OTHER_OWNER,
    });
  });
```

### Step 1: Add authority-routing and post-CAS overlap regressions

In `git-authority.test.ts`, replace the case that expects another assignment's
primary ownership to deny managed authority. Require the same managed
assignment to retain `checkoutMode === 'managed'`, keep its managed worktree
path, and pass commit and commit-and-push revalidation while the foreign owner
row remains unchanged. Parameterize two independent mismatches: another
workspace with the same provider root, and the same workspace with another
provider root. Preserve the existing assertions that an exact ownership
transition for the authorized assignment changes the effective checkout and
invalidates previously issued managed authority.

This encodes the complete three-way rule:

1. No primary owner: use the assignment's managed worktree.
2. Exact workspace and provider-root owner: use the primary checkout.
3. Different workspace or provider-root owner: continue using the assignment's
   managed worktree.

Direct ownership acquisition remains exclusively enforced by
`acquirePrimaryCheckoutOwnership`; this test changes no acquisition behavior.

Extend the interrupted-CAS fixture in `integration-cases.ts` with a complete
staged/unstaged/untracked matrix. For each kind, create both:

- A non-overlapping operator change after target CAS but before reconciliation.
  Reconciliation must carry forward the candidate, preserve exact local bytes,
  index entry, and porcelain status, clean the worker, release the integration
  lock, and retain the unrelated live owner.
- An overlapping operator change after target CAS but before reconciliation.
  Reconciliation must report the complete deterministic conflicting-path error,
  preserve local bytes/index/status and managed worktree, leave the assignment
  retryable, retain the integration lock, and retain the unrelated live owner.

Use an existing tracked path such as `README.md` for staged/unstaged cases and a
candidate-added path such as `work.txt` for the untracked overlap case. Assert
the complete error string; do not use a permissive regular expression.

Add one off-target interrupted-CAS case. Before finalization, check out an
operator feature branch in the primary checkout and seed staged, unstaged, and
untracked operator work. Capture branch name, working-tree hashes, index
entries, and porcelain status. Interrupt after target-ref CAS, bind an unrelated
live owner, then reconcile. Require `refs/heads/main` to equal the exact
candidate while the operator branch and all captured bytes/index/status remain
unchanged; also require cleaned lifecycle, removed managed worktree, released
integration lock, and unchanged owner row.

### Step 2: Run remaining cases RED

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --run src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts src/__tests__/workspace-service.test.ts src/__tests__/git-authority.test.ts
```

Expected: FAIL. New foreign-owner authority cases receive the current exact
authority error `Direct Git authority primary checkout is owned by another
assignment or provider root`. Post-CAS non-overlapping staged work receives
`Crash reconciliation primary checkout has unproved changes; preserving
worktree`; post-CAS overlap does not yet report exact paths. Previously staged
normal integration and cleanup cases remain GREEN. Preserve this output as RED
evidence.

### Step 3: Complete authority routing and post-CAS overlap classification

Preserve the staged `integration.ts` removals:

1. Remove `reapStalePrimaryOwner` from the `./db.js` import.
2. Delete the complete `fencePrimaryCheckout()` function.
3. Delete all ten calls to `fencePrimaryCheckout()` from release, ordinary
   direct/Commander finalization, repair finalization, push-only,
   reconciliation status/recovery, and interrupted-CAS recovery.
4. Update comments that claim release or status probes consult primary
   ownership. Do not alter `assertNoPrimaryOverlap`, `verifyPrimaryTarget`,
   `primaryOnRef`, integration locks, CAS, or normal carry-forward behavior.

Preserve the staged `workspace-service.ts` removal of the private
`primaryCheckoutIsOwned()` helper and its `cleanupWorkspace()` call. Retain
every terminal-lifecycle, managed-identity, cleanliness, recovery/integration
reachability, integration-record, branch-removal, and transition proof.

In `git-authority.ts`, change only `resolveEffectiveCheckout()` ownership
classification. Return the managed assignment when there is no primary owner
or when the row belongs to another workspace/provider root. Return the primary
checkout only for the exact assignment and provider-root owner. Preserve
repository discovery, assignment binding, managed-worktree identity, Git
identity, evidence observation, single-use intent, and post-authorization
revalidation.

In `repairPrimaryCheckoutAfterInterruptedCas()`, replace the whole-index
equality veto with path classification against `expectedTarget`:

```ts
function pathLines(output: string): string[] {
  return output.split('\n').filter((entry) => entry.length > 0);
}

function postCasLocalPaths(repositoryPath: string, expectedTarget: string): string[] {
  return [...new Set([
    ...pathLines(runGit(repositoryPath, ['diff', '--name-only', '--cached', expectedTarget, '--'])),
    ...pathLines(runGit(repositoryPath, ['diff', '--name-only'])),
    ...pathLines(runGit(repositoryPath, ['ls-files', '--others', '--exclude-standard'])),
  ])].sort();
}

function assertNoPostCasOverlap(
  repositoryPath: string,
  expectedTarget: string,
  candidate: string,
): void {
  const changed = new Set(changedPaths(repositoryPath, expectedTarget, candidate));
  const overlap = postCasLocalPaths(repositoryPath, expectedTarget)
    .filter((entry) => changed.has(entry));
  if (overlap.length > 0) {
    throw new Error(
      'Crash reconciliation primary checkout has local changes overlapping the carried-forward integration; '
      + `preserving worktree. Overlapping paths: ${overlap.join(', ')}`,
    );
  }
}
```

1. Collect staged paths with `git diff --name-only --cached expectedTarget`.
2. Collect unstaged and untracked paths through the existing Git primitives.
3. Intersect their sorted union with
   `changedPaths(expectedTarget, candidate)`.
4. On overlap, throw `Crash reconciliation primary checkout has local changes
   overlapping the carried-forward integration; preserving worktree.
   Overlapping paths: <sorted paths>` and preserve diagnostic/retry state.
5. On no overlap, call the existing `carryForwardFastForward()` and retain
   `verifyPrimaryAfterFastForward()`.

Do not change target-CAS, integration-lock, freeze/effect, mark-integrated,
cleanup, or recovery-release rules.

The resulting import remains:

```ts
import {
  acquireIntegrationLock,
  deleteIntegrationRecord,
  getAssignment,
  recordIntegration,
  transitionAssignment,
} from './db.js';
```

### Step 4: Prove the blanket fences are absent

Run:

```bash
python3 -c 'from pathlib import Path; integration = Path("/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/integration.ts").read_text(); cleanup = Path("/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/workspace-service.ts").read_text(); authority = Path("/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/git-authority.ts").read_text(); assert "fencePrimaryCheckout" not in integration; assert "Finalization is fenced while primary checkout is owned" not in integration; assert "reapStalePrimaryOwner" not in integration; assert "primaryCheckoutIsOwned" not in cleanup; assert "Primary checkout remains owned; preserving managed worktree" not in cleanup; assert "Direct Git authority primary checkout is owned by another assignment or provider root" not in authority'
```

Expected: exit 0. This fails if any repository-wide ownership helper, error,
import, call, or foreign-owner authority denial remains.

### Step 5: Run focused GREEN and direct-ownership regression

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --run src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts src/__tests__/workspace-service.test.ts src/__tests__/git-authority.test.ts
```

Expected: all four files pass. Do not hard-code a predicted post-change count.

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --run src/__tests__/db.test.ts -t "never reaps a live primary owner"
```

Expected: `1 passed`, `22 skipped`. This proves direct-primary exclusivity remains
independent of integration behavior.

### Step 6: Stage only Task 1 files

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts worker/mcp-servers/workspace-manager/src/git-authority.ts worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts
```

Expected: Task 1 source and tests are staged; all previously staged work remains staged.

---

## Task 2: Remove repository-wide ownership from Commander reaper liveness

**Depends on:** Task 1

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/tests/test_worktree_reaper.py`

### Step 1: Add the live-owner reaper regression (RED)

Add this case to `TestReapLeakedWorktrees`:

```python
    def test_unrelated_primary_owner_does_not_protect_finished_worker(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(
            ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
            updated_ago="25 hours",
        )
        _insert_assignment(
            ws_conn, _W2, owner_session_id=_W2, lifecycle_status="active",
            updated_ago="25 hours",
        )
        ws_conn.execute(
            "INSERT INTO primary_checkout_owners "
            "(repository_identity, workspace_guid, owner_session_id) VALUES ('repo-id', ?, ?)",
            (_W2, _W2),
        )
        ws_conn.commit()
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_called_once_with(
            {"repository_path": "/repo", "workspace_guid": _W1, "owner_session_id": _OWNER},
        )
        assert counts == {"released": 1, "surfaced": 0, "protected": 0, "errors": 0}
        owner = sqlite3.connect(str(ws)).execute(
            "SELECT workspace_guid, owner_session_id FROM primary_checkout_owners "
            "WHERE repository_identity = 'repo-id'"
        ).fetchone()
        assert owner == (_W2, _W2)
        other = sqlite3.connect(str(ws)).execute(
            "SELECT lifecycle_status, owner_session_id FROM assignments WHERE workspace_guid = ?",
            (_W2,),
        ).fetchone()
        assert other == ("active", _W2)
```

Add a second acceptance test that uses a temporary real Git repository and the
actual `WorkspaceClient.cleanup()` transport instead of a mock:

1. Import `Path`, `subprocess`, and `WorkspaceClient`.
2. Bundle current `worker/mcp-servers/workspace-manager/src/cli.ts` into a
   `tmp_path` plugin root with the repository's local `esbuild` executable and
   `--external:fsevents --external:better-sqlite3`; symlink the package's
   `node_modules` beside the temporary bundle. This writes only under
   `tmp_path`, not the repository.
3. Set `WORKSPACE_MANAGER_DB_PATH` to a temporary workspace database and create
   a real repository with configured test identity and a committed `main`.
4. Use the real client to allocate `_W1` for `_OWNER` and `_W2` for `_W2`.
5. Commit work in `_W1`, fast-forward `main`, mark `_W1` integrated, and insert
   its exact `integration_records` row. Insert `_W2` as the live
   `primary_checkout_owners` row.
6. Seed tracked staged content followed by an unstaged edit plus an untracked
   file in the primary checkout. Capture each working-tree hash, relevant index
   entry, current branch, and full porcelain status.
7. Insert an expired completed Commander worker for `_W1` using the real
   repository path. Call `_reap_leaked_worktrees()` with the real client and
   only tmux mocked absent.
8. Require `released == 1`, no protected/error count, removed `_W1` worktree and
   branch, cleaned `_W1` lifecycle, unchanged primary branch/hashes/index/status,
   and unchanged active `_W2` assignment and owner row.

The temporary bundle command is:

```python
subprocess.run([
    str(package / 'node_modules/.bin/esbuild'), 'src/cli.ts', '--bundle',
    '--platform=node', '--format=esm', f'--outfile={temporary_cli}',
    '--external:fsevents', '--external:better-sqlite3',
], cwd=package, check=True, capture_output=True, text=True)
```

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_worktree_reaper.py -q
```

Expected: FAIL because `client.cleanup` is not called and `protected` is 1.

### Step 2: Remove primary ownership from reaper liveness (GREEN)

In `commander/src/ironclaude/main.py`:

1. Remove `primary_owned_repos` from `_is_protected()`.
2. Remove the `repository_identity in primary_owned_repos` protection.
3. Stop selecting `primary_checkout_owners` in `_reap_leaked_worktrees()`.
4. Pass only `locked_workspace_guids` and the existing liveness inputs to
   `_is_protected()`.
5. Retain worker status, tmux, recent activity, exact integration-lock,
   operator-assignment, owner-binding, TTL, and fail-safe exception behavior.

In `test_worktree_reaper.py`, delete
`test_primary_checkout_owner_row_protects` and update
`test_no_signals_does_not_protect` for the narrower `_is_protected()` signature.

### Step 3: Prove the obsolete reaper predicate is absent

Run:

```bash
python3 -c 'from pathlib import Path; source = Path("/Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/main.py").read_text(); assert "primary_owned_repos" not in source; assert "SELECT repository_identity FROM primary_checkout_owners" not in source'
```

Expected: exit 0; either remaining predicate fails the check.

### Step 4: Run the full reaper file (GREEN)

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_worktree_reaper.py -q
```

Expected: all reaper tests pass, including real transport/worktree cleanup. Do
not hard-code a predicted count.

### Step 5: Stage only Task 2 files

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/tests/test_worktree_reaper.py
```

Expected: Task 2 files staged; prior staged files unchanged.

---

## Task 3: Build, verify, and cachebust the shared plugin

**Depends on:** Task 2

**Files:**
- Modify: `worker/.codex-plugin/plugin.json`
- Regenerate: `worker/mcp-servers/workspace-manager/dist/index.js`
- Regenerate: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Regenerate/verify unchanged: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

### Step 1: Refresh only the Codex cachebuster

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: `worker/.codex-plugin/plugin.json` changes from
`1.1.6+codex.20260812213409` to one fresh `1.1.6+codex.<UTC timestamp>` value.
Release version remains `1.1.6`.

### Step 2: Build the cachebusted workspace-manager bundles

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager run build
```

Expected: TypeScript, server bundle, CLI bundle, and hook-intent bundle complete
without errors.

### Step 3: Verify executable bundles carry the repair

Run:

```bash
python3 -c 'from pathlib import Path; paths = [Path("/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js"), Path("/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js")]; forbidden = ["Finalization is fenced while primary checkout is owned", "Primary checkout remains owned; preserving managed worktree", "Direct Git authority primary checkout is owned by another assignment or provider root"]; [(_ for _ in ()).throw(AssertionError(f"{p}: {marker}")) for p in paths for marker in forbidden if marker in p.read_text()]; assert all("Overlapping paths:" in p.read_text() for p in paths)'
```

Expected: exit 0. Both executable bundles omit all ownership-fence errors and
retain the exact overlap-path error.

### Step 4: Run focused workspace-manager verification

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --run src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts src/__tests__/workspace-service.test.ts src/__tests__/git-authority.test.ts src/__tests__/db.test.ts
```

Expected: all five files pass, covering integration, recovery, status, cleanup,
authority routing, push-only, stale-owner lifecycle, and live-owner exclusivity.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_worktree_reaper.py -q
```

Expected: the Commander reaper file passes.

### Step 5: Run the complete repository suite

Run:

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

Expected: hook, workspace-manager, and Commander suites exit 0. Do not predict a
post-change test count.

### Step 6: Verify release consistency and plugin structure

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_version_consistency.py -q
```

Expected: version-consistency tests pass with release `1.1.6` and one supported
Codex cachebuster suffix.

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: plugin validation succeeds.

### Step 7: Capture source fingerprints for runtime activation

Run:

```bash
shasum -a 256 /Users/roberthyatt/Code/ironclaude/worker/.codex-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected: five SHA-256 values are retained as Task 3 `expected_runtime` evidence.

### Step 8: Stage exact Task 3 files

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/.codex-plugin/plugin.json worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected: cachebuster and generated workspace-manager bundles are staged. Existing
staged files remain untouched.

---

## Task 4: Reinstall IronClaude last and prove same-task activation

**Depends on:** Task 3

**Files:**
- Verify only: `worker/.claude-plugin/plugin.json`
- Verify only: `worker/.codex-plugin/plugin.json`

**No source tests required:** Task 3 completed focused and full suites. The main
orchestrator owns Commander restart, both client reinstalls, Codex application
restart, identity verification, runtime diagnostics, and task submission. Do not
delegate this task.

### Step 1: Preserve session and plan identity

Call `get_resume_state` immediately before deployment. Require provider-native
session `019fc5e5-fc72-7493-b785-bee8cda62b1b`, this plan lineage, Task 4 in
progress, and professional mode on. Any mismatch fails closed.

### Step 2: Restart Commander onto verified source

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/ironclaude restart
```

Expected: the identity-checked CLI sends SIGHUP to the live `ironclaude.main`
daemon. Do not invoke Claude login or start a second daemon.

Run:

```bash
tail -n 80 /tmp/ic/daemon.log
```

Expected: the latest sequence ends with a fresh `Daemon started` record and no
singleton, authentication, or startup failure.

### Step 3: Reinstall the Claude plugin

Run:

```bash
claude plugin uninstall ironclaude@ironclaude --scope user --keep-data --yes
```

Expected: cached plugin removed; persistent plugin data preserved.

Run:

```bash
claude plugin install ironclaude@ironclaude --scope user
```

Expected: Claude installs IronClaude `1.1.6` from local marketplace `ironclaude`.

Run:

```bash
claude plugin details ironclaude@ironclaude
```

Expected: inventory includes workspace-manager and release `1.1.6`.

### Step 4: Verify the existing Codex restart helper

Run:

```bash
shasum -a 256 /private/tmp/restart_codex.py
```

Expected:

```text
d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f  /private/tmp/restart_codex.py
```

Read the helper and require: a 20-second delay, exact executable
`/Applications/ChatGPT.app/Contents/MacOS/ChatGPT`, SIGTERM with bounded exit
wait, and relaunch through `/usr/bin/open -b com.openai.codex`. Do not rewrite it.

### Step 5: Arm the restart helper as the penultimate shell action

Run:

```bash
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3", "/private/tmp/restart_codex.py"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'
```

Expected: detached helper starts and waits; current Codex process remains alive
long enough for the final installation command to return.

### Step 6: Reinstall Codex as the final shell action

Run:

```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: exit 0; JSON reports enabled installation from local marketplace
`ironclaude` at Task 3's fresh `1.1.6+codex.<cachebuster>` version. Run no later
shell command in this task.

### Step 7: Resume this exact task after helper restart

After Codex relaunches, reopen this same native task. Call `get_resume_state` and
require session ID `019fc5e5-fc72-7493-b785-bee8cda62b1b`, unchanged plan lineage,
Task 4 in progress, and professional mode on. Do not create a replacement task.

### Step 8: Prove runtime activation and submit

Call `run_diagnostics` with Task 3's exact installed cache path/version and all
five captured hashes: `manifest_sha256`, `state_manager_bundle_sha256`,
`workspace_manager_bundle_sha256`, `workspace_manager_cli_sha256`, and
`workspace_manager_hook_intent_sha256`, plus `client: "codex"`. Require complete
13/13 diagnostics, exact source/cache parity, provider-native session identity,
and `Runtime activation match ... PASS`.

Submit Task 4 through the normal task-boundary review. No commit or push follows.
