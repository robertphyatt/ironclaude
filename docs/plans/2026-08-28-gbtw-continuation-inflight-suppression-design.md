# GBTW Continuation-Check In-Flight Suppression (G1) Design

> **Created:** 2026-08-28
> **Status:** Design Complete
> **Origin:** live-observed this session — the GBTW Stop hook graded "holding for the subagent"
> turns "Grade: D — WORK INCOMPLETE" while a dispatched Agent subagent was genuinely in flight.
> Backlog: project_gbtw_tuning_backlog (G1), feedback_ironclaude_steers_away_from_delegation.

## Summary

The get-back-to-work (GBTW) Stop hook (`worker/hooks/get-back-to-work-impl.sh`) over-fires its
**continuation check** on legitimate in-flight subagent waits. The corrected root cause is narrow:
the hook already has a completion-aware in-flight detector, but the continuation-check suppression
path does not consult it for **Agent** subagents.

`_gbtw_extract_in_flight` (lines 37-93) computes launched-minus-completed background-job IDs from
the transcript — it recognizes both Agent async dispatch (`toolUseResult.status == "async_launched"`,
line 57) and Bash `run_in_background` jobs, and it subtracts completions (task-notifications carrying
`completed|failed|killed|stopped`, lines 73-77). It is already wired into the **tasks-in-progress
block** (lines 496-513) and the **code-review gate** (via `_gbtw_review_gate_suppress`, lines 215-226,
called at 467).

The **continuation-check suppression** (lines 924-950) does NOT use it. It runs a separate, narrower
check over only the last 3 assistant turns for named waiting tools (Monitor / TaskOutput /
ScheduleWakeup / AskUserQuestion) plus Bash `input.run_in_background == true`. An Agent subagent
(`async_launched`, no `input.run_in_background` field) matches none of these, so the continuation
grader still fires and grades the "holding" turn D/F. This is the backlogged **G1** gap.

(The separate "STOP — TASKS STILL IN PROGRESS" over-firing observed during M7c execution was the
stale-deployment symptom of the stable-dir hook revert — the repo's tasks-block already suppresses
via `_gbtw_extract_in_flight`, and `make deploy-hooks` this session restored deployed==repo. No code
change is needed there; see [[project_hook_stable_dir_revert]].)

## Approach (single bounding change — the only viable option under scope=hold)

Replace the continuation-suppression's ad-hoc `_BG_JOB_ACTIVE` detection (lines 924-950) with a call
to the existing **`_gbtw_review_gate_suppress "$TRANSCRIPT_PATH"`** helper, which already ORs the two
canonical signals: `_gbtw_extract_in_flight` (completion-aware Agent + Bash bg jobs) and
`_gbtw_recent_waiting_tool` (the persistent named waiting tools). On a `true` result set
`FIRE_CONTINUATION="false"`, and preserve the existing anti-pattern re-arm
(`_gbtw_should_rearm_check`) so a checkpoint/query-offload proposal still fires the check.

This aligns the continuation suppression with the tasks-block and code-review gate (same helpers,
same completion-aware semantics) and is strictly better than the current 3-turn window: a subagent
dispatched several turns before the wait is still detected (the helper scans the byte-bounded tail,
not just 3 turns), and a job that has already completed is correctly NOT suppressed.

**Why this cannot false-silence a genuine stall (the hard constraint):** the suppression is
**completion-aware**. `_gbtw_extract_in_flight` is launched **minus completed**, and a crashed,
killed, or finished subagent emits a task-notification whose `<status>` is `completed|failed|
killed|stopped` (lines 73-77) — so it is subtracted from the in-flight set. A genuine stall (no live
job, no waiting tool) yields an empty result, the suppression does not apply, and the continuation
grader fires exactly as today. The named-waiting-tool half (`_gbtw_recent_waiting_tool`) is the same
signal already trusted by the code-review gate.

Alternatives considered and rejected: (a) keeping the 3-turn `_BG_JOB_ACTIVE` block and only adding
an `async_launched` branch — leaves the completion-blind window bug for bg-Bash and duplicates logic
the helper already centralizes; (b) suppressing on ANY Agent dispatch regardless of completion — the
explicit false-silence hazard the memory warns about. One completion-aware helper call is minimal and
correct.

## Components / data flow

- **Unchanged:** `_gbtw_extract_in_flight`, `_gbtw_recent_waiting_tool`, `_gbtw_review_gate_suppress`,
  `_gbtw_should_rearm_check`, the tasks-in-progress block, the code-review gate, all six LLM checks.
- **Changed:** only the `FIRE_CONTINUATION` suppression stanza in the skill-aware firing section of
  `get-back-to-work-impl.sh` (the lines 924-950 `_BG_JOB_ACTIVE` computation), replaced by the helper
  call + the retained re-arm.

## Error handling

- The helper is fail-open by construction: an empty/missing transcript or absent `jq` returns empty
  (no suppression) — the continuation check fires, i.e. it fails toward nagging, never toward silence.
- The anti-pattern re-arm is preserved verbatim, so the fix does not weaken checkpoint-proposal
  detection.

## Testing strategy

`get-back-to-work-impl.sh` exposes its helpers under `GBTW_TEST_MODE=1` (lines 228-232), the existing
predicate-test seam (see `worker/hooks/test-bg-detection.sh`). Add predicate tests that seed a small
transcript JSONL and assert the suppression decision:

- **Live Agent subagent → suppressed:** a transcript with an `async_launched` Agent tool_result
  (agentId X) and NO matching completion → `_gbtw_review_gate_suppress` returns `true` (the signal the
  fix keys on). This is the exact case that graded D before.
- **Completed Agent subagent → NOT suppressed (no false-silence):** the same launch PLUS a
  task-notification `<status>completed</status>` for X → the helper returns empty; the continuation
  check would fire. This is the falsifier proving a real stall still nags.
- **Killed/failed subagent → NOT suppressed:** a `<status>killed</status>` (or `failed`) completion is
  also subtracted → empty → fires.
- **Named waiting tool still suppresses:** a recent Monitor/ScheduleWakeup tool_use → `true`
  (regression guard on the existing behavior).
- **Anti-pattern re-arm preserved:** with a live subagent AND trailing checkpoint-proposal prose, the
  continuation check is re-armed (fires) despite the in-flight job — the `_gbtw_should_rearm_check`
  path is unchanged.

## Non-goals

- No change to the tasks-in-progress block (already completion-aware; its over-firing was the stale
  deployment, now redeployed).
- No checkpoint-anti-pattern **detection** work (separate backlog:
  project_gbtw_detect_checkpoint_antipattern).
- No tone/anxiety-clause changes (closed by M4, commit 5ddb8f8).
- No change to the six LLM grading prompts or the firing matrix beyond the one suppression stanza.
