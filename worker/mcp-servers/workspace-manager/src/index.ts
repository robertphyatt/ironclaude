#!/usr/bin/env node

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import type Database from 'better-sqlite3';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { getAssignment, initDb, listUnresolvedPreservedWork } from './db.js';
import { verifyDirectGitAuthority, type DirectGitOperation } from './git-authority.js';
import { discoverRepository, worktreeExists } from './git.js';
import { finalizeCloseOut, finalizeConfirmResolution, finalizeDirectAuthority, finalizePrimaryUnassignedCommit, finalizePrimaryUnassignedPush, finalizePrimaryUnassignedCommitAndPush, finalizeReconcile, pushPendingSummary, reconcileFinalization, resolveConflictHunk, syncWorktreeToTarget } from './integration.js';
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
  syncWorktreeToTarget: PublicSingleArgumentDependency;
  reconcileWorktree: PublicSingleArgumentDependency;
  landResolvedConflict: PublicSingleArgumentDependency;
  resolveConflictHunk: PublicSingleArgumentDependency;
  closeOutWorktree: PublicSingleArgumentDependency;
  listPreservedWork: PublicSingleArgumentDependency;
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
  'sync_worktree_to_target',
  'reconcile_worktree',
  'land_resolved_conflict',
  'resolve_conflict_hunk',
  'close_out_worktree',
  'list_preserved_work',
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
    description: `Consume exact human intent and perform direct ${name.replaceAll('_', '-')} authority. Intent exists ONLY when the operator typed /${name.replaceAll('_', '-')} as their literal prompt this turn; free-text prose does not carry it. On a prose request, reply with the /${name.replaceAll('_', '-')} form for the operator to type — do NOT call this tool (it refuses without intent). After the verb completes, carry forward any remaining instruction from the prose.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
        message: { type: 'string' as const },
      },
      required: name === 'push'
        ? ['repository_path']
        : ['repository_path', 'message'],
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
          enum: ['status', 'continue', 'abort', 'rerebase', 'restore_frozen', 'reopen_for_edit'],
          description: 'Optional explicit rebase-recovery mode; omitted defaults to auto-complete-when-proven.',
        },
      },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
  {
    name: 'sync_worktree_to_target',
    description: 'Advance this managed worktree branch onto the current integration target in-session, gated to the provider-root session; refuses on a non-active lifecycle, a paused rebase, or a held integration lock.',
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
    name: 'reconcile_worktree',
    description: 'Integrate this managed worktree HEAD into local main and keep the worktree alive, gated to the provider-root session; never pushes.',
    inputSchema: {
      type: 'object' as const,
      properties: { repository_path: repositoryProperty, workspace_guid: workspaceProperty },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
  {
    name: 'land_resolved_conflict',
    description: 'Land an operator-confirmed conflict resolution into local main via the isRepair channel; consumes /confirm-resolution intent, requires the registered candidate to equal the authorized HEAD; keeps the worktree; never pushes.',
    inputSchema: {
      type: 'object' as const,
      properties: { repository_path: repositoryProperty, workspace_guid: workspaceProperty },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
  {
    name: 'resolve_conflict_hunk',
    description: 'Turn one per-hunk operator choice into staged resolved bytes on a paused integration rebase, gated to the provider-root session; drives rebase --continue when the hunk was the last unresolved path. NEVER lands and NEVER pushes — landing a completed rebase is a separate tool (land_resolved_conflict).',
    inputSchema: {
      type: 'object' as const,
      properties: {
        repository_path: repositoryProperty,
        workspace_guid: workspaceProperty,
        path: { type: 'string' as const, description: 'The single unmerged path this call resolves.' },
        choice: {
          type: 'string' as const,
          enum: ['keep-mine', 'take-target', 'prose', 'abort'],
          description: "'keep-mine' keeps the reviewed work; 'take-target' takes the drifted integration target; 'prose' writes the given content verbatim; 'abort' aborts the paused rebase, restoring the frozen pre-rebase commit.",
        },
        content: { type: 'string' as const, description: "Required, and used only, when choice is 'prose'." },
      },
      required: ['repository_path', 'workspace_guid', 'path', 'choice'],
      additionalProperties: false,
    },
  },
  {
    name: 'close_out_worktree',
    description: 'Integrate this managed worktree HEAD into local main and FULLY tear the worktree down (remove worktree + temp branch), auto-resolving push-pending/dirty/recoverable-rebase; gated to the provider-root session; never pushes.',
    inputSchema: {
      type: 'object' as const,
      properties: { repository_path: repositoryProperty, workspace_guid: workspaceProperty },
      required: ['repository_path', 'workspace_guid'],
      additionalProperties: false,
    },
  },
  {
    name: 'list_preserved_work',
    description: 'List work preserved on terminal rows for this repository and provider-root session: push obligations carried by a close-out (kind "pending-push") and residual snapshotted to a recovery ref (kind "recovery").',
    inputSchema: {
      type: 'object' as const,
      properties: { repository_path: repositoryProperty },
      required: ['repository_path'],
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

function requiredConflictHunkChoice(args: Args): 'keep-mine' | 'take-target' | 'prose' | 'abort' {
  const value = args.choice;
  if (value !== 'keep-mine' && value !== 'take-target' && value !== 'prose' && value !== 'abort') {
    throw new Error("choice must be 'keep-mine', 'take-target', 'prose', or 'abort'");
  }
  return value;
}

function optionalMode(
  args: Args,
): 'status' | 'continue' | 'abort' | 'rerebase' | 'restore_frozen' | 'reopen_for_edit' | undefined {
  const value = args.mode;
  if (value === undefined) return undefined;
  if (value !== 'status' && value !== 'continue' && value !== 'abort'
    && value !== 'rerebase' && value !== 'restore_frozen' && value !== 'reopen_for_edit') {
    throw new Error("mode must be 'status', 'continue', 'abort', 'rerebase', 'restore_frozen', or 'reopen_for_edit'");
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
    case 'sync_worktree_to_target': return dependencies.syncWorktreeToTarget(args);
    case 'reconcile_worktree': return dependencies.reconcileWorktree(args);
    case 'land_resolved_conflict': return dependencies.landResolvedConflict(args);
    case 'resolve_conflict_hunk': return dependencies.resolveConflictHunk(args);
    case 'close_out_worktree': return dependencies.closeOutWorktree(args);
    case 'list_preserved_work': return dependencies.listPreservedWork(args);
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
        workspaceGuid: optionalString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
        humanChannel,
        operation,
      });
      const message = operation === 'push' ? '' : requiredString(args, 'message');
      if (authority.checkoutMode === 'primary-unassigned') {
        if (authority.operation === 'push') return finalizePrimaryUnassignedPush(authority, undefined, db);
        if (authority.operation === 'commit-and-push') return finalizePrimaryUnassignedCommitAndPush(authority, message);
        return finalizePrimaryUnassignedCommit(authority, message);
      }
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
    syncWorktreeToTarget: (args) => {
      requireProviderRoot();
      return syncWorktreeToTarget(db, {
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: requiredString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
      });
    },
    reconcileWorktree: (args) => {
      requireProviderRoot();
      const authority = verifyDirectGitAuthority(db, {
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: optionalString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
        humanChannel,
        operation: 'reconcile',
      });
      return finalizeReconcile(db, authority);
    },
    landResolvedConflict: (args) => {
      requireProviderRoot();
      const authority = verifyDirectGitAuthority(db, {
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: optionalString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
        humanChannel,
        operation: 'confirm-resolution',
      });
      return finalizeConfirmResolution(db, authority);
    },
    resolveConflictHunk: (args) => {
      requireProviderRoot();
      return resolveConflictHunk(db, {
        repositoryPath: requiredString(args, 'repository_path'),
        workspaceGuid: requiredString(args, 'workspace_guid'),
        providerRootSessionId: identity.sessionId,
        path: requiredString(args, 'path'),
        choice: requiredConflictHunkChoice(args),
        content: optionalString(args, 'content'),
      });
    },
    closeOutWorktree: (args) => {
      requireProviderRoot();
      const repositoryPath = requiredString(args, 'repository_path');
      const workspaceGuid = requiredString(args, 'workspace_guid');
      // R3: an integrated row whose managed worktree was already removed (crash mid-teardown)
      // cannot mint authority — resolveEffectiveCheckout requires the worktree present, yielding a
      // raw identity error. Complete DB-only via the owner-bound cleanupWorkspace (which now carries
      // the obligation into preserved_work); no git op on the gone worktree, no push. A PRESENT
      // worktree falls through to the normal status-probe -> authority -> finalizeCloseOut path.
      const existing = getAssignment(db, workspaceGuid);
      if (existing && existing.lifecycle_status === 'integrated') {
        const repo = discoverRepository(repositoryPath);
        const present = fs.existsSync(existing.worktree_path)
          && worktreeExists(repo.primaryCheckoutPath, existing.worktree_path);
        if (!present) {
          const carried = pushPendingSummary(existing.disposition);
          const cleaned = service.cleanupWorkspace({ repositoryPath, workspaceGuid, ownerSessionId: identity.sessionId });
          return {
            state: 'closed-out',
            integratedCommit: cleaned.integrated_commit ?? existing.integrated_commit ?? undefined,
            ...(carried ? { pendingPush: carried } : {}),
          };
        }
      }
      // Handler-orchestrated paused-rebase recovery BEFORE authority (a paused rebase
      // detaches HEAD, so no close-out authority could be minted on it): auto-continue a
      // clean paused rebase via the authority-free recovery tool, preserve-and-defer a
      // conflict. reconcileFinalization is used UNMODIFIED (reconcile lane byte-untouched).
      const status = reconcileFinalization(db, {
        repositoryPath, workspaceGuid, providerRootSessionId: identity.sessionId, rebaseRecovery: 'status',
      });
      if (status.state === 'rebase-paused-conflict') {
        return {
          state: 'rebase-paused-conflict',
          detail: 'Close-out paused: a rebase conflict needs automated resolution (pending); worktree preserved. Not an operator task.',
        };
      }
      if (status.state === 'rebase-paused-clean') {
        try {
          const cont = reconcileFinalization(db, {
            repositoryPath, workspaceGuid, providerRootSessionId: identity.sessionId, rebaseRecovery: 'continue',
          });
          // C1: only a clean-integrate continue may proceed to teardown. A content-changing
          // resolution (rebase-recovery-repair-required) integrates NOTHING and is
          // preserved-and-deferred here; it must never fall through to finalizeCloseOut.
          if (cont.state !== 'cleaned' && cont.state !== 'integrated-local') {
            return cont;
          }
        } catch (error) {
          // continue re-conflicted or paused again: re-probe and preserve-and-defer with the
          // probe's own paused state. Never surface as an operator action item.
          const probe = reconcileFinalization(db, {
            repositoryPath, workspaceGuid, providerRootSessionId: identity.sessionId, rebaseRecovery: 'status',
          });
          if (probe.state === 'rebase-paused-conflict' || probe.state === 'rebase-paused-clean') {
            return {
              state: probe.state,
              detail: `Close-out paused: the rebase needs automated resolution (pending); worktree preserved. Not an operator task. (${error instanceof Error ? error.message : String(error)})`,
            };
          }
          throw error;
        }
      }
      const authority = verifyDirectGitAuthority(db, {
        repositoryPath,
        workspaceGuid,
        providerRootSessionId: identity.sessionId,
        humanChannel,
        operation: 'close-out',
      });
      return finalizeCloseOut(db, authority);
    },
    listPreservedWork: (args) => {
      const repository = discoverRepository(requiredString(args, 'repository_path'));
      const preserved: Array<{ workspace_guid: string; kind: 'pending-push' | 'recovery'; destinationRef?: string; ref?: string }> = [];
      // I-1: UNION the durable preserved_work table (which survives row reuse) with the existing
      // assignments cleaned/abandoned query; NEVER replace it (a legacy row carrying recovery_ref
      // with no table entry must stay listed). Dedupe: emit table rows first, then append an
      // assignments leg only when the table has not already surfaced it.
      const pendingPushGuids = new Set<string>();
      const emittedRefs = new Set<string>();
      for (const row of listUnresolvedPreservedWork(db, repository.repositoryIdentity, identity.sessionId)) {
        let payload: { destinationRef?: string; ref?: string };
        try { payload = JSON.parse(row.payload); } catch { continue; }
        if (row.kind === 'pending-push') {
          preserved.push({ workspace_guid: row.workspace_guid, kind: 'pending-push', destinationRef: payload.destinationRef });
          pendingPushGuids.add(row.workspace_guid);
        } else if (payload.ref) {
          preserved.push({ workspace_guid: row.workspace_guid, kind: 'recovery', ref: payload.ref });
          emittedRefs.add(payload.ref);
        }
      }
      const rows = db.prepare(`
        SELECT workspace_guid, disposition, recovery_ref FROM assignments
        WHERE repository_identity = ? AND owner_session_id = ?
          AND lifecycle_status IN ('cleaned', 'abandoned')
          AND (disposition IS NOT NULL OR recovery_ref IS NOT NULL)
        ORDER BY created_at ASC
      `).all(repository.repositoryIdentity, identity.sessionId) as {
        workspace_guid: string; disposition: string | null; recovery_ref: string | null;
      }[];
      for (const row of rows) {
        const summary = pushPendingSummary(row.disposition);
        if (summary && !pendingPushGuids.has(row.workspace_guid)) {
          preserved.push({ workspace_guid: row.workspace_guid, kind: 'pending-push', destinationRef: summary.destinationRef });
        }
        if (row.recovery_ref && !emittedRefs.has(row.recovery_ref)) {
          preserved.push({ workspace_guid: row.workspace_guid, kind: 'recovery', ref: row.recovery_ref });
          emittedRefs.add(row.recovery_ref);
        }
      }
      return preserved;
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

  // Exit when the parent (Claude/Codex session) goes away so this server never
  // lingers as an orphan holding memory. macOS has no PDEATHSIG, so watch both:
  process.stdin.on('end', () => process.exit(0));
  const icPpid = Number(process.env.CLAUDE_PPID);
  if (Number.isInteger(icPpid) && icPpid > 1) {
    const pollMs = Number(process.env.IC_PPID_POLL_MS) || 30000;
    setInterval(() => {
      try { process.kill(icPpid, 0); }
      catch (err: any) { if (err && err.code === 'ESRCH') process.exit(0); }
    }, pollMs); // refed on purpose — this watchdog must keep running
  }
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  startWorkspaceManagerServer().catch((error) => {
    console.error('Workspace-manager server error:', error);
    process.exit(1);
  });
}
