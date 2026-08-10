#!/usr/bin/env node

import type Database from 'better-sqlite3';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { initDb } from './db.js';
import {
  finalizeCommanderLocalCommit,
  reconcileFinalization,
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
}

export const INTERNAL_COMMAND_NAMES = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile'] as const;

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
): 'continue' | 'abort' | 'rerebase' | 'restore_frozen' | 'status' | undefined {
  const value = args.rebase_recovery;
  if (value === undefined) return undefined;
  if (value !== 'continue' && value !== 'abort' && value !== 'rerebase'
    && value !== 'restore_frozen' && value !== 'status') {
    throw new Error("rebase_recovery must be 'continue', 'abort', 'rerebase', 'restore_frozen', or 'status'");
  }
  return value;
}

function requiredRecord(args: Args, key: string): Record<string, unknown> {
  const value = args[key];
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${key} must be an object`);
  return value as Record<string, unknown>;
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
    }),
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
