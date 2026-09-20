import { describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  INTERNAL_COMMAND_NAMES,
  createInternalCommandDependencies,
  dispatchInternalCommand,
  type InternalCommandDependencies,
} from '../cli.js';
import { initDb } from '../db.js';
import { WorkspaceService } from '../workspace-service.js';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

function internalDependencies(): InternalCommandDependencies {
  return {
    allocate: vi.fn().mockReturnValue({ workspace_guid: 'workspace' }),
    bind: vi.fn().mockReturnValue({ workspace_guid: 'workspace' }),
    finalize: vi.fn().mockReturnValue({ state: 'integrated-local' }),
    abandon: vi.fn().mockReturnValue({ lifecycle_status: 'abandoned' }),
    reconcile: vi.fn().mockReturnValue({ assignments: [], unregisteredWorktrees: [] }),
    cleanup: vi.fn().mockReturnValue({ lifecycle_status: 'cleaned' }),
    sync: vi.fn().mockReturnValue({ state: 'fast-forwarded' }),
    reap: vi.fn().mockReturnValue({ lifecycle_status: 'reaped' }),
    configureSharedResources: vi.fn().mockReturnValue({ added: [], skipped: [], rejected: [], entries: [], relinked: {} }),
    listSharedResources: vi.fn().mockReturnValue({ entries: [] }),
    'reap-orphans': vi.fn().mockReturnValue({
      repositoryIdentity: '/r/.git', reaped: [], reapedWorktreeOnly: [], preservedDirty: [], preservedUnmerged: [], skippedLive: [], skippedYoung: [], errors: [],
    }),
    'resolve-orphan': vi.fn().mockReturnValue({ results: [] }),
  };
}

describe('workspace-manager internal CLI: cleanup command and abandon rescue mode', () => {
  it('exposes cleanup as an internal command alongside the existing set', () => {
    expect(INTERNAL_COMMAND_NAMES).toContain('cleanup');
    expect(INTERNAL_COMMAND_NAMES).toContain('abandon');
  });

  it('dispatches the cleanup command to its handler exactly once', () => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand('cleanup', { marker: 'cleanup' }, dependencies);
    expect(dependencies.cleanup).toHaveBeenCalledWith({ marker: 'cleanup' });
    expect(dependencies.cleanup).toHaveBeenCalledTimes(1);
    const calledOthers = [dependencies.allocate, dependencies.bind, dependencies.finalize, dependencies.abandon, dependencies.reconcile]
      .filter((dependency) => vi.mocked(dependency).mock.calls.length > 0);
    expect(calledOthers).toHaveLength(0);
    expect(result).toBeDefined();
  });

  it('forwards the mode field on the abandon command payload untouched', () => {
    const abandon = vi.spyOn(WorkspaceService.prototype, 'abandonWorkspace')
      .mockReturnValue({ lifecycle_status: 'abandoned' } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    dependencies.abandon({
      repository_path: '/repo',
      workspace_guid: '44444444-4444-4444-8444-444444444444',
      owner_session_id: 'owner-1',
      mode: 'rescue',
    });

    expect(abandon).toHaveBeenCalledWith({
      repositoryPath: '/repo',
      workspaceGuid: '44444444-4444-4444-8444-444444444444',
      ownerSessionId: 'owner-1',
      mode: 'rescue',
    });
  });

  it('omits mode when the abandon payload does not request rescue', () => {
    const abandon = vi.spyOn(WorkspaceService.prototype, 'abandonWorkspace')
      .mockReturnValue({ lifecycle_status: 'abandoned' } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    dependencies.abandon({
      repository_path: '/repo',
      workspace_guid: '44444444-4444-4444-8444-444444444444',
      owner_session_id: 'owner-1',
    });

    expect(abandon).toHaveBeenCalledWith({
      repositoryPath: '/repo',
      workspaceGuid: '44444444-4444-4444-8444-444444444444',
      ownerSessionId: 'owner-1',
      mode: undefined,
    });
  });

  it('routes a reserved row through the carve-out and an integrated row through cleanupWorkspace', () => {
    const getAssignment = vi.spyOn(WorkspaceService.prototype, 'getWorkspaceAssignment');
    const cleanupReserved = vi.spyOn(WorkspaceService.prototype, 'cleanupReservedAssignment')
      .mockReturnValue({ lifecycle_status: 'cleaned' } as never);
    const cleanupWorkspace = vi.spyOn(WorkspaceService.prototype, 'cleanupWorkspace')
      .mockReturnValue({ lifecycle_status: 'cleaned' } as never);
    const dependencies = createInternalCommandDependencies({} as never);
    const args = { repository_path: '/repo', workspace_guid: '44444444-4444-4444-8444-444444444444', owner_session_id: 'owner-1' };

    getAssignment.mockReturnValueOnce({ lifecycle_status: 'reserved' } as never);
    dependencies.cleanup(args);
    expect(cleanupReserved).toHaveBeenCalledTimes(1);
    expect(cleanupWorkspace).not.toHaveBeenCalled();

    getAssignment.mockReturnValueOnce({ lifecycle_status: 'integrated' } as never);
    dependencies.cleanup(args);
    expect(cleanupWorkspace).toHaveBeenCalledTimes(1);
    expect(cleanupReserved).toHaveBeenCalledTimes(1);
  });

  it('forwards a dispose field from the finalize command payload into finalizeCommanderLocalCommit and it validates', () => {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-cli-finalize-'));
    const databaseDir = mkdtempSync(join(tmpdir(), 'ironclaude-cli-finalize-db-'));
    try {
      git(root, 'init', '--initial-branch=main');
      git(root, 'config', 'user.name', 'CLI Finalize Test');
      git(root, 'config', 'user.email', 'cli-finalize@example.invalid');
      writeFileSync(join(root, 'README.md'), 'initial\n');
      git(root, 'add', 'README.md');
      git(root, 'commit', '-m', 'initial');
      const database = initDb(join(databaseDir, 'state.db'));
      const service = new WorkspaceService(database);
      const ownerSessionId = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
      const assignment = service.ensureSessionWorktree({ repositoryPath: root, ownerSessionId });
      writeFileSync(join(assignment.worktree_path, 'work.txt'), 'approved\n');
      git(assignment.worktree_path, 'add', 'work.txt');

      const dependencies = createInternalCommandDependencies(database);
      const command = {
        repositoryPath: root,
        workspaceGuid: assignment.workspace_guid,
        providerRootSessionId: ownerSessionId,
        message: 'release via cli passthrough',
        canonicalBranch: assignment.branch,
        localRef: `refs/heads/${assignment.branch}`,
        stagedTree: git(assignment.worktree_path, 'write-tree'),
        parentOid: git(assignment.worktree_path, 'rev-parse', 'HEAD'),
        dispose: 'release',
      };

      const result = dependencies.finalize({ command }) as { state: string };

      expect(result.state).toBe('cleaned');
      expect(existsSync(assignment.worktree_path)).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(databaseDir, { recursive: true, force: true });
    }
  });

  it('refuses to clean up an active row (fail-closed)', () => {
    vi.spyOn(WorkspaceService.prototype, 'getWorkspaceAssignment').mockReturnValue({ lifecycle_status: 'active' } as never);
    vi.spyOn(WorkspaceService.prototype, 'cleanupWorkspace').mockImplementation(() => {
      throw new Error('Only integrated or abandoned worktrees are eligible for cleanup');
    });
    const dependencies = createInternalCommandDependencies({} as never);

    expect(() => dependencies.cleanup({
      repository_path: '/repo', workspace_guid: '44444444-4444-4444-8444-444444444444', owner_session_id: 'owner-1',
    })).toThrow('eligible for cleanup');
  });
});

describe('workspace-manager internal CLI: reap command (owner-agnostic reaper path)', () => {
  it('exposes reap as an internal command alongside the existing set', () => {
    expect(INTERNAL_COMMAND_NAMES).toContain('reap');
  });

  it('dispatches the reap command to its handler exactly once', () => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand('reap', { marker: 'reap' }, dependencies);
    expect(dependencies.reap).toHaveBeenCalledWith({ marker: 'reap' });
    expect(dependencies.reap).toHaveBeenCalledTimes(1);
    const calledOthers = [dependencies.allocate, dependencies.bind, dependencies.finalize, dependencies.abandon, dependencies.reconcile, dependencies.cleanup, dependencies.sync]
      .filter((dependency) => vi.mocked(dependency).mock.calls.length > 0);
    expect(calledOthers).toHaveLength(0);
    expect(result).toBeDefined();
  });

  it('forwards repository_path and workspace_guid (no owner) into reapLeakedAssignment', () => {
    const reap = vi.spyOn(WorkspaceService.prototype, 'reapLeakedAssignment')
      .mockReturnValue({ lifecycle_status: 'reaped' } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    const result = dependencies.reap({
      repository_path: '/repo',
      workspace_guid: '44444444-4444-4444-8444-444444444444',
    });

    expect(reap).toHaveBeenCalledWith({
      repositoryPath: '/repo',
      workspaceGuid: '44444444-4444-4444-8444-444444444444',
    });
    expect(reap).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ lifecycle_status: 'reaped' });
  });
});

describe('workspace-manager internal CLI: reap-orphans command (ambiguous-orphan reaper path)', () => {
  it('exposes reap-orphans as an internal command alongside the existing set', () => {
    expect(INTERNAL_COMMAND_NAMES).toContain('reap-orphans');
  });

  it('dispatches the reap-orphans command to its handler exactly once', () => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand('reap-orphans', { marker: 'reap-orphans' }, dependencies);
    expect(dependencies['reap-orphans']).toHaveBeenCalledWith({ marker: 'reap-orphans' });
    expect(dependencies['reap-orphans']).toHaveBeenCalledTimes(1);
    const calledOthers = [dependencies.allocate, dependencies.bind, dependencies.finalize, dependencies.abandon, dependencies.reconcile, dependencies.cleanup, dependencies.sync, dependencies.reap]
      .filter((dependency) => vi.mocked(dependency).mock.calls.length > 0);
    expect(calledOthers).toHaveLength(0);
    expect(result).toBeDefined();
  });

  it('forwards repositoryPath, protectedPaths, and ttlHours into reapAmbiguousOrphans', () => {
    const reapOrphans = vi.spyOn(WorkspaceService.prototype, 'reapAmbiguousOrphans')
      .mockReturnValue({
        repositoryIdentity: '/r/.git', reaped: [], reapedWorktreeOnly: [], preservedDirty: [], preservedUnmerged: [], skippedLive: [], skippedYoung: [], errors: [],
      } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    const result = dependencies['reap-orphans']({
      repository_path: '/r',
      protected_paths: ['/p'],
      ttl_hours: 0,
    });

    expect(reapOrphans).toHaveBeenCalledWith({
      repositoryPath: '/r',
      protectedPaths: ['/p'],
      ttlHours: 0,
    });
    expect(reapOrphans).toHaveBeenCalledTimes(1);
    expect(result).toEqual({
      repositoryIdentity: '/r/.git', reaped: [], reapedWorktreeOnly: [], preservedDirty: [], preservedUnmerged: [], skippedLive: [], skippedYoung: [], errors: [],
    });
  });

  it('rejects an unknown internal command', () => {
    expect(() => dispatchInternalCommand('bogus-verb', {}, internalDependencies()))
      .toThrow('Unknown internal workspace command: bogus-verb');
  });
});

describe('workspace-manager internal CLI: resolve-orphan command (orphan resolution path)', () => {
  it('exposes resolve-orphan as an internal command alongside the existing set', () => {
    expect(INTERNAL_COMMAND_NAMES).toContain('resolve-orphan');
  });

  it('dispatches the resolve-orphan command to its handler exactly once', () => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand('resolve-orphan', { marker: 'resolve-orphan' }, dependencies);
    expect(dependencies['resolve-orphan']).toHaveBeenCalledWith({ marker: 'resolve-orphan' });
    expect(dependencies['resolve-orphan']).toHaveBeenCalledTimes(1);
    const calledOthers = [
      dependencies.allocate, dependencies.bind, dependencies.finalize, dependencies.abandon,
      dependencies.reconcile, dependencies.cleanup, dependencies.sync, dependencies.reap,
      dependencies['reap-orphans'],
    ].filter((dependency) => vi.mocked(dependency).mock.calls.length > 0);
    expect(calledOthers).toHaveLength(0);
    expect(result).toBeDefined();
  });

  it('forwards repositoryPath, protectedPaths, and resolutions into service.resolveOrphan', () => {
    const resolveOrphan = vi.spyOn(WorkspaceService.prototype, 'resolveOrphan')
      .mockReturnValue({ results: [] } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    const result = dependencies['resolve-orphan']({
      repository_path: '/r',
      protected_paths: ['/p'],
      resolutions: [{ guid: 'g1', action: 'reap' }],
    });

    expect(resolveOrphan).toHaveBeenCalledWith({
      repositoryPath: '/r',
      protectedPaths: ['/p'],
      resolutions: [{ guid: 'g1', action: 'reap' }],
    });
    expect(resolveOrphan).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ results: [] });
  });

  it('rejects a resolutions payload that is missing, empty, or has an entry with no id/guid or a bad action', () => {
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
    })).toThrow();
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [],
    })).toThrow();
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ action: 'reap' }],
    })).toThrow();
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'bogus' }],
    })).toThrow();
  });

  it('maps a resolution entry\'s snake_case integration_target into camelCase integrationTarget for service.resolveOrphan', () => {
    const resolveOrphan = vi.spyOn(WorkspaceService.prototype, 'resolveOrphan')
      .mockReturnValue({ results: [] } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    dependencies['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'merge-then-reap', integration_target: 'trunk' }],
    });

    expect(resolveOrphan).toHaveBeenCalledWith({
      repositoryPath: '/r',
      protectedPaths: undefined,
      resolutions: [{ guid: 'g1', action: 'merge-then-reap', integrationTarget: 'trunk' }],
    });
    expect(resolveOrphan).toHaveBeenCalledTimes(1);
  });

  it('omits integrationTarget when integration_target is absent from the resolution entry', () => {
    const resolveOrphan = vi.spyOn(WorkspaceService.prototype, 'resolveOrphan')
      .mockReturnValue({ results: [] } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    dependencies['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'reap' }],
    });

    expect(resolveOrphan).toHaveBeenCalledWith({
      repositoryPath: '/r',
      protectedPaths: undefined,
      resolutions: [{ guid: 'g1', action: 'reap' }],
    });
  });

  it('rejects a resolution entry whose integration_target is not a non-empty string', () => {
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'merge-then-reap', integration_target: '' }],
    })).toThrow('resolutions entries integration_target must be a non-empty string');
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'merge-then-reap', integration_target: 7 }],
    })).toThrow('resolutions entries integration_target must be a non-empty string');
  });

  it('forwards a resolution entry\'s category through to service.resolveOrphan', () => {
    const resolveOrphan = vi.spyOn(WorkspaceService.prototype, 'resolveOrphan')
      .mockReturnValue({ results: [] } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    dependencies['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'reap', category: 'dirty' }],
    });

    expect(resolveOrphan).toHaveBeenCalledWith({
      repositoryPath: '/r',
      protectedPaths: undefined,
      resolutions: [{ guid: 'g1', action: 'reap', category: 'dirty' }],
    });
    expect(resolveOrphan).toHaveBeenCalledTimes(1);
  });

  it('omits category when absent from the resolution entry', () => {
    const resolveOrphan = vi.spyOn(WorkspaceService.prototype, 'resolveOrphan')
      .mockReturnValue({ results: [] } as never);
    const dependencies = createInternalCommandDependencies({} as never);

    dependencies['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'reap' }],
    });

    expect(resolveOrphan).toHaveBeenCalledWith({
      repositoryPath: '/r',
      protectedPaths: undefined,
      resolutions: [{ guid: 'g1', action: 'reap' }],
    });
  });

  it('rejects a resolution entry whose category is not a non-empty string naming one of the known categories', () => {
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'reap', category: '' }],
    })).toThrow("resolutions entries category must be 'squash-merged', 'merged-on-origin', 'genuinely-unmerged', or 'dirty'");
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'reap', category: 7 }],
    })).toThrow("resolutions entries category must be 'squash-merged', 'merged-on-origin', 'genuinely-unmerged', or 'dirty'");
    expect(() => createInternalCommandDependencies({} as never)['resolve-orphan']({
      repository_path: '/r',
      resolutions: [{ guid: 'g1', action: 'reap', category: 'bogus-category' }],
    })).toThrow("resolutions entries category must be 'squash-merged', 'merged-on-origin', 'genuinely-unmerged', or 'dirty'");
  });
});
