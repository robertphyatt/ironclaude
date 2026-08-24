# Wave Advancement and Reviewed Carry Recovery Design

> **Created:** 2026-08-15
> **Status:** Design Complete

## Summary

This bounded recovery repairs two defects that blocked the professional-mode-off release. First, `get_next_tasks` advanced `current_wave` while the workflow remained in `reviewing`, so `mark_executing` demanded a passing grade for the new, unexecuted wave. Second, Task 1 review preparation correctly restored active receipt `54` but removed four newer paths that no reviewed task owned: `README.md`, `CHANGELOG.md`, and the current human and machine plans.

The recovery uses one two-task lineage. Task 1 makes reviewed-wave advancement transactional and seals the state-manager repair plus this recovery's design and requirements atop receipt `54`. Task 2 carries the four exact working-tree paths and fresh Codex manifest into reviewed scope, revalidates all six documentation artifacts, rebuilds verification evidence, reinstalls IronClaude, and completes task review.

The operator authorized exactly one mandatory blind plan review for this lineage. No later blind plan review or replacement lineage is permitted. Plan-review findings must be repaired in place with one advisor-remediation pass. Task code re-review remains available after C, D, or F task verdicts.

## Architecture

### Transactional reviewed-wave advancement

`get_next_tasks` treats review evidence, wave selection, task creation, and workflow-stage change as one transaction.

When the current wave is fully `review_passed` and the session remains in `reviewing`, the operation must first verify one task-boundary A or B verdict for that same wave. It then computes the successor. If another wave exists, it creates its task rows, advances `current_wave`, changes `workflow_stage` to `executing`, clears `review_pending` and `review_block_count`, and resets review-scoped testing state atomically. If no task remains, it enters `execution_complete` with the same reviewed-wave proof.

The normal client sequence remains valid:

1. Record A or B.
2. Call `mark_executing`.
3. Call `get_next_tasks`.

The recovery path makes the reversed final two calls safe. It does not weaken the grade gate: missing, informational, failing, malformed, or wrong-wave evidence refuses before mutation.

### Reviewed carry and release

Active receipt `54` remains the cumulative reviewed base. Task 1 adds the state-manager repair, its generated bundle, the existing pre-cachebuster manifest, and this recovery's two new documentation artifacts. Its A or B review seals a new active receipt before any cachebuster or bootstrap installation occurs. This order lets C, D, or F repair and re-review Task 1 without reusing a cachebuster or stale runtime oracle.

Task 2 explicitly owns:

- `README.md`
- `CHANGELOG.md`
- `docs/plans/2026-08-14-professional-mode-off-operator-authority.md`
- `docs/plans/2026-08-14-professional-mode-off-operator-authority.plan.json`
- this recovery design
- this recovery requirements contract
- `worker/.codex-plugin/plugin.json`

The two existing plan paths become this recovery's human and machine plans. This avoids creating another unowned plan pair. Task 1 owns and seals the new recovery design and requirements so its review preparation cannot reject them as unexpected staged paths. Task 2 retains all six documentation paths in its declared scope, stages their exact preserved working-tree bytes into a candidate built from Task 1's active receipt, and may repair only those paths or the manifest after a task-review C, D, or F.

## Components and Data Flow

### 1. Wave advancement transaction

The state-manager write-tool handler reads the current session, current-wave task rows, and current-wave review grade inside one transaction. It verifies that every current-wave task passed and that a task-boundary A or B exists when the caller remains in `reviewing`.

It computes the next wave only after those checks. It creates successor task rows and updates `current_wave`, `workflow_stage`, and review flags together. Audit history records the reviewed source wave, target wave, and stage transition. A repeated call returns the existing wave or terminal state without duplicating tasks or consuming another grade.

### 2. State-manager tests and bundle

Focused tests reproduce the observed Wave 1 to Wave 2 deadlock and cover normal ordering, reversed ordering, terminal completion, missing or failing verdicts, wrong-wave evidence, idempotency, and transaction rollback. The tracked state-manager bundle is rebuilt from the reviewed source.

### 3. Exact carry candidates

Before execution, the main session records the exact working-tree blob IDs and modes of all six documentation paths. Task 1 review preparation starts from receipt `54`, stages its source, test, bundle, pre-cachebuster manifest, design, and requirements, and seals them before runtime mutation. Task 2 verifies all six documentation paths against the pre-execution oracle, verifies the fresh manifest against bootstrap evidence, and stages only its seven declared paths. Unexpected entries fail without changing the real index.

### 4. Release evidence

Task 1 completes source work, focused and full verification, its final source build, submission, and task review while the cachebuster remains unchanged. C, D, or F repairs and re-reviews Task 1 before runtime mutation. After A or B, the main session uses the existing runtime to call `mark_executing`, replaces only the Codex cachebuster suffix exactly once, rebuilds the unchanged source, and requires the bundle hash to equal the reviewed bundle hash. It then records bootstrap evidence, installs the repaired Codex runtime, verifies the same session and reviewed Task 1, and calls `get_next_tasks` to release Task 2. That exact version is reused through Task 2 and final reinstall.

Task 2 is certification-only outside its seven declared paths. Task 1's full repository suite is the final source certification; Task 2 reruns the affected release-acceptance suites, proves the reviewed bundle hash did not move, and regenerates the two-client runtime oracle without another cachebuster change. Plugin validation, version consistency, and live PM-off acceptance must pass before final installation. A failure requiring changes outside Task 2's allowed files stops before installation and preserves evidence; it does not authorize an undeclared edit, another lineage, or another blind review.

Claude and Codex reinstall as the final source, plugin, and runtime mutation. Read-only post-restart proof verifies installed roots, manifests, bundles, diagnostics, provider-root session identity, and active Task 2 before task submission and review. Claude's local-path marketplace is an explicit development-mode exception: the user-scope cache remains independently byte-verified, while the live runtime may report the marketplace's canonical `worker/` source root only when that root's manifest and all four bundles match the same final oracle. Codex must continue to report its cache root exactly.

## Failure Handling

- Missing or invalid current-wave A/B evidence refuses before changing tasks, wave, stage, receipts, or audit history.
- A next-wave insertion or session-update failure rolls back the complete transaction.
- Repeated advancement neither duplicates task rows nor writes another grade.
- Receipt, index, blob, mode, path, or repository drift preserves receipt `54`, refs, index, and working bytes.
- Carry preparation never reconstructs bytes from `HEAD`, resets files, stashes work, or expands scope implicitly.
- Blind plan-review findings use the single authorized verdict and one coherent advisor-remediation pass. No second blind plan review runs.
- Task 1 C, D, or F occurs before cachebusting. It reopens only Task 1, whose source, test, bundle, manifest, design, and requirements remain repairable and reviewable without stale runtime evidence.
- Task 2 C, D, or F may repair only Task 2's seven declared paths. A verification or review finding requiring any other source or bundle change stops before further installation and reports the exact blocker.
- A post-install proof failure preserves installation evidence. If Task 2's declared paths caused the failure, the reopened task may repair them, regenerate evidence, and repeat the final install without another lineage or plan review.

## Verification

Tests must prove:

- The exact live deadlock reproduces before the repair.
- Reviewed Wave 1 can release Wave 2 from `reviewing` and atomically return to `executing`.
- The standard `mark_executing` before `get_next_tasks` order still works.
- Missing, informational, C, D, F, malformed, or wrong-wave evidence cannot advance.
- Terminal completion uses the same reviewed-wave proof and remains idempotent.
- Injected transaction failures preserve all original rows and flags.
- Receipt `54` remains the cumulative base for Task 1, and Task 1 review preparation accepts the two new recovery documents because Task 1 declares them.
- Task 2 seals the exact working-tree blobs and modes of all four preserved paths.
- Recovery design, requirements, human plan, and machine plan remain present in the final reviewed candidate.
- Task 1 source and documentation earn A or B before the single fresh cachebuster. Base version remains `1.1.6`; the resulting Codex version is reused unchanged through final reinstall.
- Final source bundles match the regenerated pre-install oracle.
- Focused state-manager, receipt, workflow, hook, Commander, live-provider, plugin-validation, and version tests pass.
- One final full repository suite passes before reinstall.
- Both installed clients match exact versions, hashes, diagnostics, session identity, and task state. Codex reports its cache root exactly. Claude reports either its cache root or the oracle-recorded canonical local-marketplace `worker/` root while its separate user-cache installation and active root both match the same bytes.

## Scope

In scope:

- Transactional reviewed-wave advancement
- Exact four-path reviewed carry
- Recovery design and requirements ownership
- State-manager rebuild and affected verification
- Single fresh-cachebuster bootstrap, final reinstall, and runtime proof

Out of scope:

- Worktree lifecycle automation
- GBTW context-anxiety handling
- New professional-mode-off behavior
- General plan-amendment APIs
- Broad receipt-policy changes
- Commit or push
