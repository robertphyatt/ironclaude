# Commit Scoping for Absolute allowed_files Design (v1.1.16 L2c)

> **Created:** 2026-10-03
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-03-commit-scoping-absolute-requirements.md`
> **Loop:** full. This loop changes the workspace-manager commit path.

## Summary

Commit scoping now reads absolute `allowed_files` entries the same way the write guard and the drift check do. An entry inside the repository is scoped by its repo-relative path. An entry outside the repository is skipped, because no commit can contain it. Relative entries behave exactly as before.

## Approach

The resolution happens in a single place, `buildScopedStagedTree`, so all three callers pick it up. Resolving real paths makes symlink and case spellings agree with git's view of the tree.

Rejected alternative: skipping every absolute entry. That would silently drop in-repo files a plan lists by absolute path.

## Components

1. **Resolve absolute entries in `scoped-tree.ts`.** Add a small helper that resolves an absolute entry against the repository root:
   - `realRoot` is `fs.realpathSync.native(repoRoot)`.
   - For the entry, walk up to its longest existing ancestor, call `fs.realpathSync.native` on that ancestor, and rejoin the missing remainder.
   - If the resolved path starts with `realRoot + path.sep`, return `path.relative(realRoot, resolved)` converted to POSIX separators.
   - Otherwise, including when it equals `realRoot`, return `null`. The caller then skips the entry.

   The existing canonical check runs on the returned relative path, so an in-repo entry still has to be canonical. The plan confirms where the repository root comes from at each call site (`repoPath`).
2. **Tests in `scoped-tree.test.ts`.** Each test is written to fail first, then pass. It uses a temp git repo plus a symlink to that repo. The cases:
   - an in-repo absolute entry is scoped;
   - an out-of-repo absolute entry is skipped, while its relative siblings are scoped;
   - a symlinked root spelling is scoped;
   - the existing rejections of `./a.ts` and `dir/` still throw;
   - the old `/abs/a.ts`-throws assertion becomes skipped.
3. **Build and deploy.** Rebuild the workspace-manager's committed build output, since the plan verifies that `dist/` and the bundle are tracked. Then refresh the Codex cache with `codex plugin add` and `diff -rq`, and restart the Commander.

## Error Handling

If resolving a path fails because an ancestor is unreadable, the entry is treated as outside the repo and skipped. This is safe: a file nobody can stat cannot be committed anyway.

A malformed relative entry still throws, as it does today.

## Testing Strategy

- The new and updated `scoped-tree` tests go from RED to GREEN.
- Run the full workspace-manager test suite.
- Run the Commander suite.
- Redeploy.
- Hold the final Fable end review.
- Commit through `/commit` as the live proof that the fix works.
