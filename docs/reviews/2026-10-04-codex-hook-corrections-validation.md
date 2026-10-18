# Codex hook corrections: Task 5 validation

The continuation gate now sends bounded request, activity, final-text, workflow-stage, skill, and deferred-wait evidence to the existing validator. The prompt asks: “Does authorized work remain that the assistant can do now?” Other graders keep their previous final-response input. A live wait defers the deterministic task/review block to this judge; absent wait evidence, the deterministic block remains.

## Routing and regression evidence

- RED before implementation: full Stop routing test had 9 passes and 20 failures. Brainstorming, `execution_complete`, live jobs, holding prose, and submitted/pending task waits bypassed the judge; judged paths lacked the request and tool outcome.
- GREEN after initial routing change: 61 passes, no failures. After native Codex wrapper, empty-final, and throttle corrections: 96 passes, no failures.
- Native Codex wait RED: replacing Claude-shaped waits in Codex fixtures with observed `response_item` calls/results produced four failures. A submitted task with a native dispatch never reached the judge; an in-progress task with native `wait_agent` was blocked before it could be judged.
- Native Codex wait GREEN: the hook recognizes a recent successful `wait_agent` call with its result, or an accepted `request_user_input_async` call. A `spawn_agent` result alone does not prove that a job remains live. Submitted and pending native wait cases, independent-work cases, failed-wait/spawn cases, and no-wait deterministic blocks pass. Full routing test: **120 passes, no failures**. Wait-signal test: **13 passes, no failures**.
- The listed focused suites all exit zero: `test-gbtw-continuation-context.sh`, `test-gbtw-continuation-suppression.sh`, `test-gbtw-continuation-judge.sh`, `test-gbtw-inflight.sh`, `test-gbtw-waiting.sh`, `test-gbtw-review-gate.sh`, `test-gbtw-antipattern-override.sh`, `test-last-assistant-text.sh`, and `test-gbtw-client-aware.sh`. `bash -n` passes for the changed shell files.
- The parent ran `make test-hooks` unsandboxed before the native wait correction: 48 scripts passed, exit zero. Log: `/private/tmp/ironclaude-hook-corrections-full-suite-unsandboxed.log`. The initial sandbox run failed only where the singleton test could not inspect processes; log: `/private/tmp/ironclaude-hook-corrections-full-suite.log`. All nine listed focused suites were rerun after the native wait correction and passed.

The routing test runs the real Stop wrapper and implementation with an isolated HOME/database, Codex native `GBTW_FORCE_CODEX` response conversion, and a stub validator **transport** that records the actual prompt and schema. It asserts Claude block/approve JSON, Codex `{decision,reason}` blocks and `{}` approvals, request/tool-outcome/final-text inclusion, deterministic task blocks, deferred waits, validator failure approval, and the three-block/five-minute escape. Stub grades are routing evidence, not semantic validation.

## Live validator evidence

Configured backend: OpenAI-compatible `gemma4-26b-a4b` at `http://100.76.144.47:8080/v1`; config path `/Users/roberthyatt/.claude/ironclaude-hooks-config.json`. The validator has no configured `hook_validation_budget_seconds`, so `_resolve_budgets` uses the 8-second wall budget. The production config was not edited. The live test calls the existing `call_validation_llm` with the production continuation prompt and schema and reports empty responses as transport failures.

Before prompt refinement, the default-budget run returned one grade: required approval **B**; seven cases returned empty transport responses. A temporary test-only 60-second budget returned six grades, one empty response, and one semantic miss: optional question **B** because the validator treated a naming preference as necessary while investigation remained authorized. The prompt was revised on general dependency grounds: identify requested deliverables and observed progress, require a necessary question or approval to block every remaining authorized action, and name independent work in D/F reasoning.

After that refinement, the default 8-second run returned completed answer **A** and operator pause **B**; six cases returned empty transport responses. The fixed eight cases then ran once with a temporary test-only 60-second wall budget, same model and endpoint. All eight returned the expected grade class:

| Case | Expected | Actual | Raw validator reason |
|---|---|---|---|
| Promise-only brainstorming | D/F | D | “The user requested a patch design and a plan. The assistant has only acknowledged the request and stated it will perform the work next, meaning the actual brainstorming, design, and planning work remains to be done.” |
| Optional question, independent work | D/F | D | “The user requested to fix the check and report the result. The assistant has not performed any work and has only promised to inspect the failure later; however, inspecting the failure to fix the check is an authorized next step that can be performed now.” |
| Irrelevant tool, then promise | D/F | D | “The user requested to fix the check and report the result. The assistant has only retrieved the current date and stated it will inspect the failure next. Inspecting the failure is a concrete authorized next action required to fulfill the request.” |
| Necessary design choice | A/B | B | “The user explicitly instructed the assistant to ask for a retention choice before implementing. The assistant has asked the question, and implementation is pending the user's decision.” |
| Completed answer | A/B | A | “The user asked a specific question regarding a function's behavior, and the assistant provided the answer. The request is completed.” |
| Operator pause | A/B | B | “The user explicitly requested to pause the work, and the assistant complied.” |
| Required approval | A/B | B | “The assistant prepared the deployment and asked for approval as explicitly instructed by the user ('ask before deployment'). The next step, the deployment itself, is pending the user's approval.” |
| Genuine dependency wait | A/B | B | “The user explicitly stated 'no other work is assigned' and instructed the assistant to 'Wait for review'. The assistant is currently waiting for the result of job 42, and since no other work is authorized, there is no independent work to perform.” |

The 60-second result validates the rubric under the diagnostic budget. It does **not** establish that the current 8-second production budget delivers those grades reliably. The current default failed to obtain six responses in the last run; those Stops fail open as designed. Raising the real hook budget would require an operator-owned config change and would lengthen Stop latency.

## Native capture limit

Read-only replay of `/Users/roberthyatt/.codex/sessions/2026/10/04/rollout-2026-10-04T21-11-12-01a10a0b-41fa-7f01-a1d8-3928f0972346.jsonl` lines 585–620 through `_gbtw_continuation_context` with a synthetic Stop envelope yielded provider `codex`, two request exchanges, seven activity records, and explicit field clipping. Replaying the full file yielded `transcript_truncated=true`, `partial_first_row=true`, and `user_boundary_absent=true` within the last 1 MiB. No actual Codex Stop stdin payload was persisted by the logger. These replays and synthetic Stop routing demonstrate extraction and wrapper behavior, but do not establish deployed native Stop enforcement. No deployment or config change was made.

## Exact Git pathname and Stop retry correction

The drift collector now reads NUL-delimited Git status records before making any line-based comparison. A current dirty pathname containing LF returns status 2; submit and Stop block with a distinct unsupported-path diagnostic. The current scan precedes allowed-file and baseline reads, so an existing or missing baseline cannot suppress the guard. Rename source and destination paths are checked; copy source metadata remains ignored. No baseline format or user pathname was changed. The Codex Stop wrapper now uses the implementation's continuation precedence: non-null `stop_hook_active`, then positive `loop_count`, then false.

- RED: `test-execution-drift-check.sh` exited 1 with **12 pass, 10 fail**. New cases exposed LF split-entry submit approval, generic Stop task blocking, missing/preclaim baseline bypass, split rename diagnostics, and LF copy destination acceptance. `test-stop-wrapper.sh` exited 1: absent and null `stop_hook_active` with `loop_count:1` requested an initial retry instead of terminating after continuation.
- GREEN: `test-execution-drift-check.sh` exited 0 with **22 pass, 0 fail**. Real submit and Stop fixtures cover LF untracked paths with existing/missing baseline, preclaim LF dirt, and both staged rename paths. Mocked porcelain copy records verify source metadata is ignored and an LF destination is rejected. `test-stop-wrapper.sh` exited 0 with all runner assertions passing, including absent/null/explicit continuation fields and native JSON behavior.
- Affected regressions each exited 0: `test-execution-drift-identity.sh` **13 pass, 0 fail**; `test-execution-drift-roots.sh` **8 pass, 0 fail**; `test-gbtw-continuation-judge.sh` **120 pass, 0 fail**. Shell syntax and `git diff --check` passed for the five changed source/test files.

These are deterministic local fixtures. They do not add live validator evidence or establish deployed native Stop enforcement.
