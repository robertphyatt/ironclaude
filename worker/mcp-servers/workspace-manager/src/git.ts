import { spawnSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, opendirSync, readFileSync, realpathSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
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

/**
 * runGit with an explicit environment (e.g. GIT_INDEX_FILE pointing at a scratch
 * index outside the worktree). Same argv-only, no-shell contract as runGit.
 */
export function runGitEnv(cwd: string, args: readonly string[], env: NodeJS.ProcessEnv): string {
  const result = spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8', env });
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

const SHARED_RESOURCE_CONFIG = 'worktree-shared-resources';

/**
 * Idempotently appends each exact line to the shared `<commonDir>/info/exclude`,
 * preserving operator entries and a trailing-newline invariant. A line already
 * present (or already appended in this call) is never duplicated.
 */
function appendExcludeLines(repositoryIdentity: string, lines: readonly string[]): void {
  const infoDirectory = path.join(repositoryIdentity, 'info');
  const excludePath = path.join(infoDirectory, 'exclude');
  mkdirSync(infoDirectory, { recursive: true });
  const existing = existsSync(excludePath) ? readFileSync(excludePath) : Buffer.alloc(0);
  const present = new Set(existing.toString('utf8').split(/\r?\n/));
  let buffer = existing;
  let appended = false;
  for (const line of lines) {
    if (present.has(line)) continue;
    present.add(line);
    const separator = buffer.length === 0 || buffer[buffer.length - 1] === 10 ? '' : '\n';
    buffer = Buffer.concat([buffer, Buffer.from(`${separator}${line}\n`, 'utf8')]);
    appended = true;
  }
  if (appended) writeFileSync(excludePath, buffer);
}

/**
 * Keep nested managed worktrees out of primary-checkout status without
 * changing tracked ignore files or operator-wide Git configuration.
 */
export function ensureManagedWorktreeExclusion(repositoryIdentity: string): void {
  appendExcludeLines(repositoryIdentity, [MANAGED_WORKTREE_EXCLUSION]);
}

/**
 * Excludes each planted shared-resource link from Git's view. Every entry is
 * written anchored with NO trailing slash (`/models`): a symlink is not a
 * directory, so a `models/` dir-slash ignore pattern would NOT match a `models`
 * symlink, and the unignored link would read untracked — flipping
 * `worktreeIsClean` false and making crash reconciliation, push-pending,
 * cleanup, and `removeWorktree` all refuse.
 */
export function ensureExcludeEntries(repositoryIdentity: string, entries: readonly string[]): void {
  appendExcludeLines(repositoryIdentity, entries.map((entry) => `/${entry}`));
}

/**
 * Reads an explicit, per-repository list of relative paths to share into managed
 * worktrees, one per line, from `<commonDir>/info/worktree-shared-resources`.
 * Blank lines and `#` comments are ignored; a missing file yields no entries.
 * Living in the common dir keeps it out of both primary and worktree status.
 */
export function readSharedResourceConfig(repositoryIdentity: string): string[] {
  const configPath = path.join(repositoryIdentity, 'info', SHARED_RESOURCE_CONFIG);
  if (!existsSync(configPath)) return [];
  return readFileSync(configPath, 'utf8')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith('#'));
}

/**
 * Append explicit relative paths to `<commonDir>/info/worktree-shared-resources`,
 * the same config `linkSharedResources` reads on every allocation. Each entry is
 * validated by `isSafeSharedEntry` (rejected entries are never written); entries
 * already present are skipped (deduped). This is the write half that lets the
 * Commander configure shared resources for a repo without any operator file edit.
 * Returns which entries were added, skipped (already present), rejected (unsafe),
 * and the full resulting list.
 */
export function addSharedResourceEntries(
  repositoryIdentity: string,
  entries: readonly string[],
  primaryCheckoutPath: string,
  allowSecretEntries = false,
): {
  added: string[];
  skipped: string[];
  rejected: string[];
  secretBlocked: string[];
  entries: string[];
  secretHits: Record<string, string[]>;
  scanTruncated: string[];
} {
  const configPath = path.join(repositoryIdentity, 'info', SHARED_RESOURCE_CONFIG);
  const present = new Set(readSharedResourceConfig(repositoryIdentity));
  const added: string[] = [];
  const skipped: string[] = [];
  const rejected: string[] = [];
  const secretBlocked: string[] = [];
  const secretHits: Record<string, string[]> = {};
  const scanTruncated: string[] = [];
  for (const entry of entries) {
    if (!isSafeSharedEntry(entry)) {
      rejected.push(entry);
      continue;
    }
    if (!allowSecretEntries && isSecretEntry(entry)) {
      secretBlocked.push(entry);
      continue;
    }
    if (!SCAN_VENDOR_SKIP.has(entry.split('/')[0])) {
      const absDir = path.join(primaryCheckoutPath, entry);
      let isDir = false;
      try {
        isDir = statSync(absDir).isDirectory();
      } catch {
        isDir = false;
      }
      if (isDir) {
        const { hits, truncated } = directoryContainsSecret(absDir, entry);
        if (truncated) scanTruncated.push(entry);
        if (hits.length > 0 && !allowSecretEntries) {
          secretBlocked.push(entry);
          secretHits[entry] = hits;
          continue;
        }
      }
    }
    if (present.has(entry)) {
      skipped.push(entry);
      continue;
    }
    present.add(entry);
    added.push(entry);
  }
  if (added.length > 0) {
    mkdirSync(path.dirname(configPath), { recursive: true });
    const existing = existsSync(configPath) ? readFileSync(configPath, 'utf8') : '';
    const separator = existing.length === 0 || existing.endsWith('\n') ? '' : '\n';
    writeFileSync(configPath, existing + separator + added.map((entry) => `${entry}\n`).join(''));
  }
  return { added, skipped, rejected, secretBlocked, entries: [...present], secretHits, scanTruncated };
}

/**
 * True when an entry names a well-known secret file, or lies under a directory
 * that conventionally holds secrets. Defense-in-depth on top of the explicit
 * allowlist — NOT exhaustive. Matched case-insensitively on the basename and on
 * any path segment (so both `.aws` and `.aws/credentials` are caught).
 */
function isSecretEntry(entry: string): boolean {
  const lower = entry.split('/').map((s) => s.toLowerCase());
  const SECRET_DIRS = new Set(['.ssh', '.aws', '.gnupg']);
  if (lower.some((s) => SECRET_DIRS.has(s))) return true;
  const base = lower[lower.length - 1];
  if (base.endsWith('.example')) return false;
  const SECRET_FILES = new Set([
    '.env', '.netrc', '.npmrc', '.pypirc', '.git-credentials',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials',
  ]);
  if (SECRET_FILES.has(base)) return true;
  if (base.startsWith('.env.')) return true;
  if (/\.(pem|key|p12|pfx)$/.test(base)) return true;
  return false;
}

const SCAN_MAX_DEPTH = 6;
const SCAN_MAX_ENTRIES = 20_000;
const SCAN_MAX_HITS = 5;
const SCAN_VENDOR_SKIP = new Set(['node_modules', '.venv', 'venv', 'site-packages', '.git']);

/**
 * Bounded, name-only, non-following breadth-first walk of an explicitly-shared
 * directory looking for well-known secret paths (per `isSecretEntry`). A
 * symlink is matched by name at the level it appears but never followed to
 * descend into whatever it points at. Vendor directories (`node_modules`,
 * `.venv`, ...) are skipped entirely rather than descended into. Reads dirents
 * incrementally via `opendirSync`/`readSync` (one at a time — a directory's
 * listing is never materialized) and returns the moment `maxEntries` dirents
 * have been examined, so both the number of dirents examined and the pending
 * queue are bounded by `maxEntries`: a huge or adversarial directory cannot make
 * add-time configuration hang or exhaust memory. Within-directory iteration order
 * is filesystem-native; this does not affect the security property because
 * shallow levels are fully examined before any descent, and per-directory order
 * only matters once a single directory exceeds the remaining budget — the
 * accepted "allow and report" (`truncated`) case. Bounded by `maxDepth` (relative
 * to the shared entry itself).
 */
export function directoryContainsSecret(
  absEntryDir: string,
  entryRel: string,
  limits: { maxDepth?: number; maxEntries?: number } = {},
): { hits: string[]; truncated: boolean } {
  const maxDepth = limits.maxDepth ?? SCAN_MAX_DEPTH;
  const maxEntries = limits.maxEntries ?? SCAN_MAX_ENTRIES;
  const hits: string[] = [];
  let examined = 0;
  let truncated = false;
  const queue: Array<{ abs: string; rel: string; depth: number }> = [{ abs: absEntryDir, rel: entryRel, depth: 0 }];
  while (queue.length > 0) {
    const { abs, rel, depth } = queue.shift()!;
    let dir: ReturnType<typeof opendirSync> | undefined;
    try {
      dir = opendirSync(abs);
      for (let d = dir.readSync(); d !== null; d = dir.readSync()) {
        if (examined >= maxEntries) {
          truncated = true;
          return { hits, truncated };
        }
        examined++;
        const childRel = `${rel}/${d.name}`;
        if (isSecretEntry(childRel)) {
          if (hits.length < SCAN_MAX_HITS) hits.push(childRel);
          if (hits.length >= SCAN_MAX_HITS) return { hits, truncated };
        } else if (d.isDirectory() && !SCAN_VENDOR_SKIP.has(d.name)) {
          if (depth + 1 <= maxDepth) {
            queue.push({ abs: path.join(abs, d.name), rel: childRel, depth: depth + 1 });
          } else {
            truncated = true;
          }
        }
      }
    } catch {
      truncated = true;
    } finally {
      dir?.closeSync();
    }
  }
  return { hits, truncated };
}

/** Rejects any entry that could escape the worktree or carry gitignore semantics. */
function isSafeSharedEntry(entry: string): boolean {
  if (entry.length === 0) return false;
  if (entry.startsWith('!') || entry.startsWith('#')) return false;
  if (entry.startsWith('/') || path.isAbsolute(entry)) return false;
  if (entry.endsWith('/')) return false;
  if (entry.includes('\\')) return false;
  if (/[*?[\]]/.test(entry)) return false;
  if (entry.split('/').some((segment) => segment === '..' || segment === '.' || segment === '')) return false;
  if (entry !== entry.trim()) return false;
  if (/[\x00-\x1f]/.test(entry)) return false;
  return true;
}

/** True when anything already occupies the path (including a dangling symlink). */
function pathPresent(target: string): boolean {
  try {
    lstatSync(target);
    return true;
  } catch {
    return false;
  }
}

/**
 * A managed worktree is a clean checkout MISSING gitignored resources (models,
 * .venv, node_modules, caches), so resource-dependent tests fail in every
 * isolated worker. For each explicitly configured relative path present in the
 * primary checkout, plant a symlink into the worktree, then exclude the planted
 * links from Git's view. Only listed paths are linked — nothing is discovered
 * from `.gitignore` or by walking the tree here or anywhere else in this module.
 * The single filesystem inspection of shared-resource content happens at add
 * time, in `addSharedResourceEntries`: an explicitly listed directory entry gets
 * a bounded, name-only, non-following walk (`directoryContainsSecret`) for
 * well-known secret paths (`.env`, `.ssh`, `.aws`, private keys, ...). That scan
 * can only WITHHOLD an entry pending explicit operator approval — it never adds
 * one on its own. Defense-in-depth, not exhaustive.
 */
export function linkSharedResources(
  primaryCheckoutPath: string,
  worktreePath: string,
  repositoryIdentity: string,
  entries: readonly string[],
): string[] {
  const linked: string[] = [];
  for (const entry of entries) {
    if (!isSafeSharedEntry(entry)) {
      console.error(`[workspace-manager] refusing unsafe shared-resource entry: ${entry}`);
      continue;
    }
    const source = path.join(primaryCheckoutPath, entry);
    const target = path.join(worktreePath, entry);
    if (!existsSync(source)) {
      console.error(`[workspace-manager] shared resource absent in primary checkout; skipping: ${entry}`);
      continue;
    }
    if (pathPresent(target)) {
      console.error(`[workspace-manager] worktree path already exists; not overwriting: ${entry}`);
      continue;
    }
    try {
      mkdirSync(path.dirname(target), { recursive: true });
      symlinkSync(source, target);
      linked.push(entry);
    } catch (error) {
      console.error(`[workspace-manager] failed to link shared resource ${entry}: ${String(error)}`);
    }
  }
  if (linked.length > 0) {
    ensureExcludeEntries(repositoryIdentity, linked);
  }
  return linked;
}

export function removeWorktree(primaryCheckoutPath: string, worktreePath: string): void {
  runGit(primaryCheckoutPath, ['worktree', 'remove', '--', worktreePath]);
}

/** Deletes only a branch name already proven to be this assignment's canonical private branch. */
export function deleteTemporaryBranch(primaryCheckoutPath: string, branch: string): void {
  runGit(primaryCheckoutPath, ['branch', '-D', '--', branch]);
}

/** Splits `git diff --name-only` output into a filtered, empty-string-free path list. */
function splitPaths(output: string): string[] {
  return output.split('\n').filter((line) => line.length > 0);
}

/** Files whose content differs between two commit-ish refs, via `git diff --name-only`. */
export function changedPaths(cwd: string, a: string, b: string): string[] {
  return splitPaths(runGit(cwd, ['diff', '--name-only', a, b]));
}

/**
 * Union of unstaged, staged, and untracked (non-ignored) paths in a worktree —
 * everything `worktreeIsClean` would flag, broken out per path instead of a
 * single clean/dirty boolean.
 */
export function dirtyAndUntrackedPaths(cwd: string): string[] {
  const unstaged = splitPaths(runGit(cwd, ['diff', '--name-only']));
  const staged = splitPaths(runGit(cwd, ['diff', '--name-only', '--cached']));
  const untracked = splitPaths(runGit(cwd, ['ls-files', '--others', '--exclude-standard']));
  return [...new Set([...unstaged, ...staged, ...untracked])];
}

/**
 * Git-native two-tree merge: fast-forwards a worktree's tracked content from
 * `fromCommit` to `toCommit` while preserving local modifications on paths
 * `fromCommit..toCommit` left untouched. Refuses (throws) when a
 * locally-modified path also changed between the two commits, rather than
 * silently discarding or overwriting the local edit.
 */
export function carryForwardFastForward(cwd: string, fromCommit: string, toCommit: string): void {
  runGit(cwd, ['read-tree', '-m', '-u', fromCommit, toCommit]);
}

/** Returns false for a missing or unresolvable ref rather than treating it as proof. */
export function isAncestor(cwd: string, ancestor: string, descendant: string): boolean {
  const result = spawnSync('git', ['-C', cwd, 'merge-base', '--is-ancestor', ancestor, descendant], { encoding: 'utf8' });
  if (result.error) throw result.error;
  if (result.status === 0) return true;
  if (result.status === 1 || result.status === 128) return false;
  throw gitError(cwd, ['merge-base', '--is-ancestor', ancestor, descendant], result.stderr || '');
}
