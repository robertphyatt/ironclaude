import { describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdtempSync, readFileSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
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
    listSurfacedOrphans: vi.fn().mockReturnValue({ orphans: [] }),
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
    expect(INTERNAL_COMMAND_NAMES).toContain('begin-handoff');
    expect(INTERNAL_COMMAND_NAMES).toContain('transfer-handoff');
    expect(INTERNAL_COMMAND_NAMES).toContain('finish-handoff');
  });

  it('keeps old dependency fixtures compatible while refusing absent private handoff handlers', () => {
    expect(() => dispatchInternalCommand('begin-handoff', {}, internalDependencies()))
      .toThrow('begin-handoff dependency is unavailable');
  });

  it('reports protocol support before a Commander grant exists', () => {
    const db = initDb(':memory:');
    const result = createInternalCommandDependencies(db)['handoff-status']!({
      workspace_guid: '11111111-1111-4111-8111-111111111111',
    });
    expect(result).toEqual({ protocol_version: 1, present: false, workspace_guid: '11111111-1111-4111-8111-111111111111' });
  });

  it('dispatches the scoped private handoff lifecycle against real Git and SQLite without changing dirty work', () => {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-cli-handoff-'));
    const databaseDir = mkdtempSync(join(tmpdir(), 'ironclaude-cli-handoff-db-'));
    const database = initDb(join(databaseDir, 'state.db'));
    try {
      git(root, 'init', '--initial-branch=main'); git(root, 'config', 'user.name', 'CLI Handoff Test');
      git(root, 'config', 'user.email', 'cli-handoff@example.invalid');
      writeFileSync(join(root, 'tracked.txt'), 'base\n'); git(root, 'add', 'tracked.txt'); git(root, 'commit', '-m', 'base');
      const source = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
      const target = '019f7cdf-023c-74e0-9ead-9c155636885d';
      const guid = '11111111-1111-4111-8111-111111111111';
      const service = new WorkspaceService(database);
      const reserved = service.reserveWorkerWorktree({ repositoryPath: root, workspaceGuid: guid, workerId: 'cli-handoff-worker' });
      const assignment = service.bindWorkerWorktree({ repositoryPath: root, repositoryIdentity: reserved.repository_identity, workspaceGuid: guid, workerId: reserved.worker_id!, ownerSessionId: source, expectedLifecycle: 'active', expectedWorktreePath: reserved.worktree_path, expectedBranch: reserved.branch, expectedBaseCommit: reserved.base_commit, expectedCurrentHead: reserved.current_head });
      const worktree = assignment.worktree_path;
      writeFileSync(join(worktree, 'tracked.txt'), 'staged\n'); git(worktree, 'add', 'tracked.txt');
      writeFileSync(join(worktree, 'tracked.txt'), 'unstaged\n'); writeFileSync(join(worktree, 'untracked.txt'), 'untracked\n');
      symlinkSync('untracked.txt', join(worktree, 'shared-link'));
      const snapshot = () => ({ head: git(worktree, 'rev-parse', 'HEAD'), branch: git(worktree, 'branch', '--show-current'), index: git(worktree, 'write-tree'), staged: git(worktree, 'diff', '--cached', '--binary'), unstaged: git(worktree, 'diff', '--binary'), status: git(worktree, 'status', '--porcelain=v1', '--untracked-files=all'), tracked: readFileSync(join(worktree, 'tracked.txt'), 'utf8'), untracked: readFileSync(join(worktree, 'untracked.txt'), 'utf8'), link: readlinkSync(join(worktree, 'shared-link')), isLink: lstatSync(join(worktree, 'shared-link')).isSymbolicLink(), primaryHead: git(root, 'rev-parse', 'HEAD') });
      const before = snapshot(); const dependencies = createInternalCommandDependencies(database);
      const token = 'private-cli-capability'; const tokenHash = createHash('sha256').update(token).digest('hex');
      const expiresAt = new Date(Date.now() + 60_000).toISOString();
      const args = { repository_path: root, repository_identity: assignment.repository_identity, workspace_guid: guid, worker_id: assignment.worker_id, source_owner_session_id: source, generation: 7, capability_token: token, expected_worktree_path: worktree, expected_branch: assignment.branch, expected_base_commit: assignment.base_commit, expected_current_head: assignment.current_head, expected_integration_target: assignment.integration_target };
      const owner = () => (database.prepare('SELECT owner_session_id FROM assignments WHERE workspace_guid = ?').get(guid) as { owner_session_id: string }).owner_session_id;
      const phase = () => (database.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(guid) as { phase: string }).phase;
      expect(dispatchInternalCommand('handoff-status', { workspace_guid: guid }, dependencies)).toEqual({ protocol_version: 1, present: false, workspace_guid: guid });
      for (const command of ['begin-handoff', 'transfer-handoff', 'finish-handoff']) {
        expect(() => dispatchInternalCommand(command, { ...args, target_owner_session_id: target }, dependencies)).toThrow(/grant not found|Handoff not found/);
        expect(snapshot()).toEqual(before); expect(owner()).toBe(source);
      }
      database.prepare(`INSERT INTO workspace_handoffs(workspace_guid,generation,capability_token_hash,grant_expires_at,source_owner_session_id,target_owner_session_id,repository_identity,worker_id,worktree_path,branch,base_commit,current_head,integration_target,phase) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,?, 'granted')`).run(guid, 7, tokenHash, expiresAt, source, assignment.repository_identity, assignment.worker_id, worktree, assignment.branch, assignment.base_commit, assignment.current_head, assignment.integration_target);
      for (const wrong of [
        { generation: 8 }, { capability_token: 'wrong' }, { source_owner_session_id: target },
        { repository_identity: 'wrong' }, { worker_id: 'wrong' }, { expected_worktree_path: '/wrong' },
        { expected_branch: 'wrong' }, { expected_base_commit: '0'.repeat(40) },
        { expected_current_head: '0'.repeat(40) }, { expected_integration_target: 'wrong' },
      ]) {
        expect(() => dispatchInternalCommand('begin-handoff', { ...args, ...wrong }, dependencies)).toThrow();
        expect(snapshot()).toEqual(before); expect(owner()).toBe(source); expect(phase()).toBe('granted');
      }
      const publicTuple = { protocol_version: 1, workspace_guid: guid, generation: 7, source_owner_session_id: source, repository_identity: assignment.repository_identity, worker_id: assignment.worker_id, worktree_path: worktree, branch: assignment.branch, base_commit: assignment.base_commit, current_head: assignment.current_head, integration_target: assignment.integration_target };
      const responses: unknown[] = [];
      const begun = dispatchInternalCommand('begin-handoff', args, dependencies);
      expect(begun).toEqual({ ...publicTuple, target_owner_session_id: null, phase: 'begun' }); responses.push(begun);
      expect(owner()).toBe(source); expect(phase()).toBe('begun'); expect(snapshot()).toEqual(before);
      expect(() => dispatchInternalCommand('transfer-handoff', { ...args, target_owner_session_id: target }, dependencies)).toThrow(/authorized/);
      expect(snapshot()).toEqual(before); expect(owner()).toBe(source);
      // Private Commander authorization occurs after begin and observed fresh target identity.
      database.prepare("UPDATE workspace_handoffs SET target_owner_session_id = ? WHERE workspace_guid = ? AND phase = 'begun'").run(target, guid);
      expect(() => dispatchInternalCommand('transfer-handoff', { ...args, target_owner_session_id: '33333333-3333-4333-8333-333333333333' }, dependencies)).toThrow(/authorized/);
      expect(snapshot()).toEqual(before); expect(owner()).toBe(source); expect(phase()).toBe('begun');
      const transferred = dispatchInternalCommand('transfer-handoff', { ...args, target_owner_session_id: target }, dependencies);
      expect(transferred).toEqual({ ...publicTuple, target_owner_session_id: target, phase: 'transferred' }); responses.push(transferred);
      expect(owner()).toBe(target); expect(phase()).toBe('transferred'); expect(snapshot()).toEqual(before);
      expect(() => dispatchInternalCommand('finish-handoff', { ...args, capability_token: 'wrong' }, dependencies)).toThrow(/capability/);
      expect(snapshot()).toEqual(before); expect(owner()).toBe(target); expect(phase()).toBe('transferred');
      const finished = dispatchInternalCommand('finish-handoff', args, dependencies);
      expect(finished).toEqual({ ...publicTuple, target_owner_session_id: target, phase: 'finished' }); responses.push(finished);
      const status = dispatchInternalCommand('handoff-status', { workspace_guid: guid }, dependencies);
      expect(status).toEqual({ ...publicTuple, target_owner_session_id: target, phase: 'finished', present: true }); responses.push(status);
      expect(owner()).toBe(target); expect(phase()).toBe('finished'); expect(snapshot()).toEqual(before);
      for (const response of responses) {
        const encoded = JSON.stringify(response);
        for (const secret of ['capability_token', 'capability_token_hash', 'grant_expires_at', token, tokenHash, expiresAt]) expect(encoded).not.toContain(secret);
      }
    } finally {
      database.close(); rmSync(root, { recursive: true, force: true }); rmSync(databaseDir, { recursive: true, force: true });
    }
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

describe('workspace-manager internal CLI: list-surfaced-orphans command (read-only orphan enumeration)', () => {
  it('exposes list-surfaced-orphans as an internal command alongside the existing set', () => {
    expect(INTERNAL_COMMAND_NAMES).toContain('list-surfaced-orphans');
  });

  it('dispatches the list-surfaced-orphans command to its handler exactly once', () => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand('list-surfaced-orphans', { marker: 'list-surfaced-orphans' }, dependencies);
    expect(dependencies.listSurfacedOrphans).toHaveBeenCalledWith({ marker: 'list-surfaced-orphans' });
    expect(dependencies.listSurfacedOrphans).toHaveBeenCalledTimes(1);
    expect(result).toBeDefined();
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
