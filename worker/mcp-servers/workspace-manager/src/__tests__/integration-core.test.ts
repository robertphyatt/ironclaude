import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { registerFinalizationTests } from './integration-cases.js';
import { initDb } from '../db.js';
import { issueDirectGitHumanIntent, verifyDirectGitAuthority } from '../git-authority.js';
import { finalizePrimaryUnassignedCommit, finalizePrimaryUnassignedPush, finalizePrimaryUnassignedCommitAndPush } from '../integration.js';

registerFinalizationTests('core');

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

describe('finalizePrimaryUnassignedCommit', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function setupUnassignedPrimary() {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-primary-unassigned-'));
    const databaseDir = mkdtempSync(join(tmpdir(), 'ironclaude-primary-unassigned-db-'));
    directories.push(root, databaseDir);
    git(root, 'init', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Primary Unassigned Test');
    git(root, 'config', 'user.email', 'primary-unassigned@example.invalid');
    writeFileSync(join(root, 'README.md'), 'initial\n');
    git(root, 'add', 'README.md');
    git(root, 'commit', '-m', 'initial');
    writeFileSync(join(root, 'work.txt'), 'approved\n');
    git(root, 'add', 'work.txt');
    const database = initDb(join(databaseDir, 'state.db'));
    const providerRootSessionId = randomUUID();
    return { root, database, providerRootSessionId };
  }

  function issueAndVerifyUnassigned(state: ReturnType<typeof setupUnassignedPrimary>) {
    issueDirectGitHumanIntent(state.database, {
      repositoryPath: state.root,
      workspaceGuid: undefined,
      providerRootSessionId: state.providerRootSessionId,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
    return verifyDirectGitAuthority(state.database, {
      repositoryPath: state.root,
      workspaceGuid: undefined,
      providerRootSessionId: state.providerRootSessionId,
      humanChannel: 'codex-user-prompt',
      operation: 'commit',
    });
  }

  it('commits an unassigned-primary authority onto the current branch without managed integration machinery', () => {
    const state = setupUnassignedPrimary();
    const priorHead = git(state.root, 'rev-parse', 'HEAD');
    const stagedTree = git(state.root, 'write-tree');
    const authority = issueAndVerifyUnassigned(state);

    const result = finalizePrimaryUnassignedCommit(authority, 'approved unassigned-primary commit');

    expect(result.state).toBe('committed');
    expect(result.commit).toBe(git(state.root, 'rev-parse', 'HEAD'));
    expect(git(state.root, 'rev-parse', 'HEAD^')).toBe(priorHead);
    expect(git(state.root, 'rev-parse', 'HEAD^{tree}')).toBe(stagedTree);
    expect(git(state.root, 'log', '-1', '--format=%s')).toBe('approved unassigned-primary commit');
  });

  it('throws and does not advance the branch when HEAD moved after the authority was captured', () => {
    const state = setupUnassignedPrimary();
    const authority = issueAndVerifyUnassigned(state);

    // Simulate the branch moving out from under the captured authority: a manual
    // commit lands on the branch before finalize runs.
    git(state.root, 'commit', '--allow-empty', '-m', 'operator moved HEAD');
    const headAfterManualCommit = git(state.root, 'rev-parse', 'HEAD');

    expect(() => finalizePrimaryUnassignedCommit(authority, 'should not land')).toThrow();
    expect(git(state.root, 'rev-parse', 'HEAD')).toBe(headAfterManualCommit);
    expect(git(state.root, 'log', '-1', '--format=%s')).toBe('operator moved HEAD');
  });

  it('creates exactly one new local commit and performs no remote/push activity', () => {
    const state = setupUnassignedPrimary();
    const beforeCount = Number(git(state.root, 'rev-list', '--count', 'HEAD'));
    const authority = issueAndVerifyUnassigned(state);

    const result = finalizePrimaryUnassignedCommit(authority, 'approved unassigned-primary commit');

    expect(result.state).toBe('committed');
    const afterCount = Number(git(state.root, 'rev-list', '--count', 'HEAD'));
    expect(afterCount).toBe(beforeCount + 1);
    // No remote configured for this repo at all; a push attempt would throw
    // because `origin` does not exist. The function completing successfully
    // with no remote configured is itself proof it performed no push.
    expect(() => git(state.root, 'remote', 'get-url', 'origin')).toThrow();
  });
});

describe('finalizePrimaryUnassignedPush', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function setupUnassignedPrimaryWithRemote() {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-primary-push-'));
    const databaseDir = mkdtempSync(join(tmpdir(), 'ironclaude-primary-push-db-'));
    const remote = mkdtempSync(join(tmpdir(), 'ironclaude-primary-push-remote-'));
    directories.push(root, databaseDir, remote);
    git(root, 'init', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Primary Push Test');
    git(root, 'config', 'user.email', 'primary-push@example.invalid');
    writeFileSync(join(root, 'README.md'), 'initial\n');
    git(root, 'add', 'README.md');
    git(root, 'commit', '-m', 'initial');
    git(remote, 'init', '--bare');
    git(root, 'remote', 'add', 'origin', remote);
    git(root, 'push', 'origin', 'main:refs/heads/main');
    const database = initDb(join(databaseDir, 'state.db'));
    const providerRootSessionId = randomUUID();
    return { root, database, providerRootSessionId };
  }

  function issueAndVerifyUnassignedOp(
    state: ReturnType<typeof setupUnassignedPrimaryWithRemote>,
    operation: 'commit' | 'push' | 'commit-and-push',
  ) {
    issueDirectGitHumanIntent(state.database, {
      repositoryPath: state.root,
      workspaceGuid: undefined,
      providerRootSessionId: state.providerRootSessionId,
      humanChannel: 'codex-user-prompt',
      operation,
    });
    return verifyDirectGitAuthority(state.database, {
      repositoryPath: state.root,
      workspaceGuid: undefined,
      providerRootSessionId: state.providerRootSessionId,
      humanChannel: 'codex-user-prompt',
      operation,
    });
  }

  it('pushes a fast-forward unassigned-primary push authority to origin (pushed-only), moving the remote', () => {
    const state = setupUnassignedPrimaryWithRemote();
    const remoteBefore = git(state.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
    writeFileSync(join(state.root, 'work.txt'), 'approved\n');
    git(state.root, 'add', 'work.txt');
    git(state.root, 'commit', '-m', 'approved local work');
    const localHead = git(state.root, 'rev-parse', 'HEAD');
    expect(remoteBefore).not.toBe(localHead);

    const authority = issueAndVerifyUnassignedOp(state, 'push');
    const result = finalizePrimaryUnassignedPush(authority);

    expect(result.state).toBe('pushed-only');
    expect(git(state.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0]).toBe(localHead);
  });

  it('refuses an unassigned-primary push whose remote advanced after verify (lease broken)', () => {
    const state = setupUnassignedPrimaryWithRemote();
    writeFileSync(join(state.root, 'work.txt'), 'approved\n');
    git(state.root, 'add', 'work.txt');
    git(state.root, 'commit', '-m', 'approved local work');
    const authority = issueAndVerifyUnassignedOp(state, 'push');
    // A second writer advances origin/main out from under the lease AFTER verify.
    writeFileSync(join(state.root, 'later.txt'), 'later\n');
    git(state.root, 'add', 'later.txt');
    git(state.root, 'commit', '-m', 'origin advanced after verify');
    git(state.root, 'push', 'origin', 'HEAD:refs/heads/main');

    // The frozen-evidence ff re-check cannot catch this (C2); assertRemoteEvidence
    // inside pushExactAuthorizedRef does — the lease no longer matches.
    expect(() => finalizePrimaryUnassignedPush(authority)).toThrow(/evidence changed or is malformed/);
  });

  it('classifies a successful push as pushed-only even when the transport result is uncertain', () => {
    const state = setupUnassignedPrimaryWithRemote();
    writeFileSync(join(state.root, 'work.txt'), 'approved\n');
    git(state.root, 'add', 'work.txt');
    git(state.root, 'commit', '-m', 'approved local work');
    const localHead = git(state.root, 'rev-parse', 'HEAD');
    const authority = issueAndVerifyUnassignedOp(state, 'push');

    let observed = false;
    const result = finalizePrimaryUnassignedPush(authority, {
      afterRemoteMutationBeforeResult: () => { observed = true; throw new Error('simulated transport uncertainty'); },
    });

    expect(observed).toBe(true);
    // Readback is authoritative: the remote proves the exact commit landed.
    expect(result.state).toBe('pushed-only');
    expect(git(state.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0]).toBe(localHead);
  });

  it('the two unassigned finalizers reject each other’s operation, so the dispatch must branch on operation', () => {
    const state = setupUnassignedPrimaryWithRemote();
    writeFileSync(join(state.root, 'work.txt'), 'approved\n');
    git(state.root, 'add', 'work.txt');
    git(state.root, 'commit', '-m', 'approved local work');

    const pushAuthority = issueAndVerifyUnassignedOp(state, 'push');
    expect(() => finalizePrimaryUnassignedCommit(pushAuthority, 'wrong lane')).toThrow(/commits only/);

    const commitState = setupUnassignedPrimaryWithRemote();
    writeFileSync(join(commitState.root, 'work.txt'), 'approved\n');
    git(commitState.root, 'add', 'work.txt');
    const commitAuthority = issueAndVerifyUnassignedOp(commitState, 'commit');
    expect(() => finalizePrimaryUnassignedPush(commitAuthority)).toThrow(/pushes only/);
  });

  it('commits the staged tree then pushes it (state pushed), moving the remote to the new commit', () => {
    const state = setupUnassignedPrimaryWithRemote();
    const parentHead = git(state.root, 'rev-parse', 'HEAD');
    const remoteBefore = git(state.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
    writeFileSync(join(state.root, 'work.txt'), 'approved\n');
    git(state.root, 'add', 'work.txt');
    const stagedTree = git(state.root, 'write-tree');

    const authority = issueAndVerifyUnassignedOp(state, 'commit-and-push');
    const result = finalizePrimaryUnassignedCommitAndPush(authority, 'approved commit-and-push');

    expect(result.state).toBe('pushed');
    const newHead = git(state.root, 'rev-parse', 'HEAD');
    expect(result.integratedCommit).toBe(newHead);
    // Exactly one new commit, child of the prior HEAD, carrying the staged tree.
    expect(git(state.root, 'rev-parse', 'HEAD^')).toBe(parentHead);
    expect(git(state.root, 'rev-parse', 'HEAD^{tree}')).toBe(stagedTree);
    // The remote moved from its prior tip to the new commit.
    expect(remoteBefore).toBe(parentHead);
    expect(git(state.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0]).toBe(newHead);
  });

  it('preserves the local commit when the push fails on a broken lease (never-lose-work)', () => {
    const state = setupUnassignedPrimaryWithRemote();
    const parentHead = git(state.root, 'rev-parse', 'HEAD');
    writeFileSync(join(state.root, 'work.txt'), 'approved\n');
    git(state.root, 'add', 'work.txt');
    const stagedTree = git(state.root, 'write-tree');

    const authority = issueAndVerifyUnassignedOp(state, 'commit-and-push');
    // A second writer advances origin/main AFTER verify, WITHOUT moving local HEAD:
    // plumbing only, so createExactCommit still succeeds and the PUSH fails on the lease.
    const intruder = git(state.root, 'commit-tree', 'HEAD^{tree}', '-p', parentHead, '-m', 'origin advanced after verify');
    git(state.root, 'push', 'origin', `${intruder}:refs/heads/main`);

    expect(() => finalizePrimaryUnassignedCommitAndPush(authority, 'approved commit-and-push'))
      .toThrow(/evidence changed or is malformed/);
    // Never-lose-work: the local commit was created and persists, and it IS the lane's
    // exact commit (child of parentHead, staged tree) — not a vacuous survivor.
    expect(git(state.root, 'rev-parse', 'HEAD^')).toBe(parentHead);
    expect(git(state.root, 'rev-parse', 'HEAD^{tree}')).toBe(stagedTree);
    // The push never landed: origin still holds the intruder, not the lane's commit.
    expect(git(state.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0]).toBe(intruder);
  });

  it('the commit-and-push finalizer rejects a push authority, and the push finalizer rejects a commit-and-push authority', () => {
    const stateA = setupUnassignedPrimaryWithRemote();
    writeFileSync(join(stateA.root, 'work.txt'), 'approved\n');
    git(stateA.root, 'add', 'work.txt');
    git(stateA.root, 'commit', '-m', 'approved local work');
    const pushAuthority = issueAndVerifyUnassignedOp(stateA, 'push');
    expect(() => finalizePrimaryUnassignedCommitAndPush(pushAuthority, 'wrong lane')).toThrow(/commit-and-push/);

    const stateB = setupUnassignedPrimaryWithRemote();
    writeFileSync(join(stateB.root, 'work.txt'), 'approved\n');
    git(stateB.root, 'add', 'work.txt');
    const capAuthority = issueAndVerifyUnassignedOp(stateB, 'commit-and-push');
    expect(() => finalizePrimaryUnassignedPush(capAuthority)).toThrow(/pushes only/);
  });
});
