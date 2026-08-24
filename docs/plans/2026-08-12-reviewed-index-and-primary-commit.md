# Reviewed Index and Primary Commit Implementation Plan

> **For Claude and Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` task by task.

**Goal:** Preserve cumulative reviewed Git bytes across index disruption, authorize exact commits from managed and unassigned primary checkouts, and make review-checklist loading canonical and fail-closed.

**Requirements:** `docs/plans/2026-08-12-reviewed-index-and-primary-commit-requirements.md`

**Architecture:** State-manager owns cumulative reviewed-tree receipts because it alone can seal a receipt and advance an A/B verdict in one SQLite transaction. A trusted temporary-index preparer builds each review candidate from the latest receipt plus submitted task paths. Workspace-manager restores and validates that receipt before `UserPromptSubmit` records commit authority; managed commits retain their integration path, while an unassigned-primary `/commit` advances only the exact authorized local branch.

**Tech stack:** TypeScript, Node.js, `better-sqlite3`, real Git repositories, Bash hooks, Python/pytest contract tests, Vitest, Codex and Claude plugin CLIs.

## Execution invariants

- Each shell step is independent. Use literal absolute paths; never rely on exported state.
- Bash may start in `commander/`. Use `git -C /Users/roberthyatt/Code/ironclaude` and absolute write paths.
- Quote globs under zsh. Do not use foreground `sleep`.
- `docs/` is ignored; stage workflow artifacts with `git add -f`.
- Evidence retains stderr and distinguishes no match from command failure.
- RED checks must fail when target behavior is absent. GREEN reruns the same selection.
- Preserve commands, paths, errors, hashes, schemas, and authority fields exactly.
- Run exactly one blind plan review after `create_plan`. Repair findings in this plan, record `advisor-remediated`, and never run a second blind plan review.
- Fix execution defects in the active task or bounded follow-up tasks. Do not retreat for execution defects.
- `Monitor` enforcement and unrelated roadmap work remain out of scope.
- Reinstallation is the final source/deployment mutation. Only same-task read-only proof follows.

## Task 1: Add durable reviewed-tree receipt storage

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/db.ts`
- Modify: `worker/mcp-servers/state-manager/src/types.ts`
- Create: `worker/mcp-servers/state-manager/src/review-receipts.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`
- Modify: `worker/hooks/session-init.sh`
- Include staged artifacts: `docs/plans/2026-08-12-reviewed-index-and-primary-commit-design.md`
- Include staged artifacts: `docs/plans/2026-08-12-reviewed-index-and-primary-commit-requirements.md`
- Include staged artifacts: `docs/plans/2026-08-12-reviewed-index-and-primary-commit.md`
- Include staged artifacts: `docs/plans/2026-08-12-reviewed-index-and-primary-commit.plan.json`

### Step 1: Write RED schema and lifecycle tests

Test re-runnable migration, one active and one pending receipt per session/repository, A/B supersession, C/D/F pending retirement, nullable managed binding, complete metadata round-trip, and `session-init.sh` schema parity.

Use this narrow type:

```ts
export type ReviewReceiptStatus = 'pending' | 'active' | 'retired';
export interface ReviewReceipt {
  id: number;
  terminal_session: string;
  repository_identity: string;
  checkout_path: string;
  checkout_mode: 'managed' | 'primary';
  workspace_guid: string | null;
  plan_lineage: number;
  wave_number: number;
  task_ids: string;
  parent_oid: string;
  tree_oid: string;
  receipt_ref: string;
  status: ReviewReceiptStatus;
  created_at: string;
  sealed_at: string | null;
  retired_at: string | null;
}
```

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts
```

Expected: nonzero; tests name missing receipt schema and CRUD.

### Step 2: Implement schema and bounded CRUD

Add a re-runnable `review_receipts` migration and matching first-run hook schema. Use parameterized SQL and partial unique indexes. Implement create pending, read active/pending, promote pending while retiring prior active, retire pending, and retire active after a verified terminal event.

### Step 3: Run GREEN regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/tools/write-tools.test.ts
```

Expected: exit zero.

### Step 4: Stage Task 1

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/review-receipts.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts worker/hooks/session-init.sh
git -C /Users/roberthyatt/Code/ironclaude add -f -- docs/plans/2026-08-12-reviewed-index-and-primary-commit-design.md docs/plans/2026-08-12-reviewed-index-and-primary-commit-requirements.md docs/plans/2026-08-12-reviewed-index-and-primary-commit.md docs/plans/2026-08-12-reviewed-index-and-primary-commit.plan.json
```

Expected: only Task 1 files and four workflow artifacts are newly staged.

## Task 2: Prepare and seal cumulative review candidates

**Depends on:** Task 1

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/review-receipts.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`

### Step 1: Add RED real-Git tests

Cover initial bootstrap from `HEAD` plus all current-plan `review_passed` and submitted tasks; later candidates from the active receipt plus submitted paths; stash/pop unstaging; newest-review precedence; additions, modifications, deletions, renames, modes, and symlinks; invalid or escaping paths; unrelated staged entries; concurrent index mutation; reachable receipt objects; and verdict atomicity.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts src/tools/write-tools.test.ts
```

Expected: nonzero; `prepare_review_candidate` and sealing are absent.

### Step 2: Implement temporary-index preparation

Add `prepare_review_candidate(repository_path)` as a write tool. Accept no model-supplied paths, task IDs, tree IDs, or evidence. Derive task paths from authenticated state rows. Build under unique `GIT_INDEX_FILE`; start from active receipt or `HEAD`; apply exact paths with argument arrays; reject unrelated staged paths; create immutable namespaced receipt ref; compare pre-state; promote through Git index locking; persist pending metadata.

When no receipt exists, include all current-plan review-passed tasks plus current submitted tasks. This bootstraps the feature after its terminal reinstall.

### Step 3: Seal inside `record_review_verdict`

For A/B task reviews, require one matching pending receipt; re-observe repository, branch, parent, checkout mode, and `write-tree`; promote receipt and advance tasks in one SQLite transaction. Seal failure records no passing grade and advances nothing. C/D/F retires pending, preserves prior active receipt, and retains existing reopen behavior. Standalone review does not change receipts.

### Step 4: Run GREEN regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts src/tools/write-tools.test.ts src/__tests__/mark-executing-review-gate.test.ts src/__tests__/review-pending-flavor-b.test.ts
```

Expected: exit zero.

### Step 5: Stage Task 2

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/review-receipts.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.test.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts
```

## Task 3: Enforce review preparation and canonical checklist loading

**Depends on:** Task 2

**Files:**
- Modify: `worker/skills/code-review/SKILL.md`
- Modify: `commander/tests/test_review_checklist.py`
- Modify: `commander/tests/test_rules_references_resolve.py`
- Create: `commander/tests/test_review_receipt_skill.py`

### Step 1: Add RED contract tests

Require candidate preparation before staged-diff reads; bounded repository-only input; stop on preparation failure; checklist resolution from active installed `SKILL.md` root independent of cwd; and missing/unreadable checklist as infrastructure failure with no grade. Reject optional `(if it exists)` and generic fallback.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_review_receipt_skill.py tests/test_review_checklist.py tests/test_rules_references_resolve.py -q
```

Expected: nonzero; preparation and mandatory canonical loading are absent.

### Step 2: Update code-review

Before `git diff --staged`, call provider-native `prepare_review_candidate` with canonical repository root. Treat tool errors as infrastructure failures. Resolve `<active-plugin-root>/rules/review-checklist.md` from the exact active `code-review/SKILL.md` path supplied by the skill loader. Missing/unreadable content stops before grading. Keep review report-only and preserve C/D/F return to execution.

### Step 3: Run GREEN regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_review_receipt_skill.py tests/test_review_checklist.py tests/test_rules_references_resolve.py tests/test_executing_plans_skill.py -q
```

Expected: exit zero.

### Step 4: Stage Task 3

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/skills/code-review/SKILL.md commander/tests/test_review_checklist.py commander/tests/test_rules_references_resolve.py commander/tests/test_review_receipt_skill.py
```

## Task 4: Restore reviewed index before managed human intent

**Depends on:** Task 3

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts`
- Create: `worker/mcp-servers/workspace-manager/src/reviewed-index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/reviewed-index.test.ts`
- Modify: `worker/hooks/tests/test-git-authority-activation.sh`

### Step 1: Add RED migration and restoration tests

Cover nullable `human_intents.workspace_guid` migration with preserved rows/indexes/expiry/foreign keys; managed commit and commit-and-push receipt restoration; stash/pop unstaging; content, mode, deletion, parent, branch, repository, checkout-mode, ref, and object drift; unrelated staged paths; lock races; and unchanged push-only behavior.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/db.test.ts src/__tests__/reviewed-index.test.ts
```

Expected: nonzero; nullable binding and restoration are absent.

### Step 2: Implement trusted receipt restoration

Read `STATE_MANAGER_DB_PATH` or `~/.claude/ironclaude.db`. Resolve one active receipt for authenticated session/repository, validate every field and Git object, compare reviewed paths with live bytes/modes, reconstruct through a temporary index, reject unexpected staging, and promote under index locking. Accept no model evidence; never stash, reset, rewrite working bytes, or infer missing receipt state.

### Step 3: Integrate prompt issuance

For managed commit and commit-and-push, restore before `issueDirectGitHumanIntent`; then let existing evidence capture the exact tree. Failure creates no intent. Preserve exact command recognition, session binding, single-use matching, expiry, and no conversation-visible evidence. Keep push-only unchanged.

### Step 4: Run GREEN regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/db.test.ts src/__tests__/reviewed-index.test.ts src/__tests__/git-authority.test.ts
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh
```

Expected: both commands exit zero.

### Step 5: Stage Task 4

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/workspace-manager/src/db.ts worker/mcp-servers/workspace-manager/src/types.ts worker/mcp-servers/workspace-manager/src/reviewed-index.ts worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts worker/mcp-servers/workspace-manager/src/__tests__/reviewed-index.test.ts worker/hooks/tests/test-git-authority-activation.sh
```

## Task 5: Authorize exact unassigned-primary commits

**Depends on:** Task 4

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/reviewed-index.test.ts`
- Modify: `worker/skills/commit/SKILL.md`
- Modify: `commander/tests/test_git_authority_skill_parity.py`

### Step 1: Add RED primary acceptance and denial tests

Prove unassigned-primary exact human `/commit`; optional GUID only for commit; primary commit-and-push/push denial; exact tree/parent/ref and unchanged working bytes; no remote command; unchanged managed integration; every identity/ref/mode/replay/expiry/subagent/model denial; no assignment mutation; receipt retirement; and commit preservation if receipt cleanup fails.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/git-authority.test.ts src/__tests__/integration-core.test.ts src/__tests__/tool-dispatch.test.ts src/__tests__/reviewed-index.test.ts
```

Expected: nonzero; commit requires GUID and managed assignment.

### Step 2: Add explicit checkout bindings

Supplied GUID retains exact managed validation. Omitted GUID is allowed only for `commit`; require zero nonterminal assignments for session/repository, canonical primary path, and `checkoutMode: primary`. Include receipt ID/ref in evidence. Reject checkout-mode transition at issuance, consumption, revalidation, or commit.

### Step 3: Add exact primary commit

Branch before `exactAssignment`. Managed authority follows existing coordinator. Primary authority validates tree, parent, branch, and local ref; uses `commit-tree` and compare-and-swap `update-ref`; verifies `HEAD`; retires receipt; and returns explicit primary-local evidence. Never invoke integration, cleanup, recycle, assignment mutation, or push.

### Step 4: Update commit skill

Accept verified managed assignment or verified unassigned-primary status. Pass `workspace_guid` only for managed mode. Preserve exact human invocation, server-held authority, and no-push language. Do not broaden primary commit-and-push or push.

### Step 5: Run GREEN regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/git-authority.test.ts src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts src/__tests__/tool-dispatch.test.ts src/__tests__/reviewed-index.test.ts
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_git_authority_skill_parity.py tests/test_worker_worktree_authority.py -q
```

Expected: both commands exit zero.

### Step 6: Stage Task 5

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/workspace-manager/src/git-authority.ts worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/types.ts worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/workspace-manager/src/__tests__/reviewed-index.test.ts worker/skills/commit/SKILL.md commander/tests/test_git_authority_skill_parity.py
```

## Task 6: Document, build, verify, and cachebust

**Depends on:** Task 5

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify generated: `worker/mcp-servers/state-manager/dist/index.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

### Step 1: Update documentation

Add `[Unreleased]` notes for receipts, pre-intent reconstruction, unassigned-primary commit, unchanged push authority, canonical checklist failure, and recovery errors. Update README command and primary-checkout descriptions without implying activation allocates worktrees.

### Step 2: Build tracked bundles

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: both exit zero and refresh four tracked bundles.

### Step 3: Run focused suites

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/tools/write-tools.test.ts src/__tests__/mark-executing-review-gate.test.ts src/__tests__/review-pending-flavor-b.test.ts
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/db.test.ts src/__tests__/reviewed-index.test.ts src/__tests__/git-authority.test.ts src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts src/__tests__/tool-dispatch.test.ts
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_review_receipt_skill.py tests/test_review_checklist.py tests/test_rules_references_resolve.py tests/test_executing_plans_skill.py tests/test_git_authority_skill_parity.py tests/test_worker_worktree_authority.py -q
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh
```

Expected: every focused command exits zero.

### Step 4: Run complete repository suite

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

Expected: exit zero. Fix deterministic defects in place or add bounded follow-ups; do not retreat or request another blind plan review.

### Step 5: Replace only Codex cachebuster

Run exactly once:

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: base stays `1.1.6`; manifest has one new `1.1.6+codex.<UTC timestamp>`.

### Step 6: Validate plugin and versions

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_version_consistency.py -q
```

Expected: exit zero.

### Step 7: Stage and verify candidate

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- README.md CHANGELOG.md worker/.codex-plugin/plugin.json worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: cached diff check exits zero; every changed path belongs to Tasks 1-6.

## Task 7: Reinstall IronClaude last and prove same-task runtime

**Depends on:** Task 6

**Files:**
- Verify only: `worker/.codex-plugin/plugin.json`

**No source edits are permitted.** Any required source repair returns to Task 6 before installation.

### Step 1: Capture intended runtime evidence

Read final manifest version and SHA-256 for manifest, state bundle, workspace bundle, CLI bundle, and hook-intent bundle.

Expected: all files and hashes exist; base remains `1.1.6` with one cachebuster.

### Step 2: Reinstall Claude first

```bash
claude plugin uninstall ironclaude@ironclaude --scope user --keep-data --yes
claude plugin install ironclaude@ironclaude --scope user
claude plugin details ironclaude@ironclaude
```

Expected: install succeeds; updated code-review and commit skills plus both MCP servers appear.

### Step 3: Verify restart helper

```bash
shasum -a 256 /private/tmp/restart_codex.py
```

Expected exactly:

```text
d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f  /private/tmp/restart_codex.py
```

### Step 4: Arm delayed quit/relaunch

```bash
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3", "/private/tmp/restart_codex.py"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'
```

Expected: exit zero.

### Step 5: Reinstall Codex as final mutation

Run exactly once:

```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: JSON reports exact intended `1.1.6+codex.<cachebuster>`. No source, config, Git, build, deployment, or installation mutation follows.

### Step 6: Resume same task and prove runtime

After relaunch, reopen task `019fc5e5-fc72-7493-b785-bee8cda62b1b`. Call `run_diagnostics` with exact intended plugin root, version, client, and five captured hashes. Require every check to pass and every path to use the new cache. Call `get_resume_state` and `get_workspace_status` read-only; require same provider-root session, professional mode on, preserved plan/task state, and truthful primary-checkout status.

### Step 7: Exercise receipt bootstrap

Submit Task 7 and invoke task-boundary code review once. Newly installed code-review must call `prepare_review_candidate`. With no active receipt, it bootstraps from all prior review-passed tasks plus Task 7, seals A/B, and leaves cumulative staged tree intact.

Expected: Task 7 is `review_passed`; active receipt binds same session, repository, parent, cumulative tree, and lineage. No second blind plan review runs.

## Completion criteria

- All seven tasks receive Grade A or B.
- Index disruption cannot silently remove earlier reviewed bytes from authorized commit tree.
- Managed and unassigned-primary `/commit` consume exact human authority; `/commit` never pushes.
- Commander and push boundaries remain unchanged.
- Review cannot grade without canonical checklist.
- Full tests and plugin validation pass.
- Claude and Codex reinstall; Codex restarts; same task proves new runtime.
- Changes remain staged. Commit or push requires a later exact operator command.
