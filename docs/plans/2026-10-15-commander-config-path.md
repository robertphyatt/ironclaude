# Commander Config Path Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make Commander config loading independent of the process cwd, so the orchestrator MCP reads `commander/config/ironclaude.json` (swap 50 GB, critical-only). Set worker and grader routing to prefer Claude.

**Requirements:** `docs/plans/2026-10-15-commander-config-path-design.md` (§Requirements R1–R4)

**Architecture:**
- `config.py` gets a module-level `_COMMANDER_ROOT` (the `commander/` dir) and two resolvers that read it at call time: `default_config_path()` and `default_machines_config_path()`.
- `load_config` and `load_machines_config` take `config_path: str | None = None` and fall back to the resolvers.
- `orchestrator_mcp.py` uses `default_machines_config_path()` when `IC_MACHINES_CONFIG` is unset.
- The shipped config sets worker and grader to `preferred: claude`, with clients `["claude", "codex"]`.

**Tech Stack:** Python 3.11, pytest (commander venv).

Loop: full. This touches spawn-gate config source and worker/grader routing.

## Execution invariants

- Literal absolute paths. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- Run pytest with `PYTHONUNBUFFERED=1` and the commander venv, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Verified at planning time:
  - `config.py` has no `pathlib` import (lines 4-18).
  - `load_config` is at `:143` and `load_machines_config` at `:234`.
  - `orchestrator_mcp.py:38` imports from `ironclaude.config`, and `:496` sets `self._machines_config_path`.
  - `test_config.py` imports `json`, `os`, `Path` and `pytest` (lines 3-8).
  - The `machines_yaml` fixture format is at `:288-309`.
  - The shipped `commander/config/ironclaude.json` currently has worker and grader set to `"preferred": "codex", "clients": ["codex", "claude"]`.
- Never stash, commit or push.

---

## Task 1: Anchor config paths and prefer Claude routing

TDD.

**Files:**
- Modify: `commander/src/ironclaude/config.py` (imports; `:143`; `:234`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (`:38`, `:496`)
- Modify: `commander/config/ironclaude.json` (worker and grader roles)
- Test: `commander/tests/test_config.py` (append a class)

### Step 1: Tests (RED)

Append to `commander/tests/test_config.py`:
```python


class TestConfigPathAnchoring:
    """Defaults resolve from the package location, not the process cwd: the
    orchestrator MCP runs with cwd ~/.ironclaude/brain and must still read
    commander/config/ironclaude.json."""

    def test_load_config_default_is_cwd_independent(self, tmp_path, monkeypatch):
        import ironclaude.config as config_mod
        root = tmp_path / "commander"
        (root / "config").mkdir(parents=True)
        (root / "config" / "ironclaude.json").write_text(
            json.dumps({"max_swap_used_gb": 42.5, "spawn_block_pressure_level": 3}))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setattr(config_mod, "_COMMANDER_ROOT", root)
        monkeypatch.chdir(elsewhere)
        cfg = config_mod.load_config()
        assert cfg["max_swap_used_gb"] == 42.5
        assert cfg["spawn_block_pressure_level"] == 3

    def test_load_machines_config_default_is_cwd_independent(self, tmp_path, monkeypatch):
        import ironclaude.config as config_mod
        root = tmp_path / "commander"
        (root / "config").mkdir(parents=True)
        (root / "config" / "machines.yaml").write_text(
            "machines:\n  - name: anchored\n    host: anchored\n    claude_path: /usr/bin/claude\n    repos:\n      - /opt/anchored\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setattr(config_mod, "_COMMANDER_ROOT", root)
        monkeypatch.chdir(elsewhere)
        machines = config_mod.load_machines_config()
        assert [m["name"] for m in machines] == ["anchored"]
        assert config_mod.default_machines_config_path() == str(root / "config" / "machines.yaml")

    def test_shipped_config_prefers_claude_for_workers_and_graders(self):
        shipped = Path(__file__).resolve().parents[1] / "config" / "ironclaude.json"
        cfg = load_config(str(shipped))
        roles = cfg["providers"]["roles"]
        for role in ("worker", "grader"):
            assert roles[role]["preferred"] == "claude"
            assert roles[role]["clients"] == ["claude", "codex"]
        assert cfg["max_swap_used_gb"] == 50
        assert cfg["spawn_block_pressure_level"] == 4
```
RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_config.py -q -k TestConfigPathAnchoring
```
Expected: 3 failed. The first two fail because `monkeypatch.setattr` raises `AttributeError` (no `_COMMANDER_ROOT`). The third fails on `preferred == "claude"`, since the file says `"codex"`. Record the measured output.

### Step 2: Implementation (GREEN)

1. In `config.py`, add `from pathlib import Path` after `import shlex`. Then, after `logger = logging.getLogger("ironclaude.config")`, add:
```python

# commander/ — this file is commander/src/ironclaude/config.py. Defaults resolve
# from here, not the process cwd: the orchestrator MCP runs with cwd
# ~/.ironclaude/brain (no config/ dir) and must still read the shipped config.
_COMMANDER_ROOT = Path(__file__).resolve().parents[2]


def default_config_path() -> str:
    return str(_COMMANDER_ROOT / "config" / "ironclaude.json")


def default_machines_config_path() -> str:
    return str(_COMMANDER_ROOT / "config" / "machines.yaml")
```
2. Change `def load_config(config_path: str = "config/ironclaude.json") -> dict:` to `def load_config(config_path: str | None = None) -> dict:`. Insert this as the first line of the body, before `cfg = copy.deepcopy(DEFAULTS)`:
```python
    if config_path is None:
        config_path = default_config_path()
```
3. Change `def load_machines_config(config_path: str = "config/machines.yaml") -> list[dict]:` to `def load_machines_config(config_path: str | None = None) -> list[dict]:`. Insert this as the first line of the body, before `try:`:
```python
    if config_path is None:
        config_path = default_machines_config_path()
```
4. In `orchestrator_mcp.py:38`, change `from ironclaude.config import make_opus_command, effort_for_tier` to `from ironclaude.config import make_opus_command, effort_for_tier, default_machines_config_path`. At `:496`, change `os.environ.get("IC_MACHINES_CONFIG", "config/machines.yaml")` to `os.environ.get("IC_MACHINES_CONFIG", default_machines_config_path())`.
5. In `commander/config/ironclaude.json`, change the `roles` object to:
```json
    "roles": {
      "worker": {"preferred": "claude", "clients": ["claude", "codex"]},
      "grader": {"preferred": "claude", "clients": ["claude", "codex"]}
    }
```

### Step 3: GREEN plus the commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_config.py -q > /private/tmp/claude-502/cfg-g.out 2>&1; echo "cfg=$?"; tail -n 3 /private/tmp/claude-502/cfg-g.out; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/cmd-g.out 2>&1; echo "suite=$?"; tail -n 3 /private/tmp/claude-502/cmd-g.out; grep -rn -e '"config/ironclaude.json"' -e '"config/machines.yaml"' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude; echo "relative_left=$?"
```
Expected:
- `cfg=0`, with the summary line reporting 0 failed;
- `suite=0`, with 0 failed;
- `relative_left=1`, meaning no cwd-relative default remains in the source.

If the full suite fails, run each failing test against `HEAD` to show whether the failure predates this change, without using `git stash`. Fix any regression this change introduced.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/config.py commander/src/ironclaude/orchestrator_mcp.py commander/config/ironclaude.json commander/tests/test_config.py; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-15-commander-config-path-design.md docs/plans/2026-10-15-commander-config-path.md docs/plans/2026-10-15-commander-config-path.plan.json
```
Expected: no output.
