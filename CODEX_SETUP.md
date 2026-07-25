# Running Codex as an IronClaude Peer

IronClaude can run OpenAI's `codex` alongside Claude Code — as **workers** and **grader** (full
peers), and as the **Brain** in its current workflow/memory form.

> **Status (v1.1.0):** Codex is a worker / grader peer. The codex **Brain** runs the
> brainstorm → plan → execute workflow and episodic memory, but **cannot yet orchestrate workers**
> (spawn/manage) — worker-orchestration, Brain tool-gating, and Codex advisor wiring land in v1.1.1.
> Treat codex-as-Brain as experimental and enable it in trusted contexts.

## Prerequisites

- The `codex` CLI installed and signed in (`codex` reachable on your `PATH`).
- A ChatGPT subscription used **through the codex CLI only**. Do **not** configure an OpenAI API
  key, LiteLLM, or OpenRouter — IronClaude drives Codex exclusively via the `codex` CLI /
  `codex app-server`.

## Model tiers

IronClaude maps its tier names to codex models
(`config/ironclaude.json` → `providers.clients.codex.models`):

| Tier   | Codex model     |
|--------|-----------------|
| haiku  | gpt-5.6-luna    |
| sonnet | gpt-5.6-terra   |
| opus   | gpt-5.6-sol     |

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
`sonnet → gpt-5.6-terra`, `opus → gpt-5.6-sol`, `haiku → gpt-5.6-luna`. Note that
`config/ironclaude.json.example` ships `brain_model: "opus"` (→ `gpt-5.6-sol`). To run the
recommended **gpt-5.6-terra**, set `brain_model: "sonnet"` in your config, or override with
`BRAIN_MODEL=gpt-5.6-terra` (a literal codex model passes through unchanged).

**Security posture:** the codex Brain's `codex app-server` is launched read-only
(`sandbox_mode="read-only"`) with `approval_policy="on-request"`, and every command-execution
approval is gated to a git-command allowlist (file mutations are declined). This mirrors the Claude
Brain's read-and-delegate posture — the Brain never mutates state directly.

**What the codex Brain does today:** runs the full brainstorm → plan → execute workflow (via the
state-manager) and searches episodic memory.

**What it can't do yet (v1.1.1):** spawn or manage workers — the orchestrator MCP server is not
wired for the codex Brain.

## Known limitations / roadmap (v1.1.1)

- Codex-Brain worker orchestration (wiring the orchestrator MCP into the codex app-server), Brain
  tool-gating (GATED_TOOLS parity), and Codex advisor wiring (Commander codex workers currently
  spawn advisor-less).
- Cross-provider failover (auto-switch a role to the other provider on a provider outage).
- Remote codex over SSH.

## Troubleshooting

- **Brain won't start / model error:** confirm `codex` is signed in and the model name is valid;
  watch the Brain's output / operator messages for `[CODEX BRAIN ERROR]`.
- **A codex role isn't being used:** confirm `providers.clients.codex.enabled` is `true` and the
  role's `clients` list includes `"codex"`.
- **Auth issues:** do **not** add an OpenAI API key to work around them — codex CLI sign-in is the
  only supported auth path.
