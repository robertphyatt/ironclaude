# Changelog

> **Versioning.** IronClaude uses a single monotonically-increasing `1.0.N`
> patch series — by deliberate convention both features and fixes increment the
> patch number (this is not strict semver). The version is declared in
> `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`,
> `worker/.codex-plugin/plugin.json`, and `.claude-plugin/marketplace.json`, kept in lockstep by
> `commander/tests/test_version_consistency.py`. Each release commit is tagged
> `vX.Y.Z`. Land changes under `## [Unreleased]` as you go, then rename that
> heading to the new version at release time so the entry matches what shipped.

## [Unreleased]

_Nothing yet._

## 1.0.24: workflow durability, Codex compatibility, and Commander hardening

Teaches the Worker that plan/design/task-state artifacts on disk are already durable, adds native direct-mode OpenAI Codex packaging, introduces scope-aware Boy Scout cleanup guidance, makes restricted-runner tests hermetic, and hardens Commander Slack interactions around account switching and operator-decision links.

### Added
- **New skill `ironclaude:workflow-durability`** teaches artifact durability under professional mode; names two anti-patterns (checkpoint anxiety, query offloading) and points at the correct workflow surfaces (`plan-interruption`, investigation PM loop).
- **Shared multi-line `_ic_is_antipattern_proposal` lexicon helper** added to `worker/hooks/hook-logger.sh`, consumed by both `get-back-to-work-claude.sh` and `subagent-drift-detector.sh`. The predicate iterates lines and returns true if any line matches a checkpoint or query-offload lexicon and is not meta-discussion (heading, blockquote, table row, or code fence).
- **New numbered behavioral rule "No Workflow Avoidance Under Stage/Context Restrictions"** added to the `activate-professional-mode` template (compact CLAUDE.md template + full behavioral.md template + concept detection table + canonical-texts library) so all plugin consumers pick it up on next activate. Repo dogfood copies (`.claude/rules/behavioral.md`, `worker/CLAUDE.md`) also updated.
- **"Common Rationalizations" rows** in `executing-plans`, `code-review`, and `brainstorming` SKILL.md files calling out the checkpoint-anxiety, review-banking, and query-offloading rationalizations respectively.
- **New tests** `worker/hooks/tests/{test-antipattern-lexicon,test-gbtw-antipattern-override,test-sad-antipattern}.sh` follow the existing `GBTW_TEST_MODE=1` source-and-call seam used by `test-gbtw-waiting.sh` / `test-gbtw-inflight.sh`.
- **Native Codex plugin manifest** at `worker/.codex-plugin/plugin.json` registers Worker skills and embeds plugin-relative launch configuration for the `episodic-memory` and `state-manager` MCP servers. Claude Code's existing direct-map `.mcp.json` remains unchanged.
- **Scope-aware Boy Scout Rule.** Every current and generated behavioral-instruction surface, including tracked root `AGENTS.md` for Codex repository guidance, rejects “pre-existing” as a reason for silence: clean up evidence-backed defects within authorized scope; otherwise describe the finding, evidence, proposed cleanup scope, and risk and ask permission. Blocked or unsafe findings are recorded rather than suppressed. A propagation guard covers every listed surface.

### Changed
- **`get-back-to-work-claude.sh` stop-hook**: a new `_gbtw_should_rearm_check` predicate is wired into **three** `FIRE_CONTINUATION=false` paths — the brainstorming case (previously an uncovered gap for AP-2 query-offloading, which happens exclusively in Bash-blocked design stages), the bg-tool suppression, and the holding/waiting suppression. An `AskUserQuestion` that IS a checkpoint or offload proposal now re-arms the continuation check rather than silently suppressing it. `CONTINUATION_PROMPT` extended with two new D/F examples plus a PROPOSING-vs-DESCRIBING guardrail (naming the anti-pattern in a design doc or skill discussion remains grade A).
- **`subagent-drift-detector.sh`** (previously a 46-line no-op that only cleaned up the subagent_sessions link) now reads the subagent's last assistant text from the transcript and blocks anti-pattern proposals via `block_stop` across five workflow stages (`executing`, `reviewing`, `brainstorming`, `plan_ready`, `final_plan_prep`). Existing `DELETE FROM subagent_sessions` cleanup and `db_audit_log` run BEFORE the block check so no rows leak on a blocked stop.
- **Codex-compatible background sync hook.** The SessionStart handler no longer sets Claude Code's `"async": true` metadata. Its existing `--background` CLI path already spawns a detached process and returns immediately, so behavior stays asynchronous without asking Codex to run an unsupported async hook.
- **Operator-wait links now require matching decision context.** Commander retains only fully delivered top-level Brain posts as link candidates and adds a permalink only when the candidate references the same worker extracted from the wait. Threaded chatter, partial deliveries, unrelated workers, and missing context produce a linkless alert rather than a misleading link.
- **Direct OpenAI Codex compatibility is explicit.** Direct Worker mode supports Claude Code and OpenAI Codex. Commander continues to orchestrate Claude Code sessions only; Codex-backed Commander workers are not included in v1.0.24.

### Fixed
- **Brainstorming-stage coverage gap for query offloading.** Prior wiring only touched the two downstream suppression blocks; the mainline `case *brainstorming*)` at the top of the case statement set `FIRE_CONTINUATION=false` unconditionally, so a subagent-based Stop event in brainstorming (the primary AP-2 surface — the stage where Bash is blocked) escaped the check entirely.
- **Codex imports no longer omit episodic-memory sync or the state-manager MCP.** Codex skips handlers marked `async`, and the Claude-only plugin manifest did not expose either MCP server to Codex. The new Codex manifest plus synchronous hook declaration removes both registration failures.
- **Slack `/login` handles noisy paste-back flows and silent waits.** Login-code parsing trims appended fragments, query strings, and URLs that cannot be part of a device code. The relay detects a CLI re-prompt after submission, emits throttled “still completing” feedback, surfaces a request for a fresh code, and logs the bounded hard timeout before killing and reaping the child process. Failed or incomplete sign-ins continue to preserve the previous account.
- **Restricted-runner tests are hermetic.** Four orphan-worker tests now exercise tmux-selection contracts through deterministic doubles instead of the operator's live server, and the wiki redirect test uses `socket.socketpair()` instead of binding a TCP listener. Coverage is preserved without environment-dependent skips.
- **Workflow-avoidance enforcement preserves multiline text and ordinary proposal grammar.** SubagentStop now classifies the complete final assistant text block, and the shared deterministic predicate recognizes common permission forms such as “Would you like me…”, “Do you want me…”, “Could we…”, and “May I…” while retaining line-scoped documentation exemptions.
- **Slack login no longer loses an immediate rejected-code re-prompt.** Submission state is published before stdin delivery under the relay synchronization boundary and rolled back on delivery failure, so a concurrent CLI re-prompt reliably asks the operator for a fresh code.
- **Hook regression harness fails on missing assertion paths.** The SubagentStop shell test rejects unexpected output and requires its exact expected pass count instead of allowing an unentered conditional to exit successfully.

## 1.0.23

Reorganizes how the Commander's autonomous "Brain" talks to you over Slack — so the channel is neither too noisy nor too quiet — and hardens the Fable-model fallback so a state file that can't be cleared can't strand the Brain on Opus.

**Background for readers new to Commander mode:** the Commander runs an autonomous "Brain" session that drives worker sessions and reports to you in a Slack channel; every ~15 minutes it also posts a **heartbeat** — a short status summary of what the workers are doing. To keep the channel readable, a prior release started silently discarding any Brain message that didn't reference a tracked work item (a "directive"). That removed cryptic status spam, but it also discarded the Brain's *direct answers to your own questions*, so asking the Brain something in Slack could get no visible response.

### Fixed
- **Direct answers to operator questions were silently dropped.** They are now delivered as **threaded replies** under the message you sent, and your message gets a ✅ reaction once it's answered, so you can see a reply landed without scanning the channel. The Brain marks a reply by prefixing it with `[reply-to:<slack-timestamp>]`, echoing the timestamp of the message it's answering — a convention documented in its system prompt. If the Brain omits the marker, the message falls through to the tactical-chatter path (below) instead of a drop path, so it still reaches you. Router in `poll_brain_responses` (`commander/src/ironclaude/main.py`); covered by `test_daemon.py` and `test_main_validate.py`.
- **A Fable-outage state file that couldn't be cleared stranded the Brain on Opus.** The Commander records "Fable is temporarily unavailable" in a small on-disk flag so it can fall back to Opus without a live API call. If *clearing* that flag failed — a disk-full or permission error on the file unlink, both observed in production — the flag stayed on disk with a future expiry and kept Fable suppressed for up to 24h even after it had recovered. `clear_fable_unavailable` (`commander/src/ironclaude/fable_availability.py`) is now fail-open: when it can't delete the file it truncates it to empty (which needs no disk allocation, so it survives the `ENOSPC` that defeats unlink and atomic-replace), and the read path already treats an empty flag as "Fable available." Covered by `test_fable_availability.py`.
- **The grader emitted an invalid model id for non-opus models, which was the *source* of the spurious Fable outages above.** The LLM grader spawns `claude -p --model <grader_model>[1m]`, but the `[1m]` 1M-context suffix is only valid for models that need it (opus); Fable 5 and Sonnet 5 have 1M natively and reject it, so a `fable`/`sonnet` grader produced `fable[1m]`/`sonnet[1m]` — the exact "issue with the selected model" error that tripped the Fable-availability flag. `_call_grader` (`commander/src/ironclaude/orchestrator_mcp.py`) now reuses `brain_client._model_needs_1m_beta` to append `[1m]` only for opus; Fable/Sonnet launch bare. The worker-spawn path was already correct. Covered by `test_orchestrator_mcp.py`.
- **The gemma4 shadow grader could fall into token-repetition loops.** The shadow grader's Ollama requests now pass `repeat_penalty: 1.3` alongside `temperature`/`num_ctx` (`commander/src/ironclaude/shadow_grader.py`), stopping gemma4 from looping on repeated tokens. Covered by `test_shadow_grader.py`.
- **Long Brain replies and tactical chatter never reached Slack, and the ✅ "answered" reaction fired even when the reply post failed.** The reply and heartbeat-threaded chatter paths in `poll_brain_responses` skipped the 39000-char chunking the directive-status path had, so a Brain message near Slack's ~40000-char per-message limit was rejected, caught, and retried indefinitely — never delivered, with a permanent stuck queue entry. Separately, the reply branch stamped the operator's message with a ✅ reaction unconditionally, ignoring `post_message`'s return value, so a reply that failed to post still marked the operator's question "answered." All three `*Brain:*` post branches (reply, chatter, directive-status) now route through one `_post_brain_message` helper (`commander/src/ironclaude/main.py`) that chunks under a shared `_BRAIN_POST_CHUNK = 39000` and uses **all-chunks-delivered** semantics — it returns the ts of the first successful chunk only when every chunk landed, and `None` if any chunk failed — so the reply-branch ✅ waits for full delivery instead of firing on the first chunk. Failed chunks still queue for retry via `SlackBot.post_message`'s existing except path. An empty-text guard short-circuits the helper on empty/whitespace-only input, so a bare `[reply-to:<ts>]` marker no longer produces a `*Brain:* ` ghost post; the reply branch logs at INFO when the helper returns `None` (empty body or chunk failure) so the drop is observable in `daemon.log`. Covered by `test_main_validate.py` and `test_enforcement.py`.

### Added
- **Tactical detail is threaded, not dumped.** Routine Brain chatter (progress notes, retries) is now threaded under the most recent heartbeat instead of the main channel — expand the thread to read it, ignore it otherwise. Directive-status updates still post to the main channel. The one message class still suppressed is the Brain echoing the Commander's own internal control markers back at it, which would otherwise form a feedback loop in the thread. The daemon records each heartbeat's Slack timestamp to thread chatter under it.
- **"Waiting on you" escalations link back to context.** When the daemon flags that it needs an operator decision, the Slack alert now includes a permalink to the relevant message, so you can jump straight to what needs attention. New `SlackBot.get_permalink` / `update_message` helpers (`commander/src/ironclaude/slack_interface.py`) fetch the permalink and edit the alert in place to append a `Link:` line; the Brain-authored `[BLOCKED]` escalation template (`commander/src/brain/rules/workflow.md`) gains a matching `Link:` field for the case where no Slack message exists yet. Covered by `test_main_operator_wait.py`.

### Changed
- **Retries keep their thread.** The Slack notification queue now stores each queued message together with its thread and re-posts a failed message back into that thread, so a transient Slack outage no longer leaks a threaded reply into the main channel (`commander/src/ironclaude/slack_interface.py`).

### Removed
- The obsolete "your last message wasn't posted" nudge the daemon used to send the Brain on a dropped message — nothing meaningful is dropped anymore, so it no longer applies.

Version 1.0.22 → 1.0.23 across `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` (kept in lockstep by `commander/tests/test_version_consistency.py`).

> **Deploy note:** the Brain learns the `[reply-to:]` convention from its system prompt, which is read once at daemon startup — restart the daemon to pick it up. Until then, replies fall through to heartbeat-threaded chatter (the safe path above), so nothing breaks in the interim.

## 1.0.22

Commander Slack-responsiveness overhaul (the brain never goes silent on a slow/unreachable Ollama), a Slack `/login` account-switch flow, a reason-aware Fable-availability gate, and several brain/heartbeat/guardrail directives.

### Added
- **Slack `/login` — switch the Anthropic account for the Brain + workers from Slack.** `login` (plain text or `/ironclaude login`) spawns `claude auth login`, relays the sign-in **URL** to Slack; the operator authorizes in a browser and pastes the code back with `login code <…>` (the live flow is device-code / paste-back — confirmed against `claude auth login --claudeai`). On a **verified** sign-in (`claude auth status` confirms the account) the daemon SIGHUP-restarts onto the new credential; an unverified, failed, or timed-out attempt never restarts and leaves the previous account intact. Implemented as a non-blocking, background-reader `AuthRelay` (`auth_relay.py`) wired into the daemon dispatch + poll loop (`main.py`), with `login`/`login code` parsing in `slack_interface.py`. Hardened (adversarial review): a per-session **generation guard** so a killed session's reader can't bleed a stale URL; a bounded **verify-retry** across ticks so a transient `claude auth status` flake doesn't produce a false "previous account intact" claim; the SIGHUP restart handler **aborts** an in-progress relay so its `claude auth login` child isn't orphaned; the success notice is **flushed** before `execvp`. Suites: `test_auth_relay.py`, plus login-wiring tests in `test_main_validate.py`.
- **Usage-limit alert.** When a Brain response signals `You've hit your limit` (or a worker rate-limit / session-limit), the daemon posts a **throttled** (per-reset-window cooldown) "⚠️ Usage limit hit — send `login` to switch accounts" prompt so the operator knows when to switch (`detect_account_limit` in `main.py`). Shares its signal set with the Fable-availability gate.

### Fixed
- **The brain→Slack path no longer blocks on a slow/unreachable Ollama.** A Brain-message validator on the brain→Slack egress path shared the grader's 600s timeout, and `OllamaClient` never failed over to the localhost fallback on a *read* timeout — so a hung endpoint silenced Slack for 10-minute stretches (repeated `Ollama timed out after 600s` with queued messages flushing the instant each timeout fired). Fixes:
  - `ollama_client.py`: a unified `_attempt` loop fixes the **failover-on-read-timeout** regression (a read timeout now tries the fallback, not just a connection error) and adds a **URL-keyed circuit breaker** (`_CircuitBreakerRegistry`, `threading.Lock`-guarded) — opens on the first transport failure, admits a single half-open prober, backs off exponentially (5s ×2, cap 300s), routes to the fallback while a URL is open, and fails open with `{"infrastructure_error": True}` only when both endpoints are down. Parse/format failures never trip the breaker. HTTP 4xx/5xx are treated as healthy (not an outage). Timeout messages now report connect-vs-read honestly.
  - `grader.py`: `LocalGrader` gains `timeout` and `keep_alive` constructor overrides; the message-path graders (`main.py`, `brain_client.py`) use a short **15s** timeout with bounded classifier input (`truncate_middle`) and a warm-model `keep_alive`, while the real grader path (`orchestrator_mcp.py`, 600s) is left untouched. Config is **hot-reloaded** on file-mtime change (no full daemon restart to pick up a URL edit).
  - `notifications.py`: the heartbeat surfaces a "validator degraded (Ollama endpoint(s) down)" marker (in both the normal and no-workers paths) when a breaker is open.
  - Existing per-site fail-open defaults are preserved via the existing `infrastructure_error` sentinel — zero call-site logic changes. New/expanded suites: `test_ollama_circuit_breaker.py`, `test_ollama_client.py`, `test_grader.py`, `test_main_validate.py`, `test_notifications.py`, plus an autouse breaker-reset fixture in `conftest.py`.
  - **Deploy:** commander code — restart the daemon once (loads the code *and* picks up the corrected Ollama URL the daemon had been caching stale).
- **d1374 — heartbeat no longer claims "Waiting on <operator>" with nothing to act on.** The heartbeat suppressed the false-positive "waiting" line when there is nothing pending on that audience.
- **d1364 — `restart_daemon` no longer thrashes in a restart loop.** Added a `directive_id` parameter that atomically marks the directive completed in the DB *before* the fork/SIGHUP, so a new Brain session doesn't see the directive as still `in_progress` and re-trigger `restart_daemon` (~every 55s).
- **d1389 — heartbeat no longer emits false-positive "WAITING ON ROBERT" lines.** A fast-path regex + an empty-`COMMANDER` guard suppress spurious "waiting" heartbeats, and the operator-wait TTL is lowered 1800→600s.
- **d1391 — guard hooks normalize a `make -C <dir>` invocation before the `make test*` allowlist check** (`brain-orchestrator-guard.sh`, `professional-mode-guard.sh`), so the allowlist matches regardless of the `-C` working-directory form.
- **d1398 — heartbeat shows an `*Active Workers:*` header** before the running-workers list when waits are present, so active workers no longer appear under the `WAITING ON` banner (`format_heartbeat` in `notifications.py`).

### Changed
- **d1362 — heartbeat two-section waiting display.** Waits are now split into `WAITING ON COMMANDER` / `WAITING ON <operator_name>` sections that always appear together when anything is holding on either audience.
- **d1384 — BrainClient default model switched from Opus to Sonnet** (native 1M context), for the brain orchestrator loop.
- **Reason-aware Fable-availability gate.** `fable_availability` now classifies *why* Fable is unavailable and sizes the recheck window to the cause instead of a blanket 24h blackout: a genuine model outage keeps the 24h window + downgrade to a working Opus, while a `brain-detected`/`spawn-died`/overload cause re-probes in ~1h. The Brain's detection sites (`brain_client.py`) forward the real error text so an outage classifies correctly, and `resolve_worker_type`/`resolve_advisor_model` keep Fable (rather than downgrading to an equally-throttled Opus) when the block is an account-wide usage limit (`classify_reason`/`parse_reset_time`/`fable_block_category`; suites `test_fable_availability.py`, `test_brain_client.py`). A live usage-limit *detector* that would exercise the keep-Fable path is deferred (needs a captured real usage-limit error string — see the `/login` usage-limit signal, which is the shared source). Hardened (adversarial review): an unambiguous model-outage anchor classifies `model_unavailable` even if the text incidentally mentions a usage word; a genuine outage that begins during a keep-Fable `usage_limit` window can re-mark to escalate the category (so `resolve_*` stops keeping Fable); and the orchestrator's "Fable recovered" clear no longer fires on mere tmux readiness while a `usage_limit` is still active.

## 1.0.21

A GBTW stop-hook fix so a worker legitimately waiting on a persistent `Monitor` is no longer falsely blocked with "TASKS STILL IN PROGRESS," plus a brain-notification fix for the turn-in-progress context when the token count is zero.

### Fixed
- **GBTW tasks-in-progress gate is now Monitor-aware.** The hard "TASKS STILL IN PROGRESS" gate in `get-back-to-work-claude.sh` only suppressed on completion-aware background Agent/Bash jobs (`_gbtw_extract_in_flight`), which is blind to a persistent `Monitor` — so a worker watching a long-running suite via a Monitor was blocked on every stop and thrashed against the block-throttle for the run's duration. Added `_gbtw_recent_waiting_tool` (detects `Monitor`/`ScheduleWakeup`/`TaskOutput`/`AskUserQuestion` in the last 3 assistant turns) and wired it into the gate (gate = classifier OR helper). Gate-only: the continuation check already handles Monitor and is left untouched; `run_in_background` is deliberately excluded from the helper (it is covered completion-aware by the classifier, so matching it here would leave the gate suppressed for up to 3 turns after a bg job finished). Unit-tested via the `GBTW_TEST_MODE` seam (`worker/hooks/tests/test-gbtw-waiting.sh` + 8 fixtures); the existing in-flight suite stays green. **Deploy:** `make deploy-hooks` to copy the hook into `~/.claude/ironclaude-hooks/`.
- **Brain notifications surface turn-in-progress context when the token count is zero.** `notifications.py`/`brain_client.py` previously suppressed the turn-in-progress context on a zero token count; it now surfaces correctly. Covered by `test_notifications.py` / `test_brain_client.py`.

### Changed
- Version is 1.0.21 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

## 1.0.20

A grader-transport overhaul (the inline grader now runs a tool-free `claude -p` subprocess with a hard timeout instead of scraping a persistent tmux pane) plus a new **Advisor Fallback** behavioral directive that makes "advisor unavailable" mean "spawn a top-tier subagent for the same review," never "skip it."

### Added
- **Advisor Fallback directive** (`.claude/rules/behavioral.md` #10, `commander/src/brain/rules/behavioral.md` #23, and the `activate-professional-mode` templates + concept-detection table). When the harness-injected `advisor` tool is unavailable, Claude must spawn a top-tier subagent (`Agent`, `model=fable` if Fable is available, else `model=opus`) to perform the same adversarial review rather than skipping it. Baked into the activation skill so it propagates to every IronClaude project (new projects at creation, existing projects on next `/activate-professional-mode`). Presence-guarded by `commander/tests/test_advisor_fallback_directive.py`.
- **`kill_worker` directive fast-path.** `kill_worker` takes an optional `directive_id`; when that directive's status is already `completed`, the kill is approved immediately and the inline grader is skipped (avoids a redundant Opus grade blocking cleanup of already-confirmed work). Opt-in and backward-compatible — absent `directive_id` the grade-or-warn behavior is unchanged, and a failed directive lookup falls through to grading. Note: this is a deliberate, opt-in relaxation of kill-grader enforcement — there is no worker↔directive linkage, so a caller can skip the kill-grade by pointing at any `completed` directive.

### Changed
- **Grader transport replaced: persistent tmux session → per-grade `claude -p` headless subprocess.** The inline grader (`OrchestratorTools._call_grader`) previously drove a persistent `ic-grader` tmux Claude session and read the verdict by **scraping the tmux pane** for a nonce delimiter + a strict single-line-JSON regex. On a large grading prompt the delimiter scrolled out of the 500-line capture window, so a valid verdict the grader produced in ~40s was never parsed → the poll loop ran the full `GRADER_TIMEOUT_SECONDS` and returned a false `F "timed out"`. Worse, `_call_grader` held `_grader_lock` and blocked the brain **synchronously** for up to 600s × a retry ≈ 1200s (~20 min) per grade, freezing the daemon. It now runs one `claude -p` subprocess per grade: the (possibly very large) grading prompt on **stdin**, the avatar system prompt via `--system-prompt-file`, the verdict as `--json-schema`-validated structured output parsed from the `--output-format json` event envelope (both the array and single-object envelope shapes are handled), and a **hard `subprocess.run(timeout=…)`** that kills a hung grade deterministically. `GRADER_TIMEOUT_SECONDS` lowered 600 → **120** (typical grade ~40s; now a real hard kill, not a false poll-timeout; prompt length is logged on timeout so systematic F-on-large-prompt stays diagnosable). Batch (multi-decision) spawn grading wraps its verdict array in an **object** schema (`{"verdicts": [...]}`) — the API rejects a top-level array `--json-schema` — and a lone uncertain decision is graded individually. Verified live end-to-end against the installed `claude` CLI (single-object and object-wrapped batch schemas both accepted).
- **Grader is billing-pinned to Claude Max.** The subprocess env strips every provider/billing routing var — `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` (the local Ollama worker path exports this), `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` — so grading can never be misrouted to Ollama/Bedrock/Vertex or metered API billing.

### Removed
- The entire tmux grader transport: `_ensure_grader`, `_spawn_grader`, `_is_grader_alive`, `_wait_for_grader_clear`, `_do_grader_send_and_poll`, the `GRADER_RESPONSE_<nonce>` scheme, `_grader_session`/`_grader_ready`, the `_GRADER_DEBUG` log globals, the `secrets` import, and `_deactivate_pm_via_sqlite` (its only runtime caller was the deleted spawn path). Obsolete transport tests removed; `test_orchestrator_debug_log.py` deleted.

### Behavior changes (intentional, reduced rigor)
- **The grader is starved of file/exec, agentic, and MCP tools.** The built-in `Task,Bash,Read,Edit,Write,NotebookEdit,Grep,Glob,WebFetch,WebSearch` **and** the agentic tools (`Skill,Workflow,ToolSearch,SendMessage,EnterWorktree,ExitWorktree,CronCreate,CronDelete,CronList,ScheduleWakeup,RemoteTrigger`) are disallowed, and `--strict-mcp-config` drops the plugin MCP servers so no `mcp__*` state-manager mutators are reachable — important because the `kill_worker` grading prompt embeds a worker-controlled log tail (treat it as untrusted). The grader still makes the one `StructuredOutput` verdict call. The three grader system prompts (spawn_worker / approve_plan / kill_worker) instruct it to evaluate **only from the inline evidence** in the prompt; it can no longer independently read a diff or run a command — it grades what it is given.
- **The shadow-concordance Slack report's "Opus tool calls" column is now always empty (M7).** `_last_grader_delta` is permanently `""` (there is no pane to scrape), so `_parse_tool_calls_from_delta` returns nothing for the primary grader.

**Deploy:** (1) **restart the daemon** — the grader-transport change is daemon code; (2) `claude plugin update` + `/reload-plugins` to pick up the `activate-professional-mode` skill change (the Advisor Fallback directive + version bump ship in the worker plugin); the `behavioral.md` directives ship with the repo and reach a project on its next `/activate-professional-mode`.

## 1.0.19

A config-controlled, **unbypassable** tier-up plan review (a higher-tier model reviews each plan with blind, fresh eyes before execution, and the commander cannot skip it or weaken the policy), a grader-reliability fix that stops `kill_worker` grading from timing out on large sessions, and a fix for a latent `create_plan` bug uncovered while building the above.

### Added
- **Config-controlled, unbypassable tier-up plan review.** A higher-tier model reviews each plan with fresh, blind eyes before execution; enforcement is governed by `tier_up_review_policy` in `~/.claude/ironclaude-hooks-config.json`:
  - **`enforced`** (default, fail-secure) — the commander **cannot** reach execution without a genuine tier-up review of the exact plan; the only way to not run it is for a **human** to change the policy.
  - **`commander-choice`** — the former default-yes-but-skippable behavior.
  - **`off`** — no review.
  - **How the gate is unbypassable:** the hard gate lives **inside the TypeScript state-manager MCP server** (MCP calls are not PreToolUse-hookable, so a hook alone would leave a direct-MCP bypass). `start_execution` refuses the `final_plan_prep → executing` transition, under `enforced`, unless a `tier_up_reviews` row matches `sha256(session.plan_json)` — mirroring the existing `mark_executing`/`review_grades` gate. A new `submit_tier_up_review` MCP tool records the review; the **server** computes the plan hash (the agent cannot forge the binding). New `tier_up_reviews` table (`db.ts`, mirrored in `worker/hooks/session-init.sh`). Missing/unreadable/invalid config resolves to `enforced`.
  - **Anti-tamper (agent-can't-weaken-its-own-guardrails):** there is deliberately **no MCP setter** for the policy, and `professional-mode-guard.sh` denies the agent writing `~/.claude/ironclaude-hooks-config.json` via `Edit`/`Write`/`MultiEdit` **and** common `Bash` write vectors (`>`, `>>`, `tee`, `sed -i`, `cp`/`mv`, `ln`/`rsync`, `truncate`, `dd`) in every stage, ahead of the `debug_allow_config_writes` bypass. Honest limitation: this defeats drift, "trivial"-rationalization, and the obvious tamper vectors — not a determined agent with arbitrary `Bash` in the executing stage (that needs OS-level sandboxing); it is the same rigor as every other IronClaude gate.
  - **The review itself** stays blind and report-only, one tier up (Sonnet→Opus, Opus→Fable-unless-unavailable→Opus via the on-disk `fable_unavailable.json` flag, Haiku→Sonnet, Fable→top-tier self-attest), dispatched via the Agent tool with only the plan/design paths and fixed criteria — never the writing rationale, conversation, prior findings, or a diff. On **Revise**, the operator's fixes are applied and `create_plan` is re-called to reload the revised plan (see Changed), then a brand-new blind reviewer runs against it.
- **Presence guards** `commander/tests/test_writing_plans_skill.py` (asserts the old Phase 4.5 is **absent** from writing-plans) and `commander/tests/test_executing_plans_skill.py` (asserts the relocated policy-gated step is present, blind, and policy-aware), so a future skill edit can't silently move or delete the gate.

### Fixed
- **`create_plan` no longer throws `SQLITE_LOCKED`.** `create_plan` wraps its mutations in a `db.transaction`, but `updateSession` (called inside it) ran a WAL checkpoint unconditionally — and a checkpoint on the same connection that holds an open write transaction raises `SQLITE_LOCKED`. This would have broken `create_plan` for every session on deploy; it was latent because no test had ever exercised `create_plan` through its transaction. `walCheckpoint` now returns early when `db.inTransaction` is true (a checkpoint is flush *timing* only — it defers to the next non-transactional write). The only `db.transaction` in the codebase is `create_plan`'s, so this is the complete blast radius.
- **`kill_worker` grader no longer times out on large sessions.** The inline grader was allowed to `Read` the worker's full session log to judge a `kill_worker` decision; on a long-running worker that log is large enough that the grader's investigation blew past `GRADER_TIMEOUT_SECONDS` (600s), failing the grade on infrastructure rather than merit. `kill_worker` now reads a **capped** log excerpt itself (`GRADER_LOG_MAX_LINES`, default 500, env-overridable) and passes it inline in the grader's user prompt, and the grader system prompt instructs it to evaluate log evidence from that excerpt rather than re-reading the file (it may still `Read`/`Bash` for other evidence — diffs, test output). The ssh-host / session / remote-log-dir resolution is hoisted above the grader call so the tail can be read for remote workers too. Covered by `commander/tests/test_kill_worker_log_cap.py`.

### Changed
- **The tier-up review relocated from `writing-plans` (former Phase 4.5) into `executing-plans` (new Step 1.5, after `create_plan`).** Running it after the plan is loaded means the server has the exact, stable plan string to hash, which eliminates file-reading and cross-representation hash mismatches and removes the soft, unenforceable Phase 4.5. `create_plan`'s valid source stages now include `final_plan_prep` so the Revise flow can re-call it to reload a revised plan (rebuilding `wave_tasks`) — otherwise a post-review revision would never reach execution.
- Version is 1.0.19 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

**Deploy (all three layers, or the change is partially inert):** (1) `make deploy-hooks` — the anti-tamper deny (`professional-mode-guard.sh`) and the schema mirror (`session-init.sh`) run from `~/.claude/ironclaude-hooks/`, not the repo; (2) `claude plugin update` + `/reload-plugins` — loads the rebuilt MCP server (`dist/index.js`, committed) and the updated skills; (3) restart the daemon so workers spawn against the rebuilt, gate-enforcing server.

## 1.0.18

Two reliability fixes to the operational plumbing behind the daemon: a daemon restart now always brings worker hooks current (no more forgotten `make deploy-hooks`), and the gemma4 shadow grader is no longer starved of context or silently failing to record its results.

### Fixed
- **Daemon auto-deploys worker hooks at startup.** Worker hooks run from the stable directory `~/.claude/ironclaude-hooks/` (deliberately, so the volatile repo working tree can't be read mid-edit by concurrent workers), and the only way to refresh that directory was the manual `make deploy-hooks` target — a step easy to forget, leaving daemons and worker sessions on stale hooks (exactly what happened on 2026-07-08: GBTW fixes committed, daemon "restarted", hooks unchanged on disk). `main()` now calls `_deploy_worker_hooks(repo_root)` in the same startup-sync family that already copies the brain CLAUDE.md/rules/grader files, mirroring the Makefile target: `worker/hooks/*.sh` → the stable dir (mandatory; a missing source dir or copy failure exits the daemon, matching the adjacent brain-file syncs) and → the latest plugin-cache hooks dir (best-effort; absent cache warns and continues). The latest cache version is chosen by numeric sort (`1.0.16` beats `1.0.9` — lexicographic would invert it, same reason the Makefile uses `sort -V`), and `shutil.copy2` preserves executable bits. Because this runs on every start, including SIGHUP `execvp` restarts, "restart the daemon" now implies current hooks. The `make deploy-hooks` target is retained for hook-only iteration without a restart. Covered by 6 tests (`commander/tests/test_main_validate.py::TestDeployWorkerHooks`): stable-dir copy + exec-bit preservation, numeric latest-version selection, non-version cache dirs ignored, cache-absent warn-and-continue, source-missing `SystemExit`, non-`.sh` files skipped — all with injected paths so no test touches the real home directory.
- **Shadow grader (gemma4) `num_ctx` truncation.** Both Ollama request payloads sent `options: {"temperature": 0.1}` only, so the raw model ran at Ollama's default 4096-token context while the grading conversation (grader system prompts + objective + up to 5×8000-char tool results) far exceeded it; Ollama silently drops the *oldest* context first — i.e. the grading instructions. Both payloads now carry `num_ctx` from a new `shadow_num_ctx` config key (default 32768, matching the proven `ollama_worker_num_ctx`; same root cause as the 2026-06-18 worker num_ctx finding, which the grader never received).
- **Shadow grader concordance rows were never persisted (cross-thread SQLite crash).** The shadow grader runs in a fire-and-forget daemon thread, but the concordance `INSERT` used the daemon's main-thread SQLite connection, so every event posted its Slack concordance report and then failed with `SQLite objects created in a thread can only be used in that same thread` (observed in production logs) — the `shadow_concordance` table never accumulated a single real row, starving every downstream measurement. `_fire_shadow_thread` now resolves the DB file path on the main thread (`PRAGMA database_list`) and the background thread opens its own short-lived connection for the write (WAL mode, set by `init_db`, makes the concurrent writer safe); failure logs an ERROR and drops the row without raising. The regression test runs the `INSERT` from a real `threading.Thread` against a real on-disk `init_db` database — an in-memory or same-thread test would mask exactly this bug class.
- **Shadow grader dropped the model's own analysis.** When gemma4 answered without a tool call, the tool loop broke without appending the assistant `content`, so the final verdict call graded from a transcript missing the model's reasoning. The analysis is now preserved in the transcript before the verdict request.
- **Shadow grader `grep_files` was a dead tool without ripgrep.** The tool shelled to `rg`; on hosts without ripgrep every call failed with `[Errno 2] No such file or directory: 'rg'` (observed in logs), wasting investigation rounds. The grep command is resolved once at import (`rg` if present, else a BSD-compatible `grep -r -m 20 -e` fallback).

### Added
- **`get_shadow_concordance_stats` MCP tool** (read-only): windowed aggregation over `shadow_concordance` (default 7 days, production rows only) returning concordance counts, disagreement-confidence breakdown, and opus-vs-shadow grade pairs — so the brain and operator can review shadow-grading trends without raw SQL. A one-line brain-rules addition (`workflow.md`) directs the brain to review this tool before proposing any grader prompt/model changes: tune against evidence, not impressions. Prompt/rubric/model tuning is deliberately deferred until clean concordance data accumulates (the d1278 assessment's own sequencing: persist → exercise → re-assess → tune), which the persistence fix above finally unblocks. Covered by 3 tests (real-thread persistence; windowed aggregation excluding `test_mode` and out-of-window rows; error dict on a dropped table) plus 4 shadow-grader tests (num_ctx on both payloads, analysis preserved, grep fallback shape).

### Changed
- Version bumped to 1.0.18 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

**Deploy:** daemon restart. As of this release the restart also auto-deploys the worker hooks, so no separate `make deploy-hooks` is needed; the brain-rules line takes effect on the next brain session.

## 1.0.17

Stop-hook false-positive fix: a worker legitimately waiting on a background subagent (dispatched via the `Agent`/Task tool with `run_in_background`) is no longer blocked by GBTW.

### Fixed
- **GBTW "tasks still in progress" gate now detects genuinely in-flight background jobs.** The suppression check in `worker/hooks/get-back-to-work-claude.sh` inspected only the last 3 assistant `requestId`s (`tail -3`); after a worker posted a few text-only "holding" turns while a background subagent was still running, the dispatch scrolled out of that window and the hook blocked — repeatedly, until "max blocks reached, likely false positives" fired. Confirmed against the real failing transcript (`7331628f-1c4d-4ee1-9c9d-347758be418d`). The `tail -3` window is replaced with true in-flight detection: over `tail -n 4000` of the transcript, collect launched background ids (Agent `toolUseResult.agentId`, Bash `Command running in background with ID: <id>`) and completed ids (delivered `<task-notification>` turns with `<status>` ∈ {completed,failed,killed,stopped}, plus the resumed plain-text `agentId: … subagent_tokens:` shape from `SendMessage`), and suppress the block iff at least one launched id has no matching completion. Diagnostics: every invocation logs the launched/completed/in-flight counts. Fail-safe: on missing jq or parse errors, falls through to today's block (never bypasses).
- Covered by 8 fixture-driven bash tests at `worker/hooks/tests/test-gbtw-inflight.sh` (subagent in-flight, subagent completed, in-flight past the old 3-turn window, resumed plain-text completion, background Bash in flight, two-dispatched-one-completed, all four terminal statuses, malformed JSONL line resilience). All 8 GREEN.
- **Fable availability caching and graceful fallback.** Fable is being removed for subscription users on 2026-07-07. Previously, only the always-on Brain had a Fable → Opus fallback (v1.0.15); on-demand Fable uses (spawn a `claude-fable` worker, or send `/advisor fable` to an Opus worker) would silently degrade or hard-fail. The daemon now caches Fable-unavailability in a small state file (`~/.ironclaude/state/fable_unavailable.json`, 24-hour TTL) and redirects at every source: `claude-fable` spawns become `claude-opus`; `/advisor fable` becomes `/advisor opus`; a `session died before ready` on a `claude-fable` spawn sets the flag, posts a one-time `⚠️ Fable unavailable` Slack alert (with the redirect target and the manual re-probe hint), and retries as `claude-opus`. Recovery is intrinsic: when the flag has expired and the next `claude-fable` spawn succeeds, the daemon posts `✅ Fable is back` to Slack. The Brain-side v1.0.15 fallback now also records the flag when it fires on a Fable model, so the worker/advisor paths pick it up automatically. Idempotent per detection episode — one alert per Fable outage, not one per operation. Fail-safe: any state-file error is treated as "Fable available (probe again)" so a corrupt file can never spuriously suppress Fable.
- Covered by 37 new tests: `commander/tests/test_fable_availability.py` (15, atomic write + TTL + transition semantics + resolve helpers, all hermetic via `monkeypatch`), `commander/tests/test_notifications.py::TestFableNotifications` (11, formatter content + mrkdwn escaping), and integration tests in `commander/tests/test_orchestrator_mcp.py` (5, worker-type redirect + advisor redirect + spawn retry + idempotency + recovery), `commander/tests/test_main_validate.py` (2, file-decision-path redirect + passthrough), and `commander/tests/test_brain_client.py::TestModelUnavailableFableTransition` (4, mark-on-fable-only + optional callback + transition-only-callback + no-callback default). All pass; no regressions.

### Changed
- Version bumped to 1.0.17 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

**Deploy:** `make deploy-hooks` (hooks run from `~/.claude/ironclaude-hooks`, not the repo) plus a daemon restart (the Fable-availability + Slack-alerts wiring lives in the commander daemon, not the hooks).

## 1.0.16

Slack observability fix: an intentionally-held worker no longer looks stuck — the operator sees exactly what is waiting on them, in every heartbeat.

### Fixed
- **"Waiting on operator" is now surfaced in every Slack heartbeat.** When the Brain held a worker for the operator's reply, its "Still holding. Awaiting …" status was silently discarded by the no-directive-ref message filter and the heartbeat showed only the raw `executing` stage — so a worker blocked on a human decision looked stuck indefinitely. The daemon now classifies a holding message at the drop boundary (via the grader, for every Brain message — so it works whether or not the message would pass the directive-ref gate) into an in-memory `operator_waits` signal, posts a one-time "⏳ Waiting on you: `<worker>` — <what it needs>" alert, and renders a "⏳ WAITING ON YOU" block in every heartbeat plus a tag on that worker's line. The state clears on the operator's next Slack message (self-healing: if the Brain is still holding it re-affirms next cycle), with a TTL backstop and a bounded map.

### Added
- Bounded Brain feedback on dropped non-waiting messages: the Brain now gets one `[FYI]` notice when a message is dropped for lacking a directive reference, so it stops blindly re-emitting. Guarded against reopening the `CONTEXT_REQUIRED` feedback loop (that the silent drop exists to break) by two mechanisms: it skips messages echoing our own `[CONTEXT REQUIRED]`/`[FYI]` markers, and throttles to at most 2 nudges per 10-minute window, reset on any successful post.

### Changed
- Version bumped to 1.0.16 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

## 1.0.15

Model-tiering release: right-size the whole system around capability-on-demand. The always-on brain runs on Sonnet and reaches Opus/Fable only when a task warrants it, backed by one-tier-up advisors — Fable-level capability on the hardest work without burning the top tier continuously.

### Added
- **Right-Size Every Subagent** behavioral directive — a new Core Principle telling every worker to delegate to subagents liberally and match the subagent model to task difficulty (Fable → Opus → Sonnet → Haiku; use the least capable model that will reliably succeed). Added to all synchronized directive copies: the worker template (`commander/src/ironclaude/templates/worker_claude_md.md`), `worker/CLAUDE.md`, the repo-root and `commander/` `CLAUDE.md`, and `.claude/rules/behavioral.md`. Harmonizes with the existing Subagent Discipline principle.
- **Sonnet default brain (user-overridable).** `brain_model` default changed `fable` → `sonnet`; still overridable via `BRAIN_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, or config. `default_opus_model` stays decoupled (`opus`) so `claude-opus` workers are unaffected. The brain handles routine orchestration itself and escalates to stronger workers on demand rather than running the top tier every cycle.
- **`claude-fable` as a first-class worker + brain escalation policy.** The brain's system prompt now lists `claude-fable` and an escalation policy: routine work → `claude-sonnet`; harder-than-it-can-decide → consult a `claude-opus` worker, then spawn `claude-opus`/`claude-fable` as advised (the brain delegates "fable-worthiness" to Opus rather than judging it itself). The spawn-time grader can recommend `claude-fable`, and an approved `claude-opus` spawn escalates to `claude-fable` only when the grader explicitly recommends it (no unconditional bump).
- **One-tier-up worker advisors.** Advisor model is now selected by worker type via `advisor.advisor_models` (`claude-sonnet` → `opus`, `claude-opus` → `fable`), with the scalar `advisor.advisor_model` as a fallback for unmapped types; `claude-fable` workers get no advisor (top tier). Applied in both the MCP and file-decision spawn paths.
- **Config-flagged `/goal` autonomous dispatch** (`dispatch.use_goal`, default off): when enabled, a spawned worker is given a `/goal` completion condition after professional-mode activation and advisor setup, for more autonomous, less-babysat execution.

### Fixed
- **Brain `fable[1m]` startup crash.** The brain unconditionally appended the `[1m]` suffix + `context-1m-2025-08-07` beta to its model string. Fable 5 and Sonnet 5 have a 1M context window natively and reject that beta, so `fable[1m]` errored on every cycle and wedged the brain. The suffix/beta is now applied only to models that need it to unlock 1M (opus); 1M-native models launch with the bare alias.
- **Message-shaped model-unavailable fallback.** The brain's fallback-to-opus fired only on raised exceptions, but the SDK returned model-unavailability as a normal assistant message (`"There's an issue with the selected model … may not have access"`), so the brain never recovered. It now also detects that message signature and falls back to opus.
- **GBTW Stop hook accepts a waiting state.** The get-back-to-work hook now treats a "holding for … / waiting for … / standing by / awaiting" final sentence as a legitimate stop — suppressing only the continuation nudge while keeping the code-review, memory-search, tasks-in-progress, and bypass gates intact — so it no longer fights `/goal`-driven or legitimately-waiting workers.
- **`scripts/bump-version.sh` now updates the correct files.** It previously targeted a nonexistent `plugins/ironclaude/.claude-plugin/plugin.json` and never touched `commander/pyproject.toml`, so it errored and left the version out of sync. It now updates `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` (the three files `test_version_consistency.py` enforces), with version-format and file-existence validation.
- **Grader test drift.** Three `TestPersistentGrader` tests injected the grader response via `read_log_tail`, but the poll loop reads `capture_pane`; the mocks were repointed so the tests exercise the real poll path instead of timing out.

### Changed
- Version bumped to 1.0.15 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

## 1.0.14

### Added
- `ironclaude restart` CLI subcommand — sends SIGHUP to the daemon via PID file. Standalone `cli.py` with `pyproject.toml` console script entry point. Covered by unit tests and a real-signal integration test that spawns a subprocess with a SIGHUP handler
- `resume_session` MCP tool — resume any Claude Code conversation by session ID into a fresh managed tmux session
- `claude-fable` worker type — routing, grader prompts, and dispatch test
- `wiki_write` description frontmatter field for improved episodic memory search routing
- Ollama worker complexity gate, grader tier matrix, and batch spawn playbook injection fix
- Ollama-powered session summarization for `list_claude_sessions`
- Session adoption — `list_claude_sessions` + `adopt_session` MCP tools for taking over manually-started Claude Code sessions
- `.claude/rules/behavioral.md` — project-level behavioral directives for Claude Code rules system
- Research docs: Ollama MLX engine evaluation, Ollama worker 72h performance analysis, Obsidian Skills evaluation
- Design docs: rate-limit recovery + stuck-worker escalation, Ollama MLX engine, session sample truncation

### Fixed
- Grader feedback text corruption — replaced log-tail delta with `capture_pane`, fixed greedy feedback regex
- Brain timeout false positives during long MCP tool chains — added `_executing_tool` flag with 1800s hard safety net
- Reduced `list_claude_sessions` sample from 2000 to 200 chars to prevent 64KB+ output bloating Brain context
- Shadow grader Ollama read timeout increased 120→300s default to prevent gemma4 tool-call timeouts
- Shadow grader plan JSON fix (null → empty string for command field)

### Changed
- `brain_model` config set to `opus` (Fable currently unavailable)
- Version bumped to 1.0.14 across `pyproject.toml`, `plugin.json`, and `marketplace.json`

## 1.0.13

### Added
- gemma4 **shadow grader** — a local Ollama grader that runs alongside the primary grader and reports tool-calling concordance between the two, surfaced through a Slack command. Verdicts enforce a JSON grammar/schema (replacing the previous regex fallback chain) with argument type-safety and non-JSON robustness (code-fence stripping, stray `tools`-key removal, symmetric verdict instructions). New `shadow_grader.py` + Ollama tool-calling support in `ollama_client.py`; covered by `test_shadow_grader.py`, `test_ollama_client.py`, `test_slack_commands.py`, and orchestrator tests
- `worker/hooks/bash-readonly-guard.sh` — a sourceable predicate lib (`is_readonly_research_bash`, `_has_blocked_metachars`, `_find_has_write_action`) shared by `professional-mode-guard.sh`, with a DB-free 36-assertion unit test (`worker/hooks/tests/test-bash-readonly-guard.sh`)
- Manual-session wiki tooling — the brain-wiki operations were extracted from `OrchestratorTools` into a standalone `WikiTools` class (single source of truth for `write`/`delete`/`query`/`log`: page-name validation, derived `index.md` rebuild, changelog append, brain-repo commit), and surfaced through a new `ic-wiki` console script so they are usable from any shell, not just the daemon. No new MCP server — the daemon now delegates to `WikiTools` (−~270 lines, behaviour unchanged). `ic-wiki` resolves the brain directory the same way the daemon does (`IC_BRAIN_CWD`, then `~/.ironclaude/brain`). Covered by `test_wiki_tools.py` (7) and `test_wiki_cli.py` (1) against a git-initialised temporary brain
- `commander/tests/test_version_consistency.py` — asserts the version string is identical across `pyproject.toml`, `plugin.json`, and `marketplace.json`, so a missed source can't silently drift on a release
- macOS Prerequisites section in the README — Apple ships Bash 3.2 but the hooks need 4+ (symlink a Homebrew Bash into the default PATH), and `better-sqlite3` builds against `node@24`; documents the symptoms when either is wrong

### Fixed
- Read-only research Bash (`cat head tail wc grep rg find ls`) is now allowed in **all** non-executing workflow stages, not just brainstorming/idle. Previously `debugging` (and other stages) fell through to the catch-all write-block, and because this Claude Code build exposes no `Grep`/`Glob` tool, Bash is the only filesystem-enumeration mechanism — so an agent told to inspect logs while debugging had no way to do so. The allowlist is enforced by one hardened predicate that blocks command chaining, output redirection (`> <`), embedded newlines, and the complete GNU/BSD `find` write/exec action set (`-exec -execdir -delete -fls -fprint* -ok*`). All edits live inside the `WORKFLOW != executing` branch, so execution mode (plan-aligned Bash, per-task `allowed_files`, the `review_pending` gate) is unchanged. The same hardened check also closes pre-existing redirection bypasses on the `git add`, read-only-`git` (`git diff > out`), `make test`, and reviewing-stage allowlists
- Read-only-git exception in `professional-mode-guard.sh` now rejects shell chaining operators (`; & | \` $()`), closing a bypass where a write command could ride past the guard by appending a permitted `git diff`/`status`/`log`/etc. — mirrors the anti-chaining guard already on the `git add` exception
- `make deploy-hooks` no longer pins a plugin-cache version in the `Makefile`; it derives the latest installed version dir at runtime. A pinned version desynced from the installed cache on every release and silently skipped the plugin-cache hook copy
- Local grader strips leaked chat-template tokens (e.g. `<|tool_response>`) before `json.loads`, eliminating recurring `Non-JSON response` warnings from the Ollama-backed grader
- Slack App initialization retries on transient DNS failures during daemon startup, so a flaky resolver no longer aborts the boot sequence
- Restored a green commander test suite via two rounds of test-only fixes — no production-code changes: (1) 35 failures in the orchestrator cluster (`IC_BRAIN_CWD` environment leakage → autouse isolation fixture, stale Ollama exception mocks, the `kill_worker` dict-return / inline-grader cluster); (2) 8 further stale tests in the grader/enforcement/db modules that asserted superseded contracts (config moved into `LocalGrader`, the directive-ref pre-filter's `no_directive_ref` sentinel + silent-drop, and schema growth to 8 tables)

### Changed
- Version set to 1.0.13 across `pyproject.toml`, `marketplace.json`, and `plugin.json`. The `Makefile` is no longer a version source — it derives the installed plugin-cache version at runtime

## 1.0.12

### Added
- Ollama worker recommended settings + scaffolding — `spawn_worker(worker_type="ollama")` now auto-ensures a `num_ctx`-fixed model variant (`ic-<base>-<num_ctx>`, default 32768) via `/api/create` and launches against it, because Ollama's 4096 default truncated ~84% of Claude Code's first turn and left local-model workers non-functional. A principle-based worker playbook is injected via `--append-system-prompt` so small models (e.g. `gemma4:12b-it-qat`) follow the workflow rail instead of re-deriving it on every tool call. Optional `CLAUDE_CODE_MAX_OUTPUT_TOKENS` cap via `ollama_worker_max_output_tokens`. Validated end-to-end against a live Ollama (`OllamaClient.create_model`, `_ensure_ollama_ctx_variant`, `ollama_playbook.py`)
- `ollama_worker_num_ctx` config knob (default 32768) controls the worker variant's context window — 32k (~7.5 GB) fits under the 8 GB VRAM ceiling out of the box; larger context (e.g. 128k) requires raising `ollama_vram_block_threshold_gb` too. Surfaced in `config/ironclaude.json.example` and the README ("Running Ollama workers on Apple Silicon")

### Fixed
- `get-back-to-work` hook now detects `Monitor`/`TaskOutput`/`ScheduleWakeup` as waiting tools (not just `Bash` `run_in_background`), preventing false-positive interrupts when workers wait on long-running background tasks (d1171)
- Ollama VRAM spawn gate respects a config-overridable **8.0 GB ceiling on already-loaded Ollama VRAM** (`ollama_vram_block_threshold_gb`); raise it on larger-memory hosts. The 8 GB default suits Apple Silicon unified memory. (Corrects an earlier description of this gate as "host-aware / scales to half of total system memory", which was inaccurate — the daemon always populates the threshold from config defaults.) README updated: the threshold is a ceiling on loaded VRAM, not a minimum required

### Changed
- Version bumped to 1.0.12 across `pyproject.toml`, `Makefile` hook-cache path, `marketplace.json`, and `plugin.json`

## 1.0.11

### Added
- Heartbeat-level stuck detection — fires an `[ACTION REQUIRED]` escalation when a worker's `(stage, log_bytes)` fingerprint is unchanged across two consecutive heartbeats (~30 min), regardless of workflow stage. Closes a gap where `AskUserQuestion` menus raised during brainstorming were invisible to the prior PM-gate-only detector. Additive to the d1132 `check_stuck_workers` path (d1162)

### Fixed
- `review_pending` Flavor B deadlock — three root causes resolved: the `subagent-drift-detector` hook no longer writes `review_pending` to the DB (`submit_task` is now the sole authority for that flag), `plan-task-context` gained a dual-check auto-clear for submitted tasks in the current wave, and the state-manager `dist` was rebuilt to include `set_testing_theatre_checked` (d1157)

### Changed
- Version bumped to 1.0.11 across `pyproject.toml`, `Makefile` hook-cache path, `marketplace.json`, and `plugin.json`

## 1.0.10

### Added
- LLM-based semantic grading replacing regex/keyword judgment — `LocalGrader` extraction with 3 call sites migrated (d1078)
- Stuck-worker detection with two-step Slack escalation — stuck-alert and 30-minute thresholds, hash-dedup bypass for prompt-waiting workers, liveness deferral cap (d1074/d1076/d1081, d1132)
- Brain proactiveness enforcement (d1074/d1076/d1081)
- `clear_stale_review_pending` MCP tool plus automatic clearing of stale `review_pending` deadlocks — hook dual-checks for submitted tasks in the current wave before blocking edits (d1141)
- Directive-ref pre-filter for Brain Slack message validation — messages without `#N`/`dN` references are filtered before the LLM grader, restoring the `CONTEXT_REQUIRED` feedback loop for conversational Brain responses (d1133)
- Auto-resolve brain model to opus when the configured model is unavailable (d1106)
- Windows setup guide (`WINDOWS_SETUP.md`) and startup-lookback-enforcer hook
- Research Directive Completion section 6b in workflow rules (d1086)

### Fixed
- Strip professional-mode preamble from heartbeat worker summaries — heartbeat now shows actual task descriptions instead of repeated "Professional mode is active…" text (d1142)
- Mid-execution state corruption guards — `claim_task` guard, `state-activator` protection, and `mark_executing` consistency enforcement (d1083)
- Clear `review_pending` on wave transition in `get_next_tasks` — prevents stale flag after compaction (d1097)
- Ollama worker professional-mode integration — expanded git allowlist in `professional-mode-guard`, `ENABLE_STOP_REVIEW` check in the stop hook, and `deploy-hooks` copying all hooks (d1095)
- Inject `ANTHROPIC_BASE_URL` into Ollama worker spawn commands and fix the attribution header — fixes Claude Code unable to reach the Ollama endpoint (d1074, d1084)

### Changed
- README rewritten for post-v1.0.5 accuracy — state machine stages, hook system table, worker types, stuck detection, Ollama config, and configuration reference; adversarial-review accuracy fixes (d1084, d1099)
- Reverted `brain_model` to opus while Fable is unavailable (d1100, follow-up revert)
- Version bumped to 1.0.10 across `pyproject.toml`, `Makefile` hook-cache path, `marketplace.json`, and `plugin.json`

## 1.0.9

### Added
- Ollama model discovery with classification — `discover_models` MCP tool inventories local models by capability tier
- Paginated `get_directives` MCP tool with date filtering and text search
- PM timeout/retry parameters wired through `spawn_worker` pipeline (`pm_timeout`, `pm_max_retries`)
- Brain behavioral directive #19: never auto-switch workers to usage credits on rate limit
- Pin/decision-format enforcement for blocked-task escalations to operator
- Auto-unpin Brain escalation messages when tasks unblock
- Wiki page name validation — reject directive-number and date-stamped slugs
- Wiki server `/wiki` → `/wiki/` redirect for correct relative link resolution
- Security-guidance plugin integration for workers (Stage 1+3 active, Stage 2 disabled)
- `conftest.py` with `os.kill` guard for safe test isolation

### Fixed
- SessionStart hook race condition on Windows — pre-flight `COUNT(*)` check in `episodic-memory-sync.sh` exits cleanly when session row doesn't exist yet
- Heartbeat shows all alive workers using tmux as ground truth instead of DB status
- Immediate Brain notification on directive confirmation, removed 5-minute delay from reminders
- SQLite lock rollback in push sweep with confirmed-directive reminder
- Model config: switch opus defaults to short alias, remove `[1m]` suffix (Max plan auto-enables 1M context)

### Changed
- Default model updated to `claude-opus-4` (short alias) across worker commands
- Brain model uses `[1m]` suffix for explicit 1M context window opt-in

## 1.0.8

### Added
- Wiki knowledge layer implementing [Karpathy's LLM wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — Brain-maintained markdown pages synthesized from episodic memory
- Wiki MCP tools: `wiki_write`, `wiki_delete`, `wiki_query`, `wiki_log`
- Dual-flag gate: gated actions now require both episodic memory search AND wiki query
- Brain rules for wiki workflows: post-directive ingest, periodic sweeps, search-triggered synthesis
- Wiki auto-commit: `wiki_write` and `wiki_delete` stage and commit after each mutation
- Wiki synthesis enforcer hook for Brain wiki compliance
- Task ledger persistence to `wiki/tasks.md` — survives daemon restarts
- `IC_ROLE` environment variable: workers get `IC_ROLE=worker` at spawn and bypass brain-orchestrator-guard restrictions
- Audit log entries for daemon-side professional mode deactivation writes

### Fixed
- Directive staleness prevention — mandatory status updates, text confirmation detection, sweep cross-referencing
- Pass missing `effort` argument to `make_opus_command()` calls — prevents TypeError when spawning opus workers
- Professional mode guard: allow `.claude/rules/` writes and `mkdir` during undecided state — unblocks first-time activation bootstrap
- Detect `AskUserQuestion` menus in `send_to_worker` — navigate to free-text option instead of accidentally selecting default menu item
- Background job detection in get-back-to-work hook to reduce false positives
- Notification heartbeat messages now show actual task description instead of repeated preamble text

### Changed
- Default model switched to `opus` (short alias) for 1M context window
- Restored illustrative override examples in README model config section
- Removed internal workflow artifacts (docs/) from repository tracking
