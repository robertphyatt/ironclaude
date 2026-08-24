# Codex deactivate-deadlock investigation Requirements (operator-approved)

> **Created:** 2026-08-21
> **Source:** operator directive (the Codex deactivate escape hatch is broken → "no deadlocks /
> no operator intervention"), chose "Bug 2 first: the deactivate escape." Read-only diagnosis; no
> fix until the cause is proven. Human commits, no push.

## Approved scope

Prove, against live state, WHY `$ironclaude:deactivate-professional-mode` in the Codex session
`019fc3b0-2757-7b22-b792-0011f9b29929` did not set `professional_mode='off'` (transcript line
44224: `get_professional_mode` returned `on`). Produce a findings note with the confirmed root
cause and the fix direction. Write no code, config, DB row, or professional-mode change.

- **R1 — does Codex run `state-activator.sh`?** Inspect the Codex hook wiring (`~/.codex/config.toml`,
  the codex plugin manifest/hooks under the `1.1.6+codex.<buster>` cache root) for a prompt-submit →
  `state-activator.sh` binding, AND look for a hook-execution record for the 21:51 deactivate turn
  in the ironclaude hook log and/or `~/.codex/logs_2.sqlite`. Absence (query shown) is the evidence
  for H-A.

- **R2 — what prompt bytes reached the deactivate branch?** `state-activator.sh` reads the prompt
  from `.prompt` (:36), `exit 0` on empty (:38-40); the deactivate branch (:137-151) is gated ONLY
  by the `:138` slash regex, the `:144` codex-link regex, and the `:148` exact
  `$ironclaude:deactivate-professional-mode` string. The `hook_event_name=="UserPromptSubmit" &&
  thread_source!="subagent"` gate at `:53` wraps ONLY the HUMAN_OPERATION intent block (:53-72) —
  it does NOT gate deactivation, so `hook_event_name` must not drive an H-D verdict. Capture the
  delivered `.prompt` and `.session_id` (drives `SESSION_TAG`, see R3) from the codex payload if it
  is logged, AND — independent of hook-payload logging — extract the operator's message bytes from
  the rollout JSONL near line 44224 and test them against `:138`/`:144`/`:148` locally. H-D =
  `.prompt` missing/empty OR the bytes match none of those three conditions.

- **R3 — durable evidence of the deactivate branch + which DB/session key.** There is NO ironclaude
  hook-log file: `log_hook`/`log_warning`/`log_error` emit `{"systemMessage":…}` JSON to stdout
  only; `~/.claude/ironclaude-errors.log` is the MCP-error sideband, never state-activator. Evidence
  instead: (a) the `audit_log` table in `~/.claude/ironclaude.db` — the SUCCESS path writes
  `action='professional_mode_off'` (state-activator.sh:163/:173); (b) the stdout-only 0-rows warning
  (:166/:176), searched for in the codex transcript/`logs_2.sqlite` (R2's domain). The success stdout
  line is verbose-gated (proves nothing if absent); `log_warning` is not. Then the DB/key: the hook's
  `DB_PATH` is hardcoded `~/.claude/ironclaude.db` (hook-logger.sh:29); the `state-manager` MCP uses
  `getDbPath()` (state-manager src/db.ts:30-35) = `STATE_MANAGER_DB_PATH` override else the same file
  — so H-C reduces to whether `STATE_MANAGER_DB_PATH` is set for the codex MCP. For H-B, `SAFE_SESSION`
  derives from payload `.session_id` (defaults `'none'`); read the `sessions` row for `019fc3b0-…`
  and compare its key to the hook's UPDATE target. (A1: the state cache `ironclaude-state-cache-
  019fc3b0-….json` is written only by state-activator.sh conditional on that `sessions` row existing,
  so its existence already largely refutes strong H-A and `SESSION_TAG='none'`.)

- **R4 — findings note.** Write `docs/plans/2026-08-21-codex-deactivate-deadlock-findings.md`
  naming the ONE confirmed hypothesis (H-A / H-B / H-C / H-D) with the exact evidence for it and
  against the others, plus the fix direction. `git add -f`.

- **R5 — strictly read-only.** Only `cat`, `grep`, `find`, `ls`, `sqlite3 -readonly … SELECT`. NO
  DB write, NO `set_professional_mode`, NO config/hook edit, NO change to the codex session's
  professional-mode state. Absence must be provable (show the query; distinguish empty from
  failed; no `2>/dev/null` on evidence).

## Non-goals

- The unassigned-primary PUSH lane (Bug 1) — a separate loop; push was deliberately deferred
  (push authority is managed-worktree-only, integration-lock + byte-equality proof).
- Implementing the deactivate fix — a follow-up loop once the root cause is proven.
- Changing the codex session's live professional-mode state or unblocking that session by mutation.
