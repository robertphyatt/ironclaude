import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createHumanIntent, initDb, recordIntegration } from '../db.js';
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
    });
    expect(manager.getWorkspaceStatusForRoot({ repositoryPath: root, ownerSessionId: OTHER_OWNER }).status)
      .toBe('unassigned');
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
});
