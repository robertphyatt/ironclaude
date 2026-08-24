# Retire Orphan plan_artifact_receipts Row - Findings

- Target session: 'bc27e45c-2bcd-4c7b-a245-aed7a9850b99'
- DB path: '/Users/roberthyatt/.claude/ironclaude.db'
- Pre-check inactive-receipt count for this session: 1
- Command deviation note: the executed command replaced markdown backtick code-spans with single quotes in this file's own output text. The managed-worktree guard's anti-escape heuristic (workspace-path-adapter.sh, workspace_command_has_explicit_checkout_escape) refuses any Bash command containing a literal backtick character, treating it as command substitution regardless of Python string-literal context. The plan.json step's command field still contains backticks and is unrunnable as written -- this is a plan-artifact staleness, not a logic, scope, or SQL change. Operator-authorized live deviation, cosmetic only (2026-08-18).
- Retired 1 inactive receipt(s). retired_at set via SQLite datetime('now').
- SQL run:
  - SELECT COUNT(*) FROM plan_artifact_receipts WHERE status='inactive' AND terminal_session=?
  - UPDATE plan_artifact_receipts SET status='retired', retired_at=datetime('now') WHERE status='inactive' AND terminal_session=?
