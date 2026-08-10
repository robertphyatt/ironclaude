#!/usr/bin/env node

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import type Database from 'better-sqlite3';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { initDb } from './db.js';
import { verifyDirectGitAuthority, type DirectGitOperation } from './git-authority.js';
import { discoverRepository } from './git.js';
import { finalizeDirectAuthority, reconcileFinalization } from './integration.js';
import { parseIronClaudeClient, resolveSessionIdentity } from './session-identity.js';
import type { Assignment, SessionIdentity } from './types.js';
import { WorkspaceService } from './workspace-service.js';

type Args = Record<string, unknown>;
type PublicSingleArgumentDependency = (args: Args) => unknown;

export interface PublicToolDependencies {
  getWorkspaceStatus: PublicSingleArgumentDependency;
  activateSessionWorkspace: PublicSingleArgumentDependency;
  usePrimaryCheckout: PublicSingleArgumentDependency;
  returnToManagedWorktree: PublicSingleArgumentDependency;
  listActiveAssignments: PublicSingleArgumentDependency;
  finalizeDirect: (operation: DirectGitOperation, args: Args) => unknown;
  reconcileFinalization: PublicSingleArgumentDependency;
}

export const PUBLIC_TOOL_NAMES = [
  'get_workspace_status',
  'activate_session_workspace',
  'use_primary_checkout',
  'return_to_managed_worktree',
  'list_active_assignments',
  'commit',
  'commit_and_push',
  'push',
  'reconcile_finalization',
] as const;

const repositoryProperty = { type: 'string' as const, description: 'Path within the target Git repository.' };
const workspaceProperty = { type: 'string' as const, description: 'Durable IronClaude workspace GUID.' };
const providerRootProperty = { type: 'string' as const, description: 'Provider-native root session identity.' };

export const publicToolDefinitions = [
  {
    name: 'get_workspace_status',
    description: 'Read the exact managed-worktree assignment bound to this provider-root session.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
        provider_root: providerRootProperty,
      },
      required: ['repository_path'],
      additionalProperties: false,
    },
  },
  {
    name: 'activate_session_workspace',
    description: 'Create or resume the managed worktree for this provider-root session.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
        integration_target: { type: 'string' as const },
      },
      required: ['repository_path', 'integration_target'],
      additionalProperties: false,
    },
  },
  {
    name: 'use_primary_checkout',
    description: 'Consume exact human intent and give this session exclusive primary-checkout ownership.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
      },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
  {
    name: 'return_to_managed_worktree',
    description: 'Consume exact human intent, restore the managed assignment, and release primary ownership.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
      },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
  {
    name: 'list_active_assignments',
    description: 'List nonterminal assignments owned by this provider-root session.',
    inputSchema: {
      type: 'object' as const,
      properties: { repository_path: repositoryProperty },
      required: ['repository_path'],
      additionalProperties: false,
    },
  },
  ...(['commit', 'commit_and_push', 'push'] as const).map((name) => ({
    name,
    description: `Consume exact human intent and perform direct ${name.replaceAll('_', '-')} authority.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
        message: { type: 'string' as const },
      },
      required: name === 'push'
        ? ['repository_path', 'workspace_guid']
        : ['repository_path', 'workspace_guid', 'message'],
      additionalProperties: false,
    },
  })),
  {
    name: 'reconcile_finalization',
    description: 'Reconcile finalization state for this workspace, gated to the provider-root session; auto-completes when proof already holds.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
        mode: {
          type: 'string' as const,
          enum: ['status', 'continue', 'abort', 'rerebase', 'restore_frozen'],
          description: 'Optional explicit rebase-recovery mode; omitted defaults to auto-complete-when-proven.',
        },
      },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
] as const;

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

function optionalMode(args: Args): 'status' | 'continue' | 'abort' | 'rerebase' | 'restore_frozen' | undefined {
  const value = args.mode;
  if (value === undefined) return undefined;
  if (value !== 'status' && value !== 'continue' && value !== 'abort'
    && value !== 'rerebase' && value !== 'restore_frozen') {
    throw new Error("mode must be 'status', 'continue', 'abort', 'rerebase', or 'restore_frozen'");
  }
  return value;
}

export function dispatchPublicTool(name: string, args: Args, dependencies: PublicToolDependencies): unknown {
  switch (name) {
    case 'get_workspace_status': return dependencies.getWorkspaceStatus(args);
    case 'activate_session_workspace': return dependencies.activateSessionWorkspace(args);
    case 'use_primary_checkout': return dependencies.usePrimaryCheckout(args);
    case 'return_to_managed_worktree': return dependencies.returnToManagedWorktree(args);
    case 'list_active_assignments': return dependencies.listActiveAssignments(args);
    case 'commit': return dependencies.finalizeDirect('commit', args);
    case 'commit_and_push': return dependencies.finalizeDirect('commit-and-push', args);
    case 'push': return dependencies.finalizeDirect('push', args);
    case 'reconcile_finalization': return dependencies.reconcileFinalization(args);
    default: throw new Error(`Unknown public workspace tool: ${name}`);
  }
}

export function createPublicToolDependencies(
  db: Database.Database,
  identity: SessionIdentity,
): PublicToolDependencies {
  const service = new WorkspaceService(db);
  const humanChannel = identity.client === 'claude' ? 'claude-user-prompt' : 'codex-user-prompt';
  // Fires for BOTH clients. Codex supplies thread_source directly; Claude's
  // invocationThreadId is populated by claudeSubagentMarker() when the request
  // carries a subagent marker, so a subagent is refused on either transport.
  // See session-identity.ts for what the Claude marker can and cannot prove.
  const requireProviderRoot = () => {
    if (identity.invocationThreadId !== null && identity.invocationThreadId !== identity.sessionId) {
      throw new Error('Direct human authority can be consumed only by the provider-root session');
    }
  };
  const assignmentRequest = (args: Args) => ({
    repositoryPath: requiredString(args, 'repository_path'),
    workspaceGuid: requiredString(args, 'workspace_guid'),
    ownerSessionId: identity.sessionId,
  });

  return {
    getWorkspaceStatus: (args) => {
      const workspaceGuid = optionalString(args, 'workspace_guid');
      const providerRoot = optionalString(args, 'provider_root');
      if ((workspaceGuid === undefined) === (providerRoot === undefined)) {
        throw new Error('get_workspace_status requires exactly one of workspace_guid or provider_root');
      }
      if (workspaceGuid !== undefined) {
        return service.getWorkspaceAssignment({
          repositoryPath: requiredString(args, 'repository_path'),
          workspaceGuid,
          ownerSessionId: identity.sessionId,
        });
      }
      if (providerRoot !== identity.sessionId) {
        throw new Error('provider_root does not match the authenticated provider-root session');
      }
      return service.getWorkspaceStatusForRoot({
        repositoryPath: requiredString(args, 'repository_path'),
        ownerSessionId: identity.sessionId,
      });
    },
    activateSessionWorkspace: (args) => service.ensureSessionWorktree({
      repositoryPath: requiredString(args, 'repository_path'),
      workspaceGuid: optionalString(args, 'workspace_guid'),
      ownerSessionId: identity.sessionId,
      integrationTarget: requiredString(args, 'integration_target'),
    }),
    usePrimaryCheckout: (args) => {
      requireProviderRoot();
      return service.usePrimaryCheckout({ ...assignmentRequest(args), humanChannel });
    },
    returnToManagedWorktree: (args) => {
      requireProviderRoot();
      return service.returnToManagedWorktree({ ...assignmentRequest(args), humanChannel });
    },
    listActiveAssignments: (args) => {
      const repository = discoverRepository(requiredString(args, 'repository_path'));
      return db.prepare(`
        SELECT * FROM assignments
        WHERE repository_identity = ? AND owner_session_id = ?
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
        ORDER BY created_at ASC
      `).all(repository.repositoryIdentity, identity.sessionId) as Assignment[];
    },
    finalizeDirect: (operation, args) => {
      requireProviderRoot();
      const authority = verifyDirectGitAuthority(db, {
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: requiredString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
        humanChannel,
        operation,
      });
      const message = operation === 'push' ? '' : requiredString(args, 'message');
      return finalizeDirectAuthority(db, authority, message);
    },
    reconcileFinalization: (args) => {
      requireProviderRoot();
      return reconcileFinalization(db, {
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: requiredString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
        rebaseRecovery: optionalMode(args),
      });
    },
  };
}

function result(value: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(value) }] };
}

async function readClaudeSessionId(): Promise<string | null> {
  const ppid = process.env.CLAUDE_PPID;
  if (!ppid) return null;
  const sessionFile = path.join(os.homedir(), '.claude', `ironclaude-session-${ppid}.id`);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const value = fs.readFileSync(sessionFile, 'utf8').trim();
      if (value && !value.startsWith('${')) return value;
    } catch {
      // Provider startup can race PPID binding; retry without changing identity source.
    }
    if (attempt < 4) await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return null;
}

export async function startWorkspaceManagerServer(): Promise<void> {
  const client = parseIronClaudeClient(process.env.IRONCLAUDE_CLIENT);
  const server = new Server(
    { name: 'workspace-manager', version: '1.1.4' },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: [...publicToolDefinitions] }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    try {
      const claudeSessionId = client === 'claude' ? await readClaudeSessionId() : null;
      const identity = resolveSessionIdentity(client, request.params._meta, claudeSessionId);
      const db = initDb();
      return result(dispatchPublicTool(
        request.params.name,
        (request.params.arguments ?? {}) as Args,
        createPublicToolDependencies(db, identity),
      ));
    } catch (error) {
      return {
        ...result({ error: error instanceof Error ? error.message : String(error) }),
        isError: true,
      };
    }
  });

  await server.connect(new StdioServerTransport());
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  startWorkspaceManagerServer().catch((error) => {
    console.error('Workspace-manager server error:', error);
    process.exit(1);
  });
}
