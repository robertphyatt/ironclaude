import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';

export interface RepositoryLocation {
  /** Canonical Git common directory; this is the durable repository identity. */
  repositoryIdentity: string;
  /** First worktree in Git's porcelain listing: repository primary checkout. */
  primaryCheckoutPath: string;
}

export interface GitWorktree {
  path: string;
  head: string | null;
  branch: string | null;
  bare: boolean;
}

const MANAGED_WORKTREE_EXCLUSION = '/.ironclaude/worktrees/';

function gitError(cwd: string, args: readonly string[], stderr: string): Error {
  const detail = stderr.trim() || 'Git command failed';
  return new Error(`${detail} (git -C ${cwd} ${args.join(' ')})`);
}

/** Executes Git through argv only. User-provided paths and refs never enter a shell. */
export function runGit(cwd: string, args: readonly string[]): string {
  const result = spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw gitError(cwd, args, result.stderr || '');
  return result.stdout || '';
}

function absoluteFrom(cwd: string, value: string): string {
  return path.resolve(cwd, value);
}

function canonicalPath(value: string): string {
  return realpathSync(value);
}

export function listWorktrees(cwd: string): GitWorktree[] {
  const output = runGit(cwd, ['worktree', 'list', '--porcelain']);
  const entries: GitWorktree[] = [];
  let current: Partial<GitWorktree> | undefined;
  for (const line of output.split('\n')) {
    if (line === '') {
      if (current?.path) {
        entries.push({ path: canonicalPath(current.path), head: current.head ?? null, branch: current.branch ?? null, bare: current.bare === true });
      }
      current = undefined;
      continue;
    }
    const separator = line.indexOf(' ');
    const key = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? '' : line.slice(separator + 1);
    if (key === 'worktree') current = { path: value, bare: false };
    else if (!current) throw new Error('Malformed git worktree porcelain output');
    else if (key === 'HEAD') current.head = value;
    else if (key === 'branch') current.branch = value;
    else if (key === 'bare') current.bare = true;
  }
  if (current?.path) {
    entries.push({ path: canonicalPath(current.path), head: current.head ?? null, branch: current.branch ?? null, bare: current.bare === true });
  }
  return entries;
}

/**
 * Git's common directory, not a caller's current worktree, is repository
 * identity. This keeps a managed-worktree resume bound to its original repo.
 */
export function discoverRepository(cwd: string): RepositoryLocation {
  const commonDirectory = runGit(cwd, ['rev-parse', '--git-common-dir']).trim();
  // `--git-common-dir` is reported relative to the directory git ran in, not to
  // the worktree top. Resolving it against the toplevel made a cwd one level
  // down point at a sibling `.git` — throwing on a plain repository, and worse,
  // silently yielding the PARENT repository's identity when repos are nested.
  // The guard hook resolves the same value against the event cwd, so the two
  // disagreed and every mutation blocked.
  const repositoryIdentity = canonicalPath(absoluteFrom(cwd, commonDirectory));
  const worktrees = listWorktrees(cwd);
  const primary = worktrees[0];
  if (!primary || primary.bare) throw new Error('Repository has no primary checkout');
  return { repositoryIdentity, primaryCheckoutPath: primary.path };
}

export function worktreeExists(cwd: string, worktreePath: string): boolean {
  const canonical = path.resolve(worktreePath);
  return listWorktrees(cwd).some((entry) => entry.path === canonical);
}

export function worktreeIsClean(worktreePath: string): boolean {
  return runGit(worktreePath, ['status', '--porcelain=v1', '--untracked-files=all']) === '';
}

export function worktreeHead(worktreePath: string): string {
  return runGit(worktreePath, ['rev-parse', 'HEAD']).trim();
}

/**
 * Branch the primary checkout is currently on, used as the default integration
 * target. Callers previously hardcoded `main`, so every worker on a `master`,
 * `trunk`, or release-branch repository targeted a ref that does not exist and
 * only discovered it at finalization, after the whole worker run.
 */
export function primaryBranch(primaryCheckoutPath: string): string {
  let ref: string;
  try {
    ref = runGit(primaryCheckoutPath, ['symbolic-ref', '--quiet', 'HEAD']).trim();
  } catch {
    throw new Error('Primary checkout is in detached HEAD; supply integration_target explicitly');
  }
  if (!ref.startsWith('refs/heads/')) {
    throw new Error('Primary checkout is not on a branch; supply integration_target explicitly');
  }
  return ref.slice('refs/heads/'.length);
}

export function addWorktree(primaryCheckoutPath: string, worktreePath: string, branch: string, baseCommit: string): void {
  if (existsSync(worktreePath)) throw new Error(`Managed worktree path already exists: ${worktreePath}`);
  runGit(primaryCheckoutPath, ['worktree', 'add', '-b', branch, '--', worktreePath, baseCommit]);
}

/**
 * Keep nested managed worktrees out of primary-checkout status without
 * changing tracked ignore files or operator-wide Git configuration.
 */
export function ensureManagedWorktreeExclusion(repositoryIdentity: string): void {
  const infoDirectory = path.join(repositoryIdentity, 'info');
  const excludePath = path.join(infoDirectory, 'exclude');
  mkdirSync(infoDirectory, { recursive: true });
  const existing = existsSync(excludePath) ? readFileSync(excludePath) : Buffer.alloc(0);
  const hasExactEntry = existing.toString('utf8').split(/\r?\n/)
    .some((line) => line === MANAGED_WORKTREE_EXCLUSION);
  if (hasExactEntry) return;
  const separator = existing.length === 0 || existing[existing.length - 1] === 10 ? '' : '\n';
  writeFileSync(excludePath, Buffer.concat([
    existing,
    Buffer.from(`${separator}${MANAGED_WORKTREE_EXCLUSION}\n`, 'utf8'),
  ]));
}

export function removeWorktree(primaryCheckoutPath: string, worktreePath: string): void {
  runGit(primaryCheckoutPath, ['worktree', 'remove', '--', worktreePath]);
}

/** Deletes only a branch name already proven to be this assignment's canonical private branch. */
export function deleteTemporaryBranch(primaryCheckoutPath: string, branch: string): void {
  runGit(primaryCheckoutPath, ['branch', '-D', '--', branch]);
}

/** Returns false for a missing or unresolvable ref rather than treating it as proof. */
export function isAncestor(cwd: string, ancestor: string, descendant: string): boolean {
  const result = spawnSync('git', ['-C', cwd, 'merge-base', '--is-ancestor', ancestor, descendant], { encoding: 'utf8' });
  if (result.error) throw result.error;
  if (result.status === 0) return true;
  if (result.status === 1 || result.status === 128) return false;
  throw gitError(cwd, ['merge-base', '--is-ancestor', ancestor, descendant], result.stderr || '');
}
