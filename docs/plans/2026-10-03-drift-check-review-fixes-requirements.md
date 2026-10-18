# Drift-Check Review Fixes (v1.1.16 L2b-1b) Requirements

> **Created:** 2026-10-03
> **Status:** Operator-approved. The operator chose "Fix now, then commit" after the L2b-1 Fable end review.
> **Parent:** `docs/plans/2026-10-03-client-parity-hooks-requirements.md` (L2b-1)

## Verified findings to fix

- **F1 (Important): the drift check reads the wrong directory.**
  - It runs `git status` in the hook `cwd`, not in the session's effective root.
  - The write guard resolves that root from the workspace `assignments` table, through `workspace_bind_effective_root` (`workspace-path-adapter.sh:341-393`).
  - As a result, managed-worktree sessions get false drift blocks from other sessions' edits in the primary checkout. Real drift inside the worktree is never inspected.
- **F2 (Important): absolute paths are not matched.**
  - `allowed_files` matching takes only exact repo-relative strings.
  - The write guard also accepts absolute paths (`professional-mode-guard.sh:780`), and real plans use them.
  - Writes the guard allowed can therefore be blocked at `submit_task`.
- **F3 (Minor): `comm` runs in the inherited locale.** The inputs are sorted with `LC_ALL=C`, but `comm -23` compares them in the inherited locale. That can produce false drift on non-macOS libcs.
- **F4 (Minor): `state-activator.sh` can print more than one JSON object.**
  - Its stdout can hold two objects in the same turn: the warning-queue drain plus a `log_error` or `log_warning` `systemMessage`.
  - The hook never emits decisions; it only emits `systemMessage` and the drain's `hookSpecificOutput`.

## Requirements

- **R1 (fixes F1).**
  - The drift check, at both the baseline and the check, resolves the repository root the same way the write guard does: it calls the adapter's `workspace_bind_effective_root`, which also takes over primary-checkout ownership. No resolution logic is duplicated.
  - With no active assignment for the session, it uses the cwd's repository root, which is today's behavior.
  - If assignment evidence exists but is broken or frozen, it fails open and logs the reason.
  - The baseline and the check must use the same root.
- **R2 (fixes F2).**
  - An absolute `allowed_files` entry under the effective root is converted to its repo-relative form before matching.
  - An absolute entry outside the root can never match a `git status` path, so it is ignored.
  - Matching stays exact; there are no patterns.
- **R3 (fixes F3).** Run `comm` under `LC_ALL=C`.
- **R4 (fixes F4).** `state-activator.sh` emits at most one JSON object per run.
  - Buffer its output.
  - Merge any `systemMessage` texts, joined by newlines, with the drain's `hookSpecificOutput.additionalContext` into one object.
- **R5 (testing).**
  - Each fix gets a RED→GREEN regression test.
  - Every existing hook test and the Commander suite still pass.
  - Then redeploy: `deploy-hooks`, the Claude plugin `hooks.json` copy, `codex plugin add` and the cache diff.
- **R6 (commit).** Only after L2b-1b passes are L2b-1 and L2b-1b committed together, by the operator. Until then, the 4 Codex hooks are not re-trusted.

## Out of scope (backlog)

- Orphaned baseline and queue files.
- The widening of the `subagent-drift-detector` fallback.
- Observations from the end review:
  - `index.lock` at claim time leaves a task without a baseline.
  - Untracked build byproducts count as drift.
  - `ExitPlanMode` is not in the matcher.
