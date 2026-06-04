# Security-Guidance Plugin Integration Analysis

> **Date:** 2026-05-29
> **Decision:** Integrate as default for all IronClaude workers
> **Implemented:** commit dd49526
> **Design doc:** [docs/plans/2026-05-29-security-guidance-plugin-integration-design.md](../plans/2026-05-29-security-guidance-plugin-integration-design.md)

## Summary

Recommendation: **Yes, integrate.** Anthropic's security-guidance plugin provides three-stage automatic vulnerability detection during coding sessions. It is free, requires no configuration beyond an env var to disable one conflicting stage, and adds detection layers that IronClaude does not currently have. Implemented in commit dd49526.

## Plugin Capabilities

The plugin runs automatically during Claude Code sessions with three independent review stages:

**Stage 1 — Lightweight pattern checks (fires on every Edit/Write)**
Runs without calling a model. Searches edited files for risky constructs:
- `eval()`, `new Function()` — code injection
- `os.system()`, `child_process.exec()` — command injection
- Unsafe deserialization patterns
- DOM injection patterns (`innerHTML`, `document.write`)

Zero usage cost. Fires on every file edit.

**Stage 2 — Model analysis (fires after each turn)**
Claude examines the complete git diff to catch vulnerabilities missed by pattern matching:
- Authorization bypass and IDOR (insecure direct object references)
- Injection flaws not caught by static patterns
- SSRF (server-side request forgery)
- Weak cryptography

Consumes model tokens. Fires after every user turn.

**Stage 3 — Deep agentic review (fires on git commit/push)**
Reviews surrounding files, sanitizers, and code paths to validate findings and reduce false positives. Runs as a background agentic process. Requires a git repository.

## Compatibility Analysis

### Stage 1: Complementary — enable

IronClaude has no equivalent free pattern-checking layer. Stage 1 adds zero cost and fires independently of IronClaude's hooks (it uses its own PostToolUse handler on Edit/Write, with no ordering conflict with hooks.json).

### Stage 2: Conflict — disable for workers

IronClaude's `code-review` skill already provides model-based analysis after every task. Leaving Stage 2 enabled for workers would:
- Duplicate the model-review step on every turn
- Add token cost with no incremental security value
- Create noise from two overlapping reviewers

**Resolution:** Set `ENABLE_STOP_REVIEW=0` in worker spawn environment. This disables Stage 2 for workers only. Users' own Claude Code sessions are unaffected.

### Stage 3: Complementary — enable

Stage 3 fires on `git commit`, independently of IronClaude's code-review skill (which fires after each task during plan execution). It provides a second, independent security check at the commit boundary — orthogonal to the workflow-gate reviews. No conflict with hooks.json.

## Configuration Rationale

Workers are spawned with `ENABLE_STOP_REVIEW=0` in their environment:

```bash
export IC_ROLE=worker; export IC_WORKER_ID=<id>; export ENABLE_STOP_REVIEW=0; exec claude ...
```

The plugin reads this env var and skips Stage 2 (per-turn model review). Stages 1 and 3 are unaffected by this flag.

Injected at all three spawn sites:
- `commander/src/ironclaude/orchestrator_mcp.py:1427` (MCP batch spawn)
- `commander/src/ironclaude/orchestrator_mcp.py:1596` (local spawn)
- `commander/src/ironclaude/main.py:1115` (Slack-driven spawn)

## Implementation Summary

Three changes landed in commit dd49526:

**1. Worker spawn env var** (`orchestrator_mcp.py:1427,1596`, `main.py:1115`)
Added `export ENABLE_STOP_REVIEW=0` to the env block in all worker spawn command strings. Tests updated to assert the new env var is present (`test_orchestrator_mcp.py:82,630,644`).

**2. Plugin presence check** (`worker/hooks/session-init.sh:156-163`)
Non-blocking check added after the stable hook directory block. Globs `~/.claude/plugins/cache/*/security-guidance` at session start. Logs a warning if the plugin is not installed; does not block session startup.

**3. README documentation** (`README.md`)
Added `/plugin install security-guidance@claude-plugins-official` to the Quick Start section after the existing ironclaude plugin install step.

## Trade-offs

**Gained:**
- Free pattern-check layer on every edit (Stage 1) — catches obvious injection patterns before any review
- Independent commit-time security validation (Stage 3) — second opinion at the git boundary
- Zero additional token cost for workers (Stage 2 disabled)

**Not covered:**
- Plugin requires manual installation (`/plugin install security-guidance@claude-plugins-official`) — IronClaude does not auto-install plugins, only warns if missing
- Stage 3 requires a git repository — workers without git repos get Stage 1 only
- Plugin requires Claude Code CLI >= 2.1.144 and Python >= 3.8 (both are existing IronClaude prerequisites)
