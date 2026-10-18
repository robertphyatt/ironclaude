# Hook Boundary Safeguards Implementation Plan

Required skill: ironclaude:executing-plans, subagent-sequential.

Goal: Close exact-path drift ambiguity and align bounded Codex Stop retry detection.

Design: docs/plans/2026-10-04-codex-hook-corrections-design.md

Requirements: docs/plans/2026-10-04-codex-hook-corrections-requirements.md

Execution invariants: Use absolute script/data paths; executable names resolve through PATH and git -C /Users/roberthyatt/Code/ironclaude; shell state does not persist. Quote globs. No foreground sleep. docs are ignored and require git add -f. Do not suppress errors on evidence commands or truncate completeness searches. Enumerate actual outputs; expected pass/fail outcomes below are acceptance criteria to measure during execution, not previously observed results. RED must demonstrate defect before GREEN. Preserve pre-existing staged experiment documents. No commits, pushes, deployment, version changes, WIP branch deletion, L3 implementation, new framework, or broad cleanup. Each step is one focused operation; split editing/test cycles into 2–5 minute actions. Parent owns state, review and staging; agents implement only claimed task. Review every task before advancing. Existing configured validator is used for live cases; transport failure is an explicit limitation, never a semantic pass. Each command runs separately with its own checked exit status.

Scope: prior tasks remain completed source artifacts; this plan implements only R9/R10.

## Task 1: Preserve exact drift boundaries and bounded Stop retries

Depends on: []

Reject LF-containing current dirty Git paths as unsupported before line-based allowed/baseline comparison; propagate distinct status 2 through submit and Stop as visible block, never generic fail-open. Scan current dirty paths before baseline/allowed lookup so pre-existing or missing-baseline cases cannot suppress this guard. Preserve normal baseline files, copy destination-only handling and both rename paths. Do not mutate user filenames. In wrapper _run_codex_stop, mirror implementation precedence: if stop_hook_active non-null use it, else loop_count > 0, else false. Keep native JSON/exit semantics. Update validation artifact with measured results. Scope remains subject to existing professional-mode/root/task eligibility and bounded three-block Stop escape. Distinct status2 must survive collector pipeline and command substitution; capture actual exit status before any ! negation. Other Git/read failures retain prior fail-open policy.

Allowed files:
- `worker/hooks/execution-drift-check.sh`
- `worker/hooks/get-back-to-work-impl.sh`
- `worker/hooks/tests/test-execution-drift-check.sh`
- `worker/hooks/get-back-to-work-claude.sh`
- `worker/hooks/test-stop-wrapper.sh`
- `docs/reviews/2026-10-04-codex-hook-corrections-validation.md`

### Step 1

RED: extend test-execution-drift-check.sh: temporary Git filename allowed.txt\nother.txt with two separate allowed entries must block submit and Stop with unsupported-path message; test with existing baseline and missing baseline, and rename source case. Real ordinary filenames continue passing. Extend test-stop-wrapper.sh failing-impl runner cases: loop_count 1 with absent stop_hook_active terminates, loop_count0 requests initial retry, explicit stop_hook_active false overrides loop_count1, true overrides loop_count0. Include LF dirt before claim; missing baseline; LF rename source and destination; reject LF copy destination but ignore copy source metadata. Require unsupported-path diagnostic, not generic task block, and clear only fixture throttle state between independent cases. Wrapper includes stop_hook_active:null plus loop_count1.

### Step 2

Run focused tests, record concrete RED; no predicted counts. Execute each command separately and record each exit; never infer combined success from final command.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
```

### Step 3

GREEN: implement described status propagation and retry precedence. Preserve pipeline status so sort or command substitution cannot swallow unsupported-path detection. Error message identifies unsupported newline Git paths without printing split misleading names. Do not add broad staging, new schema or baseline migration.

### Step 4

Run focused tests and affected regressions, record measured output in validation artifact. Run full hooks after these changes if affected regressions expose broader coupling. Execute each command separately, require every suite exit0; adjust fixed fixture assertion totals with added cases.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
```

### Step 5

Parent reviews and stages exact five source/test paths; force-add only validation artifact.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/hooks/execution-drift-check.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-execution-drift-check.sh worker/hooks/get-back-to-work-claude.sh worker/hooks/test-stop-wrapper.sh
git -C /Users/roberthyatt/Code/ironclaude add -f -- docs/reviews/2026-10-04-codex-hook-corrections-validation.md
```
