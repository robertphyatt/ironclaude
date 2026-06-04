# Episodic Memory Sync — Remove PM Gate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Remove professional mode gate from `episodic-memory-sync.sh` so conversation indexing runs for all sessions, then deploy to both active hook locations.

**Architecture:** Two-task plan. Task 1 edits the source hook; Task 2 adds a Makefile deploy target and runs it to push to both deployed locations.

**Tech Stack:** Bash, Makefile

---

## Task 1: Remove PM gate from hook

**Files:**
- Modify: `worker/hooks/episodic-memory-sync.sh:18-35`

No tests required: shell hook with no test framework; manual verification via hook log output.

**Step 1: Delete lines 18-35 from `worker/hooks/episodic-memory-sync.sh`**

Use Edit tool with:
- `old_string`: the entire PM gate block (18 lines)
- `new_string`: empty string (pure deletion)

Exact `old_string` to delete:
```
# Professional mode gate — read from SQLite (not flag file)
INPUT=$(cat)
init_session_id

# Pre-flight: async hook may fire before session-init.sh creates the session row.
_safe_session=$(echo "$SESSION_TAG" | sed "s/'/''/g")
if [ ! -f "$DB_PATH" ] || \
   [ "$(sqlite3 "$DB_PATH" ".timeout 5000" \
     "SELECT COUNT(*) FROM sessions WHERE terminal_session='${_safe_session}';" 2>/dev/null)" = "0" ]; then
    log_hook "EPISODIC-MEMORY-SYNC" "Disabled" "session not yet initialized"
    exit 0
fi
PROF_MODE=$(db_read_or_fail "EPISODIC-MEMORY-SYNC" \
  "SELECT professional_mode FROM sessions WHERE terminal_session='$(echo "$SESSION_TAG" | sed "s/'/''/g")';")
if [ "$PROF_MODE" != "on" ]; then
    log_hook "EPISODIC-MEMORY-SYNC" "Disabled" "professional mode ${PROF_MODE}"
    exit 0
fi
```

After edit, line 16 (`run_hook "EPISODIC-MEMORY-SYNC"`) should flow directly to `# Check if already running`.

**Step 2: Stage**

```bash
git add worker/hooks/episodic-memory-sync.sh
```

Expected: file staged with 18-line deletion.

---

## Task 2: Add deploy-hooks Makefile target and deploy

**Files:**
- Modify: `Makefile`

No tests required: Makefile target; verified by `make` exit code and `diff` of deployed files.

**Step 1: Add variables and `deploy-hooks` target to `Makefile`**

Append after the existing `.PHONY` line at the top of the file. Use Edit tool to change:

`old_string`:
```
.PHONY: tailscale-serve-setup
```

`new_string`:
```
.PHONY: tailscale-serve-setup deploy-hooks

PLUGIN_CACHE_HOOK_DIR := $(HOME)/.claude/plugins/cache/ironclaude/ironclaude/1.0.8/hooks
STABLE_HOOK_DIR := $(HOME)/.claude/ironclaude-hooks

# Deploys updated hooks to both active runtime locations (plugin cache + stable dir).
# Run after editing any file in worker/hooks/.
deploy-hooks:
	cp worker/hooks/episodic-memory-sync.sh $(PLUGIN_CACHE_HOOK_DIR)/episodic-memory-sync.sh
	cp worker/hooks/episodic-memory-sync.sh $(STABLE_HOOK_DIR)/episodic-memory-sync.sh
	@echo "Deployed episodic-memory-sync.sh to plugin cache and stable hooks dir"

```

**Step 2: Run `make deploy-hooks`**

```bash
make deploy-hooks
```

Expected output:
```
cp worker/hooks/episodic-memory-sync.sh ~/.claude/plugins/cache/ironclaude/ironclaude/1.0.8/hooks/episodic-memory-sync.sh
cp worker/hooks/episodic-memory-sync.sh ~/.claude/ironclaude-hooks/episodic-memory-sync.sh
Deployed episodic-memory-sync.sh to plugin cache and stable hooks dir
```

**Step 3: Verify deployment**

```bash
diff worker/hooks/episodic-memory-sync.sh ~/.claude/plugins/cache/ironclaude/ironclaude/1.0.8/hooks/episodic-memory-sync.sh && \
diff worker/hooks/episodic-memory-sync.sh ~/.claude/ironclaude-hooks/episodic-memory-sync.sh && \
echo "Both locations match source"
```

Expected: `Both locations match source` (no diff output).

**Step 4: Stage**

```bash
git add Makefile docs/plans/2026-06-03-episodic-sync-ungated-design.md docs/plans/2026-06-03-episodic-sync-ungated.md docs/plans/2026-06-03-episodic-sync-ungated.plan.json
```

Expected: all four files staged.
