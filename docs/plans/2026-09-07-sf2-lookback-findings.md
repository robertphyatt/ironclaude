# SF2 — 48h-Lookback Enforcer: Gap Findings

> **Created:** 2026-09-07 (Commander responsiveness loop, Task 8)
> **Status:** Gap confirmed; closed by registering the shell hook via a version-controlled Brain-settings sync.

## The gap

The 48h-startup-lookback gate (d1040) was enforced **nowhere** for the daemon-run Brain:

1. **`startup-lookback-enforcer.sh` is registered in no `settings.json`.** `grep -rn startup-lookback-enforcer ~/.ironclaude/brain` returns only wiki documentation (`wiki/startup-lookback-enforcer.md`, `wiki/index.md`, `wiki/log.md`) — never `~/.ironclaude/brain/.claude/settings.json` or `settings.local.json`. The shell hook existed in `commander/hooks/` but was never wired into the Brain's live settings.

2. **The in-process `_tool_guard` callback is dead.** `brain_client.py:748` runs the Brain with `permission_mode="bypassPermissions"`, which bypasses the SDK's `can_use_tool` permission callback (wired at `:751`). Empirical proof: `_tool_guard` logs a `TOOL_INVOKE` line on every invocation (`brain_client.py:709-711`), yet **`TOOL_INVOKE` count is 0 across all 10 `~/.ironclaude/brain-sessions/*.log`** while sessions are actively orchestrating (the current session showed 44 `MSG_SEND` / 34 `MSG_RECV`, 0 `TOOL_INVOKE`). So the lookback gate inside `_tool_guard_logic` (`:347-365`) is dead code for the daemon Brain.

The **live** enforcement layer for the daemon Brain is the SHELL HOOKS loaded via `setting_sources=["project","local"]` (`brain_client.py:756`) — `settings.json` (`memory-search-enforcer`, `block-push`, `wiki-synthesis-enforcer`, `attention-sweep-enforcer`) and `settings.local.json` (`brain-orchestrator-guard`). Registering `startup-lookback-enforcer.sh` there is what closes the gap.

## The fix (this task)

A version-controlled, template-driven daemon-start sync (`_sync_brain_settings_hooks` in `main.py`, mirroring the rules sync): reads `commander/src/brain/brain_settings_hooks.json`, deploys every referenced `.sh` from `commander/hooks/` → `~/.claude/ironclaude-hooks/`, and **appends** each PreToolUse entry into `~/.ironclaude/brain/.claude/settings.json` if absent — idempotent, never a whole-object rewrite, preserving all existing PreToolUse entries and the entire PostToolUse block (`attention-sweep-arm`, `block-pin-enforcer`).

## Re-arm cost (by design, per d1040 — state it plainly, do not gloss as "re-arms")

The lookback flags are **not** durable across a Brain restart/compaction:
- `start()` deletes every `/tmp/ic/lookback-*` flag (`brain_client.py:279-285`).
- `fork_session=True` changes the hook's `session_id`, so the flag filenames (`/tmp/ic/lookback-{slack,ledger}-$SESSION_TAG`) change too.

Consequence: after **every** restart or compaction, the **first** gated action (`spawn_worker`/`approve_plan`/…/`AskUserQuestion`) is blocked until BOTH lookback calls (`get_operator_messages(hours_back>=48)` + `update_ledger`) re-run and re-arm the flags. This is intentional (the lookback must be re-done per fresh context), but it is a real per-session cost, not a free "re-arm".

## Follow-ups (out of scope here)

- **Other dead `_tool_guard` gates.** `can_use_tool` being dead means the wiki-query gate and ledger-staleness gate in `_tool_guard_logic` are also unenforced for the daemon Brain (memory-search and push are covered by their own shell hooks). A guardrail audit + shell-hook coverage for those is a separate loop.
- **Full settings.json source-of-truth.** `brain_settings_hooks.json` currently declares only the NEW entries (lookback + Task-9 Agent gate). The pre-existing hooks (memory-search, block-push, wiki-synthesis, attention-sweep, and the PostToolUse pair) are NOT yet version-controlled — a full, reconstructable settings template is a follow-up.
- **`commander/hooks/*` general deploy path + memory-search-enforcer refresh** (existing backlog).
