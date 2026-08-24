# Professional-Mode Invariant Foundation (Loop 1) — Design

> **Created:** 2026-08-19
> **Status:** Design Complete
> **Scope:** hold (minimal, additive on baseline f07e3e7)

## Summary

Loop 1 of the post-revert rebuild. The revert to `f07e3e7` already fixed behavior #2
(deactivation is one guarded local `sqlite3 UPDATE` in `state-activator.sh` with zero
external dependencies) and deleted the ~20k-line authority/epoch/receipt machinery that
caused behavior #1's deadlocks. This loop closes the residue on the baseline: the single
illegitimate `professional_mode='off'` writer, and the two places where enforcement still
leaks through when professional mode is off. It is **direct fixes only** — no new tables,
no SQLite trigger — so nothing new is ever placed between the operator's deactivation
command and `professional_mode='off'`.

## Architecture

**Chosen approach: direct fixes, no epoch/trigger (operator-approved).** An earlier
proposal added an `authority_epochs` table + a trigger that rejects any off-write while an
open epoch exists, to make "no off-write without closing an epoch" structural. Evidence
against it, verified in source: after deleting the one illegitimate off-writer
(`_init_brain_session_background`), the *only* remaining `professional_mode='off'` writer
on the baseline is the legitimate deactivation hook (`_set_pm_via_sqlite` writes only
`'on'`, via `_activate_pm_via_sqlite`). The trigger's protected target set is therefore
empty — it would defend only against a hypothetical future writer (speculative defense,
which the operator's "simplest thing that works" yardstick cuts). It would also place a
new (provably-passing but non-zero-surface) element in the deactivation path, cutting
against the operator's absolute "the kill-switch can never be blocked." The epoch+trigger
remains a clean additive follow-up if a second illegitimate off-writer ever appears.

Two invariants govern the loop:
- **Invariant A** — the operator can ALWAYS turn off professional mode. No mechanism may
  ever stand between a human deactivation and `professional_mode='off'`. This loop
  preserves the baseline's dependency-free deactivation and adds nothing to that path.
- **Invariant B** — when `professional_mode='off'`, ZERO enforcement happens, except two
  explicitly-named agent-self-protection carve-outs (G3).

## Components

1. **Delete the Commander startup overwrite.** `commander/src/ironclaude/orchestrator_mcp.py`
   `_init_brain_session_background` currently does `INSERT OR IGNORE INTO sessions (...
   'off')` then an unconditional `UPDATE sessions SET professional_mode='off' ...` (~:451).
   Delete the `UPDATE` (and its audit write); keep only `INSERT OR IGNORE` so a Brain
   session row is created when absent and an existing session's professional_mode is never
   clobbered. Update the function docstring (~:407-412) and the success log line (~:471) to
   state that startup creates-if-absent and never writes professional_mode on an existing
   row.

2. **G1 — worktree adapter advisory when PM is off.** `worker/hooks/professional-mode-guard.sh`
   (~:401-424): when `professional_mode='off'` and the session owns a managed assignment,
   the off-branch still runs the workspace-path adapter and BLOCKS the write on an adapter
   failure. Fix: keep the path redirection (writes still land in the owner's worktree, so
   concurrent-agent isolation is preserved — worktree isolation is intentionally decoupled
   from PM), but on an adapter failure WARN and allow the write instead of blocking. PM-off
   must never block an operator write.

3. **G2 — gate the subagent circuit-breaker on PM.** `worker/hooks/subagent-circuit-breaker.sh`
   blocks Agent dispatch with no professional-mode check, so a tripped breaker survives
   deactivation. Fix: early-exit (no-op) unless `professional_mode='on'`.

4. **G3 — keep two guards as named exceptions.** The hooks-config anti-tamper block and the
   Commander-only private-transport block in `professional-mode-guard.sh` stay active
   regardless of PM state (operator decision). No behavior change; document them explicitly
   as the two named carve-outs to Invariant B. They restrain the agent (editing its own
   guardrail config, reaching Commander's private transport), never the operator.

5. **Salvage the PM-off operator-authority contract text.** From the reverted
   `docs/plans/2026-08-14-professional-mode-off-operator-authority-*` docs, lift only the
   contract *language* — with PM exactly `off`, the agent treats explicit operator
   instructions (including rendered IronClaude git forms) as direct requests and runs raw
   git without workflow/review/worktree/staging/commit/push/intent protocols and without a
   redundant confirmation; exact `on` retains those controls; every other state fails
   closed — into the deactivate-professional-mode skill's "off" outcome and the behavioral
   surface. Text only, no machinery.

## Data Flow

Deactivation (unchanged from baseline): trusted `UserPromptSubmit` → `state-activator.sh`
recognizes the human command → one guarded `sqlite3 UPDATE` sets `professional_mode='off'`
(active-task branch preserves `workflow_stage`; idle branch resets it) with `changes()`
verification. No new step is added. Commander startup: `_init_brain_session_background`
creates the session row if absent and writes nothing to `professional_mode` on an existing
row. Enforcement with PM off: every guard/gate reads PM state and no-ops (G1 adapter
advisory, G2 breaker early-exit), except the two G3 carve-outs.

## Error Handling

Invariant A is preserved by *removing* surface, not adding it: the deactivation path keeps
its baseline `changes()`-verified single UPDATE with no plugin-root/node/lease/trigger
dependency. G1's advisory path fails toward *allowing* the operator's write (warn, never
block). Deleting the Commander overwrite is fail-safe: absent the UPDATE, an existing
session simply retains its own professional_mode.

## Testing Strategy

- **Invariant A proof suite:** deactivation reaches `off` in both the active-task branch
  (workflow_stage preserved) and the idle branch (workflow_stage reset); and a simulated
  `_init_brain_session_background` run against an existing `on`/executing session leaves
  its professional_mode unchanged (proves the overwrite is gone). Each test fails if the
  deleted UPDATE is restored or the deactivation UPDATE is gated.
- **Invariant B enforcement sweep:** with `professional_mode='off'`, assert the guard's
  write/git/stage branches allow-all, G1 does not block on adapter failure, and G2's
  circuit-breaker no-ops — while the two G3 carve-outs still block the agent. Falsifiable:
  each check fails if its guard enforces while off.
- **No regression:** full `state-manager` (vitest), hook (shell guard suites), and
  `commander` (pytest) suites green; nothing that works today is rebuilt.

## Implementation Notes

Commander Python + hooks + one skill/behavioral-text change; NO state-manager schema
change, NO trigger, NO new MCP tool. Standing constraints: zero operator git/worktree
commands in normal operation; the PM kill-switch is absolute (Invariant A); PM-off ⇒ zero
enforcement except the two named G3 exceptions (Invariant B); no regression; minimal and
additive. Line anchors here are from a source read and MUST be re-verified against live
source during writing-plans (grep, don't trust the numbers). Full context in memory
`project_foundation_revert_rebuild`.
