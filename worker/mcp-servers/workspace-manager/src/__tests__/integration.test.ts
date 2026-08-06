import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import {
  acquireIntegrationLock,
  acquirePrimaryCheckoutOwnership,
  createHumanIntent,
  initDb,
  recordIntegration,
  releaseIntegrationLock,
} from '../db.js';
import { verifyDirectGitAuthority, type CommitAndPushEvidence, type CommitEvidence, type PushEvidence } from '../git-authority.js';
import { finalizeCommanderLocalCommit, finalizeDirectAuthority, reconcileFinalization } from '../integration.js';
import { WorkspaceService } from '../workspace-service.js';
import type { Assignment } from '../types.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER = '019f7cdf-023c-74e0-9ead-9c155636885d';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

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

  function issueAndVerify(
    database: ReturnType<typeof initDb>, assignment: Assignment, operation: 'commit' | 'commit-and-push' | 'push',
    evidence: CommitEvidence | CommitAndPushEvidence | PushEvidence,
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

  it('finalizes a direct local commit through freeze, checked fast-forward, record, and cleanup', () => {
    const { root, database, assignment } = setup(false);
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));

    const result = finalizeDirectAuthority(database, authority, 'approved direct commit');

    expect(result.state).toBe('cleaned');
    expect(git(root, 'log', '-1', '--format=%s')).toBe('approved direct commit');
    expect(readFileSync(join(root, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(database.prepare('SELECT integrated_commit FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
      .toMatchObject({ integrated_commit: git(root, 'rev-parse', 'HEAD') });
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

    expect(reconcileFinalization(crashed.database, {
      repositoryPath: crashed.root, workspaceGuid: crashed.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'cleaned', integratedCommit: candidate });
    expect(git(crashed.root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
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
  }, 15_000);

  it('rejects dirty or wrong-branch primary checkout before mutating a local source commit', () => {
    const wrongBranch = setup(false);
    git(wrongBranch.root, 'checkout', '-b', 'operator-branch');
    const wrongAuthority = issueAndVerify(wrongBranch.database, wrongBranch.assignment, 'commit', commitEvidence(wrongBranch.assignment));
    expect(() => finalizeDirectAuthority(wrongBranch.database, wrongAuthority, 'wrong branch')).toThrow('checked out');
    expect(git(wrongBranch.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');
    expect(wrongBranch.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(wrongBranch.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'active' });

    const dirty = setup(false);
    writeFileSync(join(dirty.root, 'operator.txt'), 'dirty\n');
    const dirtyAuthority = issueAndVerify(dirty.database, dirty.assignment, 'commit', commitEvidence(dirty.assignment));
    expect(() => finalizeDirectAuthority(dirty.database, dirtyAuthority, 'dirty primary')).toThrow('dirty');
    expect(git(dirty.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');
    expect(dirty.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(dirty.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'active' });

    const inactive = setup(false);
    const inactiveAuthority = issueAndVerify(inactive.database, inactive.assignment, 'commit', commitEvidence(inactive.assignment));
    inactive.database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(inactive.assignment.workspace_guid);
    expect(() => finalizeDirectAuthority(inactive.database, inactiveAuthority, 'invalid lifecycle')).toThrow('lacks durable frozen');
    expect(git(inactive.assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('initial');
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

  it('fences push-only authority at Task4 before Task3 can mutate a primary-owned checkout', () => {
    const { root, database, assignment } = setup(true);
    git(assignment.worktree_path, 'commit', '-m', 'push-only work');
    const evidence: PushEvidence = {
      checkoutMode: 'managed', canonicalBranch: assignment.branch, localRef: `refs/heads/${assignment.branch}`,
      localOid: git(assignment.worktree_path, 'rev-parse', 'HEAD'), ...remoteEvidence(assignment),
    };
    const authority = issueAndVerify(database, assignment, 'push', evidence);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: assignment.repository_identity, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    });
    expect(() => finalizeDirectAuthority(database, authority, 'ignored')).toThrow('fenced');
    expect(git(root, 'ls-remote', '--refs', 'origin', evidence.destinationRef)).toBe('');
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
    expect(existsSync(pushed.assignment.worktree_path)).toBe(false);
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
