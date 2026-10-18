# Codex Fast-Model Child Tools Investigation Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: light — investigation only: no source change, one findings note; real `codex exec` probes run against a scratch directory.

## Summary

Operator, 2026-10-17: "let's start your number one recommendation". That recommendation was to remove the shell tool from the Codex fast-model child. The Commander child (`commander/src/ironclaude/fast_model.py:104` `codex_fast_argv`) and the hook child (`worker/hooks/plan-validator.sh:163`) run `codex exec ... -s read-only` with no tool restriction. Untrusted prompt text can therefore drive read-only shell commands, and their output can land in answer fields; the C1 `reason` reaches Slack. The Claude child already runs with `--tools ""`.

The fix needs a measured mechanism. The installed Codex CLI is `0.163.0-alpha.10.2` (`/Applications/ChatGPT.app/Contents/Resources/codex-cli/codex-package.json`). Its binary contains the strings `features.shell_tool`, `features.unified_exec`, `web_search_mode`, `view_image` and `apply_patch_freeform`. Strings alone do not show which override actually removes a tool. The brainstorming stage blocks running `codex`, so this loop measures it.

## Requirements

- **I1. Enumerate.** Record `codex --version`, `codex exec --help`, and `codex features list` (or the subcommand that `codex --help` names for features), each to its own log.
- **I2. Baseline probe.** Use the current fast-child argv, the newest installed Luna and a strict one-field schema, run from a scratch directory that holds a sentinel file with a random token. The prompt asks the model to read the sentinel with a shell command and put its contents in the answer. Record from the `--json` JSONL:
  - every item `type`, in particular any command-execution items;
  - whether the token appears in the answer.

  This is the positive control: it shows whether the current argv lets the child read the file.
- **I3. Candidate probe.** Repeat I2 with the candidate overrides: `-c features.shell_tool=false -c features.unified_exec=false`, plus any web-search, view-image or apply-patch keys that I1 shows. Record the same evidence. Also record that a plain structured answer still works under the candidates, with rc 0 and a schema-valid answer.
- **I4. Findings.** Write `docs/plans/2026-10-17-codex-fast-child-tools-findings.md` with the exact commands, rc values, item types, token-present yes or no, and the minimal override set that removes every tool while keeping structured output. Stage it with `git add -f` together with this loop's docs.
- **Non-goals:** no change to `fast_model.py`, `plan-validator.sh` or tests; the fix is its own loop, planned against these findings. Never write `~/.codex/config.toml`.

Predicted answer: yes — this is the measurement the operator-approved fix needs (directive 11: open an investigation loop rather than guess).
