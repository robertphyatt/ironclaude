import type Database from 'better-sqlite3';
import { existsSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  acquireIntegrationLock,
  deleteIntegrationRecord,
  getAssignment,
  insertPreservedWork,
  recordIntegration,
  resolvePreservedWork,
  transitionAssignment,
} from './db.js';
import {
  carryForwardFastForward,
  changedPaths,
  deleteTemporaryBranch,
  dirtyAndUntrackedPaths,
  discoverRepository,
  isAncestor,
  removeWorktree,
  runGit,
  runGitEnv,
  worktreeHead,
  worktreeIsClean,
} from './git.js';
import {
  pushExactAuthorizedRef,
  pushExactAuthorizedIntegratedCandidate,
  revalidateAuthorizedCommitState,
  type AuthorizedDirectGitOperation,
  type ReconcileEvidence,
} from './git-authority.js';
import type { Assignment } from './types.js';

export interface FinalizationResult {
  state: 'cleaned' | 'pushed' | 'pushed-only' | 'integrated-local'
    | 'rebase-aborted' | 'rebase-recovery-repair-required'
    | 'rebase-rerebased-ready-for-repair' | 'rebase-frozen-restored'
    | 'rebase-paused-conflict' | 'rebase-paused-clean' | 'frozen-no-rebase'
    | 'integrated' | 'not-ready' | 'reconciled' | 'committed' | 'closed-out';
  integratedCommit?: string;
  /** The new commit sha for a commit-and-stay (verb 1) result. */
  commit?: string;
  /** A push obligation carried onto a terminal close-out row (Case A). */
  pendingPush?: { candidateCommit: string; remoteUrl: string; destinationRef: string };
  /** Residual set aside on a recovery ref during close-out (Case B). */
  recovery?: { ref: string; residualFiles: number };
  pushError?: string;
  /** Human-facing explanation for a managed rebase-recovery outcome that did not integrate. */
  detail?: string;
  /** M7b: classified paused-rebase conflicts, surfaced in plain language (no apply — M7c). */
  conflicts?: Array<{ path: string; conflictClass: 'overlap' | 'add-add' | 'delete-modify' | 'binary' | 'other'; summary: string }>;
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
  /**
   * Optional terminal disposition. Omitted or 'recycle' (the default) is
   * exactly today's behavior: `recycleFinalized` resets the worktree in place
   * for reuse. 'release' instead removes the worktree and its temporary
   * branch via `releaseFinalized` — for a worker whose session has ended.
   */
  dispose?: 'recycle' | 'release';
}

export type CommanderReviewedEvidence = Pick<
  CommanderLocalCommitInput,
  'canonicalBranch' | 'localRef' | 'stagedTree' | 'parentOid'
>;

export interface ReconcileFinalizationInput {
  repositoryPath: string;
  workspaceGuid: string;
  providerRootSessionId: string;
  /**
   * Managed mid-rebase recovery. Dispatched only for a ready assignment whose
   * integration rebase is paused in progress. 'continue' drives a
   * mechanically-recoverable rebase to completion and integrates only when the
   * existing byte-equality proof still holds; 'abort' restores the frozen
   * pre-rebase commit and leaves the integration target unchanged.
   *
   * 'status' is a strictly NON-mutating, lifecycle-aware probe handled at the top of
   * reconcileFinalization: an integrated row reports 'integrated' (its worktree may be
   * gone), a ready row is classified by rebase state, anything else is 'not-ready'.
   *
   * The no-paused-rebase modes recover a ready row whose finalization drifted and
   * was reset to frozen with NO rebase in progress: 'rerebase' replays the frozen work
   * onto the drifted target on the ATTACHED branch without integrating; 'restore_frozen'
   * resets the clean worktree back to the frozen pre-rebase commit. Neither integrates.
   */
  rebaseRecovery?: 'continue' | 'abort' | 'rerebase' | 'restore_frozen' | 'status';
}

export interface SyncWorktreeToTargetInput {
  repositoryPath: string;
  workspaceGuid: string;
  providerRootSessionId: string;
}

export interface SyncResult {
  state: 'no-op' | 'fast-forwarded' | 'rebased';
  head: string;
  baseCommit: string;
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
const usedReconcileAuthorities = new WeakSet<AuthorizedDirectGitOperation>();
const usedConfirmResolutionAuthorities = new WeakSet<AuthorizedDirectGitOperation>();

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

export function hasPushPendingObligation(disposition: string | null): boolean {
  return decodePushDisposition(disposition) !== undefined;
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

function setCurrentHead(db: Database.Database, workspaceGuid: string, currentHead: string): void {
  db.prepare("UPDATE assignments SET current_head = ?, updated_at = datetime('now') WHERE workspace_guid = ?")
    .run(currentHead, workspaceGuid);
}

function remoteRefOid(cwd: string, remoteUrl: string, destinationRef: string): string | null {
  const output = runGit(cwd, ['ls-remote', '--refs', remoteUrl, destinationRef]).trim();
  if (output === '') return null;
  const [oid, ref, ...extra] = output.split(/\s+/);
  if (extra.length !== 0 || ref !== destinationRef) throw new Error('Finalization remote proof is malformed');
  return oid;
}

/**
 * C6 (hygiene, not correctness): after a successful push, clear the now-stale push
 * disposition on any terminal `cleaned` row (a close-out that carried an obligation)
 * whose (remoteUrl, destinationRef) matches the just-pushed target AND whose carried
 * candidate is contained in the pushed local oid. The carried commit is already in
 * local main, so a normal /push publishes it regardless — this only tidies the record.
 * The smallest possible touch on the proven push lanes (D5).
 */
export function drainCarriedObligations(
  db: Database.Database,
  repositoryIdentity: string,
  remoteUrl: string,
  destinationRef: string,
  pushedLocalOid: string,
  primaryCheckoutPath: string,
): void {
  const rows = db.prepare(
    "SELECT workspace_guid, disposition FROM assignments WHERE repository_identity = ? AND lifecycle_status = 'cleaned' AND disposition IS NOT NULL",
  ).all(repositoryIdentity) as { workspace_guid: string; disposition: string }[];
  for (const row of rows) {
    const disposition = decodePushDisposition(row.disposition);
    if (disposition
      && disposition.remoteUrl === remoteUrl
      && disposition.destinationRef === destinationRef
      && isAncestor(primaryCheckoutPath, disposition.candidateCommit, pushedLocalOid)) {
      setDisposition(db, row.workspace_guid, null);
    }
  }
  // I-2: the assignments-row disposition is wiped when a cleaned GUID is reused, so the row
  // query above may find nothing. Resolve the durable preserved_work table rows directly too.
  resolvePreservedWork(db, {
    kind: 'pending-push',
    predicate: (row) => {
      if (row.repository_identity !== repositoryIdentity) return false;
      let payload: { candidateCommit?: string; remoteUrl?: string; destinationRef?: string };
      try { payload = JSON.parse(row.payload); } catch { return false; }
      return payload.remoteUrl === remoteUrl
        && payload.destinationRef === destinationRef
        && typeof payload.candidateCommit === 'string'
        && isAncestor(primaryCheckoutPath, payload.candidateCommit, pushedLocalOid);
    },
  });
}

/**
 * C7 observability: a pure (no-DB) summary of a row's push-pending obligation, for
 * surfacing carried obligations. Returns undefined when the disposition is not a valid
 * push disposition.
 */
export function pushPendingSummary(
  disposition: string | null,
): { candidateCommit: string; remoteUrl: string; destinationRef: string } | undefined {
  const decoded = decodePushDisposition(disposition);
  // Only push-pending/push-failed are OUTSTANDING; a push-succeeded record is already published
  // and is not a carried obligation to surface.
  return decoded && (decoded.phase === 'push-pending' || decoded.phase === 'push-failed')
    ? { candidateCommit: decoded.candidateCommit, remoteUrl: decoded.remoteUrl, destinationRef: decoded.destinationRef }
    : undefined;
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

const REQUIRED_COMMANDER_FINALIZATION_KEYS = [
  'repositoryPath', 'workspaceGuid', 'providerRootSessionId', 'message',
  'canonicalBranch', 'localRef', 'stagedTree', 'parentOid',
];

/**
 * `dispose` is the only OPTIONAL key: a payload that omits it (every existing
 * caller, e.g. commit_worker in orchestrator_mcp.py) validates exactly as
 * before. A payload that carries it must carry nothing else beyond the
 * required set, and its value must be exactly 'recycle' or 'release'.
 */
function requireCommanderFinalizationInput(input: CommanderLocalCommitInput): void {
  const required = [...REQUIRED_COMMANDER_FINALIZATION_KEYS].sort();
  const hasDispose = Object.prototype.hasOwnProperty.call(input, 'dispose');
  const expected = (hasDispose ? [...required, 'dispose'] : required).sort();
  const actual = Object.keys(input).sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])
    || required.some((key) => typeof input[key as keyof CommanderLocalCommitInput] !== 'string'
      || (input[key as keyof CommanderLocalCommitInput] as string).length === 0)
    || (hasDispose && input.dispose !== 'recycle' && input.dispose !== 'release')) {
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

/**
 * Advances a managed worktree's OWN branch onto the current integration target
 * in-session, killing the manual `git merge --ff-only main` workflow. The
 * target is ALWAYS `targetRef(assignment)`, read fresh from the primary
 * checkout — never a caller-supplied ref. This function imports neither push
 * helper and never updates any ref except the worktree's own branch (the
 * ff-merge and the rebase both move only the checked-out branch; the target
 * ref is read-only here).
 *
 * Gates (fail-closed, in order): requireProviderRoot() is enforced by the
 * caller (mirrors reconcileFinalization); exactAssignment binds repository
 * identity and owner; lifecycle must be exactly 'active' (a ready or
 * integrated row refuses); the worktree must have no rebase already paused
 * (probed the same way finalize's rebase-recovery path does, via
 * classifyRebaseState); and no integration_locks row may be held for this
 * repository and workspace.
 *
 * Mechanism, given T = targetRef commit, H = worktree HEAD, B = the
 * assignment's base_commit:
 *  - H === T: no-op.
 *  - H is a strict ancestor of T (worktree behind, clean or dirty): a plain
 *    `git merge --ff-only T`, which is dirty-tolerant and refuses on overlap.
 *  - Otherwise (the worktree carries its own commits atop B): the worktree
 *    must be clean, then `git rebase --onto T B`. A conflict aborts the
 *    rebase, verifies HEAD is back at the original H, and throws reporting
 *    the conflicting paths.
 * On success the git operation always runs BEFORE the durable base_commit /
 * current_head update, so a crash between them is healed by an idempotent
 * re-run (the next call sees H already at T and takes the no-op branch).
 */
export function syncWorktreeToTarget(db: Database.Database, input: SyncWorktreeToTargetInput): SyncResult {
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const assignment = exact.assignment;
  if (assignment.lifecycle_status !== 'active') {
    throw new Error('Sync requires an active assignment; preserving worktree');
  }
  const worktree = assignment.worktree_path;
  if (classifyRebaseState(worktree) !== 'frozen-no-rebase') {
    throw new Error('Sync refused: a rebase is already in progress in the worktree; preserving worktree');
  }
  const heldLock = db.prepare(`
    SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?
  `).get(assignment.repository_identity, assignment.workspace_guid);
  if (heldLock) {
    throw new Error('Sync refused: an integration lock is held for this repository and workspace; preserving worktree');
  }
  const ref = targetRef(assignment);
  const target = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  const head = worktreeHead(worktree);
  if (head === target) {
    return { state: 'no-op', head, baseCommit: assignment.base_commit };
  }
  if (isAncestor(worktree, head, target)) {
    runGit(worktree, ['merge', '--ff-only', target]);
    const newHead = worktreeHead(worktree);
    db.prepare(`
      UPDATE assignments SET base_commit = ?, current_head = ?, updated_at = datetime('now') WHERE workspace_guid = ?
    `).run(target, newHead, assignment.workspace_guid);
    return { state: 'fast-forwarded', head: newHead, baseCommit: target };
  }
  if (!worktreeIsClean(worktree)) {
    throw new Error('Sync requires a clean worktree to rebase local commits onto the target; preserving worktree');
  }
  try {
    runGit(worktree, ['rebase', '--onto', target, assignment.base_commit]);
  } catch (error) {
    const unresolved = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    try { runGit(worktree, ['rebase', '--abort']); } catch { /* best effort */ }
    if (worktreeHead(worktree) !== head) {
      throw new Error('Sync rebase abort did not restore the original worktree HEAD; preserving worktree');
    }
    throw new Error(
      `Sync rebase conflicted and was aborted; preserving worktree.${
        unresolved ? ` Unmerged paths: ${unresolved.split('\n').join(', ')}` : ''}`,
    );
  }
  const newHead = worktreeHead(worktree);
  db.prepare(`
    UPDATE assignments SET base_commit = ?, current_head = ?, updated_at = datetime('now') WHERE workspace_guid = ?
  `).run(target, newHead, assignment.workspace_guid);
  return { state: 'rebased', head: newHead, baseCommit: target };
}

/**
 * True only when the primary's HEAD symref is exactly the integration target ref.
 * A detached HEAD makes `symbolic-ref --quiet` exit non-zero (runGit throws), which
 * is treated as off-ref. The symref is stable across the ref CAS (the CAS moves the
 * branch ref, not HEAD's symref), so a primary classified off-ref before the CAS
 * stays off-ref in the checkout dispatch afterward.
 */
function primaryOnRef(primaryCheckoutPath: string, ref: string): boolean {
  try {
    return runGit(primaryCheckoutPath, ['symbolic-ref', '--quiet', 'HEAD']).trim() === ref;
  } catch {
    return false;
  }
}

/**
 * Refuses BEFORE the ref CAS when the operator's primary checkout carries a local
 * change to a path the carry-forward would rewrite. The overlap set is
 * `dirtyAndUntrackedPaths(primary)` ∩ `changedPaths(expectedTarget, integrated)`;
 * a non-empty intersection is exactly what `read-tree -m -u` would refuse, so we
 * refuse here so `main` never advances on an un-applyable carry-forward.
 *
 * Self-gates on `primaryOnRef`: when the primary is off the target ref (case 1 —
 * feature branch / detached) the integration advances by pure ref CAS and never
 * touches the primary tree, so its local changes cannot overlap and are ignored.
 * `dirtyAndUntrackedPaths` is HEAD-relative, so it is only meaningful BEFORE the
 * CAS (while HEAD == expectedTarget); do not reuse it after the CAS has moved HEAD.
 */
function assertNoPrimaryOverlap(
  primaryCheckoutPath: string,
  ref: string,
  expectedTarget: string,
  integrated: string,
): void {
  if (!primaryOnRef(primaryCheckoutPath, ref)) return;
  const dirty = dirtyAndUntrackedPaths(primaryCheckoutPath);
  if (dirty.length === 0) return;
  const changed = new Set(changedPaths(primaryCheckoutPath, expectedTarget, integrated));
  const overlap = dirty.filter((entry) => changed.has(entry));
  if (overlap.length > 0) {
    throw new Error(
      'Finalization primary checkout has local changes overlapping the carried-forward integration; '
      + `preserving worktree. Overlapping paths: ${overlap.join(', ')}`,
    );
  }
}

/**
 * Ref-level and (when on ref) checkout-level pre-checks. The operator's primary
 * checkout is NOT required to be clean: a dirty tree or an off-ref checkout is
 * tolerated here and reconciled later by the overlap refusal + carry-forward. The
 * only hard requirement is that the target ref still resolves to expectedTarget,
 * and — when the primary is actually on that ref — that its HEAD matches it.
 */
function verifyPrimaryTarget(primaryCheckoutPath: string, ref: string, expectedTarget: string): void {
  const actualTarget = runGit(primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  if (actualTarget !== expectedTarget) {
    throw new Error('Finalization primary checkout is not cleanly checked out at expected target');
  }
  if (primaryOnRef(primaryCheckoutPath, ref)) {
    const actualHead = runGit(primaryCheckoutPath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
    if (actualHead !== expectedTarget) {
      throw new Error('Finalization primary checkout is not cleanly checked out at expected target');
    }
  }
}

/**
 * Post-CAS consistency check. When the primary is on the target ref, the checkout
 * must have advanced to `integrated` (HEAD, no unmerged index entries, and every
 * carried-forward path's index+worktree content == integrated's) — operator
 * changes on OTHER paths are preserved and intentionally not asserted. When the
 * primary is off the ref (case 1), only the ref itself must resolve to integrated;
 * the operator's checkout was never touched. Content is compared against
 * `integrated` directly (not HEAD-relative) because HEAD has already moved.
 */
function verifyPrimaryAfterFastForward(
  primaryCheckoutPath: string,
  ref: string,
  expectedTarget: string,
  integratedCommit: string,
): void {
  const target = runGit(primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  if (target !== integratedCommit) {
    throw new Error('Finalization primary checkout is inconsistent after checked fast-forward');
  }
  if (!primaryOnRef(primaryCheckoutPath, ref)) return;
  const head = runGit(primaryCheckoutPath, ['rev-parse', '--verify', 'HEAD^{commit}']).trim();
  const unmerged = runGit(primaryCheckoutPath, ['ls-files', '--unmerged']).trim();
  if (head !== integratedCommit || unmerged !== '') {
    throw new Error('Finalization primary checkout is inconsistent after checked fast-forward');
  }
  const carried = changedPaths(primaryCheckoutPath, expectedTarget, integratedCommit);
  if (carried.length > 0) {
    const worktreeDrift = runGit(primaryCheckoutPath, ['diff', '--name-only', integratedCommit, '--', ...carried]).trim();
    const indexDrift = runGit(primaryCheckoutPath, ['diff', '--name-only', '--cached', integratedCommit, '--', ...carried]).trim();
    if (worktreeDrift !== '' || indexDrift !== '') {
      throw new Error('Finalization primary checkout is inconsistent after checked fast-forward');
    }
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

export function recycleFinalized(db: Database.Database, repositoryPath: string, assignment: Assignment): void {
  // Re-read: several call sites pass an in-memory row whose disposition is stale
  // relative to the DB; the fresh integrated_commit is the recycle base.
  const current = getAssignment(db, assignment.workspace_guid);
  if (!current || current.lifecycle_status !== 'integrated' || !current.integrated_commit) {
    throw new Error('Recycle requires a durable integrated assignment; preserving worktree');
  }
  if (decodePushDisposition(current.disposition)) {
    throw new Error('Refusing to discard a push-pending obligation; resolve or push it first');
  }
  const integratedCommit = current.integrated_commit;
  // Reachability proof carried forward from cleanupWorkspace: the integrated commit
  // must be contained in the integration target (each site advanced/verified it first).
  if (!isAncestor(repositoryPath, integratedCommit, targetRef(current))) {
    throw new Error('Recycle integration proof is unreachable from the integration target; preserving worktree');
  }
  // HEAD is already the integrated commit at every call site, so NO git reset — a
  // reset would destroy any stray tracked/index delta on the isRepair paths, which
  // bypass Task 1's clean-tree gate.
  db.transaction(() => {
    db.prepare('DELETE FROM integration_records WHERE workspace_guid = ?').run(current.workspace_guid);
    db.prepare(`
      UPDATE assignments
      SET base_commit = ?, current_head = ?, integrated_commit = NULL, disposition = NULL, updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'integrated'
    `).run(integratedCommit, integratedCommit, current.workspace_guid);
    transitionAssignment(db, current.workspace_guid, 'integrated', 'active');
  })();
  // Best-effort AFTER the DB commit: if the txn fails the row stays integrated with
  // its record intact and crash reconciliation still works.
  try { runGit(current.worktree_path, ['update-ref', '-d', candidateRef(current.workspace_guid)]); } catch { /* candidate ref may be absent */ }
}

/**
 * Terminal disposition for a completed worker: removes the worktree and its
 * temporary branch instead of recycling in place, so a successfully-integrated
 * worker does not leak its worktree directory. Mirrors cleanupWorkspace's
 * integrated-row proofs (clean worktree, primary checkout not owned, reachable
 * durable integration evidence via the integration_records join) before it
 * ever removes anything; any failed proof preserves the worktree. Unlike
 * cleanupWorkspace, this does not re-run validateManagedIdentity's
 * worktree-path/branch/listWorktrees cross-check — that identity is already
 * established by the finalization pipeline that ran immediately before this
 * call (exactAssignment's repository+owner binding, requireAssignmentCommitBinding's
 * branch/ref binding, and the checked fast-forward itself), so re-deriving it
 * from managedWorktreePath here would be redundant, not an additional proof.
 * Like cleanupWorkspace (and unlike recycleFinalized), the durable
 * integration_records row and finalization refs are left in place: 'cleaned'
 * is terminal (never reused), so there is nothing to make room for.
 */
export function releaseFinalized(db: Database.Database, repositoryPath: string, assignment: Assignment): void {
  // Re-read for the same reason recycleFinalized does: several call sites pass
  // an in-memory row whose fields may be stale relative to the DB.
  const current = getAssignment(db, assignment.workspace_guid);
  if (!current || current.lifecycle_status !== 'integrated' || !current.integrated_commit) {
    throw new Error('Release requires a durable integrated assignment; preserving worktree');
  }
  if (decodePushDisposition(current.disposition)) {
    throw new Error('Refusing to discard a push-pending obligation; resolve or push it first');
  }
  if (!worktreeIsClean(current.worktree_path)) {
    throw new Error('Release requires a clean worktree; preserving worktree');
  }
  const ref = targetRef(current);
  const actualHead = worktreeHead(current.worktree_path);
  const integration = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(current.workspace_guid, current.repository_identity) as {
    target_ref: string;
    integrated_commit: string;
  } | undefined;
  if (!integration
    || integration.target_ref !== ref
    || integration.integrated_commit !== current.integrated_commit
    || actualHead !== current.integrated_commit
    || !isAncestor(repositoryPath, current.integrated_commit, ref)) {
    throw new Error('Release integration proof is unreachable from the integration target; preserving worktree');
  }
  removeWorktree(repositoryPath, current.worktree_path);
  deleteTemporaryBranch(repositoryPath, current.branch);
  transitionAssignment(db, current.workspace_guid, 'integrated', 'cleaned');
}

/** Applies a Commander finalize's requested terminal disposition; default/absent is recycle. */
function disposeFinalized(
  db: Database.Database,
  repositoryPath: string,
  assignment: Assignment,
  dispose: 'recycle' | 'release' | undefined,
): void {
  if (dispose === 'release') {
    releaseFinalized(db, repositoryPath, assignment);
  } else {
    recycleFinalized(db, repositoryPath, assignment);
  }
}

function finishLocalIntegration(db: Database.Database, local: LocalFinalization): FinalizationResult {
  if (decodePushDisposition(local.assignment.disposition)) {
    return {
      state: 'integrated-local',
      integratedCommit: local.integratedCommit,
      pushError: 'Remote has not proved the exact integrated candidate',
    };
  }
  recycleFinalized(db, local.repositoryPath, local.assignment);
  return { state: 'cleaned', integratedCommit: local.integratedCommit };
}

function repairPrimaryCheckoutAfterInterruptedCas(
  repositoryPath: string,
  ref: string,
  expectedTarget: string,
  candidate: string,
): void {
  // The ref CAS is already durable (this is a post-CAS crash), so the target must
  // resolve to the candidate before any tree work.
  const currentTarget = runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  if (currentTarget !== candidate) {
    throw new Error('Crash reconciliation primary checkout has unproved changes; preserving worktree');
  }
  if (primaryOnRef(repositoryPath, ref)) {
    assertNoPostCasOverlap(repositoryPath, expectedTarget, candidate);
    carryForwardFastForward(repositoryPath, expectedTarget, candidate);
  }
  verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, candidate);
}

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

/** One private path for direct and reviewed-local finalization; it never pushes. */
function continueFrozenFinalization(
  db: Database.Database,
  repositoryPath: string,
  assignment: Assignment,
  sourcePath: string,
  frozenCommit: string,
  hooks?: FinalizationHooks,
): LocalFinalization {
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
    // Diagnostics-only: base_commit is the reviewed boundary cumulativeBinaryEffect
    // trusts below. A base_commit that is no longer the actual merge-base of the
    // frozen commit and the target (e.g. a manual `git merge --ff-only main` that
    // skipped the durable base_commit update) would silently widen the reviewed
    // effect to include un-reviewed carried-forward content. Fail loud instead.
    const mergeBase = runGit(sourcePath, ['merge-base', frozenCommit, expectedTarget]).trim();
    if (mergeBase !== ready.base_commit) {
      throw new Error('Finalization base_commit is not the merge-base; run sync_worktree_to_target');
    }
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
    if (runGit(repositoryPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim() !== expectedTarget) {
      throw new Error('Finalization target moved; preserving worktree');
    }
    verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
    // Refuse an un-applyable carry-forward BEFORE the CAS so main never advances on
    // a conflict. No-op when the primary is off ref (pure ref-advance case).
    assertNoPrimaryOverlap(repositoryPath, ref, expectedTarget, integratedCommit);
    requireExactIntegrationLock(db, ready, ref, expectedTarget);
    hooks?.beforeTargetCompareAndSwap?.();
    runGit(repositoryPath, ['update-ref', ref, integratedCommit, expectedTarget]);
    targetAdvanced = true;
    hooks?.afterTargetCompareAndSwapBeforeCheckout?.();
    // Case dispatch: on ref -> carry the checkout forward preserving unrelated
    // operator work; off ref (feature branch / detached) -> pure ref advance, ZERO
    // working-tree commands against the operator's primary checkout.
    if (primaryOnRef(repositoryPath, ref)) {
      carryForwardFastForward(repositoryPath, expectedTarget, integratedCommit);
    }
    verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, integratedCommit);
    hooks?.afterCheckedFastForwardBeforeRecord?.();
    verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, integratedCommit);
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
  const dirty = runGit(sourcePath, ['status', '--porcelain=v1', '--untracked-files=all']).trim();
  if (dirty !== '') {
    throw new Error('Managed worktree has uncommitted changes; stage or revert them before commit:\n' + dirty);
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
    if (worktreeHead(assignment.worktree_path) !== candidate || !isAncestor(repositoryPath, expectedTarget, candidate)) {
      throw new Error('Fresh repair authority is not an exact descendant candidate; preserving worktree');
    }
    verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
    // Same three-case treatment as continueFrozenFinalization: refuse an
    // un-applyable carry-forward BEFORE this CAS so a ready-repair finalize against
    // a dirty-on-target OR off-ref primary never force-overwrites operator bytes.
    assertNoPrimaryOverlap(repositoryPath, ref, expectedTarget, candidate);
    requireExactIntegrationLock(db, assignment, ref, expectedTarget);
    runGit(repositoryPath, ['update-ref', ref, candidate, expectedTarget]);
    targetAdvanced = true;
    if (primaryOnRef(repositoryPath, ref)) {
      carryForwardFastForward(repositoryPath, expectedTarget, candidate);
    }
    verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, candidate);
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

/**
 * Finalizes an unassigned-primary authority: commits it exactly onto the
 * current branch and stops there. No managed-workspace machinery (freeze,
 * carry-forward, integration record, worktree cleanup) is touched.
 */
export function finalizePrimaryUnassignedCommit(
  authority: AuthorizedDirectGitOperation,
  message: string,
): { state: 'committed'; commit: string } {
  if (authority.checkoutMode !== 'primary-unassigned') throw new Error('Not an unassigned-primary authority');
  if (authority.operation !== 'commit') throw new Error('Unassigned-primary lane commits only');
  requireMessage(message);
  const evidence = exactCommitEvidence(authority);
  const commit = createExactCommit(authority.worktreePath, evidence, message);
  return { state: 'committed', commit };
}

/**
 * Finalizes an unassigned-primary PUSH authority: force-with-lease pushes the
 * operator's own current branch to its own remote ref via the shared push
 * executor, then classifies the remote readback. The executor's
 * revalidateAuthorizedCommitState re-proves the unassigned preconditions and its
 * assertRemoteEvidence re-proves the lease live, so no managed-integration
 * machinery (integration lock, byte-equality, candidate refs) is touched and no
 * fast-forward re-check on frozen evidence is needed (the ff proof ran live at
 * verify).
 */
export function finalizePrimaryUnassignedPush(
  authority: AuthorizedDirectGitOperation,
  hooks?: FinalizationHooks,
  db?: Database.Database,
): FinalizationResult {
  if (authority.checkoutMode !== 'primary-unassigned') throw new Error('Not an unassigned-primary authority');
  if (authority.operation !== 'push') throw new Error('Unassigned-primary push lane pushes only');
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
    throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary push remote readback failed: ${detail}`);
  }
  if (remote === evidence.localOid) {
    if (db) {
      const repository = discoverRepository(authority.worktreePath);
      drainCarriedObligations(db, repository.repositoryIdentity, evidence.remoteUrl, evidence.destinationRef, evidence.localOid, repository.primaryCheckoutPath);
    }
    return { state: 'pushed-only' };
  }
  if (remote === evidence.expectedRemoteOldOid || remote === null) {
    throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary push remote has not proved the exact authorized commit`);
  }
  throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary push remote outcome is ambiguous`);
}

/**
 * Finalizes an unassigned-primary COMMIT-AND-PUSH authority: commits the staged
 * tree exactly onto the current branch (never-lose-work: this local commit
 * persists even if the push then fails), then force-with-lease pushes that new
 * commit via the shared executor and classifies the readback. No integration
 * envelope — the operator is on their own branch, authorized by their own intent.
 */
export function finalizePrimaryUnassignedCommitAndPush(
  authority: AuthorizedDirectGitOperation,
  message: string,
  hooks?: FinalizationHooks,
): FinalizationResult {
  if (authority.checkoutMode !== 'primary-unassigned') throw new Error('Not an unassigned-primary authority');
  if (authority.operation !== 'commit-and-push') throw new Error('Unassigned-primary commit-and-push lane only');
  requireMessage(message);
  const commit = createExactCommit(authority.worktreePath, exactCommitEvidence(authority), message);
  const evidence = authority.evidence as { remoteUrl: string; destinationRef: string; expectedRemoteOldOid: string | null };
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
    throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary commit-and-push remote readback failed: ${detail}`);
  }
  if (remote === commit) return { state: 'pushed', integratedCommit: commit };
  if (remote === evidence.expectedRemoteOldOid || remote === null) {
    throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary commit-and-push remote has not proved the exact authorized commit`);
  }
  throw new Error(`${mutationError ? `Push command failed: ${mutationError}. ` : ''}Unassigned-primary commit-and-push remote outcome is ambiguous`);
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
    if (remote === evidence.localOid) {
      drainCarriedObligations(db, exact.assignment.repository_identity, evidence.remoteUrl, evidence.destinationRef, evidence.localOid, exact.primaryCheckoutPath);
      return { state: 'pushed-only' };
    }
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
  if (authority.operation === 'commit') {
    // Verb 1 (commit-and-stay): create the commit and STOP. No integration into
    // local main, no recycle; the worktree stays active for continued work.
    // Integration/publishing are the separate /reconcile and /push verbs. This is
    // fully decoupled — it touches no disposition or push/integration machinery.
    setCurrentHead(db, exact.assignment.workspace_guid, committed);
    return { state: 'committed', commit: committed };
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
  recycleFinalized(db, local.repositoryPath, local.assignment);
  return { state: 'pushed', integratedCommit: local.integratedCommit };
}

/**
 * Finalizes a managed worktree's own reconcile authority: integrates its
 * current HEAD into local main (or, for a paused REPAIR row, the frozen
 * candidate) and always KEEPS the worktree alive — a reconcile never removes
 * or releases it, and (like every local finalization path in this file)
 * never pushes.
 */
export function finalizeReconcile(
  db: Database.Database,
  authority: AuthorizedDirectGitOperation,
): FinalizationResult {
  if (authority.operation !== 'reconcile') throw new Error('Reconcile finalization requires reconcile authority');
  if (authority.checkoutMode !== 'managed') throw new Error('Reconcile is only valid for a managed worktree; there is no primary or unassigned reconcile lane');
  if (usedReconcileAuthorities.has(authority)) throw new Error('Direct Git reconcile authority is single-use');
  usedReconcileAuthorities.add(authority);
  revalidateAuthorizedCommitState(authority);
  const exact = exactAssignment(db, authority.worktreePath, authority.workspaceGuid, authority.providerRootSessionId);
  const headOid = (authority.evidence as ReconcileEvidence).headOid;
  if (worktreeHead(authority.worktreePath) !== headOid) throw new Error('Reconcile HEAD changed since issuance; re-run /reconcile');
  if (exact.assignment.lifecycle_status === 'active') {
    const local = finalizeLocalCommit(db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, headOid);
    if (decodePushDisposition(local.assignment.disposition)) {
      return { state: 'integrated-local', integratedCommit: local.integratedCommit, pushError: 'Remote has not proved the exact integrated candidate' };
    }
    recycleFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'reconciled', integratedCommit: local.integratedCommit };
  }
  if (exact.assignment.lifecycle_status === 'ready_for_integration') {
    try { runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(exact.assignment.workspace_guid)}^{commit}`]); }
    catch { throw new Error('Ready repair lacks durable frozen finalization state'); }
    runGit(authority.worktreePath, ['update-ref', candidateRef(exact.assignment.workspace_guid), headOid]);
    const local = finalizeAttestedCandidate(db, exact.primaryCheckoutPath, exact.assignment, headOid);
    if (decodePushDisposition(local.assignment.disposition)) {
      return { state: 'integrated-local', integratedCommit: local.integratedCommit, pushError: 'Remote has not proved the exact integrated candidate' };
    }
    recycleFinalized(db, local.repositoryPath, local.assignment);
    return { state: 'reconciled', integratedCommit: local.integratedCommit };
  }
  throw new Error('Reconcile needs an active or paused-for-integration managed assignment; if a prior finalize is frozen, run reconcile_finalization first, then re-run /reconcile');
}

/**
 * Lands an operator-confirmed conflict resolution: consumes a confirm-resolution
 * authority whose live HEAD must equal the server-registered candidate ref, then
 * routes through the shared isRepair channel (finalizeAttestedCandidate) exactly
 * like finalizeReconcile's ready branch. It ONLY accepts a paused-for-integration
 * managed assignment, KEEPS the worktree alive (reconcile-style), and never pushes.
 * The content-pin (registered candidate === authorized HEAD) is what distinguishes
 * this verb from /reconcile: the operator is attesting a specific resolved commit.
 */
export function finalizeConfirmResolution(
  db: Database.Database,
  authority: AuthorizedDirectGitOperation,
): FinalizationResult {
  if (authority.operation !== 'confirm-resolution') throw new Error('Confirm-resolution finalization requires confirm-resolution authority');
  if (authority.checkoutMode !== 'managed') throw new Error('Confirm-resolution is only valid for a managed worktree');
  if (usedConfirmResolutionAuthorities.has(authority)) throw new Error('Direct Git confirm-resolution authority is single-use');
  usedConfirmResolutionAuthorities.add(authority);
  revalidateAuthorizedCommitState(authority);
  const exact = exactAssignment(db, authority.worktreePath, authority.workspaceGuid, authority.providerRootSessionId);
  if (exact.assignment.lifecycle_status !== 'ready_for_integration') throw new Error('Confirm-resolution needs a paused-for-integration managed assignment');
  try { runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(exact.assignment.workspace_guid)}^{commit}`]); }
  catch { throw new Error('Ready repair lacks durable frozen finalization state'); }
  const headOid = (authority.evidence as ReconcileEvidence).headOid;
  let registered: string;
  try { registered = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(exact.assignment.workspace_guid)}^{commit}`]).trim(); }
  catch { throw new Error('No confirmed resolution candidate matches the authorized HEAD; nothing landed'); }
  if (registered !== headOid) throw new Error('No confirmed resolution candidate matches the authorized HEAD; nothing landed');
  if (worktreeHead(authority.worktreePath) !== headOid) throw new Error('Resolution HEAD changed since /confirm-resolution; re-run');
  const local = finalizeAttestedCandidate(db, exact.primaryCheckoutPath, exact.assignment, headOid);
  if (decodePushDisposition(local.assignment.disposition)) {
    return { state: 'integrated-local', integratedCommit: local.integratedCommit, pushError: 'Remote has not proved the exact integrated candidate' };
  }
  recycleFinalized(db, local.repositoryPath, local.assignment);
  return { state: 'reconciled', integratedCommit: local.integratedCommit };
}

/**
 * Terminal teardown for close-out (verb 4): a durably-integrated row is removed
 * (worktree + temporary branch) and transitioned integrated→cleaned. Duplicates
 * releaseFinalized's integrated-proof block deliberately, leaving releaseFinalized
 * (and its push-pending throw, on which the Commander disposeFinalized caller
 * depends) byte-untouched. Task 4 adds the push-pending self-heal + carry here.
 */
function closeOutRelease(
  db: Database.Database,
  repositoryPath: string,
  assignment: Assignment,
  recovery?: { ref: string; residualFiles: number },
): FinalizationResult {
  const current = getAssignment(db, assignment.workspace_guid);
  if (!current || current.lifecycle_status !== 'integrated' || !current.integrated_commit) {
    throw new Error('Close-out release requires a durable integrated assignment; preserving worktree');
  }
  // I2: the durable candidate ref must equal the recorded integrated commit; a divergence means
  // the finalization record is inconsistent — refuse and preserve rather than heal to a wrong oid.
  const candidate = runGit(repositoryPath, ['rev-parse', '--verify', `${candidateRef(current.workspace_guid)}^{commit}`]).trim();
  if (candidate !== current.integrated_commit) {
    throw new Error('Close-out candidate proof differs; preserving worktree');
  }
  // Case A: a push-pending obligation only exists on an integrated row whose commit is
  // ALREADY in local main, so teardown discards zero publishable bytes. Opportunistically
  // self-heal via a read-only ls-remote (never a push); otherwise carry the obligation
  // forward on the terminal cleaned row (the integrated→cleaned transition preserves the
  // disposition — db.ts:316 writes only lifecycle_status), drained by the next /push.
  let pendingPush: { candidateCommit: string; remoteUrl: string; destinationRef: string } | undefined;
  const disposition = decodePushDisposition(current.disposition);
  if (disposition) {
    // I2: bind the carried push refs to the integrated candidate + durable frozen ref before
    // trusting them for the self-heal below.
    const frozenCommit = runGit(repositoryPath, ['rev-parse', '--verify', `${freezeRef(current.workspace_guid)}^{commit}`]).trim();
    if (disposition.candidateCommit !== current.integrated_commit || frozenCommit !== disposition.frozenCommit) {
      throw new Error('Close-out push refs differ; preserving worktree');
    }
  }
  if (disposition && disposition.phase === 'push-succeeded') {
    // obs 2: a push-succeeded disposition is already published — clear it, never carry or record it.
    setDisposition(db, current.workspace_guid, null);
  } else if (disposition) {
    let remote: string | null = null;
    try {
      remote = remoteRefOid(current.worktree_path, disposition.remoteUrl, disposition.destinationRef);
    } catch { /* offline / malformed readback is non-fatal: carry the obligation */ }
    if (remote === disposition.candidateCommit) {
      setDisposition(db, current.workspace_guid, null); // remote already has it — resolved for free
    } else {
      pendingPush = {
        candidateCommit: disposition.candidateCommit,
        remoteUrl: disposition.remoteUrl,
        destinationRef: disposition.destinationRef,
      };
    }
  }
  // I2 self-heal: a worktree HEAD drifted back to the frozen pre-integration commit (a
  // reconcileFinalization-recoverable state) is reset forward to the integrated candidate so the
  // integration proof below holds. A head mismatch WITHOUT a matching push disposition is NOT this
  // shape and falls through to the existing refusal.
  const head0 = worktreeHead(current.worktree_path);
  if (head0 !== current.integrated_commit
    && disposition
    && head0 === disposition.frozenCommit
    && worktreeIsClean(current.worktree_path)) {
    runGit(current.worktree_path, ['reset', '--hard', current.integrated_commit]);
  }
  if (!worktreeIsClean(current.worktree_path)) {
    throw new Error('Close-out release requires a clean worktree; preserving worktree');
  }
  const ref = targetRef(current);
  const actualHead = worktreeHead(current.worktree_path);
  const integration = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(current.workspace_guid, current.repository_identity) as {
    target_ref: string;
    integrated_commit: string;
  } | undefined;
  if (!integration
    || integration.target_ref !== ref
    || integration.integrated_commit !== current.integrated_commit
    || actualHead !== current.integrated_commit
    || !isAncestor(repositoryPath, current.integrated_commit, ref)) {
    throw new Error('Close-out integration proof is unreachable from the integration target; preserving worktree');
  }
  removeWorktree(repositoryPath, current.worktree_path);
  deleteTemporaryBranch(repositoryPath, current.branch);
  // I-4: the integrated->cleaned transition makes the GUID reuse-eligible (reuse NULLs the row
  // disposition). Wrap the transition and the durable table insert in one transaction so a crash
  // never lands the row cleaned with the obligation lost from both the row AND the table.
  db.transaction(() => {
    transitionAssignment(db, current.workspace_guid, 'integrated', 'cleaned');
    if (pendingPush) {
      insertPreservedWork(db, {
        workspaceGuid: current.workspace_guid,
        repositoryIdentity: current.repository_identity,
        ownerSessionId: current.owner_session_id,
        kind: 'pending-push',
        payload: JSON.stringify(pendingPush),
      });
    }
  })();
  return {
    state: 'closed-out',
    integratedCommit: current.integrated_commit,
    ...(pendingPush ? { pendingPush } : {}),
    ...(recovery ? { recovery } : {}),
  };
}

/**
 * Case B: if the worktree is dirty, snapshot the FULL residual (tracked + untracked,
 * add -A scope) to a durable refs/ironclaude/recovery/<guid> ref using a temporary
 * index in a SCRATCH path OUTSIDE the worktree (so `add -A` does not capture the index
 * file itself), WITHOUT moving HEAD/the real index/the working tree; then reset --hard
 * + clean -fd. The recovery ref is minted AND re-verified to resolve BEFORE any reset,
 * so a crash never loses the residual. Integration then lands only the reviewed HEAD.
 * NOT rescueAbandon (which commits residual onto the branch — that would ride into main).
 */
function snapshotResidualIfDirty(
  db: Database.Database,
  exact: { assignment: Assignment; primaryCheckoutPath: string },
): { ref: string; residualFiles: number } | undefined {
  const worktree = exact.assignment.worktree_path;
  if (worktreeIsClean(worktree)) return undefined;
  const residualFiles = runGit(worktree, ['status', '--porcelain=v1', '--untracked-files=all'])
    .split('\n').filter((line) => line.trim() !== '').length;
  const tmpIndex = path.join(tmpdir(), `ironclaude-closeout-index-${exact.assignment.workspace_guid}-${process.pid}`);
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_INDEX_FILE: tmpIndex };
  let snapshot: string;
  try {
    runGitEnv(worktree, ['read-tree', 'HEAD'], env);
    runGitEnv(worktree, ['add', '-A'], env);
    const tree = runGitEnv(worktree, ['write-tree'], env).trim();
    snapshot = runGitEnv(worktree, ['commit-tree', tree, '-p', 'HEAD', '-m', 'ironclaude: close-out residual snapshot'], env).trim();
  } finally {
    try { rmSync(tmpIndex, { force: true }); } catch { /* best effort */ }
  }
  // Per-lifecycle content-addressed ref (C2/R11): a flat <guid> ref would clobber a prior
  // snapshot when the same GUID is reused. Suffixing the snapshot oid makes each lifecycle's
  // residual its own ref; identical residual content maps to the same ref (tolerate-same-oid).
  const recoveryRef = `refs/ironclaude/recovery/${exact.assignment.workspace_guid}-${snapshot}`;
  try {
    // CREATE-ONLY: empty old-oid refuses to overwrite an existing ref.
    runGit(exact.primaryCheckoutPath, ['update-ref', recoveryRef, snapshot, '']);
  } catch (error) {
    // TOLERATE-SAME-OID (I-3): a prior identical-content snapshot already minted this exact
    // ref. Accept it only when the existing ref resolves to the same snapshot; else rethrow.
    const existing = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${recoveryRef}^{commit}`]).trim();
    if (existing !== snapshot) throw error;
  }
  const payload = JSON.stringify({ ref: recoveryRef, residualFiles });
  // I-4: durably record BOTH the assignments column AND the reuse-proof table row BEFORE the
  // destructive reset, so a crash never orphans the residual (the flat column is wiped on reuse).
  db.prepare("UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?")
    .run(recoveryRef, exact.assignment.workspace_guid);
  insertPreservedWork(db, {
    workspaceGuid: exact.assignment.workspace_guid,
    repositoryIdentity: exact.assignment.repository_identity,
    ownerSessionId: exact.assignment.owner_session_id,
    kind: 'recovery',
    payload,
  });
  // Re-verify the ref resolves BEFORE any destructive reset.
  runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${recoveryRef}^{commit}`]);
  runGit(worktree, ['reset', '--hard', 'HEAD']);
  runGit(worktree, ['clean', '-fd']);
  return { ref: recoveryRef, residualFiles };
}

const usedCloseOutAuthorities = new WeakSet<AuthorizedDirectGitOperation>();

/**
 * Finalizes a managed worktree's own close-out authority: integrates its current
 * HEAD into local main (or completes an already-frozen/integrated finalization),
 * then FULLY tears the worktree down via closeOutRelease. By the time this runs any
 * paused rebase has already been continued (HEAD attached) or deferred by the
 * close_out_worktree handler. Reuses finalizeLocalCommit/finalizeAttestedCandidate
 * unmodified; never modifies finalizeReconcile/reconcileFinalization.
 */
export function finalizeCloseOut(db: Database.Database, authority: AuthorizedDirectGitOperation): FinalizationResult {
  if (authority.operation !== 'close-out') throw new Error('Close-out finalization requires close-out authority');
  if (authority.checkoutMode !== 'managed') throw new Error('Close-out is only valid for a managed worktree; there is no primary or unassigned close-out lane');
  if (usedCloseOutAuthorities.has(authority)) throw new Error('Direct Git close-out authority is single-use');
  usedCloseOutAuthorities.add(authority);
  revalidateAuthorizedCommitState(authority);
  const exact = exactAssignment(db, authority.worktreePath, authority.workspaceGuid, authority.providerRootSessionId);
  const headOid = (authority.evidence as ReconcileEvidence).headOid;
  if (worktreeHead(authority.worktreePath) !== headOid) throw new Error('Close-out HEAD changed since verification; re-run /close-out');
  // Case B: set aside any dirty residual to a durable recovery ref BEFORE integrating,
  // so only the reviewed HEAD lands on main. reset --hard HEAD keeps the commit, so the
  // pin above still holds for the branches below.
  const recovery = snapshotResidualIfDirty(db, exact);
  if (exact.assignment.lifecycle_status === 'active') {
    const local = finalizeLocalCommit(db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, headOid);
    return closeOutRelease(db, local.repositoryPath, local.assignment, recovery);
  }
  if (exact.assignment.lifecycle_status === 'ready_for_integration') {
    let frozen: string;
    try { frozen = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(exact.assignment.workspace_guid)}^{commit}`]).trim(); }
    catch { throw new Error('Close-out ready repair lacks durable frozen finalization state'); }
    // Defense-in-depth (C1): close-out authority is evidence-light (the human attested no
    // commit), so this equality proof is the ONLY review guarantee. finalizeAttestedCandidate
    // proves only that headOid is a DESCENDANT of the target — an altered-content descendant
    // passes it. Require cumulativeBinaryEffect equality (mirrors :1499-1501) before
    // integrating; otherwise preserve-and-defer for automated resolution (M7).
    const target = targetRef(exact.assignment);
    const expectedTarget = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${target}^{commit}`]).trim();
    const reviewedEffect = cumulativeBinaryEffect(authority.worktreePath, exact.assignment.base_commit, frozen);
    if (!isAncestor(exact.primaryCheckoutPath, expectedTarget, headOid)
      || cumulativeBinaryEffect(authority.worktreePath, expectedTarget, headOid) !== reviewedEffect) {
      return {
        state: 'rebase-recovery-repair-required',
        detail: 'Close-out: the rebase resolution changed the reviewed content (or HEAD is not a descendant of the integration target); preserved for automated resolution (M7). Not an operator task.',
      };
    }
    runGit(authority.worktreePath, ['update-ref', candidateRef(exact.assignment.workspace_guid), headOid]);
    const local = finalizeAttestedCandidate(db, exact.primaryCheckoutPath, exact.assignment, headOid);
    return closeOutRelease(db, local.repositoryPath, local.assignment, recovery);
  }
  if (exact.assignment.lifecycle_status === 'integrated') {
    return closeOutRelease(db, exact.primaryCheckoutPath, exact.assignment, recovery);
  }
  throw new Error('Close-out needs an active, ready, or integrated managed assignment');
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
    if (decodePushDisposition(local.assignment.disposition)) {
      return { state: 'integrated-local', integratedCommit: candidate, pushError: 'Remote has not proved the exact integrated candidate' };
    }
    disposeFinalized(db, local.repositoryPath, local.assignment, input.dispose);
    return { state: 'cleaned', integratedCommit: candidate };
  }
  const local = finalizeLocalCommit(
    db, exact.primaryCheckoutPath, exact.assignment, exact.assignment.worktree_path, committed, hooks,
  );
  if (decodePushDisposition(local.assignment.disposition)) {
    return { state: 'integrated-local', integratedCommit: local.integratedCommit, pushError: 'Remote has not proved the exact integrated candidate' };
  }
  disposeFinalized(db, local.repositoryPath, local.assignment, input.dispose);
  return { state: 'cleaned', integratedCommit: local.integratedCommit };
}

/**
 * Managed recovery for a ready assignment whose integration rebase is paused
 * mid-flight. It drives only mechanically-recoverable cases and STOPS on any
 * case that needs a human. It never auto-resolves a conflict and never bypasses
 * a proof: a content-changing resolution is REJECTED by the existing
 * cumulativeBinaryEffect equality and routed to the fresh-commit (isRepair)
 * channel, which the operator drives with a fresh commit authority.
 */
function recoverRebaseInProgress(
  db: Database.Database,
  exact: { assignment: Assignment; primaryCheckoutPath: string },
  mode: 'continue' | 'abort',
): FinalizationResult {
  const assignment = exact.assignment;
  const worktree = assignment.worktree_path;
  const primary = exact.primaryCheckoutPath;
  const ref = targetRef(assignment);
  const frozen = runGit(primary, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();

  if (mode === 'abort') {
    runGit(worktree, ['rebase', '--abort']);
    if (worktreeHead(worktree) !== frozen) {
      throw new Error('Rebase recovery abort did not restore the frozen pre-rebase commit; preserving worktree');
    }
    return { state: 'rebase-aborted', detail: 'Rebase aborted; frozen pre-rebase commit restored, integration target unchanged.' };
  }

  // Case B: an unresolved conflict is not mechanically recoverable. Never
  // auto-resolve — surface the unmerged paths and stop without advancing.
  const unresolved = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
  if (unresolved !== '') {
    // M7b: surface the classified conflicts in plain language instead of a raw throw. Outcome
    // unchanged — nothing integrates, the paused rebase is left in place (no apply; that is M7c).
    return {
      state: 'rebase-paused-conflict',
      conflicts: classifyRebaseConflicts(worktree),
      detail: 'Close-out/reconcile paused: unresolved conflicts remain; automated resolution pending (M7c). Worktree preserved; nothing integrated. Not an operator task.',
    };
  }
  try {
    runGit(worktree, ['-c', 'core.editor=true', 'rebase', '--continue']);
  } catch (error) {
    // A later step re-conflicted: surface the newly unmerged paths and stop.
    const reconflict = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    if (reconflict !== '') {
      // M7b: a later rebase step re-conflicted — surface it (distinct detail from the initial stop).
      return {
        state: 'rebase-paused-conflict',
        conflicts: classifyRebaseConflicts(worktree),
        detail: 'Close-out/reconcile paused: continuing re-conflicted; automated resolution pending (M7c). Worktree preserved; nothing integrated. Not an operator task.',
      };
    }
    throw error;
  }
  // A multi-step rebase can pause again (a later edit/break, or a fresh conflict
  // that continue reported without a nonzero exit): it is still not complete.
  const stillRebaseDir = runGit(worktree, ['rev-parse', '--git-path', 'rebase-merge']).trim();
  if (existsSync(path.resolve(worktree, stillRebaseDir))) {
    const reconflict = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    throw new Error(`Rebase recovery stopped: rebase still in progress after continue; preserving worktree.${reconflict ? ` Unmerged paths: ${reconflict.split('\n').join(', ')}` : ''}`);
  }

  const head = worktreeHead(worktree);
  const expectedTarget = runGit(primary, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  const reviewedEffect = cumulativeBinaryEffect(worktree, assignment.base_commit, frozen);
  if (!isAncestor(primary, expectedTarget, head)
    || cumulativeBinaryEffect(worktree, expectedTarget, head) !== reviewedEffect) {
    // Case D: the resolution changed the reviewed content (or is not a descendant
    // of the target). The equality proof rejects it. Preserve the worktree at the
    // resolved HEAD and route to the fresh-commit isRepair channel; do NOT advance
    // the target through the equality path here.
    return {
      state: 'rebase-recovery-repair-required',
      detail: 'Rebase resolution changed the reviewed content; the equality proof rejected it. Integrate with a fresh commit (isRepair) authority; worktree preserved and integration target unchanged.',
    };
  }
  // Case A: a conflict-free recovery whose cumulative effect matches the review.
  // Integrate the continued HEAD through the existing attested-candidate coordinator.
  runGit(worktree, ['update-ref', candidateRef(assignment.workspace_guid), head]);
  const local = finalizeAttestedCandidate(db, primary, assignment, head);
  return finishLocalIntegration(db, local);
}

/** Classifies a ready worktree by whether a rebase is paused and, if so, conflicted. */
/**
 * M7b READ-ONLY: classify each unmerged path of a paused-conflict rebase into the M7a taxonomy
 * with a plain-language two-sided summary. Never mutates the worktree. A merge stage is ABSENT
 * when that side deleted the path (delete/modify): stageLines guards the stage read so a
 * taxonomy-required class never crashes the classifier.
 */
export function classifyRebaseConflicts(worktree: string): NonNullable<FinalizationResult['conflicts']> {
  const unmerged = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
  if (unmerged === '') return [];
  return unmerged.split('\n').map((path) => {
    const xy = runGit(worktree, ['status', '--porcelain=v1', '--', path]).slice(0, 2);
    // Binary detection on an unmerged path: diff the two conflict stage blobs directly
    // (`:2:` ours vs `:3:` theirs); git reports a binary pair as `-\t-`. An absent stage
    // (delete/modify) makes this throw — caught as non-binary, since delete-modify wins anyway.
    let binary = false;
    try { binary = /^-\t-/.test(runGit(worktree, ['diff', '--numstat', `:2:${path}`, `:3:${path}`]).trim()); }
    catch { binary = false; }
    const conflictClass: NonNullable<FinalizationResult['conflicts']>[number]['conflictClass'] = binary ? 'binary'
      : xy === 'UU' ? 'overlap'
      : xy === 'AA' ? 'add-add'
      : (xy === 'UD' || xy === 'DU') ? 'delete-modify'
      : 'other';
    const stageLines = (stage: 2 | 3): number => {
      try { return runGit(worktree, ['show', `:${stage}:${path}`]).split('\n').length; }
      catch { return 0; }
    };
    const ours = stageLines(2);
    const theirs = stageLines(3);
    // M7c label fix: the integration rebase is `git rebase --onto target base`, replaying
    // reviewed commits ONTO the target. During that replay git's stage :2:/--ours is the
    // checked-out INTEGRATION TARGET being rebased onto, and stage :3:/--theirs is the
    // REVIEWED WORK commit being applied — the reverse of a merge. `ours` therefore reads as
    // "the integration target" and `theirs` as "your reviewed work".
    const summary = `${path}: your reviewed work has ${theirs} line(s) here; the integration target has ${ours} line(s) (${conflictClass}).`;
    return { path, conflictClass, summary };
  });
}

export interface ResolveConflictHunkInput {
  repositoryPath: string;
  workspaceGuid: string;
  providerRootSessionId: string;
  /** The single unmerged path this call resolves. */
  path: string;
  choice: 'keep-mine' | 'take-target' | 'prose' | 'abort';
  /** Required, and used only, when choice === 'prose'. */
  content?: string;
}

export interface ResolveConflictHunkResult {
  path: string;
  /** The staged (index vs HEAD) diff of `path` after the choice was applied. */
  staged: string;
  /** Count of paths still unmerged in the worktree after this call. */
  remaining: number;
  /** Present only once the rebase has TRULY completed (no rebase-merge dir, attached HEAD). */
  candidate?: string;
  /** Fresh classification, present only when a NEW conflict surfaced (a later commit re-conflicted). */
  conflicts?: NonNullable<FinalizationResult['conflicts']>;
}

/**
 * M7c APPLY: turns one per-hunk operator choice into staged resolved bytes on a paused
 * integration rebase. It NEVER lands (no finalizeAttestedCandidate/finalizeReconcile call)
 * and NEVER pushes — landing a completed rebase is a separate tool (land_resolved_conflict).
 *
 * Choice mapping is the REVERSE of a merge, because the integration rebase replays the
 * reviewed work ONTO the target (`git rebase --onto target base`): during that replay
 * git's stage :2:/--ours is the checked-out target, stage :3:/--theirs is the reviewed
 * commit being applied. So keep-mine (keep the reviewed work) takes stage 3 (--theirs),
 * and take-target (take the drifted target) takes stage 2 (--ours).
 *
 * A multi-commit reviewed range can pause again immediately after `rebase --continue`
 * resolves this commit's last conflict, because the NEXT replayed commit conflicts too.
 * That is reported back (fresh conflicts, no candidate) rather than treated as failure —
 * the caller re-invokes this tool per remaining hunk. Only once `rebase --continue`
 * truly completes the rebase (rebase-merge dir gone AND HEAD reattached to a branch) is
 * the candidate ref registered, exactly mirroring the manual hand-resolve + candidateRef
 * seeding done by land_resolved_conflict's own tests.
 */
export function resolveConflictHunk(
  db: Database.Database,
  input: ResolveConflictHunkInput,
): FinalizationResult | ResolveConflictHunkResult {
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const assignment = exact.assignment;
  if (assignment.lifecycle_status !== 'ready_for_integration') {
    throw new Error('Resolve-conflict-hunk needs a paused-for-integration managed assignment');
  }
  const worktree = assignment.worktree_path;
  const rebaseDir = runGit(worktree, ['rev-parse', '--git-path', 'rebase-merge']).trim();
  if (!existsSync(path.resolve(worktree, rebaseDir))) {
    throw new Error('Resolve-conflict-hunk requires a paused rebase; preserving worktree');
  }

  if (input.choice === 'abort') {
    return recoverRebaseInProgress(db, exact, 'abort');
  }

  // C1: bound input.path to the CURRENT unmerged set BEFORE any filesystem effect. The tool is
  // agent-callable (requireProviderRoot only, no human intent), so an unvalidated path would let
  // 'prose' writeFileSync outside the worktree (absolute/../ traversal) or stage a non-conflicted
  // file that rebase --continue folds into the operator-confirmed commit. git reports only
  // repo-relative, in-tree, currently-conflicted paths, so an EXACT membership match refuses
  // absolute/../ paths AND non-conflicted paths AND is the correct semantic. -z (NUL split)
  // avoids core.quotePath escaping and any newline-in-path evasion.
  const unmergedPaths = runGit(worktree, ['diff', '--name-only', '--diff-filter=U', '-z'])
    .split('\0')
    .filter((entry) => entry !== '');
  if (!unmergedPaths.includes(input.path)) {
    throw new Error(`resolve_conflict_hunk only resolves a currently-conflicted path; '${input.path}' is not in the unmerged set`);
  }

  if (input.choice === 'keep-mine' || input.choice === 'take-target') {
    const stageFlag = input.choice === 'keep-mine' ? '--theirs' : '--ours';
    try {
      runGit(worktree, ['checkout', stageFlag, '--', input.path]);
    } catch {
      throw new Error(
        `Cannot resolve ${input.path} with '${input.choice}': one side deleted this path (delete-modify `
        + "conflict has no checkout stage for it); resolve it explicitly with choice 'prose', "
        + 'or abort to accept the deletion (preserve-and-defer).',
      );
    }
  } else if (input.choice === 'prose') {
    if (input.content === undefined) throw new Error("choice 'prose' requires content");
    writeFileSync(path.resolve(worktree, input.path), input.content);
  } else {
    throw new Error(`Unknown resolve-conflict-hunk choice: ${input.choice as string}`);
  }
  runGit(worktree, ['add', '--', input.path]);
  const staged = runGit(worktree, ['diff', '--cached', '--', input.path]);

  const unmergedAfterStage = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
  if (unmergedAfterStage !== '') {
    // Another path from the SAME conflicting commit is still unresolved — do not
    // attempt to continue the rebase until every hunk of this commit is staged.
    return { path: input.path, staged, remaining: unmergedAfterStage.split('\n').filter((line) => line !== '').length };
  }

  try {
    runGit(worktree, ['-c', 'core.editor=true', 'rebase', '--continue']);
  } catch {
    // The next replayed commit conflicted immediately: surface it fresh, no candidate.
    const reconflict = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    const remaining = reconflict === '' ? 0 : reconflict.split('\n').filter((line) => line !== '').length;
    return { path: input.path, staged, remaining, conflicts: classifyRebaseConflicts(worktree) };
  }

  // `rebase --continue` reported success, but a later step (e.g. an `edit`/`break`
  // stop) can still leave the rebase in progress without throwing. Verify TRUE
  // completion before registering any candidate.
  const stillRebaseDir = runGit(worktree, ['rev-parse', '--git-path', 'rebase-merge']).trim();
  const rebaseStillInProgress = existsSync(path.resolve(worktree, stillRebaseDir));
  let attachedHead = true;
  try { runGit(worktree, ['symbolic-ref', '--quiet', '--short', 'HEAD']); }
  catch { attachedHead = false; }
  if (rebaseStillInProgress || !attachedHead) {
    const reconflict = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    const remaining = reconflict === '' ? 0 : reconflict.split('\n').filter((line) => line !== '').length;
    return {
      path: input.path,
      staged,
      remaining,
      ...(reconflict !== '' ? { conflicts: classifyRebaseConflicts(worktree) } : {}),
    };
  }

  const head = worktreeHead(worktree);
  runGit(worktree, ['update-ref', candidateRef(assignment.workspace_guid), head]);
  return { path: input.path, staged, remaining: 0, candidate: head };
}

function classifyRebaseState(
  worktree: string,
): 'rebase-paused-conflict' | 'rebase-paused-clean' | 'frozen-no-rebase' {
  // A linked worktree's rebase-merge dir is resolved via git-path, never probed as
  // <worktree>/.git/rebase-merge (its .git is a gitdir file, not a directory).
  const rebaseDir = runGit(worktree, ['rev-parse', '--git-path', 'rebase-merge']).trim();
  if (!existsSync(path.resolve(worktree, rebaseDir))) return 'frozen-no-rebase';
  const unmerged = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
  return unmerged !== '' ? 'rebase-paused-conflict' : 'rebase-paused-clean';
}

/**
 * Replays the frozen reviewed work onto the drifted integration target on the
 * ATTACHED branch, using the SAME 2-arg rebase as finalize (rebase --onto ref base).
 * The 2-arg form keeps HEAD attached so a later fresh-commit isRepair authority can
 * integrate; a 3-arg rebase --onto target base frozenHead would detach and break
 * validateExactCommitState. NEVER integrates: on success the caller drives isRepair.
 */
function rerebaseFromFrozen(
  worktree: string,
  assignment: Assignment,
  frozen: string,
): FinalizationResult {
  if (worktreeHead(worktree) !== frozen) {
    throw new Error('Rerebase requires the worktree at the frozen pre-rebase commit; preserving worktree');
  }
  if (!worktreeIsClean(worktree)) {
    throw new Error('Rerebase requires a clean worktree; preserving worktree');
  }
  const ref = targetRef(assignment);
  try {
    runGit(worktree, ['rebase', '--onto', ref, assignment.base_commit]);
  } catch {
    // A conflicting replay leaves a paused rebase in place for continue/abort.
    const unmerged = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    throw new Error(`Rerebase conflicted; a paused rebase is preserved for continue/abort.${unmerged ? ` Unmerged paths: ${unmerged.split('\n').join(', ')}` : ''}`);
  }
  return { state: 'rebase-rerebased-ready-for-repair', detail: worktreeHead(worktree) };
}

/** Resets a clean worktree back to the frozen pre-rebase commit; never touches the target. */
function restoreFrozen(worktree: string, frozen: string): FinalizationResult {
  if (!worktreeIsClean(worktree)) {
    throw new Error('Restore frozen requires a clean worktree; preserving worktree');
  }
  runGit(worktree, ['reset', '--hard', frozen]);
  if (worktreeHead(worktree) !== frozen) {
    throw new Error('Restore frozen did not restore the frozen pre-rebase commit; preserving worktree');
  }
  return { state: 'rebase-frozen-restored', detail: 'Worktree reset to the frozen pre-rebase commit; integration target unchanged.' };
}

/**
 * Managed recovery for a ready row whose finalization drifted with NO paused rebase.
 * 'rerebase' and 'restore_frozen' require NO paused rebase and refuse otherwise. None
 * integrate. (The non-mutating 'status' probe is handled at the top of
 * reconcileFinalization, before the integrated-cleanup branch.)
 */
function recoverNoPausedRebase(
  exact: { assignment: Assignment; primaryCheckoutPath: string },
  mode: 'rerebase' | 'restore_frozen',
): FinalizationResult {
  const assignment = exact.assignment;
  const worktree = assignment.worktree_path;
  const state = classifyRebaseState(worktree);
  if (state !== 'frozen-no-rebase') {
    throw new Error(`Rebase ${mode} requires no paused rebase; a rebase is still in progress — resolve via continue/abort first; preserving worktree`);
  }
  const frozen = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
  return mode === 'rerebase'
    ? rerebaseFromFrozen(worktree, assignment, frozen)
    : restoreFrozen(worktree, frozen);
}

/** Crash recovery only advances a durable ready row once its record is reachable. */
export function reconcileFinalization(db: Database.Database, input: ReconcileFinalizationInput): FinalizationResult {
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const assignment = exact.assignment;
  // A 'status' request is a strictly NON-mutating, lifecycle-aware probe. It must
  // return BEFORE the integrated-cleanup branch below (which resets/cleans and REMOVES
  // the worktree) so a probe never mutates. exactAssignment touches only the DB and
  // repo discovery, so this is the earliest non-mutating point.
  // An integrated row's worktree may already be gone, so it never calls
  // classifyRebaseState (which runs git in the worktree).
  if (input.rebaseRecovery === 'status') {
    if (assignment.lifecycle_status === 'integrated') {
      return { state: 'integrated' };
    }
    if (assignment.lifecycle_status === 'ready_for_integration') {
      const state = classifyRebaseState(assignment.worktree_path);
      return { state, detail: `Managed finalization worktree state: ${state}.` };
    }
    return { state: 'not-ready' };
  }
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
    recycleFinalized(db, exact.primaryCheckoutPath, assignment);
    return { state: 'cleaned', integratedCommit: assignment.integrated_commit ?? undefined };
  }
  if (assignment.lifecycle_status !== 'ready_for_integration') {
    throw new Error('No ready finalization is available for reconciliation');
  }
  // No-paused-rebase recovery runs BEFORE the continue/abort guard: those modes
  // recover a ready row whose finalization drifted and was reset to frozen with NO
  // rebase in progress, so the guard below (which requires a paused rebase) must not
  // see them. Returning here also keeps recoverRebaseInProgress on 'continue'|'abort'.
  // ('status' is handled by the non-mutating early-return at the top of this function.)
  if (input.rebaseRecovery === 'rerebase'
    || input.rebaseRecovery === 'restore_frozen') {
    return recoverNoPausedRebase(exact, input.rebaseRecovery);
  }
  if (input.rebaseRecovery) {
    // Managed rebase recovery applies only while a rebase is actually paused.
    // Without this guard, an 'abort' request on a non-rebase ready row would fall
    // through to the frozen-replay path below and integrate — the inverse of abort.
    const rebaseInProgressDir = runGit(assignment.worktree_path, ['rev-parse', '--git-path', 'rebase-merge']).trim();
    if (!existsSync(path.resolve(assignment.worktree_path, rebaseInProgressDir))) {
      throw new Error('Rebase recovery requested but no rebase is in progress; preserving worktree');
    }
  }
  let record = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(assignment.workspace_guid, assignment.repository_identity) as {
    target_ref: string;
    integrated_commit: string;
  } | undefined;
  if (record) {
    // A reused workspace GUID can carry a STALE integration_records row from its
    // prior lifecycle: main has since advanced past record.integrated_commit, so
    // the record-present branch below would throw 'lacks reachable integration
    // proof' on every reconciliation attempt, permanently stranding the row.
    // Both proofs are required before deleting a durable record on a crash-
    // recovery path — ancestry alone is not proof of staleness (a healthy,
    // never-advanced record is trivially its own ancestor).
    const staleCheckRef = targetRef(assignment);
    const recordIsAncestorOfTarget = isAncestor(exact.primaryCheckoutPath, record.integrated_commit, staleCheckRef);
    let staleCheckCandidate: string | undefined;
    try {
      staleCheckCandidate = runGit(
        exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`],
      ).trim();
    } catch { /* an absent candidate is not provable staleness; refuse below */ }
    const recordIsProvenStale = recordIsAncestorOfTarget
      && staleCheckCandidate !== undefined
      && staleCheckCandidate !== record.integrated_commit;
    if (recordIsProvenStale) {
      deleteIntegrationRecord(db, assignment.workspace_guid);
      record = undefined;
      // Finding 1: recycleFinalized deletes the prior lifecycle's candidate ref
      // best-effort AFTER its DB commit, so a leftover candidate ref can survive
      // and mismatch source HEAD, which would otherwise throw 'candidate and
      // source HEAD differ' in the no-record branch below. Clear it ONLY when
      // proven leftover (neither the current target nor the current source
      // HEAD), gated inside this proven-stale block, so the genuine no-record
      // 'candidate differs from source HEAD' case is untouched and its candidate
      // is never cleared.
      const staleCheckSourceHead = worktreeHead(assignment.worktree_path);
      const staleCheckCurrentTarget = runGit(
        exact.primaryCheckoutPath, ['rev-parse', '--verify', `${staleCheckRef}^{commit}`],
      ).trim();
      if (staleCheckCandidate !== staleCheckCurrentTarget && staleCheckCandidate !== staleCheckSourceHead) {
        try {
          runGit(exact.primaryCheckoutPath, ['update-ref', '-d', candidateRef(assignment.workspace_guid)]);
        } catch { /* candidate ref already absent */ }
      }
    }
  }
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
      if (input.rebaseRecovery) {
        // Only 'continue'|'abort' reach here — the no-paused-rebase modes returned
        // above. Narrow explicitly rather than widen recoverRebaseInProgress's mode.
        return recoverRebaseInProgress(db, exact, input.rebaseRecovery === 'abort' ? 'abort' : 'continue');
      }
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
        requireExactIntegrationLock(db, assignment, ref, expectedTarget);
        try {
          verifyPrimaryAfterFastForward(exact.primaryCheckoutPath, ref, expectedTarget, candidate);
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
        recycleFinalized(db, exact.primaryCheckoutPath, integrated);
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
  // `record` is captured below inside a db.transaction() closure; TypeScript does
  // not narrow a `let` through a closure boundary, so pin the durable value here.
  const durableRecord = record;
  const ref = targetRef(assignment);
  if (durableRecord.target_ref !== ref
    || runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${ref}^{commit}`]).trim() !== durableRecord.integrated_commit) {
    throw new Error('Crash reconciliation lacks reachable integration proof; preserving worktree');
  }
  const candidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
  if (candidate !== durableRecord.integrated_commit || worktreeHead(assignment.worktree_path) !== candidate) {
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
    `).run(durableRecord.integrated_commit, durableRecord.integrated_commit, nextDisposition, assignment.workspace_guid);
    if (result.changes !== 1) throw new Error('Crash reconciliation assignment state changed concurrently');
    transitionAssignment(db, assignment.workspace_guid, 'ready_for_integration', 'integrated');
  })();
  const integrated = getAssignment(db, assignment.workspace_guid)!;
  if (nextDisposition) {
    return {
      state: 'integrated-local',
      integratedCommit: durableRecord.integrated_commit,
      pushError: 'Remote has not proved the exact integrated candidate',
    };
  }
  recycleFinalized(db, exact.primaryCheckoutPath, integrated);
  return { state: 'cleaned', integratedCommit: durableRecord.integrated_commit };
}
