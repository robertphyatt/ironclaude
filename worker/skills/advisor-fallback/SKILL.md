---
name: advisor-fallback
description: Client-aware tiered-up adversarial advisor (Claude subagent / fixed-function Codex broker), fired at natural discretionary advisor points
---

# Advisor Fallback (manual tiered-up advisor)

## Purpose

The reliable advisor is a **tiered-up adversarial reviewer** you invoke yourself — not the
`advisor` tool / `/advisor` slash command, which is flaky and, for Codex, does not exist. This skill
is the standard advisor path for **both** clients: at the moments you would normally consult an advisor,
dispatch a fresh, blind, one-tier-up reviewer over the work and weigh its findings.

This is the same role the `advisor` tool plays, delivered by a subagent (Claude) or the fixed-function
`run_codex_advisor_review` broker (Codex) so it works regardless of which client you are.

## When to fire it (discretionary — NOT every time)

Fire the advisor at the **same natural, judgment-based points a normal advisor call would happen** — it
does NOT fire on every task or every review gate:

- Before committing to an approach or interpretation (before substantive work).
- When stuck — recurring errors, an approach not converging, results that don't fit.
- When you believe the work is complete, before declaring it done.

Skip it for trivial mechanical steps. This is your judgment, exactly as a normal advisor is consulted by
judgment, not on a fixed cadence.

## How to invoke it — pick your client's branch

Resolve the **one-tier-up** reviewer model from your OWN client's ladder, then run that branch. Both
branches are report-only: the reviewer identifies problems with evidence; it does not rewrite the work.

### If you are a Claude session

Dispatch a fresh, blind subagent with the `Agent` tool (`subagent_type="general-purpose"`,
`model=<one-tier-up>`), giving it the task, the change/decision, the evidence, and the specific questions
to pressure-test. Have it follow the `ironclaude:adversarial-review` methodology (orientation → deep read
→ verify every finding with grep/read evidence, drop the unverified → severity-classified, report-only).

Before dispatch, read `worker/skills/write-lossless-ai-messages/SKILL.md`. Prepend its exact complete
skill content to the reviewer prompt, ahead of task, evidence, and questions. The programmatic load does
not activate an interactive worker skill: do not include `IC_LOSSLESS_AI_MESSAGES_ACTIVE` in the prompt or
report. Keep the report natural-language and lossless; this grants no new tools.

**Claude tier ladder (one tier up):** `sonnet → opus`, `opus → fable`, `fable → (top tier — skip; no
higher advisor)`. If the resolved model is `fable` and Fable is unavailable, use `opus` (read the flag at
`IRONCLAUDE_FABLE_STATE_PATH` if set else `~/.ironclaude/state/fable_unavailable.json`; on any read error
treat Fable as available). This is the existing behavior — `model=fable`, else `model=opus`.

### If you are a Codex session

Call `run_codex_advisor_review` with exactly three fields:

- `requester_model`: your current full Codex model name: `gpt-5.6-luna`, `gpt-5.6-terra`,
  `gpt-5.6-sol`, or `gpt-6-astra`. Do not pre-map it; the broker rejects any mismatch with
  provider-authenticated `x-codex-turn-metadata.model`, then maps the reviewer exactly once.
- `packet`: the complete inline, lossless review packet. Include the adversarial-review instruction,
  task and decision, operator constraints, evidence, specific questions, and every code/diff or artifact
  byte needed to review without repository reads. State whether blindness is required, and exclude prior
  findings, verdicts, repair coaching, reviewer identities, and revision history when it is.
- `review_tier: "one-up"` for normal advisor work. The selector is bounded to
  `"same"` or `"one-up"`; omission defaults to `"one-up"` only for compatibility.

Before constructing `packet`, read `worker/skills/write-lossless-ai-messages/SKILL.md` and prepend its
exact complete skill content ahead of the adversarial-review instruction and inline evidence. Programmatic
loading must not add `IC_LOSSLESS_AI_MESSAGES_ACTIVE` to the reviewer prompt or output. This grants no new
tools. Ask the reviewer to follow `ironclaude:adversarial-review`: verify findings against supplied evidence,
report only, and classify severity.

Do not run nested `codex exec` from the main agent for normal advisor work. Do not ask the operator to
approve the broker or its fixed read-only review again: IronClaude's broker is the approved workflow
surface. If the broker fails, report its bounded error and fail closed rather than bypassing it.

**Codex tier ladder (broker-owned, one mapping only):** `luna (haiku) → terra (sonnet) → sol (opus) → astra (fable)`
(`gpt-5.6-luna → gpt-5.6-terra → gpt-5.6-sol → gpt-6-astra`). The broker maps
**`gpt-5.6-sol` → `gpt-6-astra`**. At the Astra ceiling it runs
**`gpt-6-astra` → `gpt-6-astra`**, a same-tier blind pass. The reviewer stays a Codex model;
do not cross to a Claude model. Astra remains a Codex model and does not satisfy an actual Claude Fable request.

## Codex broker guarantees

The broker owns executable discovery, companion-helper preflight, fixed read-only argv, private cwd,
sanitized environment, model mapping, bounded timeout/output, JSONL parsing, and cleanup. Callers cannot
supply shell, argv, cwd, environment, or repository paths. Complete inline packets keep the reviewer
independent of repository access and avoid creating a host-security approval question in the main task.

## Weigh, don't obey

Treat the reviewer's findings as an adversary's evidence, not authority. Verify each material finding
against the current source; reconcile conflicts with evidence. "No advisor available" never means
"proceed unreviewed" — it means run this manual reviewer instead.
