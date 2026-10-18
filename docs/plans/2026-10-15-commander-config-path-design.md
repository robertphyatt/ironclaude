# Commander Config Path Design

> **Created:** 2026-10-15
> **Status:** Design Complete

Loop: full. This changes how the spawn gate gets its config and how worker and grader clients are routed. About 4 files and 40 lines.

## Summary

`config.load_config()` and `load_machines_config()` default to cwd-relative paths: `config/ironclaude.json` and `config/machines.yaml`, at `config.py:143` and `:234`. The daemon runs from `commander/`, so it reads the file. The orchestrator MCP does not:
- it runs as a Brain subprocess with cwd `~/.ironclaude/brain`, which has no `config/`;
- so `orchestrator_mcp.py:8430` falls back to `DEFAULTS`: swap limit 8 GB, block at pressure level 2, Codex disabled.

As a result, d1501 Fix Loop 7 was rejected (swap 10.9 > 8.0). This Mac idles at pressure level 2, so it would keep blocking.

On 10-02 the fix was deferred because loading the file also changes worker and grader routing.

## Requirements

- **R1:** both loaders default to paths anchored to the package location (`commander/config/…`), not the cwd. This is the same approach `brain_client.py:342` already uses for the DB path. Explicit paths passed by callers and tests keep working. *(Operator, 2026-10-15: "Anchor to package (Recommended)".)*
- **R2:** worker and grader routing prefer Claude: `"preferred": "claude"`, with clients `["claude", "codex"]` so Codex stays a fallback. The orchestrator then loads the whole file. *(Operator, 2026-10-15: "Let's have it be Claude preferred. It's called ironclaude after all. Other than that, proceed as suggested.")*
- **R3:** the spawn gate's effective values in the orchestrator become the file's: `max_swap_used_gb` 50, `spawn_block_pressure_level` 4.
- **R4:** a Commander restart applies the change. Daemon code runs from the working tree; no plugin deploy is needed.

**Verified side effects of loading the full file:**
- Codex is enabled as a client; the default is `false`.
- The file's `machines` key has no consumer. Only `machines.yaml` is read (`config.py:249`, `orchestrator_mcp.py:890`), and that file does not exist.
- All other keys in the file equal their defaults: poll, heartbeat, stale threshold, retries, `tmp_dir`/`log_dir` and the advisor values.

**Non-goals:** the env-var approach, and other config keys.

## Architecture

- **`config.py`:**
  - Add `_COMMANDER_ROOT = Path(__file__).resolve().parents[2]`, which is `commander/`.
  - `load_config` defaults to `str(_COMMANDER_ROOT / "config" / "ironclaude.json")`.
  - `load_machines_config` defaults to `str(_COMMANDER_ROOT / "config" / "machines.yaml")`.
- **`orchestrator_mcp.py:496`:** the fallback `"config/machines.yaml"` becomes the same anchored path. The env override `IC_MACHINES_CONFIG` still wins.
- **`commander/config/ironclaude.json`:** set `providers.roles.worker` and `grader` to `{"preferred": "claude", "clients": ["claude", "codex"]}`.

## Testing Strategy

**New tests in `commander/tests/test_config.py`:**
- `load_config()` with no argument, run from an unrelated cwd (`monkeypatch.chdir(tmp_path)`), returns the repo file's `max_swap_used_gb` (50) and `spawn_block_pressure_level` (4), not the defaults (8.0 and 2). This fails today.
- Worker and grader routing from that load is `preferred == "claude"`.
- `load_machines_config()` from an unrelated cwd resolves to `commander/config/machines.yaml`. The plan picks how to observe this.

**Regression:** the full `test_config.py` and the commander suite stay green.

## Implementation Notes

- After a commit, restart the Commander (`make stop`, then a detached relaunch). Check the Brain's next spawn of d1501 Fix Loop 7.
- Existing `test_config.py:152-170` call `load_config()` with no argument from `commander/`. That resolves to the same file before and after the change.
