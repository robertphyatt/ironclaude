import type Database from 'better-sqlite3';
import { existsSync } from 'node:fs';
import path from 'node:path';
import {
  acquireIntegrationLock,
  getAssignment,
  recordIntegration,
  transitionAssignment,
} from './db.js';
import {
  discoverRepository,
  isAncestor,
  runGit,
  worktreeHead,
  worktreeIsClean,
} from './git.js';
import {
  pushExactAuthorizedRef,
  pushExactAuthorizedIntegratedCandidate,
  revalidateAuthorizedCommitState,
  type AuthorizedDirectGitOperation,
} from './git-authority.js';
import { WorkspaceService } from './workspace-service.js';
import type { Assignment } from './types.js';

export interface FinalizationResult {
  state: 'cleaned' | 'pushed' | 'pushed-only' | 'integrated-local';
  integratedCommit?: string;
  pushError?: string;
}

export interface CommanderLocalCommitInput {
  repositoryPath: string;
  workspaceGuid: string;
  providerRootSessionId: string;
  message: string;
  canonicalBranch: string;
  localRef: string;
  stagedTree: string;
  parentOid: string;
}

export type CommanderReviewedEvidence = Pick<
  CommanderLocalCommitInput,
  'canonicalBranch' | 'localRef' | 'stagedTree' | 'parentOid'
>;

export interface ReconcileFinalizationInput {
  repositoryPath: string;
  workspaceGuid: string;
  providerRootSessionId: string;
}

export interface FinalizationHooks {
  /** Internal test seam for an operator target move after the rebase proof. */
  beforeCheckedFastForward?: () => void;
  /** Test-only lock race after checkout fast-forward and before durable record. */
  afterCheckedFastForwardBeforeRecord?: () => void;
  /** Test-only target race after final validation and immediately before ref CAS. */
  beforeTargetCompareAndSwap?: () => void;
  /** Test-only crash boundary after ref CAS and before primary checkout update. */
  afterTargetCompareAndSwapBeforeCheckout?: () => void;
  beforeDescendantProof?: () => void;
  /** Test-only failure injection inside durable integration transaction. */
  beforeAtomicIntegrationState?: () => void;
  /** Test-only persistence failure after remote candidate readback succeeds. */
  beforePushSuccessPersistence?: () => void;
  /** Test-only local-ref race after exact commit creation and before freeze. */
  afterExactCommitBeforeFreeze?: () => void;
  /** Test-only transport uncertainty after remote accepts candidate. */
  afterRemoteMutationBeforeResult?: () => void;
}

interface LocalFinalization {
  assignment: Assignment;
  repositoryPath: string;
  frozenCommit: string;
  candidateCommit: string;
  integratedCommit: string;
}

const usedDirectAuthorities = new WeakSet<object>();

interface IntegrationPendingDisposition {
  phase: 'integration-pending';
  frozenCommit: string;
  remoteName: string;
  remoteUrl: string;
  destinationRef: string;
  expectedRemoteOldOid: string | null;
}

interface PushDisposition {
  phase: 'push-pending' | 'push-succeeded' | 'push-failed';
  candidateCommit: string;
  frozenCommit: string;
  remoteName: string;
  remoteUrl: string;
  destinationRef: string;
  expectedRemoteOldOid: string | null;
}

function encodePushDisposition(value: PushDisposition): string {
  return JSON.stringify(value);
}

function decodePushDisposition(value: string | null): PushDisposition | undefined {
  if (!value) return undefined;
  try {
    const parsed = JSON.parse(value) as Partial<PushDisposition>;
    if ((parsed.phase !== 'push-pending' && parsed.phase !== 'push-succeeded' && parsed.phase !== 'push-failed')
      || typeof parsed.candidateCommit !== 'string'
      || typeof parsed.frozenCommit !== 'string'
      || typeof parsed.remoteName !== 'string'
      || typeof parsed.remoteUrl !== 'string'
      || typeof parsed.destinationRef !== 'string'
      || (parsed.expectedRemoteOldOid !== null && typeof parsed.expectedRemoteOldOid !== 'string')) return undefined;
    return parsed as PushDisposition;
  } catch { return undefined; }
}

function decodeIntegrationPendingDisposition(value: string | null): IntegrationPendingDisposition | undefined {
  if (!value) return undefined;
  try {
    const parsed = JSON.parse(value) as Partial<IntegrationPendingDisposition>;
    if (parsed.phase !== 'integration-pending'
      || typeof parsed.frozenCommit !== 'string'
      || typeof parsed.remoteName !== 'string'
      || typeof parsed.remoteUrl !== 'string'
      || typeof parsed.destinationRef !== 'string'
      || (parsed.expectedRemoteOldOid !== null && typeof parsed.expectedRemoteOldOid !== 'string')) return undefined;
    return parsed as IntegrationPendingDisposition;
  } catch { return undefined; }
}

function targetRef(assignment: Assignment): string {
  return assignment.integration_target.startsWith('refs/')
    ? assignment.integration_target
    : `refs/heads/${assignment.integration_target}`;
}

function freezeRef(workspaceGuid: string): string {
  return `refs/ironclaude/finalization/${workspaceGuid}/frozen`;
}

function candidateRef(workspaceGuid: string): string {
  return `refs/ironclaude/finalization/${workspaceGuid}/candidate`;
}

function setDisposition(db: Database.Database, workspaceGuid: string, disposition: string | null): void {
  db.prepare("UPDATE assignments SET disposition = ?, updated_at = datetime('now') WHERE workspace_guid = ?")
    .run(disposition, workspaceGuid);
}

function remoteRefOid(cwd: string, remoteUrl: string, destinationRef: string): string | null {
  const output = runGit(cwd, ['ls-remote', '--refs', remoteUrl, destinationRef]).trim();
  if (output === '') return null;
  const [oid, ref, ...extra] = output.split(/\s+/);
  if (extra.length !== 0 || ref !== destinationRef) throw new Error('Finalization remote proof is malformed');
  return oid;
}

function cumulativeBinaryEffect(cwd: string, base: string, head: string): string {
  return runGit(cwd, ['diff', '--binary', '--full-index', base, head]);
}

function requireExactIntegrationLock(
  db: Database.Database,
  assignment: Assignment,
  ref: string,
  expectedTarget: string,
): void {
  const lock = db.prepare(`
    SELECT repository_identity, workspace_guid, target_ref, expected_target
    FROM integration_locks WHERE repository_identity = ?
  `).get(assignment.repository_identity) as {
    repository_identity: string; workspace_guid: string; target_ref: string; expected_target: string;
  } | undefined;
  if (!lock
    || lock.repository_identity !== assignment.repository_identity
    || lock.workspace_guid !== assignment.workspace_guid
    || lock.target_ref !== ref
    || lock.expected_target !== expectedTarget) {
    throw new Error('Finalization integration lock changed; preserving worktree');
  }
}

function recoveryIntegrationLockExpectedTarget(
  db: Database.Database,
  assignment: Assignment,
  ref: string,
): string {
  const lock = db.prepare(`
    SELECT workspace_guid, target_ref, expected_target
    FROM integration_locks WHERE repository_identity = ?
  `).get(assignment.repository_identity) as {
    workspace_guid: string; target_ref: string; expected_target: string;
  } | undefined;
  if (!lock || lock.workspace_guid !== assignment.workspace_guid || lock.target_ref !== ref) {
    throw new Error('Crash reconciliation lacks durable pre-fast-forward lock proof; preserving worktree');
  }
  return lock.expected_target;
}

function releaseExactIntegrationLockIfHeld(
  db: Database.Database,
  assignment: Assignment,
  ref: string,
  expectedTarget: string,
): void {
  db.prepare(`
    DELETE FROM integration_locks
    WHERE repository_identity = ? AND workspace_guid = ? AND target_ref = ? AND expected_target = ?
  `).run(assignment.repository_identity, assignment.workspace_guid, ref, expectedTarget);
}

function requireMessage(message: string): void {
  if (message.length === 0) throw new Error('Finalization commit message must not be empty');
}

function requireCommanderFinalizationInput(input: CommanderLocalCommitInput): void {
  const expected = [
    'repositoryPath', 'workspaceGuid', 'providerRootSessionId', 'message',
    'canonicalBranch', 'localRef', 'stagedTree', 'parentOid',
  ].sort();
  const actual = Object.keys(input).sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])
    || expected.some((key) => typeof input[key as keyof CommanderLocalCommitInput] !== 'string'
      || (input[key as keyof CommanderLocalCommitInput] as string).length === 0)) {
    throw new Error('Commander finalization input is malformed');
  }
}

interface ExactCommitEvidence extends CommanderReviewedEvidence {}

function exactCommitEvidence(authority: AuthorizedDirectGitOperation): ExactCommitEvidence {
  if (authority.operation === 'push') throw new Error('Push authority cannot create a local commit');
  const evidence = authority.evidence as Partial<ExactCommitEvidence>;
  if (typeof evidence.canonicalBranch !== 'string'
    || typeof evidence.localRef !== 'string'
    || typeof evidence.stagedTree !== 'string'
    || typeof evidence.parentOid !== 'string') {
    throw new Error('Direct authority lacks exact commit evidence');
  }
  return evidence as ExactCommitEvidence;
}

function validateExactCommitState(sourcePath: string, evidence: ExactCommitEvidence): void {
  const branch = runGit(sourcePath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
  const tree = runGit(sourcePath, ['write-tree']).trim();
  const parent = runGit(sourcePath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
  const local = runGit(sourcePath, ['rev-parse', '--verify', `${evidence.localRef}^{commit}`]).trim();
  if (branch !== evidence.canonicalBranch
    || tree !== evidence.stagedTree
    || parent !== evidence.parentOid
    || local !== parent) {
    throw new Error('Reviewed commit evidence changed; preserving worktree');
  }
}

function requireAssignmentCommitBinding(evidence: ExactCommitEvidence, assignment: Assignment): void {
  if (evidence.canonicalBranch !== assignment.branch
    || evidence.localRef !== `refs/heads/${assignment.branch}`) {
    throw new Error('Reviewed commit evidence does not bind the assignment branch');
  }
}

function createExactCommit(sourcePath: string, evidence: ExactCommitEvidence, message: string): string {
  validateExactCommitState(sourcePath, evidence);
  const commit = runGit(sourcePath, ['commit-tree', evidence.stagedTree, '-p', evidence.parentOid, '-m', message]).trim();
  runGit(sourcePath, ['update-ref', evidence.localRef, commit, evidence.parentOid]);
  if (worktreeHead(sourcePath) !== commit) throw new Error('Exact commit ref update did not update checkout HEAD');
  return commit;
}


function exactAssignment(
  db: Database.Database,
  repositoryPath: string,
  workspaceGuid: string,
  providerRootSessionId: string,
): { assignment: Assignment; primaryCheckoutPath: string } {
  const repository = discoverRepository(repositoryPath);
  const assignment = getAssignment(db, workspaceGuid);
  if (!assignment
    || assignment.repository_identity !== repository.repositoryIdentity
    || assignment.owner_session_id !== providerRootSessionId) {
    throw new Error('Finalization assignment binding does not match repository and provider root');
  }
  return { assignment, primaryCheckoutPath: repository.primaryCheckoutPath };
}

function fencePrimaryCheckout(db: Database.Database, repositoryIdentity: string): void {
  if (db.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?').get(repositoryIdentity)) {
    throw new Error('Finalization is fenced while primary checkout is owned');
  }
}

function verifyPrimaryTarget(primaryCheckoutPath: string, ref: string, expectedTarget: string): void {
  if (!worktreeIsClean(primaryCheckoutPath)) {
    throw new Error('Finalization primary checkout is dirty; preserving worktree');
  }
  const checkedOutRef = runGit(primaryCheckoutPath, ['symbolic-ref', '--quiet', 'HEAD']).trim();
  const actualHead = runGit(primaryCheckoutPath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
  const actualTarget = runGit(primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  if (checkedOutRef !== ref || actualHead !== expectedTarget || actualTarget !== expectedTarget) {
    throw new Error('Finalization primary checkout is not cleanly checked out at expected target');
  }
}

function verifyPrimaryAfterFastForward(primaryCheckoutPath: string, ref: string, integratedCommit: string): void {
  const head = runGit(primaryCheckoutPath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
  const target = runGit(primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  const primaryTree = runGit(primaryCheckoutPath, ['rev-parse', '--verify', 'HEAD^{tree}']).trim();
  const integratedTree = runGit(primaryCheckoutPath, ['rev-parse', '--verify', `${integratedCommit}^{tree}`]).trim();
  if (head !== integratedCommit || target !== integratedCommit || primaryTree !== integratedTree || !worktreeIsClean(primaryCheckoutPath)) {
    throw new Error('Finalization primary checkout is inconsistent after checked fast-forward');
  }
}

function markIntegrated(
  db: Database.Database,
  assignment: Assignment,
  integratedCommit: string,
  ref: string,
  hooks?: FinalizationHooks,
): Assignment {
  const integrationPending = decodeIntegrationPendingDisposition(assignment.disposition);
  if (assignment.disposition && !integrationPending) {
    throw new Error('Finalization integration disposition is malformed');
  }
  const nextDisposition = integrationPending
    ? encodePushDisposition({ ...integrationPending, phase: 'push-pending', candidateCommit: integratedCommit })
    : null;
  db.transaction(() => {
    recordIntegration(db, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef: ref,
      integratedCommit,
    });
    hooks?.beforeAtomicIntegrationState?.();
    const updated = db.prepare(`
      UPDATE assignments
      SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ?, updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'ready_for_integration'
    `).run(integratedCommit, integratedCommit, nextDisposition, assignment.workspace_guid);
    if (updated.changes !== 1) throw new Error('Finalization assignment state changed before durable integration record');
  })();
  return getAssignment(db, assignment.workspace_guid)!;
}

function cleanupFinalized(db: Database.Database, repositoryPath: string, assignment: Assignment): void {
  new WorkspaceService(db).cleanupWorkspace({
    repositoryPath,
    workspaceGuid: assignment.workspace_guid,
    ownerSessionId: assignment.owner_session_id!,
  });
}

function finishLocalIntegration(db: Database.Database, local: LocalFinalization): FinalizationResult {
  if (decodePushDisposition(local.assignment.disposition)) {
    return {
      state: 'integrated-local',
      integratedCommit: local.integratedCommit,
      pushError: 'Remote has not proved the exact integrated candidate',
    };
  }
  cleanupFinalized(db, local.repositoryPath, local.assignment);
  return { state: 'cleaned', integratedCommit: local.integratedCommit };
}

function repairPrimaryCheckoutAfterInterruptedCas(
  repositoryPath: string,
  ref: string,
  expectedTarget: string,
  candidate: string,
): void {
  const checkedOutRef = runGit(repositoryPath, ['symbolic-ref', '--quiet', 'HEAD']).trim();
  const currentTarget = runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  const expectedTree = runGit(repositoryPath, ['rev-parse', '--verify', `${expectedTarget}^{tree}`]).trim();
  const indexTree = runGit(repositoryPath, ['write-tree']).trim();
  const unstaged = runGit(repositoryPath, ['diff', '--name-only']).trim();
  const untracked = runGit(repositoryPath, ['ls-files', '--others', '--exclude-standard']).trim();
  if (checkedOutRef !== ref || currentTarget !== candidate || indexTree !== expectedTree || unstaged !== '' || untracked !== '') {
    throw new Error('Crash reconciliation primary checkout has unproved changes; preserving worktree');
  }
  runGit(repositoryPath, ['read-tree', '--reset', '-u', candidate]);
  verifyPrimaryAfterFastForward(repositoryPath, ref, candidate);
}

/** One private path for direct and reviewed-local finalization; it never pushes. */
function continueFrozenFinalization(
  db: Database.Database,
  repositoryPath: string,
  assignment: Assignment,
  sourcePath: string,
  frozenCommit: string,
  hooks?: FinalizationHooks,
): LocalFinalization {
  fencePrimaryCheckout(db, assignment.repository_identity);
  const ready = getAssignment(db, assignment.workspace_guid);
  if (!ready || ready.lifecycle_status !== 'ready_for_integration') {
    throw new Error('Finalization requires durable ready state');
  }
  const ref = targetRef(ready);
  const expectedTarget = runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
  acquireIntegrationLock(db, {
    repositoryIdentity: ready.repository_identity,
    workspaceGuid: ready.workspace_guid,
    targetRef: ref,
    expectedTarget,
  });
  let rebaseStarted = false;
  let rebaseFinished = false;
  let targetAdvanced = false;
  let integrationRecorded = false;
  try {
    fencePrimaryCheckout(db, ready.repository_identity);
    const reviewedEffect = cumulativeBinaryEffect(sourcePath, ready.base_commit, frozenCommit);
    rebaseStarted = true;
    runGit(sourcePath, ['rebase', '--onto', ref, ready.base_commit]);
    rebaseFinished = true;
    hooks?.beforeDescendantProof?.();
    const integratedCommit = worktreeHead(sourcePath);
    if (!isAncestor(repositoryPath, expectedTarget, integratedCommit)) {
      throw new Error('Finalization descendant proof failed; preserving worktree');
    }
    if (cumulativeBinaryEffect(sourcePath, expectedTarget, integratedCommit) !== reviewedEffect) {
      throw new Error('Finalization rebased cumulative effect differs from reviewed content; preserving worktree');
    }
    runGit(sourcePath, ['update-ref', candidateRef(ready.workspace_guid), integratedCommit]);
    hooks?.beforeCheckedFastForward?.();
    fencePrimaryCheckout(db, ready.repository_identity);
    if (runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim() !== expectedTarget) {
      throw new Error('Finalization target moved; preserving worktree');
    }
    verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
    requireExactIntegrationLock(db, ready, ref, expectedTarget);
    hooks?.beforeTargetCompareAndSwap?.();
    runGit(repositoryPath, ['update-ref', ref, integratedCommit, expectedTarget]);
    targetAdvanced = true;
    hooks?.afterTargetCompareAndSwapBeforeCheckout?.();
    runGit(repositoryPath, ['read-tree', '--reset', '-u', integratedCommit]);
    verifyPrimaryAfterFastForward(repositoryPath, ref, integratedCommit);
    hooks?.afterCheckedFastForwardBeforeRecord?.();
    verifyPrimaryAfterFastForward(repositoryPath, ref, integratedCommit);
    requireExactIntegrationLock(db, ready, ref, expectedTarget);
    const integrated = markIntegrated(db, ready, integratedCommit, ref, hooks);
    integrationRecorded = true;
    return { assignment: integrated, repositoryPath, frozenCommit, candidateCommit: integratedCommit, integratedCommit };
  } catch (error) {
    if (rebaseStarted && !rebaseFinished) {
      // Preserve conflict state and durable ready/frozen evidence. A reviewer
      // can resolve and continue this rebase, then invoke reconciliation.
    } else if (!targetAdvanced) {
      // A frozen ready row is a deliberate resumable state for lock/target races.
      try { runGit(sourcePath, ['update-ref', '-d', candidateRef(ready.workspace_guid)]); } catch { /* candidate may not exist */ }
      try { runGit(sourcePath, ['reset', '--hard', frozenCommit]); } catch { /* preserve recoverable bytes */ }
    }
    throw error;
  } finally {
    if (!targetAdvanced || integrationRecorded) {
      releaseExactIntegrationLockIfHeld(db, ready, ref, expectedTarget);
    }
  }
}

function finalizeLocalCommit(
  db: Database.Database,
  repositoryPath: string,
  assignment: Assignment,
  sourcePath: string,
  frozenCommit: string,
  hooks?: FinalizationHooks,
): LocalFinalization {
  if (assignment.lifecycle_status !== 'active') {
    throw new Error('Only an active assignment can begin finalization');
  }
  const ref = targetRef(assignment);
  const expectedTarget = runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
  if (worktreeHead(sourcePath) !== frozenCommit) {
    throw new Error('Reviewed commit changed before freeze; preserving worktree');
  }
  runGit(sourcePath, ['update-ref', freezeRef(assignment.workspace_guid), frozenCommit]);
  if (worktreeHead(sourcePath) !== frozenCommit) {
    throw new Error('Reviewed commit changed before freeze; preserving worktree');
  }
  transitionAssignment(db, assignment.workspace_guid, 'active', 'ready_for_integration');
  return continueFrozenFinalization(db, repositoryPath, assignment, sourcePath, frozenCommit, hooks);
}

function finalizeAttestedCandidate(
  db: Database.Database,
  repositoryPath: string,
  assignment: Assignment,
  candidate: string,
): LocalFinalization {
  const ref = targetRef(assignment);
  const expectedTarget = runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
  acquireIntegrationLock(db, {
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    targetRef: ref,
    expectedTarget,
  });
  let targetAdvanced = false;
  let integrationRecorded = false;
  try {
    fencePrimaryCheckout(db, assignment.repository_identity);
    if (worktreeHead(assignment.worktree_path) !== candidate || !isAncestor(repositoryPath, expectedTarget, candidate)) {
      throw new Error('Fresh repair authority is not an exact descendant candidate; preserving worktree');
    }
    verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
    requireExactIntegrationLock(db, assignment, ref, expectedTarget);
    runGit(repositoryPath, ['update-ref', ref, candidate, expectedTarget]);
    targetAdvanced = true;
    runGit(repositoryPath, ['read-tree', '--reset', '-u', candidate]);
    verifyPrimaryAfterFastForward(repositoryPath, ref, candidate);
    requireExactIntegrationLock(db, assignment, ref, expectedTarget);
    const integrated = markIntegrated(db, assignment, candidate, ref);
    integrationRecorded = true;
    const frozen = runGit(repositoryPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
    return { assignment: integrated, repositoryPath, frozenCommit: frozen, candidateCommit: candidate, integratedCommit: candidate };
  } finally {
    if (!targetAdvanced || integrationRecorded) {
      releaseExactIntegrationLockIfHeld(db, assignment, ref, expectedTarget);
    }
  }
}

/** Finalizes only an authority already verified and consumed by Task 3. */
export function finalizeDirectAuthority(
  db: Database.Database,
  authority: AuthorizedDirectGitOperation,
  message: string,
  hooks?: FinalizationHooks,
): FinalizationResult {
  if (authority.operation === 'push') {
    const exact = exactAssignment(db, authority.worktreePath, authority.workspaceGuid, authority.providerRootSessionId);
    if (exact.assignment.lifecycle_status !== 'active' || authority.checkoutMode !== 'managed'
      || authority.worktreePath !== exact.assignment.worktree_path) {
      throw new Error('Push-only authority does not designate active managed workspace');
    }
    fencePrimaryCheckout(db, exact.assignment.repository_identity);
    const evidence = authority.evidence as {
      localOid: string; remoteUrl: string; destinationRef: string; expectedRemoteOldOid: string | null;
    };
    let mutationError: string | undefined;
    try {
      pushExactAuthorizedRef(authority);
      hooks?.afterRemoteMutationBeforeResult?.();
    } catch (error) {
      mutationError = error instanceof Error ? error.message : String(error);
    }
    let remote: string | null;
    try {
      remote = remoteRefOid(authority.worktreePath, evidence.remoteUrl, evidence.destinationRef);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Push-only remote readback failed: ${detail}`);
    }
    if (remote === evidence.localOid) return { state: 'pushed-only' };
    if (remote === evidence.expectedRemoteOldOid || remote === null) {
      throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Push-only remote has not proved the exact authorized commit`);
    }
    throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Push-only remote outcome is ambiguous`);
  }
  if (usedDirectAuthorities.has(authority)) throw new Error('Direct finalization authority is single-use');
  usedDirectAuthorities.add(authority);
  requireMessage(message);
  revalidateAuthorizedCommitState(authority);
  const exact = exactAssignment(db, authority.worktreePath, authority.workspaceGuid, authority.providerRootSessionId);
  if (exact.assignment.repository_identity !== authority.repositoryIdentity
    || authority.checkoutMode !== 'managed'
    || authority.worktreePath !== exact.assignment.worktree_path) {
    throw new Error('Direct finalization authority does not designate managed workspace');
  }
  fencePrimaryCheckout(db, exact.assignment.repository_identity);
  const isRepair = exact.assignment.lifecycle_status === 'ready_for_integration';
  if (exact.assignment.lifecycle_status !== 'active' && !isRepair) throw new Error('Only active or ready repair assignment can begin finalization');
  if (isRepair && authority.operation !== 'commit') throw new Error('Ready repair requires fresh exact commit authority');
  if (isRepair) {
    try { runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(exact.assignment.workspace_guid)}^{commit}`]); } catch {
      throw new Error('Ready repair lacks durable frozen finalization state');
    }
  }
  const directTarget = targetRef(exact.assignment);
  verifyPrimaryTarget(
    exact.primaryCheckoutPath,
    directTarget,
    runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${directTarget}^{commit}`]).trim(),
  );
  const directEvidence = exactCommitEvidence(authority);
  requireAssignmentCommitBinding(directEvidence, exact.assignment);
  const committed = createExactCommit(authority.worktreePath, directEvidence, message);
  hooks?.afterExactCommitBeforeFreeze?.();
  if (isRepair) {
    const candidate = committed;
    runGit(authority.worktreePath, ['update-ref', candidateRef(exact.assignment.workspace_guid), candidate]);
    const local = finalizeAttestedCandidate(db, exact.primaryCheckoutPath, exact.assignment, candidate);
    return finishLocalIntegration(db, local);
  }
  if (authority.operation === 'commit-and-push') {
    const pushEvidence = authority.evidence as {
      remoteName: string;
      remoteUrl: string;
      destinationRef: string;
      expectedRemoteOldOid: string | null;
    };
    const pending: IntegrationPendingDisposition = {
      phase: 'integration-pending', frozenCommit: committed,
      remoteName: pushEvidence.remoteName, remoteUrl: pushEvidence.remoteUrl,
      destinationRef: pushEvidence.destinationRef,
      expectedRemoteOldOid: pushEvidence.expectedRemoteOldOid,
    };
    setDisposition(db, exact.assignment.workspace_guid, JSON.stringify(pending));
  }
  const local = finalizeLocalCommit(
    db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, committed, hooks,
  );
  if (authority.operation === 'commit') {
    cleanupFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'cleaned', integratedCommit: local.integratedCommit };
  }

  // Candidate push is an internal one-use extension of Task 3 authority. The
  // source remains on the integrated candidate; no reset-to-frozen window.
  const disposition = decodePushDisposition(local.assignment.disposition);
  if (!disposition || disposition.candidateCommit !== local.candidateCommit || disposition.frozenCommit !== local.frozenCommit) {
    throw new Error('Integrated candidate lacks durable push-pending proof');
  }
  let mutationError: string | undefined;
  try {
    pushExactAuthorizedIntegratedCandidate(authority, local.candidateCommit, targetRef(local.assignment));
    hooks?.afterRemoteMutationBeforeResult?.();
  } catch (error) {
    mutationError = error instanceof Error ? error.message : String(error);
  }
  let remote: string | null;
  try {
    remote = remoteRefOid(local.assignment.worktree_path, disposition.remoteUrl, disposition.destinationRef);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return {
      state: 'integrated-local',
      integratedCommit: local.integratedCommit,
      pushError: `${mutationError ? `Push command failed: ${mutationError}. ` : ''}Remote readback failed: ${detail}`,
    };
  }
  if (remote !== local.candidateCommit) {
    return {
      state: 'integrated-local', integratedCommit: local.integratedCommit,
      pushError: `${mutationError ? `Push command failed: ${mutationError}. ` : ''}${
        remote === disposition.expectedRemoteOldOid || remote === null
          ? 'Remote has not proved the exact integrated candidate'
          : 'Remote outcome is ambiguous; preserving push-pending state'
      }`,
    };
  }
  try {
    hooks?.beforePushSuccessPersistence?.();
    setDisposition(db, local.assignment.workspace_guid, encodePushDisposition({ ...disposition, phase: 'push-succeeded' }));
    setDisposition(db, local.assignment.workspace_guid, null);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return {
      state: 'integrated-local',
      integratedCommit: local.integratedCommit,
      pushError: `Remote is already the exact integrated candidate, but local success persistence failed: ${detail}`,
    };
  }
  cleanupFinalized(db, local.repositoryPath, local.assignment);
  return { state: 'pushed', integratedCommit: local.integratedCommit };
}

/** Reviewed Brain/Commander work follows same local coordinator and never pushes. */
export function finalizeCommanderLocalCommit(
  db: Database.Database,
  input: CommanderLocalCommitInput,
  hooks?: FinalizationHooks,
): FinalizationResult {
  requireCommanderFinalizationInput(input);
  requireMessage(input.message);
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  fencePrimaryCheckout(db, exact.assignment.repository_identity);
  const isRepair = exact.assignment.lifecycle_status === 'ready_for_integration';
  if (exact.assignment.lifecycle_status !== 'active' && !isRepair) throw new Error('Only active or ready repair assignment can begin finalization');
  if (isRepair) {
    try { runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(exact.assignment.workspace_guid)}^{commit}`]); } catch {
      throw new Error('Ready repair lacks durable frozen finalization state');
    }
  }
  const commanderTarget = targetRef(exact.assignment);
  verifyPrimaryTarget(
    exact.primaryCheckoutPath,
    commanderTarget,
    runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${commanderTarget}^{commit}`]).trim(),
  );
  const reviewedEvidence: CommanderReviewedEvidence = {
    canonicalBranch: input.canonicalBranch,
    localRef: input.localRef,
    stagedTree: input.stagedTree,
    parentOid: input.parentOid,
  };
  requireAssignmentCommitBinding(reviewedEvidence, exact.assignment);
  const committed = createExactCommit(exact.assignment.worktree_path, reviewedEvidence, input.message);
  hooks?.afterExactCommitBeforeFreeze?.();
  if (isRepair) {
    const candidate = committed;
    runGit(exact.assignment.worktree_path, ['update-ref', candidateRef(exact.assignment.workspace_guid), candidate]);
    const local = finalizeAttestedCandidate(db, exact.primaryCheckoutPath, exact.assignment, candidate);
    cleanupFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'cleaned', integratedCommit: candidate };
  }
  const local = finalizeLocalCommit(
    db, exact.primaryCheckoutPath, exact.assignment, exact.assignment.worktree_path, committed, hooks,
  );
  cleanupFinalized(db, local.repositoryPath, local.assignment);
  return { state: 'cleaned', integratedCommit: local.integratedCommit };
}

/** Crash recovery only advances a durable ready row once its record is reachable. */
export function reconcileFinalization(db: Database.Database, input: ReconcileFinalizationInput): FinalizationResult {
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const assignment = exact.assignment;
  fencePrimaryCheckout(db, assignment.repository_identity);
  if (assignment.lifecycle_status === 'integrated') {
    const candidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
    if (assignment.integrated_commit !== candidate) {
      throw new Error('Integrated assignment candidate proof differs; preserving worktree');
    }
    const disposition = decodePushDisposition(assignment.disposition);
    if (assignment.disposition && !disposition) {
      throw new Error('Integrated assignment push disposition is malformed; preserving worktree');
    }
    if (disposition) {
      if (disposition.candidateCommit !== candidate
        || runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim() !== disposition.frozenCommit) {
        throw new Error('Integrated assignment push refs differ; preserving worktree');
      }
      const sourceHead = worktreeHead(assignment.worktree_path);
      if (sourceHead !== candidate) {
        if (sourceHead !== disposition.frozenCommit || !worktreeIsClean(assignment.worktree_path)) {
          throw new Error('Integrated assignment source HEAD differs from candidate; preserving worktree');
        }
        runGit(assignment.worktree_path, ['reset', '--hard', candidate]);
      }
      const remote = remoteRefOid(assignment.worktree_path, disposition.remoteUrl, disposition.destinationRef);
      if (remote !== candidate) {
        return {
          state: 'integrated-local',
          integratedCommit: candidate,
          pushError: remote === disposition.expectedRemoteOldOid || remote === null
            ? 'Remote has not proved the exact integrated candidate'
            : 'Remote outcome is ambiguous; preserving push-pending state',
        };
      }
      setDisposition(db, assignment.workspace_guid, null);
    } else if (worktreeHead(assignment.worktree_path) !== candidate) {
      throw new Error('Integrated assignment source HEAD differs from candidate; preserving worktree');
    }
    cleanupFinalized(db, exact.primaryCheckoutPath, assignment);
    return { state: 'cleaned', integratedCommit: assignment.integrated_commit ?? undefined };
  }
  if (assignment.lifecycle_status !== 'ready_for_integration') {
    throw new Error('No ready finalization is available for reconciliation');
  }
  const record = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(assignment.workspace_guid, assignment.repository_identity) as {
    target_ref: string;
    integrated_commit: string;
  } | undefined;
  if (!record) {
    const sourceHead = worktreeHead(assignment.worktree_path);
    const ref = targetRef(assignment);
    let candidate: string | undefined;
    try {
      candidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
    } catch { /* a lock/target race has no candidate yet */ }
    if (candidate && sourceHead !== candidate) {
      throw new Error('Crash reconciliation candidate and source HEAD differ; preserving worktree');
    }
    const rebaseDirectory = runGit(assignment.worktree_path, ['rev-parse', '--git-path', 'rebase-merge']).trim();
    if (existsSync(path.resolve(assignment.worktree_path, rebaseDirectory))) {
      throw new Error('Crash reconciliation requires reviewed rebase-conflict repair before retry');
    }
    // Crash may have happened after checkout-owned fast-forward but before
    // integration record. Target reachability is sufficient durable proof.
    if (candidate) {
      const currentTarget = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
      if (currentTarget !== candidate) {
        throw new Error('Crash reconciliation target is not the exact candidate; preserving worktree');
      }
      const expectedTarget = recoveryIntegrationLockExpectedTarget(db, assignment, ref);
      let integrationRecorded = false;
      try {
        fencePrimaryCheckout(db, assignment.repository_identity);
        requireExactIntegrationLock(db, assignment, ref, expectedTarget);
        try {
          verifyPrimaryAfterFastForward(exact.primaryCheckoutPath, ref, candidate);
        } catch {
          repairPrimaryCheckoutAfterInterruptedCas(exact.primaryCheckoutPath, ref, expectedTarget, candidate);
        }
        requireExactIntegrationLock(db, assignment, ref, expectedTarget);
        const frozen = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
        if (cumulativeBinaryEffect(assignment.worktree_path, assignment.base_commit, frozen)
          !== cumulativeBinaryEffect(assignment.worktree_path, expectedTarget, candidate)) {
          throw new Error('Crash reconciliation candidate effect differs from frozen review; preserving worktree');
        }
        const integrated = markIntegrated(db, assignment, candidate, ref);
        integrationRecorded = true;
        if (decodePushDisposition(integrated.disposition)) {
          return {
            state: 'integrated-local',
            integratedCommit: candidate,
            pushError: 'Remote has not proved the exact integrated candidate',
          };
        }
        cleanupFinalized(db, exact.primaryCheckoutPath, integrated);
        return { state: 'cleaned', integratedCommit: candidate };
      } finally {
        if (integrationRecorded) {
          releaseExactIntegrationLockIfHeld(db, assignment, ref, expectedTarget);
        }
      }
    }
    const frozen = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
    if (!candidate && sourceHead !== frozen) {
      throw new Error('Crash reconciliation requires fresh trusted human repair authority');
    }
    // `reset --hard` discards the index and every modified tracked file. The
    // sibling reset in finalizeAttestedCandidate guards this with the same
    // cleanliness check; without it, recovery from a crash destroys uncommitted
    // work, which the design forbids. When !candidate the throw above already
    // guarantees the ref cannot move, so the reset's only remaining effect
    // would have been that destruction.
    if (!worktreeIsClean(assignment.worktree_path)) {
      throw new Error(
        'Crash reconciliation refused: managed worktree has uncommitted changes, preserving worktree',
      );
    }
    runGit(assignment.worktree_path, ['reset', '--hard', frozen]);
    const local = continueFrozenFinalization(
      db, exact.primaryCheckoutPath, assignment, assignment.worktree_path, frozen,
    );
    return finishLocalIntegration(db, local);
  }
  const ref = targetRef(assignment);
  if (record.target_ref !== ref
    || runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim() !== record.integrated_commit) {
    throw new Error('Crash reconciliation lacks reachable integration proof; preserving worktree');
  }
  const candidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
  if (candidate !== record.integrated_commit || worktreeHead(assignment.worktree_path) !== candidate) {
    throw new Error('Crash reconciliation candidate/source proof differs; preserving worktree');
  }
  const integrationPending = decodeIntegrationPendingDisposition(assignment.disposition);
  const pushPending = decodePushDisposition(assignment.disposition);
  if (assignment.disposition && !integrationPending && !pushPending) {
    throw new Error('Crash reconciliation disposition is malformed; preserving worktree');
  }
  if (integrationPending || pushPending) {
    const frozen = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
    if ((integrationPending && integrationPending.frozenCommit !== frozen)
      || (pushPending && (pushPending.frozenCommit !== frozen || pushPending.candidateCommit !== candidate))) {
      throw new Error('Crash reconciliation pending push proof differs; preserving worktree');
    }
  }
  const nextDisposition = integrationPending
    ? encodePushDisposition({ ...integrationPending, phase: 'push-pending', candidateCommit: candidate })
    : pushPending ? encodePushDisposition(pushPending) : null;
  db.transaction(() => {
    const result = db.prepare(`
      UPDATE assignments SET integrated_commit = ?, current_head = ?, disposition = ?, updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'ready_for_integration'
    `).run(record.integrated_commit, record.integrated_commit, nextDisposition, assignment.workspace_guid);
    if (result.changes !== 1) throw new Error('Crash reconciliation assignment state changed concurrently');
    transitionAssignment(db, assignment.workspace_guid, 'ready_for_integration', 'integrated');
  })();
  const integrated = getAssignment(db, assignment.workspace_guid)!;
  if (nextDisposition) {
    return {
      state: 'integrated-local',
      integratedCommit: record.integrated_commit,
      pushError: 'Remote has not proved the exact integrated candidate',
    };
  }
  cleanupFinalized(db, exact.primaryCheckoutPath, integrated);
  return { state: 'cleaned', integratedCommit: record.integrated_commit };
}
