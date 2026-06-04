# Security-Guidance Plugin Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Integrate Anthropic's security-guidance plugin for all IronClaude workers with Stage 2 (per-turn model review) disabled via `ENABLE_STOP_REVIEW=0` env var.

**Architecture:** Add `ENABLE_STOP_REVIEW=0` at both worker spawn sites (orchestrator_mcp.py and main.py) so all worker types get Stage 1 (free pattern checks) and Stage 3 (commit-time agentic review) without Stage 2 redundancy. Add non-blocking plugin presence check in session-init.sh. Update README with install instructions.

**Tech Stack:** Python, Bash, pytest

**Design correction:** The design doc specified modifying `config.py:make_opus_command()`, but that only covers opus workers. The correct location is the two spawn-site chokepoints where `IC_ROLE`/`IC_WORKER_ID` are prepended — this covers all worker types (opus, sonnet, ollama).

---

## Task 1: Add ENABLE_STOP_REVIEW=0 to worker spawn env vars

**Files:**
- Modify: `commander/tests/test_orchestrator_mcp.py:82` (spawn command assertion)
- Modify: `commander/tests/test_orchestrator_mcp.py:630` (TestSpawnWorkerEnvVar claude)
- Modify: `commander/tests/test_orchestrator_mcp.py:644` (TestSpawnWorkerEnvVar ollama)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:1596`
- Modify: `commander/src/ironclaude/main.py:1115`

**Step 1: Update test assertions to expect ENABLE_STOP_REVIEW=0 (RED)**

In `commander/tests/test_orchestrator_mcp.py`, update three test assertions:

Line 82 — change the spawn_session assert from:
```python
f"export IC_ROLE=worker; export IC_WORKER_ID=w1; {WORKER_COMMANDS['claude-sonnet']}",
```
to:
```python
f"export IC_ROLE=worker; export IC_WORKER_ID=w1; export ENABLE_STOP_REVIEW=0; {WORKER_COMMANDS['claude-sonnet']}",
```

Line 630 — change from:
```python
assert cmd.startswith("export IC_ROLE=worker; export IC_WORKER_ID=test-1; ")
```
to:
```python
assert cmd.startswith("export IC_ROLE=worker; export IC_WORKER_ID=test-1; export ENABLE_STOP_REVIEW=0; ")
```

Line 644 — change from:
```python
assert cmd.startswith("export IC_ROLE=worker; export IC_WORKER_ID=ollama-1; ")
```
to:
```python
assert cmd.startswith("export IC_ROLE=worker; export IC_WORKER_ID=ollama-1; export ENABLE_STOP_REVIEW=0; ")
```

**Step 2: Run tests to verify they fail (RED)**

```bash
cd commander && python -m pytest tests/test_orchestrator_mcp.py::TestSpawnWorkerEnvVar -v && python -m pytest tests/test_orchestrator_mcp.py -k "test_spawn_worker_registers" -v
```

Expected: FAIL — command strings don't contain `ENABLE_STOP_REVIEW=0` yet.

**Step 3: Add ENABLE_STOP_REVIEW=0 to orchestrator_mcp.py spawn site (GREEN)**

In `commander/src/ironclaude/orchestrator_mcp.py`, line 1596 — change from:
```python
cmd = f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; {cmd}"
```
to:
```python
cmd = f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; export ENABLE_STOP_REVIEW=0; {cmd}"
```

**Step 4: Add ENABLE_STOP_REVIEW=0 to main.py spawn site**

In `commander/src/ironclaude/main.py`, line 1115 — change from:
```python
cmd = f"IC_WORKER_ID={shlex.quote(worker_id)} {cmd}"
```
to:
```python
cmd = f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; export ENABLE_STOP_REVIEW=0; {cmd}"
```

Note: main.py spawn site currently lacks `export IC_ROLE=worker` — this aligns it with the orchestrator_mcp.py pattern while adding the security plugin env var.

**Step 5: Run tests to verify they pass (GREEN)**

```bash
cd commander && python -m pytest tests/test_orchestrator_mcp.py::TestSpawnWorkerEnvVar -v && python -m pytest tests/test_orchestrator_mcp.py -k "test_spawn_worker_registers" -v
```

Expected: PASS

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/main.py commander/tests/test_orchestrator_mcp.py
```

Expected: Changes staged.

---

## Task 2: Add plugin presence check to session-init.sh

**Files:**
- Modify: `worker/hooks/session-init.sh:154` (after stable hook directory block)

No tests required: shell hook with no test framework. Verification is manual — check log output with plugin present and absent.

**Step 1: Add security-guidance plugin check block**

In `worker/hooks/session-init.sh`, after the stable hook directory block (after `log_hook "session-init" "Stable" "hooks copied to $STABLE_DIR"` / line ~154), before the `# ═══ Absolute statusline path ═══` block, add:

```bash
# ═══ Security-guidance plugin check ═══
SG_FOUND=0
for d in "$HOME/.claude/plugins/cache"/*/security-guidance; do
  [ -d "$d" ] && SG_FOUND=1 && break
done
if [ "$SG_FOUND" = "0" ]; then
  log_hook "session-init" "Security" "WARN: security-guidance plugin not installed. Run: /plugin install security-guidance@claude-plugins-official"
fi
```

**Step 2: Stage changes**

```bash
git add worker/hooks/session-init.sh
```

Expected: Changes staged.

---

## Task 3: Update README with security-guidance install instructions

**Files:**
- Modify: `README.md:91-93` (Quick Start install section)

No tests required: documentation only.

**Step 1: Add security-guidance plugin install step**

In `README.md`, after the existing plugin install block (lines 89-93):

```bash
# From within Claude Code:
/plugin marketplace add robertphyatt/ironclaude
/reload-plugins
```

Add immediately after `/reload-plugins`:

```markdown

# Security vulnerability detection (recommended):
/plugin install security-guidance@claude-plugins-official
/reload-plugins
```

**Step 2: Stage changes**

```bash
git add README.md
```

Expected: Changes staged.
