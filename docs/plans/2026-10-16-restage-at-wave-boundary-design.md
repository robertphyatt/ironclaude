# Re-stage at Wave Boundary Design

> **Created:** 2026-10-16
> **Status:** Design Complete

Loop: light. Only skill text and a test file change; no guard, DB or MCP code. About 2 files and 40 lines.

## Summary

The executing-plans skill's "Re-stage the cumulative allowed_files" step has never been able to run where it is placed. It sits in Phase 3 Step 7 and runs after `get_next_tasks` returns `complete`, by which point the workflow stage is `execution_complete`.

That step was added on 2026-08-21, against index churn from a subagent's `git stash pop`. At that time the operator chose to keep it in the skill rather than in the commit tool.

There are two defects, both verified against the source.

1. **The guard blocks the step.**
   - `worker/hooks/professional-mode-guard.sh:625` blocks every Bash command outside the `executing` stage. The only exceptions are anchored `git add`, read-only git, read-only research and `make test`.
   - The re-stage command is `python3 - … <<'PY'`, a heredoc, so it never matches an exception.
   - It was blocked twice on 2026-10-15.
2. **The commander test breaks the script.**
   - `commander/tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd` (line 372) pulls the ```` ```bash ```` block from `worker/skills/executing-plans/SKILL.md` exactly as written. The block sits inside a numbered list, so every line is indented 3 spaces.
   - Bash therefore never sees the `PY` terminator, and Python raises `IndentationError: unexpected indent`. The test fails at HEAD.
   - `worker/hooks/tests/test-skill-handoffs-restaging.sh`, added in `59b5ca7` alongside the heredoc, dedents the block correctly and passes. The script itself is sound.

## Requirements

- **R1:** move the whole "Re-stage the cumulative allowed_files" item, prose and script, from Phase 3 Step 7 into Step 5 "Advance to next wave". There it is a numbered item that runs while the stage is still `executing`: after the current wave passes review and `mark_executing` returns, and before the next `get_next_tasks` call.
  - It therefore runs at every wave boundary, including the last one before `get_next_tasks` returns `complete`.
  - The script text, including its 3-space list indentation and its BEGIN/END markers, stays byte-for-byte the same.
  - *(Operator 2026-10-16: "A: wave-boundary (Recommended)", then "Approve (Recommended)".)*
- **R2:** Phase 3 Step 7 item 2 becomes a check.
  - Run `git -C <repo-root> status --short` and confirm every `allowed_files` path with changes is staged.
  - The step notes that the re-stage already ran at the final wave boundary, because the guard blocks Bash write commands at `execution_complete`.
- **R3:** two commander test changes.
  - `test_execution_complete_restage_command_stages_from_non_root_cwd` dedents the extracted block with `textwrap.dedent` before running it.
  - `test_execution_complete_restages_cumulative_allowed_files` asserts three things: the re-stage item lies inside Step 5, before the "call `mcp__plugin_ironclaude_state-manager__get_next_tasks` again" line; Step 7 still tells the orchestrator to check the index with `git status`; and the `git stash pop` and `union` wording remains.

**Rejected alternative:** a guard exception that admits the exact canonical script at `execution_complete`. It would change enforcement code and need a full loop and a Fable end review, and a loose matcher would let arbitrary Python through.

**Accepted trade-offs:**
- The re-stage runs once per wave instead of once.
- It stages existing edits in later tasks' allowed files earlier than before. The final re-stage would stage the same files anyway.

**Non-goals:** any change to the script's logic, to the guard, or to the hook test.

## Testing Strategy

- Both commander tests in `commander/tests/test_executing_plans_skill.py`, written to fail first:
  - The placement test fails before the move.
  - The non-root-cwd test already fails at HEAD and passes once the block is dedented.
- `bash worker/hooks/tests/test-skill-handoffs-restaging.sh` still passes, which proves the moved block is unchanged.
- The full commander suite has no failures, since the one known failure is this test.
