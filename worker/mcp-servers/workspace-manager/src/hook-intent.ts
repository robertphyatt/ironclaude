#!/usr/bin/env node

import type Database from 'better-sqlite3';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { initDb } from './db.js';
import { issueDirectGitHumanIntent, type DirectGitOperation } from './git-authority.js';
import { discoverRepository } from './git.js';
import type { Assignment, HumanIntentOperation } from './types.js';
import { WorkspaceService } from './workspace-service.js';

type Args = Record<string, unknown>;

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

export function issueHumanIntentFromHook(db: Database.Database, args: Args): unknown {
  if (requiredString(args, 'hook_event_name') !== 'UserPromptSubmit'
    || requiredString(args, 'invocation_source') !== 'human') {
    throw new Error('Human intent issuance requires the trusted UserPromptSubmit hook');
  }
  const operation = requiredString(args, 'operation') as HumanIntentOperation;
  const humanChannel = requiredString(args, 'human_channel');
  if (humanChannel !== 'claude-user-prompt' && humanChannel !== 'codex-user-prompt') {
    throw new Error('human_channel is not a trusted provider prompt channel');
  }
  const repositoryPath = requiredString(args, 'repository_path');
  const ownerSessionId = requiredString(args, 'owner_session_id');
  const repository = discoverRepository(repositoryPath);
  if (operation === 'close-out') {
    // Close-out is lifecycle-TOLERANT: it must enter on active, ready-for-integration
    // (incl. a detached mid-rebase), and integrated rows (the persistent Case A state).
    // Scoped to close-out only — the other verbs keep the active-only query below.
    const closeable = db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND owner_session_id = ?
        AND lifecycle_status IN ('active', 'ready_for_integration', 'integrated')
      ORDER BY created_at ASC
    `).all(repository.repositoryIdentity, ownerSessionId) as Assignment[];
    if (closeable.length !== 1) {
      throw new Error('Human intent issuance requires exactly one closeable assignment for provider root and repository');
    }
    const closeableAssignment = closeable[0];
    const requestedCloseGuid = optionalString(args, 'workspace_guid');
    if (requestedCloseGuid !== undefined && requestedCloseGuid !== closeableAssignment.workspace_guid) {
      throw new Error('Human intent workspace binding does not match closeable assignment');
    }
    return issueDirectGitHumanIntent(db, {
      repositoryPath,
      workspaceGuid: closeableAssignment.workspace_guid,
      providerRootSessionId: ownerSessionId,
      humanChannel,
      operation: 'close-out',
    });
  }
  const assignments = db.prepare(`
    SELECT * FROM assignments
    WHERE repository_identity = ? AND owner_session_id = ?
      AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
    ORDER BY created_at ASC
  `).all(repository.repositoryIdentity, ownerSessionId) as Assignment[];
  const requestedGuid = optionalString(args, 'workspace_guid');
  if (assignments.length === 0) {
    if (requestedGuid === undefined && (operation === 'commit' || operation === 'push' || operation === 'commit-and-push')) {
      return issueDirectGitHumanIntent(db, {
        repositoryPath,
        providerRootSessionId: ownerSessionId,
        humanChannel,
        operation: operation as DirectGitOperation,
      });
    }
    throw new Error('Human intent issuance requires exactly one active assignment for provider root and repository');
  }
  if (assignments.length !== 1) {
    throw new Error('Human intent issuance requires exactly one active assignment for provider root and repository');
  }
  const assignment = assignments[0];
  if (requestedGuid !== undefined && requestedGuid !== assignment.workspace_guid) {
    throw new Error('Human intent workspace binding does not match active assignment');
  }
  if (operation === 'commit' || operation === 'commit-and-push' || operation === 'push' || operation === 'reconcile' || operation === 'confirm-resolution') {
    return issueDirectGitHumanIntent(db, {
      repositoryPath,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: ownerSessionId,
      humanChannel,
      operation: operation as DirectGitOperation,
    });
  }
  if (operation === 'use-primary-checkout' || operation === 'return-to-managed-worktree') {
    return new WorkspaceService(db).issueCheckoutHumanIntent({
      repositoryPath,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId,
      humanChannel,
      operation,
    });
  }
  throw new Error('Human intent operation is not allowed');
}

export function runHookIntent(argv: string[] = process.argv.slice(2), db: Database.Database = initDb()): unknown {
  if (argv.length !== 1) throw new Error('Hook intent helper expects exactly one JSON payload');
  let args: unknown;
  try {
    args = JSON.parse(argv[0]);
  } catch {
    throw new Error('Hook intent payload must be valid JSON');
  }
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    throw new Error('Hook intent payload must be a JSON object');
  }
  return issueHumanIntentFromHook(db, args as Args);
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    process.stdout.write(`${JSON.stringify(runHookIntent())}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
