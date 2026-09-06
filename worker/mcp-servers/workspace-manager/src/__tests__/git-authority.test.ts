import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import Database from 'better-sqlite3';
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
  resolveUnassignedPrimaryCheckout,
  revalidateAuthorizedCommitState,
  verifyDirectGitAuthority,
  type CommitAndPushEvidence,
  type CommitEvidence,
  type DirectGitAuthorityEvidence,
  type DirectGitOperation,
  type PushEvidence,
  type ReconcileEvidence,
} from '../git-authority.js';
import { issueHumanIntentFromHook } from '../hook-intent.js';
import { WorkspaceService } from '../workspace-service.js';
import type { Assignment } from '../types.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER_OWNER = '019f7cdf-023c-74e0-9ead-9c155636885d';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

// Seeds the state-manager DB (STATE_MANAGER_DB_PATH) with a session's plan
// allowed_files so the unassigned commit / commit-and-push lanes can scope their
// staged tree. NEVER assign process.env directly — vi.stubEnv is unwound by the
// afterEach vi.unstubAllEnvs().
function seedPlanScope(directories: string[], providerRootSessionId: string, files: string[]): void {
  const dir = mkdtempSync(join(tmpdir(), 'ironclaude-plan-scope-'));
  directories.push(dir);
  const dbPath = join(dir, 'state.db');
  const d = new Database(dbPath);
  d.exec('CREATE TABLE wave_tasks (terminal_session TEXT, allowed_files TEXT)');
  d.prepare('INSERT INTO wave_tasks (terminal_session, allowed_files) VALUES (?,?)')
    .run(providerRootSessionId, JSON.stringify(files));
  d.close();
  vi.stubEnv('STATE_MANAGER_DB_PATH', dbPath);
}

describe('direct Git authority', () => {
  const directories: string[] = [];

  afterEach(() => {
    vi.unstubAllEnvs();
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

  it('the missing-intent refusal names the remedy while preserving the matching-human-intent prefix', () => {
    const { root, database, assignment } = setup(false);
    const expected = commitEvidence(assignment);
    const nonce = issue(database, assignment, 'commit', expected);
    verify(root, database, assignment, 'commit', expected, nonce); // consumes the intent
    let message = '';
    try {
      verify(root, database, assignment, 'commit', expected, nonce); // no matching intent now
    } catch (error) {
      message = error instanceof Error ? error.message : String(error);
    }
    // The append preserves the original substring (18 existing assertions rely on it) AND names the fix.
    expect(message).toContain('matching human intent');
    expect(message).toContain('does not mint intent');
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

  it.each([
    'different workspace with same provider root',
    'same workspace with different provider root',
  ] as const)('keeps managed commit and commit-and-push authority under %s foreign primary ownership', (mismatch) => {
    for (const operation of ['commit', 'commit-and-push'] as const) {
      const foreign = setup(true);
      let owner: Assignment;
      let ownerSessionId: string;
      if (mismatch === 'different workspace with same provider root') {
        owner = new WorkspaceService(foreign.database).reserveWorkerWorktree({
          repositoryPath: foreign.root,
          workspaceGuid: randomUUID(),
          workerId: `foreign-${operation}`,
          integrationTarget: 'main',
        });
        ownerSessionId = OWNER;
      } else {
        owner = foreign.assignment;
        ownerSessionId = OTHER_OWNER;
      }
      foreign.database.prepare(`
        INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
        VALUES (?, ?, ?)
      `).run(owner.repository_identity, owner.workspace_guid, ownerSessionId);
      const expected = operation === 'commit'
        ? commitEvidence(foreign.assignment)
        : commitAndPushEvidence(foreign.assignment);
      const nonce = issue(foreign.database, foreign.assignment, operation, expected);
      const before = foreign.database.prepare(
        'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
      ).get(foreign.assignment.repository_identity);

      const authority = verify(foreign.root, foreign.database, foreign.assignment, operation, expected, nonce);

      expect(authority).toMatchObject({
        checkoutMode: 'managed',
        worktreePath: foreign.assignment.worktree_path,
      });
      expect(() => revalidateAuthorizedCommitState(authority)).not.toThrow();
      expect(foreign.database.prepare(
        'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
      ).get(foreign.assignment.repository_identity)).toEqual(before);
    }
  });

  it('revalidates ownership after authorization', () => {

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

  function unassignedDatabase(): ReturnType<typeof initDb> {
    const databaseDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-db-'));
    directories.push(databaseDirectory);
    return initDb(join(databaseDirectory, 'authority.db'));
  }

  it('resolves an unassigned primary checkout with zero assignments for the session', () => {
    const root = repository(false);
    const database = unassignedDatabase();

    const result = resolveUnassignedPrimaryCheckout(database, root, OWNER);

    expect(result).toEqual({ mode: 'primary-unassigned', path: realpathSync(root) });
  });

  it('refuses an unassigned primary checkout owned by another session', () => {
    const root = repository(false);
    const database = unassignedDatabase();
    const foreignWorker = new WorkspaceService(database).reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid: randomUUID(),
      workerId: 'foreign-worker',
      integrationTarget: 'main',
    });
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(foreignWorker.repository_identity, foreignWorker.workspace_guid, OTHER_OWNER);

    expect(() => resolveUnassignedPrimaryCheckout(database, root, OWNER)).toThrow(/owned by another session/);
  });

  it('refuses an unassigned primary checkout in detached HEAD', () => {
    const root = repository(false);
    const sha = git(root, 'rev-parse', 'HEAD');
    git(root, 'checkout', sha);
    const database = unassignedDatabase();

    expect(() => resolveUnassignedPrimaryCheckout(database, root, OWNER)).toThrow(/detached|branch/);
  });

  function issueUnassigned(database: ReturnType<typeof initDb>, root: string) {
    return issueDirectGitHumanIntent(database, {
      repositoryPath: root,
      workspaceGuid: undefined,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
  }

  function verifyUnassigned(database: ReturnType<typeof initDb>, root: string) {
    return verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: undefined,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
  }

  it('issues and consumes an unassigned-primary commit authority via the primary sentinel', () => {
    const root = repository(false);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'unassigned.txt'), 'staged\n');
    git(root, 'add', 'unassigned.txt');
    seedPlanScope(directories, OWNER, ['unassigned.txt']);

    issueUnassigned(database, root);
    const authority = verifyUnassigned(database, root);

    expect(authority.checkoutMode).toBe('primary-unassigned');
    expect(authority.workspaceGuid.startsWith('primary:')).toBe(true);
    expect(authority.worktreePath).toBe(realpathSync(root));
    // Single-use: the sentinel intent is consumed exactly once.
    expect(() => verifyUnassigned(database, root)).toThrow('requires a matching human intent');
  });

  it('does not let a managed-guid intent satisfy an unassigned-primary verify', () => {
    const root = repository(false);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'unassigned.txt'), 'staged\n');
    git(root, 'add', 'unassigned.txt');
    seedPlanScope(directories, OWNER, ['unassigned.txt']);

    issueUnassigned(database, root);
    const authority = verifyUnassigned(database, root); // consumes the sentinel intent

    // Plant a managed-lane intent that matches repo + operation + channel + provider
    // + evidence EXACTLY; the sole discriminator left is the workspace_guid
    // (a real UUID vs the primary sentinel). It must NOT satisfy an unassigned verify.
    createHumanIntent(database, {
      operation: 'commit',
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: authority.repositoryIdentity,
      workspaceGuid: randomUUID(),
      expectedEvidence: authority.evidence,
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: randomUUID(),
    });

    expect(() => verifyUnassigned(database, root)).toThrow('requires a matching human intent');
  });

  it('does not let an unassigned-primary sentinel intent satisfy a managed verify', () => {
    const { root, database, assignment } = setup(false);
    // Managed commit evidence the server will observe for this assignment.
    const evidence = commitEvidence(assignment);

    // Plant a sentinel-guid intent matching repo + operation + channel + provider
    // + evidence EXACTLY; the sole discriminator is the workspace_guid (primary
    // sentinel vs the assignment's real UUID). A managed verify must NOT consume it.
    createHumanIntent(database, {
      operation: 'commit',
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: `primary:${assignment.repository_identity}`,
      expectedEvidence: evidence,
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: randomUUID(),
    });

    expect(() => verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    })).toThrow('requires a matching human intent');
  });

  it('does not let an unassigned intent from one repo satisfy an unassigned verify in another', () => {
    const rootX = repository(false);
    const rootY = repository(false);
    const database = unassignedDatabase();
    // Both repos stage the in-scope file so evidence is observed (not empty-scope);
    // the isolation is proved by the intent mismatch, not by a scope refusal.
    writeFileSync(join(rootX, 'unassigned.txt'), 'staged\n');
    git(rootX, 'add', 'unassigned.txt');
    writeFileSync(join(rootY, 'unassigned.txt'), 'staged\n');
    git(rootY, 'add', 'unassigned.txt');
    seedPlanScope(directories, OWNER, ['unassigned.txt']);

    issueUnassigned(database, rootX);

    // Cross-repo isolation: repoY's verify binds repoY's repository_identity (a
    // distinct WHERE column) and a repoY-derived sentinel; the repoX intent
    // cannot satisfy it.
    expect(() => verifyUnassigned(database, rootY)).toThrow('requires a matching human intent');
  });

  function issueUnassignedPush(database: ReturnType<typeof initDb>, root: string, humanChannel = 'codex-user-prompt') {
    return issueDirectGitHumanIntent(database, {
      repositoryPath: root,
      workspaceGuid: undefined,
      providerRootSessionId: OWNER,
      humanChannel,
      operation: 'push',
    });
  }

  function verifyUnassignedPush(database: ReturnType<typeof initDb>, root: string, humanChannel = 'codex-user-prompt') {
    return verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: undefined,
      providerRootSessionId: OWNER,
      humanChannel,
      operation: 'push',
    });
  }

  it('issues, consumes, and pushes an unassigned-primary push authority (fast-forward), moving the remote', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    const remoteBefore = git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
    // A NEW local commit ahead of origin — non-vacuous oracle: the remote must MOVE.
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead of origin');
    const localHead = git(root, 'rev-parse', 'HEAD');
    expect(remoteBefore).not.toBe(localHead);

    issueUnassignedPush(database, root);
    const authority = verifyUnassignedPush(database, root);
    expect(authority.checkoutMode).toBe('primary-unassigned');
    expect(authority.operation).toBe('push');
    expect(authority.workspaceGuid.startsWith('primary:')).toBe(true);

    pushExactAuthorizedRef(authority); // any throw fails the test

    expect(git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0]).toBe(localHead);
  });

  it('refuses a non-fast-forward unassigned-primary push at issuance', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    const c0 = git(root, 'rev-parse', 'HEAD');
    // Advance origin/main past local, then move local back to c0 — origin is now
    // AHEAD of local: a genuine non-fast-forward the ff-proof must refuse.
    writeFileSync(join(root, 'remote-ahead.txt'), 'remote ahead\n');
    git(root, 'add', 'remote-ahead.txt');
    git(root, 'commit', '-m', 'remote ahead');
    git(root, 'push', 'origin', 'main:refs/heads/main');
    git(root, 'reset', '--hard', c0);

    expect(() => issueUnassignedPush(database, root)).toThrow(/fast-forward/);
  });

  it('allows an unassigned-primary push of a new branch with no remote ref (empty lease)', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    git(root, 'checkout', '-b', 'feature');
    writeFileSync(join(root, 'feature.txt'), 'feature\n');
    git(root, 'add', 'feature.txt');
    git(root, 'commit', '-m', 'feature work');

    issueUnassignedPush(database, root);
    const authority = verifyUnassignedPush(database, root);
    expect(authority.operation).toBe('push');
    pushExactAuthorizedRef(authority);
    expect(git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/feature').split(/\s+/)[0])
      .toBe(git(root, 'rev-parse', 'HEAD'));
  });

  it('re-proves zero assignments at push time: an assignment planted after verify blocks the push', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead');

    issueUnassignedPush(database, root);
    const authority = verifyUnassignedPush(database, root);
    // A managed assignment for this session appears AFTER the authority was verified.
    new WorkspaceService(database).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(() => pushExactAuthorizedRef(authority)).toThrow(/zero active assignments/);
  });

  it('re-proves primary ownership at push time: a foreign owner planted after verify blocks the push', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead');

    issueUnassignedPush(database, root);
    const authority = verifyUnassignedPush(database, root);
    const foreign = new WorkspaceService(database).reserveWorkerWorktree({
      repositoryPath: root, workspaceGuid: randomUUID(), workerId: 'foreign-worker', integrationTarget: 'main',
    });
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(foreign.repository_identity, foreign.workspace_guid, OTHER_OWNER);

    expect(() => pushExactAuthorizedRef(authority)).toThrow(/owned by another session/);
  });

  it('does not let a commit sentinel intent satisfy an unassigned-primary push verify', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead');
    // The commit issue reads plan scope; stage an in-scope file so it observes
    // evidence rather than refusing on empty scope, isolating the op-mismatch proof.
    writeFileSync(join(root, 'staged.txt'), 'staged\n');
    git(root, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);
    issueUnassigned(database, root); // operation: 'commit'
    expect(() => verifyUnassignedPush(database, root)).toThrow('requires a matching human intent');
  });

  it('does not consume an unassigned-primary push intent minted on a different channel', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead');
    issueUnassignedPush(database, root, 'claude-user-prompt');
    expect(() => verifyUnassignedPush(database, root, 'codex-user-prompt'))
      .toThrow('requires a matching human intent');
  });

  it('does not consume an expired unassigned-primary push intent', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead');
    issueUnassignedPush(database, root);
    database.prepare("UPDATE human_intents SET expires_at = '2000-01-01T00:00:00.000Z'").run();
    expect(() => verifyUnassignedPush(database, root)).toThrow('requires a matching human intent');
  });

  it('pushes an unassigned-primary push with NO plan scope even when the state DB is missing (push never reads scope)', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'ahead.txt'), 'ahead\n');
    git(root, 'add', 'ahead.txt');
    git(root, 'commit', '-m', 'local ahead');
    const localHead = git(root, 'rev-parse', 'HEAD');
    // Point plan-scope at a DB that does not exist. A commit/cap lane would fail
    // closed here; the push lane must not read scope at all.
    vi.stubEnv('STATE_MANAGER_DB_PATH', join(tmpdir(), `ironclaude-nonexistent-${randomUUID()}`, 'state.db'));

    issueUnassignedPush(database, root);
    const authority = verifyUnassignedPush(database, root);
    pushExactAuthorizedRef(authority); // any throw fails the test

    expect(git(root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0]).toBe(localHead);
  });

  function issueUnassignedCap(database: ReturnType<typeof initDb>, root: string, humanChannel = 'codex-user-prompt') {
    return issueDirectGitHumanIntent(database, {
      repositoryPath: root,
      workspaceGuid: undefined,
      providerRootSessionId: OWNER,
      humanChannel,
      operation: 'commit-and-push',
    });
  }

  function verifyUnassignedCap(database: ReturnType<typeof initDb>, root: string, humanChannel = 'codex-user-prompt') {
    return verifyDirectGitAuthority(database, {
      repositoryPath: root,
      workspaceGuid: undefined,
      providerRootSessionId: OWNER,
      humanChannel,
      operation: 'commit-and-push',
    });
  }

  it('issues and consumes an unassigned-primary commit-and-push authority (fast-forward over parent)', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    writeFileSync(join(root, 'staged.txt'), 'staged\n');
    git(root, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);

    issueUnassignedCap(database, root);
    const authority = verifyUnassignedCap(database, root);
    expect(authority.checkoutMode).toBe('primary-unassigned');
    expect(authority.operation).toBe('commit-and-push');
    expect(authority.workspaceGuid.startsWith('primary:')).toBe(true);
  });

  it('refuses a non-fast-forward unassigned-primary commit-and-push at issuance', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    const c0 = git(root, 'rev-parse', 'HEAD');
    // Advance origin/main past HEAD, then move local back to c0 so the commit's
    // parent (= HEAD = c0) is BEHIND origin — a non-fast-forward the ff-over-parent
    // proof must refuse.
    writeFileSync(join(root, 'remote-ahead.txt'), 'remote ahead\n');
    git(root, 'add', 'remote-ahead.txt');
    git(root, 'commit', '-m', 'remote ahead');
    git(root, 'push', 'origin', 'main:refs/heads/main');
    git(root, 'reset', '--hard', c0);
    writeFileSync(join(root, 'staged.txt'), 'staged\n');
    git(root, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);

    expect(() => issueUnassignedCap(database, root)).toThrow(/fast-forward/);
  });

  it('allows an unassigned-primary commit-and-push on a new branch with no remote ref', () => {
    const root = repository(true);
    const database = unassignedDatabase();
    git(root, 'checkout', '-b', 'feature');
    writeFileSync(join(root, 'staged.txt'), 'staged\n');
    git(root, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);

    issueUnassignedCap(database, root);
    const authority = verifyUnassignedCap(database, root);
    expect(authority.operation).toBe('commit-and-push');
  });

  // Sentinel isolation BOTH directions (requirements R6 "and vice versa"): the
  // operation column + the canonical-JSON evidence byte-match make every cross-lane
  // consumption fail.
  it('does not let a commit or push intent satisfy a commit-and-push verify (forward)', () => {
    const rootA = repository(true);
    const dbA = unassignedDatabase();
    writeFileSync(join(rootA, 'staged.txt'), 'staged\n');
    git(rootA, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);
    issueUnassigned(dbA, rootA); // operation 'commit'
    expect(() => verifyUnassignedCap(dbA, rootA)).toThrow('requires a matching human intent');

    const rootB = repository(true);
    const dbB = unassignedDatabase();
    writeFileSync(join(rootB, 'ahead.txt'), 'ahead\n');
    git(rootB, 'add', 'ahead.txt');
    git(rootB, 'commit', '-m', 'local ahead');
    // Stage the in-scope file so the cap verify observes evidence and fails on the
    // op mismatch (push intent vs cap verify), not on empty scope.
    writeFileSync(join(rootB, 'staged.txt'), 'staged\n');
    git(rootB, 'add', 'staged.txt');
    issueUnassignedPush(dbB, rootB); // operation 'push'
    expect(() => verifyUnassignedCap(dbB, rootB)).toThrow('requires a matching human intent');
  });

  it('does not let a commit-and-push intent satisfy a commit or push verify (reverse)', () => {
    const rootA = repository(true);
    const dbA = unassignedDatabase();
    writeFileSync(join(rootA, 'staged.txt'), 'staged\n');
    git(rootA, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);
    issueUnassignedCap(dbA, rootA); // operation 'commit-and-push'
    expect(() => verifyUnassigned(dbA, rootA)).toThrow('requires a matching human intent'); // verify as 'commit'

    const rootB = repository(true);
    const dbB = unassignedDatabase();
    writeFileSync(join(rootB, 'staged.txt'), 'staged\n');
    git(rootB, 'add', 'staged.txt');
    issueUnassignedCap(dbB, rootB); // operation 'commit-and-push'
    expect(() => verifyUnassignedPush(dbB, rootB)).toThrow('requires a matching human intent'); // verify as 'push'
  });

  // Issuance-gate battery for the widened hook lane: a zero-assignment session may
  // now issue an UNASSIGNED commit, push, or commit-and-push intent, but nothing else widens.
  function hookArgs(root: string, operation: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      hook_event_name: 'UserPromptSubmit',
      invocation_source: 'human',
      operation,
      human_channel: 'codex-user-prompt',
      repository_path: root,
      owner_session_id: OWNER,
      ...extra,
    };
  }

  it('issues an unassigned commit intent from the hook when the session holds zero assignments', () => {
    const root = repository(false);
    const database = unassignedDatabase();
    // Commit evidence now scopes to allowed_files; stage an in-scope file so the
    // hook issue observes evidence rather than refusing on empty scope.
    writeFileSync(join(root, 'staged.txt'), 'staged\n');
    git(root, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);

    const receipt = issueHumanIntentFromHook(database, hookArgs(root, 'commit'));

    // The widening: zero assignments no longer throws for a bare commit; it issues.
    expect(receipt).toMatchObject({ issued: true, operation: 'commit' });
  });

  it('issues an unassigned push intent from the hook when the session holds zero assignments', () => {
    const root = repository(true); // push evidence observes origin, so a remote is required
    const database = unassignedDatabase();

    const receipt = issueHumanIntentFromHook(database, hookArgs(root, 'push'));

    // The widening: zero assignments now also issues for a bare push (human-only lane).
    expect(receipt).toMatchObject({ issued: true, operation: 'push' });
  });

  it('issues an unassigned commit-and-push intent from the hook when the session holds zero assignments', () => {
    const root = repository(true); // commit-and-push evidence observes origin
    const database = unassignedDatabase();
    writeFileSync(join(root, 'staged.txt'), 'staged\n');
    git(root, 'add', 'staged.txt');
    seedPlanScope(directories, OWNER, ['staged.txt']);

    const receipt = issueHumanIntentFromHook(database, hookArgs(root, 'commit-and-push'));

    // The widening: zero assignments now also issues for a bare commit-and-push (human-only lane).
    expect(receipt).toMatchObject({ issued: true, operation: 'commit-and-push' });
  });

  it('refuses every non-commit, non-push, non-commit-and-push hook operation when the session holds zero assignments', () => {
    // Bounds the widening to commit + push + commit-and-push only: dropping the operation
    // guard would let one of these issue instead of throw.
    for (const operation of ['use-primary-checkout', 'return-to-managed-worktree'] as const) {
      const root = repository(false);
      const database = unassignedDatabase();
      expect(() => issueHumanIntentFromHook(database, hookArgs(root, operation)))
        .toThrow('exactly one active assignment');
    }
  });

  it('refuses a zero-assignment hook commit that supplies a workspace_guid', () => {
    const root = repository(false);
    const database = unassignedDatabase();

    // The unassigned lane takes NO guid: dropping the `requestedGuid === undefined`
    // condition would let this issue an intent instead of throwing.
    expect(() => issueHumanIntentFromHook(database, hookArgs(root, 'commit', { workspace_guid: randomUUID() })))
      .toThrow('exactly one active assignment');
  });

  it('mints a reconcile intent from the hook when the session holds exactly one active assignment', () => {
    const { root, database } = setup(false);

    const receipt = issueHumanIntentFromHook(database, hookArgs(root, 'reconcile')) as { operation: string };

    expect(receipt.operation).toBe('reconcile');
  });

  it('refuses a hook reconcile when the session holds zero assignments (stays in the assigned branch)', () => {
    const root = repository(false);
    const database = unassignedDatabase();

    // Reconcile is deliberately absent from the unassigned-widening operation list
    // (:49): proves it falls through to the assigned-branch guard, not the
    // zero-assignment commit/push/commit-and-push lane.
    expect(() => issueHumanIntentFromHook(database, hookArgs(root, 'reconcile')))
      .toThrow('exactly one active assignment');
  });

  it('cannot hold two active assignments in one repository (bounds the hook count to 0 or 1)', () => {
    // The literal "two assignments reach the hook, so it throws" case is
    // unbuildable: the partial unique index active_assignment_owner_repository is
    // on (owner_session_id, repository_identity) with the SAME non-terminal
    // predicate the hook query filters on, so length is always 0 or 1 and the
    // retained `length !== 1` throw is unreachable-but-defensive. The honest proof
    // of the "more than one" bound is that the DB refuses the second active row.
    const root = repository(false);
    const databaseDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-db-'));
    directories.push(databaseDirectory);
    const database = initDb(join(databaseDirectory, 'authority.db'));
    const service = new WorkspaceService(database);
    service.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const second = service.reserveWorkerWorktree({
      repositoryPath: root, workspaceGuid: randomUUID(), workerId: 'second-worker', integrationTarget: 'main',
    });

    // Matching the column list (not just /UNIQUE/) proves it is THAT index, so the
    // guard fails if someone drops or rescopes it and lets the count exceed 1.
    expect(() => database.prepare('UPDATE assignments SET owner_session_id = ? WHERE workspace_guid = ?')
      .run(OWNER, second.workspace_guid))
      .toThrow(/owner_session_id, assignments\.repository_identity/);
  });

  describe('reconcile direct-Git operation', () => {
    it('observes managed reconcile evidence pinned to live HEAD, and denies it for a non-managed checkout', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'reconcile target');
      const headOid = git(assignment.worktree_path, 'rev-parse', 'HEAD');

      issueDirectGitHumanIntent(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      });
      const authority = verifyDirectGitAuthority(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      });

      expect(authority.evidence).toEqual({
        checkoutMode: 'managed',
        canonicalBranch: assignment.branch,
        localRef: `refs/heads/${assignment.branch}`,
        headOid,
      });

      // Non-managed (primary) checkout must deny reconcile evidence entirely.
      const primary = setup(false);
      acquirePrimary(primary.database, primary.assignment);
      expect(() => issueDirectGitHumanIntent(primary.database, {
        repositoryPath: primary.root,
        workspaceGuid: primary.assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      })).toThrow('evidence');
    });

    it('authorizes reconcile from live HEAD but structurally excludes it from push authority', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'reconcile target');

      issueDirectGitHumanIntent(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      });
      const authority = verifyDirectGitAuthority(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      });

      expect(authority.operation).toBe('reconcile');
      // This exact message fires only at the single-use gate (:640) — a reconcile
      // authority reaches it because the operation guard above (:639) rejects only
      // 'commit' (it does NOT special-case 'reconcile') and the authority was
      // excluded from usablePushAuthorizations at verify-time (:589). A bare
      // .toThrow() would also pass if :639 threw its own, differently-worded error
      // — pin the exact text to catch that.
      expect(() => pushExactAuthorizedRef(authority)).toThrow('Direct Git push authority is single-use');
    });

    it('fails reconcile verification when live HEAD no longer byte-matches the evidence pinned at issuance', () => {
      const { root, database, assignment } = setup(false);
      issueDirectGitHumanIntent(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      });
      git(assignment.worktree_path, 'commit', '-m', 'head moved after mint');

      expect(() => verifyDirectGitAuthority(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'reconcile',
      })).toThrow('requires a matching human intent');
    });
  });

  describe('confirm-resolution direct-Git operation', () => {
    it('a confirm-resolution intent mints and consumes, binding the live managed HEAD', () => {
      const root = repository(true);
      const databaseDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-db-'));
      directories.push(databaseDirectory);
      const database = initDb(join(databaseDirectory, 'authority.db'));
      const assignment = new WorkspaceService(database).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
      git(assignment.worktree_path, 'commit', '--allow-empty', '-m', 'confirm-resolution target');
      const headOid = git(assignment.worktree_path, 'rev-parse', 'HEAD');

      issueDirectGitHumanIntent(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'confirm-resolution' as unknown as DirectGitOperation,
      });
      const authority = verifyDirectGitAuthority(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'confirm-resolution' as unknown as DirectGitOperation,
      });

      expect(authority.operation).toBe('confirm-resolution');
      expect((authority.evidence as ReconcileEvidence).headOid).toBe(headOid);
    });

    it('authorizes confirm-resolution from live HEAD but structurally excludes it from push authority', () => {
      const root = repository(true);
      const databaseDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-git-authority-db-'));
      directories.push(databaseDirectory);
      const database = initDb(join(databaseDirectory, 'authority.db'));
      const assignment = new WorkspaceService(database).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
      git(assignment.worktree_path, 'commit', '--allow-empty', '-m', 'confirm-resolution target');

      issueDirectGitHumanIntent(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'confirm-resolution' as unknown as DirectGitOperation,
      });
      const authority = verifyDirectGitAuthority(database, {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'confirm-resolution' as unknown as DirectGitOperation,
      });

      expect(() => pushExactAuthorizedRef(authority)).toThrow('Direct Git push authority is single-use');
    });
  });
});
