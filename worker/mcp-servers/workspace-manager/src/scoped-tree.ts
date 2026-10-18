import { realpathSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { runGit, runGitEnv } from './git.js';

// Maps an absolute allowed_files entry onto the repository the way the write
// guard and the drift check accept it: the real path of the entry's longest
// existing ancestor directory, plus the rest of the entry as spelled. The final
// component is never resolved, because git tracks a symlink as its own entry.
// Returns the POSIX repo-relative path when the result lies strictly inside
// realRoot. Returns null when it equals realRoot or lies outside it. Resolution
// errors other than a missing ancestor stop the commit.
function repoRelativeFromAbsolute(realRoot: string, entry: string): string | null {
  const absolute = path.resolve(entry);
  const remainder = [path.basename(absolute)];
  let ancestor = path.dirname(absolute);
  for (;;) {
    try {
      const resolved = path.join(realpathSync.native(ancestor), ...remainder);
      if (!resolved.startsWith(realRoot + path.sep)) return null;
      return path.relative(realRoot, resolved).split(path.sep).join('/');
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT' && code !== 'ENOTDIR') {
        throw new Error(`Cannot scope commit: cannot resolve ancestor '${ancestor}' for allowed_files entry '${entry}' (${code ?? 'unknown error'})`, { cause: error });
      }
      const parent = path.dirname(ancestor);
      if (parent === ancestor) return null;
      remainder.unshift(path.basename(ancestor));
      ancestor = parent;
    }
  }
}

// Builds a commit tree = parentTree with ONLY the allowed paths' EXACT staged
// index entries overlaid (adds/modifies/deletes). Foreign staged entries — a
// directory that shadows an allowed file name, a glob-shaped allowed entry, or any
// multi-match — are structurally excluded (exact path key, never a git pathspec).
// An allowed path with an unmerged (conflicted) index entry fails closed. Never
// reads the working tree. Caller supplies parentOid + a trusted allowedFiles list.
// An absolute allowed entry inside the repository is scoped by its repo-relative
// path; one outside it (or naming the root itself) is skipped. A non-canonical
// relative entry still refuses the commit.
export function buildScopedStagedTree(repoPath: string, parentOid: string, allowedFiles: readonly string[]): string {
  // One whole-index read; -z emits literal paths (no core.quotePath quoting).
  // Records are "<mode> <oid> <stage>\t<path>" separated by NUL.
  const raw = runGit(repoPath, ['ls-files', '--stage', '-z']);
  const index = new Map<string, { mode: string; oid: string; stage: string }[]>();
  for (const record of raw.split('\0')) {
    if (record.length === 0) continue;
    const tab = record.indexOf('\t');
    if (tab === -1) continue;
    const [mode, oid, stage] = record.slice(0, tab).split(/\s+/);
    const p = record.slice(tab + 1);
    const list = index.get(p) ?? [];
    list.push({ mode, oid, stage });
    index.set(p, list);
  }

  const tmpIndex = path.join(os.tmpdir(), `ironclaude-scoped-index-${process.pid}-${Date.now()}`);
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_INDEX_FILE: tmpIndex };
  try {
    runGitEnv(repoPath, ['read-tree', parentOid], env); // temp index = parent tree
    let realRoot: string | undefined;
    for (const entry of allowedFiles) {
      let rel = entry;
      if (path.posix.isAbsolute(entry)) {
        realRoot ??= realpathSync.native(repoPath);
        const inside = repoRelativeFromAbsolute(realRoot, entry);
        if (inside === null) continue; // outside the repository: no commit here can contain it
        rel = inside;
      }
      if (rel === '' || rel.endsWith('/') || path.posix.isAbsolute(rel) || rel !== path.posix.normalize(rel)) {
        throw new Error(`Cannot scope commit: allowed_files entry '${rel}' is not a canonical repo-relative path (no leading ./, no .., no //, no trailing slash)`);
      }
      const entries = index.get(rel);
      if (entries && entries.some((e) => e.stage !== '0')) {
        throw new Error(`Cannot scope commit: '${rel}' has an unresolved merge conflict (unmerged index entry); resolve it before committing`);
      }
      const staged = entries?.find((e) => e.stage === '0');
      if (staged) {
        runGitEnv(repoPath, ['update-index', '--add', '--cacheinfo', `${staged.mode},${staged.oid},${rel}`], env);
      } else {
        runGitEnv(repoPath, ['update-index', '--force-remove', '--', rel], env); // staged deletion or unstaged/absent
      }
    }
    return runGitEnv(repoPath, ['write-tree'], env).trim();
  } finally {
    try { rmSync(tmpIndex, { force: true }); } catch { /* best effort */ }
  }
}
