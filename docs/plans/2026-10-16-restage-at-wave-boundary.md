# Re-stage at Wave Boundary Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the executing-plans cumulative re-stage step actually run, by moving it to each wave boundary while the stage is still `executing`, and fix the commander test that extracts it.

**Requirements:** `docs/plans/2026-10-16-restage-at-wave-boundary-design.md` (§Requirements R1–R3)

**Architecture:**
- The "Re-stage the cumulative allowed_files" item moves from Phase 3 Step 7 into Step 5 "Advance to next wave".
  - The script block moves byte-for-byte, with the same 3-space list indentation and the same BEGIN/END markers.
  - Only the item's opening sentences change, to say when the step runs.
- Step 7 item 2 becomes a `git status --short` check.
- The commander tests dedent the extracted block and check the new placement.

**Tech Stack:** Markdown skill text, pytest (commander venv), bash hook test.

Loop: light. Only skill text and one test file change; no guard, DB or MCP code. About 2 files and 40 lines.

## Execution invariants

- Use literal absolute paths, and `git -C /Users/roberthyatt/Code/ironclaude`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push.
- `docs/` is gitignored, so stage plan files with `git add -f`.

**Verified at planning time:**

| What | Where / fact |
|---|---|
| Step 5 | `worker/skills/executing-plans/SKILL.md:606-610`. It begins `**Step 5: Advance to next wave**`, then the line ``After all tasks in the current wave pass review, call `mcp__plugin_ironclaude_state-manager__get_next_tasks` again.``, then `- If more tasks: repeat Step 4` and `- If complete: proceed to Phase 3`. Step 6 starts at `:612` (`**Step 6: Handle failures**`). |
| Step 7 | `SKILL.md:715` is `**Step 7: Plan complete**`. Item 2, the re-stage, runs `:719-786`: prose `:719-730`, then a ```` ```bash ```` block `:731-786` whose heredoc is marked `# BEGIN EXACT ALLOWED FILES RESTAGING` (`:733`) and `# END EXACT ALLOWED FILES RESTAGING` (`:784`). Item 3, `3. Suggest a commit message based on the plan's goal and changes:`, is at `:787`. |
| Commander test file | `commander/tests/test_executing_plans_skill.py` imports `json`, `re`, `subprocess` and `Path`, but not `textwrap`. |
| Placement test | `test_execution_complete_restages_cumulative_allowed_files` is at `:355-369`. |
| Non-root-cwd test | `test_execution_complete_restage_command_stages_from_non_root_cwd` is at `:372-398`. It extracts with `re.search(r"```bash\n(.*?)```", text[start:], re.DOTALL).group(1)` at `:378`, and fails at HEAD with `IndentationError` because it does not dedent. |
| Hook test | `worker/hooks/tests/test-skill-handoffs-restaging.sh` finds the script with `^   # BEGIN …` / `^   # END …` and dedents it. Keeping the 3-space indentation keeps it valid. |
| Other copies of the re-stage text | `grep -rln "Re-stage the cumulative"` over `worker` and `commander` finds only `SKILL.md` and the commander test. |

---

## Task 1: Move the re-stage to the wave boundary and fix its tests

TDD.

**Files:**
- Modify: `worker/skills/executing-plans/SKILL.md` (`:606-610`, `:719-786`)
- Modify: `commander/tests/test_executing_plans_skill.py` (imports, `:355-369`, `:378`)
- Stage only: `docs/plans/2026-10-16-restage-at-wave-boundary-design.md`, `docs/plans/2026-10-16-restage-at-wave-boundary.md`, `docs/plans/2026-10-16-restage-at-wave-boundary.plan.json`

### Step 1: Tests (RED)

In `commander/tests/test_executing_plans_skill.py`:

1. Add `import textwrap` immediately after `import subprocess`.
2. Replace the whole function `test_execution_complete_restages_cumulative_allowed_files` (`:355-369`) with:
```python
def test_wave_boundary_restages_cumulative_allowed_files():
    """Step 5 (Advance to next wave) must re-stage the union of every task's
    allowed_files while the stage is still `executing`, before the next
    get_next_tasks call, so a subagent's git stash pop (or any index churn) cannot
    yield a partial commit (project_stash_pop_unstages_prior_tasks). It cannot run at
    execution_complete: the professional-mode guard blocks Bash write commands there.
    Step 7 then only verifies the index before suggesting a commit."""
    text = _read()
    step5 = text.index("**Step 5: Advance to next wave**")
    step6 = text.index("**Step 6: Handle failures**", step5)
    restage = text.index("Re-stage the cumulative allowed_files")
    advance = text.index("get_next_tasks` again", step5)
    assert step5 < restage < advance < step6, \
        "the cumulative re-stage must run in Step 5, before the next get_next_tasks call"
    step7 = text.index("Step 7: Plan complete")
    verify = text.index("git -C <repo-root> status --short", step7)
    suggest = text.index("Suggest a commit message", step7)
    assert verify < suggest, "Step 7 must verify the staged index before suggesting a commit"
    for required in ("git stash pop", "union"):
        assert required in text, f"re-stage lost '{required}'"
```
3. In `test_execution_complete_restage_command_stages_from_non_root_cwd`, change line `:378` from
   `    cmd = re.search(r"```bash\n(.*?)```", text[start:], re.DOTALL).group(1)`
   to
   `    cmd = textwrap.dedent(re.search(r"```bash\n(.*?)```", text[start:], re.DOTALL).group(1))`

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_executing_plans_skill.py -q -k "wave_boundary_restages or restage_command_stages"
```
Expected: 1 failed, 1 passed.
- `test_wave_boundary_restages_cumulative_allowed_files` fails because the re-stage still sits in Step 7: the `assert step5 < restage < advance < step6` check fails, since `restage` comes after `step6`.
- `test_execution_complete_restage_command_stages_from_non_root_cwd` passes now that the block is dedented. That shows the indentation was the only cause of its failure at HEAD, and the script is sound. It would fail again if a later move or edit broke the block.

Record the measured result.

### Step 2: Implementation (GREEN) in `worker/skills/executing-plans/SKILL.md`

1. **Step 5.** Replace these three lines (`:608-610`):
```
After all tasks in the current wave pass review, call `mcp__plugin_ironclaude_state-manager__get_next_tasks` again.
- If more tasks: repeat Step 4
- If complete: proceed to Phase 3
```
with this text, where `<SCRIPT BLOCK>` is the ```` ```bash ```` … ```` ``` ```` block cut from Step 7 (current `:731-786`), moved unchanged:
```
After all tasks in the current wave pass review (and `mark_executing` has returned the workflow to `executing`):

1. **Re-stage the cumulative allowed_files.** A subagent's `git stash pop` (or any index
   churn) during execution can silently drop EARLIER tasks' staged content out of the git
   index, yielding a PARTIAL commit with NO error. At every wave boundary, before calling
   `get_next_tasks`, re-stage the deduped **union** of every task's `allowed_files` from
   the plan JSON so the index holds the full intended set regardless of churn. This runs
   here, while the stage is still `executing`, because the professional-mode guard blocks
   Bash write commands once `get_next_tasks` moves the workflow to `execution_complete`.
   It runs in the orchestrator, AFTER the wave's subagents. It accepts absolute or
   repo-relative file entries, resolves
   existing ancestors without following the final symlink, and rejects paths
   outside the repo or directory entries. Existing files and symlinks are staged;
   missing entries are staged only when tracked, so deletions are included and
   optional missing files are skipped. Validate every entry before staging and
   leave unrelated index entries alone. Pass repository root and plan JSON as
   separate arguments:
<SCRIPT BLOCK>
2. Call `mcp__plugin_ironclaude_state-manager__get_next_tasks` again.
   - If more tasks: repeat Step 4
   - If complete: proceed to Phase 3
```
2. **Step 7 item 2.** Remove the whole of item 2 (current `:719-786`): its prose plus the script block that moved in item 1. Put this item 2 in its place, directly before `3. Suggest a commit message based on the plan's goal and changes:`:
```
2. **Verify the index holds every allowed file.** The cumulative re-stage already ran at
   the final wave boundary (Step 5) while the stage was still `executing`; the
   professional-mode guard blocks Bash write commands at `execution_complete`, so do not
   re-run it here. Run `git -C <repo-root> status --short` and confirm every
   `allowed_files` path with changes shows as staged, with no unstaged (` M`, `AM`) or
   untracked (`??`) entry for it. If one is not staged, report it to the operator before
   suggesting a commit.
```
3. **Check the move.** `grep -c "BEGIN EXACT ALLOWED FILES RESTAGING"` on `SKILL.md` must still print `1`, and the block must sit between `**Step 5: Advance to next wave**` and `**Step 6: Handle failures**`.

### Step 3: GREEN, the hook test and the full commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_executing_plans_skill.py -q > /private/tmp/claude-502/rs1.out 2>&1; echo "t=$?"; tail -n 3 /private/tmp/claude-502/rs1.out; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-handoffs-restaging.sh > /private/tmp/claude-502/rs-hook.out 2>&1; echo "hook=$?"; tail -n 3 /private/tmp/claude-502/rs-hook.out; grep -c "BEGIN EXACT ALLOWED FILES RESTAGING" /Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/rs-suite.out 2>&1; echo "suite=$?"; tail -n 3 /private/tmp/claude-502/rs-suite.out
```
Expected:
- `t=0`, with 0 failed;
- `hook=0`, with the last lines `PASS reject …`;
- the count is `1`;
- `suite=0`, with 0 failed. The only failure before this change was `test_execution_complete_restage_command_stages_from_non_root_cwd`, which this task fixes.

Any other failure must be shown pre-existing by running it in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-restage-at-wave-boundary-design.md docs/plans/2026-10-16-restage-at-wave-boundary.md docs/plans/2026-10-16-restage-at-wave-boundary.plan.json
```
Expected: no output.
