# Changelog

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
- Default model switched to `claude-opus-4-6` for 1M context window
- Restored illustrative override examples in README model config section
- Removed internal workflow artifacts (docs/) from repository tracking
