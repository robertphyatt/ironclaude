# Tool Audit Fixes: Claude and Codex Children Consistency Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: full — security boundaries (LLM child argvs that take untrusted text, the Codex advisor broker), grader prompt construction and Codex model resolution, across the Commander, hooks and the state-manager (about 12 files).

## Summary

The tool audit (`docs/plans/2026-10-17-tool-audit-findings.md`, lineage 197) measured every tool each IronClaude role gets on both clients.
- **Codex advisor broker:** has 117 tools and read a file outside its working directory.
- **Codex graders and fast children:** still get agent-spawning tools.
- **Claude graders and fast children:** still see the server-side `advisor` tool, plugins and auto-memory.
- **Claude hook child:** does not strip the Ollama routing variables.
- **Effort:** differs between the Claude and Codex fast children.
- **Codex model:** the hook and the Commander pick different Luna models.

Operator directives, 2026-10-17:
- Fix scope: approved all four groups ("Codex no-tools set", "Harden Codex broker", "Claude --restricted + effort", "Small consistency fixes"). The episodic summarizer gets its own measurement loop.
- Grader rubric: "Put rubric in the prompt (Recommended)", the same rubric for both clients regardless of CLAUDE.md/AGENTS.md loading. This replaces the AGENTS.md-sync item.
- Codex slug: "Newest installed everywhere (Recommended)", which extends the 2026-09-30 advisor rule of the newest installed `gpt-<v>-<tier>` with the configured slug as fallback.
- Design approved: "Yes, write it up (Recommended)".
- Standing goal: "my goal here is to have codex and Claude be as consistent as possible".
- Standing constraints: never write `~/.codex/config.toml`; never push.

## Requirements

- **R1 — Shared Codex no-tools set.**
  - **Constant.** `fast_model.CODEX_NO_TOOLS_ARGS` is a flat list of `-c` argv pairs:
    - `features.<name>=false` for each `CODEX_DISABLED_FEATURES` name;
    - `features.goals=false` and `features.tool_suggest=false`;
    - `agents.enabled=false`;
    - `web_search="disabled"`.
  - **Python use.** `codex_fast_argv` and `OrchestratorTools._codex_grader_argv` use it in place of their current feature-off expansion.
  - **Hook use.** `worker/hooks/plan-validator.sh` `_call_fast_model` (Codex arm) carries the same pairs in the same order.
- **R2 — Codex advisor broker.** `worker/mcp-servers/state-manager/src/tools/advisor-review.ts` adds the same pairs to its `codex exec` argv (around lines 610-624) and keeps `-s read-only`, `--ignore-user-config` and `--ignore-rules`. The broker then has no shell, file-read, agent or app tools, so review packets must be self-contained, which they already are under the advisor-fallback contract.
- **R3 — Parity.** A Commander test extracts the `-c` values from `plan-validator.sh` and from `advisor-review.ts`, in file order, and asserts each equals the Python constant's values. A TypeScript unit test asserts the broker argv contains every pair.
- **R4 — Claude children restricted.**
  - **Fast and hook children.** `fast_model.claude_fast_argv` and the hook's `claude` call add `--restricted` and `--effort low`.
  - **Grader.** `OrchestratorTools._claude_grader_argv` adds `--restricted` and removes `--dangerously-skip-permissions`, which `--restricted` refuses. Its effort stays on the configured tier via `_grader_env`.
  - **Measured requirement.** Every Claude child still returns `structured_output` under `--json-schema`, and no longer self-reports `advisor`.
- **R5 — Hook env strip.** The hook's `claude` call runs with the same variables unset that `fast_model._claude_env` strips. The variable list is read from source at plan time.
  - **Effort variable.** `fast_model._ENV_STRIP` also gains `CLAUDE_CODE_EFFORT_LEVEL`, so the fast child and the hook child both run without it. The grader is unchanged: it uses its own `_GRADER_ENV_STRIP` and sets its effort through `_grader_env`.
  - **Why.** Measured 2026-10-17 with `claude -p --model haiku`, reading the effort recorded in each child's session file:
    - `CLAUDE_CODE_EFFORT_LEVEL=high` with `--effort low` recorded `high`.
    - `CLAUDE_CODE_EFFORT_LEVEL=high` with no flag recorded `high`.
    - No variable with `--effort low` recorded `low`.

    The inherited variable wins over the flag. Workers export `CLAUDE_CODE_EFFORT_LEVEL` (`orchestrator_mcp.py:672-701`, `config.py:241`), and hooks inherit it, so `--effort low` alone would not pin anything.
  - **Operator decision.** Answer to "How should I amend Task 4?": "Unset it + measure (Recommended)".
- **R6 — Rubric in the prompt.** `_call_grader` prepends the grader-home rubric (`os.path.join(self._grader_home, "CLAUDE.md")`, synced and substituted at Commander start-up by `main.py:6084-6096`) to the system prompt for both clients:
  - Claude receives it through `--system-prompt-file`.
  - Codex receives it in the stdin prompt.
  - A missing rubric file adds nothing; it is not an error.

  Measured requirement: both graders' verdicts on the evidence-free probe cite the rubric ("Law" or checklist language), single and batch.
- **R7 — Newest installed Codex model.**
  - **Resolver.** A resolver in the Commander maps tier word to slug: haiku→luna, sonnet→terra, opus→sol, fable→astra. It returns the newest installed `gpt-<numeric version>-<word>` from `$CODEX_HOME/models_cache.json` (default `~/.codex`), ordered by numeric version as `plan-validator.sh` `_newest_codex_model` and `advisor-review.ts` do. It falls back to the configured slug when the cache is missing, unreadable or has no match.
  - **Use.** `ProviderConfig.model_for` (codex client) and `fast_model.fast_model_clients` (codex entries) both use it.
  - **Test isolation.** Commander tests run with `CODEX_HOME` set to an empty temporary directory through an autouse fixture, so they keep resolving to configured slugs.
- **R8 — Small fixes.**
  - **Advisor fallback.** `worker/skills/advisor-fallback/SKILL.md` dispatches the Claude subagent as `subagent_type="Explore"` (read-only) in place of `general-purpose`. Any test asserting the old text is updated.
  - **Hook schema.** The Codex hook schema gets the full recursive strict transform, equivalent to `fast_model.strict_codex_schema`, in place of a top-level-only `additionalProperties:false`.
  - **Codex worker resume.** The command at `orchestrator_mcp.py` around lines 6833-6837 adds `--no-daemon` and `--model <model>`, matching the fresh spawn.
- **R9 — Tests and probes.**
  - **TDD:** RED first, then GREEN, for every argv, constant, resolver, rubric and text change.
  - **Before and after probes,** reusing `/private/tmp/claude-502/audit/` and `/private/tmp/claude-502/cgh/` scripts:
    - Codex grader, fast, hook and broker self-reports show no `collaboration.*`, `multi_agent*`, goal or `mcp__codex_apps__*` tools.
    - The broker can no longer read the sentinel.
    - The Claude grader, fast and hook children do not list `advisor`.
    - Both graders return verdicts single and batch, citing the rubric.
  - **Full regression:** the Commander, hooks and state-manager (`npm test`) suites.
- **Non-goals:**
  - `unified_exec` cannot be turned off; it stays monitored only.
  - The episodic summarizer gets its own loop.
  - Brain and interactive worker tool sets are unchanged, apart from the model slug via R7 and the resume flags.
  - No `~/.codex/config.toml` writes, and no push.

## Claude/Codex differences that remain

- **Codex `unified_exec`** stays on and cannot be turned off; Claude has no shell. Action probes show it cannot read files in the children.
- **Codex** turns tools off by name. **Claude `--restricted --tools ""`** turns off everything, including tools added in future CLI versions.
- **Grader effort.** The Claude grader takes effort from an environment variable and Codex from `-c`; both use the same tier value.

Predicted answer: yes — the operator approved this design.
