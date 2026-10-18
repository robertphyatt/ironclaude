# Claude and Codex Tool Audit per IronClaude Role Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: light — investigation only. No source change; the deliverable is a findings doc built from live measurements.

## Summary

This follows commit `fb565a1`, which made the Commander's graders and fast-model children run "with no tools and no hooks" on both clients. Its end review and probes left several gaps open:
- 10 Codex tool-like features are still enabled.
- The Claude grader still sees a server-side `advisor` tool.
- Reasoning effort differs between the Codex and Claude fast children.
- Batch grading has never been probed live.

Operator directives, 2026-10-17:
- Item 1 of next steps: "Proceed as suggested".
- Asked how to handle the 10 Codex features: "Let's do an audit of all the tools for both Claude and codex and deliberately figure out which ones conflict with ironclaude vs didn't actually matter".
- Asked for the audit scope: "Do all the tools, for both providers, and do a separate analysis for each ironclaude persona/use case" (`abs` corrected to `and`).
- Design approved: "Yes, write it up (Recommended)".
- Standing goal: "my goal here is to have codex and Claude be as consistent as possible".

## Requirements

- **A1 — Role inventory.** From current source, list every IronClaude role that launches a Claude or Codex model, with the exact argv or launch configuration for each client. Expected roles, each to be confirmed or corrected from the code:
  - Brain: Claude via the agent SDK, Codex via app-server.
  - Workers: interactive Claude and Codex.
  - Commander grader.
  - Commander fast-model child.
  - Hook fast-model child.
  - Advisor fallback: Claude `Agent` subagent, Codex advisor broker.
  - Episodic summarizer.

  Any other launch site found in the code is added.
- **A2 — Claude measurement.** For each Claude role that can run safely as a one-shot, run its real argv with a trivial prompt and `--output-format json` or stream-json. Record the `init` event's `tools` and `mcp_servers` lists verbatim.
- **A3 — Codex measurement.**
  - Record `codex --version`.
  - Record `codex features list` in full: every feature, its stage and whether it is enabled.
  - Check `codex --help` and `codex exec --help` for any command that lists tools.
  - For each Codex role that can run safely as a one-shot, run its real argv and ask the model to list every tool it can call by exact name. Record the answer, labelled as self-report.
- **A4 — Unlaunchable roles.** Assess the Brain, interactive workers and any role that cannot be launched safely from its argv and config, and mark the entry "not measured". Never start a live Brain or worker for the audit.
- **A5 — Classification.** For each role, put every tool or feature (A2/A3) in one of four classes:
  - **needed:** the role requires it.
  - **harmless:** no file, shell, network, agent, scheduling or persistent-state effect.
  - **conflicts with IronClaude:** it bypasses or duplicates something IronClaude guards or manages, namely:
    - file reads or writes, the shell, network egress;
    - spawning agents or workers, worktrees, scheduling;
    - plugin, MCP or hook loading;
    - persistent memory.

    Each conflict gets a one-line reason and a recommendation.
  - **unknown:** not determinable from the evidence.
- **A6 — Consistency.** For each role, compare Claude and Codex side by side and list every asymmetry. That includes the open items: the advisor tool, reasoning effort, the 10 Codex features and batch grading.
- **A7 — Deliverable.** `docs/plans/2026-10-17-tool-audit-findings.md` holds:
  - the role inventory with argv;
  - the raw evidence, either inline or as paths under `/private/tmp/claude-502/audit/`;
  - one matrix per role;
  - the consistency comparison;
  - a ranked list of recommended changes for the operator to approve.

  Stage it with `git add -f` together with this loop's docs.
- **Non-goals:**
  - no change to any source, config or `~/.codex/config.toml`;
  - no live Brain or worker launches;
  - fixes come in a later, operator-approved loop.

Predicted answer: yes — the operator approved this design.
