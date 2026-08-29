import type Database from 'better-sqlite3';
import path from 'node:path';
import { consumeHumanIntent, consumeMatchingHumanIntent, getAssignment, issueHumanIntent } from './db.js';
import { discoverRepository, isAncestor, listWorktrees, runGit } from './git.js';
import type { Assignment, HumanIntentReceipt } from './types.js';

export type DirectGitOperation = 'commit' | 'commit-and-push' | 'push' | 'reconcile' | 'close-out' | 'confirm-resolution';
export type DirectGitCheckoutMode = 'managed' | 'primary' | 'primary-unassigned';

interface BranchEvidence {
  checkoutMode: DirectGitCheckoutMode;
  canonicalBranch: string;
  localRef: string;
}

export interface CommitEvidence extends BranchEvidence {
  stagedTree: string;
  parentRef: 'HEAD';
  parentOid: string;
}

interface RemoteEvidence extends BranchEvidence {
  remoteName: string;
  remoteUrl: string;
  destinationRef: string;
  expectedRemoteOldOid: string | null;
}

export interface CommitAndPushEvidence extends CommitEvidence, RemoteEvidence {}

export interface PushEvidence extends RemoteEvidence {
  localOid: string;
}

export interface ReconcileEvidence extends BranchEvidence {
  checkoutMode: 'managed';
  headOid: string;
}

export type DirectGitAuthorityEvidence = CommitEvidence | CommitAndPushEvidence | PushEvidence | ReconcileEvidence;

export interface VerifyDirectGitAuthorityInput {
  repositoryPath: string;
  workspaceGuid?: string;
  providerRootSessionId: string;
  humanChannel: string;
  operation: DirectGitOperation;
  expectedEvidence?: DirectGitAuthorityEvidence;
  nonce?: string;
}

export interface IssueDirectGitHumanIntentInput {
  repositoryPath: string;
  workspaceGuid?: string;
  providerRootSessionId: string;
  humanChannel: string;
  operation: DirectGitOperation;
}

export interface AuthorizedDirectGitOperation {
  readonly operation: DirectGitOperation;
  readonly providerRootSessionId: string;
  readonly repositoryIdentity: string;
  readonly workspaceGuid: string;
  readonly checkoutMode: DirectGitCheckoutMode;
  readonly worktreePath: string;
  readonly evidence: DirectGitAuthorityEvidence;
}

const OID = /^[0-9a-f]{40,64}$/i;
const REMOTE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const REF = /^refs\/heads\/[A-Za-z0-9][A-Za-z0-9._/-]*$/;
const usablePushAuthorizations = new WeakSet<object>();
const authorityDatabases = new WeakMap<object, Database.Database>();

function denyEvidence(): never {
  throw new Error('Direct Git authority evidence changed or is malformed');
}

function requiredText(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) denyEvidence();
  return value;
}

function oid(value: unknown, nullable = false): string | null {
  if (value === null && nullable) return null;
  const text = requiredText(value);
  if (!OID.test(text)) denyEvidence();
  return text;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  if (actual.length !== sorted.length || actual.some((key, index) => key !== sorted[index])) denyEvidence();
}

function branchEvidence(
  value: Record<string, unknown>,
  assignment: Assignment,
  checkoutMode: DirectGitCheckoutMode,
): BranchEvidence {
  const mode = requiredText(value.checkoutMode);
  const canonicalBranch = requiredText(value.canonicalBranch);
  const localRef = requiredText(value.localRef);
  if ((mode !== 'managed' && mode !== 'primary')
    || mode !== checkoutMode
    || localRef !== `refs/heads/${canonicalBranch}`
    || !REF.test(localRef)
    || (checkoutMode === 'managed' && canonicalBranch !== assignment.branch)) {
    denyEvidence();
  }
  return { checkoutMode, canonicalBranch, localRef };
}

function commitEvidence(value: unknown, assignment: Assignment, checkoutMode: DirectGitCheckoutMode): CommitEvidence {
  if (!value || typeof value !== 'object' || Array.isArray(value)) denyEvidence();
  const source = value as Record<string, unknown>;
  exactKeys(source, ['checkoutMode', 'canonicalBranch', 'stagedTree', 'parentRef', 'parentOid', 'localRef']);
  const branch = branchEvidence(source, assignment, checkoutMode);
  const stagedTree = oid(source.stagedTree)!;
  const parentRef = requiredText(source.parentRef);
  const parentOid = oid(source.parentOid)!;
  if (parentRef !== 'HEAD') denyEvidence();
  return { ...branch, stagedTree, parentRef: 'HEAD', parentOid };
}

function reconcileEvidence(value: unknown, assignment: Assignment, checkoutMode: DirectGitCheckoutMode): ReconcileEvidence {
  if (!value || typeof value !== 'object' || Array.isArray(value)) denyEvidence();
  const source = value as Record<string, unknown>;
  exactKeys(source, ['checkoutMode', 'canonicalBranch', 'localRef', 'headOid']);
  const branch = branchEvidence(source, assignment, checkoutMode);
  const headOid = oid(source.headOid)!;
  if (checkoutMode !== 'managed') denyEvidence();
  return { ...branch, checkoutMode: 'managed', headOid };
}

/**
 * HEAD-INDEPENDENT intent-match key for close-out. Close-out's intent is minted at
 * prompt time — possibly on a detached HEAD (a paused rebase) or an integrated row —
 * so it observes NO commit/HEAD state. Both issuance and the verify-time consume use
 * this exact value (headOid '' is a sentinel, never a real oid), so their canonicalJson
 * match holds even though the verb's recovery deliberately moves HEAD before verify.
 * The AUTHORITY's real commit is observed live at verify (see verifyDirectGitAuthority).
 */
function closeOutIntentEvidence(assignment: Assignment): ReconcileEvidence {
  return {
    checkoutMode: 'managed',
    canonicalBranch: assignment.branch,
    localRef: `refs/heads/${assignment.branch}`,
    headOid: '',
  };
}

function remoteEvidence(value: Record<string, unknown>, assignment: Assignment, checkoutMode: DirectGitCheckoutMode): RemoteEvidence {
  const branch = branchEvidence(value, assignment, checkoutMode);
  const remoteName = requiredText(value.remoteName);
  const remoteUrl = requiredText(value.remoteUrl);
  const destinationRef = requiredText(value.destinationRef);
  const expectedRemoteOldOid = oid(value.expectedRemoteOldOid, true);
  if (!REMOTE_NAME.test(remoteName) || !REF.test(destinationRef)) denyEvidence();
  return { ...branch, remoteName, remoteUrl, destinationRef, expectedRemoteOldOid };
}

function commitAndPushEvidence(value: unknown, assignment: Assignment, checkoutMode: DirectGitCheckoutMode): CommitAndPushEvidence {
  if (!value || typeof value !== 'object' || Array.isArray(value)) denyEvidence();
  const source = value as Record<string, unknown>;
  exactKeys(source, [
    'checkoutMode', 'canonicalBranch', 'stagedTree', 'parentRef', 'parentOid', 'localRef',
    'remoteName', 'remoteUrl', 'destinationRef', 'expectedRemoteOldOid',
  ]);
  const commit = commitEvidence({
    checkoutMode: source.checkoutMode,
    canonicalBranch: source.canonicalBranch,
    stagedTree: source.stagedTree,
    parentRef: source.parentRef,
    parentOid: source.parentOid,
    localRef: source.localRef,
  }, assignment, checkoutMode);
  const remote = remoteEvidence(source, assignment, checkoutMode);
  return { ...commit, ...remote };
}

function pushEvidence(value: unknown, assignment: Assignment, checkoutMode: DirectGitCheckoutMode): PushEvidence {
  if (!value || typeof value !== 'object' || Array.isArray(value)) denyEvidence();
  const source = value as Record<string, unknown>;
  exactKeys(source, [
    'checkoutMode', 'canonicalBranch', 'localRef', 'localOid', 'remoteName', 'remoteUrl', 'destinationRef', 'expectedRemoteOldOid',
  ]);
  const branch = branchEvidence(source, assignment, checkoutMode);
  const remote = remoteEvidence(source, assignment, checkoutMode);
  const localOid = oid(source.localOid)!;
  return { ...branch, ...remote, localOid };
}

function remoteOldOid(worktreePath: string, remoteName: string, destinationRef: string): string | null {
  const output = runGit(worktreePath, ['ls-remote', '--refs', remoteName, destinationRef]).trim();
  if (output === '') return null;
  const lines = output.split('\n');
  if (lines.length !== 1) denyEvidence();
  const [remoteOid, remoteRef, ...extra] = lines[0].split(/\s+/);
  if (extra.length !== 0 || remoteRef !== destinationRef || !OID.test(remoteOid)) denyEvidence();
  return remoteOid;
}

function integrationDestinationRef(assignment: Assignment): string {
  return assignment.integration_target.startsWith('refs/')
    ? assignment.integration_target
    : `refs/heads/${assignment.integration_target}`;
}

function assertCommitPreState(worktreePath: string, evidence: CommitEvidence): void {
  try {
    const branch = runGit(worktreePath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
    const tree = runGit(worktreePath, ['write-tree']).trim();
    const parent = runGit(worktreePath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
    const local = runGit(worktreePath, ['rev-parse', '--verify', `${evidence.localRef}^{commit}`]).trim();
    if (branch !== evidence.canonicalBranch || tree !== evidence.stagedTree || parent !== evidence.parentOid || local !== parent) {
      denyEvidence();
    }
  } catch (error) {
    if (error instanceof Error && error.message === 'Direct Git authority evidence changed or is malformed') throw error;
    denyEvidence();
  }
}

function assertRemoteEvidence(worktreePath: string, evidence: RemoteEvidence): void {
  try {
    const remoteUrl = runGit(worktreePath, ['remote', 'get-url', evidence.remoteName]).trim();
    const pushUrl = runGit(worktreePath, ['remote', 'get-url', '--push', evidence.remoteName]).trim();
    if (remoteUrl !== evidence.remoteUrl
      || pushUrl !== evidence.remoteUrl
      || remoteOldOid(worktreePath, evidence.remoteName, evidence.destinationRef) !== evidence.expectedRemoteOldOid) {
      denyEvidence();
    }
  } catch (error) {
    if (error instanceof Error && error.message === 'Direct Git authority evidence changed or is malformed') throw error;
    denyEvidence();
  }
}

function assertPushState(worktreePath: string, evidence: PushEvidence): void {
  try {
    const branch = runGit(worktreePath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
    const local = runGit(worktreePath, ['rev-parse', '--verify', `${evidence.localRef}^{commit}`]).trim();
    if (branch !== evidence.canonicalBranch || local !== evidence.localOid) denyEvidence();
    assertRemoteEvidence(worktreePath, evidence);
  } catch (error) {
    if (error instanceof Error && error.message === 'Direct Git authority evidence changed or is malformed') throw error;
    denyEvidence();
  }
}

function assertPostCommitPushState(worktreePath: string, evidence: CommitAndPushEvidence): string {
  try {
    const branch = runGit(worktreePath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
    const newOid = runGit(worktreePath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
    const localOid = runGit(worktreePath, ['rev-parse', '--verify', `${evidence.localRef}^{commit}`]).trim();
    const tree = runGit(worktreePath, ['rev-parse', '--verify', 'HEAD^{tree}']).trim();
    const parents = runGit(worktreePath, ['rev-list', '--parents', '-n', '1', 'HEAD']).trim().split(/\s+/);
    if (branch !== evidence.canonicalBranch
      || localOid !== newOid
      || tree !== evidence.stagedTree
      || parents.length !== 2
      || parents[0] !== newOid
      || parents[1] !== evidence.parentOid) {
      denyEvidence();
    }
    assertRemoteEvidence(worktreePath, evidence);
    return newOid;
  } catch (error) {
    if (error instanceof Error && error.message === 'Direct Git authority evidence changed or is malformed') throw error;
    denyEvidence();
  }
}

interface EffectiveCheckout {
  assignment: Assignment;
  mode: DirectGitCheckoutMode;
  path: string;
}

function resolveEffectiveCheckout(db: Database.Database, input: VerifyDirectGitAuthorityInput, workspaceGuid: string): EffectiveCheckout {
  const repository = discoverRepository(input.repositoryPath);
  const assignment = getAssignment(db, workspaceGuid);
  if (!assignment
    || assignment.repository_identity !== repository.repositoryIdentity
    || assignment.owner_session_id !== input.providerRootSessionId) {
    throw new Error('Direct Git authority provider root, repository, or workspace binding does not match');
  }
  const expectedPath = path.join(repository.primaryCheckoutPath, '.ironclaude', 'worktrees', assignment.workspace_guid);
  if (assignment.worktree_path !== expectedPath || assignment.branch !== `ironclaude/${assignment.workspace_guid}`) {
    throw new Error('Direct Git authority managed workspace identity does not match');
  }
  const worktree = listWorktrees(repository.primaryCheckoutPath)
    .find((candidate) => candidate.path === assignment.worktree_path);
  if (!worktree || worktree.branch !== `refs/heads/${assignment.branch}`) {
    throw new Error('Direct Git authority managed workspace Git identity does not match');
  }
  const primaryOwner = db.prepare(`
    SELECT workspace_guid, owner_session_id FROM primary_checkout_owners
    WHERE repository_identity = ?
  `).get(repository.repositoryIdentity) as { workspace_guid: string; owner_session_id: string } | undefined;
  if (!primaryOwner) return { assignment, mode: 'managed', path: assignment.worktree_path };
  if (primaryOwner.workspace_guid !== assignment.workspace_guid
    || primaryOwner.owner_session_id !== input.providerRootSessionId) {
    return { assignment, mode: 'managed', path: assignment.worktree_path };
  }
  return { assignment, mode: 'primary', path: repository.primaryCheckoutPath };
}

/**
 * Resolves the primary checkout as a direct-Git target for a session that holds
 * no managed assignment at all — used by the unassigned commit AND push lanes.
 * Re-proves the lane's preconditions (zero active assignments, primary ownership,
 * a checked-out branch); callable both at issuance/verify and at push-time
 * revalidation so a change after verify is caught.
 */
export function resolveUnassignedPrimaryCheckout(
  db: Database.Database,
  repositoryPath: string,
  providerRootSessionId: string,
): { mode: 'primary-unassigned'; path: string } {
  const repository = discoverRepository(repositoryPath);
  const active = db.prepare(`
    SELECT COUNT(*) AS n FROM assignments
    WHERE repository_identity = ? AND owner_session_id = ?
      AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
  `).get(repository.repositoryIdentity, providerRootSessionId) as { n: number };
  if (active.n !== 0) throw new Error('Unassigned-primary direct-Git requires zero active assignments for this session and repository');
  const primaryOwner = db.prepare(`
    SELECT owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?
  `).get(repository.repositoryIdentity) as { owner_session_id: string } | undefined;
  if (primaryOwner && primaryOwner.owner_session_id !== providerRootSessionId) {
    throw new Error('Primary checkout is owned by another session');
  }
  let branch: string;
  try {
    branch = runGit(repository.primaryCheckoutPath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
  } catch {
    throw new Error('Unassigned-primary direct-Git requires a checked-out branch (HEAD is detached)');
  }
  if (branch.length === 0) throw new Error('Unassigned-primary direct-Git requires a checked-out branch (HEAD is detached)');
  return { mode: 'primary-unassigned', path: repository.primaryCheckoutPath };
}

function observeUnassignedCommitEvidence(path: string): DirectGitAuthorityEvidence {
  const canonicalBranch = runGit(path, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  return {
    checkoutMode: 'primary-unassigned',
    canonicalBranch,
    localRef,
    stagedTree: runGit(path, ['write-tree']).trim(),
    parentRef: 'HEAD',
    parentOid: runGit(path, ['rev-parse', '--verify', 'HEAD^{commit}']).trim(),
  };
}

/**
 * Fast-forward-only proof for the unassigned-primary push lane. The operator is
 * pushing their own current branch to its own remote ref; a lease-matched
 * non-fast-forward would rewrite a shared branch (e.g. main). A null
 * expectedRemoteOldOid means the remote ref does not yet exist (a new branch) —
 * nothing to rewrite. Evaluated LIVE at issuance/verify (not on frozen evidence).
 */
export function assertFastForwardPush(worktreePath: string, evidence: PushEvidence): void {
  if (evidence.expectedRemoteOldOid === null) return;
  if (!isAncestor(worktreePath, evidence.expectedRemoteOldOid, evidence.localOid)) {
    throw new Error('Unassigned-primary push must be fast-forward; non-fast-forward to a shared branch is refused');
  }
}

function observeUnassignedPushEvidence(path: string): PushEvidence {
  const canonicalBranch = runGit(path, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const remoteName = 'origin';
  const remoteUrl = runGit(path, ['remote', 'get-url', remoteName]).trim();
  const pushUrl = runGit(path, ['remote', 'get-url', '--push', remoteName]).trim();
  if (remoteUrl !== pushUrl) denyEvidence();
  const evidence: PushEvidence = {
    checkoutMode: 'primary-unassigned',
    canonicalBranch,
    localRef,
    localOid: runGit(path, ['rev-parse', '--verify', `${localRef}^{commit}`]).trim(),
    remoteName,
    remoteUrl,
    destinationRef: localRef,
    expectedRemoteOldOid: remoteOldOid(path, remoteName, localRef),
  };
  assertFastForwardPush(path, evidence);
  return evidence;
}

function observeUnassignedCommitAndPushEvidence(path: string): CommitAndPushEvidence {
  const canonicalBranch = runGit(path, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const remoteName = 'origin';
  const remoteUrl = runGit(path, ['remote', 'get-url', remoteName]).trim();
  const pushUrl = runGit(path, ['remote', 'get-url', '--push', remoteName]).trim();
  if (remoteUrl !== pushUrl) denyEvidence();
  const parentOid = runGit(path, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
  const expectedRemoteOldOid = remoteOldOid(path, remoteName, localRef);
  // The pushed commit is created at finalize as a CHILD of parentOid, so
  // fast-forward-over-parent implies fast-forward-over-child. The push evidence
  // carries no localOid yet (the commit does not exist), so prove ff over the
  // parent here instead of reusing assertFastForwardPush (which keys on localOid).
  if (expectedRemoteOldOid !== null && !isAncestor(path, expectedRemoteOldOid, parentOid)) {
    throw new Error('Unassigned-primary push must be fast-forward; non-fast-forward to a shared branch is refused');
  }
  return {
    checkoutMode: 'primary-unassigned',
    canonicalBranch,
    localRef,
    stagedTree: runGit(path, ['write-tree']).trim(),
    parentRef: 'HEAD',
    parentOid,
    remoteName,
    remoteUrl,
    destinationRef: localRef,
    expectedRemoteOldOid,
  };
}

function observeDirectEvidence(checkout: EffectiveCheckout, operation: DirectGitOperation): DirectGitAuthorityEvidence {
  const { assignment } = checkout;
  const canonicalBranch = runGit(checkout.path, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const branch = { checkoutMode: checkout.mode, canonicalBranch, localRef };
  if (checkout.mode === 'managed' && canonicalBranch !== assignment.branch) denyEvidence();

  if (operation === 'commit') {
    return {
      ...branch,
      stagedTree: runGit(checkout.path, ['write-tree']).trim(),
      parentRef: 'HEAD',
      parentOid: runGit(checkout.path, ['rev-parse', '--verify', 'HEAD^{commit}']).trim(),
    };
  }

  if (operation === 'reconcile' || operation === 'close-out' || operation === 'confirm-resolution') {
    if (checkout.mode !== 'managed') denyEvidence();
    return {
      ...branch,
      checkoutMode: 'managed',
      headOid: runGit(checkout.path, ['rev-parse', '--verify', 'HEAD^{commit}']).trim(),
    };
  }

  const remoteName = 'origin';
  const remoteUrl = runGit(checkout.path, ['remote', 'get-url', remoteName]).trim();
  const pushUrl = runGit(checkout.path, ['remote', 'get-url', '--push', remoteName]).trim();
  if (remoteUrl !== pushUrl) denyEvidence();
  const destinationRef = operation === 'commit-and-push'
    ? integrationDestinationRef(assignment)
    : localRef;
  const remote = {
    remoteName,
    remoteUrl,
    destinationRef,
    expectedRemoteOldOid: remoteOldOid(checkout.path, remoteName, destinationRef),
  };
  if (operation === 'commit-and-push') {
    return {
      ...branch,
      stagedTree: runGit(checkout.path, ['write-tree']).trim(),
      parentRef: 'HEAD',
      parentOid: runGit(checkout.path, ['rev-parse', '--verify', 'HEAD^{commit}']).trim(),
      ...remote,
    };
  }
  if (operation === 'push') {
    return {
      ...branch,
      localOid: runGit(checkout.path, ['rev-parse', '--verify', `${localRef}^{commit}`]).trim(),
      ...remote,
    };
  }
  throw new Error('Direct Git authority operation is not allowed');
}

export function issueDirectGitHumanIntent(
  db: Database.Database,
  input: IssueDirectGitHumanIntentInput,
): HumanIntentReceipt {
  if (input.workspaceGuid === undefined) {
    if (input.operation !== 'commit' && input.operation !== 'push' && input.operation !== 'commit-and-push') {
      throw new Error('Unassigned-primary lane supports commit, push, and commit-and-push only');
    }
    const repository = discoverRepository(input.repositoryPath);
    const unassigned = resolveUnassignedPrimaryCheckout(db, input.repositoryPath, input.providerRootSessionId);
    const evidence = input.operation === 'commit'
      ? observeUnassignedCommitEvidence(unassigned.path)
      : input.operation === 'push'
        ? observeUnassignedPushEvidence(unassigned.path)
        : observeUnassignedCommitAndPushEvidence(unassigned.path);
    return issueHumanIntent(db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.providerRootSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: `primary:${repository.repositoryIdentity}`,
      expectedEvidence: evidence,
    });
  }
  const checkout = resolveEffectiveCheckout(db, input, input.workspaceGuid);
  const assignment = checkout.assignment;
  if (input.operation === 'close-out') {
    // Evidence-light: bind branch identity only, observe NO commit/HEAD state, so the
    // intent mints even on a detached-HEAD (paused-rebase) or integrated row.
    return issueHumanIntent(db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.providerRootSessionId,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence: closeOutIntentEvidence(assignment),
    });
  }
  const evidence = observeDirectEvidence(checkout, input.operation);
  return issueHumanIntent(db, {
    operation: input.operation,
    humanChannel: input.humanChannel,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    expectedEvidence: evidence,
  });
}

/** Validates present operation-specific Git state, then consumes one intent. */
export function verifyDirectGitAuthority(
  db: Database.Database,
  input: VerifyDirectGitAuthorityInput,
): AuthorizedDirectGitOperation {
  if (input.workspaceGuid === undefined) {
    if (input.operation !== 'commit' && input.operation !== 'push' && input.operation !== 'commit-and-push') {
      throw new Error('Unassigned-primary lane supports commit, push, and commit-and-push only');
    }
    const repository = discoverRepository(input.repositoryPath);
    const unassigned = resolveUnassignedPrimaryCheckout(db, input.repositoryPath, input.providerRootSessionId);
    const evidence = input.operation === 'commit'
      ? observeUnassignedCommitEvidence(unassigned.path)
      : input.operation === 'push'
        ? observeUnassignedPushEvidence(unassigned.path)
        : observeUnassignedCommitAndPushEvidence(unassigned.path);
    const sentinel = `primary:${repository.repositoryIdentity}`;
    const intent = consumeMatchingHumanIntent(db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.providerRootSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: sentinel,
      expectedEvidence: evidence,
    });
    if (!intent) throw new Error('Direct Git operation requires a matching human intent — the operator must invoke the rendered git form (/commit, /commit-and-push, or /push) as their literal prompt; free-text prose does not mint intent');
    const authority: AuthorizedDirectGitOperation = {
      operation: input.operation,
      providerRootSessionId: input.providerRootSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: sentinel,
      checkoutMode: 'primary-unassigned',
      worktreePath: unassigned.path,
      evidence,
    };
    Object.freeze(evidence);
    Object.freeze(authority);
    authorityDatabases.set(authority, db);
    if (input.operation !== 'commit') usablePushAuthorizations.add(authority);
    return authority;
  }
  const checkout = resolveEffectiveCheckout(db, input, input.workspaceGuid);
  const assignment = checkout.assignment;
  const suppliedLegacyEvidence = input.expectedEvidence !== undefined || input.nonce !== undefined;
  if ((input.expectedEvidence === undefined) !== (input.nonce === undefined)) denyEvidence();
  let evidence: DirectGitAuthorityEvidence;
  if (!suppliedLegacyEvidence) {
    evidence = observeDirectEvidence(checkout, input.operation);
  } else if (input.operation === 'commit') {
    evidence = commitEvidence(input.expectedEvidence, assignment, checkout.mode);
    assertCommitPreState(checkout.path, evidence);
  } else if (input.operation === 'commit-and-push') {
    evidence = commitAndPushEvidence(input.expectedEvidence, assignment, checkout.mode);
    assertCommitPreState(checkout.path, evidence);
    assertRemoteEvidence(checkout.path, evidence as CommitAndPushEvidence);
  } else if (input.operation === 'push') {
    evidence = pushEvidence(input.expectedEvidence, assignment, checkout.mode);
    assertPushState(checkout.path, evidence);
  } else if (input.operation === 'reconcile' || input.operation === 'close-out' || input.operation === 'confirm-resolution') {
    evidence = reconcileEvidence(input.expectedEvidence, assignment, checkout.mode);
  } else {
    throw new Error('Direct Git authority operation is not allowed');
  }
  // Close-out's intent is EVIDENCE-LIGHT: it was minted with a HEAD-independent
  // branch-only key (closeOutIntentEvidence). The authority binds the live commit
  // observed above, but the intent must be consumed by the branch-only match key
  // (the observe path's live headOid would never equal the '' issued at prompt time).
  const matchEvidence = (input.operation === 'close-out' && !suppliedLegacyEvidence)
    ? closeOutIntentEvidence(assignment)
    : evidence;
  const intentInput = {
    operation: input.operation,
    humanChannel: input.humanChannel,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    expectedEvidence: matchEvidence,
  };
  const intent = suppliedLegacyEvidence
    ? consumeHumanIntent(db, { ...intentInput, nonce: input.nonce! })
    : consumeMatchingHumanIntent(db, intentInput);
  if (!intent) throw new Error('Direct Git operation requires a matching human intent — the operator must invoke the rendered git form (/commit, /commit-and-push, or /push) as their literal prompt; free-text prose does not mint intent');
  const authority: AuthorizedDirectGitOperation = {
    operation: input.operation,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    checkoutMode: checkout.mode,
    worktreePath: checkout.path,
    evidence,
  };
  Object.freeze(evidence);
  Object.freeze(authority);
  authorityDatabases.set(authority, db);
  if (input.operation !== 'commit' && input.operation !== 'reconcile' && input.operation !== 'close-out' && input.operation !== 'confirm-resolution') usablePushAuthorizations.add(authority);
  return authority;
}

/**
 * Task 4's narrow pre-commit seam. It cannot execute Git; it only proves the
 * authorized effective checkout has not changed ownership, path, or branch.
 */
export function revalidateAuthorizedCommitState(authority: AuthorizedDirectGitOperation): void {
  const db = authorityDatabases.get(authority);
  if (!db) throw new Error('Direct Git authority is not recognized');
  let checkoutPath: string;
  if (authority.checkoutMode === 'primary-unassigned') {
    // The unassigned lane has no assignment row to resolve. Re-prove the same
    // preconditions the sentinel was issued under — zero active assignments,
    // primary ownership, a checked-out branch — LIVE at push time, so an
    // assignment or a foreign primary owner that appeared after verify blocks
    // the push. (resolveEffectiveCheckout requires an assignment and would throw.)
    const unassigned = resolveUnassignedPrimaryCheckout(db, authority.worktreePath, authority.providerRootSessionId);
    if (unassigned.path !== authority.worktreePath) {
      throw new Error('Direct Git authority effective checkout changed');
    }
    checkoutPath = unassigned.path;
  } else {
    const checkout = resolveEffectiveCheckout(db, {
      repositoryPath: authority.worktreePath,
      workspaceGuid: authority.workspaceGuid,
      providerRootSessionId: authority.providerRootSessionId,
      humanChannel: 'internal-revalidation',
      operation: authority.operation,
      expectedEvidence: authority.evidence,
      nonce: 'internal-revalidation',
    }, authority.workspaceGuid);
    if (checkout.mode !== authority.checkoutMode || checkout.path !== authority.worktreePath) {
      throw new Error('Direct Git authority effective checkout changed');
    }
    checkoutPath = checkout.path;
  }
  try {
    const branch = runGit(checkoutPath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
    runGit(checkoutPath, ['rev-parse', '--verify', `${authority.evidence.localRef}^{commit}`]);
    if (branch !== authority.evidence.canonicalBranch) denyEvidence();
  } catch (error) {
    if (error instanceof Error && error.message === 'Direct Git authority evidence changed or is malformed') throw error;
    denyEvidence();
  }
}

/** Single-use exact-ref push with a destination-specific remote-old-OID lease. */
export function pushExactAuthorizedRef(authority: AuthorizedDirectGitOperation): void {
  if (authority.operation === 'commit') throw new Error('Commit authority does not authorize a push');
  if (!usablePushAuthorizations.has(authority)) throw new Error('Direct Git push authority is single-use');
  if (authority.operation === 'commit-and-push') {
    const db = authorityDatabases.get(authority);
    const assignment = db ? getAssignment(db, authority.workspaceGuid) : undefined;
    if (assignment?.lifecycle_status === 'integrated' && assignment.integrated_commit) {
      throw new Error('Commit-and-push authority requires exact integrated candidate push');
    }
  }
  usablePushAuthorizations.delete(authority);
  revalidateAuthorizedCommitState(authority);
  const evidence = authority.operation === 'push'
    ? authority.evidence as PushEvidence
    : authority.evidence as CommitAndPushEvidence;
  const localOid = authority.operation === 'push'
    ? (assertPushState(authority.worktreePath, evidence as PushEvidence), (evidence as PushEvidence).localOid)
    : assertPostCommitPushState(authority.worktreePath, evidence as CommitAndPushEvidence);
  const lease = `${evidence.destinationRef}:${evidence.expectedRemoteOldOid ?? ''}`;
  runGit(authority.worktreePath, [
    'push',
    '--porcelain',
    `--force-with-lease=${lease}`,
    evidence.remoteUrl,
    `${localOid}:${evidence.destinationRef}`,
  ]);
}

/**
 * Internal finalization extension for a commit-and-push authority. The
 * coordinator may call this only after it has proven the integrated candidate
 * preserves the reviewed cumulative effect. Remote bindings and the one-use
 * lease remain the original human-authorized values.
 */
export function pushExactAuthorizedIntegratedCandidate(
  authority: AuthorizedDirectGitOperation,
  candidateOid: string,
  targetRef: string,
): void {
  if (authority.operation !== 'commit-and-push') throw new Error('Only commit-and-push authority can push an integrated candidate');
  if (!usablePushAuthorizations.has(authority)) throw new Error('Direct Git push authority is single-use');
  const db = authorityDatabases.get(authority);
  if (!db) throw new Error('Direct Git authority is not recognized');
  const assignment = getAssignment(db, authority.workspaceGuid);
  const evidence = authority.evidence as CommitAndPushEvidence;
  const durable = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(authority.workspaceGuid, authority.repositoryIdentity) as { target_ref: string; integrated_commit: string } | undefined;
  const candidateRef = `refs/ironclaude/finalization/${authority.workspaceGuid}/candidate`;
  const durableCandidate = runGit(authority.worktreePath, ['rev-parse', '--verify', `${candidateRef}^{commit}`]).trim();
  if (!assignment || assignment.lifecycle_status !== 'integrated'
    || assignment.integrated_commit !== candidateOid
    || !durable || durable.target_ref !== targetRef || durable.integrated_commit !== candidateOid
    || durableCandidate !== candidateOid) {
    throw new Error('Integrated candidate evidence does not match durable finalization proof');
  }
  usablePushAuthorizations.delete(authority);
  revalidateAuthorizedCommitState(authority);
  assertRemoteEvidence(authority.worktreePath, evidence);
  const local = runGit(authority.worktreePath, ['rev-parse', '--verify', `${evidence.localRef}^{commit}`]).trim();
  if (local !== candidateOid || !OID.test(candidateOid)) {
    throw new Error('Integrated candidate does not match authorized local ref');
  }
  const lease = `${evidence.destinationRef}:${evidence.expectedRemoteOldOid ?? ''}`;
  runGit(authority.worktreePath, [
    'push', '--porcelain', `--force-with-lease=${lease}`, evidence.remoteUrl,
    `${candidateOid}:${evidence.destinationRef}`,
  ]);
}
