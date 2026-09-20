import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
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
  issueDirectGitHumanIntent,
  verifyDirectGitAuthority,
  type CommitAndPushEvidence,
  type CommitEvidence,
  type PushEvidence,
  type ReconcileEvidence,
} from '../git-authority.js';
import {
  classifyRebaseConflicts,
  drainCarriedObligations,
  finalizeCloseOut,
  finalizeCommanderLocalCommit,
  finalizeConfirmResolution,
  finalizeDirectAuthority,
  finalizeReconcile,
  pushPendingSummary,
  reconcileFinalization,
  recycleFinalized,
  releaseFinalized,
  resolveConflictHunk,
  syncWorktreeToTarget,
} from '../integration.js';
import { isAncestor } from '../git.js';
import { createInternalCommandDependencies } from '../cli.js';
import { createPublicToolDependencies } from '../index.js';
import { issueHumanIntentFromHook } from '../hook-intent.js';
import { WorkspaceService } from '../workspace-service.js';
import type { Assignment } from '../types.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER = '019f7cdf-023c-74e0-9ead-9c155636885d';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

/** Count of cumulativeBinaryEffect's temp diff directories currently in os.tmpdir(). */
function countFinalizeDiffTempDirs(): number {
  return readdirSync(tmpdir()).filter((name) => name.startsWith('ic-finalize-diff-')).length;
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
    expect(() => finalizeCommanderLocalCommit(s.database, commanderInput(s.root, s.assignment, 'conflict'))).toThrow();
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
    database: ReturnType<typeof initDb>, assignment: Assignment, operation: 'commit' | 'commit-and-push' | 'push' | 'reconcile' | 'close-out' | 'confirm-resolution',
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
  it('mints and verifies a close-out authority on an integrated row (evidence-light entry)', () => {
    const s = setup();
    seedIntegratedPushPending(s);
    // Evidence-light issuance succeeds despite lifecycle === 'integrated' (no HEAD/commit
    // observation at issuance), so /close-out can be minted on the persistent Case A state.
    const receipt = issueDirectGitHumanIntent(s.database, {
      repositoryPath: s.assignment.worktree_path,
      workspaceGuid: s.assignment.workspace_guid,
      providerRootSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'close-out',
    });
    expect(receipt).toMatchObject({ issued: true, operation: 'close-out' });
    // The verify arm mints a managed close-out authority.
    const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));
    expect(authority.operation).toBe('close-out');
    expect(authority.checkoutMode).toBe('managed');
  });
  it('close-out hook intent is lifecycle-tolerant (mints on an integrated row)', () => {
    const s = setup();
    seedIntegratedPushPending(s);
    const receipt = issueHumanIntentFromHook(s.database, {
      hook_event_name: 'UserPromptSubmit',
      invocation_source: 'human',
      operation: 'close-out',
      human_channel: 'codex-user-prompt',
      repository_path: s.assignment.worktree_path,
      owner_session_id: OWNER,
    });
    expect(receipt).toMatchObject({ issued: true, operation: 'close-out' });
  });
  it('managed /commit commits and STAYS in the worktree (verb 1)', () => {
    const { root, database, assignment } = setup(false);
    const rootMainBefore = git(root, 'rev-parse', 'HEAD');
    const baseBefore = (database.prepare('SELECT base_commit FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid) as { base_commit: string }).base_commit;
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));

    const result = finalizeDirectAuthority(database, authority, 'approved direct commit');

    expect(result.state).toBe('committed');
    const committed = (result as { state: 'committed'; commit: string }).commit;
    // commit landed on the worktree branch; HEAD advanced there
    expect(git(assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(committed);
    expect(git(assignment.worktree_path, 'log', '-1', '--format=%s')).toBe('approved direct commit');
    // local main NOT advanced (no integration); assignment stays active at its original base
    expect(git(root, 'rev-parse', 'HEAD')).toBe(rootMainBefore);
    expect(database.prepare('SELECT lifecycle_status, base_commit, current_head, integrated_commit FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'active', base_commit: baseBefore, current_head: committed, integrated_commit: null });
    // worktree alive; no finalization freeze happened
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(() => git(root, 'show-ref', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`)).toThrow();
  });

  it('refuses /commit on an integrated row (commit-and-stay needs an active assignment)', () => {
    const { database, assignment } = setup(false);
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));
    database.prepare("UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = current_head WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);
    expect(() => finalizeDirectAuthority(database, authority, 'refused'))
      .toThrow('Only active or ready repair assignment can begin finalization');
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

    it('close-out integrates HEAD into local main and FULLY tears the worktree down (verb 4)', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'reviewed close-out work');
      const authority = issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment));

      const result = finalizeCloseOut(database, authority);

      expect(result.state).toBe('closed-out');
      const integratedCommit = git(root, 'rev-parse', 'HEAD');
      expect(result.integratedCommit).toBe(integratedCommit);
      expect(existsSync(assignment.worktree_path)).toBe(false); // worktree removed
      expect(git(root, 'branch', '--list', assignment.branch)).toBe(''); // temp branch gone
      expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
    });

    it('close-out tears down an already-integrated row directly (integrated branch)', () => {
      const s = setup();
      seedIntegratedPushPending(s);
      const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));

      const result = finalizeCloseOut(s.database, authority);

      expect(result.state).toBe('closed-out');
      expect(existsSync(s.assignment.worktree_path)).toBe(false);
      expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
    });

    it('C1 close-out ready-branch preserve-and-defers a non-descendant frozen HEAD (repair-required)', () => {
      // RED-a (isAncestor disjunct): reviewed frozen HEAD diverged from the advanced
      // target (never rebased). The ready branch must PROVE descendant+equality before
      // integrating. Today finalizeAttestedCandidate throws 'not an exact descendant';
      // the gate returns the structured repair-required state instead, main unmoved.
      const { root, database, assignment, wt } = seedFrozenReadyNoRebase(true);
      const mainBefore = git(root, 'rev-parse', 'HEAD');
      const authority = issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment));

      const result = finalizeCloseOut(database, authority);

      expect(result.state).toBe('rebase-recovery-repair-required');
      expect(git(root, 'rev-parse', 'HEAD')).toBe(mainBefore); // local main UNCHANGED
      expect(existsSync(wt)).toBe(true); // worktree preserved
      expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
    });

    it('C1 close-out altered-content descendant is rejected by the equality gate (never integrates)', () => {
      // RED-b (the SECURITY falsifier): HEAD is a DESCENDANT of the advanced target but
      // its cumulative effect DIFFERS from the reviewed frozen effect. finalizeAttestedCandidate's
      // descendant check passes it, so today the altered content INTEGRATES and main advances.
      // Only the cumulativeBinaryEffect equality clause catches it. Deleting that clause from
      // the gate turns this test green->red — it is the equality proof's sole falsifier.
      const { root, database, assignment, wt, frozen } = seedFrozenReadyNoRebase(false);
      const expectedTarget = git(root, 'rev-parse', 'HEAD');
      git(wt, 'reset', '--hard', expectedTarget); // sit on the advanced target
      writeFileSync(join(wt, 'work.txt'), 'ALTERED not the reviewed bytes\n');
      git(wt, 'add', 'work.txt');
      git(wt, 'commit', '-m', 'altered resolution (NOT the reviewed content)');
      const altered = git(wt, 'rev-parse', 'HEAD');
      expect(altered).not.toBe(frozen);
      expect(isAncestor(root, expectedTarget, altered)).toBe(true); // it IS a descendant
      const authority = issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment));

      const result = finalizeCloseOut(database, authority);

      expect(result.state).toBe('rebase-recovery-repair-required');
      expect(git(root, 'rev-parse', 'HEAD')).toBe(expectedTarget); // main did NOT advance to altered
      expect(existsSync(wt)).toBe(true); // worktree preserved
      expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
    });

    it('C1 close-out equal-effect cherry-picked descendant integrates (positive control)', () => {
      // GREEN control: HEAD is a descendant of the advanced target whose cumulative effect
      // EQUALS the reviewed frozen effect (a clean cherry-pick of the frozen work). The gate
      // must ADMIT it — guards against an over-broad/inverted equality clause.
      const { root, database, assignment, wt, frozen } = seedFrozenReadyNoRebase(false);
      const expectedTarget = git(root, 'rev-parse', 'HEAD');
      git(wt, 'reset', '--hard', expectedTarget);
      git(wt, '-c', 'core.editor=true', 'cherry-pick', frozen); // same patch, equal effect
      const authority = issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment));

      const result = finalizeCloseOut(database, authority);

      expect(result.state).toBe('closed-out');
      expect(git(root, 'rev-parse', 'HEAD')).toBe(result.integratedCommit); // main advanced to the integrated commit
      expect(existsSync(wt)).toBe(false); // worktree removed
      expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
    });

    it('close_out_worktree handler closes out an active row end-to-end (observe-path verify)', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'reviewed close-out work');
      issueDirectGitHumanIntent(database, {
        repositoryPath: assignment.worktree_path,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'close-out',
      });
      const dependencies = createPublicToolDependencies(database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const result = dependencies.closeOutWorktree({
        repository_path: root, workspace_guid: assignment.workspace_guid,
      }) as { state: string };

      expect(result.state).toBe('closed-out');
      expect(existsSync(assignment.worktree_path)).toBe(false);
    });

    it('close_out_worktree handler completes DB-only on an integrated row whose worktree is gone (R3)', () => {
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      // Crash mid-teardown: the managed worktree was removed but the row is still integrated.
      // No close-out intent is issued: on a gone worktree the hook cannot mint one (issuance itself
      // resolves the live worktree), and the DB-only fast path is owner-bound via cleanupWorkspace,
      // not intent-gated. The R3 defect is precisely that this state today yields a raw identity error.
      git(s.root, 'worktree', 'remove', '--force', s.assignment.worktree_path);
      const deps = createPublicToolDependencies(s.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const result = deps.closeOutWorktree({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      }) as { state: string; pendingPush?: { candidateCommit: string; destinationRef: string } };

      expect(result.state).toBe('closed-out'); // DB-only completion, NOT a raw identity error
      expect(result.pendingPush).toMatchObject({ candidateCommit: seeded.candidate, destinationRef: seeded.destinationRef });
      expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
      const preserved = s.database.prepare(
        "SELECT payload FROM preserved_work WHERE workspace_guid = ? AND kind = 'pending-push' AND resolved_at IS NULL",
      ).get(s.assignment.workspace_guid) as { payload: string } | undefined;
      expect(preserved).toBeDefined();
    });

    it('close_out_worktree handler on an integrated PRESENT worktree still uses the normal path', () => {
      const s = setup();
      seedIntegratedPushPending(s); // worktree PRESENT
      expect(existsSync(s.assignment.worktree_path)).toBe(true);
      issueDirectGitHumanIntent(s.database, {
        repositoryPath: s.root,
        workspaceGuid: s.assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'close-out',
      });
      const deps = createPublicToolDependencies(s.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const result = deps.closeOutWorktree({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      }) as { state: string };

      expect(result.state).toBe('closed-out');
      expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
      // Discriminator only the normal path satisfies: it consumes the close-out human intent via
      // consumeMatchingHumanIntent (git-authority.ts:609-611); the DB-only fast path never touches
      // human_intents, so a present-predicate regression (fast path firing on a live worktree) fails here.
      const intent = s.database.prepare(
        "SELECT consumed_at FROM human_intents WHERE workspace_guid = ? AND operation = 'close-out'",
      ).get(s.assignment.workspace_guid) as { consumed_at: string | null };
      expect(intent.consumed_at).not.toBeNull();
    });

    it('close_out_worktree handler preserve-and-defers a rebase conflict (never handed to the operator)', () => {
      const s = seedConflictMidRebase();
      const dependencies = createPublicToolDependencies(s.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const result = dependencies.closeOutWorktree({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      }) as { state: string; detail?: string };

      expect(result.state).toBe('rebase-paused-conflict');
      expect(existsSync(s.assignment.worktree_path)).toBe(true); // worktree preserved
      expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
    });

    it('close-out carries a push-pending obligation on the terminal cleaned row (Case A)', () => {
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));

      const result = finalizeCloseOut(s.database, authority);

      expect(result.state).toBe('closed-out');
      expect(result.pendingPush).toMatchObject({ candidateCommit: seeded.candidate, destinationRef: seeded.destinationRef });
      expect(existsSync(s.assignment.worktree_path)).toBe(false);
      const row = s.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid) as { lifecycle_status: string; disposition: string };
      expect(row.lifecycle_status).toBe('cleaned');
      expect(JSON.parse(row.disposition).phase).toBe('push-pending'); // obligation carried forward
    });

    it('close-out self-heals a push-pending obligation when the remote already has the candidate (no push)', () => {
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      // The remote already holds the candidate (e.g. a prior push whose ack was lost).
      git(s.assignment.worktree_path, 'push', 'origin', `${seeded.candidate}:${seeded.destinationRef}`);
      const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));

      const result = finalizeCloseOut(s.database, authority);

      expect(result.state).toBe('closed-out');
      expect(result.pendingPush).toBeUndefined();
      const row = s.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid) as { disposition: string | null };
      expect(row.disposition).toBeNull(); // resolved for free, no push
    });

    it('close-out snapshots a dirty worktree residual to a recovery ref, keeping it off main (Case B)', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'reviewed close-out work');
      writeFileSync(join(assignment.worktree_path, 'stray.txt'), 'forgotten WIP\n'); // untracked residual
      writeFileSync(join(assignment.worktree_path, 'README.md'), 'residual edit\n'); // tracked residual
      const authority = issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment));

      const result = finalizeCloseOut(database, authority);

      expect(result.state).toBe('closed-out');
      // Per-lifecycle content-addressed ref: refs/ironclaude/recovery/<guid>-<snapshot-oid>.
      const ref = (result.recovery as { ref: string }).ref;
      expect(ref).toMatch(new RegExp(`^refs/ironclaude/recovery/${assignment.workspace_guid}-[0-9a-f]{40}$`));
      expect((result.recovery as { residualFiles: number }).residualFiles).toBeGreaterThanOrEqual(1);
      // The recovery ref resolves in the primary checkout and its tree holds the stray.
      expect(() => git(root, 'rev-parse', '--verify', `${ref}^{commit}`)).not.toThrow();
      expect(git(root, 'ls-tree', '-r', '--name-only', ref)).toContain('stray.txt');
      // Local main does NOT contain the stray residual (only the reviewed HEAD landed).
      expect(git(root, 'ls-tree', '-r', '--name-only', 'HEAD')).not.toContain('stray.txt');
      expect(existsSync(assignment.worktree_path)).toBe(false);
      expect(database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?')
        .get(assignment.workspace_guid)).toMatchObject({ recovery_ref: ref });
    });

    it('I2 close-out frozen-head self-heals an integrated row to the integrated candidate', () => {
      // An integrated push-pending row whose worktree HEAD drifted back to the frozen
      // pre-integration commit is a reconcileFinalization-recoverable state. close-out must
      // heal (reset --hard to the integrated candidate) and complete, not error.
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      git(s.assignment.worktree_path, 'reset', '--hard', seeded.frozen); // HEAD===frozen (!==candidate)
      const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));

      const result = finalizeCloseOut(s.database, authority);

      expect(result.state).toBe('closed-out'); // healed, not a throw
      expect(result.pendingPush).toMatchObject({ candidateCommit: seeded.candidate, destinationRef: seeded.destinationRef });
      expect(existsSync(s.assignment.worktree_path)).toBe(false);
      expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'cleaned' });
    });

    it('I2 close-out keeps the existing refusal for a no-disposition head mismatch', () => {
      // Negative control: a head mismatch WITHOUT a push disposition is NOT the recoverable
      // frozen-head shape, so close-out must still refuse and preserve the worktree.
      const s = setup();
      const { frozen } = seedIntegratedNoDisposition(s);
      git(s.assignment.worktree_path, 'reset', '--hard', frozen); // HEAD===frozen, no disposition
      const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));

      expect(() => finalizeCloseOut(s.database, authority)).toThrow(/integration proof is unreachable/);
      expect(existsSync(s.assignment.worktree_path)).toBe(true); // worktree preserved
    });

    it('a matching push drains a carried obligation on a cleaned row; a non-match leaves it intact (C6)', () => {
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      const authority = issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment));
      finalizeCloseOut(s.database, authority); // row now cleaned, carrying the obligation
      const disposition = JSON.parse(seeded.disposition) as { remoteUrl: string };
      const pushedOid = git(s.root, 'rev-parse', 'HEAD'); // local main contains the candidate

      // Non-matching destinationRef: obligation left intact.
      drainCarriedObligations(s.database, s.assignment.repository_identity, disposition.remoteUrl, 'refs/heads/does-not-match', pushedOid, s.root);
      expect((s.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid) as { disposition: string | null }).disposition).not.toBeNull();

      // Matching (remoteUrl, destinationRef) + candidate contained: obligation cleared.
      drainCarriedObligations(s.database, s.assignment.repository_identity, disposition.remoteUrl, seeded.destinationRef, pushedOid, s.root);
      expect((s.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid) as { disposition: string | null }).disposition).toBeNull();
    });

    it('list_preserved_work surfaces a carried push-pending obligation (C7 preserved)', () => {
      const a = setup();
      seedIntegratedPushPending(a);
      finalizeCloseOut(a.database, issueAndVerify(a.database, a.assignment, 'close-out', reconcileEvidence(a.assignment)));
      const deps = createPublicToolDependencies(a.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const preserved = deps.listPreservedWork({ repository_path: a.root }) as Array<{ workspace_guid: string; kind: string }>;

      expect(preserved).toContainEqual(expect.objectContaining({ workspace_guid: a.assignment.workspace_guid, kind: 'pending-push' }));
    });

    it('list_preserved_work surfaces a Case-B recovery ref (C7 preserved)', () => {
      const { root, database, assignment } = setup(false);
      git(assignment.worktree_path, 'commit', '-m', 'reviewed close-out work');
      writeFileSync(join(assignment.worktree_path, 'stray.txt'), 'forgotten WIP\n');
      finalizeCloseOut(database, issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment)));
      const deps = createPublicToolDependencies(database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const preserved = deps.listPreservedWork({ repository_path: root }) as Array<{ workspace_guid: string; kind: string; ref?: string }>;

      expect(preserved).toContainEqual(expect.objectContaining({ workspace_guid: assignment.workspace_guid, kind: 'recovery' }));
    });

    it('C2 preserved-work survives same-session row reuse (pending-push)', () => {
      // (A) reuseTerminalAssignment NULLs the cleaned row's disposition on same-GUID reuse.
      // The carried obligation must survive in the additive preserved_work table.
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      finalizeCloseOut(s.database, issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment)));
      // Same-session reuse of the now-cleaned GUID (worktree gone) wipes the row disposition.
      new WorkspaceService(s.database).ensureSessionWorktree({ repositoryPath: s.root, ownerSessionId: OWNER });
      const deps = createPublicToolDependencies(s.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const preserved = deps.listPreservedWork({ repository_path: s.root }) as Array<{ workspace_guid: string; kind: string; destinationRef?: string }>;

      expect(preserved).toContainEqual(expect.objectContaining({
        workspace_guid: s.assignment.workspace_guid, kind: 'pending-push', destinationRef: seeded.destinationRef,
      }));
    });

    it('C2 preserved-work keeps per-lifecycle recovery refs across two dirty close-outs on one GUID', () => {
      // (B) A flat refs/ironclaude/recovery/<guid> clobbers a prior snapshot on reuse; the
      // per-lifecycle content-addressed <guid>-<oid> ref must keep BOTH residuals listed.
      const { root, database, assignment } = setup(false);
      writeFileSync(join(assignment.worktree_path, 'work.txt'), 'first reviewed\n');
      git(assignment.worktree_path, 'add', 'work.txt');
      git(assignment.worktree_path, 'commit', '-m', 'reviewed close-out work 1');
      writeFileSync(join(assignment.worktree_path, 'stray1.txt'), 'residual one\n'); // dirty residual
      const r1 = finalizeCloseOut(database, issueAndVerify(database, assignment, 'close-out', reconcileEvidence(assignment)));
      const ref1 = (r1.recovery as { ref: string }).ref;

      const reused = new WorkspaceService(database).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
      writeFileSync(join(reused.worktree_path, 'work.txt'), 'second reviewed\n');
      git(reused.worktree_path, 'add', 'work.txt');
      git(reused.worktree_path, 'commit', '-m', 'reviewed close-out work 2');
      writeFileSync(join(reused.worktree_path, 'stray2.txt'), 'residual two\n'); // different residual
      const r2 = finalizeCloseOut(database, issueAndVerify(database, reused, 'close-out', reconcileEvidence(reused)));
      const ref2 = (r2.recovery as { ref: string }).ref;

      expect(ref1).not.toBe(ref2); // distinct per-lifecycle refs (RED: flat ref makes them equal)
      const deps = createPublicToolDependencies(database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });
      const refs = (deps.listPreservedWork({ repository_path: root }) as Array<{ kind: string; ref?: string }>)
        .filter((p) => p.kind === 'recovery').map((p) => p.ref);
      expect(refs).toContain(ref1);
      expect(refs).toContain(ref2);
    });

    it('C2 preserved-work lists an abandoned row recovery_ref with no table row (UNION)', () => {
      // (C) I-1: listPreservedWork must UNION the table with the existing assignments query,
      // never REPLACE it — a legacy abandoned row carrying recovery_ref (no table entry) must stay listed.
      const { root, database, assignment } = setup(false);
      const legacyRef = `refs/ironclaude/recovery/${assignment.workspace_guid}-legacy`;
      git(root, 'update-ref', legacyRef, git(root, 'rev-parse', 'HEAD'));
      database.prepare("UPDATE assignments SET lifecycle_status='abandoned', recovery_ref=? WHERE workspace_guid=?")
        .run(legacyRef, assignment.workspace_guid);
      const deps = createPublicToolDependencies(database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });

      const preserved = deps.listPreservedWork({ repository_path: root }) as Array<{ workspace_guid: string; kind: string; ref?: string }>;

      expect(preserved).toContainEqual(expect.objectContaining({
        workspace_guid: assignment.workspace_guid, kind: 'recovery', ref: legacyRef,
      }));
    });

    it('C2 preserved-work drains a reused-row pending-push obligation via the table (I-2)', () => {
      // (D) After reuse wipes the row disposition, a matching /push+drain must resolve the
      // durable table row too, or the obligation is surfaced forever.
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      finalizeCloseOut(s.database, issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment)));
      const pushedOid = git(s.root, 'rev-parse', 'HEAD');
      new WorkspaceService(s.database).ensureSessionWorktree({ repositoryPath: s.root, ownerSessionId: OWNER });
      const deps = createPublicToolDependencies(s.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });
      const isPendingPush = (p: { workspace_guid: string; kind: string }) =>
        p.workspace_guid === s.assignment.workspace_guid && p.kind === 'pending-push';

      const before = deps.listPreservedWork({ repository_path: s.root }) as Array<{ workspace_guid: string; kind: string }>;
      expect(before.filter(isPendingPush)).toHaveLength(1); // preserved across reuse (RED today: 0)

      const disposition = JSON.parse(seeded.disposition) as { remoteUrl: string };
      drainCarriedObligations(s.database, s.assignment.repository_identity, disposition.remoteUrl, seeded.destinationRef, pushedOid, s.root);

      const after = deps.listPreservedWork({ repository_path: s.root }) as Array<{ workspace_guid: string; kind: string }>;
      expect(after.filter(isPendingPush)).toHaveLength(0); // drained via the table (I-2)
    });

    it('C2 preserved-work does not carry a push-succeeded disposition (obs 2)', () => {
      // (E) A push-succeeded disposition is already published; close-out must clear it, never
      // carry it as a live obligation or insert a table row.
      const s = setup();
      const seeded = seedIntegratedPushPending(s);
      const succeeded = JSON.stringify({ ...JSON.parse(seeded.disposition), phase: 'push-succeeded' });
      s.database.prepare('UPDATE assignments SET disposition=? WHERE workspace_guid=?')
        .run(succeeded, s.assignment.workspace_guid);

      const result = finalizeCloseOut(s.database, issueAndVerify(s.database, s.assignment, 'close-out', reconcileEvidence(s.assignment)));

      expect(result.pendingPush).toBeUndefined(); // not carried
      expect((s.database.prepare('SELECT disposition FROM assignments WHERE workspace_guid=?')
        .get(s.assignment.workspace_guid) as { disposition: string | null }).disposition).toBeNull(); // cleared
      const deps = createPublicToolDependencies(s.database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });
      const preserved = deps.listPreservedWork({ repository_path: s.root }) as Array<{ workspace_guid: string; kind: string }>;
      expect(preserved.filter((p) => p.workspace_guid === s.assignment.workspace_guid && p.kind === 'pending-push')).toHaveLength(0); // no insert
    });

    it('pushPendingSummary reports only an outstanding push-pending/push-failed obligation', () => {
      const base = {
        candidateCommit: 'a'.repeat(40), frozenCommit: 'b'.repeat(40),
        remoteName: 'origin', remoteUrl: '/tmp/remote', destinationRef: 'refs/heads/main', expectedRemoteOldOid: null,
      };
      expect(pushPendingSummary(JSON.stringify({ ...base, phase: 'push-pending' })))
        .toMatchObject({ candidateCommit: base.candidateCommit, destinationRef: base.destinationRef });
      expect(pushPendingSummary(JSON.stringify({ ...base, phase: 'push-failed' })))
        .toMatchObject({ candidateCommit: base.candidateCommit });
      expect(pushPendingSummary(JSON.stringify({ ...base, phase: 'push-succeeded' }))).toBeUndefined(); // already published
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

    // ACTIVE path: a prior commit-and-push set an integration-pending disposition
    // then threw at the dirty gate before the active->ready transition, leaving an
    // ACTIVE row carrying it. finalizeLocalCommit's markIntegrated converts that to
    // push-pending, so the active branch must report 'integrated-local' and PRESERVE
    // the obligation rather than recycling it away (never-lose-work).
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

    it('commits and stays through an ACTIVE /commit, leaving the integration-pending marker untouched', () => {
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

      expect(result.state).toBe('committed');
      expect(existsSync(s.assignment.worktree_path)).toBe(true);
      expect(s.database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({
          lifecycle_status: 'active', disposition: expect.stringContaining('integration-pending'),
        });
    });
  });

  describe('land_resolved_conflict (confirm-resolution)', () => {
    const candidateRef = (guid: string) => `refs/ironclaude/finalization/${guid}/candidate`;

    // Reaches a ready_for_integration paused-conflict row via finalizeCommanderLocalCommit
    // (a LOCAL commit — sets NO push disposition, so a clean confirm-resolution finalize
    // reports 'reconciled', not 'integrated-local'), then hand-resolves + `rebase --continue`
    // so HEAD is the resolved candidate, and MECHANICALLY seeds candidateRef(guid) at that
    // HEAD (the apply tool is a later task). setup(true) gives a remote so "no origin ref
    // moved" is a real proof, not vacuous. Mirrors the repair-reconcile flow at ~:871.
    function seedResolvedCandidate() {
      const s = setup(true);
      const originMain = git(s.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
      git(s.root, 'checkout', '-b', 'target-change');
      writeFileSync(join(s.root, 'README.md'), 'target\n');
      git(s.root, 'add', 'README.md');
      git(s.root, 'commit', '-m', 'target change');
      git(s.root, 'checkout', 'main');
      git(s.root, 'merge', '--ff-only', 'target-change');
      writeFileSync(join(s.assignment.worktree_path, 'README.md'), 'source\n');
      git(s.assignment.worktree_path, 'add', 'README.md');
      expect(() => finalizeCommanderLocalCommit(s.database, commanderInput(s.root, s.assignment, 'conflict'))).toThrow();
      expect(s.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
        .get(s.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
      // durable frozen ref must exist for the isRepair channel (written by finalizeLocalCommit
      // before the rebase, so the conflict-paused throw leaves it in place).
      expect(() => git(s.assignment.worktree_path, 'rev-parse', '--verify',
        `refs/ironclaude/finalization/${s.assignment.workspace_guid}/frozen^{commit}`)).not.toThrow();
      writeFileSync(join(s.assignment.worktree_path, 'README.md'), 'reviewed repair\n');
      git(s.assignment.worktree_path, 'add', 'README.md');
      git(s.assignment.worktree_path, '-c', 'core.editor=true', 'rebase', '--continue');
      const candidate = git(s.assignment.worktree_path, 'rev-parse', 'HEAD');
      git(s.assignment.worktree_path, 'update-ref', candidateRef(s.assignment.workspace_guid), candidate);
      return { ...s, candidate, originMain };
    }

    function deps(database: ReturnType<typeof initDb>) {
      return createPublicToolDependencies(database, {
        client: 'codex', sessionId: OWNER, invocationThreadId: OWNER, source: 'codex_meta',
      });
    }

    function mintConfirmResolution(s: ReturnType<typeof seedResolvedCandidate>) {
      return issueDirectGitHumanIntent(s.database, {
        repositoryPath: s.root,
        workspaceGuid: s.assignment.workspace_guid,
        providerRootSessionId: OWNER,
        humanChannel: 'codex-user-prompt',
        operation: 'confirm-resolution',
      });
    }

    it('HAPPY: lands the confirmed resolution into local main, keeps the worktree, and moves NO origin ref', () => {
      const s = seedResolvedCandidate();
      mintConfirmResolution(s);

      const result = deps(s.database).landResolvedConflict({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      }) as { state: string; integratedCommit?: string };

      expect(result.state).toBe('reconciled');
      expect(result.integratedCommit).toBe(s.candidate);
      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(s.candidate); // local main advanced to the candidate
      expect(existsSync(s.assignment.worktree_path)).toBe(true); // reconcile-style: worktree kept
      const originMainAfter = git(s.root, 'ls-remote', '--refs', 'origin', 'refs/heads/main').split(/\s+/)[0];
      expect(originMainAfter).toBe(s.originMain); // NEVER pushes
    });

    it('S4: refuses without a confirm-resolution intent; nothing lands', () => {
      const s = seedResolvedCandidate();
      const mainBefore = git(s.root, 'rev-parse', 'HEAD');

      expect(() => deps(s.database).landResolvedConflict({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      })).toThrow(/matching human intent/);

      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(mainBefore);
    });

    it('S1 content-pin: a registered candidate that differs from the authorized HEAD is refused; nothing lands', () => {
      const s = seedResolvedCandidate();
      const mainBefore = git(s.root, 'rev-parse', 'HEAD');
      // point the registered candidate at a DIFFERENT (valid) commit than live HEAD
      git(s.assignment.worktree_path, 'update-ref', candidateRef(s.assignment.workspace_guid), mainBefore);
      mintConfirmResolution(s);

      expect(() => deps(s.database).landResolvedConflict({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      })).toThrow(/No confirmed resolution candidate matches the authorized HEAD/);

      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(mainBefore);
    });

    it('S1 TOCTOU tool-level: minting then moving HEAD yields no matching intent; nothing lands', () => {
      const s = seedResolvedCandidate();
      const mainBefore = git(s.root, 'rev-parse', 'HEAD');
      mintConfirmResolution(s); // bound to HEAD = candidate
      // move HEAD AFTER the intent was minted
      writeFileSync(join(s.assignment.worktree_path, 'extra.txt'), 'extra\n');
      git(s.assignment.worktree_path, 'add', 'extra.txt');
      git(s.assignment.worktree_path, 'commit', '-m', 'moved after mint');

      // verifyDirectGitAuthority observes evidence LIVE at consume, so the moved HEAD
      // yields no intent whose evidence matches -> the matching-human-intent refusal.
      expect(() => deps(s.database).landResolvedConflict({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      })).toThrow(/matching human intent/);

      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(mainBefore);
    });

    it('S1 TOCTOU direct-level: a pre-verified authority whose HEAD then moves is refused by finalizeConfirmResolution', () => {
      const s = seedResolvedCandidate();
      const mainBefore = git(s.root, 'rev-parse', 'HEAD');
      const authority = issueAndVerify(s.database, s.assignment, 'confirm-resolution', reconcileEvidence(s.assignment));
      // move HEAD AFTER the authority was verified (its evidence.headOid is pinned to the candidate)
      writeFileSync(join(s.assignment.worktree_path, 'extra.txt'), 'extra\n');
      git(s.assignment.worktree_path, 'add', 'extra.txt');
      git(s.assignment.worktree_path, 'commit', '-m', 'moved after verify');

      expect(() => finalizeConfirmResolution(s.database, authority))
        .toThrow(/Resolution HEAD changed since \/confirm-resolution/);

      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(mainBefore);
    });

    it('S3 cross-verb: a reconcile intent cannot be consumed by land_resolved_conflict; nothing lands', () => {
      const s = seedResolvedCandidate();
      const mainBefore = git(s.root, 'rev-parse', 'HEAD');
      issueDirectGitHumanIntent(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid,
        providerRootSessionId: OWNER, humanChannel: 'codex-user-prompt', operation: 'reconcile',
      });

      expect(() => deps(s.database).landResolvedConflict({
        repository_path: s.root, workspace_guid: s.assignment.workspace_guid,
      })).toThrow(/matching human intent/);

      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(mainBefore);
    });

    it('S3 cross-verb: a confirm-resolution authority is rejected by finalizeReconcile operation guard', () => {
      const s = seedResolvedCandidate();
      const authority = issueAndVerify(s.database, s.assignment, 'confirm-resolution', reconcileEvidence(s.assignment));

      expect(() => finalizeReconcile(s.database, authority))
        .toThrow(/Reconcile finalization requires reconcile authority/);
    });
  });

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

  it('preserves a push-pending disposition through a Commander finalize instead of disposing', () => {
    const { root, database, assignment } = setup(false);
    const head = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    database.prepare('UPDATE assignments SET disposition = ? WHERE workspace_guid = ?').run(
      JSON.stringify({
        phase: 'integration-pending', frozenCommit: head, remoteName: 'origin',
        remoteUrl: 'file:///unused-in-commander-guard', destinationRef: 'refs/heads/main',
        expectedRemoteOldOid: null,
      }),
      assignment.workspace_guid,
    );

    const result = finalizeCommanderLocalCommit(database, commanderInput(root, assignment, 'commander work'));

    expect(result.state).toBe('integrated-local');
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending') });
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
    const input = commanderInput(root, assignment, 'later source work');
    writeFileSync(join(root, 'target.txt'), 'advanced target\n');
    git(root, 'add', 'target.txt');
    git(root, 'commit', '-m', 'advance integration target');
    const advancedTarget = git(root, 'rev-parse', 'HEAD');
    let reviewedEffect = '';
    let rebasedEffect = '';

    expect(finalizeCommanderLocalCommit(database, input, {
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
    const input = commanderInput(changed.root, changed.assignment, 'reviewed work');
    writeFileSync(join(changed.root, 'target.txt'), 'advanced target\n');
    git(changed.root, 'add', 'target.txt');
    git(changed.root, 'commit', '-m', 'advance integration target');
    const targetBefore = git(changed.root, 'rev-parse', 'HEAD');

    expect(() => finalizeCommanderLocalCommit(changed.database, input, {
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
    const input = commanderInput(changed.root, changed.assignment, 'reviewed exact commit');
    const targetBefore = git(changed.root, 'rev-parse', 'HEAD');

    expect(() => finalizeCommanderLocalCommit(changed.database, input, {
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
    const input = commanderInput(crashed.root, crashed.assignment, 'crash boundary');

    expect(() => finalizeCommanderLocalCommit(crashed.database, input, {
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
      const input = commanderInput(state.root, state.assignment, `post-CAS non-overlap ${kind}`);
      expect(() => finalizeCommanderLocalCommit(state.database, input, {
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
      const input = commanderInput(state.root, state.assignment, `post-CAS overlap ${kind}`);
      expect(() => finalizeCommanderLocalCommit(state.database, input, {
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
    const input = commanderInput(state.root, state.assignment, 'off-ref post-CAS crash');
    expect(() => finalizeCommanderLocalCommit(state.database, input, {
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
    const beforeInput = commanderInput(before.root, before.assignment, 'lock lost before mutation');
    const beforeTarget = git(before.root, 'rev-parse', 'HEAD');
    expect(() => finalizeCommanderLocalCommit(before.database, beforeInput, {
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
    const movedAfterInput = commanderInput(movedAfter.root, movedAfter.assignment, 'target moved after fast-forward');
    expect(() => finalizeCommanderLocalCommit(movedAfter.database, movedAfterInput, {
      afterCheckedFastForwardBeforeRecord: () => git(movedAfter.root, 'commit', '--allow-empty', '-m', 'late target move'),
    })).toThrow('inconsistent after checked fast-forward');
    expect(movedAfter.database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?')
      .get(movedAfter.assignment.workspace_guid)).toBeUndefined();
    expect(existsSync(movedAfter.assignment.worktree_path)).toBe(true);
    const movedAfterCandidate = git(movedAfter.root, 'rev-parse', `refs/ironclaude/finalization/${movedAfter.assignment.workspace_guid}/candidate`);
    expect(reconcileFinalization(movedAfter.database, {
      repositoryPath: movedAfter.root, workspaceGuid: movedAfter.assignment.workspace_guid, providerRootSessionId: OWNER,
    })).toMatchObject({ state: 'cleaned', integratedCommit: movedAfterCandidate });

    const casMoved = setup(false);
    const casInput = commanderInput(casMoved.root, casMoved.assignment, 'CAS target race');
    expect(() => finalizeCommanderLocalCommit(casMoved.database, casInput, {
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
    const conflictInput = commanderInput(conflict.root, conflict.assignment, 'conflict');
    expect(() => finalizeCommanderLocalCommit(conflict.database, conflictInput)).toThrow();
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
    const movedInput = commanderInput(moved.root, moved.assignment, 'stale target');
    const original = git(moved.root, 'rev-parse', 'HEAD');
    expect(() => finalizeCommanderLocalCommit(moved.database, movedInput, {
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
    const lockedInput = commanderInput(locked.root, locked.assignment, 'serialized');
    expect(() => finalizeCommanderLocalCommit(locked.database, lockedInput)).toThrow('already held');
    expect(locked.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(locked.assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'ready_for_integration' });
    releaseIntegrationLock(locked.database, locked.assignment.repository_identity, locked.assignment.workspace_guid);
    expect(reconcileFinalization(locked.database, {
      repositoryPath: locked.root, workspaceGuid: locked.assignment.workspace_guid, providerRootSessionId: OWNER,
    }).state).toBe('cleaned');

    const nonDescendant = setup(false);
    const nonDescendantInput = commanderInput(nonDescendant.root, nonDescendant.assignment, 'negative proof');
    git(nonDescendant.root, 'commit', '--allow-empty', '-m', 'advanced target');
    expect(() => finalizeCommanderLocalCommit(nonDescendant.database, nonDescendantInput, {
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

  it('commit-and-stay ignores an untracked stray file, staying active', () => {
    const { database, assignment } = setup(false);
    const authority = issueAndVerify(database, assignment, 'commit', commitEvidence(assignment));
    writeFileSync(join(assignment.worktree_path, 'stray.txt'), 'untracked stray file\n');

    const result = finalizeDirectAuthority(database, authority, 'dirty tree at freeze');

    expect(result.state).toBe('committed');
    expect(existsSync(join(assignment.worktree_path, 'stray.txt'))).toBe(true);
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
      .toBe('committed');
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
    expect(result.conflicts).toBeUndefined(); // HC4: the proven class-1 auto-integrate lane never grows a conflicts surface
    expect(result.integratedCommit).toBe(git(root, 'rev-parse', 'HEAD'));
    expect(readFileSync(join(root, 'work.txt'), 'utf8')).toBe('approved\n');
    expect(readFileSync(join(root, 'target.txt'), 'utf8')).toBe('advanced target\n');
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');
    expect(existsSync(wt)).toBe(true);
  });

  // Seeds a paused conflicted integration rebase of a chosen KIND, mirroring seedConflictMidRebase:
  // the target-change branch commits a target-side change, ff-merges into main; the worktree stages
  // the worktree-side change; finalizeCommanderLocalCommit rebases the worktree commit onto the
  // advanced target, conflicts, throws, and leaves the paused conflicted rebase for classification.
  function seedRebaseConflictKind(
    targetSide: (root: string) => void,
    worktreeSide: (wt: string) => void,
  ): ReturnType<typeof setup> {
    const s = setup(false);
    git(s.root, 'checkout', '-b', 'target-change');
    targetSide(s.root);
    git(s.root, 'checkout', 'main');
    git(s.root, 'merge', '--ff-only', 'target-change');
    worktreeSide(s.assignment.worktree_path);
    expect(() => finalizeCommanderLocalCommit(s.database, commanderInput(s.root, s.assignment, 'conflict'))).toThrow();
    return s;
  }

  it('classifyRebaseConflicts classifies an overlapping edit conflict as overlap', () => {
    const s = seedConflictMidRebase(); // README.md modified on both sides -> UU
    const conflicts = classifyRebaseConflicts(s.assignment.worktree_path);
    expect(conflicts).toEqual([
      expect.objectContaining({ path: 'README.md', conflictClass: 'overlap' }),
    ]);
    expect(conflicts[0].summary.length).toBeGreaterThan(0);
  });

  it('classifyRebaseConflicts classifies a delete-modify conflict without crashing', () => {
    // Target modifies README.md; the worktree DELETES it -> a merge stage is absent. An unguarded
    // `git show :3:README.md` throws here, which is exactly what the stageLines try/catch prevents.
    const s = seedRebaseConflictKind(
      (root) => { writeFileSync(join(root, 'README.md'), 'target\n'); git(root, 'add', 'README.md'); git(root, 'commit', '-m', 'target modify'); },
      (wt) => { git(wt, 'rm', 'README.md'); },
    );
    const conflicts = classifyRebaseConflicts(s.assignment.worktree_path);
    expect(conflicts).toEqual([
      expect.objectContaining({ path: 'README.md', conflictClass: 'delete-modify' }),
    ]);
    expect(conflicts[0].summary.length).toBeGreaterThan(0);
  });

  it('classifyRebaseConflicts classifies an add-add conflict as add-add', () => {
    const s = seedRebaseConflictKind(
      (root) => { writeFileSync(join(root, 'NEW.md'), 'target\n'); git(root, 'add', 'NEW.md'); git(root, 'commit', '-m', 'target add'); },
      (wt) => { writeFileSync(join(wt, 'NEW.md'), 'source\n'); git(wt, 'add', 'NEW.md'); },
    );
    expect(classifyRebaseConflicts(s.assignment.worktree_path)).toEqual([
      expect.objectContaining({ path: 'NEW.md', conflictClass: 'add-add' }),
    ]);
  });

  it('classifyRebaseConflicts classifies a differing-binary conflict as binary', () => {
    const s = seedRebaseConflictKind(
      (root) => { writeFileSync(join(root, 'NEW.bin'), Buffer.from([0, 1, 2, 3, 0, 255])); git(root, 'add', 'NEW.bin'); git(root, 'commit', '-m', 'target bin'); },
      (wt) => { writeFileSync(join(wt, 'NEW.bin'), Buffer.from([255, 254, 0, 9, 9, 9])); git(wt, 'add', 'NEW.bin'); },
    );
    expect(classifyRebaseConflicts(s.assignment.worktree_path)).toEqual([
      expect.objectContaining({ path: 'NEW.bin', conflictClass: 'binary' }),
    ]);
  });

  it('classifyRebaseConflicts surfaces a rename-modify conflict with a legal class', () => {
    const s = seedRebaseConflictKind(
      (root) => { writeFileSync(join(root, 'README.md'), 'target\n'); git(root, 'add', 'README.md'); git(root, 'commit', '-m', 'target modify'); },
      (wt) => { git(wt, 'mv', 'README.md', 'RENAMED.md'); writeFileSync(join(wt, 'RENAMED.md'), 'renamed and edited\n'); git(wt, 'add', 'RENAMED.md'); },
    );
    const conflicts = classifyRebaseConflicts(s.assignment.worktree_path);
    expect(conflicts.length).toBeGreaterThanOrEqual(1);
    for (const c of conflicts) {
      expect(['overlap', 'add-add', 'delete-modify', 'binary', 'other']).toContain(c.conflictClass);
      expect(c.summary.length).toBeGreaterThan(0);
    }
  });

  it('classifyRebaseConflicts returns an empty list for a clean worktree', () => {
    const { assignment } = setup(false);
    expect(classifyRebaseConflicts(assignment.worktree_path)).toEqual([]);
  });

  it('managed rebase recovery: unresolved conflicts STOP and do not advance the target (case B)', () => {
    const s = seedConflictMidRebase();
    const targetBefore = git(s.root, 'rev-parse', 'HEAD');
    expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toContain('README.md');

    // M7b: continue now RETURNS a structured rebase-paused-conflict surface (no throw), still
    // without advancing the target. The 'unresolved conflicts remain' detail proves the op STOPPED
    // BEFORE ever running `rebase --continue` — the re-conflict-during-continue path carries a
    // DIFFERENT detail ('continuing re-conflicted'), so asserting this exact substring (not merely
    // truthy) falsifies the no-auto-resolve guard: deleting it routes to the re-conflict return and
    // this assertion fails.
    const result = reconcileFinalization(s.database, {
      repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'continue',
    });
    expect(result.state).toBe('rebase-paused-conflict');
    expect(result.conflicts).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: 'README.md', conflictClass: 'overlap' }),
    ]));
    expect(result.detail).toContain('unresolved conflicts remain');

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

  it('managed no-rebase recovery: the reconcile validator accepts all six modes and rejects an unknown one (case e, bounded widening)', () => {
    const { database } = setup(false);
    const deps = createInternalCommandDependencies(database);
    for (const mode of ['continue', 'abort', 'rerebase', 'restore_frozen', 'status', 'reopen_for_edit'] as const) {
      // Reaches past validation and fails on the missing guid/owner pairing, proving
      // the value itself was accepted by optionalRebaseRecovery.
      expect(() => deps.reconcile({ repository_path: '/x', rebase_recovery: mode }))
        .toThrow('requires workspace_guid and owner_session_id');
    }
    expect(() => deps.reconcile({ repository_path: '/x', rebase_recovery: 'bogus' }))
      .toThrow('rebase_recovery must be');
  });

  it('reopen_for_edit: a clean frozen-no-rebase ready row returns to active, worktree lands at frozen, refs+lock cleared', () => {
    const { root, database, assignment, wt, frozen } = seedFrozenReadyNoRebase(false);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, frozen);
    acquireIntegrationLock(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      targetRef: 'refs/heads/main',
      expectedTarget: git(root, 'rev-parse', 'HEAD'),
    });
    // Pre-assert both the candidate ref and the lock actually exist before recovery.
    expect(git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toBe(frozen);
    expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
      .get(assignment.repository_identity, assignment.workspace_guid)).toBeDefined();

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    });

    expect(result.state).toBe('finalization-reopened-for-edit');
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'active' });
    expect(() => git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`)).toThrow();
    expect(() => git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toThrow();
    expect(git(wt, 'rev-parse', 'HEAD')).toBe(frozen);
    expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
      .get(assignment.repository_identity, assignment.workspace_guid)).toBeUndefined();
  });

  it('reopen_for_edit: dirty tracked+untracked bytes are snapshotted to a recovery ref before the worktree lands at frozen', () => {
    const { root, database, assignment, wt, frozen } = seedFrozenReadyNoRebase(false);
    writeFileSync(join(wt, 'untracked-note.txt'), 'dirty untracked\n');
    writeFileSync(join(wt, 'work.txt'), 'dirty tracked edit\n');

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    });

    expect(result.state).toBe('finalization-reopened-for-edit');
    expect(result.recovery).toBeDefined();
    expect(() => git(root, 'rev-parse', '--verify', `${result.recovery!.ref}^{commit}`)).not.toThrow();
    expect(git(wt, 'rev-parse', 'HEAD')).toBe(frozen);
    const preserved = database.prepare(
      "SELECT payload FROM preserved_work WHERE workspace_guid = ? AND kind = 'recovery' AND resolved_at IS NULL",
    ).get(assignment.workspace_guid) as { payload: string } | undefined;
    expect(preserved).toBeDefined();
  });

  it('reopen_for_edit: refuses a paused rebase, preserving the worktree', () => {
    const conflict = seedConflictMidRebase();
    expect(() => reconcileFinalization(conflict.database, {
      repositoryPath: conflict.root, workspaceGuid: conflict.assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    })).toThrow('reopen_for_edit requires a frozen, no-rebase ready row');
    expect(existsSync(conflict.assignment.worktree_path)).toBe(true);
    expect(conflict.database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(conflict.assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
  });

  it('reopen_for_edit: refuses a non-ready (active) row', () => {
    const { root, database, assignment } = setup(false);
    expect(() => reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    })).toThrow('No ready finalization is available');
  });

  it('reopen_for_edit: a CLEAN committed HEAD diverged above frozen is preserved to a recovery ref, not discarded', () => {
    const { root, database, assignment, wt, frozen } = seedFrozenReadyNoRebase(false);
    // Simulate a 'continue' rebase resolution that then failed the equality proof: the
    // worktree HEAD is a committed commit ABOVE frozen, and the tree is CLEAN.
    writeFileSync(join(wt, 'extra.txt'), 'resolved above frozen\n');
    git(wt, 'add', 'extra.txt');
    git(wt, 'commit', '-m', 'resolved conflict above frozen');
    const diverged = git(wt, 'rev-parse', 'HEAD');
    expect(diverged).not.toBe(frozen);
    expect(git(wt, 'status', '--porcelain')).toBe('');

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    });

    expect(result.state).toBe('finalization-reopened-for-edit');
    expect(result.recovery).toBeDefined();
    expect(git(root, 'rev-parse', '--verify', `${result.recovery!.ref}^{commit}`)).toBe(diverged);
    expect(git(wt, 'rev-parse', 'HEAD')).toBe(frozen);
    const preserved = database.prepare(
      "SELECT payload FROM preserved_work WHERE workspace_guid = ? AND kind = 'recovery' AND resolved_at IS NULL",
    ).get(assignment.workspace_guid) as { payload: string } | undefined;
    expect(preserved).toBeDefined();
  });

  it('reopen_for_edit: refuses an interrupted-CAS row where the candidate already landed on the target, preserving proof', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'landed work');
    const landed = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const oldTarget = git(root, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, landed);
    acquireIntegrationLock(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: oldTarget,
    });
    git(root, 'merge', '--ff-only', landed);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, landed);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);

    expect(() => reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    })).toThrow('integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');

    expect(git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toBe(landed);
    expect(git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`)).toBe(landed);
    expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
      .get(assignment.repository_identity, assignment.workspace_guid)).toBeDefined();
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'ready_for_integration' });
  });

  it('reopen_for_edit: an unresolvable integration target does not throw a raw git error; reopen proceeds', () => {
    const { root, database, assignment, frozen } = seedFrozenReadyNoRebase(false);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, frozen);
    acquireIntegrationLock(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: git(root, 'rev-parse', 'HEAD'),
    });
    database.prepare("UPDATE assignments SET integration_target = 'nonexistent-target-branch' WHERE workspace_guid = ?").run(assignment.workspace_guid);
    expect(reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' }).state).toBe('finalization-reopened-for-edit');
    expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(assignment.repository_identity, assignment.workspace_guid)).toBeUndefined();
  });

  it('reopen_for_edit: a candidate that equals the target but has NO held lock is not an interrupted-CAS row; reopen proceeds', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'landed');
    const landed = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, landed);
    git(root, 'merge', '--ff-only', landed);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, landed);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid);
    expect(reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' }).state).toBe('finalization-reopened-for-edit');
    expect(() => git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toThrow();
  });

  it('reopen_for_edit: nulls a carried integration-pending disposition on reopen', () => {
    const { root, database, assignment, frozen } = seedFrozenReadyNoRebase(false);
    database.prepare('UPDATE assignments SET disposition = ? WHERE workspace_guid = ?').run(JSON.stringify({
      phase: 'integration-pending', frozenCommit: frozen, remoteName: 'origin',
      remoteUrl: 'file:///unused-remote', destinationRef: `refs/heads/${assignment.branch}`, expectedRemoteOldOid: null,
    }), assignment.workspace_guid);
    expect(database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
      .toMatchObject({ disposition: expect.stringContaining('integration-pending') });

    const result = reconcileFinalization(database, {
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
      rebaseRecovery: 'reopen_for_edit',
    });

    expect(result.state).toBe('finalization-reopened-for-edit');
    expect(database.prepare('SELECT disposition, lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ disposition: null, lifecycle_status: 'active' });
  });

  it('reopen_for_edit / reconcile: a landed candidate whose target has advanced completes via reconcile', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'reviewed work');
    const landed = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const oldTarget = git(root, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, landed);
    acquireIntegrationLock(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: oldTarget,
    });
    git(root, 'merge', '--ff-only', landed);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, landed);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid);
    // advance the target PAST the landed candidate with an unrelated commit
    git(root, 'commit', '--allow-empty', '-m', 'unrelated advance');
    const result = reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER });
    expect(result.state).toBe('cleaned');
    expect(result.integratedCommit).toBe(landed);
  });

  it('reopen_for_edit: refuses a landed candidate whose target has advanced (ancestry), preserving proof', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'reviewed work');
    const landed = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const oldTarget = git(root, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, landed);
    acquireIntegrationLock(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: oldTarget,
    });
    git(root, 'merge', '--ff-only', landed);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, landed);
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid);
    git(root, 'commit', '--allow-empty', '-m', 'unrelated advance');
    expect(() => reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' }))
      .toThrow('integration already landed on the target');
    expect(git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toBe(landed);
    expect(git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`)).toBe(landed);
    expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(assignment.repository_identity, assignment.workspace_guid)).toBeDefined();
  });

  it('reopen_for_edit: a stale leftover candidate (ancestor of an advanced expected_target) proceeds to a cleaning reopen', () => {
    const { root, database, assignment } = setup(false);
    git(assignment.worktree_path, 'commit', '-m', 'prior lifecycle work');
    const cOld = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    git(root, 'merge', '--ff-only', cOld);
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, cOld);
    git(root, 'commit', '--allow-empty', '-m', 'target advanced past C_old');
    const advancedTarget = git(root, 'rev-parse', 'HEAD');
    git(assignment.worktree_path, 'commit', '--allow-empty', '-m', 'current lifecycle reviewed work');
    const frozenF = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozenF);
    acquireIntegrationLock(database, {
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      targetRef: 'refs/heads/main', expectedTarget: advancedTarget,
    });
    database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid);

    const result = reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' });
    expect(result.state).toBe('finalization-reopened-for-edit');
    expect(() => git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toThrow();
    expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(assignment.repository_identity, assignment.workspace_guid)).toBeUndefined();
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
      const input = commanderInput(root, assignment, 'stale base commit');

      expect(() => finalizeCommanderLocalCommit(database, input))
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

  describe('resolve_conflict_hunk', () => {
    const conflictCandidateRef = (guid: string) => `refs/ironclaude/finalization/${guid}/candidate`;

    // Seeds a single-commit, single-path OVERLAP conflict with distinct, byte-checkable
    // content on each side, so keep-mine/take-target/prose can assert exact resulting bytes.
    function seedOverlapConflict() {
      return seedRebaseConflictKind(
        (root) => {
          writeFileSync(join(root, 'README.md'), 'target-content\n');
          git(root, 'add', 'README.md');
          git(root, 'commit', '-m', 'target modify');
        },
        (wt) => {
          writeFileSync(join(wt, 'README.md'), 'reviewed-content\n');
          git(wt, 'add', 'README.md');
        },
      );
    }

    // Seeds a single-commit conflict touching TWO paths at once (A.md and B.md both
    // conflict in the same replayed commit), so resolving one path leaves the other
    // unresolved and the rebase paused — exactly the S6 partial-resolve shape.
    function seedTwoFileConflict() {
      return seedRebaseConflictKind(
        (root) => {
          writeFileSync(join(root, 'A.md'), 'target-A\n');
          git(root, 'add', 'A.md');
          writeFileSync(join(root, 'B.md'), 'target-B\n');
          git(root, 'add', 'B.md');
          git(root, 'commit', '-m', 'target A and B');
        },
        (wt) => {
          writeFileSync(join(wt, 'A.md'), 'reviewed-A\n');
          git(wt, 'add', 'A.md');
          writeFileSync(join(wt, 'B.md'), 'reviewed-B\n');
          git(wt, 'add', 'B.md');
        },
      );
    }

    // Seeds a TWO-COMMIT reviewed range where BOTH commits conflict with the target:
    // "reviewed A" is a REAL `git commit` (finalizeCommanderLocalCommit only ever mints
    // ONE new commit from the currently staged tree atop current HEAD, so a genuine
    // multi-commit reviewed range needs a direct commit first), then "reviewed B" is
    // staged and minted via finalizeCommanderLocalCommit, giving base_commit..frozen =
    // [reviewed A, reviewed B]. The rebase replays them one at a time, so resolving A's
    // conflict and continuing immediately hits B's conflict.
    function seedTwoCommitConflicts() {
      const s = setup(false);
      git(s.root, 'checkout', '-b', 'target-change');
      writeFileSync(join(s.root, 'A.md'), 'target-A\n');
      git(s.root, 'add', 'A.md');
      git(s.root, 'commit', '-m', 'target A');
      writeFileSync(join(s.root, 'B.md'), 'target-B\n');
      git(s.root, 'add', 'B.md');
      git(s.root, 'commit', '-m', 'target B');
      git(s.root, 'checkout', 'main');
      git(s.root, 'merge', '--ff-only', 'target-change');

      writeFileSync(join(s.assignment.worktree_path, 'A.md'), 'reviewed-A\n');
      git(s.assignment.worktree_path, 'add', 'A.md');
      git(s.assignment.worktree_path, 'commit', '-m', 'reviewed A');

      writeFileSync(join(s.assignment.worktree_path, 'B.md'), 'reviewed-B\n');
      git(s.assignment.worktree_path, 'add', 'B.md');
      expect(() => finalizeCommanderLocalCommit(s.database, commanderInput(s.root, s.assignment, 'conflict'))).toThrow();
      return s;
    }

    // Mirrors production's classifyRebaseConflicts stageLines: same `git show :stage:path`
    // command, NOT trimmed, so the count is provably the same one the label swap covers.
    function stageLineCount(worktree: string, stage: 2 | 3, filePath: string): number {
      return execFileSync('git', ['-C', worktree, 'show', `:${stage}:${filePath}`], { encoding: 'utf8' }).split('\n').length;
    }

    it('keep-mine stages the REVIEWED-side content (checkout --theirs; the rebase replays reviewed work onto the target)', () => {
      const s = seedOverlapConflict();

      const result = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'README.md', choice: 'keep-mine',
      }) as { path: string; staged: string; remaining: number; candidate?: string };

      expect(result.path).toBe('README.md');
      expect(result.staged).toContain('reviewed-content');
      expect(result.remaining).toBe(0);
      expect(result.candidate).toBeDefined(); // single-commit reviewed range: continue completes the rebase
      expect(readFileSync(join(s.assignment.worktree_path, 'README.md'), 'utf8')).toBe('reviewed-content\n');
      expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toBe('');
    });

    it('take-target stages the TARGET-side content (checkout --ours; the target is what the rebase checks out)', () => {
      const s = seedOverlapConflict();

      const result = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'README.md', choice: 'take-target',
      }) as { path: string; staged: string; remaining: number; candidate?: string };

      // take-target's staged bytes equal what is ALREADY checked out as HEAD mid-rebase (the
      // target commit being rebased onto), so the staged (index vs HEAD) diff is legitimately
      // empty here — the real proof of "target content" is the byte-exact worktree read below.
      expect(result.staged).toBe('');
      expect(result.remaining).toBe(0);
      expect(result.candidate).toBeDefined();
      expect(readFileSync(join(s.assignment.worktree_path, 'README.md'), 'utf8')).toBe('target-content\n');
    });

    it("prose choice writes exactly `content`, staged", () => {
      const s = seedOverlapConflict();

      const result = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'README.md', choice: 'prose', content: 'operator-authored resolution\n',
      }) as { path: string; staged: string; remaining: number; candidate?: string };

      expect(result.remaining).toBe(0);
      expect(result.candidate).toBeDefined();
      expect(readFileSync(join(s.assignment.worktree_path, 'README.md'), 'utf8')).toBe('operator-authored resolution\n');
    });

    it('MULTI-COMMIT: resolving the first conflicting commit pauses again on the second; resolving the second completes and registers a candidate', () => {
      const s = seedTwoCommitConflicts();
      expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toBe('A.md');

      const first = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'A.md', choice: 'keep-mine',
      }) as { path: string; staged: string; remaining: number; candidate?: string; conflicts?: unknown[] };

      // A.md's own conflict is fully resolved (0 unmerged paths from ITS commit), but
      // continuing immediately replays "reviewed B", which conflicts with target B ->
      // paused again. No candidate is ever registered on this intermediate pause.
      expect(first.candidate).toBeUndefined();
      expect(first.remaining).toBe(1);
      expect(first.conflicts).toEqual(expect.arrayContaining([
        expect.objectContaining({ path: 'B.md' }),
      ]));
      expect(readFileSync(join(s.assignment.worktree_path, 'A.md'), 'utf8')).toBe('reviewed-A\n');
      expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toBe('B.md');
      expect(() => git(s.assignment.worktree_path, 'rev-parse', '--verify', `${conflictCandidateRef(s.assignment.workspace_guid)}^{commit}`))
        .toThrow();

      const second = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'B.md', choice: 'take-target',
      }) as { path: string; staged: string; remaining: number; candidate?: string };

      expect(second.remaining).toBe(0);
      expect(second.candidate).toBeDefined();
      expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toBe('');
      expect(git(s.assignment.worktree_path, 'rev-parse', '--git-path', 'rebase-merge'))
        .not.toBe(''); // sanity: still resolves to a path string
      expect(existsSync(resolve(s.assignment.worktree_path,
        git(s.assignment.worktree_path, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(false);
      expect(git(s.assignment.worktree_path, 'rev-parse', '--verify', `${conflictCandidateRef(s.assignment.workspace_guid)}^{commit}`))
        .toBe(second.candidate);
      expect(readFileSync(join(s.assignment.worktree_path, 'B.md'), 'utf8')).toBe('target-B\n');
    });

    it('S5 abort: choice abort runs recoverRebaseInProgress(abort); the frozen reviewed commit is restored byte-identical, target unchanged', () => {
      const s = seedOverlapConflict();
      const frozen = git(s.root, 'rev-parse', `refs/ironclaude/finalization/${s.assignment.workspace_guid}/frozen`);
      const targetBefore = git(s.root, 'rev-parse', 'HEAD');

      const result = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'README.md', choice: 'abort',
      });

      expect(result).toMatchObject({ state: 'rebase-aborted' });
      expect(git(s.assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(frozen);
      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
      expect(existsSync(s.assignment.worktree_path)).toBe(true);
    });

    it('S6 no-partial: resolving one hunk then abort lands nothing; the frozen commit is restored', () => {
      const s = seedTwoFileConflict();
      const frozen = git(s.root, 'rev-parse', `refs/ironclaude/finalization/${s.assignment.workspace_guid}/frozen`);
      const targetBefore = git(s.root, 'rev-parse', 'HEAD');

      const first = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'A.md', choice: 'keep-mine',
      }) as { path: string; staged: string; remaining: number; candidate?: string };
      expect(first.remaining).toBe(1); // B.md still unresolved; rebase --continue never ran
      expect(first.candidate).toBeUndefined();
      expect(() => git(s.assignment.worktree_path, 'rev-parse', '--verify', `${conflictCandidateRef(s.assignment.workspace_guid)}^{commit}`))
        .toThrow();

      const aborted = resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'B.md', choice: 'abort',
      });

      expect(aborted).toMatchObject({ state: 'rebase-aborted' });
      expect(git(s.assignment.worktree_path, 'rev-parse', 'HEAD')).toBe(frozen);
      expect(git(s.root, 'rev-parse', 'HEAD')).toBe(targetBefore);
      // nothing landed: no candidate ref was ever registered.
      expect(() => git(s.assignment.worktree_path, 'rev-parse', '--verify', `${conflictCandidateRef(s.assignment.workspace_guid)}^{commit}`))
        .toThrow();
    });

    it('M7b label fix: classifyRebaseConflicts labels stage-3 as "your reviewed work" and stage-2 as "the integration target"', () => {
      // Asymmetric line counts on the two sides so re-swapping the labels back
      // necessarily fails this assertion (the two numbers cannot both be right twice).
      const s = seedRebaseConflictKind(
        (root) => {
          writeFileSync(join(root, 'README.md'), 'target line 1\ntarget line 2\n');
          git(root, 'add', 'README.md');
          git(root, 'commit', '-m', 'target modify');
        },
        (wt) => {
          writeFileSync(
            join(wt, 'README.md'),
            'reviewed line 1\nreviewed line 2\nreviewed line 3\nreviewed line 4\nreviewed line 5\n',
          );
          git(wt, 'add', 'README.md');
        },
      );

      const theirsCount = stageLineCount(s.assignment.worktree_path, 3, 'README.md'); // reviewed work
      const oursCount = stageLineCount(s.assignment.worktree_path, 2, 'README.md'); // integration target
      expect(theirsCount).not.toBe(oursCount);

      const conflicts = classifyRebaseConflicts(s.assignment.worktree_path);
      expect(conflicts).toHaveLength(1);
      expect(conflicts[0].summary).toContain(`your reviewed work has ${theirsCount} line(s)`);
      expect(conflicts[0].summary).toContain(`the integration target has ${oursCount} line(s)`);
    });

    it('delete-modify: keep-mine on a path the reviewed side deleted is refused explicitly, and the message names abort/preserve-and-defer (I1)', () => {
      // Target modifies README.md; the reviewed worktree DELETES it -> stage 3 (--theirs,
      // reviewed) is absent. keep-mine's `checkout --theirs` has no stage to check out.
      const s = seedRebaseConflictKind(
        (root) => { writeFileSync(join(root, 'README.md'), 'target\n'); git(root, 'add', 'README.md'); git(root, 'commit', '-m', 'target modify'); },
        (wt) => { git(wt, 'rm', 'README.md'); },
      );

      let message = '';
      try {
        resolveConflictHunk(s.database, {
          repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
          path: 'README.md', choice: 'keep-mine',
        });
      } catch (error) { message = error instanceof Error ? error.message : String(error); }
      expect(message).toMatch(/delete-modify/);
      // I1: the redirect must name aborting to accept the deletion (preserve-and-defer),
      // not imply 'prose' can express a deletion.
      expect(message).toMatch(/preserve-and-defer/);

      // Refused before any mutation: the rebase is still paused, nothing staged/continued.
      expect(git(s.assignment.worktree_path, 'diff', '--name-only', '--diff-filter=U')).toContain('README.md');
      expect(existsSync(resolve(s.assignment.worktree_path,
        git(s.assignment.worktree_path, 'rev-parse', '--git-path', 'rebase-merge')))).toBe(true);
    });

    // C1 (end-review CRITICAL): resolve_conflict_hunk is agent-callable (requireProviderRoot,
    // no human intent); input.path must be bounded to the current unmerged set so 'prose'
    // cannot writeFileSync outside the worktree or inject a non-conflicted file.
    it('C1: rejects an ABSOLUTE input.path outside the worktree before any write (prose)', () => {
      const s = seedOverlapConflict();
      // A per-run temp dir OUTSIDE the worktree, tracked for cleanup (NOT a fixed tmpdir name:
      // that is stale-file-flaky AND collides across vitest parallel workers). mkdtemp creates
      // the parent, so the RED write actually lands (no ENOENT masking the RED).
      const escapeDir = mkdtempSync(join(tmpdir(), 'ironclaude-c1-escape-'));
      directories.push(escapeDir);
      const escapePath = join(escapeDir, 'evil');

      expect(() => resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: escapePath, choice: 'prose', content: 'pwned\n',
      })).toThrow(/is not in the unmerged set/);

      expect(existsSync(escapePath)).toBe(false); // guard refused before writeFileSync
    });

    it('C1: rejects a ../-traversal input.path before any write (prose)', () => {
      const s = seedOverlapConflict();
      const escapePath = join('..', 'ironclaude-c1-traversal-evil');
      const resolved = resolve(s.assignment.worktree_path, escapePath);

      expect(() => resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: escapePath, choice: 'prose', content: 'pwned\n',
      })).toThrow(/is not in the unmerged set/);

      expect(existsSync(resolved)).toBe(false);
    });

    it('C1: rejects a real in-worktree path NOT in the unmerged set (no injection into the confirmed commit)', () => {
      const s = seedOverlapConflict();
      // A benign, non-conflicted in-tree path: valid file, but not part of the conflict.
      writeFileSync(join(s.assignment.worktree_path, 'benign.txt'), 'benign\n');
      git(s.assignment.worktree_path, 'add', 'benign.txt');

      expect(() => resolveConflictHunk(s.database, {
        repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
        path: 'benign.txt', choice: 'prose', content: 'injected\n',
      })).toThrow(/is not in the unmerged set/);

      // The injection content did not land: benign.txt is byte-unchanged.
      expect(readFileSync(join(s.assignment.worktree_path, 'benign.txt'), 'utf8')).toBe('benign\n');
    });
  });

  describe('finalize cumulative-effect equality is content-size independent', () => {
    it('finalizes successfully when the reviewed --binary --full-index diff exceeds 1MB', () => {
      const { root, database, assignment } = setup(false);
      const tempDirsBefore = countFinalizeDiffTempDirs();
      writeFileSync(join(assignment.worktree_path, 'big.txt'), 'x'.repeat(1_500_000) + '\n');
      git(assignment.worktree_path, 'add', 'big.txt');

      const result = finalizeCommanderLocalCommit(
        database, commanderInput(root, assignment, 'large reviewed diff'),
      );

      expect(result.state).toBe('cleaned');
      expect(readFileSync(join(root, 'big.txt'), 'utf8').length).toBeGreaterThanOrEqual(1_500_000);
      expect(countFinalizeDiffTempDirs()).toBe(tempDirsBefore);
    });

    it('refuses finalization when the rebased cumulative effect diverges from the reviewed content, and still cleans up its temp diff files', () => {
      const { root, database, assignment } = setup(false);
      const tempDirsBefore = countFinalizeDiffTempDirs();

      expect(() => finalizeCommanderLocalCommit(
        database,
        commanderInput(root, assignment, 'reviewed work'),
        {
          beforeDescendantProof: () => {
            // Introduces an unreviewed change AFTER the rebase completes but BEFORE the
            // rebased-effect equality check, so the check must catch it deterministically
            // (no merge conflict is involved).
            writeFileSync(join(assignment.worktree_path, 'divergent.txt'), 'unreviewed extra change\n');
            git(assignment.worktree_path, 'add', 'divergent.txt');
            git(assignment.worktree_path, 'commit', '-m', 'unreviewed extra change');
          },
        },
      )).toThrow('Finalization rebased cumulative effect differs from reviewed content; preserving worktree');

      expect(countFinalizeDiffTempDirs()).toBe(tempDirsBefore);
    });
  });
  });
  }
});
}
