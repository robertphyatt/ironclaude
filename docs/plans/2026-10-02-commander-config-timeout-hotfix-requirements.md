# Commander Allocate-Timeout Hotfix: Requirements

> **Created:** 2026-10-02
> **Scope mode:** hold
> **Context:** docs/plans/2026-10-02-worktree-allocate-findings.md, docs/plans/2026-10-02-commander-unblock-repair-log.md

## Operator directives

- "I need you to untangle the mess it made so that it isn't block anymore"
- The operator chose "Repair + timeout hotfix (Rec.)". When `allocate` could not be timed, the operator chose "Hotfix, then relaunch (Rec.)".
- During planning, a problem surfaced. Making the orchestrator MCP read the real `commander/config/ironclaude.json` would also switch the Brain's worker and grader routing to Codex-preferred (`providers.roles.worker/grader.preferred: "codex"`). The orchestrator uses that config for provider routing (`_provider_router` → `provider_config_from_commander(self._config)`, `orchestrator_mcp.py:1323-1335`).
- The operator chose **"Timeout only now (Rec.)"**:
  - the hotfix only makes the allocate timeout configurable, at 900s;
  - the config-read fix waits for v1.1.16, together with a routing decision.
- Standing rules:
  - no commit trailers;
  - never push without an explicit go;
  - local tests only;
  - run pytest with `PYTHONUNBUFFERED=1`.

## Facts (verified)

- **The timeout is fixed at 60s.**
  - `WorkspaceClient.__init__` takes `timeout: int = 60` (`commander/src/ironclaude/workspace_client.py:30`) and passes it to `subprocess.run` (`:201-208`).
  - `orchestrator_mcp.py:505` builds the client with no timeout argument, so every workspace CLI call is killed at 60s.
  - On roleplaying-agents (121,265 files, 12,091 tracked by LFS), allocate can take longer than that. A kill mid-way leaves a stranded `reserved` row.
- **The orchestrator runs on DEFAULTS.** Its `self._config` comes from `load_config()` running with the Brain's working directory, where the real config file does not exist. A DEFAULTS key is therefore the value that actually reaches it.
- **The Brain will wait long enough.** Its hard-timeout watchdog during tool execution is 1800s (`brain_client.py:1043-1053`).

## Requirements

- **H1.** Add the DEFAULTS key `"workspace_cli_timeout_seconds": 900` to `commander/src/ironclaude/config.py`.
- **H2.** `orchestrator_mcp.py:505` passes `timeout=int(self._config.get("workspace_cli_timeout_seconds", 900))` to `WorkspaceClient(...)`.
  - A test proves that a non-default configured value (for example 777) reaches the client.
  - A test proves that a missing key falls back to 900.
  - A test proves that DEFAULTS contains 900.
- **H3.** The full commander suite passes.
- **H4.** After the code lands:
  - Restart the Commander, detached, with the Claude Code environment variables unset.
  - Verify that the daemon and the Brain are up.
  - Watch the first real worker spawn on roleplaying-agents. It must either reach an `active` assignment or fail with a recorded reason that is not a 60s timeout. No stranded `reserved` row may remain.

## Non-goals (deferred to v1.1.16)

- The config-path fix and the Codex-versus-Claude routing decision.
- Pressure-based swap guarding.
- Async allocate, and reclaiming dead-writer reservations.
- A single-instance lock for sync.
- LFS and repo slimming.
- Any version bump or push.
