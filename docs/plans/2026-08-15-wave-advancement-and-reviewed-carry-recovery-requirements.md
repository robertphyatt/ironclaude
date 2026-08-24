# Wave Advancement and Reviewed Carry Recovery Requirements

> **Created:** 2026-08-15
> **Status:** Operator Approved

## R1. Recovery boundary

1. The implementation must use one bounded recovery lineage.
2. The lineage must receive exactly one mandatory blind plan review.
3. No later blind plan review or replacement lineage may occur.
4. Blind-review findings must be repaired in place with one advisor-remediation pass.
5. Task-level C, D, or F findings may reopen and re-review only the affected task.

## R2. Reviewed-wave advancement

1. `get_next_tasks` must not advance from `reviewing` without a task-boundary A or B verdict for the current reviewed wave.
2. When a successor exists, the operation must create successor tasks, advance `current_wave`, enter `executing`, clear review flags, and reset review-scoped testing state atomically.
3. When no successor exists, the operation must enter `execution_complete` using the same reviewed-wave evidence.
4. Missing, informational, C, D, F, malformed, or wrong-wave evidence must refuse without mutation.
5. The existing `mark_executing` then `get_next_tasks` sequence must remain valid.
6. Repeated calls must not duplicate task rows, consume another grade, or change a completed result.
7. Transaction failure must preserve session fields, wave tasks, grades, receipts, and audit history.

## R3. Preserved evidence

1. Active receipt `54` must remain the cumulative base until Task 1 review replaces it through the normal A or B seal.
2. The recovery must preserve all current working-tree bytes, index entries, refs, prior review evidence, test evidence, and runtime oracle unless an approved task explicitly replaces that evidence.
3. Task 1 must complete source work, final build, submission, and A or B review before changing the cachebuster. Only then may the recovery replace the Codex suffix exactly once, rebuild byte-identical reviewed source for installation evidence, preserve base version `1.1.6`, and reuse that exact resulting version through bootstrap, Task 2, and final reinstall.
4. It must not reset, stash, discard, or reconstruct preserved bytes from `HEAD`.

## R4. Exact reviewed carry

Task 2 must own and seal the exact working-tree blobs and modes of:

1. `README.md`
2. `CHANGELOG.md`
3. `docs/plans/2026-08-14-professional-mode-off-operator-authority.md`
4. `docs/plans/2026-08-14-professional-mode-off-operator-authority.plan.json`

Task 1 must own and seal this recovery design and requirements contract so its candidate can be prepared atop receipt `54`. Task 2 must also own and revalidate both artifacts with the four preserved paths and fresh manifest. The existing two professional-mode-off plan paths must serve as the recovery human and machine plans.

## R5. Receipt and index safety

1. Review preparation must start from the latest active receipt.
2. It must stage only the submitted task's declared paths.
3. Unexpected staged paths must fail without changing the real index.
4. Blob, mode, path, repository, parent, branch, checkout, receipt, or index drift must fail closed.
5. Missing receipt objects must remain review-infrastructure failures; live bytes must never substitute for reviewed evidence.

## R6. Build and release

1. Task 1 must rebuild the tracked state-manager bundle after the source repair, record its reviewed hash, and require the post-cachebuster rebuild to reproduce that hash exactly.
2. Task 1 C, D, or F repair must finish before cachebusting or bootstrap installation. After A or B, the main session must call `mark_executing`, cachebust once, rebuild, prove byte identity, install the Codex bootstrap, verify the same session and reviewed task, and call `get_next_tasks` to release Task 2.
3. Task 2 must regenerate the two-client runtime oracle from final bytes without changing Task 1's cachebuster.
4. Task 2 may repair only its seven declared files. Any failed verification or review that requires another source or bundle path must stop before installation without an undeclared edit, another lineage, or another blind review.
5. Plugin validation, version consistency, live PM-off acceptance, affected focused suites, and one full repository suite must pass before installation.
6. Reinstall must remain the final source, plugin, and runtime mutation.
7. Post-restart work must be limited to read-only installed-byte proof, diagnostics, session/task identity verification, task submission, and task review.
8. No commit or push may occur.

## R7. Client and runtime proof

1. Claude must install base version `1.1.6` at one verified user-scope cache root. When Claude's registered marketplace is a local path, its live runtime may resolve to that marketplace's canonical `worker/` source root instead of the cache only when the oracle records both roots, every active manifest and bundle hash matches the final source bytes, and the separately installed cache inventory also matches byte-for-byte.
2. Codex must resolve to the exact cachebuster recorded after Task 1's single cachebuster update at its verified cache root.
3. Both clients' installed manifests and state-manager, workspace-manager server, CLI, and hook-intent bundles must match the pre-install oracle. Any distinct active Claude local-marketplace root must match the same hashes.
4. Both clients' diagnostics must report activation match.
5. Codex proof must bind the same provider-root session and active Task 2.

## R8. Exclusions

This recovery must not implement worktree lifecycle automation, GBTW context-anxiety handling, new PM-off behavior, a general plan-amendment API, broad receipt-policy changes, commit, or push.
