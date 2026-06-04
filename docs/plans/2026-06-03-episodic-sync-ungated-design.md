# Episodic Memory Sync — Remove PM Gate Design

> **Created:** 2026-06-03
> **Status:** Design Complete

## Summary

`worker/hooks/episodic-memory-sync.sh` currently exits early for any session where professional mode is not "on". This silently drops conversation indexing for all non-PM sessions — a data loss bug. The fix removes the PM gate (and the session pre-flight that blocks non-PM sessions for the same reason), so the sync runs unconditionally for all sessions.

## Architecture

Single-file edit plus a new Makefile deploy target. No structural changes to the sync mechanism itself — the singleton (PID file + lock dir) stays intact.

## Components

**`worker/hooks/episodic-memory-sync.sh`** — remove lines 18-35:
- Line 18: comment (`# Professional mode gate`)
- Lines 19-20: `INPUT=$(cat)` and `init_session_id` — dead code once guards are removed
- Lines 22-29: session pre-flight block (exits if no session row in DB)
- Lines 30-35: PROF_MODE check block (exits if professional_mode != "on")

After edit, `run_hook "EPISODIC-MEMORY-SYNC"` (line 16) flows directly to `# Check if already running` (line 37).

**`Makefile`** — add `deploy-hooks` target:
```makefile
PLUGIN_CACHE_HOOK_DIR := $(HOME)/.claude/plugins/cache/ironclaude/ironclaude/1.0.8/hooks
STABLE_HOOK_DIR := $(HOME)/.claude/ironclaude-hooks

.PHONY: deploy-hooks
deploy-hooks:
	cp worker/hooks/episodic-memory-sync.sh $(PLUGIN_CACHE_HOOK_DIR)/episodic-memory-sync.sh
	cp worker/hooks/episodic-memory-sync.sh $(STABLE_HOOK_DIR)/episodic-memory-sync.sh
```

## Data Flow

Before: SessionStart → hook fires → session pre-flight → PROF_MODE check → (exits for non-PM) → singleton check → sync  
After: SessionStart → hook fires → singleton check → sync

## Error Handling

The `CLAUDE_PLUGIN_ROOT` guard (line 65) remains — this is a legitimate env check unrelated to PM state. Singleton lock behavior unchanged.

## Testing Strategy

Manual verification: trigger a SessionStart in a non-PM session and confirm sync runs (check `/tmp/ic/daemon.log` or hook log output for "EPISODIC-MEMORY-SYNC: Started").

## Implementation Notes

- Two deployment locations must be updated: plugin cache (1.0.8) and stable hooks dir
- Both currently have identical content to source — deploy is a straight `cp`
- This change goes into next version push; staged-only, not committed standalone
