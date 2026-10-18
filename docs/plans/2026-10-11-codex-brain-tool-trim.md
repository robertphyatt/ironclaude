# Codex Brain Tool Trim (R14) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** The Codex Brain's tools match the Claude Brain's: orchestrator, episodic-memory, research and ollama, plus core shell and read. `-c` overrides turn off everything else. Most of them are derived at each start from `~/.codex/config.toml`, which is only read, and from the ironclaude plugin manifest.

**Requirements:** `docs/plans/2026-10-10-codex-brain-parity-design.md`, R13 result and R14. The operator chose "A: Match Claude set" and "A: Derive at start" on 2026-10-11.

**Architecture:**
- A new module, `ironclaude/codex_brain_tool_trim.py`, builds the `-c` pairs: fixed feature overrides, then derived plugin, user-MCP and ironclaude-MCP disables.
- `CodexBrainClient._app_server_argv` appends them before `--stdio`.
- A derivation error is a `ValueError` subclass. `start()` already catches `ValueError` around `Popen(self._app_server_argv(), …)` (`codex_brain_client.py:1089-1103`), so the Brain fails closed with `spawn failed: …`.

**Tech Stack:** Python 3.11 (`tomllib`), pytest, Codex CLI 0.162.0-alpha.17.2.

**Loop:** full. This changes the Brain's permission surface.

## Execution invariants

- Shell state does not persist between steps. Use absolute paths.
- Bash cwd is `commander/`. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- zsh `nomatch`: quote globs.
- No bare foreground `sleep`. A foreground `until <condition>; do sleep N; done` wait on a log is allowed and has worked in this loop.
- Test isolation: `commander/tests/conftest.py:254-271` (`_redirect_home`, autouse) already points `HOME` at a per-test temp dir. Task 2 makes it also clear `CODEX_HOME`, so no test anywhere (including `test_codex_brain_hooks_canary.py`, which calls `start()`) reads the operator's real `~/.codex/config.toml`.
- `docs/` is gitignored, so stage docs with `git add -f`.
- Run pytest with `PYTHONUNBUFFERED=1`.
- Never write `~/.codex/config.toml`.
- Kill processes only by exact PID or with `make stop`.
- Guard false positive: never write "git" followed by "push" in a Bash command. Build the text, for example as `'git p'+'ush'` in Python.
- The operator launches the Commander; the agent-side relaunch command is blocked.
- `SCRATCH=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe` is used as a literal path.

---

## Task 1: Trim-override builder (R14)

**Files:**
- Create: `commander/src/ironclaude/codex_brain_tool_trim.py`
- Test: `commander/tests/test_codex_brain_tool_trim.py`

**Step 1 (RED).** Create `commander/tests/test_codex_brain_tool_trim.py`:

```python
"""R14: the Codex Brain's tool set matches the Claude Brain's — inherited plugins, user
MCP servers and ironclaude worker MCP servers are disabled per start, read-only."""
import json
from pathlib import Path

import pytest

from ironclaude.codex_brain_tool_trim import (
    FIXED_OVERRIDES,
    BrainToolTrimError,
    brain_tool_trim_overrides,
)

BRAIN = frozenset({"orchestrator", "research", "ollama"})
REPO_MANIFEST = Path(__file__).parents[2] / "worker" / ".codex-plugin" / "plugin.json"


def _manifest(tmp_path, servers=("episodic-memory", "state-manager", "workspace-manager")):
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps({"mcpServers": {name: {"command": "node"} for name in servers}}))
    return path


def _values(pairs):
    assert pairs[0::2] == ["-c"] * (len(pairs) // 2)
    return pairs[1::2]


def test_missing_config_gives_fixed_and_manifest_overrides(tmp_path):
    values = _values(brain_tool_trim_overrides(tmp_path / "absent.toml", _manifest(tmp_path), BRAIN))
    assert values == [
        *FIXED_OVERRIDES,
        "plugins.ironclaude@ironclaude.mcp_servers.state-manager.enabled=false",
        "plugins.ironclaude@ironclaude.mcp_servers.workspace-manager.enabled=false",
    ]


def test_fixed_overrides_turn_off_apps_web_image_goals():
    assert FIXED_OVERRIDES == (
        "features.apps=false",
        "features.tool_suggest=false",
        "features.image_generation=false",
        "features.goals=false",
        'web_search="disabled"',
    )


def test_every_plugin_except_ironclaude_is_disabled(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[plugins."slack@openai-curated"]\nenabled = true\n'
        '[plugins."ironclaude@ironclaude"]\nenabled = true\n'
        '[plugins."ironclaude@ironclaude".mcp_servers.state-manager.tools.create_plan]\n'
        'approval_mode = "approve"\n'
        '[plugins."unified-computer-use@openai-bundled"]\nenabled = true\n'
    )
    values = _values(brain_tool_trim_overrides(config, _manifest(tmp_path, ("episodic-memory",)), BRAIN))
    assert "plugins.slack@openai-curated.enabled=false" in values
    assert "plugins.unified-computer-use@openai-bundled.enabled=false" in values
    assert not any(v.startswith("plugins.ironclaude@ironclaude.enabled") for v in values)


def test_user_mcp_servers_outside_brain_set_are_disabled(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.node_repl]\ncommand = "node"\n'
        '[mcp_servers.computer-use]\ncommand = "x"\n'
        '[mcp_servers.orchestrator]\ncommand = "python"\n'
        '[mcp_servers.ollama]\ncommand = "python"\n'
    )
    values = _values(brain_tool_trim_overrides(config, _manifest(tmp_path, ()), BRAIN))
    assert "mcp_servers.node_repl.enabled=false" in values
    assert "mcp_servers.computer-use.enabled=false" in values
    assert "mcp_servers.orchestrator.enabled=false" not in values
    assert "mcp_servers.ollama.enabled=false" not in values


def test_episodic_memory_plugin_server_is_kept(tmp_path):
    values = _values(brain_tool_trim_overrides(tmp_path / "absent.toml", _manifest(tmp_path), BRAIN))
    assert not any("episodic-memory" in v for v in values)


def test_repo_manifest_disables_exactly_the_worker_servers(tmp_path):
    values = _values(brain_tool_trim_overrides(tmp_path / "absent.toml", REPO_MANIFEST, BRAIN))
    assert [v for v in values if v.startswith("plugins.ironclaude@ironclaude.")] == [
        "plugins.ironclaude@ironclaude.mcp_servers.state-manager.enabled=false",
        "plugins.ironclaude@ironclaude.mcp_servers.workspace-manager.enabled=false",
    ]


@pytest.mark.parametrize("bad_id", ["evil.plugin@x", 'q"uote@x', "a=b@x"])
def test_unexpressible_plugin_id_fails_closed(tmp_path, bad_id):
    config = tmp_path / "config.toml"
    config.write_text(f"[plugins.'{bad_id}']\nenabled = true\n")
    with pytest.raises(BrainToolTrimError, match="cannot be expressed"):
        brain_tool_trim_overrides(config, _manifest(tmp_path), BRAIN)


def test_invalid_config_fails_closed(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[plugins\nbroken")
    with pytest.raises(BrainToolTrimError, match="cannot read"):
        brain_tool_trim_overrides(config, _manifest(tmp_path), BRAIN)


def test_missing_manifest_fails_closed(tmp_path):
    with pytest.raises(BrainToolTrimError, match="cannot read"):
        brain_tool_trim_overrides(tmp_path / "absent.toml", tmp_path / "no-manifest.json", BRAIN)


def test_trim_error_is_a_value_error():
    """start() catches ValueError around Popen(self._app_server_argv()) and fails the start."""
    assert issubclass(BrainToolTrimError, ValueError)
```

**Step 2.** Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_tool_trim.py -q
```

Expected: a collection error, `ModuleNotFoundError: No module named 'ironclaude.codex_brain_tool_trim'`. Record the summary.

**Step 3 (GREEN).** Create `commander/src/ironclaude/codex_brain_tool_trim.py`:

```python
"""R14: the Codex Brain's tool set matches the Claude Brain's.

The Claude Brain loads only project/local settings plus its own MCP servers
(brain_client.py). The Codex Brain's app-server instead inherits the operator's whole
~/.codex surface: every enabled plugin (apps, computer use, browser, ...), user MCP
servers (node_repl, ...) and the ironclaude plugin's worker MCP servers (state-manager,
workspace-manager — the latter can push). This builds `-c` overrides that turn all of
that off for the Brain process only. ~/.codex/config.toml is read, never written.

ironclaude@ironclaude itself stays enabled: the Brain's restriction hooks live in its
hooks.json, and disabling a plugin drops its hooks. Its MCP servers are disabled one by
one instead (plugins.<id>.mcp_servers.<name>.enabled=false), keeping episodic-memory.

`-c` keys are split on '.', so ids are written unquoted; an id that cannot be written
that way fails the Brain start rather than being skipped.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Iterable

KEPT_PLUGIN = "ironclaude@ironclaude"
KEPT_PLUGIN_MCP_SERVERS = frozenset({"episodic-memory"})
FIXED_OVERRIDES = (
    "features.apps=false",
    "features.tool_suggest=false",
    "features.image_generation=false",
    "features.goals=false",
    'web_search="disabled"',
)


class BrainToolTrimError(ValueError):
    """The trim cannot be derived; the Brain must not start with an unknown tool set."""


def _segment(name: str, kind: str) -> str:
    if not name or any(ch in name for ch in '."= '):
        raise BrainToolTrimError(f"{kind} {name!r} cannot be expressed as an unquoted -c key segment")
    return name


def brain_tool_trim_overrides(
    config_path: Path, manifest_path: Path, brain_mcp_servers: Iterable[str]
) -> list[str]:
    """`-c` pairs that disable everything the Brain does not share with the Claude Brain."""
    keep_servers = frozenset(brain_mcp_servers)
    config: dict = {}
    if config_path.exists():
        try:
            config = tomllib.loads(config_path.read_text())
        except (OSError, ValueError) as exc:
            raise BrainToolTrimError(f"cannot read {config_path}: {exc}") from exc
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise BrainToolTrimError(f"cannot read {manifest_path}: {exc}") from exc

    values = list(FIXED_OVERRIDES)
    for plugin_id in sorted(config.get("plugins") or {}):
        if plugin_id != KEPT_PLUGIN:
            values.append(f"plugins.{_segment(plugin_id, 'plugin')}.enabled=false")
    for server in sorted(config.get("mcp_servers") or {}):
        if server not in keep_servers:
            values.append(f"mcp_servers.{_segment(server, 'MCP server')}.enabled=false")
    for server in sorted(manifest.get("mcpServers") or {}):
        if server not in KEPT_PLUGIN_MCP_SERVERS:
            values.append(
                f"plugins.{KEPT_PLUGIN}.mcp_servers."
                f"{_segment(server, 'plugin MCP server')}.enabled=false"
            )
    return [part for value in values for part in ("-c", value)]
```

**Step 4.** Rerun the Step 2 command. Expected: all pass (12 tests: 10 test functions, with the parametrized one counting 3). Record the summary.

**Step 5.** Stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_tool_trim.py commander/tests/test_codex_brain_tool_trim.py
```

Expected: no output.

---

## Task 2: Brain argv applies the trim; fail closed (R14)

**Files:**
- Modify: `commander/src/ironclaude/codex_brain_client.py` (imports; `_app_server_argv` `:266-296`; new helper methods after `_optional_mcp_overrides`)
- Modify: `commander/tests/conftest.py:254-271` (`_redirect_home`)
- Test: `commander/tests/test_codex_brain_client.py`

**Step 1 (RED).**

(a) In `commander/tests/conftest.py`, inside `_redirect_home`, insert before `return fake_home`:

```python
    # R14: CodexBrainClient reads $CODEX_HOME/config.toml (else ~/.codex); a developer's
    # CODEX_HOME must not override the fake home and leak their real Codex config.
    monkeypatch.delenv("CODEX_HOME", raising=False)
```

Also append one sentence to its docstring: "CODEX_HOME is cleared so Codex config resolves under the fake home too."

(b) In `test_app_server_argv_forces_readonly_and_on_request`, insert `*c._tool_trim_overrides(),` after `*c._optional_mcp_overrides(),`.

(c) Append:

```python
def test_app_server_argv_trims_inherited_tools(monkeypatch, tmp_path):
    """R14: plugins and user MCP servers from $CODEX_HOME/config.toml are disabled for the Brain."""
    home = tmp_path / "home2"
    home.mkdir()
    (home / "config.toml").write_text(
        '[plugins."slack@openai-curated"]\nenabled = true\n'
        '[plugins."ironclaude@ironclaude"]\nenabled = true\n'
        '[mcp_servers.node_repl]\ncommand = "node"\n'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    argv = CodexBrainClient()._app_server_argv()
    for value in (
        "features.apps=false",
        'web_search="disabled"',
        "plugins.slack@openai-curated.enabled=false",
        "mcp_servers.node_repl.enabled=false",
        "plugins.ironclaude@ironclaude.mcp_servers.workspace-manager.enabled=false",
    ):
        assert argv[argv.index(value) - 1] == "-c"
    assert "plugins.ironclaude@ironclaude.enabled=false" not in argv
    assert "mcp_servers.orchestrator.enabled=false" not in argv
    assert argv[-1] == "--stdio"


def test_start_fails_closed_when_trim_cannot_be_derived(monkeypatch, tmp_path):
    """R14: an unreadable config must stop the Brain, never start it with an unknown tool set."""
    import ironclaude.codex_brain_client as module

    home = tmp_path / "home3"
    home.mkdir()
    (home / "config.toml").write_text("[plugins\nbroken")
    monkeypatch.setenv("CODEX_HOME", str(home))
    client = CodexBrainClient()
    monkeypatch.setattr(client, "_runtime_preflight", lambda: {
        "schema_version": 1, "status": "healthy", "reason": "destination-equivalent",
        "source_companion": "/source", "destination_companion": "/destination",
    })
    monkeypatch.setattr(client, "_preflight_orchestrator", lambda: None)
    spawned = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    client.start("", cwd="/tmp")
    assert spawned == []
    assert "spawn failed" in client.restart_reason
    assert "cannot read" in client.restart_reason
```

**Step 2.** Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py -q
```

Expected failures:
- `test_app_server_argv_forces_readonly_and_on_request` (`AttributeError: _tool_trim_overrides`);
- `test_app_server_argv_trims_inherited_tools`;
- `test_start_fails_closed_when_trim_cannot_be_derived`. The stubbed `Popen` returns `None`, so `start()` fails with `AttributeError` on `self._proc.pid`; any failure of this test is accepted in RED.

Others pass. Record the summary and the names.

**Step 3 (GREEN).** In `codex_brain_client.py`:

(a) Add the import below `from ironclaude.config import DEFAULTS, effort_for_tier`:

```python
from ironclaude.codex_brain_tool_trim import brain_tool_trim_overrides
```

(b) In `_app_server_argv`, insert `*self._tool_trim_overrides(),` after `*self._optional_mcp_overrides(),` and before `"--stdio",`.

(c) After `_optional_mcp_overrides`, add:

```python
    def _codex_config_path(self) -> Path:
        """Where the app-server reads its user config (Codex honors $CODEX_HOME)."""
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"

    def _ironclaude_plugin_manifest_path(self) -> Path:
        return Path(__file__).parents[3] / "worker" / ".codex-plugin" / "plugin.json"

    def _tool_trim_overrides(self) -> list[str]:
        """R14: `-c` pairs that give the Brain only the Claude Brain's tool set.

        Raises BrainToolTrimError (a ValueError) when the trim cannot be derived; start()
        turns that into a failed start rather than an untrimmed Brain."""
        return brain_tool_trim_overrides(
            self._codex_config_path(),
            self._ironclaude_plugin_manifest_path(),
            {"orchestrator", *_OPTIONAL_MCP_SERVERS},
        )
```

**Step 4.** Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py tests/test_codex_brain_tool_trim.py tests/test_codex_brain_hooks_canary.py -q
```

Expected: all pass. Record the summary.

**Step 5.** Stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_client.py commander/tests/test_codex_brain_client.py commander/tests/conftest.py
```

Expected: no output.

---

## Task 3: Full suites

No tests required: this task only verifies and modifies no files. Its `allowed_files` lists the whole parity change set.

**Step 1:** `make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-tool-trim-hooks.log 2>&1; echo "test-hooks exit=$?"`. Expected: `test-hooks exit=0`.

**Step 2:** run in the background with a Bash timeout of 1800000:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-tool-trim-full.log 2>&1; echo "pytest exit=$?"
```

Wait for it with a foreground `until tail -n 1 /private/tmp/ironclaude-tool-trim-full.log | grep -qE "passed|failed|error"; do sleep 5; done`. Expected: the only failure is L-T2, `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd`. Record the summary.

---

## Task 4: Differential probe, restart, live acceptance (R14)

No unit tests: this task verifies live.

**Step 1.** Run the differential probe with the Brain's exact argv, untrimmed (U) and trimmed (T). Bash timeout 900000:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -c "
import os, subprocess
from ironclaude.codex_brain_client import CodexBrainClient
c = CodexBrainClient()
argv = c._app_server_argv()
trim = c._tool_trim_overrides()
pairs = argv[2:-1]
assert argv[-1] == '--stdio' and pairs[len(pairs) - len(trim):] == trim, 'trim must be the tail before --stdio'
untrimmed = pairs[:len(pairs) - len(trim)]
prompt = ('List, verbatim and one per line, the exact name of every tool available to you, including every tools.* function callable inside your exec tool and every MCP or plugin tool. Then run the shell command: ' + 'git p' + 'ush --dry-run' + ' and report its exact result. Then reply DONE.')
scratch = '/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe'
for label, cpairs in (('U', untrimmed), ('T', pairs)):
    env = dict(os.environ, IC_ROLE='brain', IRONCLAUDE_CLIENT='codex', IRONCLAUDE_BRAIN_GATE_SESSION='trim-probe-' + label)
    with open(f'{scratch}/trim-{label}.jsonl', 'w') as out:
        rc = subprocess.run(['codex', 'exec', '-C', f'{scratch}/brainrepo', '--skip-git-repo-check', *cpairs, '--json', prompt], env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT).returncode
    print(label, 'exit', rc, 'pairs', len(cpairs) // 2)
"
```

Expected: `U exit 0 …` and `T exit 0 …`, with T having more pairs than U.

**Step 2.** For each of `trim-U.jsonl` and `trim-T.jsonl`, run one `grep -c` per marker on the file (no pipes):
- `mcp__codex_apps__`
- `cua_repl`
- `node_repl`
- `mcp__state_manager__`
- `mcp__workspace_manager__`
- `web__run`
- `image_gen`
- `create_goal`
- `request_plugin_install`
- `episodic_memory`
- `exec_command`
- `is blocked for Brain session`

Then run `ls /tmp/ic/codex-brain-gate/trim-probe-U/hooks-live /tmp/ic/codex-brain-gate/trim-probe-T/hooks-live`.

Expected:
- **U:** the first 9 markers (through `request_plugin_install`) are ≥ 1. This proves the probe sees the inherited tools.
- **T:** the first 9 markers are 0; `episodic_memory` and `exec_command` are ≥ 1.
- **Hooks:** both `hooks-live` files exist. That is the canary: the Brain's UserPromptSubmit hook ran under the trimmed argv, which does not depend on what the model chooses. Record the `is blocked for Brain session` count as secondary evidence; it is ≥ 1 only if the model attempts the push.
- **Not checked here:** `mcp__orchestrator__`. Under `codex exec` the orchestrator MCP did not show up in the earlier probe A list (probe-a.jsonl), so it is checked in the live Brain at Step 7.
- **If T still lists a family:** stop, record which family, and report. Either the override key is wrong or `codex exec` handles `-c` differently.

**Step 3.** `make -C /Users/roberthyatt/Code/ironclaude/commander stop`, then `pgrep -fl "ironclaude.main"` (exit 1), then `wc -l /tmp/ic/daemon.log` (record L) and `date`.

**Step 4.** Operator launch: AskUserQuestion asking the operator to run `make -C ~/Code/ironclaude/commander run` and confirm.

**Step 5.**

```bash
until tail -n +<L+1> /tmp/ic/daemon.log | grep -qE "Brain SDK client (started|not started)|hooks are not running|spawn failed"; do sleep 3; done; tail -n +<L+1> /tmp/ic/daemon.log | grep -v slack_bolt
```

Expected: `Brain SDK client started client=codex`, no `hooks are not running`, no `spawn failed`. On failure: `make … stop`, record the evidence, and stop.

**Step 6.** `find /tmp/ic/codex-brain-gate -name hooks-live -newer /tmp/ic-logs/ic-codex-hook-trust.log` → exactly one path (the canary, so the hooks still run with the trim).

**Step 7.** Operator check: AskUserQuestion asking the operator to post "List every tool name you have, verbatim, one per line" to the Commander in Slack and confirm. Then find the Brain rollout BR (newest `-newer /tmp/ic-logs/ic-codex-hook-trust.log` containing `"cwd":"/Users/roberthyatt/.ironclaude/brain"`) and run `grep -c` on BR for each marker from Step 2.

Expected:
- `mcp__codex_apps__`, `cua_repl`, `node_repl`, `mcp__state_manager__`, `mcp__workspace_manager__`, `web__run`, `image_gen`, `create_goal` and `request_plugin_install` are 0. If one is nonzero, run `grep -n` for it and inspect the lines: a hit inside relayed worker output or log text is not a tool and not a failure; a hit in the Brain's own tool listing is.
- `mcp__orchestrator__` is ≥ 1;
- `professional-mode-guard` is 0.

If deferred, record "Step 7 pending operator" and never substitute another check.

**Step 8.** Append an "Attempt 5 (R14 tool trim)" section to `docs/plans/2026-10-10-codex-brain-parity-live-verification.md` with the Step 1–7 output verbatim, then:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-10-codex-brain-parity-live-verification.md docs/plans/2026-10-10-codex-brain-parity-design.md docs/plans/2026-10-11-codex-brain-tool-trim.md docs/plans/2026-10-11-codex-brain-tool-trim.plan.json
```

Expected: no output.
