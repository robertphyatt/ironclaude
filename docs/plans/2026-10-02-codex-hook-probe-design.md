# Codex Hook Probe Design (v1.1.16 L2a)

> **Created:** 2026-10-02
> **Status:** Design Complete
> **Roadmap:** `docs/plans/2026-10-02-v1-1-16-roadmap.md`. This is L2a of L2 (client parity).
> **Loop:** light. It is a read-only investigation plus one headless Codex turn in a throwaway scratch repo. It touches no repo source, hook code or global config, and its only written artifact is the findings note. The scratch repo lives in the session scratchpad, outside the repo.

## Requirements

The operator chose "Probe first, then fix", then chose the "Headless probe" option.

**What L2b depends on.** L2b (the fixes) is designed on what this probe finds. It must answer:

- **Q1.** Do IronClaude's plugin hooks run in Codex today? For each event that fires (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop), what is the evidence?
  - The Codex docs say plugin hooks are skipped "until you review and trust the current hook definition". Do the trust entries in `~/.codex/config.toml` (`trusted_hash`, `enabled`) match the current `hooks.json` definitions, or did each release silently untrust them?
- **Q2.** What `tool_name` does Codex send to PreToolUse hooks for shell commands and for file edits? The docs say `Bash` and `apply_patch`, and today's rollouts name the shell tool `exec`.
- **Q3.** What fields does the Codex Stop hook input carry? In particular, is `last_assistant_message` present? Does Claude Code's Stop input carry it too? If both do, one parsing path serves both clients.
- **Q4.** Is the `systemMessage` returned by a Codex Stop hook shown to the session? The docs say it is honored.

**Constraints:**
- No push, no commits, no repo source edits.
- No permanent changes to `~/.codex/config.toml` or to IronClaude's global hook config.
- Any instrumentation must be scoped to the single probe invocation, for example a `-c` config override or environment variable on that one `codex exec`, and must leave nothing behind.
- If a question cannot be answered without a global change, record it as unanswered, with the reason. Do not make the change.
- Runs use the ChatGPT subscription through the Codex CLI only.
- Kill no processes.

## Design

One task:
1. **Evidence from history.** Grep Codex rollouts from September and October for IronClaude hook output: `hook_prompt` blocks, session-state injections and hook-run identifiers. Record the last plugin version seen per event.
2. **Trust state.** Read the hook trust entries in `~/.codex/config.toml`, compute the hashes of the current `hooks.json` definitions if Codex documents a scheme for that, and list any `codex` CLI subcommand that reports hook trust.
3. **Probe turn.** Run one `codex exec` in a fresh scratch git repo. The prompt runs a harmless shell command and then stops. Read the resulting rollout for evidence of each hook event.
4. **Hook input capture.** Where possible, capture hook input with invocation-scoped instrumentation only (Q2, Q3). Otherwise use the docs and record the gap.
5. **Claude side of Q3.** Check whether Claude Code's Stop input carries `last_assistant_message`. Use Claude Code documentation, or existing hook code and logs.
6. **Write the findings note** `docs/plans/2026-10-02-codex-hook-probe-findings.md`. For each of Q1–Q4, give the answer, the evidence (commands and output) and its implication for L2b.

## Error Handling

If `codex exec` fails, prompts for approval, or hangs, record it and stop. Do not retry with broader permissions.

## Testing Strategy

No tests required: this is an investigation. Its deliverable is the findings note with evidence.
