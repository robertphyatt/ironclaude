# Opus-Tier Unpin — Resolve Naturally (retire the `claude-opus-4-8` pin)

> **Created:** 2026-09-24
> **Status:** Design Complete

## Summary

Every internal Opus-tier model string is currently hard-pinned to the literal
`claude-opus-4-8`. That pin was deliberate (README:288-290, CHANGELOG:457-458):
the bare `opus` alias used to auto-resolve to **Opus 5**, which was too
unreliable to drive IronClaude's brainstorm→plan→execute workflow, so every
Opus-tier string was replaced with the explicit 4.8 id. Auto-resolution now
lands on **Opus 5.5** (operator-confirmed), which no longer has that problem, so
the premise for the pin is obsolete.

This change removes the pin: every `claude-opus-4-8` literal becomes the bare
`opus` alias, so the Opus tier resolves naturally to whatever the Claude CLI
serves as current-latest Opus — exactly as the `sonnet`, `haiku`, and `fable`
tiers already do (only `opus` was ever pinned). No explicit version id is
introduced anywhere; the whole point is that the code stops naming a version.

Scope is deliberately narrow (operator: "just find all the spots where we pin
4.8 and let it resolve naturally"): a mechanical alias substitution plus the
test/config/doc updates it forces. It does **not** re-architect model selection.

## Architecture

One-line conceptual change: the Opus tier joins the other three tiers in using a
bare provider alias instead of a pinned version id.

`provider_config.EXPECTED_MODELS["claude"]` is the single source of truth the
config validator enforces (`parse_provider_config` requires
`config.providers.clients.claude.models == EXPECTED_MODELS["claude"]` exactly).
Changing the validator entry `opus: "claude-opus-4-8"` → `opus: "opus"` therefore
forces the same change in every config file that declares a `claude.models`
block, and in every test that constructs or asserts that map. All other pin
sites are independent default literals scattered across the daemon/orchestrator
that each individually fall back to `claude-opus-4-8`; each becomes `"opus"`.

## Components (the complete pin surface)

**Source (7 files):**
- `commander/src/ironclaude/provider_config.py:21` — `EXPECTED_MODELS["claude"]["opus"]` (validator SoT; the `codex` opus entry `gpt-5.6-sol` is unrelated and untouched).
- `commander/src/ironclaude/config.py:52,53,59,60,71` — `default_opus_model`, `grader_model`, `advisor.advisor_model`, `advisor.advisor_models` map values, `providers.clients.claude.models.opus`.
- `commander/src/ironclaude/main.py:3745,3813,5521` — `default_opus_model`, `advisor_model`, `brain_model` `.get(...)` fallbacks.
- `commander/src/ironclaude/orchestrator_mcp.py:475,535,4818,5183,8044,8045` — `OrchestratorTools.__init__` `grader_model`/`opus_model` params, `_advisor_model_for` scalar fallback, two `notifications` `redirected_to=` literals, factory `cfg.get(...)` fallbacks.
- `commander/src/ironclaude/fable_availability.py:316,323` — the Fable-unavailable redirect target (fable → `claude-opus-4-8`) becomes fable → `opus`.
- `commander/src/ironclaude/notifications.py:424` — `format_fable_unavailable(redirected_to="claude-opus-4-8")` default.
- `commander/src/ironclaude/brain_client.py:867,879` — model-unavailable fallback literals (`resolved = "claude-opus-4-8"`).

**Config (2 files):**
- `config/ironclaude.json.example:24,25,30,40` — `brain_model`, `grader_model`, `advisor.advisor_model`, `providers.clients.claude.models.opus`.
- `commander/config/ironclaude.json:17` — `advisor.advisor_model` (the only opus pin in the live config; it has no `claude.models` block, so the validator's claude-opus entry is not exercised there).

**Docs:**
- `README.md:288-290` — rewrite the now-false rationale ("pinned to 4.8 because Opus 5 is too ADHD") to describe natural resolution and why it's now safe (5.5).
- `CHANGELOG.md` — add a new entry for this change. **Leave the historical `457-458` entry intact** (it accurately records what happened then; we do not rewrite history).

**Tests (~2 dozen assertions):** `test_config.py`, `test_brain_client.py`,
`test_orchestrator_mcp.py`, `test_grader_routing.py`, `test_grader_router.py`,
`test_worker_adapter.py`, `test_fable_availability.py`, `test_main_validate.py`,
`conftest.py` — every assertion or fixture that hard-codes `claude-opus-4-8`
updates to `opus` (including the grader-command assertion that currently expects
`--model claude-opus-4-8[1m]`, which becomes `--model opus[1m]`).

## Data Flow

Config/default (`opus`) → `provider_config.model_for(...)` / the daemon defaults
→ the resolved model string → for the Brain and grader, `brain_client` /
grader-router optionally appends the `[1m]` suffix + `context-1m-2025-08-07` beta
→ `claude --model <string>` spawns the subprocess, and the CLI resolves the bare
`opus` alias to current-latest Opus. The Fable-unavailable path
(`fable_availability.resolve_advisor_model`) now redirects a `fable` request to
`opus` instead of `claude-opus-4-8`.

## Error Handling — the `[1m]` interaction (the one real risk)

`brain_client.py:120` `_MODELS_NEEDING_1M_BETA = ("opus",)` and
`_model_needs_1m_beta()` match on the substring token `"opus"`, so a bare `opus`
string still gets the `[1m]` suffix + 1M beta. This is **correct while natural
resolution yields a model that needs the beta** — today's `opus` resolves to 4.8
(verified in-session: a subagent spawned with model=opus reported
`claude-opus-4-8`), which requires it. The danger: if natural resolution is a
native-1M Opus that *rejects* the beta (the way Fable 5 / Sonnet 5 do — the
documented "issue with the selected model (fable[1m])" Brain crash at
`brain_client.py:74-75`), leaving `opus` in that set would crash the Brain.

Decision (scope-minimal): **do not change `_MODELS_NEEDING_1M_BETA` in this
change.** It is not a 4.8 pin, and touching it now would either be speculative
(we have not observed 5.5's beta behavior) or would break 4.8, which still needs
the beta. Instead:
- Document the future trigger: when natural `opus` resolution becomes a
  native-1M Opus that rejects `[1m]`, a follow-up must drop `opus` from
  `_MODELS_NEEDING_1M_BETA` (or gate it on the resolved concrete version).
- Add a **pre-deploy verification** (below) that `claude --model opus` accepts
  the `[1m]` suffix on this box, so we cannot ship a Brain that crash-loops.

The Fable-unavailable redirect target changing from `claude-opus-4-8` to `opus`
is behavior-preserving today (both resolve to a workflow-capable Opus) and picks
up the same natural resolution as everything else.

## Testing Strategy

- **pytest, per touched module:** update every `claude-opus-4-8` assertion/fixture
  to `opus`. Falsifiable: each test still pins an exact expected string, so a
  wrong substitution (a stray leftover literal, or an unintended tier) fails.
- **`provider_config` validator:** a test that a config whose
  `claude.models.opus == "opus"` parses, and one whose value diverges from
  `EXPECTED_MODELS` still raises `ProviderConfigError` (the lockstep guard is not
  loosened — negative case proves the widening did not go further than intended).
- **grader routing:** assert the grader command is `--model opus[1m]` (was
  `--model claude-opus-4-8[1m]`) — proves the `[1m]` path is preserved on the
  bare alias.
- **brain_client fallback:** assert the unavailable-fallback resolves to `opus`.
- **Full suites:** commander pytest green; workspace-manager vitest untouched by
  this change (no TS pin sites) but run for safety.

## Implementation Notes

- **No explicit version id anywhere.** If a reviewer proposes pinning
  `claude-opus-5-5`, that contradicts the operator directive ("let it resolve
  naturally") — reject it. The code must name only the `opus` alias.
- **Validator lockstep is load-bearing:** `EXPECTED_MODELS`, `config.py`
  DEFAULTS, `config/ironclaude.json.example`, and the test fixtures that build
  the models map must all read `opus` together, or `parse_provider_config`
  rejects the config at startup. The live `commander/config/ironclaude.json` has
  no `claude.models` block, so its only edit is `advisor_model:17`.
- **Historical CHANGELOG entry (457-458) stays.** Add a new entry; do not rewrite
  the record of the original pin.
- **Pre-deploy verification (operator-gated, part of the deploy, not the code):**
  before restarting the Commander onto this change, confirm on the daemon's box
  that `claude --model opus -p ...` resolves and that `claude --model 'opus[1m]'`
  is accepted (no "issue with the selected model"). If the resolved Opus rejects
  `[1m]`, do the `_MODELS_NEEDING_1M_BETA` follow-up BEFORE deploying the Brain.
  (Run this probe directly, not via a subagent — a Bash-running subagent under
  professional mode drives the shared state machine.)
- **Deploy:** Commander-daemon-side change (config defaults + daemon/orchestrator
  literals). A Commander restart picks it up; the Brain then launches on
  natural-resolution Opus. No workspace-manager `dist/` rebuild (no TS touched).
- This is loop 1 of two; Feature B (orphan prevention) is a separate later loop.
