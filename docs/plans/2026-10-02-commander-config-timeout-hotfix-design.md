# Commander Allocate-Timeout Hotfix: Design

> **Created:** 2026-10-02
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-10-02-commander-config-timeout-hotfix-requirements.md

## Summary

The workspace CLI timeout becomes configurable, with a default of 900s, so a cold checkout of the 121k-file repo is never killed partway through. A killed allocate leaves a stranded `reserved` row, which is what we're preventing. The config-path fix is deferred because it would also change worker routing.

## Approach

This is the minimal change the operator approved: timeout only.

## Components

1. **`config.py`**: add `"workspace_cli_timeout_seconds": 900` to `DEFAULTS`.
2. **`orchestrator_mcp.py:505`**: build the client with `WorkspaceClient(workspace_plugin_root, ssh_manager=ssh_manager, timeout=int(self._config.get("workspace_cli_timeout_seconds", 900)))`.
3. **Tests** in `commander/tests`:
   - `test_config.py`: `DEFAULTS["workspace_cli_timeout_seconds"] == 900`.
   - `test_orchestrator_mcp.py`: an `OrchestratorTools` built with `config={"workspace_cli_timeout_seconds": 777}` has `_workspace_client._timeout == 777`. With `config={}`, it is 900.

## Rollout

Restart Commander, then watch the Brain's first spawn on roleplaying-agents through the daemon log and the workspace DB.

## Testing Strategy

TDD (RED, then GREEN), followed by the full commander pytest suite.
