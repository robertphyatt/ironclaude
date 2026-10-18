# Commit Scoping for Absolute allowed_files (v1.1.16 L2c) Requirements

> **Created:** 2026-10-03
> **Status:** Operator-approved.
> **Origin:** the L2b-2 `/commit` was refused. The operator said, "Note that we need to fix this. I'll deactivate pm."

## Operator decisions

1. **Sequencing.** "Own small loop first (Rec.)": L2c runs before L3 so that L3 can commit through `/commit`.
2. **Absolute entries.** "Match guard + drift (Rec.)":
   - Resolve an absolute entry's real path from its longest existing ancestor, so symlink and case spellings resolve.
   - Compare it with the repository's real root.
   - If it is inside the repo, convert it to repo-relative and scope it as usual.
   - If it is outside the repo, skip it.
   - Malformed relative entries stay rejected.
3. **Design approval.** "Yes, write it up (Rec.)".

Standing directives apply as before: one IronClaude for both clients; scope fidelity; no push and no commit by the agent; the final Fable end review must be CLEAN before the operator commits.

## Finding (verified)

`buildScopedStagedTree` (`worker/mcp-servers/workspace-manager/src/scoped-tree.ts:12`, check at `:33-34`) throws when any `allowed_files` entry is absolute:

`Cannot scope commit: allowed_files entry '<path>' is not a canonical repo-relative path …`

That refuses the whole commit. The file guard accepts absolute entries (`professional-mode-guard.sh:780`), and the drift check maps them by file identity. A plan that legitimately edits files outside the repo, such as the operator's global files, the Codex config or a plugin cache, can therefore never be committed through `/commit`.

The function has three callers:
- `git-authority.ts:354`, in `observeUnassignedCommitEvidence`;
- `git-authority.ts:420`;
- `integration.ts:443`, in `validateExactCommitState`.

The existing test `scoped-tree.test.ts:136-147` asserts that `./a.ts`, `/abs/a.ts` and `dir/` all throw.

## Requirements

- **R1.** In `buildScopedStagedTree`, handle each absolute entry as follows:
  1. Resolve its real path. Take the real path of the longest existing ancestor and append the rest of the entry.
  2. Compare the result with the repository root's real path.
  3. If it is strictly inside the root, convert it to the repo-relative path and pass it through the existing checks.
  4. If it equals the root or lies outside it, skip it.

  Relative entries keep today's behavior. That includes rejecting `./`, `..`, `//`, a trailing `/` and the empty string.
- **R2.** Write the tests first so they fail (RED), then pass (GREEN), in `scoped-tree.test.ts`:
  - an absolute entry inside the repo is scoped;
  - an absolute entry outside the repo is skipped, while its relative siblings are still scoped;
  - a symlinked spelling of the repo root maps to the right file;
  - the existing malformed-relative rejections still throw.

  The `/abs/a.ts` case changes from "throws" to "skipped", because it is outside any repo.
- **R3.** Rebuild the workspace-manager build output the repo ships. Run its test suite and the Commander suite. Redeploy: refresh the Codex cache, then restart the Commander.
- **R4.** The final Fable end review must come back CLEAN. The operator then commits through `/commit`, which also shows the fix works.

## Out of scope

- L3, which covers plan amendments and the experiment stage.
- Any change to the guard or to the drift check.
