# v1.2.0 Security Fixes Design

> **Created:** 2026-10-18
> **Status:** Design Complete

Loop: full — guard and security code (both Brain tool guards, a Brain hook, the Codex Brain start check, the handoff quarantine).

## Summary

A background security review of the v1.2.0 commit (e59359b) flagged an incomplete allowlist in `codex_brain_client.py` and untrusted input to security state in `worker_handoff.py`. A report-only Opus investigation, re-verified against the source, confirmed four issues. The operator chose "All four in v1.2.0 (Recommended)" on 2026-10-18. The fixes are folded into the v1.2.0 commit before deploy and tag.

## Requirements

1. **1b — no file writes through Brain git commands (both clients).** `git diff`, `git log` and `git show` accept `--output=<path>` / `--output <path>`, which writes a file. The Codex check (`codex_brain_client.py:142-158`), the Claude check (`brain_client.py:432-449`) and `brain-orchestrator-guard.sh` (`:66-68`) approve it today. All three must refuse any argument that is `--output` or starts with `--output=`. Approved Codex commands run outside the sandbox, so the Python check is load-bearing.
2. **1a — the Codex Brain runs only its known MCP servers.** `_verify_orchestrator_mcp` (`codex_brain_client.py:900-927`) requires `orchestrator` and `episodic-memory` but ignores extra servers, and the trim (`codex_brain_tool_trim.py`) disables only servers named in `$CODEX_HOME/config.toml`. A server from a project or system Codex config would run with no hook gate, while the Claude Brain denies unknown `mcp__` prefixes (`brain_client.py:462-467`).
   - The start fails (via the existing `_fail_start` path) when the live inventory holds any server outside `_TOOL_SERVERS`, naming the extra servers.
   - `_tool_trim_overrides` keeps `research` and `ollama` only when IronClaude's own source file for that server exists (the same existence check as `_optional_mcp_overrides`), so a user-defined server with either name is disabled.
3. **1c — the Python git checks match the hook on `git branch`.** The hook refuses `-d/-D/-m/-M/-c/-C` and branch creation (`brain-orchestrator-guard.sh:70-85`). Both Python checks approve any `git branch …` today. Port the hook's rule: allow only listing forms (no argument, or arguments that all start with `-` and none of `-d -D -m -M -c -C` or their long forms `--delete --move --copy`).
4. **2 — the usage quarantine cannot be set indefinitely.** `_quarantine_expires_at` (`worker_handoff.py:242-255`) accepts any `reset_at` later than the event. Cap it at the event time plus 7 days (the longest native limit window), or now plus 7 days when the event time cannot be parsed. A valid reset inside that window is unchanged; a later one is clamped to the cap.

Both clients keep one behavior (one IronClaude): 1b and 1c change the Claude and Codex checks identically.

## Architecture

- **Git checks.** Add one shared rule to each Python check after the subcommand test: refuse `--output` arguments for every subcommand, and apply the branch rule when the subcommand is `branch`. The two Python checks stay byte-for-byte equivalent (the Codex check is documented as a port of the Claude one). The hook gains the same `--output` refusal in its `log|diff|show` arm and keeps its existing branch rule.
- **Inventory.** After the `episodic-memory` check, compute `sorted(set(inventory) - _TOOL_SERVERS)`. If non-empty, return `"Codex Brain MCP inventory has servers outside the Brain set: <names>"`, which `start()` already turns into a failed start.
- **Trim keep-set.** `_tool_trim_overrides` passes `{"orchestrator", *(name for name in _OPTIONAL_MCP_SERVERS if self._optional_mcp_source_path(name).exists())}`.
- **Quarantine cap.** `_QUARANTINE_MAX_SECONDS = 7 * 24 * 3600`; `limit = (event if event is not None else time.time()) + _QUARANTINE_MAX_SECONDS`; return `float(min(reset_at, limit))` in the native branch.

## Error Handling

- A refused git command returns the existing denial shape on each client (a reason string for Claude, `False` for Codex, exit 2 with a stderr reason for the hook).
- An unexpected MCP server fails the Codex Brain start visibly with the server names, like the existing missing-server errors. No retry, no silent trim.
- The quarantine cap never fails; it only clamps.

## Testing Strategy

TDD, RED first, in the existing suites:
- `commander/tests/test_codex_brain_client.py` and `commander/tests/test_brain_client.py`: `git diff --output=/tmp/x`, `git log --output /tmp/x`, `git show --output=/tmp/x` refused; `git branch -D x`, `git branch new-name`, `git branch --delete x` refused; positive controls `git diff`, `git log --oneline`, `git branch`, `git branch -a`, `git branch --list` still allowed.
- `commander/hooks/tests/test-brain-orchestrator-guard.sh`: `git diff --output=/tmp/x` exits 2 for Claude Bash and Codex `exec_command`; `git diff` still exits 0.
- `test_codex_brain_client.py`: `_verify_orchestrator_mcp` with an inventory holding `orchestrator`, `episodic-memory` and an extra `node_repl` returns an error naming `node_repl`; with only known servers it returns `None` (positive control). `_tool_trim_overrides` drops `research` from the keep-set when its source is missing.
- `commander/tests/test_worker_handoff.py`: extend the `reset_at` parametrization with a reset ten years after the event, expecting event + 7 days; the existing valid-reset and default rows stay unchanged.
- Full Commander suite and `make test-hooks` at the end; an Opus end review of the combined staged diff before the operator folds it into v1.2.0.

## Implementation Notes

- No new verification tooling; no other behavior change.
- CHANGELOG 1.2.0 gets one line for the Brain guard fixes, and the commit message (the findings release paragraph's sibling) mentions them; README needs no change.
- Out of scope (backlog): the Fable end-review observations recorded in the v1.2.0 completeness findings.
