# Provider-Neutral Review Entry and Recovery Implementation Plan

> **Execution mode:** One bounded bootstrap task. A Terra subagent implements and verifies repository changes. The main session alone owns workflow transitions, the single blind plan review, plugin reinstall/restart, runtime proof, task submission, and code-review invocation.

**Goal:** Repair Claude Code and Codex task-review entry so native invocation, exact retry, testing-theatre state, retreat cleanup, and self-hosted review work without manual database or operator intervention.

**Requirements:** `docs/plans/2026-08-13-provider-neutral-review-entry-requirements.md`

**Design:** `docs/plans/2026-08-13-provider-neutral-review-entry-design.md`

**Constraints:** Exactly one blind plan review. No commit or push. Reinstall is the final mutation. Preserve the unstaged `worker/hooks/state-activator.sh` byte-for-byte and do not stage it. Carry forward the already staged reviewed-index candidate; do not reimplement its completed Tasks 1-6.

## Task 1: Bootstrap provider-neutral review and complete same-task acceptance

**Allowed files (exact; repair files and carried reviewed candidate):**

- `CHANGELOG.md`
- `README.md`
- `commander/tests/test_executing_plans_skill.py`
- `commander/tests/test_git_authority_skill_parity.py`
- `commander/tests/test_guard_block_messages.py`
- `commander/tests/test_review_checklist.py`
- `commander/tests/test_review_entry_client_parity.py`
- `commander/tests/test_review_receipt_skill.py`
- `docs/plans/2026-08-12-reviewed-index-and-primary-commit-design.md`
- `docs/plans/2026-08-12-reviewed-index-and-primary-commit-requirements.md`
- `docs/plans/2026-08-12-reviewed-index-and-primary-commit.md`
- `docs/plans/2026-08-12-reviewed-index-and-primary-commit.plan.json`
- `docs/plans/2026-08-13-provider-neutral-review-entry-design.md`
- `docs/plans/2026-08-13-provider-neutral-review-entry-requirements.md`
- `docs/plans/2026-08-13-provider-neutral-review-entry.md`
- `docs/plans/2026-08-13-provider-neutral-review-entry.plan.json`
- `worker/.codex-plugin/plugin.json`
- `worker/hooks/get-back-to-work-claude.sh`
- `worker/hooks/get-back-to-work-impl.sh`
- `worker/hooks/hook-logger.sh`
- `worker/hooks/hooks.json`
- `worker/hooks/plan-task-context.sh`
- `worker/hooks/professional-mode-guard.sh`
- `worker/hooks/session-init.sh`
- `worker/hooks/skill-state-bridge.sh`
- `worker/hooks/test-stop-wrapper.sh`
- `worker/hooks/tests/test-git-authority-activation.sh`
- `worker/hooks/tests/test-review-entry-parity.sh`
- `worker/hooks/tests/test-workflow-transition-idempotency.sh`
- `worker/mcp-servers/state-manager/dist/index.js`
- `worker/mcp-servers/state-manager/src/__tests__/mark-executing-review-gate.test.ts`
- `worker/mcp-servers/state-manager/src/__tests__/review-pending-flavor-b.test.ts`
- `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`
- `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- `worker/mcp-servers/state-manager/src/db.ts`
- `worker/mcp-servers/state-manager/src/review-receipts.ts`
- `worker/mcp-servers/state-manager/src/state-machine.ts`
- `worker/mcp-servers/state-manager/src/tools/write-tools.test.ts`
- `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- `worker/mcp-servers/state-manager/src/types.ts`
- `worker/mcp-servers/workspace-manager/dist/cli.js`
- `worker/mcp-servers/workspace-manager/dist/hook-intent.js`
- `worker/mcp-servers/workspace-manager/dist/index.js`
- `worker/mcp-servers/workspace-manager/package.json`
- `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/reviewed-index.test.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- `worker/mcp-servers/workspace-manager/src/db.ts`
- `worker/mcp-servers/workspace-manager/src/git-authority.ts`
- `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- `worker/mcp-servers/workspace-manager/src/index.ts`
- `worker/mcp-servers/workspace-manager/src/integration.ts`
- `worker/mcp-servers/workspace-manager/src/reviewed-index.ts`
- `worker/mcp-servers/workspace-manager/src/types.ts`
- `worker/skills/code-review/SKILL.md`
- `worker/skills/commit/SKILL.md`
- `worker/skills/executing-plans/SKILL.md`

The workspace-manager files, prior reviewed-index plan artifacts, `commit` skill, and existing authority tests are carry-only unless a deterministic regression from this repair requires a bounded in-scope correction. The sole planned workspace-manager package change limits the full Vitest run to one worker: 15-worker and two-worker runs passed all 215 tests but ended with `[vitest-worker]: Timeout calling "onTaskUpdate"`; a one-worker probe passed 215 tests and exited zero. `worker/hooks/state-activator.sh` is explicitly excluded.

### Step 1: Preserve the candidate boundary and write RED tests

Record the staged path list and cached patch before source edits. Assert that `worker/hooks/state-activator.sh` is unstaged and save its SHA-256 for the final equality check.

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only > /private/tmp/provider-neutral-review-entry-staged-before.txt
git -C /Users/roberthyatt/Code/ironclaude diff --cached --binary > /private/tmp/provider-neutral-review-entry-before.patch
shasum -a 256 /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh > /private/tmp/provider-neutral-review-entry-state-activator.sha256
```

Add failing tests for:

- native Claude and Codex review-entry parity and client-specific instructions;
- exact repeated preparation returning the same receipt with `reused: true`;
- rejection of every session, repository, checkout, workspace, lineage, wave, task, declared-path, branch, parent, ref, object, tree, and index mismatch;
- a persisted digest over canonical submitted task IDs and declared paths;
- successful preparation entering `reviewing`, setting `active_skill=code-review`, and resetting testing-theatre state;
- compensation when candidate or workflow-state mutation fails;
- report-only access for both clients while writes, Git mutations, commits, pushes, and direct advancement remain blocked;
- testing-theatre completion, not skill start, setting the flag;
- C/D/F recovery and unchanged semantic grading;
- transactional retreat cleanup, repeated cleanup, rollback, and preservation of active receipts and Git bytes;
- transactional reconciliation of legacy receipt 22 during the replacement task's first post-repair submission transition, using durable retreat proof; and
- infrastructure failure recording no semantic grade.

Run the narrow RED commands:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/tools/write-tools.test.ts
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-review-entry-parity.sh
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_review_entry_client_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_review_receipt_skill.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_executing_plans_skill.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_guard_block_messages.py -q
```

**Expected:** Each command containing new coverage exits nonzero for the missing provider-neutral behavior, not for fixture or environment errors.

### Step 2: Make review preparation authoritative and retry-safe

Extend state-manager persistence with re-runnable migrations for:

- a canonical declared-scope digest on review candidates; and
- plan-lineage evidence on new plan-history rows.

Build the digest from a stable, sorted representation of submitted task IDs and each task's canonical declared paths. New candidates require it. Legacy candidates without it are never treated as exact retries.

Change `prepare_review_candidate` to:

1. derive authenticated session, repository, checkout, plan, wave, submitted-task, declared-path, branch, parent, ref, object, tree, and staged-index evidence;
2. strictly revalidate and return the existing candidate only when every binding matches;
3. return the same receipt identity with `reused: true` without writing an index, ref, task, or receipt row;
4. create a candidate and enter `reviewing` with `active_skill=code-review` and `testing_theatre_checked=0` as one compensated operation; and
5. restore the original index and remove only newly created candidate evidence on failure.

Make the review workflow the only caller that prepares a candidate. Remove any orchestration instruction to pre-create one.

### Step 3: Make retreat and testing-theatre state transactional

Within the retreat transaction:

- retire pending candidates;
- preserve active receipts and receipt refs;
- reset `review_pending`, `review_block_count`, `testing_theatre_checked`, and `active_skill`;
- store plan lineage in retreat history; and
- leave workflow and receipt state unchanged if any operation fails.

During the replacement task's first post-repair `submit_task` transition, reconcile an older pending candidate only when the same session and repository have durable retreat history proving its different retired lineage. Retire the proven candidate, record an audit entry, and change task status plus review state in the same transaction. Roll back submission if reconciliation fails. Support current legacy receipt 22 through its recorded old task set, lineage, and retreat history. Preserve and report any unproven candidate.

Candidate preparation performs no orphan retirement. It strictly reuses an exact candidate for the current plan or fails closed.

Remove the hook behavior that sets `testing_theatre_checked=1` when the skill starts. Keep the existing completion operation as the only success signal.

### Step 4: Render native review actions and admit the full report-only path

Add one narrow shared renderer for task-review instructions:

- Claude Code: invoke `ironclaude:code-review` with `--task-boundary` through `Skill`.
- Codex: load `$ironclaude:code-review --task-boundary` through the native skill surface.
- Unknown client: explicit infrastructure error.

Use it in review-pending and stop/recovery messages. Do not add a generalized skill router or enable a generic `Skill` tool for Codex.

Allow both clients to complete native skill loading, candidate preparation, staged/source/plan/checklist reads, permitted tests, testing-theatre operations, and verdict recording after successful entry. Keep implementation edits, arbitrary shell, Git mutation, commit, push, and direct advancement blocked. Remove the duplicate adjacent read-only-Git log line if that file is touched.

Update `code-review` and `executing-plans` to describe each client's native action. Keep canonical checklist loading fail-closed and preserve existing grade semantics.

### Step 5: Run focused GREEN verification

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/tools/write-tools.test.ts src/__tests__/mark-executing-review-gate.test.ts src/__tests__/review-pending-flavor-b.test.ts
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/node_modules/.bin/tsc --noEmit --project /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/tsconfig.json
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-review-entry-parity.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-workflow-transition-idempotency.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-bash-readonly-guard.sh
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_review_entry_client_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_review_receipt_skill.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_executing_plans_skill.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_guard_block_messages.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_review_checklist.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_rules_references_resolve.py -q
```

**Expected:** Every command exits zero. Mutation-sensitive tests prove the retry, retreat, client-routing, and write-blocking assertions can fail.

Stabilize the workspace-manager test runner by changing only its `test` script from `vitest run` to `vitest run --maxWorkers=1`. Do not add a Vitest configuration file, change production behavior, or raise global test timeouts. Run the complete workspace-manager suite twice:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test
```

**Expected:** Both commands exit zero, all 215 tests pass in each run, and neither run reports `[vitest-worker]: Timeout calling "onTaskUpdate"`.

### Step 6: Document, test, cachebust, build, and stage the candidate

Update README and `[Unreleased]` notes without adding unrelated product claims. Build once for pre-release testing, then run the repository suite exactly once:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager run build
make -C /Users/roberthyatt/Code/ironclaude test
```

**Expected:** Both exit zero. Fix deterministic defects within this task; do not run a second blind plan review.

Replace the Codex cachebuster exactly once for this candidate, then create final bundles and validate:

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager run build
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_version_consistency.py -q
/usr/bin/python3 -c 'import hashlib,json,pathlib; root=pathlib.Path("/Users/roberthyatt/Code/ironclaude/worker"); version=json.loads((root/".codex-plugin/plugin.json").read_text())["version"]; digest=lambda relative: hashlib.sha256((root/relative).read_bytes()).hexdigest(); expected={"client":"codex","manifest_sha256":digest(".codex-plugin/plugin.json"),"plugin_root":f"/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/{version}","plugin_version":version,"state_manager_bundle_sha256":digest("mcp-servers/state-manager/dist/index.js"),"workspace_manager_bundle_sha256":digest("mcp-servers/workspace-manager/dist/index.js"),"workspace_manager_cli_sha256":digest("mcp-servers/workspace-manager/dist/cli.js"),"workspace_manager_hook_intent_sha256":digest("mcp-servers/workspace-manager/dist/hook-intent.js")}; target=pathlib.Path("/private/tmp/provider-neutral-review-entry-expected-runtime.json"); target.write_text(json.dumps(expected,sort_keys=True)+"\n"); print(target.read_text(),end="")'
```

**Expected:** Base version remains `1.1.6`; cachebuster changes once; bundle, plugin validation, and version tests pass. The printed expected-runtime object contains all eight required fields and values measured from the final source candidate before installation.

Stage only the task allowlist plus carried candidate. Force-stage ignored plan and generated artifacts. Do not stage `worker/hooks/state-activator.sh`.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- CHANGELOG.md README.md commander/tests/test_executing_plans_skill.py commander/tests/test_git-authority_skill_parity.py commander/tests/test_guard_block_messages.py commander/tests/test_review_checklist.py commander/tests/test_review_entry_client_parity.py commander/tests/test_review_receipt_skill.py worker/.codex-plugin/plugin.json worker/hooks/get-back-to-work-claude.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/hook-logger.sh worker/hooks/hooks.json worker/hooks/plan-task-context.sh worker/hooks/professional-mode-guard.sh worker/hooks/session-init.sh worker/hooks/skill-state-bridge.sh worker/hooks/test-stop-wrapper.sh worker/hooks/tests/test-git-authority-activation.sh worker/hooks/tests/test-review-entry-parity.sh worker/hooks/tests/test-workflow-transition-idempotency.sh worker/mcp-servers/state-manager/src/__tests__/mark-executing-review-gate.test.ts worker/mcp-servers/state-manager/src/__tests__/review-pending-flavor-b.test.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/review-receipts.ts worker/mcp-servers/state-manager/src/state-machine.ts worker/mcp-servers/state-manager/src/tools/write-tools.test.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/workspace-manager/package.json worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts worker/mcp-servers/workspace-manager/src/__tests__/reviewed-index.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/workspace-manager/src/db.ts worker/mcp-servers/workspace-manager/src/git-authority.ts worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/reviewed-index.ts worker/mcp-servers/workspace-manager/src/types.ts worker/skills/code-review/SKILL.md worker/skills/commit/SKILL.md worker/skills/executing-plans/SKILL.md
git -C /Users/roberthyatt/Code/ironclaude add -f -- docs/plans/2026-08-12-reviewed-index-and-primary-commit-design.md docs/plans/2026-08-12-reviewed-index-and-primary-commit-requirements.md docs/plans/2026-08-12-reviewed-index-and-primary-commit.md docs/plans/2026-08-12-reviewed-index-and-primary-commit.plan.json docs/plans/2026-08-13-provider-neutral-review-entry-design.md docs/plans/2026-08-13-provider-neutral-review-entry-requirements.md docs/plans/2026-08-13-provider-neutral-review-entry.md docs/plans/2026-08-13-provider-neutral-review-entry.plan.json worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
shasum -a 256 -c /private/tmp/provider-neutral-review-entry-state-activator.sha256
git -C /Users/roberthyatt/Code/ironclaude diff --name-only -- . ':(exclude)worker/hooks/state-activator.sh'
git -C /Users/roberthyatt/Code/ironclaude status --short
```

**Expected:** Cached diff check and hash check pass. The excluded-path diff command prints nothing. `worker/hooks/state-activator.sh` remains the same unstaged modification. Every other changed path is staged and belongs to the task or its carried reviewed candidate.

### Step 7: Reinstall as the final mutation and prove the repaired runtime

The main session performs this step. No subagent may reinstall, restart, submit, or review.

Verify the restart helper before use:

```bash
shasum -a 256 /private/tmp/restart_codex.py
claude plugin uninstall ironclaude@ironclaude --scope user --keep-data --yes
claude plugin install ironclaude@ironclaude --scope user
claude plugin details ironclaude@ironclaude
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3", "/private/tmp/restart_codex.py"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'
codex plugin add ironclaude@ironclaude --json
```

**Expected:** Both clients install the new cachebuster. `codex plugin add` is the final mutation; the delayed helper restarts Codex afterward.

When this same native task resumes, perform only read-only runtime proof, then submit and review:

1. Read `/private/tmp/provider-neutral-review-entry-expected-runtime.json` and pass that exact object as `run_diagnostics.expected_runtime`; do not derive expectations from installed output.
2. Call `get_resume_state` and `get_workspace_status`; prove same provider-root session and task remain active.
3. Submit Task 1 through the normal execution workflow; prove its transition transaction reconciles orphan receipt 22 from durable retreat evidence without direct database edits.
4. Load `$ironclaude:code-review --task-boundary` once. Do not manually call `prepare_review_candidate` first.
5. Prove candidate preparation performs no orphan cleanup and creates or strictly reuses only the current plan's authenticated candidate.
6. Complete testing-theatre detection and record the semantic verdict through the skill.

**Expected:** Codex enters `reviewing`, candidate preparation creates or strictly reuses one receipt, testing-theatre resets and completes, report-only review reads succeed, and an A or B verdict completes the task. Infrastructure failure records no grade.

If task review returns C, D, or F, repair this same task, rerun affected focused tests and one necessary full verification, update the candidate cachebuster, reinstall again as the repair's final mutation, and re-run task review. Do not retreat and do not perform a second blind plan review.
