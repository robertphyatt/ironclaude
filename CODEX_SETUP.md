# Running Codex as an IronClaude Peer

IronClaude can run OpenAI's `codex` alongside Claude Code — as **workers** and **grader** (full
peers), and as the **Brain** in its current workflow/memory form.

> **Status (v1.1.7):** Codex is a worker / grader peer. The Codex **Brain** runs the
> brainstorm → plan → execute workflow, episodic memory, worker orchestration, and
> fixed-function brokered advisor reviews. Treat codex-as-Brain as experimental and
> enable it in trusted contexts.

## Prerequisites

- The `codex` CLI installed and signed in (`codex` reachable on your `PATH`).
- A ChatGPT subscription used **through the codex CLI only**. Do **not** configure an OpenAI API
  key, LiteLLM, or OpenRouter — IronClaude drives Codex exclusively via the `codex` CLI /
  `codex app-server`.

## Install the Codex plugin

From an IronClaude source checkout, repair and verify Codex's launcher companion
before installing or updating the plugin:

```bash
codex plugin marketplace add robertphyatt/ironclaude
make codex-plugin-install
```

The preflight is idempotent and fail-closed. It creates only a missing equivalent
`codex-code-mode-host` companion symlink and refuses to overwrite a file, directory,
FIFO, dangling link, or link to different bytes. IronClaude self-updates run the
same repair before cachebusting, building, reinstalling, and restarting Codex.

## Model tiers

IronClaude maps its tier names to codex models
(`config/ironclaude.json` → `providers.clients.codex.models`):

| Tier   | Codex model     |
|--------|-----------------|
| haiku  | gpt-5.6-luna    |
| sonnet | gpt-5.6-terra   |
| opus   | gpt-5.6-sol     |
| fable  | gpt-6-astra     |

`fable` / `gpt-6-astra` is the hardest Codex tier, not the default. Select it only
when lower tiers cannot reliably handle the assigned work.

## Enable Codex for workers / grader

In `config/ironclaude.json` (copy it from `config/ironclaude.json.example` if you haven't yet):

1. Enable the codex client: set `providers.clients.codex.enabled` to `true`.
2. Add `"codex"` to the `clients` list of each role you want to run on codex (optionally make it
   `preferred`):

```json
"providers": {
  "roles": {
    "worker":  {"preferred": "claude", "clients": ["claude", "codex"]},
    "grader":  {"preferred": "claude", "clients": ["claude", "codex"]}
  }
}
```

IronClaude's provider router then routes that role to codex, falling back to Claude if a codex
capability is unavailable.

## Run the Codex Brain

The Brain is selected by an environment variable (not the role config):

```bash
BRAIN_CLIENT=codex
```

**Model:** the codex Brain runs the codex model mapped from your `brain_model` tier —
`sonnet → gpt-5.6-terra`, `opus → gpt-5.6-sol`, `haiku → gpt-5.6-luna`, and
`fable → gpt-6-astra`. Note that
`config/ironclaude.json.example` ships `brain_model: "opus"` (→ `gpt-5.6-sol`). To run the
recommended **gpt-5.6-terra**, set `brain_model: "sonnet"` in your config, or override with
`BRAIN_MODEL=gpt-5.6-terra` (a literal codex model passes through unchanged).

**Security posture:** the codex Brain's `codex app-server` is launched read-only
(`sandbox_mode="read-only"`) with `approval_policy="on-request"`, and every command-execution
approval is gated to a git-command allowlist (file mutations are declined). This mirrors the Claude
Brain's read-and-delegate posture — the Brain never mutates state directly.

**What the Codex Brain does today:** runs the full brainstorm → plan → execute workflow (via the
state-manager), searches episodic memory, orchestrates workers, and calls the fixed-function
`run_codex_advisor_review` broker for normal read-only advisor work. Normal advisor calls pass
`packet`, authenticated `requester_model`, and `review_tier: "one-up"`; omission is compatibility-only.
The broker owns model mapping
and execution; the main agent does not spawn nested Codex CLI processes or request repeated host
approval for that workflow review.

For brokered advisor reviews, the Codex ladder is Luna → Terra → Sol → Astra:
Sol maps to Astra, and Astra is the ceiling, so Astra receives a fresh same-tier
Astra review. This Codex tier must not be described as an actual Claude Fable
subagent.

## Known limitations / roadmap (v1.1.7)

- Continued Codex-Brain hardening and provider-parity validation.
- Cross-provider failover (auto-switch a role to the other provider on a provider outage).
- Remote codex over SSH.

## Troubleshooting

- **Brain won't start / model error:** confirm `codex` is signed in and the model name is valid;
  watch the Brain's output / operator messages for `[CODEX BRAIN ERROR]`.
- **A codex role isn't being used:** confirm `providers.clients.codex.enabled` is `true` and the
  role's `clients` list includes `"codex"`.
- **Auth issues:** do **not** add an OpenAI API key to work around them — codex CLI sign-in is the
  only supported auth path.
