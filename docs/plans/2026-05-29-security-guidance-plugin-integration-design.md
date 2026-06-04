# Security-Guidance Plugin Integration Design

> **Created:** 2026-05-29
> **Status:** Design Complete

## Summary

Integrate Anthropic's security-guidance plugin as a default for all IronClaude workers. The plugin provides three-stage vulnerability detection: per-edit pattern matching (free), per-turn model review, and commit-time agentic review. IronClaude will enable Stage 1 (pattern checks) and Stage 3 (commit validation) for workers, disabling Stage 2 (per-turn model review) since IronClaude's existing code-review skill already provides model-based analysis after every task.

## Architecture

Three independent changes:

1. **Worker spawn env var** — Add `ENABLE_STOP_REVIEW=0` to worker environment in `config.py:make_opus_command()`. Disables Stage 2 for workers only. User's own sessions are unaffected.

2. **Plugin presence check** — Add non-blocking verification in `worker/hooks/session-init.sh` that the security-guidance plugin is installed. Logs a warning if missing. Does not block session startup.

3. **README documentation** — Add plugin install step alongside existing ironclaude plugin install instructions.

## Components

### config.py (`commander/src/ironclaude/config.py`)

Modify `make_opus_command()` to include `ENABLE_STOP_REVIEW=0` in the export statement:

```python
def make_opus_command(model: str, effort: str) -> str:
    return f"export CLAUDE_CODE_EFFORT_LEVEL={effort} ENABLE_STOP_REVIEW=0; exec claude --model {shlex.quote(model)} --dangerously-skip-permissions"
```

Single export with space-separated vars. No new function, no new parameter.

### session-init.sh (`worker/hooks/session-init.sh`)

After the stable hook directory block (~line 154), before the statusline path block:

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

Glob pattern `*/security-guidance` accounts for marketplace namespace directory nesting. Verify exact path during implementation.

### README.md

In Quick Start section, after ironclaude plugin install step, add:

```
/plugin install security-guidance@claude-plugins-official
```

One-line explanation: security-guidance provides automatic vulnerability detection during coding sessions.

## Data Flow

1. **Session startup**: session-init.sh checks plugin cache directory, logs warning if missing
2. **Worker spawn**: tmux session exports `ENABLE_STOP_REVIEW=0` alongside existing env vars
3. **During coding**: Stage 1 pattern checks fire on every Edit/Write (free, no model call)
4. **On commit**: Stage 3 agentic review runs in background, reports findings back to worker session
5. **Stage 2 skipped**: `ENABLE_STOP_REVIEW=0` prevents per-turn model review for workers

## Error Handling

- Missing plugin: Non-blocking warning in session-init.sh log. Worker continues without security checks.
- Plugin install failure: Not our concern — plugin installation is a manual user step.
- Stage 3 failure: Plugin handles its own errors, falls back to single-shot review if agentic SDK unavailable.

## Testing Strategy

- Verify `make_opus_command()` output includes `ENABLE_STOP_REVIEW=0` in the export
- Verify session-init.sh detection: test with plugin present and absent in cache directory
- Verify glob pattern matches actual plugin cache directory structure
- Manual: spawn a worker, confirm Stage 1 fires on an edit with `eval()`, confirm Stage 2 does not fire

## Implementation Notes

- The plugin requires Claude Code CLI >= 2.1.144 and Python 3.8+. Both are already IronClaude prerequisites.
- Plugin install command: `/plugin install security-guidance@claude-plugins-official`
- If marketplace not found: `/plugin marketplace add anthropics/claude-plugins-official` first
- Plugin logs to `~/.claude/security/log.txt` for troubleshooting
- The plugin's hooks (PostToolUse on Edit/Write, Stop, PostToolUse on Bash for git commit) are independent of IronClaude's hooks in hooks.json — no ordering conflict
