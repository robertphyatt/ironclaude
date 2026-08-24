# Professional-Mode Invariant Foundation (Loop 1) — Requirements (operator-approved)

> **Created:** 2026-08-19
> Loop 1 of the operator-approved post-revert rebuild (baseline f07e3e7). Design:
> docs/plans/2026-08-19-pm-invariant-foundation-design.md. Scope = HOLD.
> Operator decisions this loop: **Approach A** (direct fixes only — NO epoch table, NO
> trigger) and **G3 = keep** the two agent-restraint guards as named exceptions.

## Binding requirements

R1 — **Delete the Commander startup off-overwrite.** `orchestrator_mcp.py`
`_init_brain_session_background` must keep only `INSERT OR IGNORE` (create the session row
if absent) and MUST NOT write `professional_mode` on an existing session row. The
unconditional `UPDATE sessions SET professional_mode='off' ...` (~:451) and its audit write
are removed; docstring (~:407-412) and log (~:471) updated to match.

R2 — **G1: PM-off worktree adapter is advisory, never blocking.** In
`professional-mode-guard.sh`, when `professional_mode='off'` and the session owns a managed
assignment, path redirection into the owner's worktree still runs (isolation preserved),
but an adapter failure WARNS and ALLOWS the write — it MUST NOT block. PM-off never blocks
an operator write.

R3 — **G2: the subagent circuit-breaker no-ops when PM is not on.**
`subagent-circuit-breaker.sh` must early-exit (allow) unless `professional_mode='on'`, so a
tripped breaker does not survive deactivation.

R4 — **G3: keep the two named exceptions.** The hooks-config anti-tamper block and the
Commander-only private-transport block stay active regardless of PM state (no behavior
change) and are documented explicitly as the two named carve-outs to Invariant B. They
restrain the agent only, never the operator.

R5 — **Salvage the PM-off operator-authority contract text (text only).** Lift the contract
language from the reverted `2026-08-14-professional-mode-off-operator-authority` docs into
the deactivate-professional-mode skill's `off` outcome and the behavioral surface: with PM
exactly `off`, explicit operator instructions (including rendered IronClaude git forms) are
direct requests, run with zero workflow/review/worktree/staging/commit/push/intent
protocols and no redundant confirmation; exact `on` retains those controls; every other
state fails closed. NO machinery.

R6 — **Invariant A (absolute):** the operator can ALWAYS turn off professional mode. Nothing
may be added to the deactivation path; deactivation stays the baseline's dependency-free
guarded SQL update. Proven for the active-task and idle branches, and by showing the
removed Commander overwrite no longer flips an executing session off.

R7 — **Invariant B:** with `professional_mode='off'`, ZERO enforcement anywhere except the
two named G3 exceptions. Proven by a PM-off enforcement sweep across the hooks/guards.

R8 — **No regression.** Full state-manager (vitest), hook guard, and commander (pytest)
suites green. Nothing that works today is rebuilt; no schema change, no trigger, no new MCP
tool.

## Global constraints
Minimal + additive on baseline f07e3e7. Commander Python + hooks + one skill/behavioral-text
change only. Zero operator git/worktree commands in normal operation. The PM kill-switch is
absolute. Line anchors are from a source read and must be re-verified live during
writing-plans.
