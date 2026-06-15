# Changelog

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
