import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { initDb, insertPreservedWork, issueHumanIntent } from '../db.js';
import { beginHandoff, currentProcessIdentity, finishHandoff, finishHandoffAfterTarget, processStartEpoch, reapStaleMutations, releaseHandoff, transferHandoff, withAssignmentMutation } from '../handoff.js';
import { createInternalCommandDependencies } from '../cli.js';
import { releaseFinalized, syncWorktreeToTarget, finalizeCommanderLocalCommit } from '../integration.js';
import { WorkspaceService } from '../workspace-service.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const TARGET = '019f7cdf-023c-74e0-9ead-9c155636885d';
const dirs: string[] = [];
const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
const spawnControl = vi.hoisted(() => ({ beforeSpawn: null as null | ((args: readonly string[]) => void) }));
vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>();
  return { ...actual, spawnSync: (cmd: string, args?: readonly string[], opts?: any) => {
    spawnControl.beforeSpawn?.(args ?? []);
    return (actual.spawnSync as any)(cmd, args, opts);
  } };
});
afterEach(() => { spawnControl.beforeSpawn = null; for (const directory of dirs.splice(0)) rmSync(directory, { recursive: true, force: true }); });

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'ironclaude-handoff-fence-')); dirs.push(root);
  git(root, 'init', '--initial-branch=main'); git(root, 'config', 'user.name', 'test'); git(root, 'config', 'user.email', 'test@example.invalid');
  writeFileSync(join(root, 'tracked.txt'), 'base\n'); writeFileSync(join(root, '.gitignore'), '.ironclaude/\n*.db*\nresource.txt\n');
  git(root, 'add', '.'); git(root, 'commit', '-m', 'base');
  const databasePath = join(root, 'handoff.db'); const db = initDb(databasePath); const service = new WorkspaceService(db); const guid = randomUUID();
  const reserved = service.reserveWorkerWorktree({ repositoryPath: root, workspaceGuid: guid, workerId: 'claude-worker' });
  const assignment = service.bindWorkerWorktree({ repositoryPath: root, repositoryIdentity: reserved.repository_identity, workspaceGuid: guid, workerId: 'claude-worker', ownerSessionId: OWNER, expectedLifecycle: 'active', expectedWorktreePath: reserved.worktree_path, expectedBranch: reserved.branch, expectedBaseCommit: reserved.base_commit, expectedCurrentHead: reserved.current_head });
  writeFileSync(join(assignment.worktree_path, 'tracked.txt'), 'staged\n'); git(assignment.worktree_path, 'add', 'tracked.txt'); writeFileSync(join(assignment.worktree_path, 'tracked.txt'), 'unstaged\n');
  writeFileSync(join(assignment.worktree_path, 'untracked.txt'), 'untracked\n'); symlinkSync('untracked.txt', join(assignment.worktree_path, 'shared-link'));
  const input = { repositoryPath: root, repositoryIdentity: assignment.repository_identity, workspaceGuid: guid, workerId: 'claude-worker', sourceOwnerSessionId: OWNER, generation: 1, capabilityToken: 'handoff-capability', expectedWorktreePath: assignment.worktree_path, expectedBranch: assignment.branch, expectedBaseCommit: assignment.base_commit, expectedCurrentHead: assignment.current_head, expectedIntegrationTarget: assignment.integration_target };
  const grant = () => db.prepare(`INSERT INTO workspace_handoffs(workspace_guid,generation,capability_token_hash,grant_expires_at,source_owner_session_id,target_owner_session_id,repository_identity,worker_id,worktree_path,branch,base_commit,current_head,integration_target,phase) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'granted')`).run(guid, 1, createHash('sha256').update(input.capabilityToken).digest('hex'), new Date(Date.now() + 60_000).toISOString(), OWNER, TARGET, assignment.repository_identity, assignment.worker_id, assignment.worktree_path, assignment.branch, assignment.base_commit, assignment.current_head, assignment.integration_target);
  const snapshot = () => ({ head: git(assignment.worktree_path, 'rev-parse', 'HEAD'), branch: git(assignment.worktree_path, 'branch', '--show-current'), index: git(assignment.worktree_path, 'write-tree'), staged: git(assignment.worktree_path, 'diff', '--cached', '--binary'), unstaged: git(assignment.worktree_path, 'diff', '--binary'), status: git(assignment.worktree_path, 'status', '--porcelain=v1', '--untracked-files=all'), tracked: readFileSync(join(assignment.worktree_path, 'tracked.txt'), 'utf8'), untracked: readFileSync(join(assignment.worktree_path, 'untracked.txt'), 'utf8'), link: readlinkSync(join(assignment.worktree_path, 'shared-link')), primaryHead: git(root, 'rev-parse', 'HEAD') });
  return { root, db, databasePath, service, guid, assignment, input, grant, snapshot };
}

type Fixture = ReturnType<typeof fixture>;
function act(f: Fixture, operation: 'begin' | 'finalize' | 'sync'): unknown {
  if (operation === 'begin') return beginHandoff(f.db, f.input);
  if (operation === 'finalize') {
    const index = git(f.assignment.worktree_path, 'write-tree'); const head = git(f.assignment.worktree_path, 'rev-parse', 'HEAD');
    return finalizeCommanderLocalCommit(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER, message: 'test', canonicalBranch: f.assignment.branch, localRef: `refs/heads/${f.assignment.branch}`, stagedTree: index, parentOid: head });
  }
  return syncWorktreeToTarget(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER });
}
function attempt(fn: () => unknown): string {
  try { fn(); return 'ok'; } catch (error) { return error instanceof Error ? error.message : String(error); }
}

describe('assignment handoff', () => {
  it('preserves service binding errors for nonexistent assignments', () => {
    const f = fixture(); const request = { repositoryPath: f.root, workspaceGuid: randomUUID(), ownerSessionId: OWNER };
    for (const invoke of [
      () => f.service.abandonWorkspace(request), () => f.service.cleanupWorkspace(request),
      () => f.service.cleanupReservedAssignment(request),
      () => f.service.usePrimaryCheckout({ ...request, humanChannel: 'test' }),
      () => f.service.returnToManagedWorktree({ ...request, humanChannel: 'test' }),
    ]) expect(invoke).toThrow('Workspace assignment binding does not match repository and provider root');
    expect(() => f.service.reapLeakedAssignment(request)).toThrow('Leaked assignment does not match canonical managed identity for this repository');
    f.db.close();
  });
  it.each([
    ['capabilityToken', 'wrong'], ['generation', 2], ['sourceOwnerSessionId', TARGET], ['repositoryIdentity', 'foreign'], ['workerId', 'foreign'],
    ['expectedWorktreePath', '/tmp/foreign'], ['expectedBranch', 'foreign'], ['expectedBaseCommit', '0'.repeat(40)], ['expectedCurrentHead', '0'.repeat(40)], ['expectedIntegrationTarget', 'foreign'],
  ] as const)('refuses wrong %s without changing source evidence', (field, value) => {
    const f = fixture(); f.grant(); const before = f.snapshot();
    expect(() => beginHandoff(f.db, { ...f.input, [field]: value })).toThrow();
    expect(f.snapshot()).toEqual(before);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('granted');
    f.db.close();
  });

  it.each(['expired', 'invalid-expiry', 'rebase-apply', 'primary', 'integration', 'live-head'] as const)('refuses %s authority/evidence', (kind) => {
    const f = fixture(); f.grant();
    if (kind === 'expired') f.db.prepare("UPDATE workspace_handoffs SET grant_expires_at = '2000-01-01T00:00:00Z'").run();
    if (kind === 'invalid-expiry') f.db.prepare("UPDATE workspace_handoffs SET grant_expires_at = 'invalid'").run();
    if (kind === 'rebase-apply') mkdirSync(resolve(f.assignment.worktree_path, git(f.assignment.worktree_path, 'rev-parse', '--git-path', 'rebase-apply')), { recursive: true });
    if (kind === 'primary') f.db.prepare('INSERT INTO primary_checkout_owners(repository_identity, workspace_guid, owner_session_id) VALUES(?,?,?)').run(f.assignment.repository_identity, f.guid, OWNER);
    if (kind === 'integration') f.db.prepare('INSERT INTO integration_locks(repository_identity,workspace_guid,target_ref,expected_target) VALUES(?,?,?,?)').run(f.assignment.repository_identity, f.guid, 'refs/heads/main', f.assignment.current_head);
    if (kind === 'live-head') git(f.assignment.worktree_path, 'commit', '-m', 'new head');
    const before = f.snapshot(); expect(() => beginHandoff(f.db, f.input)).toThrow(); expect(f.snapshot()).toEqual(before); f.db.close();
  });

  it.each(['rescue', 'cleanup', 'reap', 'primary', 'managed', 'finalize', 'sync', 'remove', 'shared-relink'] as const)('held handoff refuses actual %s before effects', (operation) => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    f.db.prepare("UPDATE workspace_handoffs SET updated_at = '2000-01-01'").run();
    const before = f.snapshot(); const request = { repositoryPath: f.root, workspaceGuid: f.guid, ownerSessionId: OWNER };
    const act = () => {
      switch (operation) {
        case 'rescue': return f.service.abandonWorkspace({ ...request, mode: 'rescue' });
        case 'cleanup': return f.service.cleanupWorkspace(request);
        case 'reap': return f.service.reapLeakedAssignment(request);
        case 'primary': return f.service.usePrimaryCheckout({ ...request, humanChannel: 'test' });
        case 'managed': return f.service.returnToManagedWorktree({ ...request, humanChannel: 'test' });
        case 'finalize': return finalizeCommanderLocalCommit(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER, message: 'test', canonicalBranch: f.assignment.branch, localRef: `refs/heads/${f.assignment.branch}`, stagedTree: before.index, parentOid: before.head });
        case 'sync': return syncWorktreeToTarget(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER });
        case 'remove': return releaseFinalized(f.db, f.root, f.assignment);
        case 'shared-relink': writeFileSync(join(f.root, 'resource.txt'), 'shared'); return f.service.configureSharedResources({ repositoryPath: f.root, entries: ['resource.txt'] });
      }
    };
    expect(act).toThrow(/handoff is pending/); expect(f.snapshot()).toEqual(before);
    expect(existsSync(join(f.assignment.worktree_path, 'resource.txt'))).toBe(false); f.db.close();
  });

  it.each(['rescue', 'sync'] as const)('durable lease excludes another connection during actual %s Git operation', (operation) => {
    const f = fixture(); const other = initDb(f.databasePath); let checked = false;
    if (operation === 'sync') {
      writeFileSync(join(f.root, 'target-only.txt'), 'target advancement\n');
      git(f.root, 'add', 'target-only.txt'); git(f.root, 'commit', '-m', 'advance target');
    }
    spawnControl.beforeSpawn = (args) => {
      if (checked || !(operation === 'rescue' ? args.includes('add') : args.includes('merge'))) return;
      checked = true;
      expect(other.prepare('SELECT operation FROM workspace_mutations WHERE workspace_guid = ?').get(f.guid)).toEqual({ operation: operation === 'rescue' ? 'abandon-workspace' : 'sync-worktree-to-target' });
      expect(() => withAssignmentMutation(other, f.guid, 'rival', () => { throw new Error('rival executed'); })).toThrow(/mutation/);
      f.grant(); expect(() => beginHandoff(other, f.input)).toThrow(/during assignment mutation/);
      other.prepare('DELETE FROM workspace_handoffs WHERE workspace_guid = ?').run(f.guid);
    };
    if (operation === 'rescue') f.service.abandonWorkspace({ repositoryPath: f.root, workspaceGuid: f.guid, ownerSessionId: OWNER, mode: 'rescue' });
    else expect(syncWorktreeToTarget(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER }).state).toBe('fast-forwarded');
    expect(checked).toBe(true); expect(other.prepare('SELECT 1 FROM workspace_mutations WHERE workspace_guid = ?').get(f.guid)).toBeUndefined();
    spawnControl.beforeSpawn = null; other.close(); f.db.close();
  });

  it('records process identity as pid plus integer UTC start epoch', () => {
    const now = Math.floor(Date.now() / 1000);
    const started = processStartEpoch(process.pid);
    expect(started).not.toBeNull();
    expect(started!).toBeLessThanOrEqual(now);
    expect(started!).toBeGreaterThanOrEqual(now - Math.ceil(process.uptime()) - 2);
    expect(currentProcessIdentity()).toBe(`${process.pid}:${started}`);
    expect(processStartEpoch(2147483647)).toBeNull();
  });

  it.each([
    ['dead pid with start time', '2147483647:1'],
    ['dead legacy pid', '2147483647'],
    ['same pid with another start time', `${process.pid}:1`],
  ])('reaps a %s claim so begin, finalize and sync proceed', (_label, identity) => {
    for (const operation of ['begin', 'finalize', 'sync'] as const) {
      const f = fixture(); if (operation === 'begin') f.grant();
      f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'stale', ?)").run(f.guid, identity);
      expect(attempt(() => act(f, operation))).not.toMatch(/already claimed|during assignment mutation/);
      expect(f.db.prepare("SELECT 1 FROM workspace_mutations WHERE nonce = 'stale'").get()).toBeUndefined();
      f.db.close();
    }
  });

  it.each(['live', 'legacy-live', 'unparseable'] as const)('keeps a %s claim blocking begin, finalize and sync', (kind) => {
    const identity = kind === 'live' ? currentProcessIdentity() : kind === 'legacy-live' ? `${process.pid}` : 'dead-process';
    for (const operation of ['begin', 'finalize', 'sync'] as const) {
      const f = fixture(); if (operation === 'begin') f.grant(); const before = f.snapshot();
      f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'held', ?)").run(f.guid, identity);
      expect(attempt(() => act(f, operation))).toMatch(operation === 'begin' ? /during assignment mutation/ : /already claimed/);
      expect(f.snapshot()).toEqual(before);
      expect(f.db.prepare("SELECT 1 FROM workspace_mutations WHERE nonce = 'held'").get()).toBeDefined();
      f.db.close();
    }
  });

  it('reap-stale-mutations removes only proved-dead claims', () => {
    const f = fixture(); const secondGuid = randomUUID();
    f.service.reserveWorkerWorktree({ repositoryPath: f.root, workspaceGuid: secondGuid, workerId: 'second-worker' });
    f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'dead', '2147483647:1')").run(f.guid);
    f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'live', ?)").run(secondGuid, currentProcessIdentity());
    expect(reapStaleMutations(f.db)).toEqual([f.guid]);
    expect(f.db.prepare('SELECT nonce FROM workspace_mutations').all()).toEqual([{ nonce: 'live' }]);
    expect(createInternalCommandDependencies(f.db)['reap-stale-mutations']!({})).toEqual({ protocol_version: 1, reaped: [] });
    f.db.close();
  });

  it('finish-after-target accepts the granted HEAD and a fast-forward of it while the target owns the assignment', () => {
    for (const commitAfterTransfer of [false, true]) {
      const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
      transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
      if (commitAfterTransfer) {
        git(f.assignment.worktree_path, 'commit', '-m', 'target work after transfer');
        expect(() => finishHandoff(f.db, f.input)).toThrow(/live Git evidence/);
      }
      finishHandoffAfterTarget(f.db, f.input);
      expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('finished');
      f.db.close();
    }
  });

  it('finish-after-target refuses a non-descendant HEAD and any owner other than the target', () => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
    git(f.assignment.worktree_path, 'commit', '--amend', '-m', 'rewritten granted head');
    expect(() => finishHandoffAfterTarget(f.db, f.input)).toThrow(/live Git evidence/);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('transferred');
    f.db.close();
    const g = fixture(); g.grant(); beginHandoff(g.db, g.input);
    transferHandoff(g.db, { ...g.input, targetOwnerSessionId: TARGET });
    g.db.prepare('UPDATE assignments SET owner_session_id = ? WHERE workspace_guid = ?').run(OWNER, g.guid);
    expect(() => finishHandoffAfterTarget(g.db, g.input)).toThrow(/not active owner/);
    g.db.close();
    const h = fixture(); h.grant(); beginHandoff(h.db, h.input);
    expect(() => finishHandoffAfterTarget(h.db, h.input)).toThrow(/capability or generation/);
    expect((h.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(h.guid) as { phase: string }).phase).toBe('begun');
    h.db.close();
  });

  it('release-handoff finishes an unclaimed begun hold while the source still owns the assignment', () => {
    const f = fixture(); f.grant(); f.db.prepare('UPDATE workspace_handoffs SET target_owner_session_id = NULL WHERE workspace_guid = ?').run(f.guid);
    beginHandoff(f.db, f.input); const before = f.snapshot();
    const release = { workspaceGuid: f.guid, generation: 1, capabilityToken: f.input.capabilityToken, sourceOwnerSessionId: OWNER };
    expect(() => releaseHandoff(f.db, { ...release, capabilityToken: 'wrong' })).toThrow(/capability/);
    expect(() => releaseHandoff(f.db, { ...release, generation: 2 })).toThrow(/capability or generation/);
    releaseHandoff(f.db, release);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('finished');
    expect(() => releaseHandoff(f.db, release)).not.toThrow();
    expect(f.snapshot()).toEqual(before);
    expect(attempt(() => syncWorktreeToTarget(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER }))).not.toMatch(/handoff is pending/);
    f.db.close();
  });

  it('release-handoff finishes an authorized but untransferred hold and keeps the recorded target', () => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    const release = { workspaceGuid: f.guid, generation: 1, capabilityToken: f.input.capabilityToken, sourceOwnerSessionId: OWNER };
    releaseHandoff(f.db, release);
    expect(f.db.prepare('SELECT phase, target_owner_session_id FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid))
      .toEqual({ phase: 'finished', target_owner_session_id: TARGET });
    expect((f.db.prepare('SELECT owner_session_id FROM assignments WHERE workspace_guid = ?').get(f.guid) as { owner_session_id: string }).owner_session_id).toBe(OWNER);
    f.db.close();
  });

  it('release-handoff refuses once ownership moved to the target', () => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
    const release = { workspaceGuid: f.guid, generation: 1, capabilityToken: f.input.capabilityToken, sourceOwnerSessionId: OWNER };
    expect(() => releaseHandoff(f.db, release)).toThrow(/no longer owns/);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('transferred');
    f.db.close();
  });

  it('release-handoff CLI validates input before touching the database', () => {
    const untouchable = new Proxy({}, { get: () => { throw new Error('database touched'); } });
    const release = createInternalCommandDependencies(untouchable as never)['release-handoff']!;
    expect(() => release({ workspace_guid: randomUUID(), capability_token: 't', source_owner_session_id: OWNER })).toThrow('generation must be a positive integer');
    expect(() => release({ workspace_guid: 'not-a-guid', generation: 1, capability_token: 't', source_owner_session_id: OWNER })).toThrow('Malformed handoff release input');
    expect(() => release({ workspace_guid: randomUUID(), generation: 1, capability_token: 't', source_owner_session_id: OWNER })).toThrow('database touched');
  });

  it('rejects duplicate begin and transfer, unauthorized destination and finish replay', () => {
    const f = fixture(); f.grant(); const before = f.snapshot(); beginHandoff(f.db, f.input);
    issueHumanIntent(f.db, { operation: 'commit', humanChannel: 'test', providerRootSessionId: OWNER, repositoryIdentity: f.assignment.repository_identity, workspaceGuid: f.guid, expectedEvidence: {} });
    insertPreservedWork(f.db, { workspaceGuid: f.guid, repositoryIdentity: f.assignment.repository_identity, ownerSessionId: OWNER, kind: 'pending-push', payload: '{"candidate":"keep"}' });
    expect(() => beginHandoff(f.db, f.input)).toThrow(/capability/);
    expect(() => transferHandoff(f.db, { ...f.input, targetOwnerSessionId: randomUUID() })).toThrow(/authorized/);
    transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
    expect((f.db.prepare('SELECT consumed_at FROM human_intents WHERE workspace_guid = ?').get(f.guid) as { consumed_at: string | null }).consumed_at).not.toBeNull();
    expect(f.db.prepare('SELECT owner_session_id, payload FROM preserved_work WHERE workspace_guid = ?').get(f.guid)).toEqual({ owner_session_id: TARGET, payload: '{"candidate":"keep"}' });
    expect(() => transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET })).toThrow(/capability/);
    finishHandoff(f.db, f.input); expect(() => finishHandoff(f.db, f.input)).toThrow(/capability/);
    expect(f.snapshot()).toEqual(before); f.db.close();
  });

  it('transfers an active dirty worktree without altering its Git or filesystem evidence', () => {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-handoff-')); dirs.push(root);
    git(root, 'init', '--initial-branch=main'); git(root, 'config', 'user.name', 'test'); git(root, 'config', 'user.email', 'test@example.invalid');
    writeFileSync(join(root, 'tracked.txt'), 'base\n'); git(root, 'add', '.'); git(root, 'commit', '-m', 'base');
    const db = initDb(join(root, 'handoff.db')); const service = new WorkspaceService(db); const guid = randomUUID();
    const reserved = service.reserveWorkerWorktree({ repositoryPath: root, workspaceGuid: guid, workerId: 'claude-worker' });
    const assignment = service.bindWorkerWorktree({ repositoryPath: root, repositoryIdentity: reserved.repository_identity, workspaceGuid: guid, workerId: 'claude-worker', ownerSessionId: OWNER, expectedLifecycle: 'active', expectedWorktreePath: reserved.worktree_path, expectedBranch: reserved.branch, expectedBaseCommit: reserved.base_commit, expectedCurrentHead: reserved.current_head });
    writeFileSync(join(assignment.worktree_path, 'tracked.txt'), 'staged\n'); git(assignment.worktree_path, 'add', 'tracked.txt'); writeFileSync(join(assignment.worktree_path, 'tracked.txt'), 'unstaged\n'); writeFileSync(join(assignment.worktree_path, 'untracked.txt'), 'untracked\n'); symlinkSync('untracked.txt', join(assignment.worktree_path, 'shared-link'));
    const snapshot = { head: git(assignment.worktree_path, 'rev-parse', 'HEAD'), branch: git(assignment.worktree_path, 'branch', '--show-current'), index: git(assignment.worktree_path, 'write-tree'), staged: git(assignment.worktree_path, 'diff', '--cached', '--binary'), unstaged: git(assignment.worktree_path, 'diff', '--binary'), status: git(assignment.worktree_path, 'status', '--porcelain=v1', '--untracked-files=all'), tracked: readFileSync(join(assignment.worktree_path, 'tracked.txt'), 'utf8'), link: readlinkSync(join(assignment.worktree_path, 'shared-link')), isLink: lstatSync(join(assignment.worktree_path, 'shared-link')).isSymbolicLink() };
    const token = 'handoff-capability';
    db.prepare(`INSERT INTO workspace_handoffs(workspace_guid,generation,capability_token_hash,grant_expires_at,source_owner_session_id,target_owner_session_id,repository_identity,worker_id,worktree_path,branch,base_commit,current_head,integration_target,phase) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'granted')`).run(guid, 1, createHash('sha256').update(token).digest('hex'), new Date(Date.now() + 60_000).toISOString(), OWNER, TARGET, assignment.repository_identity, assignment.worker_id, assignment.worktree_path, assignment.branch, assignment.base_commit, assignment.current_head, assignment.integration_target);
    const input = { repositoryPath: root, repositoryIdentity: assignment.repository_identity, workspaceGuid: guid, workerId: 'claude-worker', sourceOwnerSessionId: OWNER, generation: 1, capabilityToken: token, expectedWorktreePath: assignment.worktree_path, expectedBranch: assignment.branch, expectedBaseCommit: assignment.base_commit, expectedCurrentHead: assignment.current_head, expectedIntegrationTarget: assignment.integration_target };
    expect(() => beginHandoff(db, { ...input, capabilityToken: 'wrong' })).toThrow(/capability/);
    beginHandoff(db, input); transferHandoff(db, { ...input, targetOwnerSessionId: TARGET });
    expect(() => service.bindWorkerWorktree({ repositoryPath: root, repositoryIdentity: assignment.repository_identity, workspaceGuid: guid, workerId: 'claude-worker', ownerSessionId: OWNER, expectedLifecycle: 'active', expectedWorktreePath: assignment.worktree_path, expectedBranch: assignment.branch, expectedBaseCommit: assignment.base_commit, expectedCurrentHead: assignment.current_head })).toThrow(/already bound/);
    const after = { head: git(assignment.worktree_path, 'rev-parse', 'HEAD'), branch: git(assignment.worktree_path, 'branch', '--show-current'), index: git(assignment.worktree_path, 'write-tree'), staged: git(assignment.worktree_path, 'diff', '--cached', '--binary'), unstaged: git(assignment.worktree_path, 'diff', '--binary'), status: git(assignment.worktree_path, 'status', '--porcelain=v1', '--untracked-files=all'), tracked: readFileSync(join(assignment.worktree_path, 'tracked.txt'), 'utf8'), link: readlinkSync(join(assignment.worktree_path, 'shared-link')), isLink: lstatSync(join(assignment.worktree_path, 'shared-link')).isSymbolicLink() };
    expect(after).toEqual(snapshot); expect((db.prepare('SELECT owner_session_id FROM assignments WHERE workspace_guid = ?').get(guid) as { owner_session_id: string }).owner_session_id).toBe(TARGET); finishHandoff(db, input);
    expect((db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(guid) as { phase: string }).phase).toBe('finished');
  });
});
