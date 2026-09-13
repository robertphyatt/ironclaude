import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createAssignment, createHumanIntent, initDb, recordIntegration } from '../db.js';
import { worktreeIsClean } from '../git.js';
import { WorkspaceService } from '../workspace-service.js';

const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER_OWNER = '019f7cdf-023c-74e0-9ead-9c155636885d';
const CLI_PATH = fileURLToPath(new URL('../../dist/cli.js', import.meta.url));

interface CliResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

function runCli(dbPath: string, command: string, payload: Record<string, unknown>): Promise<CliResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI_PATH, command, JSON.stringify(payload)], {
      env: { ...process.env, WORKSPACE_MANAGER_DB_PATH: dbPath },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk: string) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk: string) => { stderr += chunk; });
    child.once('error', reject);
    child.once('close', (code) => resolve({ code, stdout, stderr }));
  });
}

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

describe('WorkspaceService real-Git lifecycle', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function repository(): string {
    const directory = mkdtempSync(join(tmpdir(), 'ironclaude-worktree-service-'));
    directories.push(directory);
    git(directory, 'init', '--initial-branch=main');
    git(directory, 'config', 'user.name', 'Workspace Test');
    git(directory, 'config', 'user.email', 'workspace-test@example.invalid');
    writeFileSync(join(directory, 'README.md'), 'initial\n');
    git(directory, 'add', 'README.md');
    git(directory, 'commit', '-m', 'initial');
    return directory;
  }

  function service(root: string): WorkspaceService {
    const databaseDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-workspace-manager-db-'));
    directories.push(databaseDirectory);
    return new WorkspaceService(initDb(join(databaseDirectory, `${root.split('/').at(-1)}.db`)));
  }

  /** The Git common dir is the durable repository identity; config lives here, outside every working tree. */
  function commonDir(root: string): string {
    return realpathSync(join(root, '.git'));
  }

  function ignoreResources(root: string, ...patterns: string[]): void {
    writeFileSync(join(root, '.gitignore'), patterns.map((pattern) => `${pattern}\n`).join(''));
    git(root, 'add', '.gitignore');
    git(root, 'commit', '-m', 'ignore resources');
  }

  /** Writes the shared-resource config to <commonDir>/info, never inside a working tree. */
  function writeSharedResourceConfig(root: string, ...entries: string[]): void {
    const infoDirectory = join(commonDir(root), 'info');
    mkdirSync(infoDirectory, { recursive: true });
    writeFileSync(join(infoDirectory, 'worktree-shared-resources'), entries.map((entry) => `${entry}\n`).join(''));
  }

  function seedDirectory(root: string, name: string, file: string, content: string): void {
    mkdirSync(join(root, name), { recursive: true });
    writeFileSync(join(root, name, file), content);
  }

  it('creates a GUID-named private branch and independently indexed clean worktree', () => {
    const root = repository();
    const manager = service(root);
    const before = git(root, 'status', '--porcelain=v1', '--untracked-files=all');
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(assignment.workspace_guid).toBe(OWNER);
    expect(assignment.worktree_path).toBe(join(realpathSync(root), '.ironclaude', 'worktrees', OWNER));
    expect(assignment.branch).toBe(`ironclaude/${OWNER}`);
    expect(git(assignment.worktree_path, 'status', '--porcelain')).toBe('');
    expect(git(root, 'rev-parse', '--git-path', 'index')).not.toBe(git(assignment.worktree_path, 'rev-parse', '--git-path', 'index'));
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe(before);
  });

  it('does not modify dirty primary-checkout bytes while allocating', () => {
    const root = repository();
    const dirty = Buffer.from([0, 255, 10, 13, 42]);
    writeFileSync(join(root, 'dirty.bin'), dirty);
    const before = git(root, 'status', '--porcelain=v1', '--untracked-files=all');

    service(root).ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(readFileSync(join(root, 'dirty.bin'))).toEqual(dirty);
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe(before);
  });

  it('preserves existing common-dir exclusions and adds one managed-root entry across repeated allocations', () => {
    const root = repository();
    const excludePath = join(root, '.git', 'info', 'exclude');
    const existing = '# operator exclusion\n*.private\n';
    writeFileSync(excludePath, existing);
    const manager = service(root);

    manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OTHER_OWNER });

    const exclusion = readFileSync(excludePath, 'utf8');
    expect(exclusion.startsWith(existing)).toBe(true);
    expect(exclusion.split(/\r?\n/).filter((line) => line === '/.ironclaude/worktrees/')).toHaveLength(1);
  });

  it('uses an explicit Commander allocation over a provider-native root GUID and keeps it bound on resume', () => {
    const root = repository();
    const manager = service(root);
    const preallocated = '33333333-3333-4333-8333-333333333333';
    const assignment = manager.ensureSessionWorktree({
      repositoryPath: root, ownerSessionId: OWNER, workspaceGuid: preallocated,
    });

    expect(assignment).toMatchObject({
      workspace_guid: preallocated,
      owner_session_id: OWNER,
      worktree_path: join(realpathSync(root), '.ironclaude', 'worktrees', preallocated),
      branch: `ironclaude/${preallocated}`,
    });
    expect(manager.ensureSessionWorktree({
      repositoryPath: root, ownerSessionId: OWNER, workspaceGuid: preallocated,
    }).workspace_guid).toBe(preallocated);
    expect(() => manager.ensureSessionWorktree({
      repositoryPath: root, ownerSessionId: OWNER, workspaceGuid: 'not-a-guid',
    })).toThrow('workspaceGuid must be a UUID');
  });

  it('resolves the same repository identity from a subdirectory as from the root', () => {
    const root = repository();
    // `--git-common-dir` is relative to the directory git ran in. Resolving it
    // against the worktree top instead made a subdirectory point at a sibling
    // .git — and sessions routinely run from a subdirectory.
    const nested = join(root, 'a', 'b');
    execFileSync('mkdir', ['-p', nested]);
    const database = initDb(join(root, 'identity.db'));
    const manager = new WorkspaceService(database);

    const fromRoot = manager.ensureSessionWorktree({
      repositoryPath: root,
      ownerSessionId: OWNER,
    });
    const fromNested = manager.ensureSessionWorktree({
      repositoryPath: nested,
      ownerSessionId: OWNER,
    });

    expect(fromNested.repository_identity).toBe(fromRoot.repository_identity);
    expect(fromNested.workspace_guid).toBe(fromRoot.workspace_guid);
  });

  it('derives the integration target from the primary branch when the caller omits it', () => {
    const root = repository();
    // Callers hardcoded "main". On a repository whose primary branch is named
    // anything else, that produced an assignment pointing at a ref that does
    // not exist, and the failure only surfaced at finalization.
    git(root, 'branch', '-m', 'trunk');
    const database = initDb(join(root, 'derived-target.db'));
    const manager = new WorkspaceService(database);

    const reserved = manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid: '66666666-6666-4666-8666-666666666666',
      workerId: 'worker-trunk',
    });
    expect(reserved.integration_target).toBe('trunk');

    const owned = manager.ensureSessionWorktree({
      repositoryPath: root,
      ownerSessionId: OWNER,
    });
    expect(owned.integration_target).toBe('trunk');
  });

  it('honours an explicit integration target over the derived primary branch', () => {
    const root = repository();
    git(root, 'branch', '-m', 'trunk');
    const database = initDb(join(root, 'explicit-target.db'));
    const manager = new WorkspaceService(database);

    expect(manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid: '77777777-7777-4777-8777-777777777777',
      workerId: 'worker-explicit',
      integrationTarget: 'release/2026-08',
    }).integration_target).toBe('release/2026-08');
  });

  it('reserves an unbound Commander worktree and binds the exact provider owner idempotently', () => {
    const root = repository();
    const database = initDb(join(root, 'worker-reservation.db'));
    const manager = new WorkspaceService(database);
    const workspaceGuid = '44444444-4444-4444-8444-444444444444';
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid,
      workerId: 'worker-17',
      integrationTarget: 'main',
    });

    expect(assignment).toMatchObject({
      workspace_guid: workspaceGuid,
      owner_session_id: null,
      worker_id: 'worker-17',
      lifecycle_status: 'active',
      worktree_path: join(realpathSync(root), '.ironclaude', 'worktrees', workspaceGuid),
      branch: `ironclaude/${workspaceGuid}`,
    });
    expect(manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid,
      workerId: 'worker-17',
      integrationTarget: 'main',
    })).toMatchObject({ workspace_guid: workspaceGuid, owner_session_id: null });
    expect(() => manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid,
      workerId: 'worker-other',
      integrationTarget: 'main',
    })).toThrow('does not match requested allocation');
    const bindInput = {
      repositoryPath: root,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid,
      workerId: 'worker-17',
      ownerSessionId: OWNER,
      expectedLifecycle: 'active' as const,
      expectedWorktreePath: assignment.worktree_path,
      expectedBranch: assignment.branch,
      expectedBaseCommit: assignment.base_commit,
      expectedCurrentHead: assignment.current_head,
    };
    expect(manager.bindWorkerWorktree(bindInput).owner_session_id).toBe(OWNER);
    expect(manager.bindWorkerWorktree(bindInput).owner_session_id).toBe(OWNER);
    expect(() => manager.bindWorkerWorktree({ ...bindInput, ownerSessionId: OTHER_OWNER }))
      .toThrow('already bound');
  });

  it('rejects changed reservation evidence before first owner binding', () => {
    const root = repository();
    const manager = service(root);
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid: '55555555-5555-4555-8555-555555555555',
      workerId: 'worker-evidence',
      integrationTarget: 'main',
    });
    const exact = {
      repositoryPath: root,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      workerId: 'worker-evidence',
      ownerSessionId: OWNER,
      expectedLifecycle: 'active' as const,
      expectedWorktreePath: assignment.worktree_path,
      expectedBranch: assignment.branch,
      expectedBaseCommit: assignment.base_commit,
      expectedCurrentHead: assignment.current_head,
    };

    const mismatches = [
      { repositoryIdentity: `${assignment.repository_identity}-other` },
      { workerId: 'worker-other' },
      { expectedLifecycle: 'materialized' as const },
      { expectedWorktreePath: `${assignment.worktree_path}-other` },
      { expectedBranch: `${assignment.branch}-other` },
      { expectedBaseCommit: '0'.repeat(40) },
      { expectedCurrentHead: '1'.repeat(40) },
    ];
    for (const mismatch of mismatches) {
      expect(() => manager.bindWorkerWorktree({ ...exact, ...mismatch }))
        .toThrow('reservation evidence');
    }
    expect(manager.bindWorkerWorktree(exact).owner_session_id).toBe(OWNER);
  });

  it('rejects binding when the recorded active worktree no longer matches Git materialization', () => {
    const root = repository();
    const manager = service(root);
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid: '66666666-6666-4666-8666-666666666666',
      workerId: 'worker-git',
      integrationTarget: 'main',
    });
    git(root, 'worktree', 'remove', assignment.worktree_path);

    expect(() => manager.bindWorkerWorktree({
      repositoryPath: root,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      workerId: 'worker-git',
      ownerSessionId: OWNER,
      expectedLifecycle: 'active',
      expectedWorktreePath: assignment.worktree_path,
      expectedBranch: assignment.branch,
      expectedBaseCommit: assignment.base_commit,
      expectedCurrentHead: assignment.current_head,
    })).toThrow('Git identity');
  });

  it('rejects binding when materialized Git HEAD drifted from the durable current head', () => {
    const root = repository();
    const manager = service(root);
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root,
      workspaceGuid: '77777777-7777-4777-8777-777777777777',
      workerId: 'worker-head',
      integrationTarget: 'main',
    });
    writeFileSync(join(assignment.worktree_path, 'drift.txt'), 'drift\n');
    git(assignment.worktree_path, 'add', 'drift.txt');
    git(assignment.worktree_path, 'commit', '-m', 'drift');

    expect(() => manager.bindWorkerWorktree({
      repositoryPath: root,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      workerId: 'worker-head',
      ownerSessionId: OWNER,
      expectedLifecycle: 'active',
      expectedWorktreePath: assignment.worktree_path,
      expectedBranch: assignment.branch,
      expectedBaseCommit: assignment.base_commit,
      expectedCurrentHead: assignment.current_head,
    })).toThrow('Git HEAD');
  });

  it('serializes two-process identical allocation and returns one active assignment twice', async () => {
    const root = repository();
    const dbPath = join(root, 'concurrent-identical.db');
    initDb(dbPath).close();
    const payload = {
      repository_path: root,
      workspace_guid: '88888888-8888-4888-8888-888888888888',
      worker_id: 'worker-concurrent-same',
      integration_target: 'main',
    };

    const results = await Promise.all([
      runCli(dbPath, 'allocate', payload),
      runCli(dbPath, 'allocate', payload),
    ]);
    expect(results.map((result) => result.code)).toEqual([0, 0]);
    const assignments = results.map((result) => JSON.parse(result.stdout));
    expect(assignments).toEqual([
      expect.objectContaining({ workspace_guid: payload.workspace_guid, lifecycle_status: 'active' }),
      expect.objectContaining({ workspace_guid: payload.workspace_guid, lifecycle_status: 'active' }),
    ]);
    expect(git(root, 'worktree', 'list', '--porcelain').match(/ironclaude\/88888888/g)).toHaveLength(1);
  });

  it('serializes two-process different-GUID allocation for one worker', async () => {
    const root = repository();
    const dbPath = join(root, 'concurrent-different.db');
    initDb(dbPath).close();
    const common = {
      repository_path: root,
      worker_id: 'worker-concurrent-different',
      integration_target: 'main',
    };
    const results = await Promise.all([
      runCli(dbPath, 'allocate', { ...common, workspace_guid: '99999999-9999-4999-8999-999999999999' }),
      runCli(dbPath, 'allocate', { ...common, workspace_guid: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' }),
    ]);

    expect(results.filter((result) => result.code === 0)).toHaveLength(1);
    expect(results.filter((result) => result.code !== 0)).toHaveLength(1);
    expect(results.find((result) => result.code !== 0)?.stderr)
      .toContain('already reserved to a different managed workspace');
    const database = initDb(dbPath);
    expect(database.prepare(`
      SELECT workspace_guid FROM assignments
      WHERE worker_id = ? AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
    `).all(common.worker_id)).toHaveLength(1);
    database.close();
    expect(git(root, 'worktree', 'list', '--porcelain').match(/branch refs\/heads\/ironclaude\//g)).toHaveLength(1);
  });

  it('returns only an exact durable Git identity on matching resume and denies a different provider root', () => {
    const root = repository();
    const database = initDb(join(root, 'resume.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER })).toMatchObject({
      workspace_guid: assignment.workspace_guid,
      worktree_path: assignment.worktree_path,
    });
    expect(() => manager.getWorkspaceAssignment({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OTHER_OWNER,
    })).toThrow('assignment binding');
    database.prepare('UPDATE assignments SET branch = ? WHERE workspace_guid = ?')
      .run(`ironclaude/${randomUUID()}`, assignment.workspace_guid);
    expect(() => manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER }))
      .toThrow('canonical managed identity');
  });

  it('reads assigned or explicit unassigned status by provider root without mutation', () => {
    const root = repository();
    const database = initDb(join(root, 'status.db'));
    const manager = new WorkspaceService(database);
    const before = database.prepare('SELECT COUNT(*) AS count FROM assignments').get() as { count: number };

    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OWNER })).toEqual({
      status: 'unassigned',
      repositoryIdentity: git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir'),
      ownerSessionId: OWNER,
    });
    expect((database.prepare('SELECT COUNT(*) AS count FROM assignments').get() as { count: number }).count)
      .toBe(before.count);

    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OWNER })).toEqual({
      status: 'assigned',
      assignment,
      effectiveRoot: 'managed',
      primaryOwnedByThisSession: false,
      currentHead: assignment.current_head,
    });
    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OTHER_OWNER }).status)
      .toBe('unassigned');
  });

  it('reports the managed effective root with no primary ownership when this session does not own the primary checkout', () => {
    const root = repository();
    const database = initDb(join(root, 'status-effective-root.db'));
    const manager = new WorkspaceService(database);
    manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OWNER })).toMatchObject({
      status: 'assigned',
      effectiveRoot: 'managed',
      primaryOwnedByThisSession: false,
    });
  });

  it('reports the primary effective root and ownership when the three-part owner row matches this session', () => {
    const root = repository();
    const database = initDb(join(root, 'status-effective-root-owned.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(assignment.repository_identity, assignment.workspace_guid, OWNER);

    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OWNER })).toMatchObject({
      status: 'assigned',
      effectiveRoot: 'primary',
      primaryOwnedByThisSession: true,
    });
  });

  it('does not report primary ownership for a repository-only match when the owner row belongs to a different session', () => {
    const root = repository();
    const database = initDb(join(root, 'status-effective-root-other-session.db'));
    const manager = new WorkspaceService(database);
    manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const otherAssignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OTHER_OWNER });
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(otherAssignment.repository_identity, otherAssignment.workspace_guid, OTHER_OWNER);

    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OWNER })).toMatchObject({
      status: 'assigned',
      effectiveRoot: 'managed',
      primaryOwnedByThisSession: false,
    });
  });

  it('reads the live worktree HEAD for currentHead instead of the cached column after a fast-forward', () => {
    const root = repository();
    const database = initDb(join(root, 'status-current-head.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    writeFileSync(join(assignment.worktree_path, 'note.txt'), 'progress\n');
    git(assignment.worktree_path, 'add', 'note.txt');
    git(assignment.worktree_path, 'commit', '-m', 'advance');
    const liveHead = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    expect(liveHead).not.toBe(assignment.current_head);

    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OWNER })).toMatchObject({
      status: 'assigned',
      currentHead: liveHead,
    });
  });

  it('rejects AI checkout switching without a matching, single-use human intent, including cross-session replay', () => {
    const root = repository();
    const database = initDb(join(root, 'intent.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const evidence = { primaryHead: git(root, 'rev-parse', 'HEAD') };

    expect(() => manager.usePrimaryCheckout({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: 'missing',
    })).toThrow('matching human intent');

    const intent = createHumanIntent(database, {
      operation: 'use-primary-checkout', humanChannel: 'codex-user-prompt', providerRootSessionId: OWNER,
      repositoryIdentity: assignment.repository_identity, workspaceGuid: assignment.workspace_guid,
      expectedEvidence: evidence, expiresAt: '2030-01-01T00:00:00.000Z', nonce: 'primary-once',
    });
    expect(() => manager.usePrimaryCheckout({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OTHER_OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: intent.nonce,
    })).toThrow('assignment binding');
    expect(manager.usePrimaryCheckout({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: intent.nonce,
    }).primaryCheckoutPath).toBe(realpathSync(root));
    expect(() => manager.usePrimaryCheckout({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: intent.nonce,
    })).toThrow('matching human intent');

    const returnIntent = createHumanIntent(database, {
      operation: 'return-to-managed-worktree', humanChannel: 'codex-user-prompt', providerRootSessionId: OWNER,
      repositoryIdentity: assignment.repository_identity, workspaceGuid: assignment.workspace_guid,
      expectedEvidence: evidence, expiresAt: '2030-01-01T00:00:00.000Z', nonce: 'return-once',
    });
    database.prepare('UPDATE assignments SET branch = ? WHERE workspace_guid = ?')
      .run(`ironclaude/${randomUUID()}`, assignment.workspace_guid);
    expect(() => manager.returnToManagedWorktree({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: returnIntent.nonce,
    })).toThrow('canonical managed identity');
    expect(database.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?')
      .get(assignment.repository_identity)).toBeTruthy();
    database.prepare('UPDATE assignments SET branch = ? WHERE workspace_guid = ?')
      .run(assignment.branch, assignment.workspace_guid);
    expect(() => manager.returnToManagedWorktree({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OTHER_OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: returnIntent.nonce,
    })).toThrow('assignment binding');
    expect(database.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?')
      .get(assignment.repository_identity)).toBeTruthy();
    expect(manager.returnToManagedWorktree({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: returnIntent.nonce,
    }).managedWorktreePath).toBe(assignment.worktree_path);
    expect(database.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?')
      .get(assignment.repository_identity)).toBeUndefined();
    expect(() => manager.returnToManagedWorktree({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt', expectedEvidence: evidence, nonce: returnIntent.nonce,
    })).toThrow('matching human intent');
  });

  it('issues and consumes checkout-switch intents without public nonce or evidence fields', () => {
    const root = repository();
    const database = initDb(join(root, 'server-held-intent.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    const primaryReceipt = manager.issueCheckoutHumanIntent({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'use-primary-checkout',
    });
    expect(primaryReceipt).toMatchObject({ issued: true, operation: 'use-primary-checkout' });
    expect(primaryReceipt).not.toHaveProperty('nonce');
    expect(manager.usePrimaryCheckout({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
    }).primaryCheckoutPath).toBe(realpathSync(root));
    expect(() => manager.usePrimaryCheckout({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
    })).toThrow('already owned');

    manager.issueCheckoutHumanIntent({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
      operation: 'return-to-managed-worktree',
    });
    expect(manager.returnToManagedWorktree({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
    }).managedWorktreePath).toBe(assignment.worktree_path);
    expect(() => manager.returnToManagedWorktree({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      humanChannel: 'codex-user-prompt',
    })).toThrow('ownership');
  });

  it('refuses to abandon a workspace that still holds the primary checkout', () => {
    const root = repository();
    const database = initDb(join(root, 'abandon-guard.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(assignment.repository_identity, assignment.workspace_guid, OWNER);

    expect(() => manager.abandonWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    })).toThrow('Return to the managed worktree before abandoning while holding the primary checkout');
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
      .toMatchObject({ lifecycle_status: 'active' });

    // Once ownership is released, abandonment proceeds.
    database.prepare('DELETE FROM primary_checkout_owners WHERE repository_identity = ?').run(assignment.repository_identity);
    expect(manager.abandonWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    }).lifecycle_status).toBe('abandoned');
  });

  it('preserves abandoned worktrees without reachable recovery proof, then deletes verified branch before cleaning', () => {
    const root = repository();
    const database = initDb(join(root, 'cleanup.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'recovery.txt'), 'recoverable\n');
    git(assignment.worktree_path, 'add', 'recovery.txt');
    git(assignment.worktree_path, 'commit', '-m', 'recoverable work');
    const recoveryHead = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER });

    expect(() => manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    })).toThrow('recovery evidence');
    expect(existsSync(assignment.worktree_path)).toBe(true);

    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    git(root, 'update-ref', recoveryRef, recoveryHead);
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?')
      .run(recoveryRef, assignment.workspace_guid);
    writeFileSync(join(assignment.worktree_path, 'dirty.txt'), 'do not delete\n');
    expect(() => manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    })).toThrow('dirty');
    expect(existsSync(assignment.worktree_path)).toBe(true);
    rmSync(join(assignment.worktree_path, 'dirty.txt'));

    git(root, 'update-ref', recoveryRef, git(root, 'rev-parse', 'HEAD'));
    expect(() => manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    })).toThrow('recovery evidence');
    expect(existsSync(assignment.worktree_path)).toBe(true);

    git(root, 'update-ref', recoveryRef, recoveryHead);
    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');

    const unknownGuid = randomUUID();
    const unknownPath = join(root, '.ironclaude', 'worktrees', unknownGuid);
    git(root, 'worktree', 'add', '-b', `ironclaude/${unknownGuid}`, unknownPath, 'HEAD');
    expect(manager.reconcileRepository(root).ambiguousWorktreePaths).toEqual([realpathSync(unknownPath)]);
    expect(git(unknownPath, 'status', '--porcelain')).toBe('');
  });

  it('rescue-abandon commits uncommitted work onto the worker branch, reclaims the dir, and preserves the branch', () => {
    const root = repository();
    const database = initDb(join(root, 'rescue.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const branch = assignment.branch;
    // Uncommitted, untracked content: not reachable from main, not committed anywhere yet.
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    expect(worktreeIsClean(assignment.worktree_path)).toBe(false);
    expect(git(root, 'log', 'main', '--oneline', '--', 'unintegrated.txt')).toBe('');

    const abandoned = manager.abandonWorkspace({
      repositoryPath: root,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: OWNER,
      mode: 'rescue',
    });

    expect(abandoned.lifecycle_status).toBe('abandoned');
    // Falsifier: without rescue, this content is unreachable once the dir is gone.
    expect(existsSync(assignment.worktree_path)).toBe(false);
    // The branch must SURVIVE reclamation, or the rescued commit would be stranded.
    expect(git(root, 'branch', '--list', branch)).not.toBe('');
    const branchTip = git(root, 'rev-parse', branch);
    expect(git(root, 'cat-file', '-p', `${branchTip}:unintegrated.txt`)).toBe('rescue me');
    // recovery_ref records the durable RECOVERY REF NAME, which resolves to the exact
    // rescued commit — the anchor survives even if the branch is later deleted.
    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    expect(abandoned.recovery_ref).toBe(recoveryRef);
    expect(git(root, 'rev-parse', recoveryRef)).toBe(branchTip);
    // The rescue-commit must never land on main.
    expect(git(root, 'log', 'main', '--oneline', '--', 'unintegrated.txt')).toBe('');
  });

  it('preserves integrated-looking work until its exact recorded target contains the recorded commit', () => {
    const root = repository();
    const database = initDb(join(root, 'integrated.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const targetRef = 'refs/heads/main';
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ? WHERE workspace_guid = ?
    `).run(integratedCommit, assignment.workspace_guid);
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef,
      integratedCommit,
    });

    expect(() => manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    })).toThrow('integration evidence');
    expect(existsSync(assignment.worktree_path)).toBe(true);

    git(root, 'merge', '--ff-only', integratedCommit);
    expect(manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');
  });

  it('carries a push-pending obligation into preserved_work when tombstoning an integrated worktree', () => {
    const root = repository();
    const database = initDb(join(root, 'push-pending-tombstone.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const disposition = JSON.stringify({
      phase: 'push-pending', candidateCommit: integratedCommit, frozenCommit: integratedCommit,
      remoteName: 'origin', remoteUrl: 'file:///unused-in-tombstone-carry', destinationRef: 'refs/heads/main',
      expectedRemoteOldOid: null,
    });
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ? WHERE workspace_guid = ?
    `).run(integratedCommit, integratedCommit, disposition, assignment.workspace_guid);
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef: 'refs/heads/main',
      integratedCommit,
    });
    git(root, 'merge', '--ff-only', integratedCommit);

    const cleaned = manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    });

    expect(cleaned.lifecycle_status).toBe('cleaned'); // reclaimed, not refused
    expect(existsSync(assignment.worktree_path)).toBe(false); // worktree removed
    expect(git(root, 'branch', '--list', assignment.branch)).toBe(''); // branch gone
    const preserved = database.prepare(
      "SELECT payload FROM preserved_work WHERE workspace_guid = ? AND kind = 'pending-push' AND resolved_at IS NULL",
    ).get(assignment.workspace_guid) as { payload: string } | undefined;
    expect(preserved).toBeDefined();
    const payload = JSON.parse(preserved!.payload) as { candidateCommit: string; destinationRef: string };
    expect(payload.candidateCommit).toBe(integratedCommit);
    expect(payload.destinationRef).toBe('refs/heads/main');
  });

  it('reapLeakedAssignment carries a push-pending dead-worker obligation (integrated + gone worktree)', () => {
    const root = repository();
    const database = initDb(join(root, 'reap-push-pending-gone.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const disposition = JSON.stringify({
      phase: 'push-pending', candidateCommit: integratedCommit, frozenCommit: integratedCommit,
      remoteName: 'origin', remoteUrl: 'file:///unused', destinationRef: 'refs/heads/main',
      expectedRemoteOldOid: null,
    });
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ? WHERE workspace_guid = ?
    `).run(integratedCommit, integratedCommit, disposition, assignment.workspace_guid);
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid, repositoryIdentity: assignment.repository_identity,
      targetRef: 'refs/heads/main', integratedCommit,
    });
    git(root, 'merge', '--ff-only', integratedCommit);
    // Worker died mid-teardown: worktree removed, owner cleared (leaked).
    git(root, 'worktree', 'remove', '--force', assignment.worktree_path);
    database.prepare("UPDATE assignments SET owner_session_id = NULL WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);

    const reaped = manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid: assignment.workspace_guid });

    expect(reaped.lifecycle_status).toBe('cleaned'); // reclaimed, not wedged
    const preserved = database.prepare(
      "SELECT payload FROM preserved_work WHERE workspace_guid = ? AND kind = 'pending-push' AND resolved_at IS NULL",
    ).get(assignment.workspace_guid) as { payload: string } | undefined;
    expect(preserved).toBeDefined();
    expect((JSON.parse(preserved!.payload) as { candidateCommit: string }).candidateCommit).toBe(integratedCommit);
  });

  it('cleans a proven integrated worktree while another session owns the primary checkout', () => {
    const root = repository();
    const database = initDb(join(root, 'integrated-under-other-owner.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const other = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OTHER_OWNER });
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const targetRef = 'refs/heads/main';
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ? WHERE workspace_guid = ?
    `).run(integratedCommit, assignment.workspace_guid);
    recordIntegration(database, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef,
      integratedCommit,
    });
    git(root, 'merge', '--ff-only', integratedCommit);
    database.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(other.repository_identity, other.workspace_guid, OTHER_OWNER);
    writeFileSync(join(root, 'README.md'), 'operator staged\n');
    git(root, 'add', 'README.md');
    writeFileSync(join(root, 'README.md'), 'operator unstaged\n');
    const primaryHash = git(root, 'hash-object', join(root, 'README.md'));
    const primaryIndex = git(root, 'ls-files', '-s', '--', 'README.md');
    const primaryBefore = git(root, 'status', '--porcelain=v1', '--untracked-files=all');

    expect(manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    }).lifecycle_status).toBe('cleaned');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');
    expect(git(root, 'hash-object', join(root, 'README.md'))).toBe(primaryHash);
    expect(git(root, 'ls-files', '-s', '--', 'README.md')).toBe(primaryIndex);
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe(primaryBefore);
    expect(database.prepare(
      'SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?',
    ).get(assignment.repository_identity)).toMatchObject({
      workspace_guid: other.workspace_guid,
      owner_session_id: OTHER_OWNER,
    });
  });

  it('links configured shared resources into the worktree as symlinks and keeps it clean', () => {
    const root = repository();
    ignoreResources(root, 'models/', '.venv/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    seedDirectory(root, '.venv', 'pyvenv.cfg', 'cfg\n');
    writeSharedResourceConfig(root, 'models', '.venv');
    // Config lives in the common dir, not the working tree: the primary stays clean.
    expect(git(root, 'status', '--porcelain=v1', '--untracked-files=all')).toBe('');

    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);
    expect(lstatSync(join(assignment.worktree_path, '.venv')).isSymbolicLink()).toBe(true);
    expect(readFileSync(join(assignment.worktree_path, 'models', 'weights.bin'), 'utf8')).toBe('weights\n');
    expect(readFileSync(join(assignment.worktree_path, '.venv', 'pyvenv.cfg'), 'utf8')).toBe('cfg\n');
    // LOAD-BEARING: a `models/` dir-slash ignore does NOT match a `models` symlink, so without an
    // anchored no-slash exclude entry the planted links read UNTRACKED and this predicate goes false —
    // which is exactly what makes crash reconciliation, push-pending, cleanup, and removeWorktree refuse.
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('skips a listed resource absent in the primary and still activates the assignment', () => {
    const root = repository();
    ignoreResources(root, 'models/', 'absent-dir/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    writeSharedResourceConfig(root, 'models', 'absent-dir');

    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(assignment.lifecycle_status).toBe('active');
    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);
    expect(existsSync(join(assignment.worktree_path, 'absent-dir'))).toBe(false);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('never links an unlisted gitignored path even when it exists in the primary', () => {
    const root = repository();
    ignoreResources(root, 'models/', 'secret.env');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    writeFileSync(join(root, 'secret.env'), 'API_KEY=leak\n');
    // Only `models` is listed; the gitignored secret is not.
    writeSharedResourceConfig(root, 'models');

    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);
    expect(existsSync(join(assignment.worktree_path, 'secret.env'))).toBe(false);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('does not overwrite a path that already exists in the fresh worktree', () => {
    const root = repository();
    // README.md is tracked and therefore present in every checkout; listing it must be a no-op.
    writeSharedResourceConfig(root, 'README.md');

    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(lstatSync(join(assignment.worktree_path, 'README.md')).isSymbolicLink()).toBe(false);
    expect(readFileSync(join(assignment.worktree_path, 'README.md'), 'utf8')).toBe('initial\n');
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('rejects escaping or glob config entries even when their sources exist, planting nothing', () => {
    const root = repository();
    // Give the rejected entries REAL sources so ONLY isSafeSharedEntry can stop them:
    // the existsSync(source) skip must not be what makes this test pass.
    const escapeName = `ironclaude-escape-${randomUUID()}`;
    const outside = join(realpathSync(root), '..', escapeName);
    mkdirSync(join(outside, 'inner'), { recursive: true });
    writeFileSync(join(outside, 'inner', 'marker'), 'do-not-reach\n');
    directories.push(outside);
    seedDirectory(root, 'mo*dels', 'weights.bin', 'weights\n');
    writeSharedResourceConfig(root, `../${escapeName}`, '/etc/x', 'mo*dels');

    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(assignment.lifecycle_status).toBe('active');
    // The glob entry has a real source; only validation keeps it from being linked.
    expect(existsSync(join(assignment.worktree_path, 'mo*dels'))).toBe(false);
    // The `../` entry escapes the worktree; had it been honoured the link would land one level up.
    expect(existsSync(join(realpathSync(root), '.ironclaude', 'worktrees', escapeName))).toBe(false);
  });

  it('creates no links when no shared-resource config is present', () => {
    const root = repository();
    ignoreResources(root, 'models/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    // No config file written.

    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    expect(existsSync(join(assignment.worktree_path, 'models'))).toBe(false);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('removes a worktree with planted links through cleanupWorkspace and preserves the primary resource', () => {
    const root = repository();
    ignoreResources(root, 'models/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    writeSharedResourceConfig(root, 'models');
    const database = initDb(join(root, 'cleanup-shared.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);

    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER });
    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    git(root, 'update-ref', recoveryRef, git(assignment.worktree_path, 'rev-parse', 'HEAD'));
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?')
      .run(recoveryRef, assignment.workspace_guid);

    // Removal must go through removeWorktree (git worktree remove), not rmSync.
    expect(manager.cleanupWorkspace({
      repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER,
    }).lifecycle_status).toBe('cleaned');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    // The symlink was unlinked, not descended: the primary resource and its content survive.
    expect(existsSync(join(root, 'models'))).toBe(true);
    expect(readFileSync(join(root, 'models', 'weights.bin'), 'utf8')).toBe('weights\n');
  });

  it('reset-and-reuses a spent same-GUID worktree in place and clears its stale integration record', () => {
    const root = repository();
    const database = initDb(join(root, 'reuse-spent.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;

    // Drive the GUID to a spent (cleaned) terminal state by mirroring the
    // integrated-cleanup fixture above: commit reviewed work, mark integrated,
    // record the integration whose target_ref matches the assignment's own
    // integration_target (default primary branch, never hard-coded), fast-forward
    // that target onto the commit, then clean up.
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    const targetRef = `refs/heads/${assignment.integration_target}`;
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ? WHERE workspace_guid = ?
    `).run(integratedCommit, guid);
    recordIntegration(database, {
      workspaceGuid: guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef,
      integratedCommit,
    });
    git(root, 'merge', '--ff-only', integratedCommit);
    manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER });

    // Spent: worktree removed on disk, but the integration record still stands.
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?').get(guid)).toBeTruthy();

    // Re-allocating the SAME owner (hence same GUID) must reset-and-reuse the
    // spent row in place, not collide on assignments.workspace_guid.
    // Falsifier (pre-GREEN): createAssignment INSERT throws
    // "UNIQUE constraint failed: assignments.workspace_guid".
    const reused = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    expect(reused.workspace_guid).toBe(guid);
    expect(reused.lifecycle_status).toBe('active');
    expect(existsSync(reused.worktree_path)).toBe(true);
    // The recycled row carries no stale integration commit from its prior life.
    // Falsifier: dropping `integrated_commit = NULL` from the reuse UPDATE leaves
    // the retired SHA on the fresh active row.
    expect(reused.integrated_commit).toBeNull();
    // The prior lifecycle's integration record is cleared so the recycled row
    // carries no stale integration proof.
    expect(database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?').get(guid)).toBeUndefined();
  });

  it('refuses to reset an integrated same-GUID row whose worktree is gone, preserving its integration record', () => {
    const root = repository();
    const database = initDb(join(root, 'reuse-integrated-gone.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;

    // Drive the GUID to an INTEGRATED terminal state (live crash-recovery
    // evidence), then remove the worktree from Git + disk WITHOUT cleaning up, so
    // the row is terminal-and-worktree-gone (reuseSpent === true) yet still
    // 'integrated'.
    writeFileSync(join(assignment.worktree_path, 'integrated.txt'), 'integrated\n');
    git(assignment.worktree_path, 'add', 'integrated.txt');
    git(assignment.worktree_path, 'commit', '-m', 'integrated work');
    const integratedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    database.prepare(`
      UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ? WHERE workspace_guid = ?
    `).run(integratedCommit, guid);
    recordIntegration(database, {
      workspaceGuid: guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef: `refs/heads/${assignment.integration_target}`,
      integratedCommit,
    });
    git(root, 'worktree', 'remove', '--force', assignment.worktree_path);
    expect(existsSync(assignment.worktree_path)).toBe(false);
    // Plant the finalization candidate ref that a real integrated row carries, so we
    // can prove the reuse branch (which deletes it) is never entered for a non-cleaned row.
    const candidateRef = `refs/ironclaude/finalization/${guid}/candidate`;
    git(root, 'update-ref', candidateRef, integratedCommit);

    // Re-allocating the SAME owner: an integrated row is NOT 'cleaned', so the reuse
    // gate (lifecycle === 'cleaned' && worktreeGone) skips it and allocation falls to
    // createAssignment's bare INSERT, which collides on the assignments PRIMARY KEY.
    // Its crash-recovery evidence (row, integration record, candidate ref) is preserved.
    // Falsifier: widening the gate back to any terminal row runs the reset (row
    // 'reserved', integrated_commit NULL, record + candidate ref deleted), failing below.
    expect(() => manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER }))
      .toThrow('UNIQUE constraint failed');
    // Preserved: the integrated row, its integration record, and its candidate ref.
    expect(database.prepare('SELECT lifecycle_status, integrated_commit FROM assignments WHERE workspace_guid = ?')
      .get(guid)).toMatchObject({ lifecycle_status: 'integrated', integrated_commit: integratedCommit });
    expect(database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?').get(guid)).toBeTruthy();
    expect(git(root, 'rev-parse', '--verify', candidateRef).trim()).toBe(integratedCommit);
  });

  it('refuses to reset an abandoned same-GUID row whose worktree is gone, preserving the row and its branch', () => {
    const root = repository();
    const database = initDb(join(root, 'reuse-abandoned-gone.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'wip.txt'), 'unintegrated work\n');
    git(assignment.worktree_path, 'add', 'wip.txt');
    git(assignment.worktree_path, 'commit', '-m', 'abandoned work');
    const branchHead = git(assignment.worktree_path, 'rev-parse', 'HEAD').trim();
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER });
    git(root, 'worktree', 'remove', '--force', assignment.worktree_path);
    expect(existsSync(assignment.worktree_path)).toBe(false);

    // Abandoned rows keep their branch (they can never be cleaned in production), so
    // reuse via addWorktree -b would collide on the surviving branch. Narrowing reuse
    // to 'cleaned' routes this to createAssignment's clean PRIMARY-KEY collision instead.
    // Falsifier: re-admitting 'abandoned' commits the reset (row 'reserved'), then
    // addWorktree throws a branch-already-exists error, failing both asserts below.
    expect(() => manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER }))
      .toThrow('UNIQUE constraint failed');
    // The abandoned row is NOT corrupted to 'reserved'; the branch and its work survive.
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(guid)).toMatchObject({ lifecycle_status: 'abandoned' });
    expect(git(root, 'rev-parse', '--verify', `refs/heads/${branch}`).trim()).toBe(branchHead);
  });

  it('never resets a live non-terminal same-GUID row: the early return preserves it untouched', () => {
    const root = repository();
    const database = initDb(join(root, 'reuse-live.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const sentinel = '0'.repeat(40);
    database.prepare('UPDATE assignments SET base_commit = ? WHERE workspace_guid = ?')
      .run(sentinel, assignment.workspace_guid);

    // A live (active) owner row short-circuits in ensureSessionWorktree's
    // early-return branch, never reaching the reuse gate; the sentinel column
    // survives untouched. Were the reset path reachable it would rewrite
    // base_commit to the live primary HEAD.
    const again = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    expect(again.workspace_guid).toBe(assignment.workspace_guid);
    expect(again.lifecycle_status).toBe('active');
    expect(again.base_commit).toBe(sentinel);
  });

  it('refuses to reset a terminal same-GUID row whose worktree still exists on disk, preserving its recovery proof', () => {
    const root = repository();
    const database = initDb(join(root, 'reuse-terminal-present.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    writeFileSync(join(assignment.worktree_path, 'recovery.txt'), 'recoverable\n');
    git(assignment.worktree_path, 'add', 'recovery.txt');
    git(assignment.worktree_path, 'commit', '-m', 'recoverable work');
    const recoveryHead = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: assignment.workspace_guid, ownerSessionId: OWNER });
    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    git(root, 'update-ref', recoveryRef, recoveryHead);
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?')
      .run(recoveryRef, assignment.workspace_guid);

    // Terminal (abandoned) but the worktree was never cleaned: it is still on
    // disk. The worktreeGone conjunct must keep the reuse path from firing, so
    // allocation collides on the PRIMARY KEY instead of destroying the row.
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(() => manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER }))
      .toThrow('UNIQUE constraint failed');
    // Preserved: worktree intact, still abandoned, recovery proof NOT nulled.
    // Deleting the worktreeGone conjunct makes the reset path run and null
    // recovery_ref, failing this exact assertion.
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(database.prepare('SELECT lifecycle_status, recovery_ref FROM assignments WHERE workspace_guid = ?')
      .get(assignment.workspace_guid)).toMatchObject({ lifecycle_status: 'abandoned', recovery_ref: recoveryRef });
  });

  it('rescueAbandon anchors the rescued commit on a durable ref that survives branch deletion', () => {
    const root = repository();
    const database = initDb(join(root, 'rescue-durable-ref.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    // Uncommitted, untracked content: reachable from nothing until rescue anchors it.
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');

    const abandoned = manager.abandonWorkspace({
      repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue',
    });

    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    // recovery_ref stores the durable REF NAME, not a bare SHA.
    expect(abandoned.recovery_ref).toBe(recoveryRef);
    const rescuedCommit = git(root, 'rev-parse', recoveryRef);

    // Delete the worker branch. Without a durable recovery ref, the rescued commit
    // would become unreachable and the work lost — the exact never-lose-work failure.
    git(root, 'branch', '-D', branch);
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(git(root, 'cat-file', '-p', `${recoveryRef}:unintegrated.txt`)).toBe('rescue me');
  });

  it('cleanupWorkspace tombstones a rescue-abandoned row (branch deleted, ref preserved)', () => {
    const root = repository();
    const database = initDb(join(root, 'rescue-tombstone.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');

    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    const rescuedCommit = git(root, 'rev-parse', recoveryRef);
    // Rescue removed the worktree directory but preserved the branch.
    expect(existsSync(assignment.worktree_path)).toBe(false);

    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');
    // The worker branch is now gone, but the recovery ref still anchors the rescued work.
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
  });

  it('reapLeakedAssignment preserves then tombstones an ownerless active row (present worktree)', () => {
    const root = repository();
    const database = initDb(join(root, 'reap-active-present.db'));
    const manager = new WorkspaceService(database);
    const guid = randomUUID();
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root, workspaceGuid: guid, workerId: 'worker-1',
    });
    expect(assignment.owner_session_id).toBeNull();
    expect(assignment.lifecycle_status).toBe('active');
    // Uncommitted work in the leaked worktree: must survive the reap.
    writeFileSync(join(assignment.worktree_path, 'leaked.txt'), 'leaked work\n');

    const reaped = manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid: guid });
    expect(reaped.lifecycle_status).toBe('cleaned');
    // Work preserved on a durable recovery ref; worktree and branch both gone.
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    expect(git(root, 'cat-file', '-p', `${recoveryRef}:leaked.txt`)).toBe('leaked work');
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');
  });

  it('reapLeakedAssignment tombstones an ownerless row whose worktree is gone but branch survives', () => {
    const root = repository();
    const database = initDb(join(root, 'reap-worktree-gone-branch.db'));
    const manager = new WorkspaceService(database);
    const guid = randomUUID();
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root, workspaceGuid: guid, workerId: 'worker-1',
    });
    writeFileSync(join(assignment.worktree_path, 'committed.txt'), 'committed work\n');
    git(assignment.worktree_path, 'add', 'committed.txt');
    git(assignment.worktree_path, 'commit', '-m', 'committed work');
    const branchTip = git(root, 'rev-parse', assignment.branch);
    // Worktree removed from Git + disk, but the branch (and its commit) survive.
    git(root, 'worktree', 'remove', '--force', assignment.worktree_path);
    expect(existsSync(assignment.worktree_path)).toBe(false);

    const reaped = manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid: guid });
    expect(reaped.lifecycle_status).toBe('cleaned');
    // The recovery ref was minted at the surviving branch tip before the branch was deleted.
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    expect(git(root, 'rev-parse', recoveryRef)).toBe(branchTip);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');
  });

  it('reapLeakedAssignment on an ownerless row with worktree AND branch gone records base_commit and tombstones', () => {
    const root = repository();
    const database = initDb(join(root, 'reap-worktree-and-branch-gone.db'));
    const manager = new WorkspaceService(database);
    const guid = randomUUID();
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root, workspaceGuid: guid, workerId: 'worker-1',
    });
    const baseCommit = assignment.base_commit;
    git(root, 'worktree', 'remove', '--force', assignment.worktree_path);
    git(root, 'branch', '-D', assignment.branch);
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', assignment.branch)).toBe('');

    const reaped = manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid: guid });
    expect(reaped.lifecycle_status).toBe('cleaned');
    // With no worktree and no branch, the recovery ref falls back to the recorded base commit.
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    expect(git(root, 'rev-parse', recoveryRef)).toBe(baseCommit);
  });

  it('reapLeakedAssignment refuses+preserves a present worktree checked out on a FOREIGN branch', () => {
    const root = repository();
    const database = initDb(join(root, 'reap-foreign-branch.db'));
    const manager = new WorkspaceService(database);
    const guid = randomUUID();
    const assignment = manager.reserveWorkerWorktree({
      repositoryPath: root, workspaceGuid: guid, workerId: 'worker-1',
    });
    // Check the managed worktree out onto a DIFFERENT branch: its Git identity no
    // longer matches the durable assignment, so reconciliation must preserve it.
    git(assignment.worktree_path, 'checkout', '-b', 'foreign-branch');

    expect(() => manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid: guid }))
      .toThrow('does not match durable assignment');
    // Preserved: row untouched, worktree still on disk.
    expect(existsSync(assignment.worktree_path)).toBe(true);
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(guid)).toMatchObject({ lifecycle_status: 'active' });
  });

  it('reapLeakedAssignment routes a reserved never-materialized row to a row-delete', () => {
    const root = repository();
    const database = initDb(join(root, 'reap-reserved.db'));
    const manager = new WorkspaceService(database);
    const guid = randomUUID();
    const worktreePath = join(realpathSync(root), '.ironclaude', 'worktrees', guid);
    const baseCommit = git(root, 'rev-parse', 'HEAD');
    // A reserved row whose materialization never planted a worktree on disk.
    createAssignment(database, {
      workspaceGuid: guid,
      repositoryIdentity: commonDir(root),
      worktreePath,
      branch: `ironclaude/${guid}`,
      baseCommit,
      currentHead: baseCommit,
      ownerSessionId: null,
      workerId: 'worker-1',
      integrationTarget: 'main',
    });
    expect(existsSync(worktreePath)).toBe(false);
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?')
      .get(guid)).toMatchObject({ lifecycle_status: 'reserved' });

    const reaped = manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid: guid });
    expect(reaped.lifecycle_status).toBe('cleaned');
    // Nothing on disk and nothing to anchor: the row is deleted outright.
    expect(database.prepare('SELECT 1 FROM assignments WHERE workspace_guid = ?').get(guid)).toBeUndefined();
  });

  it('tombstone upgrades a legacy raw-SHA recovery_ref (absent worktree) to a durable ref before deleting the branch', () => {
    const root = repository();
    const database = initDb(join(root, 'legacy-absent.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    const rescuedCommit = git(root, 'rev-parse', recoveryRef);
    // Simulate a DEPLOYED pre-fix legacy row: only the branch anchors the commit, and
    // recovery_ref is a bare SHA (no durable ref).
    git(root, 'update-ref', '-d', recoveryRef);
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?').run(rescuedCommit, guid);
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', branch)).not.toBe('');

    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');

    // Branch deleted, but the rescued commit is now anchored by a durable ref that
    // survives the deletion, and the DB records the ref NAME, not the bare SHA.
    expect(git(root, 'branch', '--list', branch)).toBe('');
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(git(root, 'cat-file', '-p', `${recoveryRef}:unintegrated.txt`)).toBe('rescue me');
    expect(database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid))
      .toMatchObject({ recovery_ref: recoveryRef });
  });

  it('tombstone upgrades a legacy raw-SHA recovery_ref (present worktree) before deleting the branch', () => {
    const root = repository();
    const database = initDb(join(root, 'legacy-present.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'recovery.txt'), 'recoverable\n');
    git(assignment.worktree_path, 'add', 'recovery.txt');
    git(assignment.worktree_path, 'commit', '-m', 'recoverable work');
    const rescuedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    // Default abandon keeps the worktree on disk; set a bare-SHA recovery_ref (legacy).
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER });
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?').run(rescuedCommit, guid);
    expect(existsSync(assignment.worktree_path)).toBe(true);

    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');

    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    expect(git(root, 'branch', '--list', branch)).toBe('');
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid))
      .toMatchObject({ recovery_ref: recoveryRef });
  });

  it('tombstone leaves a normal ref-name recovery_ref untouched (no re-mint, no DB rewrite)', () => {
    const root = repository();
    const database = initDb(join(root, 'refname-noop.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    const rescuedCommit = git(root, 'rev-parse', recoveryRef);
    const before = database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid) as { recovery_ref: string };
    expect(before.recovery_ref).toBe(recoveryRef);

    // R2 "no DB write" detector: no UPDATE touching recovery_ref may run while
    // cleaning up a ref-name row. updated_at cannot detect this — the lifecycle
    // transition unconditionally rewrites updated_at on the abandoned->cleaned tombstone.
    const prepareSpy = vi.spyOn(database, 'prepare');
    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');
    const recoveryRefWrites = prepareSpy.mock.calls
      .map((call) => String(call[0]))
      .filter((sql) => /update/i.test(sql) && /recovery_ref/i.test(sql));
    prepareSpy.mockRestore();
    expect(recoveryRefWrites).toEqual([]);

    const after = database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid) as { recovery_ref: string };
    expect(after.recovery_ref).toBe(recoveryRef);
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(git(root, 'branch', '--list', branch)).toBe('');
  });

  it('tombstone refuses (preserves) a raw-SHA recovery_ref whose object no longer exists', () => {
    const root = repository();
    const database = initDb(join(root, 'unanchorable.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    git(root, 'update-ref', '-d', recoveryRef);
    // A bare full-hex SHA that names no existing object. git rev-parse --verify accepts a
    // full 40-hex without an existence check, so the gate's mint (update-ref) is what fails
    // here — fail-closed: it throws before any branch deletion, preserving the row.
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?').run('1'.repeat(40), guid);

    expect(() => manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER }))
      .toThrow();
    // Row NOT tombstoned; the worker branch is preserved.
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(guid))
      .toMatchObject({ lifecycle_status: 'abandoned' });
    expect(git(root, 'branch', '--list', branch)).not.toBe('');
  });

  it('configureSharedResources appends config and relinks into an ALREADY-LIVE managed worktree', () => {
    const root = repository();
    ignoreResources(root, 'models/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    const manager = service(root);
    // Live worktree created BEFORE the entry is configured (the incident shape).
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    expect(existsSync(join(assignment.worktree_path, 'models'))).toBe(false);

    const result = manager.configureSharedResources({ repositoryPath: root, entries: ['models'] });

    expect(result.added).toEqual(['models']);
    expect(result.relinked[assignment.worktree_path]).toEqual(['models']);
    // The live worker now has the data — no respawn.
    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);
    expect(readFileSync(join(assignment.worktree_path, 'models', 'weights.bin'), 'utf8')).toBe('weights\n');
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
    expect(manager.listSharedResources({ repositoryPath: root })).toEqual({ entries: ['models'] });
  });

  it('configureSharedResources writes a source-absent entry to config but does NOT relink it', () => {
    const root = repository();
    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    const result = manager.configureSharedResources({ repositoryPath: root, entries: ['absent-data'] });

    expect(result.added).toEqual(['absent-data']);
    expect(result.relinked[assignment.worktree_path] ?? []).toEqual([]);
    expect(existsSync(join(assignment.worktree_path, 'absent-data'))).toBe(false);
    expect(manager.listSharedResources({ repositoryPath: root })).toEqual({ entries: ['absent-data'] });
  });

  it('configureSharedResources blocks a well-known secret entry from being relinked into a live worktree', () => {
    const root = repository();
    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    const result = manager.configureSharedResources({ repositoryPath: root, entries: ['.env'] });

    expect(result.secretBlocked).toEqual(['.env']);
    expect(existsSync(join(assignment.worktree_path, '.env'))).toBe(false);
  });

  it('configureSharedResources relinks a secret entry into a live worktree when the operator sets allowSecretEntries', () => {
    const root = repository();
    ignoreResources(root, '.env');
    writeFileSync(join(root, '.env'), 'SECRET=1\n');
    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    const result = manager.configureSharedResources({ repositoryPath: root, entries: ['.env'], allowSecretEntries: true });

    expect(result.relinked[assignment.worktree_path]).toEqual(['.env']);
    expect(lstatSync(join(assignment.worktree_path, '.env')).isSymbolicLink()).toBe(true);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('configureSharedResources relinks an already-configured entry once its source appears (present-but-unlinked recovery)', () => {
    const root = repository();
    ignoreResources(root, 'models/');
    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    // Configured while the source is absent: written to config, nothing linked.
    const first = manager.configureSharedResources({ repositoryPath: root, entries: ['models'] });
    expect(first.added).toEqual(['models']);
    expect(first.relinked[assignment.worktree_path] ?? []).toEqual([]);
    expect(existsSync(join(assignment.worktree_path, 'models'))).toBe(false);
    // Operator provides the source; the Brain re-issues configure to recover.
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    const second = manager.configureSharedResources({ repositoryPath: root, entries: ['models'] });
    expect(second.skipped).toEqual(['models']);
    expect(second.relinked[assignment.worktree_path]).toEqual(['models']);
    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });

  it('configureSharedResources does NOT relink into an operator worktree outside the managed root', () => {
    const root = repository();
    ignoreResources(root, 'models/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    const manager = service(root);
    // An operator-created worktree OUTSIDE .ironclaude/worktrees/, with no assignments row.
    const operatorWt = mkdtempSync(join(tmpdir(), 'ironclaude-operator-wt-'));
    directories.push(operatorWt);
    rmSync(operatorWt, { recursive: true, force: true });
    git(root, 'worktree', 'add', '-b', 'operator-branch', operatorWt);

    const result = manager.configureSharedResources({ repositoryPath: root, entries: ['models'] });

    expect(result.added).toEqual(['models']);
    // No live assignments row → nothing relinked; the operator worktree is untouched.
    expect(result.relinked).toEqual({});
    expect(existsSync(join(operatorWt, 'models'))).toBe(false);
  });

  it('does NOT relink into a managed worktree whose row is in a TERMINAL lifecycle (integrated), even with the dir on disk', () => {
    // Discriminating test for the `lifecycle_status NOT IN ('integrated','abandoned','cleaned')`
    // clause: this is the ONLY case that fails if that clause is deleted (a terminal row
    // whose worktree dir still exists would then be wrongly relinked).
    const root = repository();
    ignoreResources(root, 'models/');
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    const database = initDb(join(root, 'terminal-lifecycle.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    // Move the row to a TERMINAL lifecycle while the worktree dir stays on disk.
    database.prepare("UPDATE assignments SET lifecycle_status = 'integrated' WHERE workspace_guid = ?")
      .run(assignment.workspace_guid);
    expect(existsSync(assignment.worktree_path)).toBe(true);

    const result = manager.configureSharedResources({ repositoryPath: root, entries: ['models'] });

    expect(result.added).toEqual(['models']);
    // Terminal row is excluded by the lifecycle filter → not relinked, no symlink planted.
    expect(result.relinked[assignment.worktree_path] ?? []).toEqual([]);
    expect(existsSync(join(assignment.worktree_path, 'models'))).toBe(false);
  });

  it('configureSharedResources scans an explicitly-shared directory for secrets end-to-end, blocks it, and relinks under allowSecretEntries', () => {
    const root = repository();
    ignoreResources(root, 'config/', 'models/');
    seedDirectory(root, 'config', '.env', 'SECRET=1\n');
    const manager = service(root);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });

    const blocked = manager.configureSharedResources({ repositoryPath: root, entries: ['config'] });
    expect(blocked.secretBlocked).toEqual(['config']);
    expect(blocked.secretHits.config).toEqual(['config/.env']);
    expect(existsSync(join(assignment.worktree_path, 'config'))).toBe(false);

    const overridden = manager.configureSharedResources({ repositoryPath: root, entries: ['config'], allowSecretEntries: true });
    expect(overridden.relinked[assignment.worktree_path]).toEqual(['config']);
    expect(lstatSync(join(assignment.worktree_path, 'config')).isSymbolicLink()).toBe(true);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);

    // Regression: a clean shared directory (no secrets) still links normally (operator-free path).
    seedDirectory(root, 'models', 'weights.bin', 'weights\n');
    const clean = manager.configureSharedResources({ repositoryPath: root, entries: ['models'] });
    expect(clean.relinked[assignment.worktree_path]).toEqual(['models']);
    expect(lstatSync(join(assignment.worktree_path, 'models')).isSymbolicLink()).toBe(true);
    expect(worktreeIsClean(assignment.worktree_path)).toBe(true);
  });
});
