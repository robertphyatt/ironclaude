import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
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
import { assertNoPrimaryOverlap, primaryOnRef, pushPendingSummary, verifyPrimaryAfterFastForward } from './integration.js';
import {
  addSharedResourceEntries,
  addWorktree,
  canonicalDefaultBranchRef,
  carryForwardFastForward,
  contentMergedInto,
  deleteTemporaryBranch,
  discoverRepository,
  ensureManagedWorktreeExclusion,
  gitError,
  GIT_MAX_BUFFER,
  gitSupportsMergeTreeWriteTree,
  isAncestor,
  linkSharedResources,
  listManagedBranches,
  listWorktrees,
  originHeadBranchRef,
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

interface ConfigureSharedResourcesInput extends RepositoryRequest {
  entries: readonly string[];
  allowSecretEntries?: boolean;
}

interface ConfigureSharedResourcesResult {
  added: string[];
  skipped: string[];
  rejected: string[];
  secretBlocked: string[];
  entries: string[];
  relinked: Record<string, string[]>;
  secretHits: Record<string, string[]>;
  scanTruncated: string[];
}

interface ListSharedResourcesResult {
  entries: string[];
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

/** One PRESERVED ambiguous orphan, classified and surfaced for a human decision. */
export interface PreservedOrphan {
  id: string;
  guid: string;
  branch: string;
  category: 'squash-merged' | 'merged-on-origin' | 'genuinely-unmerged' | 'dirty';
  tip: string;
  worktreePresent: boolean;
  evidence: string;
  muted: boolean;
}

/** One operator-authorized disposition for a previously surfaced ambiguous orphan. */
export interface OrphanResolutionRequest {
  /** The 8-hex short id `reapAmbiguousOrphans` reported; either this or `guid` must be given. */
  id?: string;
  guid?: string;
  action: 'reap' | 'keep' | 'merge-then-reap';
  /**
   * Explicit integration target branch for `merge-then-reap`, overriding the
   * repository's canonical default branch. An orphan carries no durable
   * integration target of its own (unlike an assignment's `integration_target`,
   * captured once at worktree materialization), so without this override the
   * target is `canonicalDefaultBranchRef`: `refs/remotes/origin/HEAD`'s
   * branch, falling back to `main` when origin/HEAD is unset. Ignored for
   * `reap`/`keep`.
   */
  integrationTarget?: string;
  /**
   * The orphan category the operator's consent was obtained against (from
   * the surface the operator was shown). For `action: 'reap'`, a dirty
   * worktree is force-removed ONLY when this equals `'dirty'` AND the
   * currently persisted `orphan_surface.category` also equals `'dirty'` —
   * `row.category` alone is refreshed by the daemon sweep independently of
   * consent, so it can silently flip to `'dirty'` after a non-dirty category
   * was surfaced and consented to. Without a matching category, a dirty
   * worktree is refused (`refused-changed`) rather than force-discarded.
   * Ignored for `keep`/`merge-then-reap` (the latter has its own dirty
   * refusal, unaffected by this field).
   */
  category?: 'squash-merged' | 'merged-on-origin' | 'genuinely-unmerged' | 'dirty';
}

export interface OrphanResolutionOutcome {
  id: string;
  guid: string;
  outcome: string;
  error?: string;
}

export interface ResolveOrphanInput {
  repositoryPath: string;
  protectedPaths?: string[];
  resolutions: OrphanResolutionRequest[];
}

export interface ResolveOrphanResult {
  results: OrphanResolutionOutcome[];
}

export interface OrphanReapResult {
  repositoryIdentity: string;
  reaped: string[];
  reapedWorktreeOnly: string[];  // worktree removed but its branch delete failed (partial)
  preservedDirty: string[];
  preservedUnmerged: string[];
  preservedDetail: PreservedOrphan[];
  skippedLive: string[];
  skippedYoung: string[];
  errors: { name: string; error: string }[];
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

  /**
   * Owner-agnostic sweep over every AMBIGUOUS orphan — a managed-shaped
   * worktree or branch (`ironclaude/<guid>`) with NO assignments row at all,
   * so neither `cleanupWorkspace` nor `reapLeakedAssignment` can ever reach
   * it. Every disposition preserves work by default: only a worktree proven
   * clean, old enough (`ttlHours`), unprotected, and whose branch tip is an
   * ancestor of the reaper target (origin/HEAD's default branch when it exists
   * locally, else the primary checkout's branch) is actually removed. A
   * dangling branch with no worktree at all is reaped the same way, by branch
   * tip alone.
   */
  reapAmbiguousOrphans(input: { repositoryPath: string; protectedPaths?: string[]; ttlHours?: number }): OrphanReapResult {
    const repository = discoverRepository(input.repositoryPath);
    const protectedSet = new Set((input.protectedPaths ?? []).map((p) => path.resolve(p)));
    const ttlHours = input.ttlHours ?? 24;
    const cutoffMs = Date.now() - ttlHours * 3600 * 1000;
    // origin/HEAD's branch when set AND present locally; otherwise the primary
    // checkout's current branch (the v1.1.11 behavior). origin/HEAD can name a
    // branch with no local ref — a `clone -b` checkout, or a stale origin/HEAD
    // after a remote default-branch rename — and judging against a missing ref
    // would misread every orphan as unmerged. Only when the fallback is needed
    // does a detached primary matter: primaryBranch throws and the sweep fails
    // safe, reaping nothing (a usable origin/HEAD makes it irrelevant). The reaper
    // never advances a ref, unlike mergeOrphanThenReap, which keeps
    // canonicalDefaultBranchRef's fail-closed default.
    const originHead = originHeadBranchRef(repository.primaryCheckoutPath);
    const target = originHead && this.refResolves(repository.primaryCheckoutPath, originHead)
      ? originHead
      : integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));

    const result: OrphanReapResult = {
      repositoryIdentity: repository.repositoryIdentity,
      reaped: [], reapedWorktreeOnly: [], preservedDirty: [], preservedUnmerged: [], preservedDetail: [],
      skippedLive: [], skippedYoung: [], errors: [],
    };

    const ambiguous = new Map<string, string>();
    for (const worktreePath of this.reconcileRepository(input.repositoryPath).ambiguousWorktreePaths) {
      ambiguous.set(path.basename(worktreePath), worktreePath);
    }
    const guids = new Set<string>([...ambiguous.keys()]);
    for (const branch of listManagedBranches(repository.primaryCheckoutPath)) {
      guids.add(branch.slice('ironclaude/'.length));
    }

    // Prune surfaced rows whose orphan no longer exists at all (branch AND
    // worktree gone — e.g. removed out-of-band since it was surfaced); these
    // guids are never visited by the loop below, so nothing else would delete
    // them. This is the sweep, which is allowed to mutate.
    const surfacedRows = this.db.prepare(
      'SELECT workspace_guid FROM orphan_surface WHERE repository_identity = ?',
    ).all(repository.repositoryIdentity) as Array<{ workspace_guid: string }>;
    for (const { workspace_guid } of surfacedRows) {
      if (!guids.has(workspace_guid)) {
        this.deleteOrphanSurface(repository.repositoryIdentity, workspace_guid);
      }
    }

    const scratchDir = mkdtempSync(path.join(os.tmpdir(), 'ironclaude-orphan-'));
    try {
      for (const guid of guids) {
        const name = managedBranch(guid);
        try {
          if (this.hasNonCleanedAssignment(repository.repositoryIdentity, guid)) continue;
          const branchRef = `refs/heads/${name}`;
          const hasBranch = this.refResolves(repository.primaryCheckoutPath, branchRef);
          const worktreePath = ambiguous.get(guid);
          // A registered worktree whose directory was removed outside git still
          // surfaces here (listWorktrees tolerates the ENOENT rather than
          // throwing), but its directory is gone: treat it the same as no
          // worktree at all rather than calling worktreeIsClean/worktreeHead
          // against a path that no longer exists on disk.
          const present = worktreePath !== undefined && existsSync(worktreePath);
          if (present && protectedSet.has(path.resolve(worktreePath!))) { result.skippedLive.push(name); continue; }
          const tip = hasBranch
            ? runGit(repository.primaryCheckoutPath, ['rev-parse', branchRef]).trim()
            : present ? worktreeHead(worktreePath!) : null;
          if (tip === null) continue;
          const committedMs = Number(runGit(repository.primaryCheckoutPath, ['show', '-s', '--format=%ct', tip]).trim()) * 1000;
          if (committedMs > cutoffMs) { result.skippedYoung.push(name); continue; }
          if (present && !worktreeIsClean(worktreePath!)) {
            result.preservedDirty.push(name);
            let evidence = `worktree at ${worktreePath} has uncommitted changes`;
            if (!isAncestor(repository.primaryCheckoutPath, tip, target)) {
              const unmergedCount = runGit(
                repository.primaryCheckoutPath, ['rev-list', '--count', `${target}..${tip}`],
              ).trim();
              evidence += `; also ${unmergedCount} unmerged commit(s) not on ${target} (lost on reap)`;
            }
            result.preservedDetail.push(this.buildPreservedOrphan(
              repository, guid, name, tip, 'dirty',
              evidence, present,
            ));
            continue;
          }
          if (!isAncestor(repository.primaryCheckoutPath, tip, target)) {
            result.preservedUnmerged.push(name);
            const classified = this.classifyPreservedOrphan(repository, tip, target, scratchDir);
            result.preservedDetail.push(this.buildPreservedOrphan(
              repository, guid, name, tip, classified.category, classified.evidence, present,
            ));
            continue;
          }
          let worktreeRemoved = false;
          if (present) {
            removeWorktree(repository.primaryCheckoutPath, worktreePath!);
            worktreeRemoved = true;
          } else if (worktreePath !== undefined) {
            // Registered in git's worktree administration but its directory is
            // gone: targeted equivalent of `git worktree prune` for just this
            // one entry, so the branch delete below is not refused as "used
            // by worktree" — no blanket repository-wide prune.
            removeWorktree(repository.primaryCheckoutPath, worktreePath, { force: true });
            worktreeRemoved = true;
          }
          try {
            if (this.refResolves(repository.primaryCheckoutPath, branchRef)) {
              deleteTemporaryBranch(repository.primaryCheckoutPath, name);
            }
            result.reaped.push(name);
            this.deleteOrphanSurface(repository.repositoryIdentity, guid);
          } catch (branchError) {
            if (worktreeRemoved) result.reapedWorktreeOnly.push(name);
            result.errors.push({ name, error: branchError instanceof Error ? branchError.message : String(branchError) });
          }
        } catch (error) {
          result.errors.push({ name, error: error instanceof Error ? error.message : String(error) });
        }
      }
    } finally {
      rmSync(scratchDir, { recursive: true, force: true });
    }
    return result;
  }

  /**
   * Read-only enumeration of the currently-surfaced, currently-LIVE orphans
   * for a repo (the rows `reapAmbiguousOrphans` wrote to `orphan_surface`),
   * for the Brain's per-orphan walkthrough. Excludes rows the operator has
   * `keep`-muted (muted_tip === tip) so a kept orphan drops out until its tip
   * changes. Also excludes a row whose branch ref no longer resolves AND
   * whose worktree directory no longer exists — i.e. the orphan was removed
   * out-of-band (outside the tool chain) since it was surfaced. That check is
   * read-only: the stale `orphan_surface` row is left in place for the sweep
   * to prune later. Purely read-only overall: this method never writes, and
   * never re-runs the reaper.
   */
  listSurfacedOrphans(input: { repositoryPath: string }): {
    orphans: Array<{ id: string; workspace_guid: string; branch: string; tip: string; category: string }>;
  } {
    const repository = discoverRepository(input.repositoryPath);
    const rows = this.db.prepare(`
      SELECT workspace_guid, short_id, tip, category, muted_tip FROM orphan_surface
      WHERE repository_identity = ?
    `).all(repository.repositoryIdentity) as Array<{
      workspace_guid: string; short_id: string; tip: string; category: string; muted_tip: string | null;
    }>;
    const orphans = rows
      .filter((row) => row.muted_tip !== row.tip)
      .filter((row) => {
        const branchRef = `refs/heads/${managedBranch(row.workspace_guid)}`;
        return this.refResolves(repository.primaryCheckoutPath, branchRef)
          || existsSync(managedWorktreePath(repository.primaryCheckoutPath, row.workspace_guid));
      })
      .map((row) => ({
        id: row.short_id,
        workspace_guid: row.workspace_guid,
        branch: managedBranch(row.workspace_guid),
        tip: row.tip,
        category: row.category,
      }));
    return { orphans };
  }

  /**
   * Authorized-consent disposition of previously SURFACED ambiguous orphans
   * (rows `reapAmbiguousOrphans` wrote to `orphan_surface`). Consent is bound
   * to the exact tip surfaced: if the branch (or worktree) has moved since,
   * the resolution is refused — it is never silently reaped or kept sight
   * unseen. A refusal never mutates the surfaced row; only the daemon sweep
   * (`upsertOrphanSurface`) refreshes tip/category, on its own schedule, so a
   * subsequent re-review still sees the state it was surfaced against. Every
   * resolution is processed independently (one bad entry never aborts the
   * batch) and an audit row is written for every outcome.
   */
  resolveOrphan(input: ResolveOrphanInput): ResolveOrphanResult {
    const repository = discoverRepository(input.repositoryPath);
    const protectedSet = new Set((input.protectedPaths ?? []).map((p) => path.resolve(p)));

    const results: OrphanResolutionOutcome[] = [];
    for (const resolution of input.resolutions) {
      const outcome = this.resolveOneOrphan(repository, protectedSet, resolution);
      results.push(outcome);
      this.recordOrphanResolutionAudit(
        repository.repositoryIdentity,
        outcome.guid || null,
        outcome.id || null,
        resolution.action,
        outcome.outcome,
      );
    }
    return { results };
  }

  private findOrphanSurfaceRow(
    repositoryIdentity: string,
    resolution: OrphanResolutionRequest,
  ): { workspace_guid: string; short_id: string; tip: string; category: string } | undefined {
    // Look up by the tip-bound `id` FIRST whenever it is present: the id is
    // bound to the exact surfaced tip, so it must govern even if a (stable)
    // guid is also supplied — otherwise an id+guid resolution would resolve
    // against the guid's CURRENT row and bypass the tip-binding. An id that is
    // present but no longer matches any row (its tip moved) returns undefined
    // here → the caller reports not-surfaced, with no fall-through to the guid.
    if (resolution.id) {
      return this.db.prepare(`
        SELECT workspace_guid, short_id, tip, category FROM orphan_surface
        WHERE repository_identity = ? AND short_id = ?
      `).get(repositoryIdentity, resolution.id) as
        { workspace_guid: string; short_id: string; tip: string; category: string } | undefined;
    }
    if (resolution.guid) {
      return this.db.prepare(`
        SELECT workspace_guid, short_id, tip, category FROM orphan_surface
        WHERE repository_identity = ? AND workspace_guid = ?
      `).get(repositoryIdentity, resolution.guid) as
        { workspace_guid: string; short_id: string; tip: string; category: string } | undefined;
    }
    return undefined;
  }

  private recordOrphanResolutionAudit(
    repositoryIdentity: string,
    workspaceGuid: string | null,
    shortId: string | null,
    action: string,
    outcome: string,
  ): void {
    this.db.prepare(`
      INSERT INTO orphan_resolution_audit (repository_identity, workspace_guid, short_id, action, outcome)
      VALUES (?, ?, ?, ?, ?)
    `).run(repositoryIdentity, workspaceGuid, shortId, action, outcome);
  }

  /** One resolution's disposition. Never throws — every failure mode returns an `error` outcome instead. */
  private resolveOneOrphan(
    repository: RepositoryLocation,
    protectedSet: Set<string>,
    resolution: OrphanResolutionRequest,
  ): OrphanResolutionOutcome {
    let id = resolution.id ?? '';
    let guid = resolution.guid ?? '';
    try {
      // A destructive disposition (reap / merge-then-reap) must be addressed
      // by the tip-bound `id` surfaced for THIS specific tip, never by the
      // guid alone: the guid is stable across tip moves, so accepting it here
      // would silently bypass the tip-binding that makes a stale resolution
      // refuse (`currentTip !== row.tip`, below) rather than discard new work.
      // `keep` carries no destructive effect, so guid-only addressing remains
      // fine for it.
      if (!resolution.id && resolution.guid && (resolution.action === 'reap' || resolution.action === 'merge-then-reap')) {
        return { id, guid, outcome: 'refused-changed' };
      }

      const row = this.findOrphanSurfaceRow(repository.repositoryIdentity, resolution);
      if (!row) return { id, guid, outcome: 'not-surfaced' };
      guid = row.workspace_guid;
      id = row.short_id;

      const name = managedBranch(guid);
      const branchRef = `refs/heads/${name}`;
      const worktreePath = managedWorktreePath(repository.primaryCheckoutPath, guid);
      // Registered: git's worktree administration still references this path
      // (listWorktrees tolerates a gone directory rather than throwing).
      // Present: the directory actually exists on disk. A worktree can be
      // registered but not present when its directory was removed outside
      // git; git then refuses to delete the branch as "used by worktree"
      // until that stale registration is cleared (see `registered` use below).
      const registered = worktreeExists(repository.primaryCheckoutPath, worktreePath);
      const present = registered && existsSync(worktreePath);
      const hasBranch = this.refResolves(repository.primaryCheckoutPath, branchRef);
      const currentTip = hasBranch
        ? runGit(repository.primaryCheckoutPath, ['rev-parse', branchRef]).trim()
        : present ? worktreeHead(worktreePath) : null;

      if (currentTip === null || currentTip !== row.tip) {
        // Never rewrite the surfaced row here: a refusal must leave the row
        // exactly as surfaced. Only the daemon sweep (`upsertOrphanSurface`)
        // refreshes tip/category, on its own schedule.
        return { id, guid, outcome: 'refused-changed' };
      }

      if (this.hasNonCleanedAssignment(repository.repositoryIdentity, guid)
        || (present && protectedSet.has(path.resolve(worktreePath)))) {
        return { id, guid, outcome: 'skipped-live' };
      }

      if (resolution.action === 'keep') {
        this.db.prepare(`
          UPDATE orphan_surface SET muted_tip = ? WHERE repository_identity = ? AND workspace_guid = ?
        `).run(currentTip, repository.repositoryIdentity, guid);
        return { id, guid, outcome: 'kept' };
      }

      if (resolution.action === 'merge-then-reap') {
        if (present && !worktreeIsClean(worktreePath)) {
          // New uncommitted work appeared after consent was surfaced: refuse
          // rather than let the merge silently discard it.
          return { id, guid, outcome: row.category === 'dirty' ? 'refused-dirty' : 'refused-changed' };
        }
        return this.mergeOrphanThenReap(repository, name, branchRef, worktreePath, present, registered, currentTip, resolution, id, guid);
      }

      // action === 'reap'
      let force = false;
      if (present && !worktreeIsClean(worktreePath)) {
        // Force-removing a dirty worktree is safe only when the operator's
        // consent explicitly named the dirty category AND the persisted
        // surface row agrees the orphan is currently dirty. `row.category`
        // alone is not enough: the daemon sweep refreshes it independently
        // of consent, so a resolution surfaced against a stale non-dirty
        // category (or carrying no category at all) must never force a
        // discard just because the row happens to read 'dirty' by the time
        // this resolution runs.
        if (row.category !== 'dirty' || resolution.category !== 'dirty') {
          return { id, guid, outcome: 'refused-changed' };
        }
        force = true;
      }
      let worktreeRemoved = false;
      if (present) {
        removeWorktree(repository.primaryCheckoutPath, worktreePath, { force });
        worktreeRemoved = true;
      } else if (registered) {
        // Targeted equivalent of `git worktree prune` for just this one
        // stale (directory-gone) entry, so the branch delete below is not
        // refused as "used by worktree" — no blanket repository-wide prune.
        removeWorktree(repository.primaryCheckoutPath, worktreePath, { force: true });
        worktreeRemoved = true;
      }
      try {
        if (this.refResolves(repository.primaryCheckoutPath, branchRef)) {
          deleteTemporaryBranch(repository.primaryCheckoutPath, name);
        }
        this.deleteOrphanSurface(repository.repositoryIdentity, guid);
        return { id, guid, outcome: 'reaped' };
      } catch (branchError) {
        return {
          id,
          guid,
          outcome: worktreeRemoved ? 'reaped-worktree-only' : 'error',
          error: branchError instanceof Error ? branchError.message : String(branchError),
        };
      }
    } catch (error) {
      return { id, guid, outcome: 'error', error: error instanceof Error ? error.message : String(error) };
    }
  }

  /**
   * `merge-then-reap`: integrates the orphan branch's committed content into
   * the reaper's target ref, then reaps the orphan — all via Git plumbing
   * (rev-parse / merge-base / merge-tree / commit-tree / update-ref) against
   * commit objects only. The operator's primary checkout is NEVER driven
   * through `git checkout`, `git merge`, or any other working-tree command:
   * when the primary is on a DIFFERENT branch than the target (an operator
   * working on their own feature branch while a reap runs), the target ref
   * is advanced by a bare CAS `update-ref` and the primary's checked-out tree
   * and index are left byte-for-byte untouched. Only when the primary happens
   * to be checked out ON the target ref does `carryForwardFastForward` (a
   * two-tree `read-tree -m -u`) carry the checkout forward, exactly as
   * `continueFrozenFinalization` in integration.ts does for assignments.
   *
   * The target defaults to the repository's CANONICAL default branch
   * (`canonicalDefaultBranchRef`, derived from `refs/remotes/origin/HEAD`,
   * falling back to `refs/heads/main`) — never the primary checkout's live
   * current branch, which an operator may have moved since the orphan was
   * surfaced. `resolution.integrationTarget` overrides this default and lets
   * a caller pin the actual reaper target explicitly.
   */
  private mergeOrphanThenReap(
    repository: RepositoryLocation,
    name: string,
    branchRef: string,
    worktreePath: string,
    present: boolean,
    registered: boolean,
    tip: string,
    resolution: OrphanResolutionRequest,
    id: string,
    guid: string,
  ): OrphanResolutionOutcome {
    const primary = repository.primaryCheckoutPath;
    const target = resolution.integrationTarget
      ? integrationTargetRef(resolution.integrationTarget)
      : canonicalDefaultBranchRef(primary);
    const expected = runGit(primary, ['rev-parse', `${target}^{commit}`]).trim();

    let newCommit: string;
    if (isAncestor(primary, tip, expected)) {
      // Already merged (or a no-op orphan): nothing to advance, go straight to reap.
      newCommit = expected;
    } else if (isAncestor(primary, expected, tip)) {
      newCommit = tip; // fast-forward
    } else {
      if (!gitSupportsMergeTreeWriteTree()) {
        return { id, guid, outcome: 'needs-manual-merge' };
      }
      const mergeTreeArgs = ['merge-tree', '--write-tree', expected, tip];
      const mt = spawnSync('git', ['-C', primary, ...mergeTreeArgs], { encoding: 'utf8', maxBuffer: GIT_MAX_BUFFER });
      if (mt.error) throw mt.error;
      if (mt.status === 1) return { id, guid, outcome: 'conflict' }; // no ref moved
      if (mt.status !== 0) throw gitError(primary, mergeTreeArgs, mt.stderr || '');
      const tree = (mt.stdout || '').split('\n')[0].trim();
      const botEnv = {
        ...process.env,
        GIT_AUTHOR_NAME: 'IronClaude Orphan Reaper',
        GIT_AUTHOR_EMAIL: 'ironclaude-reaper@localhost',
        GIT_COMMITTER_NAME: 'IronClaude Orphan Reaper',
        GIT_COMMITTER_EMAIL: 'ironclaude-reaper@localhost',
      };
      const commitTreeArgs = ['commit-tree', tree, '-p', expected, '-p', tip, '-m', `ironclaude: merge orphan ${name} into ${target}`];
      const ct = spawnSync('git', ['-C', primary, ...commitTreeArgs], { encoding: 'utf8', env: botEnv, maxBuffer: GIT_MAX_BUFFER });
      if (ct.error) throw ct.error;
      if (ct.status !== 0) throw gitError(primary, commitTreeArgs, ct.stderr || '');
      newCommit = (ct.stdout || '').trim();
    }

    if (newCommit !== expected) {
      // Refuse an un-applyable carry-forward BEFORE the CAS so the target
      // never advances on a conflict; self-gates as a no-op when the primary
      // is off the target ref (pure ref-advance case).
      assertNoPrimaryOverlap(primary, target, expected, newCommit);
      const upd = spawnSync('git', ['-C', primary, 'update-ref', target, newCommit, expected], { encoding: 'utf8', maxBuffer: GIT_MAX_BUFFER });
      if (upd.error) throw upd.error;
      // Target ref moved concurrently between the rev-parse snapshot and this
      // CAS (the orphan itself is unchanged); a plain retry recomputes the
      // merge against the advanced target and succeeds.
      if (upd.status !== 0) return { id, guid, outcome: 'target-moved' };
      // Case dispatch: on ref -> carry the checkout forward preserving unrelated
      // operator work; off ref (feature branch / detached) -> pure ref advance,
      // ZERO working-tree commands against the operator's primary checkout.
      if (primaryOnRef(primary, target)) {
        carryForwardFastForward(primary, expected, newCommit);
      }
      verifyPrimaryAfterFastForward(primary, target, expected, newCommit);
    }

    let worktreeRemoved = false;
    if (present) {
      removeWorktree(primary, worktreePath);
      worktreeRemoved = true;
    } else if (registered) {
      // Targeted equivalent of `git worktree prune` for just this one stale
      // (directory-gone) entry, so the branch delete below is not refused as
      // "used by worktree" — mirrors the plain-reap dir-gone path. No blanket
      // repository-wide prune.
      removeWorktree(primary, worktreePath, { force: true });
      worktreeRemoved = true;
    }
    try {
      if (this.refResolves(primary, branchRef)) {
        deleteTemporaryBranch(primary, name);
      }
      this.deleteOrphanSurface(repository.repositoryIdentity, guid);
      return { id, guid, outcome: 'merged-then-reaped' };
    } catch (branchError) {
      return {
        id,
        guid,
        outcome: worktreeRemoved ? 'reaped-worktree-only' : 'error',
        error: branchError instanceof Error ? branchError.message : String(branchError),
      };
    }
  }

  /**
   * Determines why a preserved (non-ancestor) orphan's content has not
   * reached `target`: already merged under a new SHA (squash), already merged
   * to origin's copy of the target branch but not yet fast-forwarded locally,
   * or genuinely unmerged anywhere. `contentMergedInto` is read-only and
   * fail-safe (never over-claims merged on an error); the origin check is
   * skipped when the origin-tracking ref does not resolve, or when origin is
   * already at or behind `target` (merged-on-origin is then impossible, so
   * running the expensive double-scan would only ever confirm
   * genuinely-unmerged).
   */
  private classifyPreservedOrphan(
    repository: RepositoryLocation,
    tip: string,
    target: string,
    scratchDir: string,
  ): { category: PreservedOrphan['category']; evidence: string } {
    if (contentMergedInto(repository.primaryCheckoutPath, tip, target, scratchDir)) {
      return { category: 'squash-merged', evidence: `content already reached ${target} (squash-merge detected)` };
    }
    const originRef = target.startsWith('refs/heads/')
      ? `refs/remotes/origin/${target.slice('refs/heads/'.length)}`
      : null;
    if (originRef
      && this.refResolves(repository.primaryCheckoutPath, originRef)
      && !isAncestor(repository.primaryCheckoutPath, originRef, target)
      && contentMergedInto(repository.primaryCheckoutPath, tip, originRef, scratchDir)) {
      return { category: 'merged-on-origin', evidence: `content already reached ${originRef}` };
    }
    return { category: 'genuinely-unmerged', evidence: `not an ancestor of ${target}` };
  }

  /** Upserts the orphan's surface row and builds the reported PreservedOrphan, including `muted`. */
  private buildPreservedOrphan(
    repository: RepositoryLocation,
    guid: string,
    branch: string,
    tip: string,
    category: PreservedOrphan['category'],
    evidence: string,
    worktreePresent: boolean,
  ): PreservedOrphan {
    const id = createHash('sha256').update(`${guid}\0${tip}`).digest('hex').slice(0, 8);
    const row = this.upsertOrphanSurface(repository.repositoryIdentity, guid, id, tip, category);
    return { id, guid, branch, category, tip, worktreePresent, evidence, muted: row.muted_tip === tip };
  }

  /**
   * Upserts the durable orphan_surface row for one (repository, guid), keyed
   * on its primary key so a repeated sweep refreshes tip/category in place
   * rather than duplicating rows. `muted_tip` is never written here — only an
   * explicit operator mute action sets it — so it survives the upsert
   * untouched and is returned for the caller to compare against the current tip.
   */
  private upsertOrphanSurface(
    repositoryIdentity: string,
    guid: string,
    shortId: string,
    tip: string,
    category: string,
  ): { muted_tip: string | null } {
    this.db.prepare(`
      INSERT INTO orphan_surface (repository_identity, workspace_guid, short_id, tip, category)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(repository_identity, workspace_guid) DO UPDATE SET
        tip = excluded.tip,
        category = excluded.category,
        short_id = excluded.short_id
    `).run(repositoryIdentity, guid, shortId, tip, category);
    return this.db.prepare(
      'SELECT muted_tip FROM orphan_surface WHERE repository_identity = ? AND workspace_guid = ?',
    ).get(repositoryIdentity, guid) as { muted_tip: string | null };
  }

  /** Drops the surface row for a guid that is no longer a preserved orphan (reaped). */
  private deleteOrphanSurface(repositoryIdentity: string, guid: string): void {
    this.db.prepare(
      'DELETE FROM orphan_surface WHERE repository_identity = ? AND workspace_guid = ?',
    ).run(repositoryIdentity, guid);
  }

  private hasNonCleanedAssignment(repositoryIdentity: string, workspaceGuid: string): boolean {
    return this.db.prepare(`
      SELECT 1 FROM assignments
      WHERE repository_identity = ? AND workspace_guid = ? AND lifecycle_status <> 'cleaned'
    `).get(repositoryIdentity, workspaceGuid) !== undefined;
  }

  /**
   * Current explicit shared-resource entries configured for the repository,
   * wrapped in an object. The return MUST be an object (not a bare array): the
   * Commander's WorkspaceClient._decode rejects any non-object JSON response, so a
   * bare array would make the orchestrator list tool error on every real call.
   */
  listSharedResources(input: RepositoryRequest): ListSharedResourcesResult {
    const repository = discoverRepository(input.repositoryPath);
    return { entries: readSharedResourceConfig(repository.repositoryIdentity) };
  }

  /**
   * Add explicit shared-resource entries for a repository and relink the newly
   * added ones into every currently-live MANAGED worktree, so a running worker
   * gets the data without a respawn. Only live managed worktrees (rows in the
   * assignments table, non-terminal, still on disk) are relinked — operator-created
   * or orphaned worktrees are never touched. `relinked` reports the entries actually
   * planted per worktree (a source-absent entry is written to config but not linked).
   */
  configureSharedResources(input: ConfigureSharedResourcesInput): ConfigureSharedResourcesResult {
    const repository = discoverRepository(input.repositoryPath);
    const written = addSharedResourceEntries(
      repository.repositoryIdentity,
      input.entries,
      repository.primaryCheckoutPath,
      input.allowSecretEntries ?? false,
    );
    const relinked: Record<string, string[]> = {};
    // Relink every safe in-config entry from this request (added ∪ skipped), not
    // just newly-added ones: an entry configured while its source was absent is
    // written to config but never linked, and the Brain's recovery re-issues it
    // (so it arrives as `skipped`, already present). linkSharedResources is
    // idempotent (pathPresent skip), so already-linked entries are no-ops.
    const toRelink = [...written.added, ...written.skipped];
    if (toRelink.length > 0) {
      const liveManaged = this.db.prepare(`
        SELECT worktree_path FROM assignments
        WHERE repository_identity = ?
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
      `).all(repository.repositoryIdentity) as { worktree_path: string }[];
      for (const { worktree_path } of liveManaged) {
        if (!existsSync(worktree_path)) continue;
        const planted = linkSharedResources(
          repository.primaryCheckoutPath,
          worktree_path,
          repository.repositoryIdentity,
          toRelink,
        );
        if (planted.length > 0) relinked[worktree_path] = planted;
      }
    }
    return { ...written, relinked };
  }
}
