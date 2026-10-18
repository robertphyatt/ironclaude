# Codex Fast-Model Child Without Tools Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: full — changes a security boundary (LLM child-process argv) in both the Commander and the hooks.

## Summary

The investigation (`docs/plans/2026-10-17-codex-fast-child-tools-findings.md`) found that the Codex fast-model child can run shell commands with the flags in use today. Asked to `cat` a sentinel file, the Luna child ran `/bin/zsh -lc 'cat secret.txt'` and put the file's contents into its answer. Prompts to this child include untrusted text from worker panes and Brain messages, and some answers reach Slack. The Claude child already runs with `--tools ""`.

Operator directives, 2026-10-17:
- "let's start your number one recommendation" (remove the shell tool from the Codex fast-model child).
- Scope decision: "All tool features (Recommended)". Turn off shell_tool, unified_exec, view_image, apps, browser_use, computer_use, multi_agent and image_generation.
- Design approved: "Yes, write it up (Recommended)".

## Requirements

- **R1 — Commander child.** `commander/src/ironclaude/fast_model.py` gains a module constant `CODEX_DISABLED_FEATURES = ("shell_tool", "unified_exec", "view_image", "apps", "browser_use", "computer_use", "multi_agent", "image_generation")`. `codex_fast_argv` inserts `"-c", f"features.{name}=false"` for each name, in that order, directly after `"-c", 'model_reasoning_effort="low"'`. Everything else in the argv stays the same, including `-s read-only`.
- **R2 — Hook child.** `worker/hooks/plan-validator.sh` `_call_fast_model` adds the same eight `-c features.<name>=false` flags, in the same order, directly after `-c 'model_reasoning_effort="low"'`. The Claude arm is unchanged.
- **R3 — Tests first (RED, then GREEN).**
  - `commander/tests/test_fast_model.py::test_codex_argv_is_ephemeral_low_effort` asserts the full new argv.
  - The two exact-argv assertions in `worker/hooks/tests/test-fast-model-fallback.sh` (the Luna case near line 134 and the Terra case near line 221) assert the new argv head.
- **R4 — Real probes through the shipped code.** Run the sentinel prompt from the investigation (a file holding a random token; the prompt asks the model to `cat` it, or else answer `NO_TOOLS`):
  - through the Commander's `fast_model.call_fast_model` with a codex-only grader config at the Haiku class;
  - through the hook's `_call_fast_model` with `IC_HOOK_CLIENT=codex`.

  Each must return a structured answer that does not contain the token. A plain prompt ("put ok in answer") must still return a structured answer through both paths.
- **R5 — Regression.** The full Commander suite and the hooks suite pass.
- **Not covered:** removing view_image, browser_use, computer_use, apps, multi_agent and image_generation is not checked by behaviour, because a text-only classifier prompt cannot exercise those tools. Those flags rest on the `codex features list` names recorded in the findings.
- **Non-goals:**
  - No change to `-s read-only`, the schemas, the Claude arm, the local-box path, or `~/.codex/config.toml`.
  - Deploy follows this loop as a separate operator-requested step.

Predicted answer: yes — the operator approved this design.
