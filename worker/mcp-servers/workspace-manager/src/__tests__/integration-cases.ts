import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import {
  acquireIntegrationLock,
  acquirePrimaryCheckoutOwnership,
  createHumanIntent,
  initDb,
  recordIntegration,
  releaseIntegrationLock,
} from '../db.js';
import {
  verifyDirectGitAuthority,
  type CommitAndPushEvidence,
  type CommitEvidence,
  type PushEvidence,
  type ReconcileEvidence,
} from '../git-authority.js';
import {
  finalizeCommanderLocalCommit,
  finalizeDirectAuthority,
  finalizeReconcile,
  reconcileFinalization,
  recycleFinalized,
  syncWorktreeToTarget,
} from '../integration.js';
import { createInternalCommandDependencies } from '../cli.js';
import { WorkspaceService } from '../workspace-service.js';
import type { Assignment } from '../types.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER = '019f7cdf-023c-74e0-9ead-9c155636885d';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

export function registerFinalizationTests(part: 'core' | 'recovery'): void {
describe('finalization coordinator', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function setup(withRemote = true) {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-finalization-'));
    const databaseDir = mkdtempSync(join(tmpdir(), 'ironclaude-finalization-db-'));
    directories.push(root, databaseDir);
    git(root, 'init', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Finalization Test');
    git(root, 'config', 'user.email', 'finalization@example.invalid');
    writeFileSync(join(root, 'README.md'), 'initial\n');
    git(root, 'add', 'README.md');
    git(root, 'commit', '-m', 'initial');
    if (withRemote) {
      const remote = mkdtempSync(join(tmpdir(), 'ironclaude-finalization-remote-'));
      directories.push(remote);
      git(remote, 'init', '--bare');
      git(root, 'remote', 'add', 'origin', remote);
      git(root, 'push', 'origin', 'main:refs/heads/main');
    }
    const database = initDb(join(databaseDir, 'state.db'));
    const assignment = new WorkspaceService(database).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'work.txt'), 'approved\n');
    git(assignment.worktree_path, 'add', 'work.txt');
    return { root, database, assignment };
  }

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

  function commitEvidence(assignment: Assignment): CommitEvidence {
    const source = assignment.worktree_path;
    return {
      checkoutMode: 'managed', canonicalBranch: assignment.branch, localRef: `refs/heads/${assignment.branch}`,
      stagedTree: git(source, 'write-tree'), parentRef: 'HEAD', parentOid: git(source, 'rev-parse', 'HEAD'),
    };
  }

  function commanderEvidence(assignment: Assignment) {
    const source = assignment.worktree_path;
    return {
      canonicalBranch: assignment.branch,
      localRef: `refs/heads/${assignment.branch}`,
      stagedTree: git(source, 'write-tree'),
      parentOid: git(source, 'rev-parse', 'HEAD'),
    };
  }

  function commanderInput(root: string, assignment: Assignment, message: string) {
    return {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      message,
      ...commanderEvidence(assignment),
    };
  }

  function remoteEvidence(assignment: Assignment) {
    const source = assignment.worktree_path;
    const destinationRef = `refs/heads/${assignment.branch}`;
    const line = git(source, 'ls-remote', '--refs', 'origin', destinationRef);
    return {
      remoteName: 'origin', remoteUrl: git(source, 'remote', 'get-url', 'origin'), destinationRef,
      expectedRemoteOldOid: line === '' ? null : line.split(/\s+/)[0],
    };
  }

  function seedIntegratedPushPending(state: ReturnType<typeof setup>) {
    git(state.assignment.worktree_path, 'commit', '-m', 'frozen authorized commit');
    const frozen = git(state.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(state.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'rebased integration candidate');
    const candidate = git(state.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(state.root, 'merge', '--ff-only', candidate);
    git(state.root, 'update-ref', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/frozen`, frozen);
    git(state.root, 'update-ref', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/candidate`, candidate);
    const destinationRef = `refs/heads/${state.assignment.branch}`;
    const disposition = JSON.stringify({
      phase: 'push-pending', candidateCommit: candidate, frozenCommit: frozen,
      remoteName: 'origin', remoteUrl: git(state.assignment.worktree_path, 'remote', 'get-url', 'origin'),
      destinationRef, expectedRemoteOldOid: null,
    });
    recordIntegration(state.database, {
      workspaceGuid: state.assignment.workspace_guid, repositoryIdentity: state.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: candidate,
    });
    state.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ?
      WHERE workspace_guid = ?
    `).run(candidate, candidate, disposition, state.assignment.workspace_guid);
    return { frozen, candidate, destinationRef, disposition };
  }

  // Seeds a real conflict-based mid-rebase: the integration rebase stops with
  // README.md unmerged, the assignment left in ready_for_integration with a
  // durable frozen ref. Mirrors the conflict path exercised at the refusal test.
  function seedConflictMidRebase() {
    const s = setup(false);
    git(s.root, 'checkout', '-b', 'target-change');
    writeFileSync(join(s.root, 'README.md'), 'target\n');
    git(s.root, 'add', 'README.md');
    git(s.root, 'commit', '-m', 'target change');
    git(s.root, 'checkout', 'main');
    git(s.root, 'merge', '--ff-only', 'target-change');
    writeFileSync(join(s.assignment.worktree_path, 'README.md'), 'source\n');
    git(s.assignment.worktree_path, 'add', 'README.md');
    const authority = issueAndVerify(s.database, s.assignment, 'commit', commitEvidence(s.assignment));
    expect(() => finalizeDirectAuthority(s.database, authority, 'conflict')).toThrow();
    return s;
  }

  // Seeds a ready assignment whose finalization drifted with NO paused rebase: the
  // reviewed work is committed and frozen, the integration target has advanced, and
  // the worktree is reset --hard back to the frozen commit ATTACHED on its branch —
  // exactly the state finalize's :468-471 catch leaves after a mid-rebase drift throw.
  // `conflicting` decides whether the target change collides with the reviewed line.
  function seedFrozenReadyNoRebase(conflicting: boolean) {
    const { root, database, assignment } = setup(false);
    const wt = assignment.worktree_path;
    if (conflicting) {
      writeFileSync(join(wt, 'README.md'), 'source line\n');
      git(wt, 'add', 'README.md');
    }
    git(wt, 'commit', '-m', 'reviewed work');
    const frozen = git(wt, 'rev-parse', 'HEAD');
    if (conflicting) {
      writeFileSync(join(root, 'README.md'), 'target line\n');
      git(root, 'add', 'README.md');
    } else {
      writeFileSync(join(root, 'target.txt'), 'advanced target\n');
      git(root, 'add', 'target.txt');
    }
    git(root, 'commit', '-m', 'advance integration target');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozen);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);
    git(wt, 'reset', '--hard', frozen);
    expect(existsSync(resolve(wt, git(wt, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(false);
    return { root, database, assignment, wt, frozen };
  }

  // Seeds an integrated row with NO disposition whose worktree HEAD is the candidate:
  // exactly the shape whose plain-reconcile integrated branch reaches recycleFinalized
  // (disposition null -> else-if worktreeHead === candidate -> recycleFinalized recycles
  // the worktree in place). Shared by the status-probe survival test and the plain-reconcile
  // recycle test so the "worktree survives after status" assertion is provably
  // meaningful: the very same seed IS recycled by a plain reconcile.
  function seedIntegratedNoDisposition(state: ReturnType<typeof setup>) {
    git(state.assignment.worktree_path, 'commit', '-m', 'frozen authorized commit');
    const frozen = git(state.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(state.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'rebased integration candidate');
    const candidate = git(state.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(state.root, 'merge', '--ff-only', candidate);
    git(state.root, 'update-ref', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/frozen`, frozen);
    git(state.root, 'update-ref', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/candidate`, candidate);
    recordIntegration(state.database, {
      workspaceGuid: state.assignment.workspace_guid, repositoryIdentity: state.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: candidate,
    });
    state.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = NULL
      WHERE workspace_guid = ?
    `).run(candidate, candidate, state.assignment.workspace_guid);
    return { frozen, candidate };
  }

  function reconcileEvidence(assignment: Assignment): ReconcileEvidence {
    const source = assignment.worktree_path;
    return {
      checkoutMode: 'managed', canonicalBranch: assignment.branch, localRef: `refs/heads/${assignment.branch}`,
      headOid: git(source, 'rev-parse', 'HEAD'),
    };
  }

  function issueAndVerify(
    database: ReturnType<typeof initDb>, assignment: Assignment, operation: 'commit' | 'commit-and-push' | 'push' | 'reconcile',
    evidence: CommitEvidence | CommitAndPushEvidence | PushEvidence | ReconcileEvidence,
  ) {
    const nonce = randomUUID();
    createHumanIntent(database, {
      operation, humanChannel: 'codex-user-prompt', providerRootSessionId: OWNER,
      repositoryIdentity: assignment.repository_identity, workspaceGuid: assignment.workspace_guid,
      expectedEvidence: evidence, expiresAt: '2030-01-01T00:00:00.000Z', nonce,
    });
    return verifyDirectGitAuthority(database, {
      repositoryPath: assignment.worktree_path, workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER, humanChannel: 'codex-user-prompt', operation,
      expectedEvidence: evidence, nonce,
    });
  }

  if (part === 'core') {
  describe('core integration', () => {
  it('finalizes a direct local commit through freeze, checked fast-forward, record, and cleanup', () => {
    const { root, database, assignment } = setup(false);
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));

    const result = finalizeDirectAuthority(database, authority, 'approved direct commit');

    expect(result.state).toBe('cleaned');
    expect(git(root, 'log', '-1', '--format=%s')).toBe('approved direct commit');
    expect(readFileSync(join(root, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
    expect(existsSync(assignment.worktree_path)).toBe(true);
    const integratedCommit = git(root, 'rev-parse', 'HEAD');
    expect(database.prepare('SELECT lifecycle_status, base_commit, integrated_commit FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'active', base_commit: integratedCommit, integrated_commit: null });
    expect(git(root, 'show-ref', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`)).not.toBe('');
  });

  it('uses same coordinator for reviewed Commander local work and never pushes', () => {
    const { root, database, assignment } = setup(true);

    const result = finalizeCommanderLocalCommit(
      database, commanderInput(root, assignment, 'reviewed local work'),
    );

    expect(result.state).toBe('cleaned');
    expect(git(root, 'ls-remote', '--refs', 'origin', `refs/heads/${assignment.branch}`)).toBe('');
  });

  it('Commander local commit leaves the integration target on the remote byte-unmoved (never pushes)', () => {
    // R6 behavioral proof: the commander path (the only push-free writer) must not
    // move origin/main. Complements the branch-ref check above by pinning the
    // shared target ref before == after across a real local integration.
    const { root, database, assignment } = setup(true);
    const remoteMainBefore = git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];

    const result = finalizeCommanderLocalCommit(
      database, commanderInput(root, assignment, 'commander local work, no push'),
    );

    expect(result.state).toBe('cleaned');
    const remoteMainAfter = git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
    expect(remoteMainAfter).toBe(remoteMainBefore);
  });

  describe('reconcile', () => {
    it('integrates a managed worktree HEAD into local main and keeps the worktree alive, never pushing', () => {
      const { root, database, assignment } = setup(true);
      git(assignment.worktree_path, 'commit', '-m', 'work to reconcile');
      const remoteMainBefore = git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
      const authority = issueAndVerify(database, assignment, 'reconcile', reconcileEvidence(assignment));

      const result = finalizeReconcile(database, authority);

      expect(result.state).toBe('reconciled');
      const integratedCommit = git(root, 'rev-parse', 'HEAD');
      expect(result.integratedCommit).toBe(integratedCommit);
      expect(existsSync(assignment.worktree_path)).toBe(true);
      expect(database.prepare('SELECT lifecycle_status, base_commit FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active', base_commit: integratedCommit });
      const remoteMainAfter = git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
      expect(remoteMainAfter).toBe(remoteMainBefore);
    });

    it('is single-use: a second finalizeReconcile on the same authority throws', () => {
      const { database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'work to reconcile');
      const authority = issueAndVerify(database, assignment, 'reconcile', reconcileEvidence(assignment));

      expect(finalizeReconcile(database, authority).state).toBe('reconciled');

      expect(() => finalizeReconcile(database, authority)).toThrow(/single-use/);
    });

    it('refuses when the worktree HEAD moved after the reconcile authority was verified', () => {
      const { database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'work to reconcile');
      const authority = issueAndVerify(database, assignment, 'reconcile', reconcileEvidence(assignment));

      writeFileSync(join(assignment.worktree_path, 'more.txt'), 'more\n');
      git(assignment.worktree_path, 'add', 'more.txt');
      git(assignment.worktree_path, 'commit', '-m', 'moved after verify');

      expect(() => finalizeReconcile(database, authority)).toThrow(/HEAD changed/);
    });

    it('refuses a dirty worktree', () => {
      const { database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'work to reconcile');
      const authority = issueAndVerify(database, assignment, 'reconcile', reconcileEvidence(assignment));

      writeFileSync(join(assignment.worktree_path, 'dirty.txt'), 'dirty\n');

      expect(() => finalizeReconcile(database, authority)).toThrow();
      expect(existsSync(assignment.worktree_path)).toBe(true);
    });

    // REPAIR path: a prior commit-and-push authority paused mid-rebase on a real
    // conflict, leaving the row 'ready_for_integration' with a durable frozen ref
    // and an 'integration-pending' disposition (mirrors the conflict-repair test
    // above, minus the retry). A reconcile pinned to the resolved worktree HEAD
    // must integrate via finalizeAttestedCandidate, and since markIntegrated
    // converts the surviving integration-pending disposition into push-pending,
    // finalizeReconcile must report 'integrated-local' and PRESERVE that
    // disposition (and its integration_records row) rather than recycling.
    it('preserves a push-pending disposition through a REPAIR reconcile instead of recycling', () => {
      const conflict = setup(true);
      git(conflict.root, 'checkout', '-b', 'target-change');
      writeFileSync(join(conflict.root, 'README.md'), 'target\n');
      git(conflict.root, 'add', 'README.md');
      git(conflict.root, 'commit', '-m', 'target change');
      git(conflict.root, 'checkout', 'main');
      git(conflict.root, 'merge', '--ff-only', 'target-change');
      writeFileSync(join(conflict.assignment.worktree_path, 'README.md'), 'source\n');
      git(conflict.assignment.worktree_path, 'add', 'README.md');
      const conflictEvidence = {
        ...commitEvidence(conflict.assignment), ...remoteEvidence(conflict.assignment),
      } satisfies CommitAndPushEvidence;
      const conflictAuthority = issueAndVerify(conflict.database, conflict.assignment, 'commit-and-push', conflictEvidence);
      expect(() => finalizeDirectAuthority(conflict.database, conflictAuthority, 'conflicted push')).toThrow();
      expect(conflict.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
        .get(conflict.assignment.workspace_guid)).toMatchObject({
          lifecycle_status: 'ready_for_integration', disposition: expect.stringContaining('integration-pending'),
        });
      writeFileSync(join(conflict.assignment.worktree_path, 'README.md'), 'reviewed repair\n');
      git(conflict.assignment.worktree_path, 'add', 'README.md');
      git(conflict.assignment.worktree_path, '-c', 'core.editor=true', 'rebase', '--continue');
      const reconcileAuthority = issueAndVerify(
        conflict.database, conflict.assignment, 'reconcile', reconcileEvidence(conflict.assignment),
      );

      const result = finalizeReconcile(conflict.database, reconcileAuthority);

      expect(result.state).toBe('integrated-local');
      expect(result.pushError).toEqual(expect.stringContaining('Remote has not proved'));
      expect(existsSync(conflict.assignment.worktree_path)).toBe(true);
      expect(conflict.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
        .get(conflict.assignment.workspace_guid)).toMatchObject({
          lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
        });
      expect(conflict.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
        .get(conflict.assignment.workspace_guid)).toBeTruthy();
    });
  });

  it('recycles the managed worktree in place after a clean Commander local integration', () => {
    const { root, database, assignment } = setup(false);

    const result = finalizeCommanderLocalCommit(
      database, commanderInput(root, assignment, 'clean local integration'),
    );

    expect(result.state).toBe('cleaned');
    const integratedCommit = result.integratedCommit!;
    expect(git(root, 'rev-parse', 'HEAD')).toBe(integratedCommit);
    // Worktree survives at the integrated commit — no removal, no reset.
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(git(assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(integratedCommit);
    expect(database.prepare('SELECT lifecycle_status, base_commit, integrated_commit FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'active', base_commit: integratedCommit, integrated_commit: null });
  });

  it('release disposes the managed worktree by removal, contrasted with recycle leaving it active', () => {
    const recycle = setup(false);
    const recycleResult = finalizeCommanderLocalCommit(
      recycle.database, commanderInput(recycle.root, recycle.assignment, 'recycle disposition'),
    );
    expect(recycleResult.state).toBe('cleaned');
    expect(existsSync(recycle.assignment.worktree_path)).toBe(true);
    expect(recycle.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(recycle.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active' });

    const release = setup(false);
    const releaseOwner = bindOtherLivePrimaryOwner(release);
    const releaseBranch = release.assignment.branch;
    const releaseWorktree = release.assignment.worktree_path;
    const releaseResult = finalizeCommanderLocalCommit(
      release.database,
      { ...commanderInput(release.root, release.assignment, 'release disposition'), dispose: 'release' },
    );
    expect(releaseResult.state).toBe('cleaned');
    expect(existsSync(releaseWorktree)).toBe(false);
    expect(git(release.root, 'branch', '--list', releaseBranch)).toBe('');
    expect(git(release.root, 'rev-parse', 'HEAD')).toBe(releaseResult.integratedCommit);
    expect(release.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(release.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
    expect(release.database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(release.assignment.repository_identity)).toMatchObject({
      workspace_guid: releaseOwner.workspace_guid,
      owner_session_id: OTHER,
    });
  });

  it('permits a SECOND finalize in the same recycled session without an integration-record collision', () => {
    const { root, database, assignment } = setup(false);
    expect(finalizeCommanderLocalCommit(database, commanderInput(root, assignment, 'first local integration')).state)
      .toBe('cleaned');

    // The recycled worktree still exists at the integrated commit: stage fresh reviewed work.
    writeFileSync(join(assignment.worktree_path, 'second.txt'), 'more approved work\n');
    git(assignment.worktree_path, 'add', 'second.txt');
    const recycled = database.prepare('SELECT * FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid) as Assignment;

    // Without the integration_records DELETE the 2nd markIntegrated INSERT would throw
    // SQLITE_CONSTRAINT: integration_records.workspace_guid.
    const second = finalizeCommanderLocalCommit(database, commanderInput(root, recycled, 'second local integration'));

    expect(second.state).toBe('cleaned');
    expect(readFileSync(join(root, 'second.txt'), 'utf8')).toBe('more approved work\n');
    expect(git(root, 'log', '-1', '--format=%s')).toBe('second local integration');
  });

  it('push-pending integration preserves the worktree and leaves the row integrated (unchanged path)', () => {
    const s = setup(true);
    seedIntegratedPushPending(s);

    // Remote never proved the candidate ref: reconcile stays push-pending, never recycles.
    const result = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
    });

    expect(result.state).toBe('integrated-local');
    expect(existsSync(s.assignment.worktree_path)).toBe(true);
    expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'integrated' });
  });

  it('refuses to recycle a non-integrated (active) assignment and preserves the worktree', () => {
    const { root, database, assignment } = setup(false);
    // The freshly-materialized row is active, not integrated: recycle must refuse.
    expect(() => recycleFinalized(database, root, assignment))
      .toThrow(/Recycle requires a durable integrated assignment/);
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active' });
  });

  it('requires exact private Commander input bound to owner, assignment, ref, and tree', () => {
    const { root, database, assignment } = setup(false);
    const input = commanderInput(root, assignment, 'reviewed local work');
    expect(() => finalizeCommanderLocalCommit(database, {
      ...input, reviewedEvidence: commanderEvidence(assignment),
    } as never)).toThrow('malformed');
    expect(() => finalizeCommanderLocalCommit(database, {
      ...input, providerRootSessionId: OTHER,
    })).toThrow('binding');
    expect(() => finalizeCommanderLocalCommit(database, {
      ...input, workspaceGuid: '33333333-3333-4333-8333-333333333333',
    })).toThrow();

    const wrongRef = setup(false);
    const targetBefore = git(wrongRef.root, 'rev-parse', 'HEAD');
    expect(() => finalizeCommanderLocalCommit(wrongRef.database, {
      ...commanderInput(wrongRef.root, wrongRef.assignment, 'must not target main'),
      localRef: 'refs/heads/main',
    })).toThrow('assignment branch');
    expect(git(wrongRef.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(git(wrongRef.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');

    const replay = setup(false);
    const replayInput = commanderInput(replay.root, replay.assignment, 'one local commit');
    expect(finalizeCommanderLocalCommit(replay.database, replayInput).state).toBe('cleaned');
    expect(() => finalizeCommanderLocalCommit(replay.database, replayInput)).toThrow();
  });

  it('rolls back integration record, integrated lifecycle, and push-pending transition together on durable-mark failure', () => {
    const { root, database, assignment } = setup(true);
    const authority = issueAndVerify(database, assignment, 'commit-and-push', {
      ...commitEvidence(assignment), ...remoteEvidence(assignment),
    });
    expect(() => finalizeDirectAuthority(database, authority, 'atomic durable mark', {
      beforeAtomicIntegrationState: () => { throw new Error('injected durable mark failure'); },
    })).toThrow('injected durable mark failure');
    expect(database.prepare('SELECT lifecycle_status, integrated_commit, disposition FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration', integrated_commit: null });
    expect(database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
      .toMatchObject({ disposition: expect.stringContaining('integration-pending') });
    expect(database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?').get(assignment.workspace_guid)).toBeUndefined();
    expect(git(root, 'log', '-1', '--format=%s')).toBe('atomic durable mark');
    expect(database.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ?')
      .get(assignment.repository_identity)).toBeTruthy();

    const recovered = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
    });
    expect(recovered).toMatchObject({ state: 'integrated-local' });
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({
        lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
      });
  });

  it('durably records remote intent before integration and atomically exposes integrated push-pending state', () => {
    const direct = setup(true);
    const evidence = { ...commitEvidence(direct.assignment), ...remoteEvidence(direct.assignment) } satisfies CommitAndPushEvidence;
    const authority = issueAndVerify(direct.database, direct.assignment, 'commit-and-push', evidence);
    let sawIntegrationPending = false;
    let sawAtomicPushPending = false;

    const result = finalizeDirectAuthority(direct.database, authority, 'durable remote intent', {
      beforeCheckedFastForward: () => {
        const row = direct.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
          .get(direct.assignment.workspace_guid) as { lifecycle_status: string; disposition: string };
        sawIntegrationPending = row.lifecycle_status === 'ready_for_integration'
          && JSON.parse(row.disposition).phase === 'integration-pending';
      },
      beforePushSuccessPersistence: () => {
        const row = direct.database.prepare('SELECT lifecycle_status, integrated_commit, disposition FROM assignments WHERE workspace_guid = ?')
          .get(direct.assignment.workspace_guid) as { lifecycle_status: string; integrated_commit: string; disposition: string };
        const record = direct.database.prepare('SELECT integrated_commit FROM integration_records WHERE workspace_guid = ?')
          .get(direct.assignment.workspace_guid) as { integrated_commit: string };
        sawAtomicPushPending = row.lifecycle_status === 'integrated'
          && JSON.parse(row.disposition).phase === 'push-pending'
          && record.integrated_commit === row.integrated_commit;
        throw new Error('injected push-success persistence failure');
      },
    });

    expect(sawIntegrationPending).toBe(true);
    expect(sawAtomicPushPending).toBe(true);
    expect(result).toMatchObject({
      state: 'integrated-local',
      pushError: expect.stringContaining('Remote is already the exact integrated candidate'),
    });
    expect(git(direct.root, 'ls-remote', '--refs', 'origin', evidence.destinationRef).split(/\s+/)[0])
      .toBe(result.integratedCommit);
    expect(direct.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
      .get(direct.assignment.workspace_guid)).toMatchObject({ disposition: expect.stringContaining('push-pending') });
    expect(reconcileFinalization(direct.database, {
      repositoryPath: direct.root, workspaceGuid: direct.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'cleaned', integratedCommit: result.integratedCommit });

    const uncertain = setup(true);
    const uncertainEvidence = { ...commitEvidence(uncertain.assignment), ...remoteEvidence(uncertain.assignment) } satisfies CommitAndPushEvidence;
    const uncertainAuthority = issueAndVerify(uncertain.database, uncertain.assignment, 'commit-and-push', uncertainEvidence);
    const uncertainResult = finalizeDirectAuthority(uncertain.database, uncertainAuthority, 'remote accepted before transport failure', {
      afterRemoteMutationBeforeResult: () => { throw new Error('simulated transport result failure'); },
    });
    expect(uncertainResult).toMatchObject({ state: 'pushed' });
    expect(git(uncertain.root, 'ls-remote', '--refs', 'origin', uncertainEvidence.destinationRef).split(/\s+/)[0])
      .toBe(uncertainResult.integratedCommit);
  });

  it('preserves the exact cumulative binary effect when replaying multi-commit work onto an advanced target', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'earlier source work');
    writeFileSync(join(assignment.worktree_path, 'later.txt'), 'later\n');
    git(assignment.worktree_path, 'add', 'later.txt');
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));
    writeFileSync(join(root, 'target.txt'), 'advanced target\n');
    git(root, 'add', 'target.txt');
    git(root, 'commit', '-m', 'advance integration target');
    const advancedTarget = git(root, 'rev-parse', 'HEAD');
    let reviewedEffect = '';
    let rebasedEffect = '';

    expect(finalizeDirectAuthority(database, authority, 'later source work', {
      beforeCheckedFastForward: () => {
        reviewedEffect = git(root, 'diff', '--binary', '--full-index', assignment.base_commit,
          `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`);
        rebasedEffect = git(root, 'diff', '--binary', '--full-index', advancedTarget,
          git(assignment.worktree_path, 'rev-parse', 'HEAD'));
      },
    }).state).toBe('cleaned');
    expect(rebasedEffect).toBe(reviewedEffect);
    expect(git(root, 'log', '--format=%s', '-2')).toBe('later source work\nearlier source work');
    expect(readFileSync(join(root, 'target.txt'), 'utf8')).toBe('advanced target\n');
  });

  it('rejects a descendant whose cumulative effect changed after rebase before target mutation', () => {
    const changed = setup(false);
    const authority = issueAndVerify(changed.database, changed.assignment, 'commit', commitEvidence(changed.assignment));
    writeFileSync(join(changed.root, 'target.txt'), 'advanced target\n');
    git(changed.root, 'add', 'target.txt');
    git(changed.root, 'commit', '-m', 'advance integration target');
    const targetBefore = git(changed.root, 'rev-parse', 'HEAD');

    expect(() => finalizeDirectAuthority(changed.database, authority, 'reviewed work', {
      beforeDescendantProof: () => {
        writeFileSync(join(changed.assignment.worktree_path, 'unreviewed.txt'), 'changed after review\n');
        git(changed.assignment.worktree_path, 'add', 'unreviewed.txt');
        git(changed.assignment.worktree_path, 'commit', '--amend', '--no-edit');
      },
    })).toThrow('cumulative effect differs');
    expect(git(changed.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(existsSync(changed.assignment.worktree_path)).toBe(true);
    expect(changed.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(changed.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
  });

  it('freezes only the exact reviewed commit returned by commit creation', () => {
    const changed = setup(false);
    const authority = issueAndVerify(changed.database, changed.assignment, 'commit', commitEvidence(changed.assignment));
    const targetBefore = git(changed.root, 'rev-parse', 'HEAD');

    expect(() => finalizeDirectAuthority(changed.database, authority, 'reviewed exact commit', {
      afterExactCommitBeforeFreeze: () => git(
        changed.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'unreviewed replacement head',
      ),
    })).toThrow('changed before freeze');
    expect(git(changed.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(git(changed.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('unreviewed replacement head');
    expect(existsSync(changed.assignment.worktree_path)).toBe(true);
  });

  it('performs commit-and-push after truthful local integration, while push-only never mutates integration', () => {
    const direct = setup(true);
    const evidence = { ...commitEvidence(direct.assignment), ...remoteEvidence(direct.assignment) } satisfies CommitAndPushEvidence;
    const authority = issueAndVerify(direct.database, direct.assignment, 'commit-and-push', evidence);

    const result = finalizeDirectAuthority(direct.database, authority, 'approved and pushed');

    expect(result.state).toBe('pushed');
    expect(git(direct.root, 'ls-remote', '--refs', 'origin', evidence.destinationRef).split(/\s+/)[0]).toBe(result.integratedCommit);

    const only = setup(true);
    git(only.assignment.worktree_path, 'commit', '-m', 'existing work');
    const pushEvidence: PushEvidence = {
      checkoutMode: 'managed', canonicalBranch: only.assignment.branch, localRef: `refs/heads/${only.assignment.branch}`,
      localOid: git(only.assignment.worktree_path, 'rev-parse', 'HEAD'), ...remoteEvidence(only.assignment),
    };
    const pushAuthority = issueAndVerify(only.database, only.assignment, 'push', pushEvidence);
    const before = only.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(only.assignment.workspace_guid);
    const owner = bindOtherLivePrimaryOwner(only);
    let observedTransportUncertainty = false;
    const pushed = finalizeDirectAuthority(only.database, pushAuthority, 'ignored', {
      afterRemoteMutationBeforeResult: () => {
        observedTransportUncertainty = true;
        throw new Error('simulated push-only transport result failure');
      },
    });
    expect(observedTransportUncertainty).toBe(true);
    expect(pushed.state).toBe('pushed-only');
    expect(git(only.root, 'ls-remote', '--refs', 'origin', pushEvidence.destinationRef).split(/\s+/)[0])
      .toBe(pushEvidence.localOid);
    expect(only.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(only.assignment.workspace_guid)).toEqual(before);
    expect(only.database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(only.assignment.repository_identity)).toMatchObject({
      workspace_guid: owner.workspace_guid,
      owner_session_id: OTHER,
    });
  });

  it('recovers a crash between target CAS and checkout update without discarding primary work', () => {
    const crashed = setup(false);
    const authority = issueAndVerify(crashed.database, crashed.assignment, 'commit', commitEvidence(crashed.assignment));

    expect(() => finalizeDirectAuthority(crashed.database, authority, 'crash boundary', {
      afterTargetCompareAndSwapBeforeCheckout: () => { throw new Error('simulated process crash before checkout update'); },
    })).toThrow('simulated process crash before checkout update');

    const candidate = git(
      crashed.root, 'rev-parse', `refs/ironclaude/finalization/${crashed.assignment.workspace_guid}/candidate`,
    );
    expect(git(crashed.root, 'rev-parse', 'HEAD')).toBe(candidate);
    expect(git(crashed.root, 'status', '--porcelain=v1', '--untracked-files=all')).not.toBe('');
    expect(crashed.database.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ?')
      .get(crashed.assignment.repository_identity)).toBeTruthy();
    const owner = bindOtherLivePrimaryOwner(crashed);

    expect(reconcileFinalization(crashed.database, {
      repositoryPath: crashed.root, workspaceGuid: crashed.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'cleaned', integratedCommit: candidate });
    expect(git(crashed.root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
    // R4 lock-not-stranded pin: reconcileFinalization's interrupted-CAS branch
    // releases the retained lock (releaseExactIntegrationLockIfHeld DELETEs the row).
    // Falsifier: dropping the finally-release at integration.ts:1405-1408 leaves the row.
    expect(crashed.database.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ?')
      .get(crashed.assignment.repository_identity)).toBeUndefined();
    expect(crashed.database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(crashed.assignment.repository_identity)).toMatchObject({
      workspace_guid: owner.workspace_guid,
      owner_session_id: OTHER,
    });
  });

  it.each(['staged', 'unstaged', 'untracked'] as const)(
    'carries forward non-overlapping %s primary work after interrupted CAS under a live owner',
    (kind) => {
      const state = setup(false);
      const authority = issueAndVerify(state.database, state.assignment, 'commit', commitEvidence(state.assignment));
      expect(() => finalizeDirectAuthority(state.database, authority, `post-CAS non-overlap ${kind}`, {
        afterTargetCompareAndSwapBeforeCheckout: () => { throw new Error('simulated post-CAS crash'); },
      })).toThrow('simulated post-CAS crash');
      const candidate = git(
        state.root, 'rev-parse', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/candidate`,
      );
      const owner = bindOtherLivePrimaryOwner(state);
      const local = seedPrimaryChange(state, kind, false);

      expect(reconcileFinalization(state.database, {
        repositoryPath: state.root,
        workspaceGuid: state.assignment.workspace_guid,
        providerRootSessionId: OWNER,
      })).toMatchObject({ state: 'cleaned', integratedCommit: candidate });
      expect(git(state.root, 'rev-parse', 'refs/heads/main')).toBe(candidate);
      expect(git(state.root, 'hash-object', join(state.root, local.changedPath))).toBe(local.beforeHash);
      expect(git(state.root, 'ls-files', '-s', '--', local.changedPath)).toBe(local.beforeIndex);
      expect(git(state.root, 'status', '--porcelain=v1', '--untracked-files=all', '--', local.changedPath))
        .toBe(local.beforeStatus);
      expect(git(state.assignment.worktree_path, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
      expect(state.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(state.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active' });
      expect(state.database.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ?')
        .get(state.assignment.repository_identity)).toBeUndefined();
      expect(state.database.prepare(
        'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
      ).get(state.assignment.repository_identity)).toMatchObject({
        workspace_guid: owner.workspace_guid,
        owner_session_id: OTHER,
      });
    },
  );

  it.each(['staged', 'unstaged', 'untracked'] as const)(
    'refuses overlapping %s primary work after interrupted CAS with exact paths under a live owner',
    (kind) => {
      const state = setup(false);
      if (kind !== 'untracked') {
        writeFileSync(join(state.assignment.worktree_path, 'README.md'), 'approved worker edit\n');
        git(state.assignment.worktree_path, 'add', 'README.md');
      }
      const authority = issueAndVerify(state.database, state.assignment, 'commit', commitEvidence(state.assignment));
      expect(() => finalizeDirectAuthority(state.database, authority, `post-CAS overlap ${kind}`, {
        afterTargetCompareAndSwapBeforeCheckout: () => { throw new Error('simulated post-CAS crash'); },
      })).toThrow('simulated post-CAS crash');
      const owner = bindOtherLivePrimaryOwner(state);
      const local = seedPrimaryChange(state, kind, true);

      expect(() => reconcileFinalization(state.database, {
        repositoryPath: state.root,
        workspaceGuid: state.assignment.workspace_guid,
        providerRootSessionId: OWNER,
      })).toThrow(
        'Crash reconciliation primary checkout has local changes overlapping the carried-forward integration; '
        + `preserving worktree. Overlapping paths: ${local.changedPath}`,
      );
      expect(git(state.root, 'hash-object', join(state.root, local.changedPath))).toBe(local.beforeHash);
      expect(git(state.root, 'ls-files', '-s', '--', local.changedPath)).toBe(local.beforeIndex);
      expect(git(state.root, 'status', '--porcelain=v1', '--untracked-files=all', '--', local.changedPath))
        .toBe(local.beforeStatus);
      expect(existsSync(state.assignment.worktree_path)).toBe(true);
      expect(state.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(state.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
      expect(state.database.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ?')
        .get(state.assignment.repository_identity)).toBeTruthy();
      expect(state.database.prepare(
        'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
      ).get(state.assignment.repository_identity)).toMatchObject({
        workspace_guid: owner.workspace_guid,
        owner_session_id: OTHER,
      });
    },
  );

  it('recovers interrupted CAS off-ref without changing operator branch, bytes, index, or status', () => {
    const state = setup(false);
    git(state.root, 'checkout', '-b', 'operator-feature');
    writeFileSync(join(state.root, 'README.md'), 'operator staged\n');
    git(state.root, 'add', 'README.md');
    writeFileSync(join(state.root, 'README.md'), 'operator unstaged\n');
    writeFileSync(join(state.root, 'operator-notes.txt'), 'operator untracked\n');
    const branch = git(state.root, 'symbolic-ref', '--quiet', '--short', 'HEAD');
    const readmeHash = git(state.root, 'hash-object', join(state.root, 'README.md'));
    const readmeIndex = git(state.root, 'ls-files', '-s', '--', 'README.md');
    const notesHash = git(state.root, 'hash-object', join(state.root, 'operator-notes.txt'));
    const notesIndex = git(state.root, 'ls-files', '-s', '--', 'operator-notes.txt');
    const beforeStatus = git(state.root, 'status', '--porcelain=v1', '--untracked-files=all');
    const authority = issueAndVerify(state.database, state.assignment, 'commit', commitEvidence(state.assignment));
    expect(() => finalizeDirectAuthority(state.database, authority, 'off-ref post-CAS crash', {
      afterTargetCompareAndSwapBeforeCheckout: () => { throw new Error('simulated post-CAS crash'); },
    })).toThrow('simulated post-CAS crash');
    const candidate = git(
      state.root, 'rev-parse', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/candidate`,
    );
    const owner = bindOtherLivePrimaryOwner(state);

    expect(reconcileFinalization(state.database, {
      repositoryPath: state.root,
      workspaceGuid: state.assignment.workspace_guid,
      providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'cleaned', integratedCommit: candidate });
    expect(git(state.root, 'rev-parse', 'refs/heads/main')).toBe(candidate);
    expect(git(state.root, 'symbolic-ref', '--quiet', '--short', 'HEAD')).toBe(branch);
    expect(git(state.root, 'hash-object', join(state.root, 'README.md'))).toBe(readmeHash);
    expect(git(state.root, 'ls-files', '-s', '--', 'README.md')).toBe(readmeIndex);
    expect(git(state.root, 'hash-object', join(state.root, 'operator-notes.txt'))).toBe(notesHash);
    expect(git(state.root, 'ls-files', '-s', '--', 'operator-notes.txt')).toBe(notesIndex);
    expect(git(state.root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe(beforeStatus);
    expect(existsSync(state.assignment.worktree_path)).toBe(true);
    expect(state.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(state.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active' });
    expect(state.database.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ?')
      .get(state.assignment.repository_identity)).toBeUndefined();
    expect(state.database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(state.assignment.repository_identity)).toMatchObject({
      workspace_guid: owner.workspace_guid,
      owner_session_id: OTHER,
    });
  });

  it('preserves commit-and-push intent through conflict repair and pre-fast-forward retry recovery', () => {
    const conflict = setup(true);
    git(conflict.root, 'checkout', '-b', 'target-change');
    writeFileSync(join(conflict.root, 'README.md'), 'target\n');
    git(conflict.root, 'add', 'README.md');
    git(conflict.root, 'commit', '-m', 'target change');
    git(conflict.root, 'checkout', 'main');
    git(conflict.root, 'merge', '--ff-only', 'target-change');
    writeFileSync(join(conflict.assignment.worktree_path, 'README.md'), 'source\n');
    git(conflict.assignment.worktree_path, 'add', 'README.md');
    const conflictEvidence = {
      ...commitEvidence(conflict.assignment), ...remoteEvidence(conflict.assignment),
    } satisfies CommitAndPushEvidence;
    const conflictAuthority = issueAndVerify(conflict.database, conflict.assignment, 'commit-and-push', conflictEvidence);
    expect(() => finalizeDirectAuthority(conflict.database, conflictAuthority, 'conflicted push')).toThrow();
    writeFileSync(join(conflict.assignment.worktree_path, 'README.md'), 'reviewed repair\n');
    git(conflict.assignment.worktree_path, 'add', 'README.md');
    git(conflict.assignment.worktree_path, '-c', 'core.editor=true', 'rebase', '--continue');
    const repairAuthority = issueAndVerify(conflict.database, conflict.assignment, 'commit', commitEvidence(conflict.assignment));

    expect(finalizeDirectAuthority(conflict.database, repairAuthority, 'reviewed repair')).toMatchObject({
      state: 'integrated-local', pushError: expect.stringContaining('Remote has not proved'),
    });
    expect(existsSync(conflict.assignment.worktree_path)).toBe(true);
    expect(conflict.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
      .get(conflict.assignment.workspace_guid)).toMatchObject({
        lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
      });

    const retry = setup(true);
    const retryEvidence = {
      ...commitEvidence(retry.assignment), ...remoteEvidence(retry.assignment),
    } satisfies CommitAndPushEvidence;
    const retryAuthority = issueAndVerify(retry.database, retry.assignment, 'commit-and-push', retryEvidence);
    expect(() => finalizeDirectAuthority(retry.database, retryAuthority, 'retry pending push', {
      beforeCheckedFastForward: () => git(retry.root, 'commit', '--allow-empty', '-m', 'move before fast-forward'),
    })).toThrow('target');

    expect(reconcileFinalization(retry.database, {
      repositoryPath: retry.root, workspaceGuid: retry.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'integrated-local', pushError: expect.stringContaining('Remote has not proved') });
    expect(existsSync(retry.assignment.worktree_path)).toBe(true);
    expect(retry.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
      .get(retry.assignment.workspace_guid)).toMatchObject({
        lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
      });
  });

  it('revalidates exact integration-lock ownership before mutation and after fast-forward before recording', () => {
    const before = setup(false);
    const beforeAuthority = issueAndVerify(before.database, before.assignment, 'commit', commitEvidence(before.assignment));
    const beforeTarget = git(before.root, 'rev-parse', 'HEAD');
    expect(() => finalizeDirectAuthority(before.database, beforeAuthority, 'lock lost before mutation', {
      beforeCheckedFastForward: () => {
        before.database.prepare('DELETE FROM integration_locks WHERE repository_identity = ?')
          .run(before.assignment.repository_identity);
      },
    })).toThrow('integration lock changed');
    expect(git(before.root, 'rev-parse', 'HEAD')).toBe(beforeTarget);
    expect(before.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
      .get(before.assignment.workspace_guid)).toBeUndefined();
    expect(existsSync(before.assignment.worktree_path)).toBe(true);

    const after = setup(true);
    const afterEvidence = { ...commitEvidence(after.assignment), ...remoteEvidence(after.assignment) } satisfies CommitAndPushEvidence;
    const afterAuthority = issueAndVerify(after.database, after.assignment, 'commit-and-push', afterEvidence);
    expect(() => finalizeDirectAuthority(after.database, afterAuthority, 'lock lost after mutation', {
      afterCheckedFastForwardBeforeRecord: () => {
        after.database.prepare('DELETE FROM integration_locks WHERE repository_identity = ?')
          .run(after.assignment.repository_identity);
      },
    })).toThrow('integration lock changed');
    const candidate = git(after.root, 'rev-parse', `refs/ironclaude/finalization/${after.assignment.workspace_guid}/candidate`);
    expect(git(after.root, 'rev-parse', 'HEAD')).toBe(candidate);
    expect(after.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
      .get(after.assignment.workspace_guid)).toBeUndefined();
    expect(existsSync(after.assignment.worktree_path)).toBe(true);
    expect(() => reconcileFinalization(after.database, {
      repositoryPath: after.root, workspaceGuid: after.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('lock proof');
    expect(existsSync(after.assignment.worktree_path)).toBe(true);
    expect(after.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
      .get(after.assignment.workspace_guid)).toMatchObject({ disposition: expect.stringContaining('integration-pending') });
    expect(git(after.root, 'ls-remote', '--refs', 'origin', afterEvidence.destinationRef)).toBe('');

    const movedAfter = setup(false);
    const movedAfterAuthority = issueAndVerify(
      movedAfter.database, movedAfter.assignment, 'commit', commitEvidence(movedAfter.assignment),
    );
    expect(() => finalizeDirectAuthority(movedAfter.database, movedAfterAuthority, 'target moved after fast-forward', {
      afterCheckedFastForwardBeforeRecord: () => git(movedAfter.root, 'commit', '--allow-empty', '-m', 'late target move'),
    })).toThrow('inconsistent after checked fast-forward');
    expect(movedAfter.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
      .get(movedAfter.assignment.workspace_guid)).toBeUndefined();
    expect(existsSync(movedAfter.assignment.worktree_path)).toBe(true);
    expect(() => reconcileFinalization(movedAfter.database, {
      repositoryPath: movedAfter.root, workspaceGuid: movedAfter.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('exact candidate');
    expect(existsSync(movedAfter.assignment.worktree_path)).toBe(true);

    const casMoved = setup(false);
    const casAuthority = issueAndVerify(casMoved.database, casMoved.assignment, 'commit', commitEvidence(casMoved.assignment));
    expect(() => finalizeDirectAuthority(casMoved.database, casAuthority, 'CAS target race', {
      beforeTargetCompareAndSwap: () => git(casMoved.root, 'commit', '--allow-empty', '-m', 'move at CAS boundary'),
    })).toThrow();
    expect(git(casMoved.root, 'log', '-1', '--format=%s')).toBe('move at CAS boundary');
    expect(casMoved.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
      .get(casMoved.assignment.workspace_guid)).toBeUndefined();
  });

  it('preserves work on rebase conflict, target movement, primary ownership, and held integration lock', () => {
    const conflict = setup(false);
    git(conflict.root, 'checkout', '-b', 'target-change');
    writeFileSync(join(conflict.root, 'README.md'), 'target\n');
    git(conflict.root, 'add', 'README.md');
    git(conflict.root, 'commit', '-m', 'target change');
    git(conflict.root, 'checkout', 'main');
    git(conflict.root, 'merge', '--ff-only', 'target-change');
    writeFileSync(join(conflict.assignment.worktree_path, 'README.md'), 'source\n');
    git(conflict.assignment.worktree_path, 'add', 'README.md');
    const conflictAuthority = issueAndVerify(conflict.database, conflict.assignment, 'commit', commitEvidence(conflict.assignment));
    expect(() => finalizeDirectAuthority(conflict.database, conflictAuthority, 'conflict')).toThrow();
    expect(existsSync(conflict.assignment.worktree_path)).toBe(true);
    expect(conflict.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(conflict.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'ready_for_integration' });
    expect(() => reconcileFinalization(conflict.database, {
      repositoryPath: conflict.root, workspaceGuid: conflict.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('rebase-conflict repair');
    writeFileSync(join(conflict.assignment.worktree_path, 'README.md'), 'reviewed repair\n');
    git(conflict.assignment.worktree_path, 'add', 'README.md');
    git(conflict.assignment.worktree_path, '-c', 'core.editor=true', 'rebase', '--continue');
    expect(() => reconcileFinalization(conflict.database, {
      repositoryPath: conflict.root, workspaceGuid: conflict.assignment.workspace_guid, providerRootSessionId: OWNER,
      reviewedRepairEvidence: { arbitrary: 'structural data cannot authorize repair' },
    } as never)).toThrow('fresh trusted human repair authority');
    const repairAuthority = issueAndVerify(
      conflict.database, conflict.assignment, 'commit', commitEvidence(conflict.assignment),
    );
    expect(finalizeDirectAuthority(conflict.database, repairAuthority, 'fresh reviewed repair attestation').state)
      .toBe('cleaned');

    const moved = setup(false);
    const movedAuthority = issueAndVerify(moved.database, moved.assignment, 'commit', commitEvidence(moved.assignment));
    const original = git(moved.root, 'rev-parse', 'HEAD');
    expect(() => finalizeDirectAuthority(moved.database, movedAuthority, 'stale target', {
      beforeCheckedFastForward: () => git(moved.root, 'commit', '--allow-empty', '-m', 'operator moved target'),
    })).toThrow('target');
    expect(git(moved.root, 'rev-parse', 'HEAD')).not.toBe(original);
    expect(existsSync(moved.assignment.worktree_path)).toBe(true);
    expect(moved.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(moved.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'ready_for_integration' });
    expect(reconcileFinalization(moved.database, {
      repositoryPath: moved.root, workspaceGuid: moved.assignment.workspace_guid, providerRootSessionId: OWNER,
    }).state).toBe('cleaned');

    const primary = setup(false);
    const primaryAuthority = issueAndVerify(primary.database, primary.assignment, 'commit', commitEvidence(primary.assignment));
    acquirePrimaryCheckoutOwnership(primary.database, { repositoryIdentity: primary.assignment.repository_identity, workspaceGuid: primary.assignment.workspace_guid, ownerSessionId: OWNER });
    expect(() => finalizeDirectAuthority(primary.database, primaryAuthority, 'fenced')).toThrow('effective checkout changed');

    const locked = setup(false);
    acquireIntegrationLock(locked.database, { repositoryIdentity: locked.assignment.repository_identity, workspaceGuid: locked.assignment.workspace_guid, targetRef: 'refs/heads/main', expectedTarget: git(locked.root, 'rev-parse', 'HEAD') });
    const lockedAuthority = issueAndVerify(locked.database, locked.assignment, 'commit', commitEvidence(locked.assignment));
    expect(() => finalizeDirectAuthority(locked.database, lockedAuthority, 'serialized')).toThrow('already held');
    expect(locked.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(locked.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'ready_for_integration' });
    releaseIntegrationLock(locked.database, locked.assignment.repository_identity, locked.assignment.workspace_guid);
    expect(reconcileFinalization(locked.database, {
      repositoryPath: locked.root, workspaceGuid: locked.assignment.workspace_guid, providerRootSessionId: OWNER,
    }).state).toBe('cleaned');

    const nonDescendant = setup(false);
    const nonDescendantAuthority = issueAndVerify(
      nonDescendant.database, nonDescendant.assignment, 'commit', commitEvidence(nonDescendant.assignment),
    );
    git(nonDescendant.root, 'commit', '--allow-empty', '-m', 'advanced target');
    expect(() => finalizeDirectAuthority(nonDescendant.database, nonDescendantAuthority, 'negative proof', {
      beforeDescendantProof: () => git(nonDescendant.assignment.worktree_path, 'reset', '--hard', nonDescendant.assignment.base_commit),
    })).toThrow('descendant proof');
    expect(nonDescendant.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(nonDescendant.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'ready_for_integration' });
  }, 60_000);

  it('rejects a ready-repair finalize that lacks durable frozen state before mutating a local source commit', () => {
    // B-prime: a dirty or off-ref primary checkout is no longer rejected here — that
    // behavior is covered by the case-1/case-2/case-3 tests. This test retains only
    // the ready-repair-without-frozen guard, which is independent of primary state.
    const inactive = setup(false);
    const inactiveAuthority = issueAndVerify(inactive.database, inactive.assignment, 'commit', commitEvidence(inactive.assignment));
    inactive.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(inactive.assignment.workspace_guid);
    expect(() => finalizeDirectAuthority(inactive.database, inactiveAuthority, 'invalid lifecycle')).toThrow('lacks durable frozen');
    expect(git(inactive.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');
  });

  it('refuses to freeze a dirty managed worktree, leaving the assignment active and unfrozen', () => {
    const { database, assignment } = setup(false);
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));
    writeFileSync(join(assignment.worktree_path, 'stray.txt'), 'untracked stray file\n');

    expect(() => finalizeDirectAuthority(database, authority, 'dirty tree at freeze'))
      .toThrow(/uncommitted changes[\s\S]*stray\.txt/);

    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active' });
  });

  it('rejects post-review index mutation for direct and reviewed Commander commits', () => {
    const direct = setup(false);
    const directAuthority = issueAndVerify(direct.database, direct.assignment, 'commit', commitEvidence(direct.assignment));
    writeFileSync(join(direct.assignment.worktree_path, 'work.txt'), 'changed after review\n');
    git(direct.assignment.worktree_path, 'add', 'work.txt');
    expect(() => finalizeDirectAuthority(direct.database, directAuthority, 'stale direct')).toThrow('Reviewed commit evidence changed');
    expect(git(direct.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');

    const commander = setup(false);
    const reviewed = commanderEvidence(commander.assignment);
    writeFileSync(join(commander.assignment.worktree_path, 'work.txt'), 'changed after review\n');
    git(commander.assignment.worktree_path, 'add', 'work.txt');
    expect(() => finalizeCommanderLocalCommit(commander.database, {
      ...commanderInput(commander.root, commander.assignment, 'stale commander'),
      ...reviewed,
    })).toThrow('Reviewed commit evidence changed');
    expect(git(commander.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');
  });

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

  it('pushes under another live primary owner without mutating integration', () => {
    const { root, database, assignment } = setup(true);
    git(assignment.worktree_path, 'commit', '-m', 'push-only work');
    const evidence: PushEvidence = {
      checkoutMode: 'managed', canonicalBranch: assignment.branch, localRef: `refs/heads/${assignment.branch}`,
      localOid: git(assignment.worktree_path, 'rev-parse', 'HEAD'), ...remoteEvidence(assignment),
    };
    const authority = issueAndVerify(database, assignment, 'push', evidence);
    const before = database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid);
    const owner = bindOtherLivePrimaryOwner({ root, database, assignment });
    expect(finalizeDirectAuthority(database, authority, 'ignored').state).toBe('pushed-only');
    expect(git(root, 'ls-remote', '--refs', 'origin', evidence.destinationRef).split(/\s+/)[0]).toBe(evidence.localOid);
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid)).toEqual(before);
    expect(database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(assignment.repository_identity)).toMatchObject({
      workspace_guid: owner.workspace_guid,
      owner_session_id: OTHER,
    });
  });

  it('reconciles a crash only from durable integration reachability and keeps integrated-local state after push failure', () => {
    const mismatched = setup(false);
    git(mismatched.assignment.worktree_path, 'commit', '-m', 'candidate work');
    const candidate = git(mismatched.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(mismatched.root, 'update-ref', `refs/ironclaude/finalization/${mismatched.assignment.workspace_guid}/candidate`, candidate);
    mismatched.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(mismatched.assignment.workspace_guid);
    git(mismatched.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'unproved drift');
    expect(() => reconcileFinalization(mismatched.database, {
      repositoryPath: mismatched.root, workspaceGuid: mismatched.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('candidate and source HEAD differ');

    const unreachable = setup(false);
    git(unreachable.assignment.worktree_path, 'commit', '-m', 'unreachable work');
    const unreachableCommit = git(unreachable.assignment.worktree_path, 'rev-parse', 'HEAD');
    unreachable.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(unreachable.assignment.workspace_guid);
    recordIntegration(unreachable.database, {
      workspaceGuid: unreachable.assignment.workspace_guid, repositoryIdentity: unreachable.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: unreachableCommit,
    });
    expect(() => reconcileFinalization(unreachable.database, {
      repositoryPath: unreachable.root, workspaceGuid: unreachable.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('reachable integration proof');
    expect(existsSync(unreachable.assignment.worktree_path)).toBe(true);

    const advancedWithoutRecord = setup(false);
    git(advancedWithoutRecord.assignment.worktree_path, 'commit', '-m', 'advanced before record');
    const advancedCommit = git(advancedWithoutRecord.assignment.worktree_path, 'rev-parse', 'HEAD');
    const advancedOldTarget = git(advancedWithoutRecord.root, 'rev-parse', 'HEAD');
    git(advancedWithoutRecord.root, 'update-ref', `refs/ironclaude/finalization/${advancedWithoutRecord.assignment.workspace_guid}/frozen`, advancedCommit);
    acquireIntegrationLock(advancedWithoutRecord.database, {
      repositoryIdentity: advancedWithoutRecord.assignment.repository_identity,
      workspaceGuid: advancedWithoutRecord.assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: advancedOldTarget,
    });
    git(advancedWithoutRecord.root, 'merge', '--ff-only', advancedCommit);
    git(advancedWithoutRecord.root, 'update-ref', `refs/ironclaude/finalization/${advancedWithoutRecord.assignment.workspace_guid}/candidate`, advancedCommit);
    advancedWithoutRecord.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(advancedWithoutRecord.assignment.workspace_guid);
    expect(reconcileFinalization(advancedWithoutRecord.database, {
      repositoryPath: advancedWithoutRecord.root, workspaceGuid: advancedWithoutRecord.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'cleaned', integratedCommit: advancedCommit });

    const crash = setup(false);
    git(crash.assignment.worktree_path, 'commit', '-m', 'durable work');
    const integrated = git(crash.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(crash.root, 'merge', '--ff-only', integrated);
    git(crash.root, 'update-ref', `refs/ironclaude/finalization/${crash.assignment.workspace_guid}/candidate`, integrated);
    crash.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration', integrated_commit = ? WHERE workspace_guid = ?")
      .run(integrated, crash.assignment.workspace_guid);
    recordIntegration(crash.database, { workspaceGuid: crash.assignment.workspace_guid, repositoryIdentity: crash.assignment.repository_identity, targetRef: 'refs/heads/main', integratedCommit: integrated });
    expect(reconcileFinalization(crash.database, { repositoryPath: crash.root, workspaceGuid: crash.assignment.workspace_guid, providerRootSessionId: OWNER }).state)
      .toBe('cleaned');

    const failedPush = setup(true);
    const evidence = { ...commitEvidence(failedPush.assignment), ...remoteEvidence(failedPush.assignment) } satisfies CommitAndPushEvidence;
    const authority = issueAndVerify(failedPush.database, failedPush.assignment, 'commit-and-push', evidence);
    git(failedPush.assignment.worktree_path, 'remote', 'set-url', 'origin', 'file:///missing-remote');
    const result = finalizeDirectAuthority(failedPush.database, authority, 'push fails');
    expect(result.state).toBe('integrated-local');
    expect(result.pushError).toBeTruthy();
    expect(failedPush.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(failedPush.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'integrated' });
    expect(existsSync(failedPush.assignment.worktree_path)).toBe(true);

    const pendingRecord = setup(true);
    git(pendingRecord.assignment.worktree_path, 'commit', '-m', 'candidate before durable state');
    const pendingCandidate = git(pendingRecord.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(pendingRecord.root, 'merge', '--ff-only', pendingCandidate);
    git(pendingRecord.root, 'update-ref', `refs/ironclaude/finalization/${pendingRecord.assignment.workspace_guid}/frozen`, pendingCandidate);
    git(pendingRecord.root, 'update-ref', `refs/ironclaude/finalization/${pendingRecord.assignment.workspace_guid}/candidate`, pendingCandidate);
    const pendingDestination = `refs/heads/${pendingRecord.assignment.branch}`;
    pendingRecord.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'ready_for_integration', disposition = ? WHERE workspace_guid = ?
    `).run(JSON.stringify({
      phase: 'integration-pending', frozenCommit: pendingCandidate, remoteName: 'origin',
      remoteUrl: git(pendingRecord.assignment.worktree_path, 'remote', 'get-url', 'origin'),
      destinationRef: pendingDestination, expectedRemoteOldOid: null,
    }), pendingRecord.assignment.workspace_guid);
    recordIntegration(pendingRecord.database, {
      workspaceGuid: pendingRecord.assignment.workspace_guid,
      repositoryIdentity: pendingRecord.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: pendingCandidate,
    });
    expect(reconcileFinalization(pendingRecord.database, {
      repositoryPath: pendingRecord.root, workspaceGuid: pendingRecord.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'integrated-local', integratedCommit: pendingCandidate });
    expect(existsSync(pendingRecord.assignment.worktree_path)).toBe(true);
    expect(pendingRecord.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
      .get(pendingRecord.assignment.workspace_guid)).toMatchObject({
        lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending'),
      });
  });

  it('self-heals a reused-GUID strand carrying a stale integration record, by deleting it and re-integrating', () => {
    const stale = setup(true);
    git(stale.assignment.worktree_path, 'commit', '-m', 'candidate work');
    const candidateCommit = git(stale.assignment.worktree_path, 'rev-parse', 'HEAD');
    const preFfTip = git(stale.root, 'rev-parse', 'HEAD');
    git(stale.root, 'update-ref', `refs/ironclaude/finalization/${stale.assignment.workspace_guid}/frozen`, candidateCommit);
    acquireIntegrationLock(stale.database, {
      repositoryIdentity: stale.assignment.repository_identity,
      workspaceGuid: stale.assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: preFfTip,
    });
    git(stale.root, 'merge', '--ff-only', candidateCommit);
    git(stale.root, 'update-ref', `refs/ironclaude/finalization/${stale.assignment.workspace_guid}/candidate`, candidateCommit);
    const destinationRef = `refs/heads/${stale.assignment.branch}`;
    stale.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'ready_for_integration', disposition = ? WHERE workspace_guid = ?
    `).run(JSON.stringify({
      phase: 'integration-pending', frozenCommit: candidateCommit, remoteName: 'origin',
      remoteUrl: git(stale.assignment.worktree_path, 'remote', 'get-url', 'origin'),
      destinationRef, expectedRemoteOldOid: null,
    }), stale.assignment.workspace_guid);
    // Falsifier: pre-fix a truthy record ALWAYS takes the record-present branch, so
    // this stale row (integrated_commit = the pre-ff tip, not the ff'd candidate)
    // throws immediately instead of self-healing.
    recordIntegration(stale.database, {
      workspaceGuid: stale.assignment.workspace_guid,
      repositoryIdentity: stale.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: preFfTip,
    });

    const result = reconcileFinalization(stale.database, {
      repositoryPath: stale.root, workspaceGuid: stale.assignment.workspace_guid, providerRootSessionId: OWNER,
    });
    expect(result).toMatchObject({ state: 'integrated-local', integratedCommit: candidateCommit });
    expect(existsSync(stale.assignment.worktree_path)).toBe(true);
    expect(stale.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(stale.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'integrated' });
    expect(stale.database.prepare('SELECT integrated_commit FROM integration_records WHERE workspace_guid = ?')
      .get(stale.assignment.workspace_guid)).toMatchObject({ integrated_commit: candidateCommit });
    expect(git(stale.root, 'ls-remote', '--refs', 'origin', destinationRef)).toBe('');
  });

  it('clears a proven-leftover candidate ref alongside a stale record and self-heals via frozen replay (push-human-only preserved)', () => {
    const leftover = setup(true);
    const rootTip = git(leftover.root, 'rev-parse', 'HEAD');
    git(leftover.assignment.worktree_path, 'commit', '-m', 'work F');
    const workF = git(leftover.assignment.worktree_path, 'rev-parse', 'HEAD');
    // Main advances independently of the assignment (never merges workF), so the
    // pre-existing tip is a strict ancestor of the new target and distinct from F.
    git(leftover.root, 'commit', '--allow-empty', '-m', 'main advances independently');
    const staleCandidateTree = git(leftover.root, 'rev-parse', `${rootTip}^{tree}`);
    const staleCandidate = git(leftover.root, 'commit-tree', staleCandidateTree, '-p', rootTip, '-m', 'stale leftover candidate');
    git(leftover.root, 'update-ref', `refs/ironclaude/finalization/${leftover.assignment.workspace_guid}/frozen`, workF);
    git(leftover.root, 'update-ref', `refs/ironclaude/finalization/${leftover.assignment.workspace_guid}/candidate`, staleCandidate);
    const destinationRef = `refs/heads/${leftover.assignment.branch}`;
    leftover.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'ready_for_integration', disposition = ? WHERE workspace_guid = ?
    `).run(JSON.stringify({
      phase: 'integration-pending', frozenCommit: workF, remoteName: 'origin',
      remoteUrl: git(leftover.assignment.worktree_path, 'remote', 'get-url', 'origin'),
      destinationRef, expectedRemoteOldOid: null,
    }), leftover.assignment.workspace_guid);
    // No integration lock held: this exercises recovery from a durable ready row
    // with no lock, no fresh candidate, only a frozen commit and a leftover ref.
    recordIntegration(leftover.database, {
      workspaceGuid: leftover.assignment.workspace_guid,
      repositoryIdentity: leftover.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: rootTip,
    });

    const result = reconcileFinalization(leftover.database, {
      repositoryPath: leftover.root, workspaceGuid: leftover.assignment.workspace_guid, providerRootSessionId: OWNER,
    });
    expect(result.state).toBe('integrated-local');
    // The frozen-replay path structurally cannot push (the only push sites are
    // inside finalizeDirectAuthority), so this is a hard push-human-only guard.
    expect(git(leftover.root, 'ls-remote', '--refs', 'origin', destinationRef)).toBe('');
    expect(existsSync(leftover.assignment.worktree_path)).toBe(true);
  });

  it('refuses a stale record with no candidate ref: absence is not provable staleness, and the record is never deleted', () => {
    const absentCandidate = setup(false);
    const originalTip = git(absentCandidate.root, 'rev-parse', 'HEAD');
    git(absentCandidate.root, 'commit', '--allow-empty', '-m', 'main advances');
    absentCandidate.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(absentCandidate.assignment.workspace_guid);
    recordIntegration(absentCandidate.database, {
      workspaceGuid: absentCandidate.assignment.workspace_guid,
      repositoryIdentity: absentCandidate.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: originalTip,
    });
    expect(() => reconcileFinalization(absentCandidate.database, {
      repositoryPath: absentCandidate.root, workspaceGuid: absentCandidate.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('reachable integration proof');
    expect(absentCandidate.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
      .get(absentCandidate.assignment.workspace_guid)).toBeTruthy();
  });

  it('restores an integrated C&P candidate from durable pending-push state before judging remote outcome', () => {
    const pending = setup(true);
    git(pending.assignment.worktree_path, 'commit', '-m', 'frozen authorized commit');
    const frozen = git(pending.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(pending.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'rebased integration candidate');
    const candidate = git(pending.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(pending.root, 'merge', '--ff-only', candidate);
    git(pending.root, 'update-ref', `refs/ironclaude/finalization/${pending.assignment.workspace_guid}/frozen`, frozen);
    git(pending.root, 'update-ref', `refs/ironclaude/finalization/${pending.assignment.workspace_guid}/candidate`, candidate);
    const destinationRef = `refs/heads/${pending.assignment.branch}`;
    const disposition = JSON.stringify({
      phase: 'push-pending', candidateCommit: candidate, frozenCommit: frozen,
      remoteName: 'origin', remoteUrl: git(pending.assignment.worktree_path, 'remote', 'get-url', 'origin'),
      destinationRef, expectedRemoteOldOid: null,
    });
    recordIntegration(pending.database, {
      workspaceGuid: pending.assignment.workspace_guid, repositoryIdentity: pending.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: candidate,
    });
    pending.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ?
      WHERE workspace_guid = ?
    `).run(candidate, candidate, disposition, pending.assignment.workspace_guid);
    git(pending.assignment.worktree_path, 'reset', '--hard', frozen);

    const result = reconcileFinalization(pending.database, {
      repositoryPath: pending.root, workspaceGuid: pending.assignment.workspace_guid, providerRootSessionId: OWNER,
    });

    expect(result).toMatchObject({ state: 'integrated-local', integratedCommit: candidate });
    expect(git(pending.assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(candidate);
    expect(existsSync(pending.assignment.worktree_path)).toBe(true);
    expect(pending.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?').get(pending.assignment.workspace_guid))
      .toMatchObject({ disposition });
    expect(git(pending.root, 'ls-remote', '--refs', 'origin', destinationRef)).toBe('');

    const divergent = setup(true);
    const divergentState = seedIntegratedPushPending(divergent);
    git(divergent.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'new unresolved work');
    const divergentHead = git(divergent.assignment.worktree_path, 'rev-parse', 'HEAD');
    expect(() => reconcileFinalization(divergent.database, {
      repositoryPath: divergent.root, workspaceGuid: divergent.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow('source HEAD differs');
    expect(git(divergent.assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(divergentHead);
    expect(divergentHead).not.toBe(divergentState.candidate);
  });

  it('cleans pending-push recovery only after remote proves exact integrated candidate was pushed', () => {
    const pushed = setup(true);
    git(pushed.assignment.worktree_path, 'commit', '-m', 'frozen authorized commit');
    const frozen = git(pushed.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(pushed.assignment.worktree_path, 'commit', '--allow-empty', '-m', 'rebased integration candidate');
    const candidate = git(pushed.assignment.worktree_path, 'rev-parse', 'HEAD');
    git(pushed.root, 'merge', '--ff-only', candidate);
    git(pushed.root, 'update-ref', `refs/ironclaude/finalization/${pushed.assignment.workspace_guid}/frozen`, frozen);
    git(pushed.root, 'update-ref', `refs/ironclaude/finalization/${pushed.assignment.workspace_guid}/candidate`, candidate);
    const destinationRef = `refs/heads/${pushed.assignment.branch}`;
    const disposition = JSON.stringify({
      phase: 'push-pending', candidateCommit: candidate, frozenCommit: frozen,
      remoteName: 'origin', remoteUrl: git(pushed.assignment.worktree_path, 'remote', 'get-url', 'origin'),
      destinationRef, expectedRemoteOldOid: null,
    });
    recordIntegration(pushed.database, {
      workspaceGuid: pushed.assignment.workspace_guid, repositoryIdentity: pushed.assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit: candidate,
    });
    pushed.database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ?
      WHERE workspace_guid = ?
    `).run(candidate, candidate, disposition, pushed.assignment.workspace_guid);
    git(pushed.assignment.worktree_path, 'reset', '--hard', frozen);
    git(pushed.root, 'push', 'origin', `${candidate}:${destinationRef}`);

    const result = reconcileFinalization(pushed.database, {
      repositoryPath: pushed.root, workspaceGuid: pushed.assignment.workspace_guid, providerRootSessionId: OWNER,
    });

    expect(result).toMatchObject({ state: 'cleaned', integratedCommit: candidate });
    expect(existsSync(pushed.assignment.worktree_path)).toBe(true);
    expect(pushed.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?').get(pushed.assignment.workspace_guid))
      .toMatchObject({ disposition: null });
    expect(git(pushed.root, 'ls-remote', '--refs', 'origin', destinationRef).split(/\s+/)[0]).toBe(candidate);
  });

  it('preserves pending-push recovery on ambiguous or failed remote readback', () => {
    const ambiguous = setup(true);
    const ambiguousState = seedIntegratedPushPending(ambiguous);
    git(ambiguous.root, 'push', 'origin', `${ambiguousState.frozen}:${ambiguousState.destinationRef}`);

    expect(reconcileFinalization(ambiguous.database, {
      repositoryPath: ambiguous.root, workspaceGuid: ambiguous.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({
      state: 'integrated-local', integratedCommit: ambiguousState.candidate,
      pushError: expect.stringContaining('ambiguous'),
    });
    expect(existsSync(ambiguous.assignment.worktree_path)).toBe(true);
    expect(ambiguous.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
      .get(ambiguous.assignment.workspace_guid)).toMatchObject({ disposition: ambiguousState.disposition });

    const repointed = setup(true);
    const repointedState = seedIntegratedPushPending(repointed);
    const otherRemote = mkdtempSync(join(tmpdir(), 'ironclaude-repointed-remote-'));
    directories.push(otherRemote);
    git(otherRemote, 'init', '--bare');
    git(repointed.root, 'push', otherRemote, `${repointedState.candidate}:${repointedState.destinationRef}`);
    git(repointed.assignment.worktree_path, 'remote', 'set-url', 'origin', otherRemote);
    expect(reconcileFinalization(repointed.database, {
      repositoryPath: repointed.root, workspaceGuid: repointed.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'integrated-local', integratedCommit: repointedState.candidate });
    expect(existsSync(repointed.assignment.worktree_path)).toBe(true);

    const failed = setup(true);
    const failedState = seedIntegratedPushPending(failed);
    const failedDisposition = JSON.stringify({
      ...JSON.parse(failedState.disposition), remoteUrl: 'file:///missing-readback-remote',
    });
    failed.database.prepare('UPDATE assignments SET disposition = ? WHERE workspace_guid = ?')
      .run(failedDisposition, failed.assignment.workspace_guid);
    expect(() => reconcileFinalization(failed.database, {
      repositoryPath: failed.root, workspaceGuid: failed.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toThrow();
    expect(existsSync(failed.assignment.worktree_path)).toBe(true);
    expect(failed.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
      .get(failed.assignment.workspace_guid)).toMatchObject({ disposition: failedDisposition });
  });

  });
  }

  if (part === 'recovery') {
  describe('recovery integration', () => {
  it('managed rebase recovery: a conflict-free mid-rebase continues, proofs pass, integrates (case A)', () => {
    const { root, database, assignment } = setup(false);
    const wt = assignment.worktree_path;
    const base = assignment.base_commit;
    // Reviewed work is a single disjoint-file commit (work.txt was staged by setup()).
    git(wt, 'commit', '-m', 'reviewed work');
    const frozen = git(wt, 'rev-parse', 'HEAD');
    // Advance the target with a disjoint file so the replay never conflicts.
    writeFileSync(join(root, 'target.txt'), 'advanced target\n');
    git(root, 'add', 'target.txt');
    git(root, 'commit', '-m', 'advance integration target');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozen);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);
    // Seed a genuine conflict-free mid-rebase: rebase --onto main base, paused on `edit`.
    try {
      execFileSync('git', ['-C', wt, '-c', 'core.editor=true', 'rebase', '-i', '--onto', 'main', base], {
        encoding: 'utf8', env: { ...process.env, GIT_SEQUENCE_EDITOR: "sed -i.bak 's/^pick/edit/'" },
      });
    } catch { /* rebase pauses at the `edit` stop; the state is asserted below */ }
    expect(existsSync(resolve(wt, git(wt, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(true);
    expect(git(wt, 'diff', '--name-only', '--diff-filter=U')).toBe('');

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'continue',
    });

    expect(result.state).toBe('cleaned');
    expect(result.integratedCommit).toBe(git(root, 'rev-parse', 'HEAD'));
    expect(readFileSync(join(root, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(readFileSync(join(root, 'target.txt'), 'utf8')).toBe('advanced target\n');
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
    expect(existsSync(wt)).toBe(true);
  });

  it('managed rebase recovery: unresolved conflicts STOP and do not advance the target (case B)', () => {
    const s = seedConflictMidRebase();
    const targetBefore = git(s.root, 'rev-parse', 'HEAD');
    expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toContain('README.md');

    // The 'unresolved conflicts remain' prefix proves the op STOPPED before ever
    // running `rebase --continue` — a re-conflict during continue carries a
    // different prefix, so pinning the prefix (not just the path) falsifies the
    // no-auto-resolve guard.
    expect(() => reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'continue',
    })).toThrow('unresolved conflicts remain');
    expect(() => reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'continue',
    })).toThrow('README.md');

    expect(git(s.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    // The rebase is left exactly as found — untouched, still in progress.
    expect(existsSync(resolve(s.assignment.worktree_path,
      git(s.assignment.worktree_path, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(true);
    expect(existsSync(s.assignment.worktree_path)).toBe(true);
    expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
  });

  it('managed rebase recovery: abort restores the frozen pre-rebase commit, target unchanged (case C)', () => {
    const s = seedConflictMidRebase();
    const frozen = git(s.root, 'rev-parse', `refs/ironclaude/finalization/${s.assignment.workspace_guid}/frozen`);
    const targetBefore = git(s.root, 'rev-parse', 'HEAD');

    const result = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'abort',
    });

    expect(result.state).toBe('rebase-aborted');
    expect(git(s.assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(frozen);
    expect(git(s.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(existsSync(s.assignment.worktree_path)).toBe(true);
  });

  it('managed rebase recovery: a content-changing resolution is REJECTED by the equality proof and routes to isRepair (case D)', () => {
    const s = seedConflictMidRebase();
    const wt = s.assignment.worktree_path;
    const targetBefore = git(s.root, 'rev-parse', 'HEAD');
    // Operator resolves the conflict in a way that CHANGES the tree (differs from reviewed 'source').
    writeFileSync(join(wt, 'README.md'), 'resolved differently\n');
    git(wt, 'add', 'README.md');
    expect(git(wt, 'diff', '--name-only', '--diff-filter=U')).toBe('');

    const result = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'continue',
    });

    // The equality proof rejects the changed content: no equality-path integration.
    expect(result.state).toBe('rebase-recovery-repair-required');
    expect(git(s.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(existsSync(wt)).toBe(true);
    expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });

    // Routing proof: only a fresh isRepair commit authority integrates, advancing the target.
    writeFileSync(join(wt, 'repair.txt'), 'fresh repair\n');
    git(wt, 'add', 'repair.txt');
    const repairAuthority = issueAndVerify(s.database, s.assignment, 'commit', commitEvidence(s.assignment));
    expect(finalizeDirectAuthority(s.database, repairAuthority, 'fresh reviewed repair').state).toBe('cleaned');
    expect(git(s.root, 'rev-parse', 'HEAD')).not.toBe(targetBefore);
    // The resolved content and the fresh repair both landed on the target THROUGH
    // the isRepair channel — never through the equality path.
    expect(readFileSync(join(s.root, 'README.md'), 'utf8')).toBe('resolved differently\n');
    expect(existsSync(join(s.root, 'repair.txt'))).toBe(true);
    expect(existsSync(wt)).toBe(true);
  });

  it('managed rebase recovery: refuses when no rebase is in progress instead of integrating', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'reviewed work');
    const frozen = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozen);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);
    const targetBefore = git(root, 'rev-parse', 'HEAD');

    expect(() => reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'abort',
    })).toThrow('no rebase is in progress');

    expect(git(root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
  });

  it('managed no-rebase recovery: status reports frozen-no-rebase, then rerebase replays frozen work onto the drifted target, ATTACHED, without integrating (case a)', () => {
    const { root, database, assignment, wt } = seedFrozenReadyNoRebase(false);
    const targetBefore = git(root, 'rev-parse', 'HEAD');

    const status = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('frozen-no-rebase');

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'rerebase',
    });

    expect(result.state).toBe('rebase-rerebased-ready-for-repair');
    // ATTACHED: symbolic-ref succeeds (nonzero/throw when detached) and equals the branch.
    expect(git(wt, 'symbolic-ref', '--quiet', '--short', 'HEAD')).toBe(assignment.branch);
    expect(git(wt, 'rev-parse', 'HEAD')).toBe(result.detail);
    // Both the reviewed work and the disjoint target change are present after replay.
    expect(readFileSync(join(wt, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(readFileSync(join(wt, 'target.txt'), 'utf8')).toBe('advanced target\n');
    // No paused rebase left behind; integration target NOT advanced.
    expect(existsSync(resolve(wt, git(wt, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(false);
    expect(git(root, 'rev-parse', 'HEAD')).toBe(targetBefore);
  });

  it('managed no-rebase recovery: after a clean rerebase a fresh isRepair authority integrates the rebased content onto the target (case b, end-to-end)', () => {
    const { root, database, assignment, wt } = seedFrozenReadyNoRebase(false);

    const rerebased = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'rerebase',
    });
    expect(rerebased.state).toBe('rebase-rerebased-ready-for-repair');

    // A fresh reviewed repair commit atop the rebased head integrates: this proves the
    // 2-arg rebase kept the branch ATTACHED, so validateExactCommitState's symbolic-ref
    // and local===parent checks pass. A 3-arg detaching rebase would fail this path.
    writeFileSync(join(wt, 'repair.txt'), 'fresh repair\n');
    git(wt, 'add', 'repair.txt');
    const repairAuthority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));
    expect(finalizeDirectAuthority(database, repairAuthority, 'fresh reviewed repair').state).toBe('cleaned');

    // Rebased content + the fresh repair both landed on the target.
    expect(readFileSync(join(root, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(readFileSync(join(root, 'target.txt'), 'utf8')).toBe('advanced target\n');
    expect(existsSync(join(root, 'repair.txt'))).toBe(true);
    expect(existsSync(wt)).toBe(true);
  });

  it('managed no-rebase recovery: a conflicting rerebase throws with the unmerged path and leaves a paused rebase (case c, status -> paused-conflict)', () => {
    const { root, database, assignment, wt } = seedFrozenReadyNoRebase(true);
    const targetBefore = git(root, 'rev-parse', 'HEAD');

    // Pin the 'Rerebase conflicted' prefix AND the path: a raw git failure leaking
    // through would satisfy a bare README.md match, so both together prove the branch.
    expect(() => reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'rerebase',
    })).toThrow('Rerebase conflicted');
    expect(existsSync(resolve(wt, git(wt, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(true);

    const status = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('rebase-paused-conflict');
    // Target unchanged; rerebase never integrates.
    expect(git(root, 'rev-parse', 'HEAD')).toBe(targetBefore);
  });

  it('managed no-rebase recovery: restore_frozen resets a clean worktree back to the frozen pre-rebase commit (case d)', () => {
    const { root, database, assignment, wt, frozen } = seedFrozenReadyNoRebase(false);

    const rerebased = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'rerebase',
    });
    expect(rerebased.state).toBe('rebase-rerebased-ready-for-repair');
    expect(git(wt, 'rev-parse', 'HEAD')).not.toBe(frozen);

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'restore_frozen',
    });

    expect(result.state).toBe('rebase-frozen-restored');
    expect(git(wt, 'rev-parse', 'HEAD')).toBe(frozen);
  });

  // NO RED — pins already-correct dispatch ordering at integration.ts:1291-1294;
  // falsifier: relocating the :1291 dispatch below the record-present branch.
  it('managed no-rebase recovery: rerebase dispatches BEFORE the integration_records fetch, so a stale-but-live record cannot make rerebase unreachable', () => {
    const { root, database, assignment } = setup(false);
    const wt = assignment.worktree_path;
    const initialCommit = git(root, 'rev-parse', 'HEAD');

    git(wt, 'commit', '-m', 'reviewed work');
    const frozen = git(wt, 'rev-parse', 'HEAD');

    // Seed a stale integration_records row from a prior lifecycle of this SAME
    // workspace guid (integrated_commit = initialCommit, an ancestor of whatever
    // main advances to below) alongside a MATCHING stale candidate ref. Matching
    // candidate === record.integrated_commit means the self-heal staleness proof
    // at :1319-1350 (recordIsProvenStale, which requires candidate !== record) does
    // NOT fire, so the record survives intact into the reachable-proof check at
    // :1436-1438 if that code is ever reached.
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef: 'refs/heads/main',
      integratedCommit: initialCommit,
    });
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, initialCommit);

    // Advance main past the stale record, on a path DISJOINT from the frozen work
    // (mirrors seedFrozenReadyNoRebase's non-conflicting shape).
    writeFileSync(join(root, 'target.txt'), 'advanced target\n');
    git(root, 'add', 'target.txt');
    git(root, 'commit', '-m', 'advance integration target');

    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozen);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);
    git(wt, 'reset', '--hard', frozen);
    expect(existsSync(resolve(wt, git(wt, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(false);

    // If reconcileFinalization ever reached the record-fetch/staleness branch with
    // this row still present and unproven-stale, target_ref would match but main's
    // current oid would differ from the stale integrated_commit, throwing 'Crash
    // reconciliation lacks reachable integration proof'. The rerebase dispatch at
    // :1291-1294 returns before any of that runs, so no such throw occurs here.
    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'rerebase',
    });

    expect(result.state).toBe('rebase-rerebased-ready-for-repair');
  });

  it('reconcile status on an integrated row is non-mutating: returns integrated and preserves the worktree, target, and disposition', () => {
    const s = setup(false);
    const { candidate } = seedIntegratedNoDisposition(s);
    const targetBefore = git(s.root, 'rev-parse', 'HEAD');

    const status = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });

    // Crisp behavioral assertions first: pre-fix the integrated cleanup branch runs,
    // returning 'cleaned' and REMOVING the worktree before the DB read below could run.
    expect(status.state).toBe('integrated');
    expect(existsSync(s.assignment.worktree_path)).toBe(true);
    // A probe never advances the integration target or clears the disposition.
    expect(git(s.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
    expect(s.database.prepare('SELECT lifecycle_status, disposition, integrated_commit FROM assignments WHERE workspace_guid = ?')
      .get(s.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'integrated', disposition: null, integrated_commit: candidate });
  });

  it('plain reconcile (no rebase_recovery) on that same integrated row STILL runs crash-recovery cleanup', () => {
    const s = setup(false);
    const { candidate } = seedIntegratedNoDisposition(s);

    const result = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
    });

    expect(result).toMatchObject({ state: 'cleaned', integratedCommit: candidate });
    // The mutating recycle path stays reachable without the probe: worktree recycled in place.
    expect(existsSync(s.assignment.worktree_path)).toBe(true);
  });

  it('reconcile status on an integrated row whose worktree is gone returns integrated without probing the worktree', () => {
    const s = setup(false);
    seedIntegratedNoDisposition(s);
    // Deregister AND delete the linked worktree so repository discovery survives (a
    // dir removed by rmSync alone still lists in `git worktree list` and crashes
    // discovery before the branch under test). This mirrors a post-cleanup integrated
    // row whose worktree no longer exists.
    git(s.root, 'worktree', 'remove', '--force', s.assignment.worktree_path);
    expect(existsSync(s.assignment.worktree_path)).toBe(false);

    // Pre-fix the integrated cleanup branch reads worktreeHead on the missing dir and
    // throws; post-fix the top status early-return never calls classifyRebaseState or
    // worktreeHead on an integrated row, so a gone worktree is fine.
    const status = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('integrated');
  });

  it('reconcile status on a ready frozen-no-rebase row still reports frozen-no-rebase', () => {
    const { root, database, assignment } = seedFrozenReadyNoRebase(false);
    const status = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('frozen-no-rebase');
  });

  it('reconcile status on a ready paused-conflict row still reports rebase-paused-conflict', () => {
    const conflict = seedConflictMidRebase();
    const status = reconcileFinalization(conflict.database, {
      repositoryPath: conflict.root, workspaceGuid: conflict.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('rebase-paused-conflict');
  });

  it('reconcile status on an integrated push-pending row returns integrated without remote readback or disposition change', () => {
    const s = setup();
    const { disposition } = seedIntegratedPushPending(s);

    // Pre-fix this row takes the integrated disposition branch (ls-remote readback +
    // setDisposition) and returns 'integrated-local'; post-fix the top status
    // early-return precedes all of it, so the push-pending disposition is untouched.
    const status = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('integrated');
    expect(existsSync(s.assignment.worktree_path)).toBe(true);
    expect(s.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
      .get(s.assignment.workspace_guid)).toMatchObject({ disposition });
  });

  it('reconcile status on an active (not-ready) row returns not-ready without throwing', () => {
    const { root, database, assignment } = setup(false);
    const status = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'status',
    });
    expect(status.state).toBe('not-ready');
  });

  it('managed no-rebase recovery: the reconcile validator accepts all five modes and rejects an unknown one (case e, bounded widening)', () => {
    const { database } = setup(false);
    const deps = createInternalCommandDependencies(database);
    for (const mode of ['continue', 'abort', 'rerebase', 'restore_frozen', 'status'] as const) {
      // Reaches past validation and fails on the missing guid/owner pairing, proving
      // the value itself was accepted by optionalRebaseRecovery.
      expect(() => deps.reconcile({ repository_path: '/x', rebase_recovery: mode }))
        .toThrow('requires workspace_guid and owner_session_id');
    }
    expect(() => deps.reconcile({ repository_path: '/x', rebase_recovery: 'bogus' }))
      .toThrow('rebase_recovery must be');
  });

  describe('syncWorktreeToTarget', () => {
    it('is a no-op when the worktree already matches the target', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'reset', '--hard', 'HEAD');
      const head = git(assignment.worktree_path, 'rev-parse', 'HEAD');
      const beforeRow = database.prepare('SELECT base_commit, current_head FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid);

      const result = syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      });

      expect(result.state).toBe('no-op');
      expect(git(assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(head);
      expect(database.prepare('SELECT base_commit, current_head FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toEqual(beforeRow);
    });

    it('fast-forwards a strictly-behind worktree onto the advanced target in-session, without touching main or the remote', () => {
      const { root, database, assignment } = setup(true);
      const remoteBefore = git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main');
      writeFileSync(join(root, 'target.txt'), 'advanced\n');
      git(root, 'add', 'target.txt');
      git(root, 'commit', '-m', 'advance main');
      const mainOid = git(root, 'rev-parse', 'main');

      const result = syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      });

      expect(result.state).toBe('fast-forwarded');
      expect(git(assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(mainOid);
      // work.txt was left staged (dirty) by setup(); the ff-merge is dirty-tolerant
      // because it does not overlap main's advance, so it survives intact.
      expect(readFileSync(join(assignment.worktree_path, 'work.txt'), 'utf8')).toBe('approved\n');
      expect(database.prepare('SELECT base_commit, current_head FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ base_commit: mainOid, current_head: mainOid });
      expect(git(root, 'rev-parse', 'main')).toBe(mainOid);
      expect(git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main')).toBe(remoteBefore);
    });

    it('rebases a worktree with its own commits onto the advanced target, preserving worker content', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'reset', '--hard', 'HEAD');
      writeFileSync(join(assignment.worktree_path, 'own.txt'), 'own change\n');
      git(assignment.worktree_path, 'add', 'own.txt');
      git(assignment.worktree_path, 'commit', '-m', 'own work');
      writeFileSync(join(root, 'target.txt'), 'advanced\n');
      git(root, 'add', 'target.txt');
      git(root, 'commit', '-m', 'advance main');
      const mainOid = git(root, 'rev-parse', 'main');

      const result = syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      });

      expect(result.state).toBe('rebased');
      expect(git(assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('own work');
      expect(git(assignment.worktree_path, 'rev-parse', 'HEAD~1')).toBe(mainOid);
      expect(readFileSync(join(assignment.worktree_path, 'own.txt'), 'utf8')).toBe('own change\n');
      const newHead = git(assignment.worktree_path, 'rev-parse', 'HEAD');
      expect(database.prepare('SELECT base_commit, current_head FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ base_commit: mainOid, current_head: newHead });
      expect(git(root, 'rev-parse', 'main')).toBe(mainOid);
    });

    it('aborts a conflicting rebase, restores the original worktree HEAD, and reports the conflicting paths', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'reset', '--hard', 'HEAD');
      writeFileSync(join(assignment.worktree_path, 'README.md'), 'worker change\n');
      git(assignment.worktree_path, 'add', 'README.md');
      git(assignment.worktree_path, 'commit', '-m', 'worker edits README');
      const originalHead = git(assignment.worktree_path, 'rev-parse', 'HEAD');
      writeFileSync(join(root, 'README.md'), 'main change\n');
      git(root, 'add', 'README.md');
      git(root, 'commit', '-m', 'main edits README');
      const mainOid = git(root, 'rev-parse', 'main');
      const beforeBaseCommit = database.prepare('SELECT base_commit FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid);

      expect(() => syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      })).toThrow(/conflict.*README\.md/s);

      expect(git(assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(originalHead);
      expect(database.prepare('SELECT base_commit FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toEqual(beforeBaseCommit);
      expect(git(root, 'rev-parse', 'main')).toBe(mainOid);
    });

    it('refuses on a non-active lifecycle', () => {
      const { root, database, assignment } = setup(false);
      database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
        .run(assignment.workspace_guid);

      expect(() => syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      })).toThrow('active');
    });

    it('refuses when a rebase is already paused in the worktree', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'reset', '--hard', 'HEAD');
      writeFileSync(join(assignment.worktree_path, 'README.md'), 'worker change\n');
      git(assignment.worktree_path, 'add', 'README.md');
      git(assignment.worktree_path, 'commit', '-m', 'worker edits README');
      writeFileSync(join(root, 'README.md'), 'main change\n');
      git(root, 'add', 'README.md');
      git(root, 'commit', '-m', 'main edits README');
      try {
        git(assignment.worktree_path, 'rebase', 'main');
      } catch { /* expected: leaves a paused rebase in place */ }
      expect(existsSync(resolve(
        assignment.worktree_path, git(assignment.worktree_path, 'rev-parse', '--git-path', 'rebase-merge'),
      ))).toBe(true);

      expect(() => syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      })).toThrow('rebase');
    });

    it('refuses when an integration lock is held for this repository and workspace', () => {
      const { root, database, assignment } = setup(false);
      acquireIntegrationLock(database, {
        repositoryIdentity: assignment.repository_identity,
        workspaceGuid: assignment.workspace_guid,
        targetRef: 'refs/heads/main',
        expectedTarget: git(root, 'rev-parse', 'main'),
      });

      expect(() => syncWorktreeToTarget(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      })).toThrow('lock');
    });

    it('finalize detects a stale base_commit that is not the merge-base of frozen and target, and points at sync', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'reset', '--hard', 'HEAD');
      writeFileSync(join(root, 'target.txt'), 'advanced\n');
      git(root, 'add', 'target.txt');
      git(root, 'commit', '-m', 'advance main');
      const mainOid = git(root, 'rev-parse', 'main');
      // The manual mechanism sync_worktree_to_target replaces: ff-merge onto the
      // advanced target WITHOUT going through the durable base_commit update.
      git(assignment.worktree_path, 'merge', '--ff-only', mainOid);
      writeFileSync(join(assignment.worktree_path, 'work.txt'), 'approved\n');
      git(assignment.worktree_path, 'add', 'work.txt');
      const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));

      expect(() => finalizeDirectAuthority(database, authority, 'stale base commit'))
        .toThrow('base_commit is not the merge-base; run sync_worktree_to_target');
    });
  });

  // Seeds a ready-for-integration repair row: reviewed work committed + frozen, the
  // worktree ATTACHED at frozen (a descendant of the current target) with fresh repair
  // work staged, ready for a fresh isRepair commit authority to integrate.
  function seedRepairReady(state: ReturnType<typeof setup>) {
    const wt = state.assignment.worktree_path;
    git(wt, 'commit', '-m', 'reviewed work');
    const frozen = git(wt, 'rev-parse', 'HEAD');
    git(state.root, 'update-ref', `refs/ironclaude/finalization/${state.assignment.workspace_guid}/frozen`, frozen);
    state.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(state.assignment.workspace_guid);
    writeFileSync(join(wt, 'repair.txt'), 'fresh repair\n');
    git(wt, 'add', 'repair.txt');
    return { frozen };
  }

  it('case 1: advances the target via pure ref CAS when the primary is on a feature branch, leaving the operator checkout untouched', () => {
    const state = setup(false);
    const { root, database, assignment } = state;
    const owner = bindOtherLivePrimaryOwner(state);
    const mainTip = git(root, 'rev-parse', 'HEAD');
    // Operator moves the primary onto a feature branch and dirties an unrelated file.
    git(root, 'checkout', '-b', 'operator-feature');
    writeFileSync(join(root, 'operator.txt'), 'operator work in progress\n');
    const beforeHash = git(root, 'hash-object', join(root, 'operator.txt'));

    const result = finalizeCommanderLocalCommit(
      database, commanderInput(root, assignment, 'case1 feature-branch finalize'),
    );

    expect(result.state).toBe('cleaned');
    const integrated = result.integratedCommit!;
    // main advanced to the integrated commit via the ref CAS alone.
    expect(git(root, 'rev-parse', 'refs/heads/main')).toBe(integrated);
    expect(integrated).not.toBe(mainTip);
    // Operator checkout is byte-for-byte untouched: still on the feature branch, still dirty.
    expect(git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')).toBe('operator-feature');
    expect(git(root, 'hash-object', join(root, 'operator.txt'))).toBe(beforeHash);
    expect(readFileSync(join(root, 'operator.txt'), 'utf8')).toBe('operator work in progress\n');
    // The worker file was never written into the operator's feature-branch tree.
    expect(existsSync(join(root, 'work.txt'))).toBe(false);
    expect(database.prepare(
      'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(assignment.repository_identity)).toMatchObject({ workspace_guid: owner.workspace_guid });
  });

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
        'Finalization primary checkout has local changes overlapping the carried-forward integration; '
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

  it('repair channel (finalizeAttestedCandidate): ready-repair finalize honors the three primary cases without overwriting operator work', () => {
    // (i) dirty-on-target, no overlap -> succeeds, operator bytes hash-identical.
    const dirtyTarget = setup(false);
    const dirtyOwner = bindOtherLivePrimaryOwner(dirtyTarget);
    seedRepairReady(dirtyTarget);
    writeFileSync(join(dirtyTarget.root, 'operator-notes.txt'), 'unrelated operator edit\n');
    const dirtyBefore = git(dirtyTarget.root, 'hash-object', join(dirtyTarget.root, 'operator-notes.txt'));
    const dirtyResult = finalizeCommanderLocalCommit(
      dirtyTarget.database, commanderInput(dirtyTarget.root, dirtyTarget.assignment, 'repair no-overlap'),
    );
    expect(dirtyResult.state).toBe('cleaned');
    expect(git(dirtyTarget.root, 'rev-parse', 'HEAD')).toBe(dirtyResult.integratedCommit);
    expect(readFileSync(join(dirtyTarget.root, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(readFileSync(join(dirtyTarget.root, 'repair.txt'), 'utf8')).toBe('fresh repair\n');
    expect(git(dirtyTarget.root, 'hash-object', join(dirtyTarget.root, 'operator-notes.txt'))).toBe(dirtyBefore);
    expect(dirtyTarget.database.prepare(
      'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(dirtyTarget.assignment.repository_identity)).toMatchObject({ workspace_guid: dirtyOwner.workspace_guid });

    // (ii) primary on a FEATURE BRANCH -> succeeds via pure ref advance, feature tree untouched.
    const feature = setup(false);
    const featureOwner = bindOtherLivePrimaryOwner(feature);
    seedRepairReady(feature);
    const featureMainTip = git(feature.root, 'rev-parse', 'HEAD');
    git(feature.root, 'checkout', '-b', 'operator-feature');
    writeFileSync(join(feature.root, 'operator.txt'), 'operator work in progress\n');
    const featureBefore = git(feature.root, 'hash-object', join(feature.root, 'operator.txt'));
    const featureResult = finalizeCommanderLocalCommit(
      feature.database, commanderInput(feature.root, feature.assignment, 'repair feature branch'),
    );
    expect(featureResult.state).toBe('cleaned');
    expect(git(feature.root, 'rev-parse', 'refs/heads/main')).toBe(featureResult.integratedCommit);
    expect(featureResult.integratedCommit).not.toBe(featureMainTip);
    expect(git(feature.root, 'symbolic-ref', '--quiet', '--short', 'HEAD')).toBe('operator-feature');
    expect(git(feature.root, 'hash-object', join(feature.root, 'operator.txt'))).toBe(featureBefore);
    expect(existsSync(join(feature.root, 'work.txt'))).toBe(false);
    expect(feature.database.prepare(
      'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(feature.assignment.repository_identity)).toMatchObject({ workspace_guid: featureOwner.workspace_guid });

    // (iii) overlap -> preserving refusal before the CAS, target not advanced.
    const overlap = setup(false);
    const overlapOwner = bindOtherLivePrimaryOwner(overlap);
    seedRepairReady(overlap);
    const overlapMainTip = git(overlap.root, 'rev-parse', 'HEAD');
    writeFileSync(join(overlap.root, 'work.txt'), 'operator conflicting edit\n');
    const overlapBefore = git(overlap.root, 'hash-object', join(overlap.root, 'work.txt'));
    expect(() => finalizeCommanderLocalCommit(
      overlap.database, commanderInput(overlap.root, overlap.assignment, 'repair overlap'),
    )).toThrow(/overlap/i);
    expect(git(overlap.root, 'rev-parse', 'refs/heads/main')).toBe(overlapMainTip);
    expect(git(overlap.root, 'hash-object', join(overlap.root, 'work.txt'))).toBe(overlapBefore);
    expect(existsSync(overlap.assignment.worktree_path)).toBe(true);
    expect(overlap.database.prepare(
      'SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(overlap.assignment.repository_identity)).toMatchObject({ workspace_guid: overlapOwner.workspace_guid });
  });
  });
  }
});
}
