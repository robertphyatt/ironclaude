# IronClaude Worker Plugin Integration Guide

## What IronClaude Worker Plugin Is

The IronClaude worker plugin is a Claude Code plugin installed on every worker. It provides:

- **State machine** enforcing a structured workflow: brainstorming → design → planning → execution → review
- **Professional mode** that blocks code changes except during plan execution
- **MCP tools** for state transitions (mark_design_ready, mark_plan_ready, claim_task, submit_task, etc.)
- **Hooks** that enforce workflow discipline (professional-mode-guard, get-back-to-work, task-completion-validator)
- **Skills** (slash commands) that guide workers through each stage

### IronClaude State Storage

- **Database:** `~/.claude/ironclaude.db` — SQLite with a `sessions` table keyed by `terminal_session` (UUID)
- **Session ID files:** `~/.claude/ironclaude-session-{pane_pid}.id` — written when a worker session starts
- **Hooks config:** `~/.claude/ironclaude-hooks-config.json` — validation backend settings
