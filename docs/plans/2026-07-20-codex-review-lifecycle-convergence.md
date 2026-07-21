# Codex Review Lifecycle Convergence Implementation Plan

> **For Claude/Codex:** Use `ironclaude:executing-plans` in inline mode. Task 1 is intentionally one atomic self-update boundary: splitting runtime implementation from reinstall would leave the first task review on the old deadlocking MCP runtime.

**Goal:** Make the existing submit/review/rework lifecycle provider-neutral so a review-ready sequential or final parallel submission enters `reviewing`, A/B returns through `mark_executing changed:true`, and failed review reopens the exact batch atomically without a new MCP subsystem.

**Requirements:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md`

**Design:** `docs/plans/2026-07-20-codex-review-lifecycle-convergence-design.md`

**Estimated memory:** `estimated_memory_gb: 0.5` (source edits and local test/build processes; no direct model inference).

**Architecture:** `submit_task` owns provider-neutral, parallel-safe review coordination: intermediate parallel submissions remain executing with no review gate, while sequential or final parallel submissions enter reviewing atomically. `record_review_verdict` owns passing batch advancement or failed-review batch rework; hooks and shipped review skills enforce strict read-only inspection. All executable changes, tests, final bundle, reinstall, restart, and live review-entry proof ship in one bootstrap task so its own review runs on the corrected runtime. Wave 1/MP-W08 and MP-W11 remain completed prerequisites protected by regressions; roadmap Waves 2–13 remain release-blocking.

**Constraints:** Preserve native task `019f7742-abd8-7c62-af7b-fe07189f1ffd`. No commit or push. Cachebust before final build; reinstall; fully restart Codex; reopen this task before Task 1 submission.

---

## Task 1: Bootstrap the corrected lifecycle into its own review boundary

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/state-machine.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- Modify: `worker/hooks/professional-mode-guard.sh`
- Create: `worker/hooks/tests/test-review-lifecycle-guards.sh`
- Modify: `worker/hooks/tests/test-workflow-transition-idempotency.sh`
- Modify: `worker/skills/executing-plans/SKILL.md`
- Modify: `worker/skills/code-review/SKILL.md`
- Modify: `commander/tests/test_executing_plans_skill.py`
- Create: `commander/tests/test_code_review_skill.py`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify: `worker/mcp-servers/state-manager/dist/index.js`

### 1.1 Add complete RED lifecycle coverage

Using existing database helpers, add current-wave/provider-native-session tests for:

1. transition table permits `executing -> reviewing` and retains `reviewing -> executing` plus same-target no-op behavior;
2. sequential submission and the final submission in a two-task parallel wave return `review_ready:true`, `executing -> reviewing`, `changed:true`, same `session_id`, exact submitted batch, both gate fields, and one task-context audit;
3. the first submission in that parallel wave returns `review_ready:false`, `executing -> executing`, `changed:false`, exact remaining-in-progress count, task `submitted`, both gate fields clear, and leaves its sibling write-capable; a pending unclaimed sibling does not delay sequential review;
4. invalid source stage (including a second submit while `reviewing`), transaction-local stale persisted session stage, invalid task/status, stale transaction-local task/count, and empty task-boundary verdict reject without mutation;
5. intermediate and review-ready submission each roll back independently when task update, session/stage update, or final audit insert fails;
6. informational A–F records grade/audit only, returns `advanced_count:0`, and rolls back on grade or audit failure;
7. A/B re-reads the submitted batch inside its transaction, advances exactly current-wave submitted tasks, clears both flags, preserves `reviewing`, and later allows one `mark_executing` returning `changed:true`; `mark_executing` also requires zero still-submitted current-wave tasks so an older A/B grade cannot authorize return; inject failures independently at grade insert, task update, session flag update, and audit insert and require total rollback;
8. C/D/F re-reads inside its transition transaction, reopens exactly the current-wave submitted batch, clears both flags, returns `reviewing -> executing changed:true`, same session, and exact `rework_count`; inject failures independently at grade insert, task update, session/stage update, and audit insert and require total rollback;
9. task-boundary verdict outside `reviewing`, `claim_task` while `reviewing`, `get_next_tasks` advancement while a submitted review is pending, premature/stale-grade `mark_executing`, and provider/session mismatch remain blocked without mutation; `set_testing_theatre_checked` remains permitted in `reviewing` and changes only that flag plus one audit for the same session;
10. public tool definitions describe exact source stages, intermediate/final batch responses, verdict advancement/rework, and guarded review return.

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/tools/write-tools.test.ts src/__tests__/workflow-transition-idempotency.test.ts
```

Expected RED: only new lifecycle assertions fail.

### 1.2 Add combined RED hook and Commander coverage

Create `test-review-lifecycle-guards.sh` with a temporary HOME/database, provider-native fixture session, registered design and wave-task rows, direct JSON hook inputs, and before/after snapshots. Run `plan-task-context.sh` then `professional-mode-guard.sh` for every case:

- `executing + pending + submitted`: Bash blocked by plan-task context;
- `reviewing + pending + submitted`: `git status --short`, `cat worker/skills/code-review/SKILL.md`, and exact focused command `pytest commander/tests/test_code_review_skill.py -q` pass;
- `reviewing`: `git add --dry-run worker/.codex-plugin/plugin.json`, chained Bash, and docs/source `Write`, `Edit`, `MultiEdit`, and `NotebookEdit` are blocked with unchanged snapshots/index;
- docs exception passes only in `brainstorming`, `design_ready`, `design_marked_for_use`, `plan_ready`, `plan_marked_for_use`, and `final_plan_prep`, and blocks in `idle`, `debugging`, `plan_interrupted`, `execution_complete`, and `reviewing`;
- `executing`: docs Write outside exact `allowed_files` blocks and inside exact `allowed_files` passes;
- stale gate with zero submitted tasks clears only gate counters for the fixture session.

Extend `test-workflow-transition-idempotency.sh` snapshot with `testing_theatre_checked`; seed `reviewing + active_skill=code-review`; send redundant code-review Skill payload; require zero output, write, or audit.

Add executing-plans tests for literal orchestration guidance: provider-native preflight; claim every parallel task before dispatch; intermediate `review_ready:false` continues siblings without review; sequential/final `review_ready:true` begins one submitted-batch review; A/B preflight plus one exact return; C/D/F reopens the same batch and must not call `mark_executing`; equal targets skip; explicit no reset, replacement task, or raw database workaround.

Create `commander/tests/test_code_review_skill.py` to require task-boundary review is read-only, contains no AUTO-FIX path, requires successful verdict persistence, treats A/B as orchestrator return, treats C/D/F as already-reopened rework without proceed/TODO choices, and leaves standalone review informational.

```bash
bash worker/hooks/tests/test-review-lifecycle-guards.sh
```

```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_executing_plans_skill.py -q
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_code_review_skill.py -q
```

Expected RED: only current docs/`git add` review bypass and missing lifecycle guidance fail; bridge characterization stays green.

### 1.3 Implement the existing lifecycle, without a new subsystem

In `state-machine.ts`, change the existing transition table entry exactly to permit review entry:

```ts
executing: ['reviewing', 'idle', 'execution_complete'],
```

In `write-tools.ts`:

- inside one `db.transaction`, re-read the persisted session by native ID and require its current source stage to equal `executing`; then re-query by `terminal_session + task_id + current_wave`, require `in_progress`, change exactly that task to `submitted`, count other current-wave `in_progress` tasks, and capture task/batch/remaining response values in closure variables declared immediately outside the transaction;
- if remaining count is nonzero, keep workflow `executing`, clear both gate fields, emit one submission audit, and return `review_ready:false`, `changed:false`, `from/to:"executing"`, exact task/batch/remaining/gate/session values; if zero, pass the transaction-local persisted source stage to `validateWorkflowTransition(source, 'reviewing', ...)` and throw on rejection, update stage plus both gate fields, emit one submission/review-entry audit, and return `review_ready:true`, `changed:true`, `from:"executing"`, `to:"reviewing"` plus exact captured values;
- require `reviewing` for task-boundary verdicts and reject an empty submitted set before writes;
- restrict `claim_task` to source `executing`; while a submitted review is pending, `get_next_tasks` remains non-advancing;
- informational verdicts use one transaction for grade plus audit only;
- A/B use one transaction, re-read current-wave submitted IDs inside it, grade, advance exactly those tasks, clear both flags, insert one final audit, preserve `reviewing`, and return exact IDs/counts/session; `mark_executing` requires both a current-wave A/B task-boundary grade and zero current-wave `submitted` tasks;
- C/D/F use `executeWorkflowTransition(..., 'executing', ...)`, re-read IDs inside it, grade, reopen exactly those tasks to `in_progress`, clear both flags, insert one final audit, and return transition fields plus IDs, `advanced_count:0`, and exact `rework_count`;
- update public definitions for `submit_task`, `claim_task`, `record_review_verdict`, and `mark_executing` to match exact stages, batch results, advancement/rework behavior, and guards.

Every thrown task/session/grade/audit error must escape the transaction and roll back all writes. Do not trust pre-transaction task objects or submitted-ID sets for mutation.

In `professional-mode-guard.sh`, restrict the docs whitelist to exactly `brainstorming|design_ready|design_marked_for_use|plan_ready|plan_marked_for_use|final_plan_prep`; restrict the generic non-executing `git add` exception so it excludes `reviewing`; do not expand the established reviewing read-only parser.

In `executing-plans/SKILL.md`, replace only submission/review instructions with the exact sequential/parallel batch, entry/A-B/C-D-F/preflight/session/no-workaround contract tested above. Preserve unrelated skill guidance.

In `code-review/SKILL.md`, remove task-boundary AUTO-FIX behavior; require strict read-only inspection and successful `record_review_verdict`; make A/B return to the orchestrator; make C/D/F report already-atomic rework with no proceed/TODO choice; keep standalone review informational.

### 1.4 Run focused GREEN and prerequisite regressions

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/tools/write-tools.test.ts src/__tests__/workflow-transition-idempotency.test.ts
```

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/__tests__/session-identity.test.ts src/__tests__/tool-dispatch.test.ts
```

```bash
bash worker/hooks/tests/test-review-lifecycle-guards.sh
```

```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_executing_plans_skill.py commander/tests/test_code_review_skill.py commander/tests/test_workflow_transition_preflight.py commander/tests/test_version_consistency.py -q
```

Expected: all focused lifecycle, rollback, combined-guard, MP-W08, MP-W11, and Commander contract tests pass with exact counts.

### 1.5 Run complete pre-cachebuster gates

```bash
npm --prefix worker/mcp-servers/state-manager test
```

```bash
commander/.venv/bin/python -m pytest
```

Run the complete existing hook inventory plus the new test, one command at a time:

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```
```bash
bash worker/hooks/tests/test-bash-readonly-guard.sh
```
```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```
```bash
bash worker/hooks/tests/test-gbtw-inflight.sh
```
```bash
bash worker/hooks/tests/test-gbtw-waiting.sh
```
```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```
```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```
```bash
bash worker/hooks/tests/test-review-lifecycle-guards.sh
```

```bash
codex plugin validate worker
```

Expected: every suite/validation passes with exact counts, skips, warnings, duration, and exit status.

### 1.6 Cachebust, typecheck, and build the final source

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: `worker/.codex-plugin/plugin.json` retains base `1.0.24` and receives one `+codex.<new-token>` suffix newer than `1.0.24+codex.20260721013248`.

```bash
npm --prefix worker/mcp-servers/state-manager exec tsc -- --noEmit
```

```bash
npm --prefix worker/mcp-servers/state-manager run bundle
```

Expected: no TypeScript diagnostics; final bundle contains final source.

### 1.7 Repeat all complete post-build gates

Run the exact state-manager, Commander, eight hook, and plugin-validation commands from Step 1.5 again and record fresh results. The eight exact hook commands are repeated here to keep execution mechanical:

```bash
npm --prefix worker/mcp-servers/state-manager test
```

```bash
commander/.venv/bin/python -m pytest
```

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```
```bash
bash worker/hooks/tests/test-bash-readonly-guard.sh
```
```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```
```bash
bash worker/hooks/tests/test-gbtw-inflight.sh
```
```bash
bash worker/hooks/tests/test-gbtw-waiting.sh
```
```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```
```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```
```bash
bash worker/hooks/tests/test-review-lifecycle-guards.sh
```

```bash
codex plugin validate worker
```

Expected: every post-build suite/validation passes with exact evidence.

### 1.8 Confirm marketplace and reinstall

```bash
codex plugin list
```

Expected: `ironclaude` resolves through the confirmed local marketplace to `/Users/roberthyatt/Code/ironclaude/worker`.

```bash
codex plugin add ironclaude@ironclaude
```

Expected: installation succeeds at the exact new cache root.

### 1.9 Prove source/cache parity and capture the restart oracle

```bash
node -e 'const fs=require("fs"),os=require("os"),path=require("path"),crypto=require("crypto");const sha=p=>crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");const v=JSON.parse(fs.readFileSync("worker/.codex-plugin/plugin.json","utf8")).version;const root=path.join(os.homedir(),".codex/plugins/cache/ironclaude/ironclaude",v);const files=[".codex-plugin/plugin.json","mcp-servers/state-manager/dist/index.js","mcp-servers/state-manager/src/state-machine.ts","mcp-servers/state-manager/src/tools/write-tools.ts","mcp-servers/state-manager/src/tools/write-tools.test.ts","mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts","hooks/professional-mode-guard.sh","hooks/tests/test-review-lifecycle-guards.sh","hooks/tests/test-workflow-transition-idempotency.sh","skills/executing-plans/SKILL.md","skills/code-review/SKILL.md"];for(const f of files){const a=path.join("worker",f),b=path.join(root,f);if(!fs.readFileSync(a).equals(fs.readFileSync(b)))throw new Error(`mismatch: ${f}`);console.log(`${sha(a)}  ${f}`)}console.log("EXPECTED_RUNTIME="+JSON.stringify({plugin_root:root,plugin_version:v,manifest_sha256:sha(path.join(root,".codex-plugin/plugin.json")),bundle_sha256:sha(path.join(root,"mcp-servers/state-manager/dist/index.js")),client:"codex"}))'
```

Expected: every worker/cache pair matches, one SHA-256 prints per path, and one complete `EXPECTED_RUNTIME` JSON object prints. Commander tests are repository-only.

### 1.10 Stage exact Task 1 files before restart

```bash
git add worker/mcp-servers/state-manager/src/state-machine.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.test.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/hooks/professional-mode-guard.sh worker/hooks/tests/test-review-lifecycle-guards.sh worker/hooks/tests/test-workflow-transition-idempotency.sh worker/skills/executing-plans/SKILL.md worker/skills/code-review/SKILL.md commander/tests/test_executing_plans_skill.py commander/tests/test_code_review_skill.py worker/.codex-plugin/plugin.json worker/mcp-servers/state-manager/dist/index.js
```

Expected: exact files staged; no commit or push.

### 1.11 Fully restart and verify the same task on the new runtime

Record current `get_resume_state`: same native task, professional mode on, plan name, Task 1 `in_progress`. Fully quit/relaunch Codex and reopen task `019f7742-abd8-7c62-af7b-fe07189f1ffd`. Require the executing-plans skill locator to contain the new version. Call:

```text
get_resume_state({})
run_diagnostics({expected_runtime: <exact EXPECTED_RUNTIME JSON from Step 1.9>})
```

Expected: same session ID/mode/plan/Task 1; all 13 diagnostics pass; runtime client/root/version/manifest hash/bundle hash exactly match the printed oracle. Installation output, plugin list, and byte parity alone are insufficient. A different task is recovery-only.

### Task 1 boundary acceptance

After Step 1.11, Task 1 has no in-progress sibling. The orchestrator calls `submit_task({task_id:1})` once and requires exact same-session `review_ready:true`, `executing -> reviewing changed:true`, exact submitted batch, and gate fields. During the strictly read-only task-boundary review, run `git status --short` and `cat worker/.codex-plugin/plugin.json`; both must pass. Run `git add --dry-run worker/.codex-plugin/plugin.json`; it must be blocked with `BLOCKED — COMMAND NOT ALLOWED DURING REVIEW` and leave the index unchanged. The combined hook tests already prove test-command behavior before submission; do not invoke blocked `npm` during live reviewing. Record A/B successfully, preflight current stage `reviewing`, call `mark_executing({})` once, and require exact same-session `reviewing -> executing changed:true`. C/D/F must instead atomically reopen Task 1 in `executing`; fix within its allowed files, rerun affected gates, and resubmit without `mark_executing`. Any verdict-persistence or transition mismatch fails closed and blocks Task 2.

---

## Task 2: Record durable Wave 1R evidence

**Depends on:** Task 1

**Files:**
- Modify: `docs/validation/2026-07-20-workflow-transition-idempotency.md`

**No new tests required:** Evidence-only task reruns the focused gates and records already-tested implementation/runtime behavior.

Record stale-process correction; same native ID and MP-W11 evidence; exact installed cache/skill locators/manifest/client/hashes/13 diagnostics; sequential and two-task parallel submission semantics; exact Task 1 submit and return; strict read-only code-review behavior; combined guard allowed/blocked results; transaction-local validation and complete rollback matrix; empty boundary rejection and informational neutrality; pre/post-build full suites; plugin validation and parity; roadmap Waves 2–13 still release-blocking; no commit/push or later-feature completion claim.

Also record Wave 0 MP-W01–MP-W07/MP-W09/MP-W10 as completed prerequisites protected by named executing-plans tests, then reproduce and verify the roadmap disposition exactly: Wave 2 = MP-001/MP-002/MP-011/MP-012/MP-D04/MP-C01; Wave 3 = MP-003/MP-006/MP-D01/MP-D05/MP-D10; Wave 4 = MP-004/MP-007/MP-009/MP-010/MP-C02/MP-C03; Wave 5 = MP-005/MP-008/MP-D02/MP-D03/MP-D06/MP-D07/MP-C04; Wave 6 = MP-D08/MP-D09/MP-C05; Waves 7–9 = their named queued defects; Wave 10 = MP-C06; Wave 11 = queued `create_plan` hardening; Wave 12 = MP-013/MP-014/MP-C07; Wave 13 = all active requirements with MP-D08 evidence emphasized.

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/tools/write-tools.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/runtime-fingerprint.test.ts src/__tests__/run-diagnostics.test.ts src/__tests__/session-identity.test.ts src/__tests__/tool-dispatch.test.ts
```

```bash
bash worker/hooks/tests/test-review-lifecycle-guards.sh
```

```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_executing_plans_skill.py commander/tests/test_code_review_skill.py commander/tests/test_workflow_transition_preflight.py commander/tests/test_version_consistency.py -q
```

```bash
git diff --check -- docs/validation/2026-07-20-workflow-transition-idempotency.md
```

```bash
git add -f docs/validation/2026-07-20-workflow-transition-idempotency.md
```

Expected: exact focused evidence passes; validation file staged; no commit or push.
