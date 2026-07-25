---
name: advisor-fallback
description: Client-aware manual tiered-up adversarial advisor (claude subagent / codex exec), fired at the natural discretionary advisor points
---

# Advisor Fallback (manual tiered-up advisor)

## Purpose

The reliable advisor is a **manual tiered-up adversarial reviewer** you invoke yourself — not the
`advisor` tool / `/advisor` slash command, which is flakey and, for codex, does not exist. This skill
is the standard advisor path for **both** clients: at the moments you would normally consult an advisor,
dispatch a fresh, blind, one-tier-up reviewer over the work and weigh its findings.

This is the same role the `advisor` tool plays, delivered by a subagent (claude) or a `codex exec`
review (codex) so it works regardless of which client you are.

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

**Claude tier ladder (one tier up):** `sonnet → opus`, `opus → fable`, `fable → (top tier — skip; no
higher advisor)`. If the resolved model is `fable` and Fable is unavailable, use `opus` (read the flag at
`IRONCLAUDE_FABLE_STATE_PATH` if set else `~/.ironclaude/state/fable_unavailable.json`; on any read error
treat Fable as available). This is the existing behavior — `model=fable`, else `model=opus`.

### If you are a Codex session

Run a one-tier-up `codex exec` adversarial review over the work. **Pass the code/diff to review INLINE in
the prompt** — do not rely on the reviewer grepping the repo (see the gotchas). Command
(live-validated 2026-07-22 — the grader `codex exec` family minus `--output-schema`):

```bash
codex exec --json --ephemeral --skip-git-repo-check -s read-only -m <ONE_TIER_UP_MODEL> -
```

- Deliver the adversarial-review instruction + the code/diff under review on **stdin** (trailing `-`).
- Read the findings from the **last** `item.completed` event whose `item.type == "agent_message"`, field
  `.text` (the model may emit intermediate narration; take the last agent_message). Without `--json` the
  response text is printed directly — either form works; `--json` + last-`agent_message` is the reliable
  parse.
- Auth is your ChatGPT subscription (`codex login status` → "Logged in using ChatGPT") — never an API key.
- Ask the reviewer to follow the `ironclaude:adversarial-review` standard (verify findings with evidence,
  report-only, severity-classified).

**Codex tier ladder (one tier up):** `luna (haiku) → terra (sonnet) → sol (opus)`. Codex has **no `fable`
tier**, so at the ceiling **`sol → sol`: run a same-tier BLIND pass** (a fresh blind review at the same
model still catches defects — most advisor value is blindness, not tier). The reviewer model stays a
codex model — codex is its own advisor peer; do not cross to a claude model.

## Codex gotchas (from the 2026-07-22 live probe)

- **The ephemeral `codex exec` reviewer fires the IronClaude hooks and lands in professional-mode
  `undecided`, so its Bash is guard-blocked.** This is why you pass the review content INLINE: the
  reviewer reasons over what you give it (and can still do non-bash file reads). Report-only review of
  inline content is unaffected.
- **Session litter:** `--ephemeral` is codex-session-ephemeral, not IronClaude-session-ephemeral. Each
  invocation leaves an `undecided`/`idle` row in `~/.claude/ironclaude.db` (`sessions`) and an
  `~/.claude/ironclaude-session-<pid>.id` file. These are inert to active workflows; be aware they
  accumulate (a periodic undecided/idle sweep is a reasonable follow-up). The same applies to the codex
  grader.

## Weigh, don't obey

Treat the reviewer's findings as an adversary's evidence, not authority. Verify each material finding
against the current source; reconcile conflicts with evidence. "No advisor available" never means
"proceed unreviewed" — it means run this manual reviewer instead.
