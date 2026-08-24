# Codex deactivate-professional-mode deadlock — root-cause investigation Design

> **Created:** 2026-08-21
> **Status:** Design Complete
> **Scope mode:** selective (investigation-only; confirm the root cause, no fix until proven)

## Summary

In Codex session `019fc3b0-2757-7b22-b792-0011f9b29929` (working the `roleplaying-agents`
**primary checkout**, branch `main`), the operator invoked
`$ironclaude:deactivate-professional-mode` and it **did not take effect** — the agent called
`get_professional_mode` and got `{"professional_mode":"on"}`, then correctly reported
"⚠️ deactivation FAILED" (transcript
`~/.codex/sessions/2026/08/02/rollout-2026-08-02T12-15-29-019fc3b0-…jsonl`, line 44224, 21:51Z).
The UserPromptSubmit hook that owns the state transition did not set `professional_mode='off'`.

This is one half of a hard deadlock (the other is the commit-and-push-from-primary-checkout gap,
a **separate** loop): a Codex primary-checkout session with reviewed staged changes can neither
push (push authority is managed-worktree-only, deliberately) nor deactivate to escape. A broken
universal escape hatch violates the operator's standing "no operator intervention / no deadlocks"
directive. **This loop finds the root cause of the deactivate failure only.** No fix until proven;
the fix is a follow-up loop.

## Grounded facts (current source)

- `worker/hooks/state-activator.sh:137-178` is the deactivate handler. It detects the Claude
  slash forms (`:138`) and the **Codex markdown-link form** via
  `CODEX_DEACTIVATE_LINK_RE='^\[\$ironclaude:deactivate-professional-mode\]\(/[^)[:cntrl:]]*/skills/deactivate-professional-mode/SKILL\.md\)$'`
  (`:144`), then runs
  `UPDATE sessions SET professional_mode='off' … WHERE terminal_session='${SAFE_SESSION}'`
  against `DB_PATH` (`:161`/`:171`), logging a warning if it affects **0 rows** (`:166`/`:176`).
- The operator's exact invocation was
  `[$ironclaude:deactivate-professional-mode](/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.6+codex.20260821201512/skills/deactivate-professional-mode/SKILL.md)`
  — which the `:144` regex should match.
- `state-activator.sh` is wired in `worker/hooks/hooks.json` (the **Claude** UserPromptSubmit
  config). Codex fires hooks from its **own** configuration (`~/.codex/config.toml` and/or the
  codex plugin manifest), so whether Codex runs `state-activator.sh` at all is unverified.
- The codex session's ironclaude state cache
  (`~/.claude/ironclaude-state-cache-019fc3b0-…json`) shows `professional_mode:"on"`,
  `workflow_stage:"execution_complete"`.

## Hypotheses (the investigation decides which)

- **H-A — the hook never runs in Codex.** Codex's hook wiring doesn't include
  `state-activator.sh` on user-prompt submit, so nothing sets `professional_mode='off'`. Deactivate
  is then structurally impossible in Codex (the most severe: the escape hatch never worked there).
- **H-B — the hook runs but the UPDATE affects 0 rows.** A `terminal_session`/`SAFE_SESSION`
  mismatch (the codex session's row key differs from what the hook computes) or a `DB_PATH`
  mismatch means the UPDATE targets no row; `:166`/`:176` would have logged the 0-rows warning.
- **H-C — split-DB.** The hook writes `professional_mode='off'` to one database, but the Codex
  `state-manager` MCP reads `get_professional_mode` from a *different* database, so the write is
  invisible to the verifier.
- **H-D — regex/input mismatch.** The exact prompt Codex delivered to the hook differed from the
  `:144` regex (trailing whitespace, rendering, or a non-link plain `$ironclaude:…` form the
  `:148` exact check also should catch) so `DEACTIVATE_REQUEST` stayed false.

## Architecture

A strictly read-only investigation whose **execute stage** (which unblocks Bash/sqlite) answers
four evidence questions and writes a findings note. It changes no code, config, DB row, or
professional-mode state.

1. **Does Codex run `state-activator.sh` on prompt submit? (authoritative H-A probe.)** Inspect
   the Codex hook wiring (`~/.codex/config.toml`, the codex plugin manifest under
   `~/.codex/plugins/cache/ironclaude/ironclaude/1.1.6+codex.<buster>/`, and any codex hooks
   config) for a prompt-submit → `state-activator.sh` binding — this wiring check, NOT a
   `logs_2` absence, is the authoritative H-A evidence. **Strong corroboration (A1):**
   `~/.claude/ironclaude-state-cache-<SESSION_TAG>.json` is written ONLY by `state-activator.sh`
   (:214-220), conditional on the `sessions` SELECT (:192) returning a row for the `SESSION_TAG`
   key — so the existence of `ironclaude-state-cache-019fc3b0-….json` (it carries a `timestamp`
   field, :218) proves the hook ran in that codex session with the correct key at least once,
   nearly refuting strong H-A and the `SESSION_TAG='none'` H-B variant; compare its `timestamp`
   to 21:51Z (overwritten every prompt, so it proves wiring+key, not per-turn behaviour). Treat
   `logs_2.sqlite` presence/absence as corroborating only, and only after calibrating that the
   codex log captures ironclaude hook activity at all.
2. **Durable evidence of the deactivate branch (there is NO ironclaude hook-log file).**
   `log_hook`/`log_warning`/`log_error` (hook-logger.sh:117-148/167-174/152-163) emit
   `{"systemMessage":…}` JSON to **stdout** only; `~/.claude/ironclaude-errors.log` (:32) is the
   MCP-error sideband and never records `state-activator` decisions. Two real channels: (a) the
   **success** path writes a durable row — `db_audit_log "hook:state-activator"
   "professional_mode_off" …` (state-activator.sh:163/:173 → `audit_log` table in
   `~/.claude/ironclaude.db`); (b) the **0-rows warning** (:166/:176) is stdout-only, captured
   (if anywhere) by the codex transcript/`logs_2.sqlite` (item-4 domain). Note the success stdout
   line is verbose-gated (hook-logger.sh:122-129) so its absence proves nothing; `log_warning`
   is ungated.
3. **Which DB + session key? (H-B vs H-C.)** The hook's `DB_PATH` is hardcoded
   `$HOME/.claude/ironclaude.db` (hook-logger.sh:29) — so the hook side of H-C is fixed. The
   `state-manager` MCP resolves via `getDbPath()` (state-manager `src/db.ts:30-35`):
   `STATE_MANAGER_DB_PATH` env override, else the same `~/.claude/ironclaude.db`. So H-C reduces
   to one read-only check: is `STATE_MANAGER_DB_PATH` set for the codex MCP
   (`grep STATE_MANAGER_DB_PATH ~/.codex/config.toml` + the codex plugin MCP config)? For H-B,
   `SAFE_SESSION` derives from the payload `.session_id` (defaults `'none'`, hook-logger.sh:95-99);
   read the `sessions` row for the codex session (`SELECT terminal_session, professional_mode,
   updated_at FROM sessions WHERE terminal_session LIKE '%019fc3b0%'`) and compare its key to what
   the hook's UPDATE targeted.
4. **Prompt-bytes probe (H-D) — NOT the event name.** The deactivate branch is gated ONLY by the
   `:138` slash regex, the `:144` codex-link regex, and the `:148` exact
   `$ironclaude:deactivate-professional-mode` string, plus the empty-prompt `exit 0` (:38-40). The
   `hook_event_name=="UserPromptSubmit" && thread_source!="subagent"` gate at `:53` wraps ONLY the
   HUMAN_OPERATION intent block (`:53-72`) — it does NOT gate deactivation, so `hook_event_name`
   must not drive an H-D verdict. H-D = `.prompt` missing/empty OR the delivered bytes match none
   of `:138`/`:144`/`:148`. Independent of any codex hook-payload logging, extract the operator's
   message bytes from the rollout JSONL near line 44224 and test them against those three
   conditions locally (read-only).

Deliverable: a findings note naming the confirmed hypothesis with the evidence, plus the fix
direction (e.g., wire the hook into codex; fix the session-key/DB resolution; align the DBs).

## Components

- `docs/plans/2026-08-21-codex-deactivate-deadlock-findings.md` — the sole deliverable. `git add -f`.
- No product-code, config, or DB change. No tests (read-only diagnosis; values measured live).

## Data Flow

Human submits `$ironclaude:deactivate-professional-mode` in Codex → (if wired) `state-activator.sh`
runs, matches the codex link regex, UPDATEs `sessions.professional_mode='off'` for the session →
the Codex `state-manager` MCP `get_professional_mode` should read `off`. The break is somewhere on
that chain; the investigation reads each link (wiring, hook log, DBs, session key, prompt bytes)
and reports where it broke.

## Error Handling

- All commands read-only: `cat`, `grep`, `sqlite3 … SELECT`, `ls`, `find`. NO DB write, NO
  `set_professional_mode`, NO config edit. The operator's `professional_mode` state is not touched.
- `logs_2.sqlite` is large + WAL-live; use `sqlite3 -readonly` and bounded `SELECT`s by timestamp,
  never a full dump.
- Absence must be provable: if a hook-execution record is absent from the codex log, that ABSENCE
  (with the query shown) is the evidence for H-A — distinguish "no record" from "query failed".

## Testing Strategy

No unit tests — the deliverable is a findings note; every value is measured live in the execute
stage and pasted. Verification: the note names one confirmed hypothesis (H-A/B/C/D) with the exact
evidence for it and against the others, or states precisely which question could not be answered.

## Implementation Notes

- **No fix in this loop.** Once the root cause is proven, the fix (wire the codex hook / correct
  the session-key or DB resolution / align DBs) is a separate loop.
- **Bug 1 (unassigned-primary push lane) is a separate loop** — episodic memory confirms push was
  deliberately deferred (push authority is managed-worktree-only, integration-lock + byte-equality
  proof), so it is a real architecture change, not a quick add.
- Human commits, no push. Local only.
