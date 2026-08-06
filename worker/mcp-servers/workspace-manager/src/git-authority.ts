import type Database from 'better-sqlite3';
import path from 'node:path';
import { consumeHumanIntent, consumeMatchingHumanIntent, getAssignment, issueHumanIntent } from './db.js';
import { discoverRepository, listWorktrees, runGit } from './git.js';
import type { Assignment, HumanIntentReceipt } from './types.js';

export type DirectGitOperation = 'commit' | 'commit-and-push' | 'push';
export type DirectGitCheckoutMode = 'managed' | 'primary';

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

export type DirectGitAuthorityEvidence = CommitEvidence | CommitAndPushEvidence | PushEvidence;

export interface VerifyDirectGitAuthorityInput {
  repositoryPath: string;
  workspaceGuid: string;
  providerRootSessionId: string;
  humanChannel: string;
  operation: DirectGitOperation;
  expectedEvidence?: DirectGitAuthorityEvidence;
  nonce?: string;
}

export interface IssueDirectGitHumanIntentInput {
  repositoryPath: string;
  workspaceGuid: string;
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

function resolveEffectiveCheckout(db: Database.Database, input: VerifyDirectGitAuthorityInput): EffectiveCheckout {
  const repository = discoverRepository(input.repositoryPath);
  const assignment = getAssignment(db, input.workspaceGuid);
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
    throw new Error('Direct Git authority primary checkout is owned by another assignment or provider root');
  }
  return { assignment, mode: 'primary', path: repository.primaryCheckoutPath };
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
  const checkout = resolveEffectiveCheckout(db, input);
  const evidence = observeDirectEvidence(checkout, input.operation);
  return issueHumanIntent(db, {
    operation: input.operation,
    humanChannel: input.humanChannel,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: checkout.assignment.repository_identity,
    workspaceGuid: checkout.assignment.workspace_guid,
    expectedEvidence: evidence,
  });
}

/** Validates present operation-specific Git state, then consumes one intent. */
export function verifyDirectGitAuthority(
  db: Database.Database,
  input: VerifyDirectGitAuthorityInput,
): AuthorizedDirectGitOperation {
  const checkout = resolveEffectiveCheckout(db, input);
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
  } else {
    throw new Error('Direct Git authority operation is not allowed');
  }
  const intentInput = {
    operation: input.operation,
    humanChannel: input.humanChannel,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    expectedEvidence: evidence,
  };
  const intent = suppliedLegacyEvidence
    ? consumeHumanIntent(db, { ...intentInput, nonce: input.nonce! })
    : consumeMatchingHumanIntent(db, intentInput);
  if (!intent) throw new Error('Direct Git operation requires a matching human intent');
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
  if (input.operation !== 'commit') usablePushAuthorizations.add(authority);
  return authority;
}

/**
 * Task 4's narrow pre-commit seam. It cannot execute Git; it only proves the
 * authorized effective checkout has not changed ownership, path, or branch.
 */
export function revalidateAuthorizedCommitState(authority: AuthorizedDirectGitOperation): void {
  const db = authorityDatabases.get(authority);
  if (!db) throw new Error('Direct Git authority is not recognized');
  const checkout = resolveEffectiveCheckout(db, {
    repositoryPath: authority.worktreePath,
    workspaceGuid: authority.workspaceGuid,
    providerRootSessionId: authority.providerRootSessionId,
    humanChannel: 'internal-revalidation',
    operation: authority.operation,
    expectedEvidence: authority.evidence,
    nonce: 'internal-revalidation',
  });
  if (checkout.mode !== authority.checkoutMode || checkout.path !== authority.worktreePath) {
    throw new Error('Direct Git authority effective checkout changed');
  }
  try {
    const branch = runGit(checkout.path, ['symbolic-ref', '--quiet', '--short', 'HEAD']).trim();
    runGit(checkout.path, ['rev-parse', '--verify', `${authority.evidence.localRef}^{commit}`]);
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
