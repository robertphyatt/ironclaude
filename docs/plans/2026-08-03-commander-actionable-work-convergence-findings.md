# Commander Actionable-Work Convergence Findings

> **Date:** 2026-08-03
> **Status:** Complete; source, regression, live aging, and restart-durability acceptance passed
> **Scope:** Evidence for `docs/plans/2026-08-03-commander-actionable-work-convergence.plan.json`

## Protected scope

- `AGENTS.md`: `13161859a034afc49c04940ed8424eb52710a3125aaccb21e556d1ca62ea4eb5`
- `commander/config/ironclaude.json`: `bac970ca9d7d26bc6c11ba2d3225b0d7a237fb2bf65c22ec49af2f9e97dee5c8`
- Both files remained modified by the operator, byte-identical to the planned baseline, and unstaged.
- No commit or push occurred.

## Task evidence

### Task 1: durable acknowledgment

- RED: 8 intended failures, 1 pass, 614 deselected.
- Targeted GREEN after assertion repair: 9 passed, 614 deselected.
- Full prescribed Task 1 regression: 623 passed.
- Boundary review: A; testing-theatre audit clean.
- Verification covered persistence across reopen, reciprocal disposition exclusivity in both insertion orders, a two-connection race with exactly one stored disposition, continued directive supersession, validation with zero rows on rejection, immutable repeats, both conflict directions, and the actual FastMCP wrapper.

### Task 2: convergent accounting and callable parity

- RED: 6 intended failures, 11 passes, 348 deselected.
- Focused GREEN: 17 passed, 348 deselected.
- Full prescribed Task 2 regression: 365 passed.
- Boundary review: A; testing-theatre audit clean.
- Verification covered acknowledged-message quiescence, aging cleanup, all existing idle tiers, directive/acknowledged/unresolved audit classification, both Brain instruction surfaces, and fail-visible stale Codex MCP inventory.

### Task 3: worker-plan target validation

- RED: 8 intended failures, 75 deselected.
- Focused GREEN: 8 passed, 75 deselected.
- Full prescribed Task 3 regression including Slack parser coverage: 187 passed.
- Boundary review: A; testing-theatre audit clean.
- Verification covered queue-time and consumer-time validation, registered status/session use, disappearance after queue, recorded remote SSH routing, false `send_keys` results, and valid local approval/rejection.

## Combined regression

- Original combined focused regression: 1,175 passed in 138.60 seconds.
- Original full Commander suite: 2,512 passed, 1 pre-existing skip in 254.61 seconds.
- Final continuation combined regression: 1,196 passed in 137.01 seconds.
- Final continuation full Commander suite: 2,533 passed, 1 pre-existing skip in 261.48 seconds.
- Transport-enforcement final combined regression: 1,220 passed in 144.18 seconds.
- Transport-enforcement final full Commander suite: 2,557 passed, 1 pre-existing skip in 267.87 seconds.

## Shared reply-transport continuation

### Shared parser and daemon path

- RED failed at collection because the shared parser did not exist.
- Focused GREEN: 14 passed, 324 deselected.
- Full prescribed regression: 338 passed in 53.31 seconds.
- Boundary review: A; testing-theatre audit clean.
- Verification covered strict digits-dot-digits timestamps, distinct malformed leading markers, unchanged unmarked content, marked-reply precedence over operator-wait capture, marker-only suppression, exact threading, and delivery-gated reaction.

### Orchestrator MCP path

- RED: 5 intended failures, 7 passes, 598 deselected.
- Focused GREEN: 12 passed, 598 deselected.
- Full prescribed regression: 610 passed in 118.55 seconds.
- Boundary review: A; testing-theatre audit clean.
- Verification covered cleaned-body grading, exact `thread_ts`, malformed/marker-only suppression before grading or Slack effects, post-before-reaction ordering, failure/rejection suppression, and unchanged unmarked top-level posting.

### Cross-module malformed-marker regression

- First combined continuation run exposed one stale expectation: 1 failed, 1,195 passed in 147.05 seconds.
- The sole contradictory test expected `[reply-to:abc]` to become heartbeat chatter even though the approved strict parser drops malformed leading marker-like input.
- Terra RED: 1 failed, 82 deselected.
- Terra focused GREEN: 1 passed, 82 deselected.
- Full `test_main_validate.py`: 83 passed in 0.34 seconds.
- Main-context confirmation: 1 passed, 82 deselected.
- Boundary review: A; testing-theatre audit clean. Production behavior did not change in this final task.
- Final continuation plan review: initial `HAS-ISSUES` was remediated in plan artifacts; fresh report-only `gpt-5.6-sol` peer review returned `SOLID` against the current plan hash.

## Stale directive reconciliation

- Before: directive `1457`, source timestamp `1785731067.460039`, status `pending_confirmation`; no matching worker.
- Action: invoked the existing `update_directive_status` FastMCP wrapper with the live Slack client.
- After: directive `1457` is preserved with status `completed` and updated timestamp `2026-08-03 22:43:16`; no matching worker was created.
- Repository status did not change from this live reconciliation.

## Runtime restart

- Existing identity-checked CLI sent SIGHUP to Commander PID `66653`; no second `make run` was started.
- The same PID executed the normal shutdown and `os.execvp()` restart sequence.
- Fresh startup evidence began at `2026-08-03T22:43:45.097924+00:00`.
- Slack Bolt established a new Socket Mode session and reported running.
- Brain startup completed without `orchestrator MCP inventory missing tools`.
- Process inspection found one Commander process and one attached Codex app-server. The Brain app-server resolved to Codex model `gpt-5.6-terra`.

### Final continuation restart

- A sandboxed CLI attempt failed safely because its internal `ps` identity query was denied; it did not signal or start any process.
- Escalated read-only inspection proved PID `66653` still belonged to the sole `ironclaude.main` process and had one attached Codex app-server.
- The same identity-checking CLI then sent SIGHUP to PID `66653`; no second daemon was started.
- The same PID completed shutdown and `os.execvp()` and logged fresh startup at `2026-08-03T23:50:14.810031+00:00`.
- Slack Bolt reported running at `17:50:15`; Brain SDK and Commander startup completed at `17:50:16`.
- Post-restart inspection found one Commander process and one attached Codex Brain app-server, with no `orchestrator MCP inventory missing tools` error.

## Failed live acceptance

Post-restart aging recovery surfaced an operator-authored non-actionable PF2e status question with source timestamp `1785790011.533349`.

- One immutable acknowledgment exists for the exact timestamp.
- No directive, worker, or worker event was created for it.
- Authoritative Slack `conversations.replies` returned only the operator root message; no threaded Brain reply exists.
- Daemon logs show the first status response was routed under the heartbeat instead of under the operator message.
- Raw Codex Brain rollout evidence shows the Brain later called orchestrator `post_message` with content beginning `[reply-to:1785790011.533349]`.
- `OrchestratorTools.post_message()` graded and posted that content as a top-level message because it does not parse the marker or pass `thread_ts`; only daemon `poll_brain_responses()` implements the marker contract.

This falsifies the design premise that updating Brain instructions plus daemon response parsing covers every existing direct-reply path. The loop must retreat and add one shared reply-routing contract across daemon output and orchestrator MCP posting. A final live gate still requires one fresh operator-authored non-actionable status question after the repair, followed by exact acknowledgment, no directive/worker, one threaded reply, restart, and no aging/idle nudge after the applicable threshold.

## Second failed live acceptance gate

- Directive `#1457` remains preserved with source timestamp `1785731067.460039`, status `completed`, and updated timestamp `2026-08-03 22:43:16`.
- No worker row references `#1457`. The historical `brain_decision` event for target `d1457` remains evidence of the previously rejected arbitrary-target path, not worker creation.
- Operator supplied a fresh non-actionable status question at exact timestamp `1785801581.255109` after the final restart. No bot-authored or synthesized substitute was used.
- Slack delivery partially passed: one cleaned Brain response was delivered in the containing Slack thread; posted text had no transport marker; the exact operator message received eyes and check reactions; no directive, worker, or worker event was created from it.
- Durable disposition failed: `operator_message_acknowledgements` has no row for `1785801581.255109`.
- Raw Codex Brain rollout proves the turn used memory retrieval and then emitted `[reply-to:1785801581.255109]`; it never invoked `acknowledge_operator_message`.
- Runtime inventory and source prove the acknowledgment tool exists, startup requires it, and both Brain instruction surfaces say it must succeed before a direct reply. The failure is therefore not missing exposure or stale runtime.
- Existing MCP `post_message` and daemon marked-reply transport accept and deliver a direct reply without independently requiring its durable acknowledgment. Existing tests separately prove prompt wording, acknowledgment storage, and reply delivery, but do not fail when reply delivery occurs without acknowledgment.
- Root cause: durable disposition is a prompt-only precondition. Codex can omit the tool call while transport still posts and reacts, so source enforcement and an integrated regression are required. Restart durability and threshold-quiescence checks were not run because the prerequisite acknowledgment failed.

## Transport-enforcement continuation

### Plan and Task 1

- Fresh report-only `gpt-5.6-sol` review of the complete operator provenance, design, requirements, human plan, and machine plan returned `SOLID` for fidelity and efficacy.
- Task 1 used one sequential `gpt-5.6-terra` worker. RED failed at collection because `persist_operator_message_acknowledgement` did not exist.
- Focused GREEN: 12 passed, 624 deselected.
- Full `test_db.py` plus `test_orchestrator_mcp.py`: 636 passed in 84.89 seconds.
- The shared helper uses only the injected connection, validates exact timestamps and nonblank reasons, returns immutable acknowledgments, commits before return, converges concurrent duplicate inserts, re-raises directive conflicts, rolls back persistence failures, and returns a normal dict independent of row factory.
- The MCP method delegates to the shared helper. Its test monkeypatches only the imported delegation seam; helper behavior and the two-connection races use real SQLite connections.
- Boundary review: A; testing-theatre audit clean.

### Task 2

- Task 2 used a second sequential `gpt-5.6-terra` worker.
- RED: 9 intended transport-seam failures, 32 passes, 899 deselected.
- Focused GREEN: 41 passed, 899 deselected.
- Full `test_daemon.py`, `test_main_validate.py`, and `test_orchestrator_mcp.py`: 940 passed in 212.00 seconds.
- Both daemon and MCP marked-reply transports now require durable acknowledgment after parsing/grading and before their first Slack post. An existing explicit reason remains immutable; otherwise both use the same classification-only fallback.
- Missing database, directive conflict, or persistence error posts and reacts to nothing. Daemon polling continues after a rejected reply. Unmarked/top-level behavior remains unchanged.
- Commit-before-post tests observe the acknowledgment from a second independent SQLite connection inside the first Slack-post callback. A real failing `SlackBot` proves the committed classification remains, the exact thread is queued, and no check reaction occurs.
- Boundary review: A; testing-theatre audit clean.

## Successful final live acceptance

### First restart and fresh message

- After 1,220 focused and 2,557 full-suite tests passed, the identity-checking CLI sent SIGHUP to the sole Commander PID `66653`; no duplicate daemon was started.
- The same PID completed shutdown and `os.execvp()` and logged fresh startup at `2026-08-04T00:58:16.362667+00:00`.
- Slack Bolt reported running, Brain SDK startup completed, one Codex Brain app-server attached, and no `orchestrator MCP inventory missing tools` error appeared.
- The operator then authored the clearly non-actionable question `what is the current status of the pf2e pipeline now?` at exact timestamp `1785805709.575399`. It was not bot-authored, synthesized, or reused from either failed gate.
- Exactly one immutable acknowledgment exists with reason `Status inquiry only; reporting verified current PF2e pipeline state without creating a directive.` and `created_at=2026-08-04 01:08:59`. The Brain's explicit semantic reason therefore won; the transport fallback was not needed.
- No directive, worker event, or worker was derived from that message. Directive `#1457` remained preserved with status `completed` and no matching worker.
- Authoritative Slack `conversations.replies` found the source in containing thread `1785792371.908909` and exactly one cleaned Brain reply at `1785805764.576489`. The posted text contained no `[reply-to:]` marker.
- The exact source had one eyes reaction and one check reaction. The check path remained delivery-gated.

### Aging threshold and second restart

- The live monitor remained active beyond 36 minutes after intake, crossing the 30-minute eligibility boundary and at least one complete five-minute message-aging scan.
- No `UNPROCESSED MESSAGE`, unprocessed-message idle escalation, duplicate reply, directive, worker, or matching event appeared for `1785805709.575399`.
- The identity-checking CLI sent a second SIGHUP to the same sole PID `66653`. Fresh startup completed at `2026-08-04T01:46:05.910737+00:00`, with Slack Bolt running and Brain SDK ready.
- After restart, the acknowledgment count remained one; its exact reason and `created_at` were unchanged. Directive and event counts remained zero.
- Slack still contained exactly one cleaned reply and the same eyes/check reactions, with no duplicate or nudge.
- Final topology contained one `ironclaude.main` daemon and one attached Codex Brain app-server. No commit or push occurred.

## Mandatory successor PM loops

These are IronClaude product fixes, not operator-specific configuration and not additions to this loop.

1. **Delegated-execution recommendation parity.** Make sequential subagent execution the default recommendation, using the least capable suitable tier (`gpt-5.6-terra` for routine implementation). Main context retains orchestration, state transitions, reviews, and human gates. Inline execution may be recommended only with a clear, task-specific explanation showing why delegation is unsuitable; shared files, serial dependencies, or a live gate alone are insufficient.
2. **Provider-tier and activation-budget truthfulness.** Remove provider-confusing worker presentation such as reporting `claude-opus` when resolved execution is Codex, report resolved client/model, keep professional-mode activation inside the outer MCP deadline, and produce a truthful terminal result plus cleanup before any bounded retry. Preserve the existing provider architecture: no second router, provider alias, silent cloud fallback, retry subsystem, or new operator control.
