# Codex work check results, 2026-10-06

All suites ran against the working tree, which contains the staged (uncommitted) handoff source. The workspace-manager dist bundles were NOT rebuilt for the staged handoff source; vitest and tsc exercised the TypeScript test source and source files, not dist. Nothing was fixed, edited, committed or deployed. Raw logs are in the session scratchpad as val-*.log.

## Commands (verbatim) and results

1. `cd /Users/roberthyatt/Code/ironclaude && for s in worker/hooks/test-*.sh worker/hooks/tests/test-*.sh commander/hooks/tests/test-*.sh; do bash "$s" > ".../scratchpad/val-hook-$(basename "$s").log" 2>&1; echo "$s exit=$?"; done`
   Result: 47 suites; 45 exit 0, 2 exit 1 (listed below).
2. `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run > .../val-vitest-full.log 2>&1; echo "exit=$?"`
   Exit 1. Test Files 2 failed | 11 passed (13). Tests 5 failed | 524 passed | 1 skipped (530). Errors 1 error.
3. `npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- src/__tests__/handoff.test.ts src/__tests__/cli.test.ts > .../val-vitest-handoff.log 2>&1; echo "exit=$?"`
   Exit 0. Test Files 2 passed (2). Tests 60 passed (60).
4. `.../workspace-manager/node_modules/.bin/tsc --noEmit --project .../workspace-manager/tsconfig.json > .../val-tsc.log 2>&1; echo "exit=$?"`
   Exit 0. No error lines (empty log).
5. `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_quota.py tests/test_worker_pm_handoff.py tests/test_worker_handoff.py tests/test_worker_handoff_process.py tests/test_worker_handoff_daemon.py tests/test_workspace_handoff_authority.py tests/test_provider_router.py tests/test_worker_registry.py tests/test_lifecycle_logging.py > .../val-pytest-handoff.log 2>&1; echo "exit=$?"`
   Exit 0. "331 passed in 40.63s".
6. `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > .../val-pytest-full.log 2>&1; echo "exit=$?"`
   Exit 1. "1 failed, 3760 passed in 425.24s (0:07:05)". Failed: tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd (IndentationError: unexpected indent in a bash here-document run via python3 stdin; assertion `result.returncode == 0` got 1).

## Vitest full-run failures (step 2)

- src/__tests__/db.test.ts: 3 failures in the human_intents migration tests (v2 FK-drop populated DB, v2 legacy NULL-guid row, v3 operation-widen). Each: expected [1,2,3,4,5,6,7] to deeply equal [1,2,3,4,5,6] (schema version list now has a 7th entry).
- src/__tests__/tool-dispatch.test.ts: "keeps hook issuance out of the general CLI and exposes no internal push command" (command list has 17 entries, test expects 13); "rejects forged review authority fields at the private CLI boundary" (expected 'Commander finalization input is malformed', got 'db.transaction is not a function').

## Known onTaskUpdate RPC timeout limitation

OBSERVED in this run, recorded separately from the test failures above: `Error: [vitest-worker]: Timeout calling "onTaskUpdate"` (an unhandled error, "Errors 1 error"). This is the known backlog limitation (vitest worker RPC timeout during the long-running src/__tests__/integration-core.test.ts, which took 80798ms for 96 tests in this run). The log does not name integration-core on the error itself; the attribution rests on that file being the slow suite and on the backlog entry. It is not counted among the 5 test failures.

## Hook suites with non-zero exit

### worker/hooks/tests/test-gbtw-continuation-judge.sh (exit 1)
Log tail:
```
FAIL mixed_failure_and_continuation_D tool result expected=yes actual=no
worker/hooks/tests/test-gbtw-continuation-judge.sh: line 150: rg: command not found
FAIL mixed_failure_and_continuation_D final expected=yes actual=no
worker/hooks/tests/test-gbtw-continuation-judge.sh: line 151: rg: command not found
FAIL mixed_failure_and_continuation_D central question expected=yes actual=no
worker/hooks/tests/test-gbtw-continuation-judge.sh: line 152: rg: command not found
FAIL mixed_failure_and_continuation_D workflow context expected=yes actual=no
worker/hooks/tests/test-gbtw-continuation-judge.sh: line 209: rg: command not found
FAIL mixed failed evaluator warns before valid D block expected=yes actual=no
worker/hooks/tests/test-gbtw-continuation-judge.sh: line 210: rg: command not found
PASS mixed evaluator warning does not claim approval
PASS unavailable_warning_io_failure decision
PASS warning queue directory remains fail-open
PASS throttle attempt 2 blocks
PASS throttle attempt 2 judges
PASS throttle attempt 3 blocks
PASS throttle attempt 3 judges
PASS throttle fourth Stop passes natively
PASS throttle fourth Stop skips judge
results: 77 pass, 107 fail
```
Observation: the log shows `rg: command not found` (ripgrep missing from PATH in this shell); this is evidence from the log, not a verified root cause of all 107 failures.

### worker/hooks/tests/test-last-assistant-text.sh (exit 1)
Log tail:
```
PASS  1 codex last_assistant_message
PASS  2 field preferred over transcript
PASS  3 transcript fallback
PASS  4 both absent -> empty
FAIL  5 GBTW reports unavailable Codex evaluation (got: {
  "decision": "approve",
  "reason": "Stopping is appropriate; evaluation unavailable: bypass, continuation, rigor, prediction, evidence",
  "systemMessage": "[GET-BACK-TO-WORK]: Allowed - evaluation unavailable (following_professional_mode:unavailable, diligently_finished_work:unavailable, rigor_quality:unavailable, evidence_quality:unavailable, prediction_quality:unavailable, coa_quality:suppressed())"
})
PASS  6 drift detector blocks on Codex-shaped anti-pattern
PASS  7 clean subagent text does not block
Results: 6 pass, 1 fail
```

## All hook suites and exit codes

worker/hooks/test-bg-detection.sh exit=0
worker/hooks/test-codex-brain-gated-actions.sh exit=0
worker/hooks/test-config-guard-integration.sh exit=0
worker/hooks/test-config-guard.sh exit=0
worker/hooks/test-guard-security.sh exit=0
worker/hooks/test-poll-dedup.sh exit=0
worker/hooks/test-stop-wrapper.sh exit=0
worker/hooks/tests/test-antipattern-lexicon.sh exit=0
worker/hooks/tests/test-bash-readonly-guard.sh exit=0
worker/hooks/tests/test-episodic-memory-sync-singleton.sh exit=0
worker/hooks/tests/test-execution-drift-check.sh exit=0
worker/hooks/tests/test-execution-drift-identity.sh exit=0
worker/hooks/tests/test-execution-drift-roots.sh exit=0
worker/hooks/tests/test-gbtw-antipattern-override.sh exit=0
worker/hooks/tests/test-gbtw-block-tone.sh exit=0
worker/hooks/tests/test-gbtw-bypass-carveout.sh exit=0
worker/hooks/tests/test-gbtw-client-aware.sh exit=0
worker/hooks/tests/test-gbtw-continuation-context.sh exit=0
worker/hooks/tests/test-gbtw-continuation-judge.sh exit=1
worker/hooks/tests/test-gbtw-continuation-suppression.sh exit=0
worker/hooks/tests/test-gbtw-inflight.sh exit=0
worker/hooks/tests/test-gbtw-review-gate.sh exit=0
worker/hooks/tests/test-gbtw-waiting.sh exit=0
worker/hooks/tests/test-git-authority-activation.sh exit=0
worker/hooks/tests/test-ic-skill-ref.sh exit=0
worker/hooks/tests/test-last-assistant-text.sh exit=1
worker/hooks/tests/test-managed-worktree-guard.sh exit=0
worker/hooks/tests/test-mcp-state-logger-active-skill.sh exit=0
worker/hooks/tests/test-memory-search-gate.sh exit=0
worker/hooks/tests/test-misc-hooks-client-aware.sh exit=0
worker/hooks/tests/test-openai-backend.sh exit=0
worker/hooks/tests/test-plan-task-context-apply-patch.sh exit=0
worker/hooks/tests/test-pmguard-client-aware.sh exit=0
worker/hooks/tests/test-pmguard-enterplanmode.sh exit=0
worker/hooks/tests/test-professional-mode-deactivation.sh exit=0
worker/hooks/tests/test-professional-mode-docs-nested-repo.sh exit=0
worker/hooks/tests/test-sad-antipattern.sh exit=0
worker/hooks/tests/test-session-init-plan-lineage.sh exit=0
worker/hooks/tests/test-session-init-stable-dir-seed.sh exit=0
worker/hooks/tests/test-skill-handoffs-restaging.sh exit=0
worker/hooks/tests/test-skill-state-bridge-inherit-review.sh exit=0
worker/hooks/tests/test-state-activator-single-object.sh exit=0
worker/hooks/tests/test-subagent-circuit-breaker.sh exit=0
worker/hooks/tests/test-warning-queue.sh exit=0
worker/hooks/tests/test-workflow-transition-idempotency.sh exit=0
commander/hooks/tests/test-brain-task-gate.sh exit=0
commander/hooks/tests/test-memory-search-enforcer.sh exit=0
commander/hooks/tests/test-startup-lookback-enforcer.sh exit=0
