import { describe, expect, it, vi } from 'vitest';
import {
  PUBLIC_TOOL_NAMES,
  createPublicToolDependencies,
  dispatchPublicTool,
  publicToolDefinitions,
  type PublicToolDependencies,
} from '../index.js';
import {
  INTERNAL_COMMAND_NAMES,
  createInternalCommandDependencies,
  dispatchInternalCommand,
  type InternalCommandDependencies,
} from '../cli.js';
import { WorkspaceService } from '../workspace-service.js';

const PUBLIC_TOOLS = [
  'get_workspace_status',
  'activate_session_workspace',
  'use_primary_checkout',
  'return_to_managed_worktree',
  'list_active_assignments',
  'commit',
  'commit_and_push',
  'push',
] as const;

const INTERNAL_COMMANDS = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile'] as const;

function publicDependencies(): PublicToolDependencies {
  return {
    getWorkspaceStatus: vi.fn().mockReturnValue({ workspace_guid: 'workspace' }),
    activateSessionWorkspace: vi.fn().mockReturnValue({ workspace_guid: 'workspace' }),
    usePrimaryCheckout: vi.fn().mockReturnValue({ primaryCheckoutPath: '/repo' }),
    returnToManagedWorktree: vi.fn().mockReturnValue({ managedWorktreePath: '/repo/.ironclaude/worktrees/workspace' }),
    listActiveAssignments: vi.fn().mockReturnValue([]),
    finalizeDirect: vi.fn().mockReturnValue({ state: 'integrated-local' }),
  };
}

function internalDependencies(): InternalCommandDependencies {
  return {
    allocate: vi.fn().mockReturnValue({ workspace_guid: 'workspace' }),
    bind: vi.fn().mockReturnValue({ workspace_guid: 'workspace' }),
    finalize: vi.fn().mockReturnValue({ state: 'integrated-local' }),
    abandon: vi.fn().mockReturnValue({ lifecycle_status: 'abandoned' }),
    reconcile: vi.fn().mockReturnValue({ assignments: [], unregisteredWorktrees: [] }),
  };
}

describe('workspace-manager entrypoint surfaces', () => {
  it('exposes only the bounded public MCP surface', () => {
    expect(PUBLIC_TOOL_NAMES).toEqual(PUBLIC_TOOLS);
    expect(publicToolDefinitions.map((definition) => definition.name)).toEqual(PUBLIC_TOOLS);
    expect(PUBLIC_TOOL_NAMES).not.toContain('integrate');
    expect(PUBLIC_TOOL_NAMES).not.toContain('integrate_workspace');
    expect(PUBLIC_TOOL_NAMES).not.toContain('commit_worker');
    for (const definition of publicToolDefinitions.filter((tool) => [
      'use_primary_checkout', 'return_to_managed_worktree', 'commit', 'commit_and_push', 'push',
    ].includes(tool.name))) {
      expect(definition.inputSchema.properties).not.toHaveProperty('nonce');
      expect(definition.inputSchema.properties).not.toHaveProperty('expected_evidence');
      expect(definition.inputSchema.properties).not.toHaveProperty('human_channel');
    }
  });

  it('keeps hook issuance out of the general CLI and exposes no internal push command', () => {
    expect(INTERNAL_COMMAND_NAMES).toEqual(INTERNAL_COMMANDS);
    expect(INTERNAL_COMMAND_NAMES).not.toContain('push');
    expect(INTERNAL_COMMAND_NAMES).not.toContain('commit-and-push');
    expect(INTERNAL_COMMAND_NAMES).not.toContain('integrate');
    expect(INTERNAL_COMMAND_NAMES).not.toContain('issue-human-intent');
  });

  it.each(PUBLIC_TOOLS.slice(0, 5))('dispatches public tool %s exactly once', (name) => {
    const dependencies = publicDependencies();
    const result = dispatchPublicTool(name, { marker: name }, dependencies);
    const called = Object.values(dependencies).filter((dependency) => vi.mocked(dependency).mock.calls.length > 0);
    expect(called).toHaveLength(1);
    expect(called[0]).toHaveBeenCalledWith({ marker: name });
    expect(result).toBeDefined();
  });

  it('keeps commit, commit-and-push, and push on the direct finalization boundary', () => {
    const dependencies = publicDependencies();
    dispatchPublicTool('commit', { marker: 'commit' }, dependencies);
    dispatchPublicTool('commit_and_push', { marker: 'commit_and_push' }, dependencies);
    dispatchPublicTool('push', { marker: 'push' }, dependencies);
    expect(dependencies.finalizeDirect).toHaveBeenNthCalledWith(1, 'commit', { marker: 'commit' });
    expect(dependencies.finalizeDirect).toHaveBeenNthCalledWith(2, 'commit-and-push', { marker: 'commit_and_push' });
    expect(dependencies.finalizeDirect).toHaveBeenNthCalledWith(3, 'push', { marker: 'push' });
  });

  it.each(INTERNAL_COMMANDS)('dispatches internal command %s exactly once', (name) => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand(name, { marker: name }, dependencies);
    const called = Object.values(dependencies).filter((dependency) => vi.mocked(dependency).mock.calls.length > 0);
    expect(called).toHaveLength(1);
    expect(called[0]).toHaveBeenCalledWith({ marker: name });
    expect(result).toBeDefined();
  });

  it('rejects unknown public and internal operations without fallback', () => {
    expect(() => dispatchPublicTool('integrate', {}, publicDependencies())).toThrow('Unknown public workspace tool: integrate');
    expect(() => dispatchInternalCommand('push', {}, internalDependencies())).toThrow('Unknown internal workspace command: push');
    expect(() => dispatchInternalCommand('issue-human-intent', {}, internalDependencies()))
      .toThrow('Unknown internal workspace command: issue-human-intent');
  });

  it('defines additive read-only status lookup by exact GUID or provider root', () => {
    const definition = publicToolDefinitions.find((tool) => tool.name === 'get_workspace_status');
    expect(definition?.inputSchema.required).toEqual(['repository_path']);
    expect(definition?.inputSchema.properties).toHaveProperty('provider_root');
    expect(definition?.inputSchema.properties).toHaveProperty('workspace_guid');
  });

  it('dispatches status lookup through exactly one compatible selector', () => {
    const assignment = { workspace_guid: 'workspace' } as never;
    const exact = vi.spyOn(WorkspaceService.prototype, 'getWorkspaceAssignment').mockReturnValue(assignment);
    const root = vi.spyOn(WorkspaceService.prototype, 'getWorkspaceStatusForRoot')
      .mockReturnValue({ status: 'assigned', assignment });
    const dependencies = createPublicToolDependencies({} as never, {
      client: 'codex',
      sessionId: 'root-session',
      invocationThreadId: 'root-session',
      source: 'codex_meta',
    });

    expect(dependencies.getWorkspaceStatus({
      repository_path: '/repo', workspace_guid: 'workspace',
    })).toBe(assignment);
    expect(exact).toHaveBeenCalledWith({
      repositoryPath: '/repo', workspaceGuid: 'workspace', ownerSessionId: 'root-session',
    });
    expect(dependencies.getWorkspaceStatus({
      repository_path: '/repo', provider_root: 'root-session',
    })).toEqual({ status: 'assigned', assignment });
    expect(root).toHaveBeenCalledWith({ repositoryPath: '/repo', ownerSessionId: 'root-session' });
    expect(() => dependencies.getWorkspaceStatus({ repository_path: '/repo' }))
      .toThrow('exactly one');
    expect(() => dependencies.getWorkspaceStatus({
      repository_path: '/repo', workspace_guid: 'workspace', provider_root: 'root-session',
    })).toThrow('exactly one');
    expect(() => dependencies.getWorkspaceStatus({
      repository_path: '/repo', provider_root: 'other-root',
    })).toThrow('authenticated provider-root');
  });

  it('rejects forged review authority fields at the private CLI boundary', () => {
    const dependencies = createInternalCommandDependencies({} as never);
    expect(() => dependencies.finalize({ command: {}, reviewed_evidence: { forged: true } }))
      .toThrow('Commander finalization input is malformed');
  });

  it('rejects direct-human authority consumption from a Codex subagent identity', () => {
    const dependencies = createPublicToolDependencies({} as never, {
      client: 'codex',
      sessionId: 'root-session',
      invocationThreadId: 'child-thread',
      source: 'codex_meta',
    });
    expect(() => dependencies.usePrimaryCheckout({
      repository_path: '/repo', workspace_guid: 'workspace',
    })).toThrow('provider-root session');
    expect(() => dependencies.finalizeDirect('push', {
      repository_path: '/repo', workspace_guid: 'workspace',
    })).toThrow('provider-root session');
  });

  it('routes ownerless allocate to worker reservation and preserves owned direct allocation', () => {
    const reserved = { workspace_guid: '44444444-4444-4444-8444-444444444444' } as never;
    const reserve = vi.spyOn(WorkspaceService.prototype, 'reserveWorkerWorktree').mockReturnValue(reserved);
    const ensure = vi.spyOn(WorkspaceService.prototype, 'ensureSessionWorktree').mockReturnValue(reserved);
    const dependencies = createInternalCommandDependencies({} as never);

    expect(dependencies.allocate({
      repository_path: '/repo',
      workspace_guid: '44444444-4444-4444-8444-444444444444',
      worker_id: 'worker-17',
      integration_target: 'main',
    })).toBe(reserved);
    expect(reserve).toHaveBeenCalledWith({
      repositoryPath: '/repo',
      workspaceGuid: '44444444-4444-4444-8444-444444444444',
      workerId: 'worker-17',
      integrationTarget: 'main',
    });
    expect(ensure).not.toHaveBeenCalled();

    dependencies.allocate({
      repository_path: '/repo',
      workspace_guid: '44444444-4444-4444-8444-444444444444',
      owner_session_id: 'owner-direct',
      integration_target: 'main',
    });
    expect(ensure).toHaveBeenCalledWith({
      repositoryPath: '/repo',
      workspaceGuid: '44444444-4444-4444-8444-444444444444',
      ownerSessionId: 'owner-direct',
      workerId: undefined,
      integrationTarget: 'main',
    });
  });

  it('routes bind through the expected-state workspace service boundary', () => {
    const bound = { workspace_guid: '44444444-4444-4444-8444-444444444444' } as never;
    const bind = vi.spyOn(WorkspaceService.prototype, 'bindWorkerWorktree').mockReturnValue(bound);
    const dependencies = createInternalCommandDependencies({} as never);
    const args = {
      repository_path: '/repo',
      repository_identity: '/repo/.git',
      workspace_guid: '44444444-4444-4444-8444-444444444444',
      worker_id: 'worker-17',
      owner_session_id: 'owner-native',
      expected_lifecycle: 'active',
      expected_worktree_path: '/repo/.ironclaude/worktrees/44444444-4444-4444-8444-444444444444',
      expected_branch: 'ironclaude/44444444-4444-4444-8444-444444444444',
      expected_base_commit: 'a'.repeat(40),
      expected_current_head: 'a'.repeat(40),
    };

    expect(dependencies.bind(args)).toBe(bound);
    expect(bind).toHaveBeenCalledWith({
      repositoryPath: args.repository_path,
      repositoryIdentity: args.repository_identity,
      workspaceGuid: args.workspace_guid,
      workerId: args.worker_id,
      ownerSessionId: args.owner_session_id,
      expectedLifecycle: args.expected_lifecycle,
      expectedWorktreePath: args.expected_worktree_path,
      expectedBranch: args.expected_branch,
      expectedBaseCommit: args.expected_base_commit,
      expectedCurrentHead: args.expected_current_head,
    });
  });
});
