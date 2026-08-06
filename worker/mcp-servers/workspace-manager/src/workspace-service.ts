import { randomUUID } from 'node:crypto';
import path from 'node:path';
import type Database from 'better-sqlite3';
import {
  acquirePrimaryCheckoutOwnership,
  bindAssignmentOwner,
  consumeHumanIntent,
  consumeMatchingHumanIntent,
  createAssignment,
  getAssignment,
  issueHumanIntent,
  releasePrimaryCheckoutOwnership,
  transitionAssignment,
} from './db.js';
import {
  addWorktree,
  deleteTemporaryBranch,
  discoverRepository,
  ensureManagedWorktreeExclusion,
  isAncestor,
  listWorktrees,
  primaryBranch,
  removeWorktree,
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

export interface ProviderRootStatusRequest extends RepositoryRequest {
  ownerSessionId: string;
}

export type ProviderRootWorkspaceStatus =
  | { status: 'assigned'; assignment: Assignment }
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

  private primaryCheckoutIsOwned(repositoryIdentity: string): boolean {
    return this.db.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?')
      .get(repositoryIdentity) !== undefined;
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
    const assignment = createAssignment(this.db, {
      workspaceGuid: input.workspaceGuid,
      repositoryIdentity: repository.repositoryIdentity,
      worktreePath,
      branch,
      baseCommit,
      currentHead: baseCommit,
      ownerSessionId: input.ownerSessionId,
      workerId: input.workerId,
      integrationTarget: input.integrationTarget,
    });

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
    this.validateManagedIdentity(repository, assignments[0]);
    return { status: 'assigned', assignment: assignments[0] };
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
      if (primaryOwner) throw new Error('Primary checkout is already owned');
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

  abandonWorkspace(input: AssignmentRequest): Assignment {
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status === 'abandoned') return assignment;
    if (!nonterminal(assignment.lifecycle_status)) {
      throw new Error('Only unresolved managed worktrees can be abandoned');
    }
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'abandoned');
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
    this.validateManagedIdentity(repository, assignment);
    if (!worktreeIsClean(assignment.worktree_path)) {
      throw new Error('Managed worktree is dirty; preserving it');
    }
    if (this.primaryCheckoutIsOwned(repository.repositoryIdentity)) {
      throw new Error('Primary checkout remains owned; preserving managed worktree');
    }
    const actualHead = worktreeHead(assignment.worktree_path);
    if (assignment.lifecycle_status === 'abandoned') {
      if (!assignment.recovery_ref || !isAncestor(repository.primaryCheckoutPath, actualHead, assignment.recovery_ref)) {
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
        || actualHead !== assignment.integrated_commit
        || !isAncestor(repository.primaryCheckoutPath, assignment.integrated_commit, integration.target_ref)) {
        throw new Error('Integrated worktree lacks reachable durable integration evidence; preserving it');
      }
    }
    removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    deleteTemporaryBranch(repository.primaryCheckoutPath, assignment.branch);
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'cleaned');
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
