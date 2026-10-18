# Codex Grader Hardening and Fast-Model Denylist Completion Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: full — security boundary (Codex child argvs that take untrusted text) and a grader schema/parse contract.

## Summary

Operator, 2026-10-17: "Proceed as suggested with option 1+2", clarified as "I meant combined". The design was approved with "Yes, write it up (Recommended)".

**Part 1: the Commander's main Codex grader.**
- **Argv.** `commander/src/ironclaude/orchestrator_mcp.py` `_codex_grader_argv` (lines 1123-1139) launches `codex exec` with no `--ignore-user-config`, no `--ignore-rules` and no tool-feature flags. It grades untrusted worker output.
- **History.** `git log -S "--ignore-user-config"` on that file returns nothing. The positive control, `-S "--skip-git-repo-check"`, returns `e46886b`. So the omission was never a deliberate removal, and episodic memory records no decision about it.
- **Schema.** `_GRADER_VERDICT_SCHEMA` (lines 944-954) leaves `recommended_model` out of `required`. Strict Codex structured output rejects that with HTTP 400 `invalid_json_schema`, the same defect class fixed for the fast model in `785311a`.

**Part 2: finishing the fast-model denylist from `7890647`.**
- **Findings.** The Opus end review and the background commit security review both found `memories`, `plugins` and `hooks` still enabled.
- **Drift risk.** The two lists, `CODEX_DISABLED_FEATURES` and `plan-validator.sh`, have no parity test.
- **Doc overclaim.** `worker/config-schema/llm-backend.md:233` says "so the child has no tools", which is more than was proven.

## Requirements

- **G1 — Grader argv.** `_codex_grader_argv` returns `[codex_path, "exec", "-c", effort, *(-c features.<name>=false for each name in fast_model.CODEX_DISABLED_FEATURES), "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--output-schema", schema_file, "-s", "read-only", "-m", model, "-"]`. The effort, model, read-only sandbox, cwd and env are unchanged.
- **G2 — Grader schema.** In `_call_grader`, only the Codex arm writes `fast_model.strict_codex_schema(schema)` to the schema file, for both the single and the batch schema. The Claude arm's `--json-schema` is unchanged.
- **G3 — Null stripping.** In `_parse_codex_grader_output`, a `recommended_model` whose value is `None` is omitted from the single verdict and from every batch verdict dict. A non-null value is kept. Every other field is unchanged.
- **D1 — Denylist.** `CODEX_DISABLED_FEATURES` becomes `("shell_tool", "unified_exec", "view_image", "apps", "browser_use", "computer_use", "multi_agent", "image_generation", "memories", "plugins", "hooks")`. `plan-validator.sh` `_call_fast_model` gets the same 11 flags in the same order.
- **D2 — Parity.** A Commander test extracts every `features.<name>=false` name from `worker/hooks/plan-validator.sh`, in file order, and asserts the list equals `list(fast_model.CODEX_DISABLED_FEATURES)`.
- **D3 — Doc.** In `llm-backend.md:233`, the Codex flag sentence lists all 11 names and says the child runs "with these tool features off" instead of "so the child has no tools".
- **C1 — Claude grader consistency.** Operator, 2026-10-17: "my goal here is to have codex and Claude be as consistent as possible here". Asked whether to align the Claude grader with the Claude fast child, the operator chose "Yes, align both (Recommended)" and asked to "Flag any differences for my review".
  - In `_claude_grader_argv` (lines 1043-1072), the `--disallowedTools <21 names>` pair is replaced by `--tools ""`.
  - `--settings '{"disableAllHooks":true}'` is added. The string must be identical to `fast_model._DISABLE_HOOKS_SETTINGS`.
  - Everything else is unchanged: `-p`, `--system-prompt-file`, `--output-format json`, `--json-schema`, the `--model` `[1m]` logic, `--dangerously-skip-permissions`, `--strict-mcp-config` and `--no-session-persistence`.
  - A real probe through `_claude_grader_argv` must still return `structured_output` with the grader schema on the configured Claude grader model (`opus`).
- **T1 — Tests first.** RED, then GREEN, for G1-G3 and D1-D2. Existing exact-argv tests are updated, and the existing grader tests stay green.
- **T2 — Real probes, before and after, through the shipped functions:**
  - `gpt-5.6-sol` (the configured Codex opus-class grader model) with the grader schema. Before: the current argv and schema, expected to fail. After: the new argv and the strict schema, which must return a verdict object.
  - A file-read sentinel through the grader argv. It must leak before and not after.
  - The fast-model sentinel and plain probes with all 11 flags. They must answer without the token, and the plain probe must still answer.
- **T3 — Regression.** Full Commander suite and hooks suite.
- **Non-goals:** the advisor broker (`advisor-review.ts`), the Codex Brain and Codex workers, `~/.codex/config.toml`, and any push. Deploy is a separate operator step.

## Claude/Codex differences that remain (for operator review)

After this change, these four children run with these tool features off and no hooks: the Claude grader, the Codex grader, the Claude fast child and the Codex fast child. The following differences remain. Each is listed with whether it is a real gap or only a different mechanism for the same effect.

| # | Difference | Claude | Codex | Assessment |
|---|---|---|---|---|
| X1 | How tools are removed | `--tools ""` turns off *every* tool, including any added in future CLI versions (an allowlist of none) | `-c features.X=false` for 11 named features (a denylist) | **Real gap.** A tool feature added in a later Codex version would be on by default. Besides the 11 we turn off, `codex features list` (0.163.0-alpha.10.2) shows these tool-shaped features still enabled today: code_mode_host, sleep_tool, skill_search, tool_suggest, goals, in_app_browser, browser_use_external, worktrees, remote_plugin and plugin_sharing; only shell file-reads were probed. `codex exec` has no measured "no tools at all" flag. Mitigations: `-s read-only` stays on, and the D2 parity test only keeps our two lists in sync. A follow-up could fail closed when `codex features list` shows an unknown enabled feature. |
| X2 | Filesystem sandbox | none (the child has no tools, so it has nothing to sandbox) | `-s read-only` | Codex is stricter. Defence in depth, kept. |
| X3 | User config and rules | `--strict-mcp-config` drops MCP. User settings still load, apart from hooks. | `--ignore-user-config --ignore-rules` drop all user config and rules | Different mechanism, same intent. Claude has no single "ignore user settings" flag. `disableAllHooks` plus `--tools ""` cover the risky parts. |
| X4 | Reasoning effort | Grader: env `CLAUDE_CODE_EFFORT_LEVEL` (orchestrator_mcp.py:1015). Fast child: not set (CLI default). | Grader: `-c model_reasoning_effort` from the same configured level. Fast child: `-c model_reasoning_effort="low"`. | **Fast-child mismatch:** Codex is pinned to low and Claude uses the CLI default. Not changed here; flagged. |
| X5 | Schema | The original schema; Claude accepts optional properties | `strict_codex_schema` copy (all properties required, optional ones nullable), with nulls stripped on parse | Different mechanism, same accepted shape after parse. |
| X6 | Environment | Grader strips the `_GRADER_ENV_STRIP` billing and routing variables so it uses the Claude Max subscription | The process environment unchanged; Codex uses its own ChatGPT login | Intentional (auth differs by provider). Unchanged. |
| X7 | Prompt delivery | `--system-prompt-file` plus the user prompt | System and user prompt joined on stdin | Codex has no separate system-prompt flag for `exec`. Unchanged. |
| X8 | Working directory | Grader: `_grader_home`; fast child: `/tmp` | The same as Claude for each role | Same. |

Predicted answer: yes — the operator approved this design.
