import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import path from 'node:path';
import type Database from 'better-sqlite3';
import {
  acquirePrimaryCheckoutOwnership,
  bindAssignmentOwner,
  consumeHumanIntent,
  consumeMatchingHumanIntent,
  createAssignment,
  getAssignment,
  insertPreservedWork,
  issueHumanIntent,
  reapStalePrimaryOwner,
  releasePrimaryCheckoutOwnership,
  reuseTerminalAssignment,
  transitionAssignment,
} from './db.js';
import { pushPendingSummary } from './integration.js';
import {
  addWorktree,
  deleteTemporaryBranch,
  discoverRepository,
  ensureManagedWorktreeExclusion,
  isAncestor,
  linkSharedResources,
  listWorktrees,
  primaryBranch,
  readSharedResourceConfig,
  removeWorktree,
  runGit,
  worktreeExists,
  worktreeHead,
  worktreeIsClean,
} from './git.js';
import type { RepositoryLocation } from './git.js';
import type { Assignment, AssignmentLifecycle, HumanIntentReceipt } from './types.js';

interface RepositoryRequest {
  repositoryPath: string;
}

interface AssignmentRequest extends RepositoryRequest {
  workspaceGuid: string;
  ownerSessionId: string;
}

interface AbandonWorkspaceInput extends AssignmentRequest {
  /**
   * 'rescue' reclaims the worktree DIRECTORY without losing unintegrated work:
   * any uncommitted change is committed onto the worker's OWN branch (never
   * main), the commit is recorded as recovery evidence, and only the worktree
   * directory is removed — the branch survives so a future reaper can still
   * reach the rescued commit.
   */
  mode?: 'rescue';
}

export interface ProviderRootStatusRequest extends RepositoryRequest {
  ownerSessionId: string;
}

export type ProviderRootWorkspaceStatus =
  | {
      status: 'assigned';
      assignment: Assignment;
      // These three fields are derived and always populated by
      // getWorkspaceStatusForRoot; they are typed optional only so that
      // unrelated inline constructors of this discriminated union (e.g.
      // test mocks elsewhere) need not restate them.
      /** Where this session's writes actually land: the primary checkout or its managed worktree. */
      effectiveRoot?: 'primary' | 'managed';
      /** True iff this exact session (repository + workspace GUID + owner session) owns the primary checkout. */
      primaryOwnedByThisSession?: boolean;
      /** Live Git HEAD of the assignment's worktree; falls back to the cached column if the read fails. */
      currentHead?: string;
    }
  | { status: 'unassigned'; repositoryIdentity: string; ownerSessionId: string };

interface IntentRequest extends AssignmentRequest {
  humanChannel: string;
  expectedEvidence?: unknown;
  nonce?: string;
}

export interface IssueCheckoutHumanIntentInput extends AssignmentRequest {
  humanChannel: string;
  operation: 'use-primary-checkout' | 'return-to-managed-worktree';
}

export interface EnsureSessionWorktreeInput extends RepositoryRequest {
  ownerSessionId: string;
  /** Commander allocation takes precedence over a provider-native root GUID. */
  workspaceGuid?: string;
  workerId?: string;
  integrationTarget?: string;
}

export interface ReserveWorkerWorktreeInput extends RepositoryRequest {
  workspaceGuid: string;
  workerId: string;
  /** Defaults to the primary checkout's current branch when omitted. */
  integrationTarget?: string;
}

export interface BindWorkerWorktreeInput extends RepositoryRequest {
  repositoryIdentity: string;
  workspaceGuid: string;
  workerId: string;
  ownerSessionId: string;
  expectedLifecycle: AssignmentLifecycle;
  expectedWorktreePath: string;
  expectedBranch: string;
  expectedBaseCommit: string;
  expectedCurrentHead: string;
}

export interface Reconciliation {
  repositoryIdentity: string;
  knownWorktreePaths: string[];
  missingWorktreePaths: string[];
  ambiguousWorktreePaths: string[];
}

function managedWorktreePath(primaryCheckoutPath: string, workspaceGuid: string): string {
  return path.join(primaryCheckoutPath, '.ironclaude', 'worktrees', workspaceGuid);
}

function managedBranch(workspaceGuid: string): string {
  return `ironclaude/${workspaceGuid}`;
}

function integrationTargetRef(target: string): string {
  return target.startsWith('refs/') ? target : `refs/heads/${target}`;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function validUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}

function selectWorkspaceGuid(input: EnsureSessionWorktreeInput): string {
  if (input.workspaceGuid !== undefined) {
    if (!validUuid(input.workspaceGuid)) throw new Error('workspaceGuid must be a UUID');
    return input.workspaceGuid;
  }
  return validUuid(input.ownerSessionId) ? input.ownerSessionId : randomUUID();
}

function nonterminal(status: AssignmentLifecycle): boolean {
  return status !== 'integrated' && status !== 'abandoned' && status !== 'cleaned';
}

function waitForConcurrentReservation(milliseconds: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

/**
 * Coordinates assignment records with real Git worktrees. It deliberately
 * returns target directories rather than claiming it can relocate an already
 * running provider process.
 */
export class WorkspaceService {
  constructor(private readonly db: Database.Database) {}

  /**
   * Proves a durable row still designates exactly its own managed worktree.
   * Existence alone is insufficient: a different branch at a reused path is
   * ambiguous and must be preserved for reconciliation.
   */
  private validateManagedIdentity(repository: RepositoryLocation, assignment: Assignment): void {
    const expectedPath = managedWorktreePath(repository.primaryCheckoutPath, assignment.workspace_guid);
    const expectedBranch = managedBranch(assignment.workspace_guid);
    if (assignment.worktree_path !== expectedPath || assignment.branch !== expectedBranch) {
      throw new Error('Durable assignment no longer matches canonical managed identity; reconciliation must preserve it');
    }
    const observed = listWorktrees(repository.primaryCheckoutPath)
      .find((worktree) => worktree.path === expectedPath);
    if (!observed || observed.branch !== `refs/heads/${assignment.branch}`) {
      throw new Error('Managed worktree Git identity does not match durable assignment; reconciliation must preserve it');
    }
  }

  /**
   * A repository-only match is not ownership: two different sessions on the
   * same repository must never be conflated. Ownership requires the exact
   * three-part binding (repository, workspace GUID, and owner session) that
   * `acquirePrimaryCheckoutOwnership` records.
   */
  private sessionOwnsPrimary(repositoryIdentity: string, workspaceGuid: string, ownerSessionId: string): boolean {
    return this.db.prepare(`
      SELECT 1 FROM primary_checkout_owners
      WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ?
    `).get(repositoryIdentity, workspaceGuid, ownerSessionId) !== undefined;
  }

  private materializeManagedWorktree(
    repository: RepositoryLocation,
    input: {
      workspaceGuid: string;
      ownerSessionId: string | null;
      workerId?: string;
      integrationTarget: string;
    },
  ): Assignment {
    const baseCommit = worktreeHead(repository.primaryCheckoutPath);
    const worktreePath = managedWorktreePath(repository.primaryCheckoutPath, input.workspaceGuid);
    const branch = managedBranch(input.workspaceGuid);
    const assignmentInput = {
      workspaceGuid: input.workspaceGuid,
      repositoryIdentity: repository.repositoryIdentity,
      worktreePath,
      branch,
      baseCommit,
      currentHead: baseCommit,
      ownerSessionId: input.ownerSessionId,
      workerId: input.workerId,
      integrationTarget: input.integrationTarget,
    };
    // A spent same-GUID row (terminal AND its worktree gone from disk and Git)
    // is reset-and-reused in place so a re-allocated provider root reclaims its
    // own retired GUID instead of colliding on the assignments PRIMARY KEY. A
    // live row never reaches here (ensureSessionWorktree early-returns it); a
    // terminal row whose worktree still exists is preserved by the worktreeGone
    // conjunct and collides instead, protecting unrecovered work.
    const priorRow = getAssignment(this.db, input.workspaceGuid);
    const worktreeGone = priorRow !== undefined
      && !existsSync(priorRow.worktree_path)
      && !worktreeExists(repository.primaryCheckoutPath, priorRow.worktree_path);
    const reuseSpent = priorRow !== undefined && priorRow.lifecycle_status === 'cleaned' && worktreeGone;
    let assignment: Assignment;
    if (reuseSpent) {
      try {
        runGit(repository.primaryCheckoutPath, ['update-ref', '-d', `refs/ironclaude/finalization/${input.workspaceGuid}/candidate`]);
      } catch { /* ref may be absent */ }
      assignment = reuseTerminalAssignment(this.db, assignmentInput);
    } else {
      assignment = createAssignment(this.db, assignmentInput);
    }

    return this.materializeReservedAssignment(repository, assignment);
  }

  private materializeReservedAssignment(
    repository: RepositoryLocation,
    assignment: Assignment,
  ): Assignment {
    try {
      addWorktree(
        repository.primaryCheckoutPath,
        assignment.worktree_path,
        assignment.branch,
        assignment.base_commit,
      );
      // A managed worktree is a clean checkout missing gitignored resources.
      // Plant symlinks for the explicitly configured shared paths (and exclude
      // them from Git's view) so resource-dependent tests can run in isolation.
      linkSharedResources(
        repository.primaryCheckoutPath,
        assignment.worktree_path,
        repository.repositoryIdentity,
        readSharedResourceConfig(repository.repositoryIdentity),
      );
      transitionAssignment(this.db, assignment.workspace_guid, 'reserved', 'materialized');
      return transitionAssignment(this.db, assignment.workspace_guid, 'materialized', 'active');
    } catch (error) {
      // The durable reserved record prevents a second owner from silently
      // taking this GUID or reusing its branch after a partial materialization.
      throw error;
    }
  }

  ensureSessionWorktree(input: EnsureSessionWorktreeInput): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const workspaceGuid = selectWorkspaceGuid(input);
    const existing = this.db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND owner_session_id = ?
        AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
      ORDER BY created_at ASC
      LIMIT 1
    `).get(repository.repositoryIdentity, input.ownerSessionId) as Assignment | undefined;
    if (existing) {
      if (existing.workspace_guid !== workspaceGuid) {
        throw new Error('Provider root is already bound to a different managed workspace');
      }
      this.validateManagedIdentity(repository, existing);
      return existing;
    }

    ensureManagedWorktreeExclusion(repository.repositoryIdentity);
    return this.materializeManagedWorktree(repository, {
      workspaceGuid,
      ownerSessionId: input.ownerSessionId,
      workerId: input.workerId,
      integrationTarget: input.integrationTarget ?? primaryBranch(repository.primaryCheckoutPath),
    });
  }

  reserveWorkerWorktree(input: ReserveWorkerWorktreeInput): Assignment {
    if (!validUuid(input.workspaceGuid)) throw new Error('workspaceGuid must be a UUID');
    if (input.workerId.length === 0) throw new Error('workerId must not be empty');
    if (input.integrationTarget !== undefined && input.integrationTarget.length === 0) {
      throw new Error('integrationTarget must not be empty');
    }
    const repository = discoverRepository(input.repositoryPath);
    const baseCommit = worktreeHead(repository.primaryCheckoutPath);
    const worktreePath = managedWorktreePath(repository.primaryCheckoutPath, input.workspaceGuid);
    const branch = managedBranch(input.workspaceGuid);

    const claim = this.db.transaction((): { assignment: Assignment; created: boolean } => {
      const existing = getAssignment(this.db, input.workspaceGuid);
      if (existing) {
        this.assertMatchingWorkerReservation(repository, existing, input);
        return { assignment: existing, created: false };
      }
      const other = this.db.prepare(`
        SELECT * FROM assignments
        WHERE repository_identity = ? AND worker_id = ?
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
        ORDER BY created_at ASC LIMIT 1
      `).get(repository.repositoryIdentity, input.workerId) as Assignment | undefined;
      if (other) throw new Error('Worker is already reserved to a different managed workspace');
      return {
        assignment: createAssignment(this.db, {
          workspaceGuid: input.workspaceGuid,
          repositoryIdentity: repository.repositoryIdentity,
          worktreePath,
          branch,
          baseCommit,
          currentHead: baseCommit,
          ownerSessionId: null,
          workerId: input.workerId,
          integrationTarget: input.integrationTarget ?? primaryBranch(repository.primaryCheckoutPath),
        }),
        created: true,
      };
    }).immediate();

    if (claim.created) {
      ensureManagedWorktreeExclusion(repository.repositoryIdentity);
      return this.materializeReservedAssignment(repository, claim.assignment);
    }
    return this.waitForActiveWorkerReservation(repository, input);
  }

  private assertMatchingWorkerReservation(
    repository: RepositoryLocation,
    assignment: Assignment,
    input: ReserveWorkerWorktreeInput,
  ): void {
    if (assignment.repository_identity !== repository.repositoryIdentity
      || assignment.worker_id !== input.workerId
      || assignment.integration_target !== input.integrationTarget
      || assignment.owner_session_id !== null
      || !['reserved', 'materialized', 'active'].includes(assignment.lifecycle_status)) {
      throw new Error('Durable worker reservation does not match requested allocation');
    }
  }

  private waitForActiveWorkerReservation(
    repository: RepositoryLocation,
    input: ReserveWorkerWorktreeInput,
  ): Assignment {
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
      const current = getAssignment(this.db, input.workspaceGuid);
      if (!current) throw new Error('Durable worker reservation disappeared during allocation');
      this.assertMatchingWorkerReservation(repository, current, input);
      if (current.lifecycle_status === 'active') {
        this.validateManagedIdentity(repository, current);
        if (worktreeHead(current.worktree_path) !== current.current_head) {
          throw new Error('Durable worker reservation HEAD does not match materialized worktree');
        }
        return current;
      }
      waitForConcurrentReservation(25);
    }
    throw new Error('Matching worker reservation is still materializing; preserving durable assignment');
  }

  bindWorkerWorktree(input: BindWorkerWorktreeInput): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment
      || input.repositoryIdentity !== repository.repositoryIdentity
      || assignment.repository_identity !== input.repositoryIdentity
      || assignment.worker_id !== input.workerId
      || input.expectedLifecycle !== 'active'
      || assignment.lifecycle_status !== input.expectedLifecycle
      || assignment.worktree_path !== input.expectedWorktreePath
      || assignment.branch !== input.expectedBranch
      || assignment.base_commit !== input.expectedBaseCommit
      || assignment.current_head !== input.expectedCurrentHead) {
      throw new Error('Worker reservation evidence does not match durable assignment');
    }
    this.validateManagedIdentity(repository, assignment);
    if (worktreeHead(assignment.worktree_path) !== assignment.current_head) {
      throw new Error('Worker reservation Git HEAD does not match durable assignment');
    }
    return bindAssignmentOwner(this.db, assignment.workspace_guid, input.ownerSessionId);
  }

  getWorkspaceAssignment(input: AssignmentRequest): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment
      || assignment.repository_identity !== repository.repositoryIdentity
      || assignment.owner_session_id !== input.ownerSessionId) {
      throw new Error('Workspace assignment binding does not match repository and provider root');
    }
    return assignment;
  }

  getWorkspaceStatusForRoot(input: ProviderRootStatusRequest): ProviderRootWorkspaceStatus {
    const repository = discoverRepository(input.repositoryPath);
    const assignments = this.db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND owner_session_id = ?
        AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
      ORDER BY created_at ASC
    `).all(repository.repositoryIdentity, input.ownerSessionId) as Assignment[];
    if (assignments.length === 0) {
      return {
        status: 'unassigned',
        repositoryIdentity: repository.repositoryIdentity,
        ownerSessionId: input.ownerSessionId,
      };
    }
    if (assignments.length !== 1) {
      throw new Error('Workspace status is ambiguous for provider root and repository');
    }
    const assignment = assignments[0];
    this.validateManagedIdentity(repository, assignment);
    const primaryOwnedByThisSession = this.sessionOwnsPrimary(
      repository.repositoryIdentity,
      assignment.workspace_guid,
      input.ownerSessionId,
    );
    let currentHead: string;
    try {
      currentHead = worktreeHead(assignment.worktree_path);
    } catch {
      currentHead = assignment.current_head;
    }
    return {
      status: 'assigned',
      assignment,
      effectiveRoot: primaryOwnedByThisSession ? 'primary' : 'managed',
      primaryOwnedByThisSession,
      currentHead,
    };
  }

  private checkoutIntentEvidence(
    repository: RepositoryLocation,
    assignment: Assignment,
    ownerSessionId: string,
    operation: IssueCheckoutHumanIntentInput['operation'],
  ): Record<string, unknown> {
    this.validateManagedIdentity(repository, assignment);
    const primaryOwner = this.db.prepare(`
      SELECT workspace_guid, owner_session_id FROM primary_checkout_owners
      WHERE repository_identity = ?
    `).get(repository.repositoryIdentity) as { workspace_guid: string; owner_session_id: string } | undefined;
    if (operation === 'use-primary-checkout') {
      // Reap a dead/timed-out owner before the exclusivity throw so a stale row
      // cannot deadlock issuance; a surviving (live) owner still blocks.
      reapStalePrimaryOwner(this.db, repository.repositoryIdentity);
      if (this.db.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?')
        .get(repository.repositoryIdentity)) {
        throw new Error('Primary checkout is already owned');
      }
    } else if (!primaryOwner
      || primaryOwner.workspace_guid !== assignment.workspace_guid
      || primaryOwner.owner_session_id !== ownerSessionId) {
      throw new Error('Primary checkout ownership does not match assignment binding');
    }
    return {
      checkoutMode: operation === 'use-primary-checkout' ? 'managed' : 'primary',
      primaryCheckoutPath: repository.primaryCheckoutPath,
      managedWorktreePath: assignment.worktree_path,
      branch: assignment.branch,
      currentHead: worktreeHead(assignment.worktree_path),
      lifecycleStatus: assignment.lifecycle_status,
    };
  }

  issueCheckoutHumanIntent(input: IssueCheckoutHumanIntentInput): HumanIntentReceipt {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    const expectedEvidence = this.checkoutIntentEvidence(
      repository,
      assignment,
      input.ownerSessionId,
      input.operation,
    );
    return issueHumanIntent(this.db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.ownerSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence,
    });
  }

  /** Human approval is required before logical ownership of primary checkout. */
  usePrimaryCheckout(input: IntentRequest): { primaryCheckoutPath: string; assignment: Assignment } {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if ((input.expectedEvidence === undefined) !== (input.nonce === undefined)) {
      throw new Error('Primary checkout switching authority input is malformed');
    }
    const legacyAuthority = input.expectedEvidence !== undefined;
    if (legacyAuthority) this.validateManagedIdentity(repository, assignment);
    const expectedEvidence = legacyAuthority
      ? input.expectedEvidence
      : this.checkoutIntentEvidence(repository, assignment, input.ownerSessionId, 'use-primary-checkout');
    const intentInput = {
      operation: 'use-primary-checkout',
      humanChannel: input.humanChannel,
      providerRootSessionId: input.ownerSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence,
    } as const;
    const intent = input.nonce === undefined
      ? consumeMatchingHumanIntent(this.db, intentInput)
      : consumeHumanIntent(this.db, { ...intentInput, nonce: input.nonce });
    if (!intent) throw new Error('Primary checkout switching requires a matching human intent');
    acquirePrimaryCheckoutOwnership(this.db, {
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: input.ownerSessionId,
    });
    return { primaryCheckoutPath: repository.primaryCheckoutPath, assignment };
  }

  /** Human approval is also required to release primary checkout ownership. */
  returnToManagedWorktree(input: IntentRequest): { managedWorktreePath: string; assignment: Assignment } {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    // This must precede intent consumption and ownership release: a malformed
    // durable row cannot turn a primary checkout into an unowned checkout.
    if ((input.expectedEvidence === undefined) !== (input.nonce === undefined)) {
      throw new Error('Managed worktree switching authority input is malformed');
    }
    const legacyAuthority = input.expectedEvidence !== undefined;
    if (legacyAuthority) this.validateManagedIdentity(repository, assignment);
    const expectedEvidence = legacyAuthority
      ? input.expectedEvidence
      : this.checkoutIntentEvidence(repository, assignment, input.ownerSessionId, 'return-to-managed-worktree');
    const intentInput = {
      operation: 'return-to-managed-worktree',
      humanChannel: input.humanChannel,
      providerRootSessionId: input.ownerSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence,
    } as const;
    const intent = input.nonce === undefined
      ? consumeMatchingHumanIntent(this.db, intentInput)
      : consumeHumanIntent(this.db, { ...intentInput, nonce: input.nonce });
    if (!intent) throw new Error('Managed worktree switching requires a matching human intent');
    releasePrimaryCheckoutOwnership(this.db, repository.repositoryIdentity, assignment.workspace_guid, input.ownerSessionId);
    return { managedWorktreePath: assignment.worktree_path, assignment };
  }

  abandonWorkspace(input: AbandonWorkspaceInput): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status === 'abandoned') return assignment;
    if (!nonterminal(assignment.lifecycle_status)) {
      throw new Error('Only unresolved managed worktrees can be abandoned');
    }
    // A live owner must not abandon itself into a stale ownership row that then
    // deadlocks the next acquirer until the TTL elapses: refuse while it holds the
    // primary checkout, scoped to this exact assignment so a different session's
    // ownership never blocks this one.
    if (this.db.prepare(`
      SELECT 1 FROM primary_checkout_owners
      WHERE repository_identity = ? AND workspace_guid = ?
    `).get(assignment.repository_identity, assignment.workspace_guid)) {
      throw new Error('Return to the managed worktree before abandoning while holding the primary checkout.');
    }
    if (input.mode === 'rescue') {
      return this.rescueAbandon(repository, assignment);
    }
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'abandoned');
  }

  /**
   * Commits any uncommitted worktree content onto the worker's OWN branch
   * (never main), mints a durable `refs/ironclaude/recovery/<guid>` ref at that
   * commit and records the REF NAME as recovery evidence, transitions the
   * assignment to abandoned, then removes ONLY the worktree directory. The
   * durable ref — not the worker branch — is the anchor: a later reaper may
   * delete the branch, and the rescued commit stays reachable through the ref.
   */
  private rescueAbandon(repository: RepositoryLocation, assignment: Assignment): Assignment {
    const worktreePresent = existsSync(assignment.worktree_path)
      && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (worktreePresent) {
      if (!worktreeIsClean(assignment.worktree_path)) {
        runGit(assignment.worktree_path, ['add', '-A']);
        runGit(assignment.worktree_path, ['commit', '-m', 'ironclaude: rescue-commit before reclaiming worktree']);
      }
      const rescuedHead = worktreeHead(assignment.worktree_path);
      const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
      runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, rescuedHead]);
      this.db.prepare(`
        UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
      `).run(recoveryRef, assignment.workspace_guid);
    }
    const abandoned = transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'abandoned');
    if (worktreePresent) removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    return abandoned;
  }

  /**
   * Carve-out for a reserved row that never got as far as owning a real
   * worktree (`addWorktree` never ran or failed before it could complete):
   * there is nothing on disk to remove and no branch to preserve, so the row
   * is deleted outright. Refuses — deferring to `cleanupWorkspace`'s proven
   * proofs — the moment a worktree actually exists for this row.
   */
  cleanupReservedAssignment(input: AssignmentRequest): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status !== 'reserved') {
      throw new Error('Only a reserved, never-materialized assignment is eligible for this carve-out');
    }
    if (existsSync(assignment.worktree_path) || worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path)) {
      throw new Error('Reserved assignment has a materialized worktree; use cleanupWorkspace instead');
    }
    const result = this.db.prepare(`
      DELETE FROM assignments WHERE workspace_guid = ? AND lifecycle_status = 'reserved'
    `).run(assignment.workspace_guid);
    if (result.changes !== 1) throw new Error('Reserved assignment changed concurrently');
    return { ...assignment, lifecycle_status: 'cleaned' as const };
  }

  /** True iff `ref` resolves in `root`; a missing or unresolvable ref returns false rather than throwing. */
  private refResolves(root: string, ref: string): boolean {
    try {
      runGit(root, ['rev-parse', '--verify', '--quiet', ref]);
      return true;
    } catch {
      return false;
    }
  }

  /** True iff `ref` is an actual git ref (not merely a resolvable object such as a raw SHA). */
  private refIsDurableRef(root: string, ref: string): boolean {
    try {
      runGit(root, ['show-ref', '--verify', '--quiet', ref]);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Guarantees a durable git ref anchors an abandoned row's recovery commit before its
   * branch can be deleted. A ref-name `recovery_ref` is returned unchanged (no-op). A
   * legacy raw-SHA `recovery_ref` that still resolves to a reachable object is upgraded:
   * mint `refs/ironclaude/recovery/<guid>` at that commit and record the REF NAME. A
   * `recovery_ref` that is neither a durable ref nor a reachable object throws, so the
   * row and its branch are preserved (never-lose-work).
   */
  private ensureDurableRecoveryAnchor(repository: RepositoryLocation, assignment: Assignment): string {
    const current = assignment.recovery_ref;
    if (!current) throw new Error('Abandoned assignment lacks recovery evidence; preserving it');
    if (this.refIsDurableRef(repository.primaryCheckoutPath, current)) return current;
    if (!this.refResolves(repository.primaryCheckoutPath, current)) {
      throw new Error('Recovery evidence is neither a durable ref nor a reachable commit; preserving it');
    }
    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, current]);
    this.db.prepare(`
      UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
    `).run(recoveryRef, assignment.workspace_guid);
    return recoveryRef;
  }

  /**
   * Deletes a terminal (integrated or abandoned) assignment only when its
   * recorded recovery/integration proof still holds, then removes the worktree
   * (when present) and its private branch. Every failed proof preserves work.
   *
   * Handles both the present-worktree case (proof anchored on the live worktree
   * HEAD, byte-identical to the original cleanup path) and the worktree-gone
   * case a reaper reaches after `rescueAbandon` has already removed the
   * directory: there the abandoned proof is that the durable recovery ref still
   * resolves, and branch deletion is skipped when the branch is already gone (a
   * `git branch -D` on a nonexistent branch would otherwise throw).
   */
  private tombstoneTerminalAssignment(repository: RepositoryLocation, assignment: Assignment): Assignment {
    const present = existsSync(assignment.worktree_path)
      && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (present && !worktreeIsClean(assignment.worktree_path)) {
      throw new Error('Managed worktree is dirty; preserving it');
    }
    if (assignment.lifecycle_status === 'abandoned') {
      if (present) {
        if (!assignment.recovery_ref
          || !isAncestor(repository.primaryCheckoutPath, worktreeHead(assignment.worktree_path), assignment.recovery_ref)) {
          throw new Error('Abandoned worktree lacks reachable durable recovery evidence; preserving it');
        }
      } else if (!assignment.recovery_ref || !this.refResolves(repository.primaryCheckoutPath, assignment.recovery_ref)) {
        throw new Error('Abandoned worktree lacks reachable durable recovery evidence; preserving it');
      }
    } else {
      const integration = this.db.prepare(`
        SELECT target_ref, integrated_commit FROM integration_records
        WHERE workspace_guid = ? AND repository_identity = ?
      `).get(assignment.workspace_guid, repository.repositoryIdentity) as {
        target_ref: string;
        integrated_commit: string;
      } | undefined;
      if (!assignment.integrated_commit
        || !integration
        || integration.target_ref !== integrationTargetRef(assignment.integration_target)
        || integration.integrated_commit !== assignment.integrated_commit
        || (present && worktreeHead(assignment.worktree_path) !== assignment.integrated_commit)
        || !isAncestor(repository.primaryCheckoutPath, assignment.integrated_commit, integration.target_ref)) {
        throw new Error('Integrated worktree lacks reachable durable integration evidence; preserving it');
      }
    }
    if (assignment.lifecycle_status === 'abandoned') {
      this.ensureDurableRecoveryAnchor(repository, assignment);
    }
    if (present) removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    // Safe now that the recovery/integration ref anchors the commit; skip when
    // the branch is already gone so cleanup of a branch-reaped row still tombstones.
    if (this.refResolves(repository.primaryCheckoutPath, `refs/heads/${assignment.branch}`)) {
      deleteTemporaryBranch(repository.primaryCheckoutPath, assignment.branch);
    }
    // Never-lose-work: carry any outstanding push obligation into the durable preserved_work
    // table (drained later by a human /push of local main) rather than refusing to reclaim. The
    // transition and the carry are one transaction so a crash never lands the row cleaned with the
    // obligation lost from both the row and the table. pushPendingSummary excludes push-succeeded.
    const carried = pushPendingSummary(assignment.disposition);
    return this.db.transaction(() => {
      const cleaned = transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'cleaned');
      if (carried) {
        insertPreservedWork(this.db, {
          workspaceGuid: assignment.workspace_guid,
          repositoryIdentity: repository.repositoryIdentity,
          ownerSessionId: assignment.owner_session_id,
          kind: 'pending-push',
          payload: JSON.stringify(carried),
        });
      }
      return cleaned;
    })();
  }

  /**
   * Deletes only a terminal assignment whose recorded recovery/integration
   * proof still reaches its actual Git HEAD. Every failed proof preserves work.
   */
  cleanupWorkspace(input: AssignmentRequest): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status !== 'integrated' && assignment.lifecycle_status !== 'abandoned') {
      throw new Error('Only integrated or abandoned worktrees are eligible for cleanup');
    }
    const present = existsSync(assignment.worktree_path)
      && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (present) this.validateManagedIdentity(repository, assignment);
    return this.tombstoneTerminalAssignment(repository, assignment);
  }

  /**
   * Owner-agnostic reaper for a LEAKED managed assignment — one whose owning
   * session is gone, so `cleanupWorkspace`'s owner-match can never fire. It
   * still proves canonical managed identity (repository, path, branch) before
   * touching anything, and preserves work at every step: a present worktree is
   * rescued (`rescueAbandon` anchors its content on a durable recovery ref), a
   * worktree-gone row mints a recovery ref at the surviving branch tip (or, when
   * even the branch is gone, at the recorded base commit) BEFORE transitioning
   * to abandoned, and a present worktree on a foreign branch is refused and
   * preserved. Only after work is anchored does it tombstone the row.
   */
  reapLeakedAssignment(input: { repositoryPath: string; workspaceGuid: string }): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment
      || assignment.repository_identity !== repository.repositoryIdentity
      || assignment.worktree_path !== managedWorktreePath(repository.primaryCheckoutPath, assignment.workspace_guid)
      || assignment.branch !== managedBranch(assignment.workspace_guid)) {
      throw new Error('Leaked assignment does not match canonical managed identity for this repository');
    }
    if (assignment.lifecycle_status === 'cleaned') return assignment;
    if (assignment.lifecycle_status === 'reserved') {
      return this.reapReservedAssignment(repository, assignment);
    }
    const present = existsSync(assignment.worktree_path)
      && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    // A present worktree on a foreign branch is ambiguous; refuse and preserve it.
    if (present) this.validateManagedIdentity(repository, assignment);
    if (assignment.lifecycle_status !== 'integrated' && assignment.lifecycle_status !== 'abandoned') {
      if (present) {
        this.rescueAbandon(repository, assignment);
      } else {
        const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
        const branchRef = `refs/heads/${assignment.branch}`;
        const anchor = this.refResolves(repository.primaryCheckoutPath, branchRef)
          ? branchRef
          : assignment.base_commit;
        // Mint the durable recovery ref BEFORE transitioning to abandoned: work
        // must be anchored before the row is marked terminal.
        runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, anchor]);
        this.db.prepare(`
          UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
        `).run(recoveryRef, assignment.workspace_guid);
        transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'abandoned');
      }
    }
    return this.tombstoneTerminalAssignment(repository, getAssignment(this.db, input.workspaceGuid)!);
  }

  /**
   * Owner-agnostic variant of `cleanupReservedAssignment`'s carve-out: a
   * reserved row that never materialized a real worktree has nothing on disk to
   * remove and no branch to preserve, so the row is deleted outright. Refuses
   * the moment a worktree actually exists — that row is not a bare reservation.
   */
  private reapReservedAssignment(repository: RepositoryLocation, assignment: Assignment): Assignment {
    if (existsSync(assignment.worktree_path) || worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path)) {
      throw new Error('Reserved assignment has a materialized worktree; use cleanupWorkspace instead');
    }
    const result = this.db.prepare(`
      DELETE FROM assignments WHERE workspace_guid = ? AND lifecycle_status = 'reserved'
    `).run(assignment.workspace_guid);
    if (result.changes !== 1) throw new Error('Reserved assignment changed concurrently');
    return { ...assignment, lifecycle_status: 'cleaned' as const };
  }

  /** Read-only reconciliation intentionally never deletes missing or unknown worktrees. */
  reconcileRepository(repositoryPath: string): Reconciliation {
    const repository = discoverRepository(repositoryPath);
    const assignments = this.db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND lifecycle_status <> 'cleaned'
    `).all(repository.repositoryIdentity) as Assignment[];
    const observed = listWorktrees(repository.primaryCheckoutPath);
    const observedPaths = new Set(observed.map((worktree) => worktree.path));
    const knownPaths = new Set(assignments.map((assignment) => path.resolve(assignment.worktree_path)));
    const managedRoot = path.join(repository.primaryCheckoutPath, '.ironclaude', 'worktrees') + path.sep;
    const ambiguousWorktreePaths = observed
      .filter((worktree) => worktree.path.startsWith(managedRoot)
        && worktree.branch?.startsWith('refs/heads/ironclaude/')
        && !knownPaths.has(worktree.path))
      .map((worktree) => worktree.path)
      .sort();
    return {
      repositoryIdentity: repository.repositoryIdentity,
      knownWorktreePaths: assignments
        .map((assignment) => path.resolve(assignment.worktree_path))
        .filter((worktreePath) => observedPaths.has(worktreePath))
        .sort(),
      missingWorktreePaths: assignments
        .map((assignment) => path.resolve(assignment.worktree_path))
        .filter((worktreePath) => !observedPaths.has(worktreePath))
        .sort(),
      ambiguousWorktreePaths,
    };
  }
}
