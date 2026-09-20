#!/usr/bin/env node

import type Database from 'better-sqlite3';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { initDb } from './db.js';
import {
  finalizeCommanderLocalCommit,
  reconcileFinalization,
  syncWorktreeToTarget,
  type CommanderLocalCommitInput,
} from './integration.js';
import { WorkspaceService } from './workspace-service.js';

type Args = Record<string, unknown>;
type InternalDependency = (args: Args) => unknown;

export interface InternalCommandDependencies {
  allocate: InternalDependency;
  bind: InternalDependency;
  finalize: InternalDependency;
  abandon: InternalDependency;
  reconcile: InternalDependency;
  cleanup: InternalDependency;
  sync: InternalDependency;
  reap: InternalDependency;
  configureSharedResources: InternalDependency;
  listSharedResources: InternalDependency;
  'reap-orphans': InternalDependency;
  'resolve-orphan': InternalDependency;
}

export const INTERNAL_COMMAND_NAMES = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile', 'cleanup', 'sync', 'reap', 'configure-shared-resources', 'list-shared-resources', 'reap-orphans', 'resolve-orphan'] as const;

function waitForCliDatabase(milliseconds: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

export function initCliDb(): Database.Database {
  let lastBusyError: unknown;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return initDb();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!/database is locked|SQLITE_BUSY/i.test(message)) throw error;
      lastBusyError = error;
      waitForCliDatabase(25);
    }
  }
  throw lastBusyError instanceof Error
    ? lastBusyError
    : new Error('workspace-manager database remained locked during initialization');
}

function requiredString(args: Args, key: string): string {
  const value = args[key];
  if (typeof value !== 'string' || value.length === 0) throw new Error(`${key} must be a non-empty string`);
  return value;
}

function optionalString(args: Args, key: string): string | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== 'string' || value.length === 0) throw new Error(`${key} must be a non-empty string`);
  return value;
}

function optionalRebaseRecovery(
  args: Args,
): 'continue' | 'abort' | 'rerebase' | 'restore_frozen' | 'status' | 'reopen_for_edit' | undefined {
  const value = args.rebase_recovery;
  if (value === undefined) return undefined;
  if (value !== 'continue' && value !== 'abort' && value !== 'rerebase'
    && value !== 'restore_frozen' && value !== 'status' && value !== 'reopen_for_edit') {
    throw new Error("rebase_recovery must be 'continue', 'abort', 'rerebase', 'restore_frozen', 'status', or 'reopen_for_edit'");
  }
  return value;
}

function optionalAbandonMode(args: Args): 'rescue' | undefined {
  const value = args.mode;
  if (value === undefined) return undefined;
  if (value !== 'rescue') throw new Error("mode must be 'rescue'");
  return value;
}

function requiredRecord(args: Args, key: string): Record<string, unknown> {
  const value = args[key];
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${key} must be an object`);
  return value as Record<string, unknown>;
}

function requiredStringArray(args: Args, key: string): string[] {
  const value = args[key];
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error(`${key} must be an array of strings`);
  }
  return value as string[];
}

function optionalStringArray(args: Args, key: string): string[] | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error(`${key} must be an array of strings`);
  }
  return value as string[];
}

function optionalNumber(args: Args, key: string): number | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== 'number' || Number.isNaN(value)) throw new Error(`${key} must be a number`);
  return value;
}

type OrphanResolutionAction = 'reap' | 'keep' | 'merge-then-reap';
const ORPHAN_RESOLUTION_ACTIONS: readonly OrphanResolutionAction[] = ['reap', 'keep', 'merge-then-reap'];

type OrphanCategory = 'squash-merged' | 'merged-on-origin' | 'genuinely-unmerged' | 'dirty';
const ORPHAN_CATEGORIES: readonly OrphanCategory[] = ['squash-merged', 'merged-on-origin', 'genuinely-unmerged', 'dirty'];

function requiredResolutions(
  args: Args,
  key: string,
): { id?: string; guid?: string; action: OrphanResolutionAction; integrationTarget?: string; category?: OrphanCategory }[] {
  const value = args[key];
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`${key} must be a non-empty array of resolution objects`);
  }
  return value.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error(`${key} entries must be objects`);
    }
    const record = item as Record<string, unknown>;
    const action = record.action;
    if (typeof action !== 'string' || !ORPHAN_RESOLUTION_ACTIONS.includes(action as OrphanResolutionAction)) {
      throw new Error(`${key} entries must have action 'reap', 'keep', or 'merge-then-reap'`);
    }
    const id = record.id;
    const guid = record.guid;
    if (id !== undefined && (typeof id !== 'string' || id.length === 0)) {
      throw new Error(`${key} entries id must be a non-empty string`);
    }
    if (guid !== undefined && (typeof guid !== 'string' || guid.length === 0)) {
      throw new Error(`${key} entries guid must be a non-empty string`);
    }
    if (id === undefined && guid === undefined) {
      throw new Error(`${key} entries must have an id or a guid`);
    }
    const integrationTargetValue = record.integration_target;
    let integrationTarget: string | undefined;
    if (integrationTargetValue !== undefined) {
      if (typeof integrationTargetValue !== 'string' || integrationTargetValue.length === 0) {
        throw new Error(`${key} entries integration_target must be a non-empty string`);
      }
      integrationTarget = integrationTargetValue;
    }
    const categoryValue = record.category;
    let category: OrphanCategory | undefined;
    if (categoryValue !== undefined) {
      if (typeof categoryValue !== 'string' || !ORPHAN_CATEGORIES.includes(categoryValue as OrphanCategory)) {
        throw new Error(
          `${key} entries category must be 'squash-merged', 'merged-on-origin', 'genuinely-unmerged', or 'dirty'`,
        );
      }
      category = categoryValue as OrphanCategory;
    }
    return { id, guid, action: action as OrphanResolutionAction, integrationTarget, category };
  });
}

export function dispatchInternalCommand(
  name: string,
  args: Args,
  dependencies: InternalCommandDependencies,
): unknown {
  switch (name) {
    case 'allocate': return dependencies.allocate(args);
    case 'bind': return dependencies.bind(args);
    case 'finalize': return dependencies.finalize(args);
    case 'abandon': return dependencies.abandon(args);
    case 'reconcile': return dependencies.reconcile(args);
    case 'cleanup': return dependencies.cleanup(args);
    case 'sync': return dependencies.sync(args);
    case 'reap': return dependencies.reap(args);
    case 'configure-shared-resources': return dependencies.configureSharedResources(args);
    case 'list-shared-resources': return dependencies.listSharedResources(args);
    case 'reap-orphans': return dependencies['reap-orphans'](args);
    case 'resolve-orphan': return dependencies['resolve-orphan'](args);
    default: throw new Error(`Unknown internal workspace command: ${name}`);
  }
}

export function createInternalCommandDependencies(
  db: Database.Database,
): InternalCommandDependencies {
  const service = new WorkspaceService(db);
  return {
    allocate: (args) => {
      const ownerSessionId = optionalString(args, 'owner_session_id');
      if (ownerSessionId !== undefined) {
        return service.ensureSessionWorktree({
          repositoryPath: requiredString(args, 'repository_path'),
          workspaceGuid: optionalString(args, 'workspace_guid'),
          ownerSessionId,
          workerId: optionalString(args, 'worker_id'),
          integrationTarget: optionalString(args, 'integration_target'),
        });
      }
      return service.reserveWorkerWorktree({
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: requiredString(args, 'workspace_guid'),
        workerId: requiredString(args, 'worker_id'),
        // Omitted means "the primary checkout's current branch", resolved on the
        // host that owns the repository — which may not be this machine.
        integrationTarget: optionalString(args, 'integration_target'),
      });
    },
    bind: (args) => {
      const expectedLifecycle = requiredString(args, 'expected_lifecycle');
      if (expectedLifecycle !== 'active') throw new Error('expected_lifecycle must be active');
      return service.bindWorkerWorktree({
        repositoryPath: requiredString(args, 'repository_path'),
        repositoryIdentity: requiredString(args, 'repository_identity'),
        workspaceGuid: requiredString(args, 'workspace_guid'),
        workerId: requiredString(args, 'worker_id'),
        ownerSessionId: requiredString(args, 'owner_session_id'),
        expectedLifecycle,
        expectedWorktreePath: requiredString(args, 'expected_worktree_path'),
        expectedBranch: requiredString(args, 'expected_branch'),
        expectedBaseCommit: requiredString(args, 'expected_base_commit'),
        expectedCurrentHead: requiredString(args, 'expected_current_head'),
      });
    },
    finalize: (args) => {
      const command = requiredRecord(args, 'command') as unknown as CommanderLocalCommitInput;
      return finalizeCommanderLocalCommit(db, command);
    },
    abandon: (args) => service.abandonWorkspace({
      repositoryPath: requiredString(args, 'repository_path'),
      workspaceGuid: requiredString(args, 'workspace_guid'),
      ownerSessionId: requiredString(args, 'owner_session_id'),
      mode: optionalAbandonMode(args),
    }),
    cleanup: (args) => {
      const repositoryPath = requiredString(args, 'repository_path');
      const workspaceGuid = requiredString(args, 'workspace_guid');
      const ownerSessionId = requiredString(args, 'owner_session_id');
      const assignment = service.getWorkspaceAssignment({ repositoryPath, workspaceGuid, ownerSessionId });
      if (assignment.lifecycle_status === 'reserved') {
        return service.cleanupReservedAssignment({ repositoryPath, workspaceGuid, ownerSessionId });
      }
      return service.cleanupWorkspace({ repositoryPath, workspaceGuid, ownerSessionId });
    },
    reconcile: (args) => {
      const repositoryPath = requiredString(args, 'repository_path');
      const workspaceGuid = optionalString(args, 'workspace_guid');
      const ownerSessionId = optionalString(args, 'owner_session_id');
      const rebaseRecovery = optionalRebaseRecovery(args);
      if (workspaceGuid || ownerSessionId) {
        if (!workspaceGuid || !ownerSessionId) {
          throw new Error('workspace_guid and owner_session_id must be provided together for finalization reconciliation');
        }
        return reconcileFinalization(db, {
          repositoryPath,
          workspaceGuid,
          providerRootSessionId: ownerSessionId,
          rebaseRecovery,
        });
      }
      if (rebaseRecovery) {
        throw new Error('rebase_recovery requires workspace_guid and owner_session_id for finalization reconciliation');
      }
      return service.reconcileRepository(repositoryPath);
    },
    sync: (args) => syncWorktreeToTarget(db, {
      repositoryPath: requiredString(args, 'repository_path'),
      workspaceGuid: requiredString(args, 'workspace_guid'),
      providerRootSessionId: requiredString(args, 'owner_session_id'),
    }),
    reap: (args) => service.reapLeakedAssignment({
      repositoryPath: requiredString(args, 'repository_path'),
      workspaceGuid: requiredString(args, 'workspace_guid'),
    }),
    configureSharedResources: (args) => service.configureSharedResources({
      repositoryPath: requiredString(args, 'repository_path'),
      entries: requiredStringArray(args, 'entries'),
      allowSecretEntries: args.allow_secret_entries === true,
    }),
    listSharedResources: (args) => service.listSharedResources({
      repositoryPath: requiredString(args, 'repository_path'),
    }),
    'reap-orphans': (args) => service.reapAmbiguousOrphans({
      repositoryPath: requiredString(args, 'repository_path'),
      protectedPaths: optionalStringArray(args, 'protected_paths'),
      ttlHours: optionalNumber(args, 'ttl_hours'),
    }),
    'resolve-orphan': (args) => service.resolveOrphan({
      repositoryPath: requiredString(args, 'repository_path'),
      protectedPaths: optionalStringArray(args, 'protected_paths'),
      resolutions: requiredResolutions(args, 'resolutions'),
    }),
  };
}

export function runCli(
  argv: string[] = process.argv.slice(2),
  db: Database.Database = initDb(),
): unknown {
  const command = argv[0];
  if (!command) throw new Error(`Expected one internal command: ${INTERNAL_COMMAND_NAMES.join(', ')}`);
  const raw = argv[1] ?? '{}';
  let args: unknown;
  try {
    args = JSON.parse(raw);
  } catch {
    throw new Error('Internal command payload must be valid JSON');
  }
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    throw new Error('Internal command payload must be a JSON object');
  }
  return dispatchInternalCommand(command, args as Args, createInternalCommandDependencies(db));
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    process.stdout.write(`${JSON.stringify(runCli(process.argv.slice(2), initCliDb()))}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
