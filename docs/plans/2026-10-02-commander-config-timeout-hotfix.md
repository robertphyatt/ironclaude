# Commander Allocate-Timeout Hotfix Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the workspace CLI timeout configurable (default 900s), so a cold allocate on roleplaying-agents is never killed partway through. Then relaunch Commander and confirm the first worker spawn completes.

**Requirements:** `docs/plans/2026-10-02-commander-config-timeout-hotfix-requirements.md` (H1–H4)

**Design:** `docs/plans/2026-10-02-commander-config-timeout-hotfix-design.md`

**Architecture:** Add a new DEFAULTS key in `config.py`, and pass it as `timeout=` where the orchestrator builds `WorkspaceClient` (`orchestrator_mcp.py:505`). Then restart Commander and observe.

**Tech Stack:** Python 3.11, pytest.

## Execution invariants

- **Absolute paths.** Shell state does not persist between steps.
- **pytest command:** `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`
- **Staging:** `git -C /Users/roberthyatt/Code/ironclaude add …`. `docs/` is gitignored, so plan files need `-f`. Do not commit or push.
- **Process handling:** kill processes by exact PID only. Never hand-edit the workspace DB or worktrees.

---

## Task 1: Configurable workspace CLI timeout (H1–H3)

**Files:**
- Modify: `commander/src/ironclaude/config.py` (DEFAULTS, after the `"max_swap_used_gb": 8.0,` line)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:505-508`
- Test: `commander/tests/test_config.py`, `commander/tests/test_orchestrator_mcp.py`

**Step 1: Write the tests (RED).**

(a) Append to `commander/tests/test_config.py`:

```python
def test_workspace_cli_timeout_default_is_900():
    from ironclaude.config import DEFAULTS
    assert DEFAULTS["workspace_cli_timeout_seconds"] == 900
```

(b) Append to `commander/tests/test_orchestrator_mcp.py`:

```python
def test_workspace_client_timeout_comes_from_config(registry, mock_tmux, monkeypatch):
    ctor = MagicMock()
    monkeypatch.setattr("ironclaude.orchestrator_mcp.WorkspaceClient", ctor)
    OrchestratorTools(
        registry, mock_tmux, ssh_manager=None,
        config={"workspace_cli_timeout_seconds": 777},
    )
    ctor.assert_called_once()
    assert ctor.call_args.kwargs["timeout"] == 777


def test_workspace_client_timeout_falls_back_to_900(registry, mock_tmux, monkeypatch):
    ctor = MagicMock()
    monkeypatch.setattr("ironclaude.orchestrator_mcp.WorkspaceClient", ctor)
    OrchestratorTools(registry, mock_tmux, ssh_manager=None, config={})
    ctor.assert_called_once()
    assert ctor.call_args.kwargs["timeout"] == 900
```

These tests install their own constructor mock. That overrides the module's autouse `default_workspace_client` fixture, which also patches `WorkspaceClient`. The test's patch runs last, on the same function-scoped `monkeypatch`.

What each test catches if the change is broken:
- **The 777 test** fails if the configured value is not forwarded as `timeout=`.
- **The 900 test** fails if the fallback is absent or wrong.
- **The DEFAULTS test** fails if the key is absent.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_config.py::test_workspace_cli_timeout_default_is_900 tests/test_orchestrator_mcp.py::test_workspace_client_timeout_comes_from_config tests/test_orchestrator_mcp.py::test_workspace_client_timeout_falls_back_to_900
```

Expected: 3 failed.
- The DEFAULTS test raises `KeyError: 'workspace_cli_timeout_seconds'`.
- Both timeout tests raise `KeyError: 'timeout'`. Today `orchestrator_mcp.py:505-508` passes only `ssh_manager`.

**Step 3: Implement (GREEN).**

(a) In `config.py` DEFAULTS, add a new line directly after `    "max_swap_used_gb": 8.0,`:

```python
    "workspace_cli_timeout_seconds": 900,
```

(b) In `orchestrator_mcp.py`, replace:

```python
        self._workspace_client = WorkspaceClient(
            workspace_plugin_root,
            ssh_manager=ssh_manager,
        )
```

with:

```python
        self._workspace_client = WorkspaceClient(
            workspace_plugin_root,
            ssh_manager=ssh_manager,
            timeout=int(self._config.get("workspace_cli_timeout_seconds", 900)),
        )
```

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_config.py tests/test_orchestrator_mcp.py tests/test_workspace_client.py
```

Expected: all pass, 0 failed.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the passed count.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/config.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_config.py commander/tests/test_orchestrator_mcp.py
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-02-commander-config-timeout-hotfix-requirements.md docs/plans/2026-10-02-commander-config-timeout-hotfix-design.md docs/plans/2026-10-02-commander-config-timeout-hotfix.md docs/plans/2026-10-02-commander-config-timeout-hotfix.plan.json
```

Expected: exit 0 for both.

---

## Task 2: Relaunch Commander and confirm the first spawn (H4)

**Depends on:** Task 1.

No tests required: this is an operational relaunch, and the evidence is recorded in the log.

**Files:**
- Create: `docs/plans/2026-10-02-commander-config-timeout-hotfix-log.md`

**Step 1: Relaunch Commander, detached, with the Claude Code environment variables unset.**

```bash
env -u CLAUDE_CODE_BRIDGE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_CODE_SESSION_ID -u CLAUDE_EFFORT -u CLAUDE_PID -u CLAUDECODE nohup python3 -c "import os; os.setsid(); os.execvp('make',['make','-C','/Users/roberthyatt/Code/ironclaude/commander','run'])" >/tmp/ironclaude-restart-launch.log 2>&1 & disown
```

Then check that it came up:
- `cat /tmp/ic-daemon.pid` and `cat /tmp/ic/brain.pid`;
- `ps -o pid=,ppid=,command= -p <daemon>,<brain>` shows both processes;
- `/tmp/ironclaude-restart-launch.log` contains no `Traceback` and no `ERROR`.

Expected: the daemon and the Brain are up.

**Step 2: Watch the first spawn.**

Directive d1501 is still active, so the Brain will try to spawn its reviewer on its own.
1. Wait for the spawn with a background `ScheduleWakeup` or `Monitor`, not by polling in a loop.
2. Read `/tmp/ic/daemon.log` and the newest `~/.ironclaude/brain-sessions/*.log` for spawn or allocate lines.
3. Query `sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT workspace_guid,worker_id,lifecycle_status,created_at,updated_at FROM assignments WHERE lifecycle_status NOT IN ('integrated','abandoned','cleaned')"`.

Expected: one of three outcomes.
- **PASS:** a new roleplaying-agents assignment reaches `active`.
  - Record the allocate duration from the daemon and Brain log timestamps around the spawn call and its result.
  - If those are unavailable, fall back to the DB's `created_at` and `updated_at`. Label that fallback as a proxy: it covers reserve through active, including bind and reconcile, so it is not allocate alone.
- **FAIL, stop and report:** any `TimeoutExpired` or "timed out after N seconds" in `/tmp/ic/daemon.log` or in the spawn failure reason, for any N. Record N:
  - 60 means the change did not take effect;
  - 900 means allocate exceeds the new budget.
- **H4 NOT MET, report the reason:** any other spawn failure, such as `WorkspaceClientError`. Do not claim a pass.

In every case, no row may be left in `reserved` after the spawn attempt finishes. If one is, stop and report. Also record that remote (SSH) allocates do not use this timeout (`workspace_client.py:217`).

**Step 3: Write the log and stage it.**

Write `docs/plans/2026-10-02-commander-config-timeout-hotfix-log.md` with the evidence from Steps 1 and 2, then stage it:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-02-commander-config-timeout-hotfix-log.md
```

Expected: exit 0.
