import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import {
  acquirePrimaryCheckoutOwnership,
  createHumanIntent,
  initDb,
  recordIntegration,
  releasePrimaryCheckoutOwnership,
} from '../db.js';
import {
  issueDirectGitHumanIntent,
  pushExactAuthorizedIntegratedCandidate,
  pushExactAuthorizedRef,
  revalidateAuthorizedCommitState,
  verifyDirectGitAuthority,
  type CommitAndPushEvidence,
  type CommitEvidence,
  type DirectGitAuthorityEvidence,
  type DirectGitOperation,
  type PushEvidence,
} from '../git-authority.js';
import { WorkspaceService } from '../workspace-service.js';
import type { Assignment } from '../types.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER_OWNER = '019f7cdf-023c-74e0-9ead-9c155636885d';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

describe('direct Git authority', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function repository(withRemote = true): string {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-'));
    directories.push(root);
    git(root, 'init', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Git Authority Test');
    git(root, 'config', 'user.email', 'git-authority@example.invalid');
    writeFileSync(join(root, 'README.md'), 'initial\n');
    git(root, 'add', 'README.md');
    git(root, 'commit', '-m', 'initial');
    if (withRemote) {
      const remote = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-remote-'));
      directories.push(remote);
      git(remote, 'init', '--bare');
      git(root, 'remote', 'add', 'origin', remote);
      git(root, 'push', 'origin', 'main:refs/heads/main');
    }
    return root;
  }

  function setup(withRemote = true): { root: string; database: ReturnType<typeof initDb>; assignment: Assignment } {
    const root = repository(withRemote);
    const databaseDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-db-'));
    directories.push(databaseDirectory);
    const database = initDb(join(databaseDirectory, 'authority.db'));
    const assignment = new WorkspaceService(database).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'authority.txt'), 'staged\n');
    git(assignment.worktree_path, 'add', 'authority.txt');
    return { root, database, assignment };
  }

  function commitEvidence(
    assignment: Assignment,
    source = assignment.worktree_path,
    checkoutMode: 'managed' | 'primary' = 'managed',
  ): CommitEvidence {
    const canonicalBranch = git(source, 'symbolic-ref', '--short', 'HEAD');
    return {
      checkoutMode,
      canonicalBranch,
      stagedTree: git(source, 'write-tree'),
      parentRef: 'HEAD',
      parentOid: git(source, 'rev-parse', 'HEAD'),
      localRef: `refs/heads/${canonicalBranch}`,
    };
  }

  function remoteEvidence(
    assignment: Assignment,
    source = assignment.worktree_path,
    destinationRef = `refs/heads/${assignment.branch}`,
) {
    const remoteLine = git(source, 'ls-remote', '--refs', 'origin', destinationRef);
    return {
      remoteName: 'origin',
      remoteUrl: git(source, 'remote', 'get-url', 'origin'),
      destinationRef,
      expectedRemoteOldOid: remoteLine === '' ? null : remoteLine.split(/\s+/)[0],
    };
  }

  function commitAndPushEvidence(assignment: Assignment): CommitAndPushEvidence {
    return { ...commitEvidence(assignment), ...remoteEvidence(assignment) };
  }

  function integrateAuthorizedCandidate(
    root: string,
    database: ReturnType<typeof initDb>,
    assignment: Assignment,
  ): { frozen: string; candidate: string; targetRef: string } {
    git(assignment.worktree_path, 'commit', '-m', 'frozen authorized work');
    const frozen = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozen);

    writeFileSync(join(root, 'target.txt'), 'target advanced\n');
    git(root, 'add', 'target.txt');
    git(root, 'commit', '-m', 'advance integration target');
    git(assignment.worktree_path, 'rebase', 'main');
    const candidate = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    git(root, 'merge', '--ff-only', candidate);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, candidate);

    const targetRef = 'refs/heads/main';
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef,
      integratedCommit: candidate,
    });
    database.prepare(`
      UPDATE assignments
      SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?
      WHERE workspace_guid = ?
    `).run(candidate, candidate, assignment.workspace_guid);
    return { frozen, candidate, targetRef };
  }

  function pushEvidence(assignment: Assignment): PushEvidence {
    const source = assignment.worktree_path;
    return {
      checkoutMode: 'managed',
      canonicalBranch: git(source, 'symbolic-ref', '--short', 'HEAD'),
      localRef: `refs/heads/${assignment.branch}`,
      localOid: git(source, 'rev-parse', `refs/heads/${assignment.branch}`),
      ...remoteEvidence(assignment),
    };
  }

  function issue(
    database: ReturnType<typeof initDb>,
    assignment: Assignment,
    operation: DirectGitOperation,
    expectedEvidence: DirectGitAuthorityEvidence,
    nonce = randomUUID(),
  ): string {
    createHumanIntent(database, {
      operation,
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence,
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce,
    });
    return nonce;
  }

  function verify(
    root: string,
    database: ReturnType<typeof initDb>,
    assignment: Assignment,
    operation: DirectGitOperation,
    expectedEvidence: DirectGitAuthorityEvidence,
    nonce: string,
    providerRootSessionId = OWNER,
  ) {
    return verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId,
      humanChannel: 'codex-user-prompt',
      operation,
      expectedEvidence,
      nonce,
    });
  }

  function acquirePrimary(database: ReturnType<typeof initDb>, assignment: Assignment, ownerSessionId = OWNER): void {
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId,
    });
  }

  it('authorizes a local commit with no remote and consumes its exact intent once', () => {
    const { root, database, assignment } = setup(false);
    const expected = commitEvidence(assignment);
    const nonce = issue(database, assignment, 'commit', expected);

    const authority = verify(root, database, assignment, 'commit', expected, nonce);

    expect(authority.operation).toBe('commit');
    expect(() => verify(root, database, assignment, 'commit', expected, nonce)).toThrow('matching human intent');
    expect(() => pushExactAuthorizedRef(authority)).toThrow('does not authorize a push');
  });

  it('issues and consumes direct authority with server-observed evidence and no public nonce', () => {
    const { root, database, assignment } = setup(false);
    const receipt = issueDirectGitHumanIntent(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
    expect(receipt).toMatchObject({ issued: true, operation: 'commit' });
    expect(receipt).not.toHaveProperty('nonce');
    expect(receipt).not.toHaveProperty('expectedEvidence');

    const authority = verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
    expect(authority).toMatchObject({ operation: 'commit', workspaceGuid: assignment.workspace_guid });
    expect(() => verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    })).toThrow('matching human intent');
  });

  it('leaves server-held direct intent pending when observed evidence changes', () => {
    const { root, database, assignment } = setup(false);
    issueDirectGitHumanIntent(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
    writeFileSync(join(assignment.worktree_path, 'later.txt'), 'later staged\n');
    git(assignment.worktree_path, 'add', 'later.txt');
    expect(() => verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    })).toThrow('matching human intent');
    expect(database.prepare('SELECT consumed_at FROM human_intents ORDER BY intent_id DESC LIMIT 1').get())
      .toEqual({ consumed_at: null });
  });

  it('uses an exactly owned primary checkout as direct human commit target', () => {
    const { root, database, assignment } = setup(false);
    acquirePrimary(database, assignment);
    writeFileSync(join(root, 'primary.txt'), 'primary staged\n');
    git(root, 'add', 'primary.txt');
    const expected = commitEvidence(assignment, root, 'primary');
    const nonce = issue(database, assignment, 'commit', expected);

    const authority = verify(root, database, assignment, 'commit', expected, nonce);

    expect(authority).toMatchObject({ checkoutMode: 'primary', worktreePath: realpathSync(root) });
  });

  it('does not consume a managed intent if checkout ownership changes before verification', () => {
    const { root, database, assignment } = setup(false);
    const expected = commitEvidence(assignment);
    const nonce = issue(database, assignment, 'commit', expected);
    acquirePrimary(database, assignment);

    expect(() => verify(root, database, assignment, 'commit', expected, nonce)).toThrow('evidence');
    releasePrimaryCheckoutOwnership(database, assignment.repository_identity, assignment.workspace_guid, OWNER);
    expect(verify(root, database, assignment, 'commit', expected, nonce).checkoutMode).toBe('managed');
  });

  it('denies another assignment primary ownership and revalidates ownership after authorization', () => {
    const foreign = setup(false);
    const manager = new WorkspaceService(foreign.database);
    const other = manager.ensureSessionWorktree({ repositoryPath: foreign.root, ownerSessionId: OTHER_OWNER });
    const foreignExpected = commitEvidence(foreign.assignment);
    const foreignNonce = issue(foreign.database, foreign.assignment, 'commit', foreignExpected);
    acquirePrimary(foreign.database, other, OTHER_OWNER);
    expect(() => verify(foreign.root, foreign.database, foreign.assignment, 'commit', foreignExpected, foreignNonce))
      .toThrow('owned by another');

    const changed = setup(true);
    const commitExpected = commitEvidence(changed.assignment);
    const commitNonce = issue(changed.database, changed.assignment, 'commit', commitExpected);
    const commitAuthority = verify(changed.root, changed.database, changed.assignment, 'commit', commitExpected, commitNonce);
    acquirePrimary(changed.database, changed.assignment);
    expect(() => revalidateAuthorizedCommitState(commitAuthority)).toThrow('effective checkout changed');

    const push = setup(true);
    const pushExpected = commitAndPushEvidence(push.assignment);
    const pushNonce = issue(push.database, push.assignment, 'commit-and-push', pushExpected);
    const pushAuthority = verify(push.root, push.database, push.assignment, 'commit-and-push', pushExpected, pushNonce);
    acquirePrimary(push.database, push.assignment);
    expect(() => pushExactAuthorizedRef(pushAuthority)).toThrow('effective checkout changed');
    expect(() => pushExactAuthorizedRef(pushAuthority)).toThrow('single-use');
  });

  it('binds authority to provider root session and distinct operation intent', () => {
    const { root, database, assignment } = setup();
    const expected = commitEvidence(assignment);
    const nonce = issue(database, assignment, 'commit', expected);

    expect(() => verify(root, database, assignment, 'commit', expected, nonce, OTHER_OWNER)).toThrow('provider root');
    expect(() => verify(root, database, assignment, 'push', expected, nonce)).toThrow('evidence');
    expect(verify(root, database, assignment, 'commit', expected, nonce).providerRootSessionId).toBe(OWNER);
  });

  it('freezes the consumed authority and evidence against post-verification mutation', () => {
    const { root, database, assignment } = setup();
    const expected = commitAndPushEvidence(assignment);
    const nonce = issue(database, assignment, 'commit-and-push', expected);
    const authority = verify(root, database, assignment, 'commit-and-push', expected, nonce);

    expect(() => {
      (authority.evidence as CommitAndPushEvidence).destinationRef = 'refs/heads/unauthorized';
    }).toThrow();
    expect(() => {
      (authority as { workspaceGuid: string }).workspaceGuid = randomUUID();
    }).toThrow();
    expect(authority.evidence).toMatchObject(expected);
  });

  it('pushes the exact integrated candidate after a nontrivial target rebase', () => {
    const { root, database, assignment } = setup();
    const expected = {
      ...commitEvidence(assignment),
      ...remoteEvidence(assignment, assignment.worktree_path, 'refs/heads/main'),
    } satisfies CommitAndPushEvidence;
    const nonce = issue(database, assignment, 'commit-and-push', expected);

    const authority = verify(root, database, assignment, 'commit-and-push', expected, nonce);
    const integrated = integrateAuthorizedCandidate(root, database, assignment);

    expect(integrated.candidate).not.toBe(integrated.frozen);
    expect(() => pushExactAuthorizedRef(authority)).toThrow('integrated candidate');
    pushExactAuthorizedIntegratedCandidate(authority, integrated.candidate, integrated.targetRef);

    expect(git(root, 'ls-remote', '--refs', 'origin', expected.destinationRef).split(/\s+/)[0])
      .toBe(integrated.candidate);
    expect(() => pushExactAuthorizedIntegratedCandidate(authority, integrated.candidate, integrated.targetRef))
      .toThrow('single-use');

    const changedRemote = setup();
    const changedEvidence = {
      ...commitEvidence(changedRemote.assignment),
      ...remoteEvidence(changedRemote.assignment, changedRemote.assignment.worktree_path, 'refs/heads/main'),
    } satisfies CommitAndPushEvidence;
    const changedNonce = issue(changedRemote.database, changedRemote.assignment, 'commit-and-push', changedEvidence);
    const changedAuthority = verify(
      changedRemote.root, changedRemote.database, changedRemote.assignment,
      'commit-and-push', changedEvidence, changedNonce,
    );
    const changedIntegrated = integrateAuthorizedCandidate(
      changedRemote.root, changedRemote.database, changedRemote.assignment,
    );
    git(changedRemote.assignment.worktree_path, 'remote', 'set-url', 'origin', 'file:///changed-after-authorization');
    expect(() => pushExactAuthorizedIntegratedCandidate(
      changedAuthority, changedIntegrated.candidate, changedIntegrated.targetRef,
    )).toThrow('evidence');

    const changedPushUrl = setup();
    const pushUrlEvidence = {
      ...commitEvidence(changedPushUrl.assignment),
      ...remoteEvidence(changedPushUrl.assignment, changedPushUrl.assignment.worktree_path, 'refs/heads/main'),
    } satisfies CommitAndPushEvidence;
    const pushUrlNonce = issue(changedPushUrl.database, changedPushUrl.assignment, 'commit-and-push', pushUrlEvidence);
    const pushUrlAuthority = verify(
      changedPushUrl.root, changedPushUrl.database, changedPushUrl.assignment,
      'commit-and-push', pushUrlEvidence, pushUrlNonce,
    );
    const pushUrlIntegrated = integrateAuthorizedCandidate(
      changedPushUrl.root, changedPushUrl.database, changedPushUrl.assignment,
    );
    const unauthorizedRemote = mkdtempSync(join(tmpdir(), 'ironclaude-unauthorized-pushurl-'));
    directories.push(unauthorizedRemote);
    git(unauthorizedRemote, 'init', '--bare');
    git(changedPushUrl.assignment.worktree_path, 'remote', 'set-url', '--push', 'origin', unauthorizedRemote);
    expect(() => pushExactAuthorizedIntegratedCandidate(
      pushUrlAuthority, pushUrlIntegrated.candidate, pushUrlIntegrated.targetRef,
    )).toThrow('evidence');
    expect(git(unauthorizedRemote, 'for-each-ref', '--format=%(refname)')).toBe('');
  });

  it('rejects a frozen or mismatched candidate instead of weakening integrated provenance', () => {
    const frozenAttempt = setup();
    const frozenEvidence = {
      ...commitEvidence(frozenAttempt.assignment),
      ...remoteEvidence(frozenAttempt.assignment, frozenAttempt.assignment.worktree_path, 'refs/heads/main'),
    } satisfies CommitAndPushEvidence;
    const frozenNonce = issue(frozenAttempt.database, frozenAttempt.assignment, 'commit-and-push', frozenEvidence);
    const frozenAuthority = verify(
      frozenAttempt.root, frozenAttempt.database, frozenAttempt.assignment,
      'commit-and-push', frozenEvidence, frozenNonce,
    );
    const frozenIntegrated = integrateAuthorizedCandidate(
      frozenAttempt.root, frozenAttempt.database, frozenAttempt.assignment,
    );

    expect(() => pushExactAuthorizedIntegratedCandidate(
      frozenAuthority, frozenIntegrated.frozen, frozenIntegrated.targetRef,
    )).toThrow('evidence');
    expect(git(frozenAttempt.root, 'ls-remote', '--refs', 'origin', frozenEvidence.destinationRef).split(/\s+/)[0])
      .toBe(frozenEvidence.expectedRemoteOldOid);

    const wrongTarget = setup();
    const wrongEvidence = {
      ...commitEvidence(wrongTarget.assignment),
      ...remoteEvidence(wrongTarget.assignment, wrongTarget.assignment.worktree_path, 'refs/heads/main'),
    } satisfies CommitAndPushEvidence;
    const wrongNonce = issue(wrongTarget.database, wrongTarget.assignment, 'commit-and-push', wrongEvidence);
    const wrongAuthority = verify(
      wrongTarget.root, wrongTarget.database, wrongTarget.assignment,
      'commit-and-push', wrongEvidence, wrongNonce,
    );
    const wrongIntegrated = integrateAuthorizedCandidate(wrongTarget.root, wrongTarget.database, wrongTarget.assignment);
    expect(() => pushExactAuthorizedIntegratedCandidate(
      wrongAuthority, wrongIntegrated.candidate, 'refs/heads/not-main',
    )).toThrow('evidence');
  });

  it('pushes only the separately authorized exact existing local commit', () => {
    const { root, database, assignment } = setup();
    git(assignment.worktree_path, 'commit', '-m', 'existing local work');
    const expected = pushEvidence(assignment);
    const nonce = issue(database, assignment, 'push', expected);

    const authority = verify(root, database, assignment, 'push', expected, nonce);
    pushExactAuthorizedRef(authority);

    expect(git(root, 'ls-remote', '--refs', 'origin', expected.destinationRef).split(/\s+/)[0]).toBe(expected.localOid);
  });

  it('denies changed staged tree, parent, branch, remote, ref or OID evidence', () => {
    const tree = setup();
    const treeEvidence = commitEvidence(tree.assignment);
    const treeNonce = issue(tree.database, tree.assignment, 'commit', treeEvidence);
    writeFileSync(join(tree.assignment.worktree_path, 'authority.txt'), 'changed\n');
    git(tree.assignment.worktree_path, 'add', 'authority.txt');
    expect(() => verify(tree.root, tree.database, tree.assignment, 'commit', treeEvidence, treeNonce)).toThrow('evidence');

    const parent = setup();
    const parentEvidence = commitEvidence(parent.assignment);
    const parentNonce = issue(parent.database, parent.assignment, 'commit', parentEvidence);
    git(parent.assignment.worktree_path, 'commit', '-m', 'changed parent');
    expect(() => verify(parent.root, parent.database, parent.assignment, 'commit', parentEvidence, parentNonce)).toThrow('evidence');

    const branch = setup();
    const branchEvidence = commitEvidence(branch.assignment);
    const branchNonce = issue(branch.database, branch.assignment, 'commit', branchEvidence);
    git(branch.assignment.worktree_path, 'checkout', '-b', 'not-authorized');
    expect(() => verify(branch.root, branch.database, branch.assignment, 'commit', branchEvidence, branchNonce)).toThrow('identity');

    const remote = setup();
    const remoteEvidence = commitAndPushEvidence(remote.assignment);
    const remoteNonce = issue(remote.database, remote.assignment, 'commit-and-push', remoteEvidence);
    git(remote.assignment.worktree_path, 'remote', 'set-url', 'origin', 'file:///not-authorized');
    expect(() => verify(remote.root, remote.database, remote.assignment, 'commit-and-push', remoteEvidence, remoteNonce)).toThrow('evidence');

    const ref = setup();
    git(ref.assignment.worktree_path, 'commit', '-m', 'existing local work');
    const refEvidence = pushEvidence(ref.assignment);
    const refNonce = issue(ref.database, ref.assignment, 'push', refEvidence);
    git(ref.assignment.worktree_path, 'reset', '--hard', 'HEAD^');
    expect(() => verify(ref.root, ref.database, ref.assignment, 'push', refEvidence, refNonce)).toThrow('evidence');
  });

  it('denies changed remote-old-OID before or after commit-and-push authorization', () => {
    const before = setup();
    const beforeEvidence = commitAndPushEvidence(before.assignment);
    const beforeNonce = issue(before.database, before.assignment, 'commit-and-push', beforeEvidence);
    git(before.root, 'push', 'origin', `${beforeEvidence.parentOid}:${beforeEvidence.destinationRef}`);
    expect(() => verify(before.root, before.database, before.assignment, 'commit-and-push', beforeEvidence, beforeNonce)).toThrow('evidence');

    const after = setup();
    const afterEvidence = commitAndPushEvidence(after.assignment);
    const afterNonce = issue(after.database, after.assignment, 'commit-and-push', afterEvidence);
    const authority = verify(after.root, after.database, after.assignment, 'commit-and-push', afterEvidence, afterNonce);
    git(after.assignment.worktree_path, 'commit', '-m', 'approved local work');
    git(after.root, 'push', 'origin', `${afterEvidence.parentOid}:${afterEvidence.destinationRef}`);
    expect(() => pushExactAuthorizedRef(authority)).toThrow('evidence');
    expect(() => pushExactAuthorizedRef(authority)).toThrow('single-use');
  });

  it('denies a commit-and-push post-authorization commit with changed tree or merge parent', () => {
    const changedTree = setup();
    const treeEvidence = commitAndPushEvidence(changedTree.assignment);
    const treeNonce = issue(changedTree.database, changedTree.assignment, 'commit-and-push', treeEvidence);
    const treeAuthority = verify(changedTree.root, changedTree.database, changedTree.assignment, 'commit-and-push', treeEvidence, treeNonce);
    writeFileSync(join(changedTree.assignment.worktree_path, 'authority.txt'), 'changed\n');
    git(changedTree.assignment.worktree_path, 'add', 'authority.txt');
    git(changedTree.assignment.worktree_path, 'commit', '-m', 'wrong tree');
    expect(() => pushExactAuthorizedRef(treeAuthority)).toThrow('evidence');

    const merge = setup();
    const mergeEvidence = commitAndPushEvidence(merge.assignment);
    const mergeNonce = issue(merge.database, merge.assignment, 'commit-and-push', mergeEvidence);
    const mergeAuthority = verify(merge.root, merge.database, merge.assignment, 'commit-and-push', mergeEvidence, mergeNonce);
    git(merge.assignment.worktree_path, 'commit', '-m', 'first local work');
    git(merge.assignment.worktree_path, 'checkout', '-b', 'side');
    writeFileSync(join(merge.assignment.worktree_path, 'side.txt'), 'side\n');
    git(merge.assignment.worktree_path, 'add', 'side.txt');
    git(merge.assignment.worktree_path, 'commit', '-m', 'side work');
    git(merge.assignment.worktree_path, 'checkout', merge.assignment.branch);
    git(merge.assignment.worktree_path, 'merge', '--no-ff', 'side', '-m', 'merge work');
    expect(() => pushExactAuthorizedRef(mergeAuthority)).toThrow('evidence');
  });
});
