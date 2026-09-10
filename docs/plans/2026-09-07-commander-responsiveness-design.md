# Commander Responsiveness Fixes Design

> **Created:** 2026-09-07
> **Status:** Design Complete
> **Scope mode:** selective (baseline = R1–R6 + SF1/SF2; R7/R8 deferred)

## Summary

The IronClaude Commander is sluggish to its human operator. A read-only, evidence-backed Fable analysis (`scratchpad/commander-responsiveness-report.md`) found the felt lag is dominated by the idle Brain restarting itself every ~30 min, compounded by a dead fast-reply path, a single-threaded daemon loop with synchronous LLM graders that stall, background nudges competing with operator input, self-inflicted subagent fan-out, and repetitive re-narration. This design implements the report's recommendations R1–R6 plus two side findings (SF1 log pollution, SF2 a possible guardrail gap) — **improving responsiveness without weakening any guardrail** (PM workflow, plan-review/tier-up gates, task-boundary code review, worktree isolation, human-only git/no-push, never-fake-past-a-gate, trust/identity). R7 (prompt trimming) and R8 (sweep cadence/model) are deferred: R1 removes their cause.

## Architecture (per finding, chosen approach)

- **R1 — ping-then-restart-only-if-unanswered** (operator-selected). The idle-restart block at `brain_client.py:950-960` ("Absolute inactivity: no SDK messages for 1800s") fires ONLY when the Brain is legitimately idle (thread alive `:932`, no tool in flight `:947-948`, no message pending). Real failures stay covered by the dead-thread check (`:932-933`), the tool-execution hang net (`:935-946`, 1800s), and the message-sent-but-silent normal timeout (`:961+`, `timeout_seconds`). Replace :950-960 so that after 1800s idle the daemon sends a lightweight `[PING]` to the Brain and restarts ONLY if no SDK activity follows within `timeout_seconds`. Preserves the stall backstop that motivated the check (incidents #1245 worker frozen 7+h, #1132 stuck 8h — now also covered deterministically by `check_stuck_workers`/`check_idle_enforcement`). MUST NOT reintroduce the #1364 SIGHUP restart-race — the ping path is a health check in `needs_restart`, separate from `restart_daemon(directive_id=…)`.

- **R2 — make the threaded-reply path live.** `_NARRATION_PREFIX = "[NARRATION] "` (`brain_client.py:139`, applied `:848`) prepends every Brain text, but `slack_interface.py:52` `_REPLY_TO_PREFIX_RE = ^\s*\[reply-to:` requires the marker at the START, so `[NARRATION] [reply-to:…]` never matches and the thread-under-operator + ✅ branch never runs. Fix: strip `_NARRATION_PREFIX` before `parse_reply_to_marker` in the response-poll path (or do not prefix marker-led text), and add the missing test feeding `f"{_NARRATION_PREFIX}[reply-to:{ts}] body"` through the real seam.

- **R3 — operator fast lane** (own wave; medium risk). In the daemon main loop (`main.py` ~`:4664-4683`, `poll_interval_seconds=15`): poll Slack commands/Brain responses every ~2-3s while gating `check_workers`/heartbeat on their own ~15s timers; and bound/offload the inline LLM grader calls (`_detect_worker_prompt`, `_maybe_capture_operator_wait`, `_validate_brain_message`) so an empty-Ollama stall (up to 339s observed) cannot block operator pickup. Single-writer discipline for `check_workers` state; if reordering proves too entangled at execution, surface it rather than force it.

- **R4 — operator priority over background nudges.** Gate idle tiers (`main.py:4299`, `:4322`), `GRADER CHECK` (`:4607`), and directive reminders (`:4232`) on `brain._executing_tool` being False, and flip `system_prompt.md:356-358` so an `OPERATOR MESSAGE` gets a brief ack/ETA before the Brain continues multi-step tool work. Nudges are not gates — no enforcement lives here.

- **R5 — cut in-turn fan-out + bound grader latency.** (a) Stop the Brain wrapping its own gated actions in `general-purpose` `Task` subagents (202 calls) via a `PreToolUse` hook allowing only `ironclaude:search-conversations` plus a prompt rule. (b) Bound `send_to_worker`/`post_message` local-grader latency by lowering the `GRADER_TIMEOUT_SECONDS` **class constant** (`orchestrator_mcp.py:400`) — change ONLY the constant; the two tests that override it to 1s/5s are intentional fast-timeout tests and MUST NOT be modified. Opus fallback on grader infrastructure error already exists.

- **R6 — de-duplicate operator_wait alerts.** In `_maybe_capture_operator_wait` (`main.py:2657`): skip the alert when the `worker_id` is already in `_get_pending_confirmation_waits()` (the 15-min heartbeat already renders it), key `_operator_wait_alerted` on `(worker_id, directive-status)` rather than the paraphrased question, and keep the alerted marker across the TTL prune. One alert per pending directive instead of one per restart (was 21×).

- **SF1 — stop pytest polluting `/tmp/ic/daemon.log`.** Test runs write into the live daemon log (test lines with `pytest-of-roberthyatt` paths, `reply-to:not-a-ts`), corrupting latency stats. Redirect logging to a test-scoped path (fixture/env) so tests never touch `/tmp/ic/daemon.log`.

- **SF2 — close the lookback-enforcer gap if real.** `commander/hooks/startup-lookback-enforcer.sh` exists (dual-layer guardrail d1040: gates spawn_worker/approve_plan/reject_plan/send_to_worker/kill_worker on `/tmp/ic/lookback-slack`+`lookback-ledger` flags; Python `_lookback_slack`/`_lookback_ledger` in brain_client.py; flag persistence is load-bearing). Fable found it may not be registered in the Brain's `settings.json`. Investigate (execute-stage) → produce a findings note establishing whether the 48h-lookback gate is enforced anywhere. If genuinely unregistered, register/repair it WITH tests proving the gate blocks the 5 tools until both flags are set. This STRENGTHENS a guardrail (closes a gap); it never weakens one.

## Components / waves

- **Wave 1 (low-risk, high-value, independent):** R1, R2, R6, SF1.
- **Wave 2 (daemon-loop scheduling, entangled, medium risk):** R3, R4, R5. Sequenced so R5's grader-constant change and R3's grader-bounding don't collide; single-writer discipline for the loop.
- **Wave 3 (guardrail):** SF2 — investigation task (findings note) THEN a conditional register/repair task gated on a confirmed gap.

## Data flow (interactive path, after fixes)

Operator Slack → socket → fast-lane poll (~2-3s, R3) → `brain.send_message` (idle Brain: new turn; no 30-min restart wiping context, R1) → Brain text with `[reply-to:]` → prefix stripped (R2) → threaded reply + ✅ under the operator message, no duplicate narration, no 57-157s post_message grader detour.

## Error handling / guardrail-safety

Every change is health-check (R1), routing (R2), scheduling (R3/R4), latency-bounding/fan-out (R5), or surfacing (R6/SF1) — none touches a review verdict, workflow transition, worktree, git authority, or trust check. SF2 strengthens enforcement. R1 keeps all real-failure restart paths; R5 keeps grader rejection semantics and the 1s/5s test overrides; R4 nudges remain advisory.

## Testing strategy

TDD per code change (RED → GREEN → stage): R1 (ping-probe fires/does-not-fire under idle vs unanswered-ping; real-failure checks still trip), R2 (the missing `[NARRATION] [reply-to:]` seam test), R3 (fast-lane poll cadence + grader-bound timeout without reordering side effects), R4 (nudges gated on busy), R5 (grader-constant bound — assert via the class attribute, do NOT touch the 1s/5s overrides; the `Task` PreToolUse allow-list), R6 (single alert per pending directive across simulated restarts). Docs (`system_prompt.md`, `settings.json`) verified by grep markers. SF2: an execute-stage findings note precedes any registration change; the fix (if any) ships with a gate-blocks-until-flags test. Run the full commander suite before staging.

## Implementation notes

- **Episodic constraints (must honor):** #1364 SIGHUP restart-race (don't reintroduce via R1); #1311/d712 grader-timeout is a class constant with two intentional 1s/5s test overrides (R5); d1040 lookback flag persistence is load-bearing and daemon re-arms on startup (SF2).
- **Verify at planning:** the current `GRADER_TIMEOUT_SECONDS` value (episodic says 300s at `orchestrator_mcp.py:400`; the Fable report cited 600s from hooks-config — reconcile before bounding), and every `main.py` line anchor (`:2657`, `:4299`, `:4322`, `:4607`, `:4232`, `:4664-4683`) against current source.
- **Deploy:** these are Commander source + Brain system-prompt/settings changes; they take effect on the next `make run` Commander restart (operator-timed), not on commit. No push without an explicit operator go.
- Project-agnostic; no game/pf2e specifics. Lands as staged changes on top of `fd1cfed` (v1.1.9).
