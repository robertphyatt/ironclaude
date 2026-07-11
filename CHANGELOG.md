# Changelog

> **Versioning.** IronClaude uses a single monotonically-increasing `1.0.N`
> patch series — by deliberate convention both features and fixes increment the
> patch number (this is not strict semver). The version is declared in
> `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, and
> `.claude-plugin/marketplace.json`, kept in lockstep by
> `commander/tests/test_version_consistency.py`. Each release commit is tagged
> `vX.Y.Z`. Land changes under `## [Unreleased]` as you go, then rename that
> heading to the new version at release time so the entry matches what shipped.

## [Unreleased]

_Nothing yet._

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
