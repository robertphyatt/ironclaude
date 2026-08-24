# Re-Stage Cumulative allowed_files at execution_complete (stash-pop fix) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the end-of-loop commit capture the full intended file set even if a subagent's `git stash pop` dropped earlier tasks out of the index, by re-staging the union of every task's allowed_files at execution_complete in the executing-plans skill.

**Requirements:** docs/plans/2026-08-21-commit-restage-cumulative-allowed-files-requirements.md

**Architecture:** At execution_complete (executing-plans SKILL.md Step 7, before the commit-message suggestion) the orchestrator re-stages the deduped union of every task's `allowed_files` (from the plan JSON) via `git add -- <existing paths>`. It runs after the subagents, corrects a churned index, covers every commit path, adds only explicit allowed_files (no over-stage/clobber), and tolerates a missing path.

**Tech Stack:** Markdown (skill), Python (governance test), pytest.

**Execution invariants (author + reviewer check against these):** shell state does not persist between steps (literal absolute paths); Bash cwd is `commander/` (use `git -C <root>` / absolute paths); quote globs; foreground `sleep` blocked; `docs/` gitignored (`git add -f`); an empty result must be distinguishable from a failed command; commander pytest runs as explicit-file foreground batches with `-m "not destructive"`; the `professional-mode-guard` treats a `|` inside a quoted grep pattern as a shell pipe AND matches the word "commit" anywhere in a git command (name artifacts without "commit"; use single-term greps).

---

## Task 1: Re-stage step in executing-plans Step 7 + governance test

**Files:**
- Modify: `worker/skills/executing-plans/SKILL.md` (Step 7 "Plan complete", :658 — insert a re-stage item before "Suggest a commit message")
- Test: `commander/tests/test_executing_plans_skill.py` (add a governance test)

**Step 1: Write the governance tests (RED).** Two tests: a text-presence guard (the step exists, in order) and a **behavioral** guard (the embedded command actually stages from a non-root cwd — this is the guard that fails when the command's frame/quoting/JSON-key breaks, which a text test cannot catch). First ensure the module imports at the top of `commander/tests/test_executing_plans_skill.py` include `json`, `re`, and `subprocess` (the file currently imports only `from pathlib import Path`); add:

```python
import json
import re
import subprocess
```

Then append both tests (harness: module-level `_read()` returns the SKILL.md text):

```python
def test_execution_complete_restages_cumulative_allowed_files():
    """Step 7 (Plan complete) must re-stage the union of every task's allowed_files at
    execution_complete, before the commit-message suggestion, so a subagent's git stash pop
    (or any index churn) cannot yield a partial commit
    (project_stash_pop_unstages_prior_tasks)."""
    text = _read()
    assert "Re-stage the cumulative allowed_files" in text, \
        "Step 7 lost the cumulative allowed_files re-stage step"
    step7 = text.index("Step 7: Plan complete")
    restage = text.index("Re-stage the cumulative allowed_files", step7)
    suggest = text.index("Suggest a commit message", step7)
    assert restage < suggest, \
        "the cumulative allowed_files re-stage must precede the commit-message suggestion"
    for required in ("git stash pop", "union"):
        assert required in text, f"Step 7 re-stage lost '{required}'"


def test_execution_complete_restage_command_stages_from_non_root_cwd(tmp_path):
    """The Step 7 re-stage one-liner must actually stage allowed_files when run from a
    non-root cwd (the executing-plans invariant: Bash cwd is commander/), tolerate a
    missing path, and stage nothing outside allowed_files."""
    text = _read()
    start = text.index("Re-stage the cumulative allowed_files")
    cmd = re.search(r"```bash\n(.*?)```", text[start:], re.DOTALL).group(1)

    repo = tmp_path / "repo"
    (repo / "worker").mkdir(parents=True)
    (repo / "commander").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "worker" / "a.md").write_text("a")
    (repo / "worker" / "outside.md").write_text("outside")  # not in allowed_files
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"tasks": [
        {"allowed_files": ["worker/a.md", "worker/zz-missing.md"]},
    ]}))

    filled = cmd.replace("<plan-json-path>", str(plan)).replace("<repo-root>", str(repo))
    result = subprocess.run(["bash", "-c", filled], cwd=repo / "commander",
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=True).stdout.split()
    assert staged == ["worker/a.md"]
```

`worker/zz-missing.md` sorts last on purpose: it pins the "`if` (not `&&`) keeps returncode 0 when the last path is missing" property. `outside.md` staying unstaged is the R2 no-clobber negative case.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_executing_plans_skill.py
```
Expected: FAIL — the text-presence test fails at its first assert, and the behavioral test errors at `text.index("Re-stage the cumulative allowed_files")` (ValueError), because `Re-stage the cumulative allowed_files` is not yet in SKILL.md.

**Step 2: Add the re-stage step to SKILL.md (GREEN).** In `worker/skills/executing-plans/SKILL.md` Step 7, replace:
```
When `mcp__plugin_ironclaude_state-manager__get_next_tasks` returns `{status: "complete"}`:
1. The MCP automatically transitions workflow to execution_complete
2. Suggest a commit message based on the plan's goal and changes:
```
with:
```
When `mcp__plugin_ironclaude_state-manager__get_next_tasks` returns `{status: "complete"}`:
1. The MCP automatically transitions workflow to execution_complete
2. **Re-stage the cumulative allowed_files.** A subagent's `git stash pop` (or any index
   churn) during execution can silently drop EARLIER tasks' staged content out of the git
   index, yielding a PARTIAL commit with NO error. Before suggesting a commit, re-stage the
   deduped **union** of every task's `allowed_files` from the plan JSON so the index holds
   the full intended set regardless of churn. This runs in the orchestrator, AFTER the
   subagents. Only these explicit paths are staged — a working change OUTSIDE allowed_files
   is never touched. Stage only paths that exist on disk (replace `<plan-json-path>` and
   `<repo-root>`):
   ```bash
   python3 -c "import json,sys; print('\n'.join(sorted({f for t in json.load(open(sys.argv[1]))['tasks'] for f in t.get('allowed_files', [])})))" <plan-json-path> \
     | while IFS= read -r f; do if [ -e "<repo-root>/$f" ]; then git -C <repo-root> add -- "$f"; fi; done
   ```
3. Suggest a commit message based on the plan's goal and changes:
```

**Step 3: Run the test — GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_executing_plans_skill.py
```
Expected: all tests in the file pass (the prior tests + the new one).

**Step 4: Full regression (no build — no TS/dist change).**

state-manager vitest:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test
```
Expected: all test files pass.

workspace-manager vitest:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test
```
Expected: `Test Files 8 passed (8)`.

Hook suites:
```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh; do echo "== $t =="; bash "$t" || echo "SUITE FAILED: $t"; done
```
Expected: each 0 failed; no `SUITE FAILED`.

Commander pytest batches — build the lists:
```bash
rm -f /tmp/rcaf_tests.txt && ls /Users/roberthyatt/Code/ironclaude/commander/tests/test_*.py | sort > /tmp/rcaf_tests.txt && total=$(wc -l < /tmp/rcaf_tests.txt) && per=$(( (total + 3) / 4 )) && split -l "$per" /tmp/rcaf_tests.txt /tmp/rcaf_batch_ && wc -l /tmp/rcaf_tests.txt /tmp/rcaf_batch_aa /tmp/rcaf_batch_ab /tmp/rcaf_batch_ac /tmp/rcaf_batch_ad
```
Expected: 4 batch files `_aa`.._ad`.

Run each batch (repeat for `_aa`, `_ab`, `_ac`, `_ad` as SEPARATE Bash calls):
```bash
test -s /tmp/rcaf_batch_aa || { echo "FATAL: /tmp/rcaf_batch_aa missing/empty"; exit 1; } && cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q -m "not destructive" $(tr '\n' ' ' < /tmp/rcaf_batch_aa)
```
Expected: across the batches, all pass, 2 deselected (the `@pytest.mark.destructive` signal tests), 0 failed. (Orchestrator batch-lifecycle tests need free RAM > 10% of total; failures there are the known environmental gap, not this change.)

**Step 5: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py
```
Expected: both files staged.

**Step 6: Return shell cwd to repo root.**
```bash
cd /Users/roberthyatt/Code/ironclaude
```
Expected: cwd is `/Users/roberthyatt/Code/ironclaude`.
