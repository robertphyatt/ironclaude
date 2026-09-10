# Commander Responsiveness Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the Commander responsive to the operator (remove the ~30-min idle-Brain restart, fix the dead threaded-reply path, add an operator fast lane, cut self-inflicted latency, de-dup alerts) and close the confirmed 48h-lookback + Task-fan-out guardrail gaps by registering shell-hook enforcement through a version-controlled Brain-settings sync — without weakening any guardrail.

**Requirements:** `docs/plans/2026-09-07-commander-responsiveness-design.md`

**Architecture:** Nine tasks implementing R1–R6 + SF1/SF2 from an evidence-backed read-only analysis, verified against current source. Each is a health-check (R1), routing (R2), scheduling (R3/R4), latency-bound/fan-out (R5), surfacing (R6/SF1), or guardrail-registration (SF2/R5a) change — none touches a review verdict, workflow transition, worktree, git authority, or trust check. Tasks are a serial chain because most touch `main.py`; serializing avoids file-guard collisions. Responsiveness wins (R1–R6, SF1) are the early tasks; the guardrail-registration infra (SF2/R5a) is the last two.

**Tech Stack:** Python (commander daemon) + Brain system-prompt/settings/rules + bash PreToolUse hooks + pytest.

## Execution invariants (reviewer checks commands against these)

- Bash cwd is `commander/`; each command self-contained. Test: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest <file> -q`. Stage with `git -C /Users/roberthyatt/Code/ironclaude add <repo-relative paths>`. `docs/` is gitignored (`add -f`).
- **Brain enforcement facts (source-verified):** the Brain runs with `permission_mode="bypassPermissions"` (brain_client.py:748), which bypasses the `can_use_tool=_tool_guard` callback (:751) — `_tool_guard` never fires (`TOOL_INVOKE` count is 0 across all live brain-session logs). The daemon Brain's live tool enforcement is the SHELL HOOKS loaded via `setting_sources=["project","local"]` (:756). Live `~/.ironclaude/brain/.claude/settings.json` (verified): PreToolUse [block-push (Bash matcher, in `~/.ironclaude/brain/hooks/`), memory-search-enforcer, wiki-synthesis-enforcer, attention-sweep-enforcer]; PostToolUse [attention-sweep-arm (on get_worker_status), block-pin-enforcer (on `mcp__orchestrator__update_ledger`, in `~/.ironclaude/brain/hooks/`)]; `settings.local.json` [brain-orchestrator-guard]. NO Stop hooks. Consequence: (SF2) the 48h-lookback gate in `_tool_guard_logic` (:347-365) is dead code — enforced nowhere — and `startup-lookback-enforcer.sh` is registered in no settings.json → a real gap, closed by REGISTERING the shell hook (the SF2 merge must APPEND without dropping any existing entry, PostToolUse `block-pin` included); (R5a) the subagent-fan-out gate must be a shell hook, not the dead callback.
- **Must-not-change (episodic, load-bearing):** (a) the SIGHUP/`restart_daemon` sequence (#1364) — R1 stays inside `needs_restart`, never touches `restart_daemon`. (b) Heartbeat posts are TOP-LEVEL by design — R2/R4 never thread them. (c) The shared `_local_grader` (:518) serves 5 call sites at 600s — R5b adds a SEPARATE bounded `_message_grader`, never re-times the shared one; the 1s/5s/15s grader test fixtures and `GRADER_TIMEOUT_SECONDS=120` (:469, the Codex/Opus subprocess constant) are untouched.
- Verified anchors (current source): `brain_client.py` `_NARRATION_PREFIX`:139 (applied :848), `needs_restart`:912 (absolute-inactivity :950-960, hard hang-net :936-946, skip-while-executing short-circuit :947-949, normal-timeout :961-972), `send_message`:887, reset-on-SDK-msg :810/:838, `restart()`:1012-1014, `_tool_guard_logic`:319 (lookback gate :347-365), `_tool_guard`:706 (logs `TOOL_INVOKE` :709-711), `bypassPermissions`:748 / `can_use_tool`:751 / `setting_sources`:756. `slack_interface.py` `parse_reply_to_marker`:56-63. `main.py` `run`:4664-4683 (`poll_interval`:4666, `time.sleep`:4683), `_maybe_capture_operator_wait`:2657, marker-parse :2756 (before narration check :2785), `_get_pending_confirmation_waits`:4365 (keys `d{id}` :4380), `_operator_wait_alerted`:1408 (clear-on-input :1921-1923), idle-tier reset :4253-4289 / sends :4296+ / `GRADER CHECK` :4607 / stuck-notify `.add` :4652, logging setup :4686-4698, rules sync :4750-4763 (brain_cwd :4722; NO settings.json handling exists yet). `orchestrator_mcp.py` shared `_local_grader`:518, grader :5779/:6959, `GRADER_TIMEOUT_SECONDS`:469, `get_operator_messages`:1483/7164. `grader.py` timeout :77-79. `system_prompt.md` Urgent Notifications :354-364. `workflow.md` Context Recovery :723-742. `commander/hooks/startup-lookback-enforcer.sh` gates prefixed `mcp__orchestrator__*` tools + AskUserQuestion :48-55, arms on get_operator_messages≥48h :20-31 + update_ledger :34-39, flags `/tmp/ic/lookback-{slack,ledger}-$SESSION_TAG` :16-18 — UNREGISTERED. `worker/hooks/subagent-circuit-breaker.sh:55` gates `TOOL_NAME="Agent"` — but that is the WORKER's newer PATH CLI (2.1.221). The daemon Brain runs the SDK BUNDLED CLI (2.1.59), which emits `TOOL_NAME="Task"` (52 Brain transcripts contain `"name":"Task"`, ZERO contain `"name":"Agent"`) — so the Brain Task-gate (R5a) must match BOTH `Agent` and `Task`. `worker/hooks/tests/test-subagent-circuit-breaker.sh` (bash-test convention: mktemp, fake HOME, fake session_id). `commander/src/brain/orchestrator_claude.md` (NOT under `rules/`) is synced to `brain_cwd/CLAUDE.md` by main.py:4738-4745.

---

## Task 1 (R1): Ping-probe instead of restarting an idle Brain

**Files:** Modify `commander/src/ironclaude/brain_client.py`, `commander/src/ironclaude/main.py`, `commander/src/brain/system_prompt.md`; Test `commander/tests/test_brain_client.py`, `commander/tests/test_daemon.py`

**Depends on:** none

**Step 0 (confirm — execute-stage):** `cat ~/.ironclaude/brain/.claude/settings.json ~/.ironclaude/brain/.claude/settings.local.json` — verified: the live settings has NO Stop hooks (the brain-stop-hook/permission-seeker names live only in the unconsumed `commander/hooks/brain-hooks.json`), so a `[PING]` turn triggers no Stop-hook sweep. It DOES incur one permission-seeking LLM grade per ping (brain_client.py:849→618, on every Brain text incl. `[PING-ACK]`) — ~1 cheap grade / 30 min vs. the full restart+cache-rebuild it replaces. Acceptable; note it in the code comment. Do NOT add a ping-turn grade exemption (out of scope). If a Stop hook has since appeared, surface it.

Add `self._ping_sent_at` (float, init 0.0). In `needs_restart`, place the unanswered-ping check **before** the `:947-949` `_executing_tool` short-circuit: `if self._ping_sent_at and self._last_response_time < self._ping_sent_at and now - self._ping_sent_at > self.timeout_seconds → restart`. On 1800s idle (old :950-960) with no ping outstanding, `send_message("[PING]")`, set `_ping_sent_at=now`, return False. Reset `_ping_sent_at=0.0` on any SDK message (:810) **and** in `restart()` (:1012-1014 — restart zeroes `_last_response_time`; a stale `_ping_sent_at` would fire immediately → circuit breaker :991-998). Keep dead-thread (:932), tool-hang (:936-946), normal-timeout (:961-972). Add a `[PING-ACK]` rule to `system_prompt.md` (reply `[PING-ACK]`, take no action); drop `[PING-ACK]` from the narration in `poll_brain_responses` (main.py).

**RED (test_brain_client.py):** (a) live thread, not `_executing_tool`, idle>1800s, no ping → `needs_restart()==False` and `[PING]` delivered + `_ping_sent_at` set; (b) `_ping_sent_at` set, **`_executing_tool=True`** (pin it), `_last_response_time<_ping_sent_at`, `now-_ping_sent_at>timeout_seconds` → `needs_restart()==True` (runs before the short-circuit); (c) any SDK message resets `_ping_sent_at`; (d) `restart()` resets `_ping_sent_at`; (e) dead-thread/tool-hang/normal-timeout unchanged.
**GREEN → 0 failed** (`pytest tests/test_brain_client.py tests/test_daemon.py -q`).
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/brain_client.py commander/src/ironclaude/main.py commander/src/brain/system_prompt.md commander/tests/test_brain_client.py commander/tests/test_daemon.py`.

---

## Task 2 (R2): Make the threaded `[reply-to:]` path live

**Files:** Modify `commander/src/ironclaude/main.py`; Test `commander/tests/test_daemon.py` — **Depends on:** 1

Strip a leading `_NARRATION_PREFIX` before `parse_reply_to_marker` (parse at :2756 precedes the :2785 narration check) **only** for marker-led text (starting with `_NARRATION_PREFIX + "[reply-to:"`). An **unconditional** strip breaks `TestBrainNarrationThreading` (test_daemon.py:385-391).
**RED:** `f"{_NARRATION_PREFIX}[reply-to:{ts}] body"` threads under `ts` via the leading-marker ✅ branch (not top-level), not duplicated; **and** plain `[NARRATION] status` still posts as narration.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_daemon.py`.

---

## Task 3 (R6): De-duplicate operator_wait alerts

**Files:** Modify `commander/src/ironclaude/main.py`; Test `commander/tests/test_main_operator_wait.py` — **Depends on:** 2

In `_maybe_capture_operator_wait` (:2657): extract the directive id (`\bd(\d+)\b`/`#(\d+)`); skip when `d{id}` is already in `_get_pending_confirmation_waits()` (:4380); key `_operator_wait_alerted` (:1408) on `(worker_id, directive_id, status)` with `SELECT status FROM directives WHERE id=?`; retain across the TTL prune. **Leave** :1921-1923 alone (R1 removes the restart multiplier) — comment the decision.
**RED:** same pending directive across two sweeps → exactly ONE alert; `d{id}` in the pending set → no new alert; composite key dedups paraphrases.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_main_operator_wait.py`.

---

## Task 4 (SF1): Never let tests write the live daemon log

**Files:** Modify `commander/src/ironclaude/main.py`; Test `commander/tests/test_daemon.py` — **Depends on:** 3

Guard the logging setup (:4686-4698) so the `/tmp/ic/daemon.log` `RotatingFileHandler` is NOT attached when `PYTEST_CURRENT_TEST in os.environ` (test_daemon.py:1917/1935 call `main()`).
**RED:** with `PYTEST_CURRENT_TEST` set, the daemon-log handler is not attached.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_daemon.py`.

---

## Task 5 (R3): Operator fast lane in the daemon loop

**Files:** Modify `commander/src/ironclaude/main.py`; Test `commander/tests/test_daemon.py` — **Depends on:** 4

Split `run()` (:4664-4683): FAST lane ~2-3s = `poll_slack_commands`, `poll_brain_responses`, `flush_queue`; SLOW lane at `poll_interval` (15s) = everything else (incl. `post_heartbeat`, which self-throttles :4523-4525 — no separate heartbeat timer). Single-writer discipline for `check_workers`. After R2, marker-led replies skip `_maybe_capture_operator_wait` (:2781-2783) and `[NARRATION]` skips `_validate_brain_message` (:2785); remaining fast-lane synchronous exposure is `_AWAITING_PHRASE_RE` narration (:2663) — offload it. `_detect_worker_prompt` runs in `check_workers` (slow lane). If reordering is too entangled, STOP and surface.
**RED:** (a) fast lane ticks ~2-3s without a full `check_workers` each tick; (b) a slow synchronous grader on the fast lane does not block the next fast poll.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_daemon.py`.

---

## Task 6 (R4): Operator priority over background nudges

**Files:** Modify `commander/src/ironclaude/main.py`, `commander/src/brain/system_prompt.md`; Test `commander/tests/test_daemon.py` — **Depends on:** 5

Gate the **whole** idle-escalation tier block on `not brain._executing_tool`: early-return after the reset logic (:4253-4289, so `_idle_enforcement_start` keeps accumulating) and before the tier sends (:4296+); skip `_heartbeat_stuck_notified.add` (:4652) when gated. `GRADER CHECK` (:4607) is inside `post_heartbeat`, so gating defers it one heartbeat interval (~900s) — acceptable, state it. Add an `OPERATOR MESSAGE` quick-ack rule to `system_prompt.md` (:354-364); keep `[ACTION REQUIRED]`/`[reply-to:]` rules; note `_executing_tool` is a weak busy proxy (clears :838). NEVER thread heartbeats.
**RED:** `_executing_tool=True` → idle-tier sends + GRADER CHECK + stuck-notify suppressed that cycle and `_idle_enforcement_start` keeps accumulating; resume when it clears.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/brain/system_prompt.md commander/tests/test_daemon.py`.

---

## Task 7 (R5b): Bound/keep-alive the operator-facing message grader

**Files:** Modify `commander/src/ironclaude/orchestrator_mcp.py`, `commander/src/ironclaude/grader.py`; Test `commander/tests/test_orchestrator_mcp.py`, `commander/tests/test_grader.py` — **Depends on:** 6

**MEASURE FIRST (caveat):** `grep -c "Ollama returned empty response" /tmp/ic/daemon.log` only confirms empty-Ollama stalls occur in-env — it does **not** measure the target grader, which runs in the ORCHESTRATOR MCP SUBPROCESS (brain_client.py:722-732; `orchestrator_mcp.main()` attaches no log handler), so its latency never reaches daemon.log (those lines are the DAEMON's own graders). To decide bound-vs-keep_alive for the target, add a temporary latency log to the orchestrator grader path, OR (preferred default) treat cold model load as the cause: `keep_alive="30m"` **plus a conservative bound (120s, not 60s)** so a genuine cold load doesn't trip a false `infrastructure_error` → Opus fallback on every cold call. Then bound the local grader on the `send_to_worker` (:5779)/`post_message` (:6959) path. The shared `_local_grader` (:518) also serves :4431/:5253/:5881/:5936 — do **not** re-time it globally. Add `self._message_grader = LocalGrader(config_path=..., timeout=MESSAGE_GRADER_TIMEOUT_SECONDS, keep_alive="30m")` (config key defaulting to **120**, conservative — not 60), used only by those two sites via a signature-compatible `_call_local_grader(sp, up, schema, *, bounded=False)` so the ~50 existing MagicMock seams (e.g. :3510-3518) keep working. Because the target grader's latency isn't observable in daemon.log (it runs in the MCP subprocess), default to `keep_alive="30m"` + the 120s bound rather than a tight 60s (a 60s bound alone → false `infrastructure_error` → Opus fallback on every cold model load). Keep the Opus fallback. Do **not** touch `GRADER_TIMEOUT_SECONDS` (:469).
**RED:** the two sites use the bounded `_message_grader` (timeout default 120 AND `keep_alive="30m"`), NOT the shared 600s `_local_grader`; existing mock seams still pass; `GRADER_TIMEOUT_SECONDS` and the 15s fixture untouched.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/grader.py commander/tests/test_orchestrator_mcp.py commander/tests/test_grader.py`.

---

## Task 8 (SF2): Version-controlled Brain-settings hook sync + register the lookback enforcer

**Files:** Modify `commander/src/ironclaude/main.py`, `commander/src/brain/rules/workflow.md`, `commander/src/brain/orchestrator_claude.md`, `CHANGELOG.md`; Create `commander/src/brain/brain_settings_hooks.json`, `docs/plans/2026-09-07-sf2-lookback-findings.md`, `commander/hooks/tests/test-startup-lookback-enforcer.sh`; Test `commander/tests/test_daemon.py`

**Depends on:** 7

The 48h-lookback gate is enforced **nowhere** for the daemon Brain (see Execution invariants: `can_use_tool` dead; `startup-lookback-enforcer.sh` registered in no settings.json). Close it by REGISTERING the shell hook in the live enforcement layer, via a **version-controlled, template-driven daemon-start sync** (operator chose full sync infra — reproducible, not a hand-edit to the gitignored live settings.json). Build the sync in main.py near the rules sync (:4750-4763): (1) read the repo template `commander/src/brain/brain_settings_hooks.json` (a PreToolUse-entries list); (2) for **every** hook script the template references, deploy it from `commander/hooks/` → `~/.claude/ironclaude-hooks/` (template-driven, so Task 9 adds its script by editing ONLY the template); (3) **merge by APPENDING** each template entry to `~/.ironclaude/brain/.claude/settings.json` `hooks.PreToolUse` **only if its command is absent** — entry-level append, **idempotent** (twice → one), **never a whole-hooks-object rewrite**. The live settings.json holds PreToolUse [block-push, memory-search-enforcer, wiki-synthesis-enforcer, attention-sweep-enforcer] AND PostToolUse [attention-sweep-arm, block-pin-enforcer on `update_ledger`] — the merge MUST preserve ALL and leave PostToolUse untouched (dropping `block-pin-enforcer`, which gates the `update_ledger` this task makes more load-bearing, is the exact regression to prevent); never touch `settings.local.json`. Register `startup-lookback-enforcer.sh` with a no-matcher entry shaped like the live memory-search entry. The hook already gates the prefixed `mcp__orchestrator__*` tools + AskUserQuestion and arms on `get_operator_messages(hours_back>=48)` + `update_ledger` — **deploy it, no edit to the `.sh` source**. Both new hooks `source hook-logger.sh` (not template-referenced; lives in `worker/hooks/`, deployed by `make deploy-hooks`) — document the dependency; the bash tests stage it from `worker/hooks/`. Add `update_ledger` to Context Recovery in `workflow.md` (:723-742) so the ledger flag arms. Findings note recording: the `can_use_tool`=dead evidence (`TOOL_INVOKE`=0 across 10 logs); the other dead gates (wiki-query, ledger-staleness) as follow-up; the **re-arm cost** (`start()` deletes every `/tmp/ic/lookback-*` flag brain_client.py:279-285 and `fork_session=True` changes the hook session_id, so after every restart/compaction the first gated action blocks until BOTH lookback calls re-run — by design per d1040; state it plainly); and that the template holds only the NEW entries (a full settings.json source-of-truth incl. memory-search/block-push/PostToolUse is follow-up). Boy-Scout: fix `commander/src/brain/orchestrator_claude.md` (:34, synced to `brain_cwd/CLAUDE.md` by main.py:4738-4745 — NOT under `rules/`) `get_robert_messages` → `get_operator_messages` (real tool orchestrator_mcp.py:1483/7164). CHANGELOG.

**RED:** (a) test_daemon.py: the fixture settings.json MIRRORS the real one — PreToolUse [block-push, memory-search-enforcer, wiki-synthesis-enforcer, attention-sweep-enforcer] AND PostToolUse [attention-sweep-arm, block-pin-enforcer] — and after the sync ALL survive, PostToolUse is byte-identical (block-pin NOT dropped), the lookback entry appears once, a SECOND run keeps exactly one (idempotent), and every template-referenced script is deployed into a fixture hooks dir; (b) `commander/hooks/tests/test-startup-lookback-enforcer.sh` (bash, pattern of `worker/hooks/tests/test-subagent-circuit-breaker.sh`): mktemp SCRIPT_DIR, copy the hook + `hook-logger.sh` (from `worker/hooks/`) into it, feed JSON with a **fake session_id** (distinct SESSION_TAG → live `/tmp/ic` flags untouched; trap-clean): with neither flag, `tool_name=mcp__orchestrator__spawn_worker` → block (exit 2) and `mcp__orchestrator__kill_worker` → block; `mcp__orchestrator__get_worker_status` → allow (query bypass); after arming via `get_operator_messages(hours_back=48)` + `update_ledger` → `spawn_worker` allow (exit 0).
**GREEN:** `pytest tests/test_daemon.py -q` → 0 failed; `bash commander/hooks/tests/test-startup-lookback-enforcer.sh` → all pass.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/brain/brain_settings_hooks.json commander/src/brain/rules/workflow.md commander/src/brain/orchestrator_claude.md CHANGELOG.md commander/tests/test_daemon.py commander/hooks/tests/test-startup-lookback-enforcer.sh && git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-07-sf2-lookback-findings.md`.

---

## Task 9 (R5a): Shell-hook gate on the Brain's Agent (subagent) fan-out

**Files:** Create `commander/hooks/brain-task-gate.sh`, `commander/hooks/tests/test-brain-task-gate.sh`; Modify `commander/src/brain/brain_settings_hooks.json`, `commander/src/brain/system_prompt.md`; Test `commander/tests/test_daemon.py`

**Depends on:** 8

Cut the Brain wrapping gated actions in general-purpose subagents via a PreToolUse **shell** hook (the `can_use_tool` path is dead). Create `commander/hooks/brain-task-gate.sh` (structure modeled on `worker/hooks/subagent-circuit-breaker.sh` but **DB-free**) with matcher **`Agent|Task`** — verified the daemon Brain (SDK bundled CLI 2.1.59) emits `tool_name="Task"` (52 Brain transcripts have `"name":"Task"`, ZERO have `"name":"Agent"`; subagent-circuit-breaker.sh:55's `Agent` gate is the *worker's* newer PATH CLI). Match BOTH (future-proof against an SDK-bundle upgrade). Read `.tool_input.subagent_type`: allow only `ironclaude:search-conversations`; else `block_pretooluse` telling the Brain to act directly (**fail-closed**: absent/empty `subagent_type` also blocks). The gate MUST be **DB-free** — do NOT copy subagent-circuit-breaker.sh's `db_read_or_fail` sessions lookup (a Brain session has no `sessions` row → it would DATABASE-ERROR-block every dispatch). Register by adding its entry (matcher `Agent|Task`) to `commander/src/brain/brain_settings_hooks.json` — the Task-8 sync then deploys the script + merges the entry, so this task edits ONLY the template + new script + system_prompt, **not main.py**. Keep/add the `system_prompt.md` rule: do not wrap gated actions in general-purpose subagents. Do **not** use SDK `disallowed_tools` (blocks search-conversations too). (The Brain searches episodic memory via `mcp__episodic-memory__` MCP tools directly, not the search-conversations subagent, so this is close to a total subagent ban for the Brain — stricter, not weaker; the allowance is a harmless safety valve.)
**RED:** (a) `commander/hooks/tests/test-brain-task-gate.sh` (bash, mktemp + hook-logger.sh, fake session_id): for BOTH `tool_name="Task"` AND `tool_name="Agent"` — `subagent_type="general-purpose"` → block (exit 2), `="ironclaude:search-conversations"` → allow (exit 0), missing `subagent_type` → block; and the gate makes NO database read (runs with no `sessions` row). (b) test_daemon.py: the template includes the `Agent|Task`-matcher entry and the Task-8 sync deploys+merges it.
**GREEN:** `pytest tests/test_daemon.py -q` → 0 failed; `bash commander/hooks/tests/test-brain-task-gate.sh` → all pass.
**Stage:** `git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/brain-task-gate.sh commander/src/brain/brain_settings_hooks.json commander/src/brain/system_prompt.md commander/tests/test_daemon.py commander/hooks/tests/test-brain-task-gate.sh`.

---

## Notes

- All changes are Commander source + Brain system-prompt/settings/rules/hooks; they take effect on the next `make run` restart (operator-timed, CC env vars stripped per the restart runbook), not on commit. No push without an explicit operator go.
- Deferred: R7 (prompt trimming), R8 (sweep cadence/model) — R1 removes their cause. Follow-up backlog: general `commander/hooks/*.sh` deploy + memory-search-enforcer refresh; a full version-controlled settings.json source-of-truth; the other dead `_tool_guard` gates (wiki-query, ledger-staleness); client-aware hook message templates (Codex `Skill` tool defect).
- Lands as staged changes on top of `fd1cfed` (v1.1.9). Project-agnostic.
